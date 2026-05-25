# GPU Fleet Failure Classifier — Design Document

## Background

GPU cluster on-call engineers today diagnose job failures by reading raw Slurm logs and sacct records manually. This takes 20–40 minutes per incident and requires expertise most rotation members don't have uniformly. Pre-failure GPU health signals — rising ECC single-bit error rates, NVLink CRC increments, thermal excursions — are already present in DCGM/Prometheus data 60–90 minutes before failure, but go unread until after the job dies. The business case and program goals are in `docs/PRD.md`. This document covers how the system is built and why specific design decisions were made.

## Goals

- Surface failure root cause automatically within 20 minutes of a job ending, without requiring an engineer to read logs.
- Correlate failures against pre-failure DCGM signals and compute lead times, enabling proactive intervention in Phase 2+.
- Provide structured, queryable failure data for capacity planning and hardware refresh workflows.

## Non-Goals

- **Not real-time alerting.** This system classifies completed or failing jobs. Real-time alerting is AlertManager's responsibility; this system feeds it, not replaces it.
- **Not automated remediation in Phase 1.** Phase 1 is information-only. Automated node cordoning is Phase 2, gated on stakeholder approval (see `docs/roadmap.md`).

---

## What This Is
A Dockerized system that ingests simulated Slurm job failure logs and DCGM GPU health metrics,
classifies failures by root cause, correlates failures against pre-failure GPU health signals,
and surfaces results in Grafana. No real GPU or Slurm cluster required.

---

## Stack
- Python 3.11 (simulators, classifier, correlation engine)
- PostgreSQL 16 (job events, correlation results, node health)
- Prometheus (GPU metric time series via pushgateway)
- Grafana 10 (5 dashboards, provisioned as code)
- AlertManager (P1–P4 routing to Slack + PagerDuty webhooks)
- Docker Compose (single `docker-compose up` to run everything)

---

## Directory Structure
```
project/
├── docker-compose.yml
├── prometheus/
│   └── prometheus.yml
├── alertmanager/
│   └── alertmanager.yml
├── grafana/
│   ├── provisioning/
│   │   ├── datasources/datasources.yml
│   │   └── dashboards/dashboards.yml
│   └── dashboards/
│       ├── fleet_health.json
│       ├── job_failure_analysis.json
│       ├── prefailure_signals.json
│       ├── node_reliability.json
│       └── cost_impact.json
├── simulator/
│   ├── slurm_log_sim.py      # generates slurmctld.log + slurmd.log
│   ├── sacct_sim.py           # generates sacct JSON records
│   └── dcgm_sim.py            # pushes metrics to pushgateway
├── classifier/
│   ├── log_parser.py
│   ├── sacct_parser.py
│   ├── classifier.py
│   └── correlation_engine.py
├── db/
│   └── schema.sql
└── docs/
    ├── escalation_runbook.md
    ├── sla_document.md
    └── postmortem_template.md
```

---

## Docker Compose Services
```
slurm-simulator   python simulator/slurm_log_sim.py  (runs every 5 min via schedule)
dcgm-simulator    python simulator/dcgm_sim.py        (runs every 30 sec)
pushgateway       prom/pushgateway:latest
prometheus        prom/prometheus:latest               (scrapes pushgateway)
postgres          postgres:16                          (init with db/schema.sql)
classifier        python classifier/classifier.py      (runs every 15 min)
grafana           grafana/grafana:10.0.0               (provisioned via /grafana dir)
alertmanager      prom/alertmanager:latest
```

Shared volume: `./logs` mounted into slurm-simulator (write) and classifier (read).
Postgres env: POSTGRES_DB=fleetdb, POSTGRES_USER=fleet, POSTGRES_PASSWORD=fleet123.
Prometheus scrape: pushgateway on port 9091.
Grafana port: 3000. Postgres datasource port: 5432.

---

## Failure Taxonomy (8 categories)

