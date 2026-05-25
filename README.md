# GPU Fleet Failure Classifier

GPU cluster on-call engineers typically learn what caused a job failure by reading logs manually — a process that takes 20–40 minutes and requires deep familiarity with Slurm, CUDA, and NCCL internals. On a 64-GPU cluster running LLM training jobs at ~$100/hour, that triage window represents significant wasted compute and a full iteration cycle for the ML team.

This system classifies Slurm job failures automatically by root cause, correlates failures against pre-failure GPU health signals with up to 90 minutes of lead time, and surfaces results to on-call engineers through Grafana dashboards and AlertManager. The goal is to reduce P1 incident MTTR from ~40 minutes to under 10.

No real GPU cluster or Slurm installation required — all components run in Docker.

## Stack

Python 3.11 · PostgreSQL 16 · Prometheus · Grafana 10 · AlertManager · Docker Compose

## Quick start

```bash
docker compose up -d
```

- Grafana: http://localhost:3000 (admin / admin)
- Prometheus: http://localhost:9090
- AlertManager: http://localhost:9093
- Simulator UI (oncall training): http://localhost:8000

## How it works

Three simulators generate realistic data on a 4-hour cycle:
- **slurm-simulator** — writes `slurmctld.log` and `slurmd.log` with failure patterns
- **sacct-simulator** — writes `sacct_data.json` with job records
- **dcgm-simulator** — pushes GPU metrics (temp, ECC, NVLink CRC, XID, clock) to Prometheus via pushgateway

The classifier reads logs + sacct data every 15 minutes and assigns one of 8 failure categories to each job:

`GPU_HARDWARE` · `NCCL_COMM_FAILURE` · `CUDA_OOM` · `THERMAL_THROTTLE` · `INFRA_STORAGE` · `PREEMPTION` · `TIMEOUT` · `UNKNOWN`

The correlation engine queries Prometheus for pre-failure DCGM signals and computes lead times (up to 90 minutes before failure for GPU_HARDWARE events). A node health rollup aggregates weekly per-node failure stats for the node reliability dashboard.

## Evaluate classifier accuracy

```bash
python3 tests/eval_classifier.py
```

Scores against `tests/ground_truth.json` (13 labeled scenarios). Currently 13/13 correct.

## Operator Training

The `simulator-ui` is a structured on-call readiness program — not just a React frontend. It presents realistic GPU failure incidents drawn from the same 8-category taxonomy used by the live classifier, and requires the engineer to diagnose the root cause using the same tools they would use in production (log viewer, simulated Grafana panels, Slack feed).

**What it tests:**
- Recognizing pre-failure signal patterns (ECC SBE rate, NVLink CRC, thermal excursion)
- Distinguishing infrastructure failures from user errors
- Knowing when to drain a node vs. resubmit a job

**Passing score:** ≥ 80% across 5 scored scenarios

**Program role:** Per `docs/roadmap.md`, 100% of the on-call rotation must complete training with a passing score before Phase 1 go-live (Milestone M1.3). This is a hard launch gate.

## Operational Artifacts

Eight documents in `docs/` cover the full program lifecycle:

| Document | Audience | Purpose |
|----------|----------|---------|
| `PRD.md` | Stakeholders, hiring reviewers | Business case, goals, non-goals, success metrics |
| `roadmap.md` | Program team | 3-phase, 24-week milestones with exit criteria |
| `stakeholder_map.md` | Program team | RACI matrix, concerns/mitigations, comms plan |
| `risk_register.md` | Program team | 10-item risk log with owners and mitigations |
| `launch_criteria.md` | Infra Lead, On-call Lead | Phase 1 production readiness checklist |
| `escalation_runbook.md` | On-call engineers | Response procedures for all 8 failure categories |
| `sla_document.md` | On-call team, FinOps | P1–P4 response tiers + classifier service SLOs |
| `postmortem_template.md` | On-call team | Filled-in postmortem for the S01 GPU hardware incident |

## Structure

```
simulator/      slurm, sacct, and dcgm simulators
simulator-ui/   oncall training application (FastAPI backend + React frontend)
classifier/     log parser, sacct parser, classifier, correlation engine, node health rollup
grafana/        5 provisioned dashboards + datasource config
db/             PostgreSQL schema
docs/           PRD, roadmap, stakeholder map, risk register, launch criteria,
                escalation runbook, SLA document, postmortem
tests/          ground truth dataset + evaluator
```
