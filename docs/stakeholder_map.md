# Stakeholder Map — GPU Fleet Failure Classifier

| Field | Value |
|-------|-------|
| **Program Owner** | Infrastructure Platform Team |
| **Last Updated** | 2026-05-01 |

---

## 1. Stakeholder Table

| Team | Representative Role | Primary Concern | Influence |
|------|---------------------|-----------------|-----------|
| Cluster Operations / Infra | On-call engineers, Infra Lead | Classification accuracy; false P1 pages erode trust in the tool | **High** |
| ML Platform | ML Platform Lead | Automated node cordoning disrupts running jobs; checkpoint policy changes require researcher communication | **High** |
| Hardware Reliability | HW Reliability Lead | Need cordon authority and structured failure data for vendor RMA tickets | **High** |
| ML Research / Users | Research tech leads | Clear root-cause communication when jobs fail; no surprises when jobs are preempted or nodes drained | **Medium** |
| Finance / FinOps | FinOps analyst | Accurate GPU-hours-lost data by failure category for cost attribution and chargeback | **Medium** |
| Security | Security engineer | Job failure data may contain model names, user identifiers, or proprietary training run metadata | **Low** |

---

## 2. Concerns and Mitigations

### Cluster Operations / Infra (High influence)

**Concern:** If the classifier produces false-positive P1 classifications, on-call engineers will stop trusting it. A tool that pages for nothing is worse than no tool.

**Mitigation:**
- Phase 1 begins with 2 weeks of shadow mode (classify but do not page). On-call lead reviews classifications daily.
- Accuracy threshold of ≥ 90% on real cluster data (M1.2) is required before go-live — not just on simulator data.
- `classifier_last_run_age_seconds` alert gives on-call visibility into classifier health; stale data is flagged, not silently aged.
- Human-override UI (Phase 2, M2.4) lets on-call correct misclassifications; each override feeds the accuracy metric.

---

### ML Platform (High influence)

**Concern:** Automated node cordoning (Phase 2) will drain a node mid-training run, destroying hours of GPU-compute for a job that might have completed successfully. ML Platform owns the job scheduler and will block Phase 2 if this risk isn't addressed.

**Mitigation:**
- Phase 1 is information-only. No automated actions until Phase 2.
- Phase 2 auto-cordon (M2.1) requires **joint written approval** from Infra Lead and ML Platform Lead before activation (OQ2, PRD §8).
- Auto-cordon signal threshold is `ECC_SBE_VOL_TOTAL > 30/hr sustained for 5 minutes` — not a single spike. This threshold is calibrated to have near-zero false triggers.
- ML Platform is listed as **Approver** on the Phase 2 go-live milestone (see RACI, §3).

**Concern:** Mandatory checkpoint policy (M2.3) changes researcher workflow and may break jobs that don't support mid-run checkpointing.

**Mitigation:**
- ML Platform owns M2.3 milestone and defines the technical requirements and exemption process.
- Communication plan (researcher notice ≥ 2 weeks before enforcement) is a milestone dependency.

---

### Hardware Reliability (High influence)

**Concern:** Need structured, queryable failure data to file vendor RMA tickets. Currently assembling this data manually from DCGM exports and on-call notes.

**Mitigation:**
- `job_events` and `correlation_results` PostgreSQL tables provide structured failure data from day one.
- `node_reliability` Grafana dashboard surfaces per-node failure rates, hardware failure counts, and ECC accumulation.
- Phase 3 M3.3 automates the threshold-based flag: nodes with ≥ 3 GPU_HARDWARE events in 90 days trigger a Hardware Reliability review ticket.
- Hardware Reliability team is **Consulted** on the ECC_SBE cordon threshold (Phase 2 M2.1) to ensure it aligns with their vendor escalation criteria.

---

## 3. RACI Matrix

**Decisions mapped to Responsible / Accountable / Consulted / Informed:**

| Decision | R | A | C | I |
|----------|---|---|---|---|
| Phase 1 go-live sign-off | Infra Lead | Infra Lead | ML Platform Lead, On-call Lead | FinOps, Research Leads |
| Cordon policy scope (Phase 2 M2.1) | Infra Lead | Engineering Director | ML Platform Lead, HW Reliability Lead | Research Leads, Security |
| Alert severity change (ECC_SBE P3→P2) | On-call Lead | Infra Lead | HW Reliability | ML Platform, Research |
| Hardware refresh threshold definition (Phase 3 M3.3) | HW Reliability Lead | HW Reliability Lead | Infra Lead, FinOps | Research, ML Platform |
| Classification accuracy SLO targets | Infra Lead | Infra Lead | On-call Lead, ML Platform Lead | All |
| Checkpoint policy requirements (Phase 2 M2.3) | ML Platform Lead | ML Platform Lead | Research Tech Leads | Infra, FinOps |
| Security review of failure data scope | Security Engineer | Security Engineer | Infra Lead | All |

---

## 4. Communication Plan

| Stakeholder Group | Channel | Cadence | Content |
|-------------------|---------|---------|---------|
| Cluster Ops / On-call team | Slack #infra-oncall + weekly sync | Weekly during Phase 1 | Classification accuracy, false-positive count, open issues |
| ML Platform | Bi-weekly sync | Every 2 weeks | Phase progress, upcoming policy changes, cordon policy status |
| Hardware Reliability | Async (shared Grafana + Slack #hw-reliability) | Weekly automated report (Phase 3) | Node failure rates, ECC accumulation, flagged nodes |
| ML Research / Users | Slack #gpu-notifications + email | Per-incident (automated) + monthly summary | Job failure category, safe-to-resubmit status, monthly cluster reliability digest |
| FinOps | Monthly report | Monthly | GPU-hours lost by category, cost impact, preventable cost trend |
| Engineering Director | Quarterly business review | Quarterly | Phase completion, MTTR trends, cost recovery, risks |

---

## 5. Escalation Path

If a stakeholder concern cannot be resolved at the team level:

1. **Week-level blocking issue** (e.g., ML Platform blocking Phase 2 go-live): escalate to Infra Lead + ML Platform Lead within 48 hours.
2. **Cross-team policy dispute** (e.g., cordon authority disagreement unresolved by Week 12): escalate to Engineering Director for resolution within one week.
3. **Security concern blocking launch**: Security Lead and Infra Lead present joint recommendation to Engineering Director; resolution within 5 business days.