| Category | sacct State | Primary Log Pattern | DCGM Pre-failure Signal |
|---|---|---|---|
| GPU_HARDWARE | NODE_FAIL | `Xid ... 48` or `ECC Double Bit Error` | ECC_SBE rising rate → ECC_DBE > 0 |
| NCCL_COMM_FAILURE | FAILED | `ncclSystemError` or `Socket: Connection timed out` | NVLINK_CRC errors incrementing |
| CUDA_OOM | OUT_OF_MEMORY | `CUDA out of memory` or `cudaErrorMemoryAllocation` | None |
| THERMAL_THROTTLE | FAILED | No log (inferred from DCGM) | GPU_TEMP > 82°C + SM_CLOCK drop > 15% |
| INFRA_STORAGE | FAILED | `Stale file handle` or `lustre` or `NFS` | None |
| PREEMPTION | PREEMPTED | sacct State only (signal 9) | None |
| USER_ERROR | FAILED | ExitCode non-zero, no hardware pattern | None |
| TIMEOUT | TIMEOUT | sacct State only | None |

Classification priority order: GPU_HARDWARE > NCCL_COMM_FAILURE > CUDA_OOM >
THERMAL_THROTTLE > INFRA_STORAGE > PREEMPTION > TIMEOUT > USER_ERROR.
Apply first matching rule.

---

## DCGM Metrics (exact Prometheus metric names as used by dcgm-exporter)

| Metric | Normal Range | Pre-failure Pattern | Threshold |
|---|---|---|---|
| `DCGM_FI_DEV_GPU_UTIL` | 80–95% training | No change | — |
| `DCGM_FI_DEV_ECC_SBE_VOL_TOTAL` | 0–5 cumulative | Rising rate before DBE | > 30/hour rate |
| `DCGM_FI_DEV_ECC_DBE_VOL_TOTAL` | Always 0 | Jumps to > 0 at failure | Any > 0 |
| `DCGM_FI_DEV_GPU_TEMP` | 65–78°C | Rises before throttle | > 82°C sustained |
| `DCGM_FI_DEV_SM_CLOCK` | 1410 MHz (A100) | Drops during throttle | > 15% below baseline |
| `DCGM_FI_DEV_NVLINK_CRC_FLIT_ERROR_COUNT_TOTAL` | 0 always | Increments before NCCL | Any increment from 0 |
| `DCGM_FI_DEV_XID_ERRORS` | 0 | XID 48=ECC, 74=NVLink, 79=bus | Any > 0 |
| `DCGM_FI_DEV_POWER_USAGE` | 300–400W | No pattern | — |
| `DCGM_FI_DEV_MEM_COPY_UTIL` | 40–80% | No pattern | — |

Prometheus labels on all metrics: `hostname`, `gpu` (index 0–7), `modelName` (A100-SXM4-80GB).
Nodes: gpu01 through gpu10. Jobs use contiguous node ranges e.g. gpu[03-10].

---

## Slurm Log Patterns (exact strings to generate per failure type)

**GPU_HARDWARE** (slurmctld.log):
```
slurmctld: _node_down: node gpu03 is DOWN: Not responding
slurmctld: _job_requeue: requeueing job {job_id} due to node failure gpu03
```
**GPU_HARDWARE** (slurmd.log on gpu03):
```
error: NVRM: Xid (PCI:0000:03:00): 48, pid='<unknown>', name=<unknown>
error: ECC Double Bit Error detected on GPU 0
```

**NCCL_COMM_FAILURE** (slurmd.log):
```
error: [ncclSystemError] Socket: Connection timed out <net/socket.cc:490>
error: NCCL version 2.18.3 - unhandled system error (ncclSystemError)
```

**CUDA_OOM** (slurmd.log):
```
RuntimeError: CUDA out of memory. Tried to allocate 18.50 GiB
error: CUDA error: out of memory (error 2)
```

**INFRA_STORAGE** (slurmd.log):
```
error: /scratch/lustre: Stale file handle
OSError: [Errno 116] Stale file handle: '/lustre/scratch/job_{job_id}'
```

**USER_ERROR** (slurmd.log):
```
srun: error: Task launch for StepId={job_id}.0 failed on node gpu02: execve failed
error: execve(): /usr/bin/python3.11: No such file or directory
```

**THERMAL_THROTTLE**: no log — detected via DCGM metrics only.
**PREEMPTION / TIMEOUT**: sacct State field only, no specific log pattern.

---

## sacct JSON Schema (one object per job)

```json
{
  "JobID": "847293",
  "JobName": "llama3-70b-finetune",
  "User": "researcher-01",
  "Account": "nlp-team",
  "State": "NODE_FAIL",
  "ExitCode": "1:0",
  "DerivedExitCode": "1:0",
  "Reason": "NodeDown",
  "NodeList": "gpu[03-10]",
  "Submit": "2024-03-15T10:00:00",
  "Start": "2024-03-15T14:23:01",
  "End": "2024-03-15T16:47:12",
  "Elapsed": "02:24:11",
  "AllocGRES": "gpu:8",
  "ReqMem": "320G",
  "MaxRSS": "298G"
}
```

