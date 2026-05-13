# GPU Fleet Failure Classifier

Dockerized system that ingests simulated Slurm job failures and DCGM GPU health metrics, classifies failures by root cause, and surfaces results in Grafana. No real GPU or Slurm cluster required.

## Stack

Python 3.11 · PostgreSQL 16 · Prometheus · Grafana 10 · AlertManager · Docker Compose

## Quick start

```bash
docker compose up -d
```

- Grafana: http://localhost:3000 (admin / admin)
- Prometheus: http://localhost:9090
- AlertManager: http://localhost:9093

## What it does

Three simulators generate realistic data on a 4-hour cycle:
- **slurm-simulator** — writes `slurmctld.log` and `slurmd.log` with failure patterns
- **sacct-simulator** — writes `sacct_data.json` with job records
- **dcgm-simulator** — pushes GPU metrics (temp, ECC, NVLink CRC, XID, clock) to Prometheus via pushgateway

The classifier reads logs + sacct data every 15 minutes and assigns one of 8 failure categories to each job:

`GPU_HARDWARE` · `NCCL_COMM_FAILURE` · `CUDA_OOM` · `THERMAL_THROTTLE` · `INFRA_STORAGE` · `PREEMPTION` · `TIMEOUT` · `USER_ERROR`

The correlation engine queries Prometheus for pre-failure DCGM signals and computes lead times. A node health rollup aggregates weekly per-node failure stats.

## Evaluate classifier accuracy

```bash
python3 tests/eval_classifier.py
```

Scores against `tests/ground_truth.json` (13 labeled scenarios). Currently 13/13 correct.

## Structure

```
simulator/      slurm, sacct, and dcgm simulators
classifier/     log parser, sacct parser, classifier, correlation engine, node health rollup
grafana/        5 provisioned dashboards + datasource config
db/             PostgreSQL schema
docs/           escalation runbook, SLA document, postmortem template
tests/          ground truth dataset + evaluator
```
