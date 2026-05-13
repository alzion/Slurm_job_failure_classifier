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