NodeList parse: `gpu[03-10]` → `[gpu03, gpu04, ..., gpu10]`.
Use regex: expand bracket notation to list of hostnames.

---

## PostgreSQL Schema (db/schema.sql)

```sql
CREATE TABLE job_events (
  id SERIAL PRIMARY KEY,
  job_id VARCHAR(20) UNIQUE NOT NULL,
  job_name VARCHAR(100),
  account VARCHAR(50),
  state VARCHAR(20),
  exit_code VARCHAR(10),
  node_list TEXT[],
  gpu_count INTEGER,
  start_time TIMESTAMPTZ,
  end_time TIMESTAMPTZ,
  elapsed_seconds INTEGER,
  failure_category VARCHAR(30),
  classification_confidence VARCHAR(10),
  log_patterns_matched JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE correlation_results (
  id SERIAL PRIMARY KEY,
  job_id VARCHAR(20) REFERENCES job_events(job_id),
  node_hostname VARCHAR(20),
  metric_name VARCHAR(80),
  signal_detected BOOLEAN,
  signal_onset_time TIMESTAMPTZ,
  lead_time_seconds INTEGER,
  baseline_value FLOAT,
  peak_anomaly_value FLOAT,
  anomaly_ratio FLOAT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE node_health_weekly (
  id SERIAL PRIMARY KEY,
  node_hostname VARCHAR(20),
  week_start DATE,
  total_jobs INTEGER DEFAULT 0,
  failed_jobs INTEGER DEFAULT 0,
  failure_rate FLOAT,
  hardware_failures INTEGER DEFAULT 0,
  nccl_failures INTEGER DEFAULT 0,
  ecc_sbe_accumulated INTEGER DEFAULT 0,
  avg_temperature FLOAT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(node_hostname, week_start)
);
```

---

## Correlation Algorithm (pseudocode for correlation_engine.py)

```
For each job in job_events WHERE failure_category IN ('GPU_HARDWARE','NCCL_COMM_FAILURE','THERMAL_THROTTLE')
  AND job_id NOT IN (SELECT DISTINCT job_id FROM correlation_results):

  nodes = parse_nodelist(job.node_list)  # expand gpu[03-10] to list
  T = job.end_time
  baseline_start = T - 6 hours
  baseline_end   = T - 2 hours
  anomaly_start  = T - 2 hours
  anomaly_end    = T

  For each node in nodes:
    For each metric in SIGNAL_METRICS:  # ECC_SBE, ECC_DBE, NVLINK_CRC, GPU_TEMP, SM_CLOCK, XID

      baseline_series = prometheus.query_range(metric, node, baseline_start, baseline_end, step=60s)
      anomaly_series  = prometheus.query_range(metric, node, anomaly_start, anomaly_end, step=60s)

      baseline_rate = mean(diff(baseline_series))   # for counters: rate of change
      anomaly_rate  = mean(diff(anomaly_series))    # for counters: rate of change
      baseline_mean = mean(baseline_series)          # for absolute metrics
      anomaly_mean  = mean(anomaly_series)           # for absolute metrics

      # Apply threshold per metric
      if metric == ECC_SBE: signal = anomaly_rate > 30  # per hour equivalent
      if metric == ECC_DBE: signal = max(anomaly_series) > 0
      if metric == NVLINK_CRC: signal = anomaly_rate > 0 and baseline_rate == 0
      if metric == GPU_TEMP: signal = anomaly_mean > 82
      if metric == SM_CLOCK: signal = anomaly_mean < baseline_mean * 0.85
      if metric == XID: signal = max(anomaly_series) > 0

      onset_time = first timestamp in anomaly_series where threshold crossed (if signal)
      lead_time  = T - onset_time (if signal, else NULL)

      INSERT INTO correlation_results (job_id, node_hostname, metric_name,
        signal_detected, signal_onset_time, lead_time_seconds,
        baseline_value, peak_anomaly_value, anomaly_ratio)
```

Prometheus HTTP API: `GET /api/v1/query_range?query=<metric>{hostname="<node>"}&start=<unix>&end=<unix>&step=60`

---

## Scenario Catalog (8 failure + 5 healthy — simulator must generate all 13)

