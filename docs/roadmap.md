# Program Roadmap — GPU Fleet Failure Classifier

| Field | Value |
|-------|-------|
| **Program Owner** | Infrastructure Platform Team |
| **Status** | Phase 1 — Active |
| **Last Updated** | 2026-05-01 |

---

## Overview

Three phases over 24 weeks. Each phase has a defined exit criteria gate; Phase N+1 does not start until Phase N exits cleanly.

```
Phase 1 (Wks 1–8)    Foundation      Deploy, validate, build trust
Phase 2 (Wks 9–16)   Automation      Act on signals, not just report them
Phase 3 (Wks 17–24)  Prediction      Shift from reactive to proactive
```

---

## Phase 1 — Foundation (Weeks 1–8): Deploy, Validate, Build Trust

**Goal:** Get the classifier running on a real cluster, validate its accuracy against real failure data, train the on-call rotation, and go live with dashboards and notifications — with no automated actions yet. Establish the baseline metrics that Phase 2 and 3 improvements will be measured against.

### Milestones

| ID | Milestone | Owner | Target Week | Status |
|----|-----------|-------|-------------|--------|
| M1.1 | Deploy to staging cluster in shadow mode (classify but do not alert) | Infra | Week 2 | — |
| M1.2 | Validate classification accuracy ≥ 90% against manually labeled failure set from real cluster logs | Infra | Week 4 | — |
| M1.3 | 100% of on-call rotation completes simulator-ui training with passing score (≥ 80%) before Phase 1 go-live | On-call Lead | Week 6 | — |
| M1.4 | Go-live: Grafana dashboards live, Slack notifications active, PagerDuty routing validated for P1/P2 | Infra | Week 8 | — |

### Exit Criteria (Phase 1 → Phase 2 gate)

- [ ] Zero false-positive P1 classifications in a 72-hour production window
- [ ] `classifier_last_run_age_seconds` alert firing correctly in Prometheus
- [ ] All on-call engineers have completed simulator-ui training (score ≥ 80%)
- [ ] Baseline metrics captured: MTTR p75, GPU-hours lost/week by category, signal coverage %
- [ ] Launch criteria checklist in `docs/launch_criteria.md` fully signed off

---

## Phase 2 — Automation (Weeks 9–16): Act on Signals, Not Just Report Them

**Goal:** Move from information surfacing to automated response on the highest-confidence signals. Each automated action requires explicit stakeholder approval before activation.

### Milestones

| ID | Milestone | Owner | Target Week | Status |
|----|-----------|-------|-------------|--------|
| M2.1 | Automated node drain rule: `DCGM_FI_DEV_ECC_SBE_VOL_TOTAL` rate > 30/hr sustained for 5 min → drain node | Infra + ML Platform (joint approval) | Week 10 | — |
| M2.2 | Upgrade ECC_SBE sustained alert from P3 → P2 in AlertManager | On-call Lead | Week 10 | — |
| M2.3 | Mandatory checkpoint policy: jobs > 4 GPU-hours must checkpoint every 30 min | ML Platform | Week 12 | — |
| M2.4 | Human-override UI in classifier: on-call can correct misclassifications; corrections feed accuracy tracking | Infra | Week 16 | — |

### Exit Criteria (Phase 2 → Phase 3 gate)

- [ ] MTTR for GPU_HARDWARE ≤ 15 min p75 over a 30-day window
- [ ] Zero auto-cordon events that interrupted a job that would have completed successfully (false-positive cordon rate = 0 over 30 days)
- [ ] Override rate (misclassification rate) ≤ 10% over 30-day window

### Dependencies and Approval Gates

- **M2.1** requires written joint approval from Infra Lead and ML Platform Lead before activation. See open question OQ2 in `docs/PRD.md`.
- **M2.3** requires ML Platform policy update and researcher communication plan published at least 2 weeks before enforcement.

---

## Phase 3 — Prediction (Weeks 17–24): Shift from Reactive to Proactive

**Goal:** Use the pre-failure signal data accumulated over Phases 1–2 to alert before failure and provide fleet reliability intelligence for capacity planning.

### Milestones

| ID | Milestone | Owner | Target Week | Status |
|----|-----------|-------|-------------|--------|
| M3.1 | Pre-failure alerting: page on-call when a pre-failure signal is detected with ≥ 60 min lead time while the job is still running | Infra | Week 19 | — |
| M3.2 | Fleet reliability report: weekly automated report of GPU-hours lost by failure category, rolling 12-week view, delivered to Slack and shared with FinOps | Infra | Week 21 | — |
| M3.3 | Hardware refresh recommendation workflow: nodes with ≥ 3 GPU_HARDWARE events in 90 days automatically flagged in Grafana and trigger a Hardware Reliability team review ticket | Infra + Hardware Reliability | Week 24 | — |

### Exit Criteria (Program complete)

- [ ] ≥ 10% reduction in GPU-hours lost to preventable failures vs. Phase 1 baseline over a 30-day window
- [ ] Pre-failure alert lead time ≥ 60 min for ≥ 50% of detected GPU_HARDWARE events
- [ ] Hardware refresh pipeline has processed at least one node flagged by M3.3

---

## Risks

See `docs/risk_register.md` for full risk log. Key program-level risks:

| Risk | Phase | Mitigation |
|------|-------|-----------|
| Classification accuracy below threshold in production | 1 | Shadow mode + manual validation in M1.2 before go-live |
| Auto-cordon causes more disruption than it prevents | 2 | Phase 2 gated behind joint approval; 5-min sustained signal requirement before trigger |
| Prometheus retention gaps break correlation | 1 | Validated as M1.1 pre-requisite (see OQ3 in PRD) |
| Stakeholder misalignment on cordon authority | 2 | OQ2 resolved in writing by Week 10; escalate if not resolved by Week 8 |
