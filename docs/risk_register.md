# Risk Register — GPU Fleet Failure Classifier

| Field | Value |
|-------|-------|
| **Program Owner** | Infrastructure Platform Team |
| **Last Updated** | 2026-05-01 |
| **Review Cadence** | At each phase gate; ad-hoc when new risks identified |

**Likelihood:** H = likely within the phase, M = possible, L = unlikely but plausible
**Impact:** H = blocks phase exit or causes production incident, M = degrades a goal metric, L = minor friction

---

## Active Risks

| # | Risk Description | Likelihood | Impact | Mitigation | Owner | Status |
|---|-----------------|------------|--------|-----------|-------|--------|
| R01 | Classification accuracy below threshold in production | M | H | Shadow mode in Phase 1 for 2 weeks before go-live. Accuracy validated against manually labeled real-cluster failures (M1.2), not just simulator data. Human-override UI (Phase 2) feeds ongoing accuracy tracking. | Infra Lead | **Open** |
| R02 | On-call team stops trusting classifier after early misclassifications | M | H | Accuracy dashboard reviewed weekly with on-call lead during Phase 1. Explicit accuracy SLA: if override rate exceeds 20% in any 2-week window, go-live is delayed and root cause investigated before re-attempting. | On-call Lead | **Open** |
| R03 | Auto-cordon (Phase 2) causes more disruption than it prevents | M | H | Auto-cordon gated behind joint written approval (ML Platform + Infra). Signal threshold requires 5-minute sustained rate — not a spike. False-positive cordon rate tracked as a Phase 2 exit criterion (target: 0 in 30 days). | Infra Lead + ML Platform | **Open** |
| R04 | Prometheus retention gaps break pre-failure correlation | M | H | OQ3 in PRD: validate and enforce 8-hour retention as a Phase 1 pre-requisite before M1.1 deployment. Retention check added to launch criteria checklist. | Infra Platform | **Open** |
| R05 | Log format changes break classification rules | M | M | Pattern library (`classifier/log_parser.py`) is versioned separately from the classifier runtime. Regression test suite (`tests/eval_classifier.py`, 13 scenarios) run on every dependency upgrade. New failure modes added to ground truth as they are observed. | Infra | **Open** |
| R06 | Stakeholder misalignment on cordon authority blocks Phase 2 | M | H | OQ2 (PRD §8) is a Phase 2 pre-requisite: cordon authority ownership must be resolved in writing by Week 10. Escalation path to Engineering Director if unresolved by Week 8 (2 weeks early). | Engineering Director | **Open** |
| R07 | Database credentials shipped as defaults (fleet/fleet123) reach production | L | H | Default credentials are intentional for local Docker Compose development only. `docs/launch_criteria.md` has an explicit pre-launch checklist item: rotate credentials and restrict `job_events` table access to service account + read-only reporting role. | Infra | **Open** |
| R08 | Job failure data exposes PII or proprietary model metadata | L | M | Security review of `job_events` schema and log data scope is a launch criteria gate item. Job names (e.g., `llama3-70b-finetune`) and user fields may be sensitive. Mitigation: security team reviews data classification before Phase 1 go-live. | Security + Infra | **Open** |
| R09 | Checkpoint policy (Phase 2 M2.3) breaks jobs that don't support mid-run checkpointing | M | M | ML Platform owns M2.3 and defines technical requirements + exemption process. Researcher notice required ≥ 2 weeks before enforcement. Exemption list maintained in ML Platform runbook. | ML Platform | **Open** |
| R10 | Classifier silently stops running; dashboards show stale data without alert | L | M | `classifier_last_run_age_seconds` Prometheus metric with a P2 alert if classifier hasn't run in 30 minutes. `classifier_runs` table in PostgreSQL provides a queryable audit trail independent of Prometheus. | Infra | **Open** |

---

## Closed Risks

*None yet — register opened at program start.*

---

## Risk Review Notes

**Phase 1 Gate Review (target: Week 8)**
- R01, R02: Review accuracy data from 2-week shadow run. If override rate > 20%, do not proceed to go-live.
- R04: Prometheus retention must be confirmed before M1.1 deploy.
- R07, R08: Both must be closed (mitigated) before Phase 1 go-live.

**Phase 2 Gate Review (target: Week 16)**
- R03: Review false-positive cordon rate over 30-day window. Zero tolerance.
- R06: Must be closed (written agreement in place) before Phase 2 starts.
- R09: Exemption list published and researcher communication sent before M2.3 enforcement.