**S01 GPU_HARDWARE**: job 847293, gpu[03-10], failure at T.
  ECC_SBE on gpu03: 0 until T-3h, then +5/hr, +18/hr at T-2h, +67/hr at T-90min.
  ECC_DBE on gpu03: 1 at T-5min.
  XID on gpu03: 48 at T-5min.

**S02 NCCL_COMM_FAILURE**: job 847301, gpu[01-08], failure at T.
  NVLINK_CRC on gpu01: 0 until T-90min, then +2/min incrementing.

**S03 CUDA_OOM**: job 847310, gpu[05-06], failure at T.
  All metrics normal. sacct State=OUT_OF_MEMORY.

**S04 THERMAL_THROTTLE**: job 847318, gpu[07-08], failure at T.
  GPU_TEMP on gpu07: 72°C until T-45min, rises to 86°C.
  SM_CLOCK on gpu07: 1410 MHz until T-40min, drops to 1185 MHz.

**S05 INFRA_STORAGE**: job 847325, gpu[02-04], failure at T.
  All GPU metrics normal.

**S06 PREEMPTION**: job 847332, gpu[09-10], sacct State=PREEMPTED.
  All metrics normal.

**S07 USER_ERROR**: job 847340, gpu[01], sacct State=FAILED ExitCode=1:0.
  All metrics normal.

**S08 TIMEOUT**: job 847348, gpu[03-06], sacct State=TIMEOUT.
  All metrics normal.

**S09–S13 HEALTHY**: jobs 847350-847354, various node sets, State=COMPLETED.
  All metrics normal throughout.

Stagger failure times: each scenario separated by 30-60 minutes.
Simulator should replay scenarios on a loop with new job IDs each cycle.

---

## Grafana Dashboard Specs (5 dashboards)

**fleet_health.json**: 4 panels. Heatmap: GPU_TEMP by node (last 24h).
Time series: ECC_SBE_VOL_TOTAL rate by node. Time series: NVLINK_CRC rate by node.
Stat panel: XID_ERRORS count (threshold: green=0, red>0).

**job_failure_analysis.json**: 4 panels. Bar chart: failure count by category by day (30d, from PostgreSQL).
Pie: failure category distribution (7d). Table: top 5 failing nodes (from node_health_weekly).
Time series: failures/day with 7-day moving average.

**prefailure_signals.json**: 4 panels. Big number: % failures with detectable precursor
(query: SELECT 100.0*COUNT(*) FILTER (WHERE signal_detected) / COUNT(*) FROM correlation_results).
Histogram: lead_time_seconds distribution. Scatter: anomaly_ratio vs lead_time_seconds colored by metric_name.
Table: active precursor alerts (current jobs with signal_detected in last 2h).

**node_reliability.json**: 1 panel. Table with conditional formatting:
node_hostname, total_jobs, failed_jobs, failure_rate (red>15%, amber>5%), hardware_failures, ecc_sbe_accumulated.

**cost_impact.json**: 3 panels. Variable: gpu_cost_per_hour (default 2.00).
Bar: GPU-hours lost per category (gpu_count × elapsed_seconds/3600 grouped by failure_category).
Stat: total cost impact ($). Stat: preventable cost (failures with signal_detected=true).

---

## TPM Artifact Specs

**escalation_runbook.md**: One H2 section per failure category (8 sections).
Each section: Detection trigger | Severity | Who gets paged | Acknowledge SLA |
Step 1–5 diagnostic steps | Node cordon command | Job owner message template | Escalation path.
Node cordon command: `scontrol update NodeName=<node> State=drain Reason="<category> detected"`

**sla_document.md**: 4 tiers (P1/P2/P3/P4).
P1: acknowledge 15min, diagnosis 30min, cordon 1hr, vendor ticket 2hr, replacement 72hr.
P2: acknowledge 30min, team engaged 2hr, resolved 24hr.
P3: acknowledge 4hr business hours, self-service remediation.
P4: automated notification only, no ops SLA.

**postmortem_template.md**: Write a complete filled-in postmortem for S01 GPU_HARDWARE.
Sections: Incident ID | Severity | Impact | Timeline with exact timestamps from S01 |
Root cause | Contributing factors | 5 Whys | Action items (3 items with owner + due date) |
What pre-failure analyzer showed (retrospective: signal detectable at T-2h31m with 2h31m lead time).

---

## Alternatives Considered

### 1. LLM-based log classification instead of regex rules

