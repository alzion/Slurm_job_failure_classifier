# GPU Fleet Failure Classifier

Automatically classifies Slurm job failures by root cause and surfaces pre-failure GPU health signals up to 90 minutes before a job dies.

On a 64-GPU cluster running LLM training jobs at ~$100/hour, manual triage takes 20–40 minutes and requires deep familiarity with Slurm, CUDA, and NCCL internals. This system reduces P1 incident MTTR to under 10 minutes by correlating log patterns, `sacct` records, and DCGM time series into a single verdict with evidence — no real GPU cluster required.

---

## Table of Contents

- [What it does](#what-it-does)
- [Quick start](#quick-start)
- [Services and ports](#services-and-ports)
- [Failure categories](#failure-categories)
- [How it works](#how-it-works)
- [Evaluate classifier accuracy](#evaluate-classifier-accuracy)
- [Operator training](#operator-training)
- [Operational documents](#operational-documents)
- [Repository layout](#repository-layout)
- [Project status](#project-status)

---

## What it does

- Reads `slurmctld.log`, `slurmd.log`, and `sacct_data.json` every 15 minutes
- Assigns each failed job one of 8 root-cause categories with a confidence level
- Queries Prometheus for DCGM pre-failure signals and computes lead times (up to 90 minutes before failure for GPU hardware events)
- Rolls up per-node failure history weekly for a node reliability dashboard
- Routes P1–P4 alerts to PagerDuty and Slack via AlertManager
- Scores 13/13 on the labeled ground-truth dataset

No real GPU cluster or Slurm installation required — everything runs in Docker.

---

## Quick start

**Prerequisites:** Docker with Compose v2, 4 GB free RAM

```bash
git clone <repo-url>
cd Slurm_job_failure_classifier
docker compose up -d
```

Wait ~30 seconds for all services to become healthy, then open Grafana:

```
http://localhost:3000   (admin / admin)
```

The simulators begin generating data immediately on a 4-hour cycle. The classifier runs its first pass within 15 minutes. Dashboards will show live failure events, correlation results, and node health within that first cycle.

---

## Services and ports

| Service | URL | Notes |
|---|---|---|
| Grafana | http://localhost:3000 | 5 provisioned dashboards, login: admin / admin |
| Prometheus | http://localhost:9090 | DCGM metric time series |
| Pushgateway | http://localhost:9091 | Receives metrics from dcgm-simulator |
| AlertManager | http://localhost:9093 | P1–P4 alert routing |
| Simulator UI | http://localhost:8001 | On-call training program |
| PostgreSQL | localhost:5433 | DB: `fleetdb`, user: `fleet`, password: `fleet123` |

All ports bind to `127.0.0.1` — not exposed to the network.

---

## Failure categories

The classifier assigns every failed job exactly one category:

| Category | Typical signals |
|---|---|
| `GPU_HARDWARE` | XID 48, ECC double-bit error, SBE rate > 30/hr |
| `NCCL_COMM_FAILURE` | NVLink CRC errors, NCCL timeout in log |
| `CUDA_OOM` | `cudaMalloc` failed, exit code 1 + OOM pattern |
| `THERMAL_THROTTLE` | GPU temp > 82 °C, SM clock drop |
| `INFRA_STORAGE` | NFS mount failure, I/O error in slurmd log |
| `PREEMPTION` | `JobState=PREEMPTED` in sacct |
| `TIMEOUT` | `JobState=TIMEOUT`, elapsed ≥ timelimit |
| `UNKNOWN` | No matching pattern |

---

## How it works

Two data paths converge in the classifier:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          SIMULATORS  (data generation)                      │
│                                                                              │
│  ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────────┐    │
│  │  slurm-simulator │   │  sacct-simulator  │   │   dcgm-simulator     │    │
│  │                  │   │                   │   │                      │    │
│  │ slurmctld.log    │   │ sacct_data.json   │   │ GPU metrics          │    │
│  │ slurmd.log       │   │ (job records)     │   │ (temp, ECC, NVLink,  │    │
│  │ (every 5 min)    │   │ (every 5 min)     │   │  XID, clock, power)  │    │
│  └────────┬─────────┘   └────────┬──────────┘   └──────────┬───────────┘   │
└───────────┼──────────────────────┼───────────────────────────┼──────────────┘
            │  shared /logs volume │                           │ HTTP POST
            ▼                      ▼                           ▼
     ┌─────────────┐       ┌──────────────┐           ┌────────────────┐
     │ log files   │       │ sacct_data   │           │  Pushgateway   │
     └──────┬──────┘       └──────┬───────┘           └───────┬────────┘
            └──────────┬──────────┘                           │ scrape
                       │  read every 15 min                   ▼
                       ▼                             ┌────────────────┐
              ┌─────────────────┐                   │   Prometheus   │
              │   CLASSIFIER    │◄──────────────────│   :9090        │
              │                 │  query_range API   └───────┬────────┘
              │  log_parser.py  │  (6-hr lookback)           │
              │  sacct_parser   │                            │
              │  classifier.py  │                            │
              │  correlation    │                            │
              │  _engine.py     │                            │
              └────────┬────────┘                           │
                       │ upsert                              │
                       ▼                                     │
              ┌─────────────────┐                           │
              │   PostgreSQL    │◄──────────────────────────┘
              │   :5432         │
              └────────┬────────┘
                       │ SQL datasource
                       ▼
              ┌─────────────────────────────────────────┐
              │               Grafana  :3000             │
              │  fleet_health        (Prometheus)        │
              │  job_failure_analysis (PostgreSQL)       │
              │  prefailure_signals  (PostgreSQL+Prom)   │
              │  node_reliability    (PostgreSQL)        │
              │  cost_impact         (PostgreSQL)        │
              └────────────────┬────────────────────────┘
                               │ alert rules
                               ▼
                      ┌─────────────────┐
                      │  AlertManager   │
                      │  P1 → PagerDuty │
                      │  P2–P4 → Slack  │
                      └─────────────────┘
```

**Log / sacct path** — the classifier parses failure patterns from log files and `sacct` records every 15 minutes, assigns a category and confidence level, then writes to PostgreSQL.

**Metrics path** — DCGM metrics (temperature, ECC error counts, NVLink CRC, XID codes, SM clock, power) are pushed to Pushgateway every 30 seconds and stored in Prometheus. The correlation engine queries a 6-hour lookback window to find which pre-failure signals appeared before each classified job and computes their lead times.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full component table and design constraints.

---

## Evaluate classifier accuracy

```bash
python3 tests/eval_classifier.py
```

Scores the classifier against `tests/ground_truth.json`, which contains 13 labeled scenarios covering all 8 failure categories. Expected output:

```
13/13 correct (100%)
```

To add a new scenario, append an entry to `tests/ground_truth.json` following the existing schema and re-run.

---

## Operator training

The `simulator-ui` at http://localhost:8001 is a structured on-call readiness program. It presents realistic GPU failure incidents drawn from the same 8-category taxonomy and requires the engineer to diagnose the root cause using the same tools they'd use in production.

**What it tests:**
- Recognizing pre-failure signal patterns (ECC SBE rate, NVLink CRC, thermal excursion)
- Distinguishing infrastructure failures from user errors
- Knowing when to drain a node vs. requeue a job

**Passing score:** ≥ 80% across 5 scored scenarios

Per `docs/roadmap.md`, 100% of the on-call rotation must pass before Phase 1 go-live (Milestone M1.3). This is a hard launch gate.

---

## Operational documents

All documents live in `docs/`:

| Document | Audience | Purpose |
|---|---|---|
| `PRD.md` | Stakeholders | Business case, goals, non-goals, success metrics |
| `roadmap.md` | Program team | 3-phase, 24-week milestones with exit criteria |
| `stakeholder_map.md` | Program team | RACI matrix, concerns, mitigations, comms plan |
| `risk_register.md` | Program team | 10-item risk log with owners and mitigations |
| `launch_criteria.md` | Infra Lead, On-call Lead | Phase 1 production readiness checklist |
| `escalation_runbook.md` | On-call engineers | Response procedures for all 8 failure categories |
| `sla_document.md` | On-call team, FinOps | P1–P4 response tiers and classifier service SLOs |
| `postmortem_template.md` | On-call team | Filled-in postmortem for the S01 GPU hardware incident |

---

## Repository layout

```
classifier/       log parser, sacct parser, classifier, correlation engine, node health rollup
simulator/        slurm, sacct, and dcgm data simulators
simulator-ui/     on-call training app (FastAPI backend + React frontend)
grafana/          5 provisioned dashboards + datasource config
prometheus/       prometheus.yml + alert rules
alertmanager/     alertmanager.yml (routing config)
db/               PostgreSQL schema
docs/             PRD, roadmap, stakeholder map, risk register, launch criteria,
                  escalation runbook, SLA document, postmortem
tests/            ground truth dataset (13 labeled scenarios) + evaluator
logs/             shared volume for simulator → classifier log exchange
```

---

## Project status

Active development. The classifier and all 5 dashboards are complete and accurate against the ground-truth dataset. The on-call training program (simulator-ui) is in integration testing. Phase 1 production deployment is gated on operator training completion (M1.3) — see `docs/roadmap.md` for the full milestone schedule.
