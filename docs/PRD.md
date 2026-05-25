# Product Requirements Document — GPU Fleet Failure Classifier

| Field | Value |
|-------|-------|
| **Author** | Infrastructure Platform Team |
| **Status** | Approved — Phase 1 |
| **Last Updated** | 2026-05-01 |
| **Stakeholder Sign-off** | See §9 |

---

## 1. Problem Statement

GPU cluster on-call engineers today triage job failures manually. When a training job fails, the engineer reads `slurmctld.log`, `slurmd.log`, and sacct records by hand, cross-references DCGM metrics in Grafana, and draws their own conclusion about root cause. This process takes **20–40 minutes per incident** and requires deep familiarity with Slurm internals, CUDA error codes, and NCCL failure modes that not all rotation members have.

On a 64-GPU cluster running LLM training jobs at ~$100/hour (A100 spot), a 35-minute triage window represents **~$58 of wasted GPU-compute per incident**. At 3 P1 incidents per week, that is ~$9K/year on a single cluster — before accounting for the research team's lost iteration cycle.

A secondary cost is invisible: pre-failure GPU health signals (rising ECC single-bit error rates, NVLink CRC increments, thermal excursions) are already present in DCGM data **60–90 minutes before job failure**. Without automated detection, these signals go unread until after the failure occurs.

---

## 2. Opportunity

Automated failure classification and pre-failure signal correlation can:

1. **Reduce on-call MTTR** by surfacing root cause immediately when a job fails, without requiring the engineer to read logs.
2. **Enable proactive intervention** by alerting when a pre-failure signal crosses threshold, while the job is still running.
3. **Provide structured failure data** for capacity planning: weekly GPU-hours lost by category, node reliability scores, and hardware refresh recommendations.

Quantified value at steady state (single cluster, 3 P1/week):
- MTTR reduction 40 min → 10 min: **~$9K/year** in recovered GPU compute
- One preventable hardware failure per month (job checkpointed before node failure): **~$2.4K/year** in avoided recompute
- Hardware refresh flagging (3 incidents/90 days → vendor review): reduces unplanned P1 frequency over time

---

## 3. Users and Personas

### Primary Users

**On-call Infrastructure Engineer**
- *Context*: Paged at 2 AM because a training job failed. Has 15 minutes to acknowledge per SLA.
- *Need*: Immediately know the failure category and the single most important diagnostic action. Does not want to read logs from scratch.
- *Success*: Opens Grafana, sees `GPU_HARDWARE / HIGH confidence / gpu03 ECC DBE at T−5m`, drains the node, and closes the alert — without touching raw logs.

**Reliability / Capacity Planning Engineer**
- *Context*: Weekly review of cluster health. Owns the hardware refresh budget request.
- *Need*: Trend data — GPU-hours lost by category, failure rate per node, nodes approaching refresh threshold.
- *Success*: Single Grafana dashboard answers "which nodes are degrading?" and "how much compute did we lose to preventable failures this week?"

### Secondary Users

**ML Researcher**
- *Context*: Their training job failed. They want to know if it was their code or the infrastructure.
- *Need*: Clear failure category communicated via Slack notification. Does not need to interpret logs.
- *Success*: Receives a Slack message with failure category, affected node, and whether a resubmit is safe.

---

## 4. Goals

| # | Goal | 90-day Target | 6-month Target |
|---|------|---------------|----------------|
| G1 | Classification accuracy on P1/P2 failures | ≥ 90% correct category | ≥ 95% correct category |
| G2 | On-call MTTR for GPU_HARDWARE incidents | Reduced by ≥ 30% vs. pre-launch baseline | Reduced by ≥ 50% |
| G3 | GPU-hours lost to preventable failures (signal-detectable) | Baseline established | Reduced by ≥ 20% vs. baseline |

Measurement methodology:
- **G1**: Weekly review of human overrides vs. automated classifications logged in `classifier_runs`. Override rate = misclassification rate.
- **G2**: `job_events.created_at − job_events.end_time` p75 as proxy for time-to-classification; oncall acknowledges from Grafana rather than logs.
- **G3**: `cost_impact` Grafana panel, "preventable cost" stat, rolling 12-week view.

---

## 5. Non-Goals

- **Not a real-time alerting system.** AlertManager owns alert delivery. This system classifies completed or failing jobs; it does not replace AlertManager's Prometheus rule evaluation.
- **Not a job scheduler or auto-remediation system.** Phase 1 surfaces information only. Automated node cordoning is Phase 2, gated on a separate stakeholder approval.
- **Not a capacity planning tool.** This system provides failure data that feeds capacity planning decisions; it does not make or recommend capacity changes directly.
- **Not a general-purpose log aggregation system.** Ingests only Slurm (`slurmctld.log`, `slurmd.log`) and sacct records. Does not ingest application logs, kernel logs, or network switch logs.

---

## 6. Success Metrics

| Metric | Measurement | Target (6 months post-launch) |
|--------|-------------|-------------------------------|
| Classification accuracy | Override rate from `classifier_runs` | < 10% (≥ 90% correct) |
| Classifier availability | `classifier_errors_total / classifier_runs_total` | < 0.5% error rate |
| Dashboard data freshness | `classifier_last_run_age_seconds` p95 | < 1200 s (20 min) |
| On-call MTTR, GPU_HARDWARE | Incident ticket open → node drained, p75 | ≤ 15 min |
| Pre-failure signal coverage | % of GPU_HARDWARE failures with detectable precursor ≥ 30 min before failure | ≥ 60% |
| GPU-hours lost, preventable | `cost_impact` panel, preventable cost, rolling 4-week | Decreasing trend; ≥ 20% reduction at 6 months |

---

## 7. Phased Scope

See `docs/roadmap.md` for full milestone detail.

| Phase | Scope |
|-------|-------|
| **Phase 1 (Weeks 1–8)** | Deploy classifier + Grafana dashboards. Shadow mode for 2 weeks. Oncall training via simulator-ui. Go-live with notifications only — no automated actions. |
| **Phase 2 (Weeks 9–16)** | Automated node drain on sustained ECC_SBE signal. Checkpoint policy enforcement. Human-override UI. |
| **Phase 3 (Weeks 17–24)** | Pre-failure alerting with lead-time guarantee. Fleet reliability report. Hardware refresh recommendation workflow. |

---

## 8. Open Questions

| # | Question | Proposed Resolution | Decision Owner | Due |
|---|----------|---------------------|----------------|-----|
| OQ1 | Should automated node cordon (Phase 2) be in Phase 1 scope if accuracy ≥ 95% in shadow mode? | No — keep Phase 1 as information-only regardless of accuracy. Trust requires a track record. | Infra Lead + ML Platform | Week 6 |
| OQ2 | Who owns the cordon policy — infra team or cluster operations? | Infra team proposes; cluster ops approves. Joint sign-off required before Phase 2 M2.1. | Engineering Director | Week 10 |
| OQ3 | What is the minimum Prometheus retention required? Pre-failure correlation needs 6-hour lookback. Current retention may be shorter on some clusters. | Validate and enforce 8-hour retention before Phase 1 go-live. | Infra Platform | Week 2 |

---

## 9. Stakeholder Sign-off

| Stakeholder | Team | Role | Sign-off Date |
|-------------|------|------|---------------|
| [Infra Lead] | Cluster Operations | Approver | — |
| [ML Platform Lead] | ML Platform | Approver | — |
| [Hardware Reliability Lead] | Hardware Reliability | Consulted | — |
| [FinOps Lead] | Finance / FinOps | Informed | — |
| [Security Lead] | Security | Consulted | — |