**Approach:** Send raw log lines to a language model at classification time; ask it to identify the failure category.

**Why rejected:**
- **Latency:** A classification run that queries an LLM API adds 2–5 seconds per job. At 15-minute intervals across hundreds of jobs, this compounds. On-call tools must be fast and predictable.
- **Cost:** At ~$0.01–0.05 per classification, a busy cluster running thousands of jobs per day would generate meaningful API costs with no accuracy advantage over well-tuned regex.
- **Non-determinism:** LLM outputs are not guaranteed to be consistent across API versions. A model upgrade could silently shift classification behavior. Regex rules are auditable, version-controlled, and deterministic.
- **Offline requirement:** Classifiers may run in air-gapped or restricted-network environments (some GPU clusters have no internet egress). Regex rules have no external dependency.

**If reconsidered:** LLM classification is worth revisiting for the `UNKNOWN` category only — using it as a second-pass on unclassified failures to identify novel failure modes and suggest new rules. This keeps the hot path deterministic while using LLM for exploratory analysis.

---

### 2. Classify using only sacct state fields, without log parsing

**Approach:** Use `sacct` state (`NODE_FAIL`, `OUT_OF_MEMORY`, `FAILED`, `TIMEOUT`, etc.) as the sole classification signal. No log parsing.

**Why rejected:**
- **sacct state is too coarse.** The `FAILED` state covers NCCL communication failures, Lustre/NFS storage errors, user code errors, and DCGM-silent GPU issues — all mapped to the same string. Log parsing is what distinguishes these cases.
- **`NODE_FAIL` is the only reliably specific state.** It maps cleanly to `GPU_HARDWARE`. Everything else requires log evidence or DCGM correlation to classify correctly.
- **Thermal throttle is invisible to sacct.** A job that fails due to thermal throttling shows `FAILED` in sacct with no distinguishing signal. DCGM correlation is the only detection path.

**What was kept from this approach:** sacct state is used as a *hint* (first pass), not the sole signal. State hints (`NODE_FAIL → GPU_HARDWARE`, `OUT_OF_MEMORY → CUDA_OOM`) seed the candidate list; log evidence and DCGM data refine it.

---

## Open Questions

| # | Question | Proposed Resolution | Decision Owner | Due |
|---|----------|---------------------|----------------|-----|
| OQ-D1 | Should the classifier own the alerting path (write directly to AlertManager API), or should it write failure events to a queue that AlertManager consumes via a webhook receiver? | Classifier writes to PostgreSQL only; a separate Prometheus recording rule or Grafana alert queries the DB and fires through AlertManager. This keeps the classifier stateless with respect to alerting and makes alert routing changes independent of classifier deployments. | Infra Lead | Phase 1 design review |
| OQ-D2 | At what log volume does the current full-file re-parse approach need to be replaced with offset tracking or log streaming? | Current approach re-reads full log files every 15 minutes. At ~10 MB/day (estimated for a 10-node cluster), this is acceptable. At ~100 MB/day (100-node cluster, high failure rate), re-parse latency may exceed the 15-minute interval. Switch to offset-based incremental parsing when log growth rate exceeds 50 MB/day. | Infra | Phase 2 capacity review |
| OQ-D3 | Who defines the per-GPU-SKU thermal threshold table? The current threshold (> 82°C) is calibrated for A100-SXM4-80GB. H100 and other SKUs have different TDP and throttle onset temperatures. | Hardware Reliability team owns the threshold table, versioned in `classifier/thermal_thresholds.json` (to be created). Infra consumes it. | HW Reliability + Infra | Phase 2 M2.1 |

---

## Future Work

The following items are explicitly deferred to Phase 2 or Phase 3. They are noted here so the design record reflects what was considered and not just what was built.

- **Automated node cordon on ECC_SBE threshold** (Phase 2 M2.1): The classifier already detects the ECC_SBE rate signal. Triggering an automated `scontrol drain` requires stakeholder approval and a false-positive rate of zero over a 30-day window. See `docs/roadmap.md`.
- **Pre-failure alerting with lead-time guarantee** (Phase 3 M3.1): Alert on active pre-failure signals while a job is still running. Requires the correlation engine to run in near-real-time (currently runs post-failure) and a Prometheus recording rule to evaluate signal thresholds on live metrics.
- **Per-SKU thermal threshold table** (Phase 2+): See OQ-D3 above. Required before expanding to H100 or other non-A100 nodes.
