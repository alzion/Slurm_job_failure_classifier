# Real-Cluster Deployment Guide

This guide walks through connecting the classifier to a real Slurm GPU cluster. The simulator runs automatically in the default setup; this guide replaces it with real data sources.

---

## Before you begin

### What the classifier needs from your cluster

| Data source | How it's used | Where it lives |
|---|---|---|
| `slurmctld.log` | Failure pattern matching (NODE_FAIL, requeue events) | Slurm controller node |
| `slurmd.log` | Per-node failure details (XID codes, OOM, NCCL errors) | Each compute node |
| `sacct --json` | Job state, exit codes, node lists, elapsed time | Slurm accounting DB |
| DCGM metrics | Pre-failure signal detection (ECC, temp, NVLink) | Prometheus / dcgm-exporter |

### Prerequisites

- Docker with Compose v2 on a monitoring host (does not need to be the Slurm controller)
- Slurm 21.08+ — required for `sacct --json`
- `dcgm-exporter` deployed on GPU nodes and scraped by Prometheus
- The monitoring host can reach your Prometheus instance over the network

### Two decisions to make first

**1. How will log files reach the classifier?**

The classifier reads from a single directory (`/logs` inside the container). Pick one:
- **Shared filesystem** (Lustre, NFS, GPFS, BeeGFS) — if your logs are already on a shared path, bind-mount it directly. Simplest option.
- **rsync from controller** — if logs live only on the Slurm controller, rsync them to the monitoring host on a cron. Covered in [Step 2B](#step-2b-rsync-from-the-slurm-controller).

> **Note on slurmd.log:** In most clusters `slurmd.log` is on each compute node separately. If you need per-node daemon logs (XID codes, OOM details), you need rsyslog or another log aggregator to collect them onto one host. The classifier works with only `slurmctld.log` if that aggregation isn't in place — you'll get fewer pattern matches but DCGM-based correlation still works fully.

**2. Poll mode or hook mode?**

- **Poll mode** (default): the classifier reads logs and sacct data every 15 minutes. Simple to set up. MTTR impact: add up to 15 minutes to first classification.
- **Hook mode**: a Slurm epilog script triggers the classifier immediately when a job ends. Classification happens within seconds of failure. Requires write access to the Slurm controller's epilog directory.

Start with poll mode. Migrate to hook mode once you've validated accuracy.

---

## Step 1 — Clone and configure

On the monitoring host:

```bash
git clone https://github.com/alzion/Slurm_job_failure_classifier.git
cd Slurm_job_failure_classifier
cp .env.example .env
```

Open `.env` and fill in:

```bash
# PostgreSQL — change from the defaults before going live
POSTGRES_PASSWORD=<strong-password>

# Your cluster's Prometheus instance
REAL_PROMETHEUS_URL=http://prometheus.your-cluster.internal:9090

# DCGM label names — check these in Step 4 before starting
DCGM_HOSTNAME_LABEL=hostname
DCGM_GPU_INDEX_LABEL=gpu

# The path on this host that contains your Slurm logs
LOG_MOUNT=/mnt/shared/slurm/logs

# Leave as poll until you set up epilog hooks
CLASSIFIER_MODE=poll
```

---

## Step 2 — Wire up log files

Choose the option that matches your cluster's log setup.

### Step 2A — Shared filesystem

If `slurmctld.log` is on a path already visible from the monitoring host (e.g. NFS or Lustre), set `LOG_MOUNT` in `.env` to that path:

```bash
LOG_MOUNT=/shared/logs/slurm
```

Verify the file is readable before starting:

```bash
tail -5 /shared/logs/slurm/slurmctld.log
```

### Step 2B — rsync from the Slurm controller

If logs live only on the Slurm controller, create a local directory on the monitoring host and sync into it:

```bash
mkdir -p /opt/slurm-logs
LOG_MOUNT=/opt/slurm-logs   # set this in .env
```

Add a cron job on the monitoring host to pull logs every 5 minutes:

```bash
# /etc/cron.d/slurm-log-sync
*/5 * * * * root rsync -az --no-owner slurm-controller.internal:/var/log/slurm/ /opt/slurm-logs/
```

For `slurmd.log`, the simplest approach is rsyslog forwarding on each compute node. Add to `/etc/rsyslog.d/slurm-forward.conf` on each node:

```
if $programname == 'slurmd' then @@monitoring-host.internal:514
```

And receive it on the monitoring host into the log directory. If per-node `slurmd.log` is not feasible, the classifier still works — it falls back to `slurmctld.log` patterns and DCGM metrics.

---

## Step 3 — Wire up sacct data

The classifier reads sacct data from `/logs/sacct_data.json`. The `adapt_sacct.py` script fetches it from the Slurm accounting database.

### Option A — Cron (poll mode, recommended to start)

Install the cron on any host with `sacct` access (the Slurm controller is easiest):

```bash
# /etc/cron.d/slurm-sacct-sync
*/15 * * * * slurm python3 /opt/classifier/adapt_sacct.py \
    --lookback 30 \
    --output /opt/slurm-logs/sacct_data.json
```

The output path must match `LOG_MOUNT` so the classifier container can read it.

Verify it works manually first:

```bash
python3 adapt_sacct.py --lookback 30 --output /tmp/test_sacct.json
cat /tmp/test_sacct.json | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'{len(d[\"jobs\"])} jobs')"
```

### Option B — Epilog hook (hook mode, lower latency)

Once poll mode is validated and you want sub-minute classification latency:

1. Copy the epilog script to the Slurm controller:
```bash
cp epilog_hook.sh /etc/slurm/epilog.d/01_classify.sh
chmod +x /etc/slurm/epilog.d/01_classify.sh
```

2. Edit the script to set paths for your environment:
```bash
SACCT_PATH=/opt/slurm-logs/sacct_data.json
# CLASSIFIER_EXEC points at the running classifier container
CLASSIFIER_EXEC="docker exec classifier"
```

3. Tell Slurm to run the epilog. In `slurm.conf`:
```
Epilog=/etc/slurm/epilog.d/01_classify.sh
```

4. Reload Slurm: `scontrol reconfig`

5. Update `.env` on the monitoring host: `CLASSIFIER_MODE=hook`

---

## Step 4 — Verify DCGM metric labels

Before starting the stack, confirm the exact label names your dcgm-exporter uses. The correlation engine queries Prometheus with `{hostname="gpu03"}` by default — if your label is different, zero signals will be found with no error message.

```bash
# Replace with your Prometheus URL
PROM=http://prometheus.your-cluster.internal:9090

# Find what label names your DCGM metrics use
curl -s "$PROM/api/v1/series?match[]=DCGM_FI_DEV_GPU_TEMP" | \
  python3 -c "import json,sys; d=json.load(sys.stdin); print(d['data'][0])"
```

Example output:
```json
{
  "__name__": "DCGM_FI_DEV_GPU_TEMP",
  "hostname": "gpu03",
  "gpu": "0",
  "instance": "gpu03:9400",
  "job": "dcgm"
}
```

From this output, identify:
- The hostname label (`hostname`, `Hostname`, `instance`, or `node`) → set `DCGM_HOSTNAME_LABEL`
- The GPU index label (`gpu`, `GPU_I_ID`) → set `DCGM_GPU_INDEX_LABEL`

Also confirm the metrics exist and have data:

```bash
curl -s "$PROM/api/v1/query?query=DCGM_FI_DEV_ECC_SBE_VOL_TOTAL" | \
  python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d['data']['result']), 'series')"
```

If that returns 0 series, dcgm-exporter is not scraping correctly — fix that before proceeding.

---

## Step 5 — Start the stack

```bash
# Production mode: no simulators, real data sources
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Check all services started:

```bash
docker compose ps
```

Expected: classifier, correlation-engine, node-health-rollup, override-api, grafana, prometheus, pushgateway, alertmanager, postgres all `running`.

Watch the classifier's first run:

```bash
docker logs -f classifier
```

You should see within 15 minutes (poll mode) or on the next job completion (hook mode):

```
2026-06-20 14:00:01 INFO Classifier run starting
2026-06-20 14:00:01 INFO   247 log evidence records, 38 sacct jobs
2026-06-20 14:00:02 INFO   847293 → GPU_HARDWARE (HIGH)
2026-06-20 14:00:02 INFO Done: {'written': 38, 'skipped': 0, 'errors': 0}
```

If you see `0 log evidence records` → the log path is wrong or files are empty (check Step 2).  
If you see `0 sacct jobs` → sacct data is not being written (check Step 3).

---

## Step 6 — Validate

### Check classification accuracy

After the classifier has processed at least one full day of real failures:

```bash
# Run against the live database
python3 tests/eval_classifier.py
```

This compares against the 13 synthetic ground-truth scenarios. On real data the scenarios won't match directly — use it to confirm the database is reachable and classification is happening, then build a real-data ground truth set from incidents you witnessed and know the root cause of.

### Check Grafana dashboards

Open http://monitoring-host:3000 (admin / the password set in `.env`).

The five dashboards that should show data:
- **Fleet Health** — GPU metric time series from your Prometheus
- **Job Failure Analysis** — classified failures from PostgreSQL
- **Pre-failure Signals** — correlation results with lead times
- **Node Reliability** — weekly per-node failure rate
- **Cost Impact** — GPU-hours lost by failure category

If Fleet Health is empty but the others have data, `REAL_PROMETHEUS_URL` is set incorrectly or Grafana's Prometheus datasource URL needs updating in `grafana/provisioning/datasources/`.

### Check the override API

```bash
curl http://monitoring-host:8002/health
# → {"status": "ok"}

curl http://monitoring-host:8002/api/v1/accuracy
# → {"period_days": 30, "overall": {...}, "by_category": [...]}
```

---

## Step 7 — Configure alerts

### Slack

Edit `alertmanager/alertmanager.yml` and replace the placeholder webhook URL:

```yaml
receivers:
  - name: slack-p2
    slack_configs:
      - api_url: 'https://hooks.slack.com/services/YOUR/SLACK/WEBHOOK'
        channel: '#gpu-oncall'
```

### PagerDuty

```yaml
receivers:
  - name: pagerduty-p1
    pagerduty_configs:
      - routing_key: 'YOUR_PAGERDUTY_INTEGRATION_KEY'
```

Reload AlertManager after editing:

```bash
docker compose restart alertmanager
```

---

## Troubleshooting

### Every job classifies as UNKNOWN

The classifier ran but found no log evidence. Check in order:

1. **Log path** — confirm the file is inside the container:
   ```bash
   docker exec classifier ls -la /logs/
   docker exec classifier tail -5 /logs/slurmctld.log
   ```

2. **Log format** — the parser expects Slurm bracket timestamps (`[2026-05-16T12:34:56.000]`). Run one line through manually:
   ```bash
   docker exec classifier python3 -c "
   from classifier.log_parser import _split_line
   print(_split_line('[2026-06-20T14:00:00.000] _node_down: node gpu03 is DOWN'))
   "
   ```
   If it returns `(None, '')`, the timestamp format isn't recognised — check your Slurm log format setting.

3. **sacct state** — many `FAILED` jobs with no log pattern is normal if the jobs are failing for reasons the parser doesn't recognise yet. Check the raw exit codes:
   ```bash
   docker exec postgres psql -U fleet fleetdb -c \
     "SELECT state, COUNT(*) FROM job_events GROUP BY state ORDER BY 2 DESC;"
   ```

### No pre-failure signals in correlation results

1. Confirm Prometheus is reachable from inside the container:
   ```bash
   docker exec correlation-engine curl -s "$REAL_PROMETHEUS_URL/api/v1/query?query=up" | head -c 200
   ```

2. Confirm the hostname label matches your nodes:
   ```bash
   docker exec correlation-engine python3 -c "
   import os, requests
   prom = os.environ['PROMETHEUS_URL']
   label = os.environ.get('DCGM_HOSTNAME_LABEL', 'hostname')
   r = requests.get(f'{prom}/api/v1/series', params={'match[]': 'DCGM_FI_DEV_GPU_TEMP'})
   series = r.json()['data']
   if series:
       print('Labels on first series:', series[0])
   else:
       print('No DCGM_FI_DEV_GPU_TEMP series found')
   "
   ```

3. Check the `correlation_results` table — if rows exist but `signal_detected` is all FALSE, the thresholds may be too high for your hardware:
   ```bash
   docker exec postgres psql -U fleet fleetdb -c \
     "SELECT metric_name, signal_detected, COUNT(*) FROM correlation_results GROUP BY 1,2;"
   ```

### sacct returns no jobs

```bash
# Test sacct directly on the controller
sacct --json --starttime=$(date -d '1 hour ago' +%FT%T) | python3 -c \
  "import json,sys; d=json.load(sys.stdin); print(len(d.get('jobs',[])), 'jobs')"
```

Common causes: Slurm version < 21.08 (no `--json` flag), accounting not enabled in `slurm.conf` (`AccountingStorageType=accounting_storage/slurmdbd`), or the user running the script has no sacct permissions.

### Classifier container restarts repeatedly

```bash
docker logs classifier --tail 50
```

Most common cause: cannot connect to PostgreSQL. Confirm the DB is healthy:

```bash
docker exec postgres pg_isready -U fleet -d fleetdb
```

---

## Running both modes simultaneously

The simulator profile still works alongside the prod overlay. Useful for testing new pattern rules against synthetic data while the real cluster runs in the background:

```bash
# Start real-cluster stack
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# In a separate project directory, run the simulator for development
git clone ... classifier-dev
cd classifier-dev
docker compose --profile simulator up -d
```

They use separate PostgreSQL volumes and different ports, so they don't conflict.
