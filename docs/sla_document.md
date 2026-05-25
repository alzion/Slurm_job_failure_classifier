# GPU Fleet SLA

## Severity Tiers

| Tier | Failures | Acknowledge | Diagnosis | Cordon | Vendor Ticket | Replacement |
|------|----------|-------------|-----------|--------|---------------|-------------|
| **P1** | GPU_HARDWARE | 15 min | 30 min | 1 hr | 2 hr | 72 hr |
| **P2** | NCCL_COMM_FAILURE, THERMAL_THROTTLE, INFRA_STORAGE | 30 min | 2 hr | 2 hr | 8 hr | — |
| **P3** | CUDA_OOM | 4 hr (business hours) | Self-service | — | — | — |
| **P4** | PREEMPTION, TIMEOUT, USER_ERROR | Automated notification | Self-service | — | — | — |

## Alert Routing

| Tier | PagerDuty | Slack |
|------|-----------|-------|
| P1 | ✓ (immediate) | #gpu-critical |
| P2 | ✓ (15 min delay) | #gpu-alerts |
| P3 | — | #gpu-alerts |
| P4 | — | #gpu-notifications |

## Inhibit Rules
- Active P1 suppresses P2 alerts on the same node.
- Active P2 suppresses P3 alerts on the same node.

## Node Return-to-Service
A drained node requires sign-off from the on-call engineer before re-enabling:
```bash
scontrol update NodeName=<node> State=resume
```
P1 nodes additionally require a passed GPU burn-in test (`gpu-burn 60`) before resume.

---

## Classifier Service SLOs

The classifier is itself a service with operational SLOs. A classifier that runs late, errors silently, or produces stale data undermines all downstream SLAs. These SLOs are monitored via Prometheus and surfaced in Grafana.

| Metric | SLO Target | Measurement Query | Alert Threshold |
|--------|-----------|-------------------|-----------------|
| **Classification latency** | Job classified within 20 min of `end_time` | `job_events.created_at - job_events.end_time` p95 | P3 alert if p95 > 1200 s |
| **Classifier availability** | ≥ 99.5% of 15-min windows result in a successful run | `1 - (classifier_errors_total / classifier_runs_total)` over 7d | P2 alert if error rate > 0.5% in 24h window |
| **Classification accuracy** | ≥ 90% correct category on P1/P2 failures | Weekly review: override count / total P1+P2 classifications from `classifier_runs` | Manual review trigger if override rate > 10% in any 2-week window |
| **Dashboard data freshness** | Grafana panels reflect data ≤ 20 min old | `time() - classifier_last_run_timestamp` | P2 alert if `classifier_last_run_age_seconds > 1800` |

### Classifier Health Metrics (Prometheus)

The following metrics are pushed to the pushgateway after each `run_once()` call:

| Metric | Type | Description |
|--------|------|-------------|
| `classifier_runs_total` | Counter | Incremented on each successful run |
| `classifier_errors_total` | Counter | Incremented on each run that raises an exception |
| `classifier_jobs_classified_total` | Counter | Incremented per job written to `job_events` |
| `classifier_last_run_timestamp` | Gauge | Unix epoch of last successful run |

### Classifier Run History (Database)

Each run is persisted to `classifier_runs` in PostgreSQL (see `db/schema.sql`). This provides a queryable audit trail independent of Prometheus retention and enables a Grafana panel showing run history and any gaps.
