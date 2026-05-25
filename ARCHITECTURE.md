# Architecture — GPU Fleet Failure Classifier

## System Overview

The system has two data paths that converge in the classifier:

1. **Log / sacct path** — discrete failure events written to files, parsed every 15 minutes
2. **Metrics path** — continuous GPU health time series pushed to Prometheus every 30 seconds

Both paths feed PostgreSQL; Grafana queries PostgreSQL and Prometheus to produce dashboards.

---

## Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          SIMULATORS  (data generation)                      │
│                                                                              │
│  ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────────┐    │
│  │  slurm-simulator │   │  sacct-simulator  │   │   dcgm-simulator     │    │
│  │                  │   │                   │   │                      │    │
│  │ slurmctld.log    │   │ sacct_data.json   │   │ GPU metrics          │    │
│  │ slurmd.log       │   │ (job records)     │   │ (temp, ECC, NVLink,  │    │
│  │ (failure patterns│   │                   │   │  XID, clock, power)  │    │
│  │  every 5 min)    │   │ (every 5 min)     │   │ (every 30 sec)       │    │
│  └────────┬─────────┘   └────────┬──────────┘   └──────────┬───────────┘   │
└───────────┼──────────────────────┼───────────────────────────┼──────────────┘
            │                      │                           │
            │  shared /logs volume │                           │ HTTP POST
            ▼                      ▼                           ▼
     ┌─────────────┐       ┌──────────────┐           ┌────────────────┐
     │ slurmctld   │       │ sacct_data   │           │  Pushgateway   │
     │ .log        │       │ .json        │           │  :9091         │
     │ slurmd.log  │       │              │           └───────┬────────┘
     └──────┬──────┘       └──────┬───────┘                  │ scrape
            │                     │                           ▼
            │                     │                  ┌────────────────┐
            └──────────┬──────────┘                  │  Prometheus    │
                       │                             │  :9090         │
                       │  read every 15 min          │                │
                       ▼                             │  DCGM metrics  │
              ┌─────────────────┐                   │  time series   │
              │   CLASSIFIER    │◄──────────────────│                │
              │                 │  query_range API   └───────┬────────┘
              │  log_parser.py  │  (6-hr lookback)           │
              │  sacct_parser   │                            │ query
              │  classifier.py  │                            │
              │  correlation    │                            │
              │  _engine.py     │                            │
              │  node_health    │                            │
              │  _rollup.py     │                            │
              └────────┬────────┘                           │
                       │                                     │
                       │ upsert                              │
                       ▼                                     │
              ┌─────────────────┐                           │
              │   PostgreSQL    │◄──────────────────────────┘
              │   :5432         │  (correlation_results,
              │                 │   node_health_weekly)
              │  job_events     │
              │  correlation    │
              │  _results       │
              │  node_health    │
              │  _weekly        │
              │  classifier_runs│
              └────────┬────────┘
                       │
                       │ SQL datasource
                       ▼
              ┌─────────────────────────────────────────┐
              │               Grafana  :3000             │
              │                                          │
              │  fleet_health         (Prometheus)       │
              │  job_failure_analysis (PostgreSQL)       │
              │  prefailure_signals   (PostgreSQL+Prom)  │
              │  node_reliability     (PostgreSQL)       │
              │  cost_impact          (PostgreSQL)       │
              └────────────────┬────────────────────────┘
                               │
                               │ alert rules
                               ▼
                      ┌─────────────────┐
                      │  AlertManager   │
                      │  :9093          │
                      │                 │
                      │  P1 → PagerDuty │
                      │  P2 → Slack     │
                      │  P3/P4 → Slack  │
                      └─────────────────┘


                   ┌──────────────────────────────┐
                   │  simulator-ui  :8000          │
                   │  (oncall training — standalone)│
                   │                               │
                   │  FastAPI backend              │
                   │  React frontend               │
                   │  5 scored scenarios           │
                   │  passing score ≥ 80%          │
                   └──────────────────────────────┘
```

---

## Component Responsibilities

| Component | Technology | Role |
|-----------|-----------|------|
| `slurm-simulator` | Python | Generates `slurmctld.log` + `slurmd.log` with 8 failure patterns on a 4-hour loop |
| `sacct-simulator` | Python | Generates `sacct_data.json` with matching job records (state, exit code, node list, elapsed) |
| `dcgm-simulator` | Python | Pushes 9 DCGM GPU health metrics to Pushgateway every 30 seconds with pre-failure anomaly patterns |
| `pushgateway` | Prometheus | Receives metrics from dcgm-simulator; Prometheus scrapes it |
| `prometheus` | Prometheus | Stores DCGM metric time series; evaluated by classifier and Grafana |
| `classifier` | Python | Every 15 min: parses logs + sacct, classifies each job into 1 of 8 categories, correlates against Prometheus pre-failure signals, upserts to PostgreSQL |
| `postgres` | PostgreSQL 16 | Stores job events, correlation results, node health rollup, classifier run history |
| `grafana` | Grafana 10 | 5 provisioned dashboards querying PostgreSQL and Prometheus |
| `alertmanager` | Prometheus | Routes P1–P4 alerts to PagerDuty and Slack channels |
| `simulator-ui` | FastAPI + React | Standalone oncall training app — presents failure scenarios and scores engineer responses |

---

## Key Design Constraints

- **Classifier is the only writer to `job_events`.** No simulator writes directly to PostgreSQL.
- **Prometheus is read-only from the classifier's perspective.** The classifier queries `query_range`; it never writes metrics (health metrics go to Pushgateway instead).
- **No service has a direct dependency on `simulator-ui`.** It runs independently and shares no database or volume with the main stack.
- **All services start with `docker compose up -d`.** No external dependencies, no real GPU or Slurm installation required.

---

## Port Map

| Service | Port | Protocol |
|---------|------|----------|
| Grafana | 3000 | HTTP |
| Prometheus | 9090 | HTTP |
| Pushgateway | 9091 | HTTP |
| AlertManager | 9093 | HTTP |
| PostgreSQL | 5432 | TCP |
| simulator-ui | 8000 | HTTP |
