# Portfolio Improvements — SrTPM Readiness

Tracked against a Director-level TPM review of this repo as a SrTPM infrastructure candidate portfolio. Items are ordered by the signal they send to a hiring committee, not by implementation effort.

---

## Priority 1 — Critical: Missing TPM Artifacts

These are the artifacts that distinguish a SrTPM portfolio from a strong SWE portfolio. Their absence is the first thing a hiring manager notices.

---

### 1. Write `docs/PRD.md` — Product Requirements Document

**The single most important gap.** A PRD is the artifact a SrTPM owns that an L5 SWE does not write. Its absence signals that the candidate may think like a strong engineer but not yet like a program owner.

Sections to include:

- **Problem Statement** — What is the current state? Oncall engineers today triage GPU job failures manually, with no root-cause information surfaced automatically. What does that cost in MTTR and wasted GPU-hours?
- **Opportunity** — What becomes possible with automated classification? Quantify: if MTTR drops from 45 min to 10 min for P1 hardware failures, and a 64-GPU job costs ~$100/hr, each avoided 35-minute triage saves $58. At 3 P1 incidents/week that's ~$9K/year on a single cluster.
- **Users and Personas** — Two primary users: (1) on-call infra engineer who needs fast root-cause during an incident; (2) reliability/capacity planning team that needs weekly failure trends to justify hardware refresh cycles and staffing. One secondary user: ML researchers who want to understand why their jobs failed.
- **Goals** — Three measurable goals with 90-day and 6-month targets. Example: (1) Classification accuracy ≥ 90% on P1/P2 failures within 60 days of deployment. (2) On-call MTTR for GPU_HARDWARE incidents reduced by ≥ 30% within 90 days. (3) GPU-hours lost to preventable failures (signal-detectable) reduced by ≥ 20% in 6 months.
- **Non-Goals** — Explicitly state what this does not do. Not a real-time alerting system (that's AlertManager's job). Not a job scheduler or auto-remediation system. Not a capacity planning tool (though it feeds one).
- **Success Metrics** — How will you know if this is working 6 months post-launch? Define the measurement methodology, not just the metric names.
- **Phased Scope** — What ships in Phase 1 vs. later. See `docs/roadmap.md`.
- **Open Questions** — At least 3 unresolved questions that require stakeholder input. Example: Should automated node cordon on ECC_SBE threshold be in Phase 1 or Phase 2? Who owns the cordon policy — infra team or the cluster operations team?
- **Stakeholder Sign-off** — A table listing stakeholders, their role in the decision (Approver / Consulted / Informed), and a placeholder for sign-off date.

---

### 2. Write `docs/roadmap.md` — Phased Program Roadmap

A SrTPM is expected to think 2–3 quarters ahead. A point-in-time system with no roadmap signals a project, not a program.

Structure:

**Phase 1 — Foundation (Weeks 1–8): Deploy, validate, build trust**
- Milestone 1.1: Deploy to staging cluster, run for 2 weeks in shadow mode (classify but don't alert)
- Milestone 1.2: Validate accuracy ≥ 90% against manually labeled failure set from real cluster logs
- Milestone 1.3: Train oncall rotation via simulator-ui; 100% of oncall engineers complete training before Phase 2 go-live
- Milestone 1.4: Go-live with Grafana dashboards and Slack notifications; no automated actions yet
- Exit criteria: Zero false-positive P1 pages in 72-hour window; classifier health metric green

**Phase 2 — Automation (Weeks 9–16): Act on signals, not just report them**
- Milestone 2.1: Automated node drain rule when `DCGM_FI_DEV_ECC_SBE_VOL_TOTAL` rate > 30/hr sustained for 5 min (action item from S01 postmortem)
- Milestone 2.2: Upgrade ECC_SBE sustained alert from P3 → P2 in AlertManager
- Milestone 2.3: Mandatory checkpoint policy enforced for jobs > 4 GPU-hours
- Milestone 2.4: Human-override UI in classifier allowing oncall to correct misclassifications; corrections feed accuracy tracking
- Exit criteria: MTTR for GPU_HARDWARE ≤ 15 min p75 over 30-day window

**Phase 3 — Prediction (Weeks 17–24): Shift from reactive to proactive**
- Milestone 3.1: Pre-failure alerting — page oncall when pre-failure signal detected with ≥ 60 min lead time, job still running
- Milestone 3.2: Fleet reliability report for capacity planning: weekly trend of GPU-hours lost by failure category, rolling 12-week view
- Milestone 3.3: Hardware refresh recommendation workflow: nodes with ≥ 3 GPU_HARDWARE events in 90 days automatically flagged for vendor review
- Exit criteria: ≥ 10% reduction in GPU-hours lost to preventable failures vs. Phase 1 baseline

---

### 3. Write `docs/stakeholder_map.md` — Stakeholders and RACI

Cross-functional alignment is where most program work actually lives. A portfolio project that only shows solo engineering work doesn't demonstrate this. Describe the hypothetical stakeholder landscape as if this were a real program at a mid-to-large tech company.

Sections to include:

**Stakeholder Table** — For each stakeholder group: team name, representative role, interest/concern, influence level (High/Med/Low).

| Team | Role | Primary Concern | Influence |
|---|---|---|---|
| Cluster Operations / Infra | On-call engineers | Accuracy of classification; don't want false P1 pages | High |
| ML Platform | Team that owns training jobs and job scheduler | Automated node cordoning disrupts running jobs; need advance notice | High |
| Hardware Reliability | Team that handles node disposition and vendor RMAs | Want cordon authority; need structured failure data for vendor tickets | High |
| ML Research / Users | Researchers running training jobs | Want clear root-cause communication when their jobs fail | Medium |
| Finance / FinOps | Cost attribution for GPU compute | Need accurate GPU-hours-lost by failure category for chargeback | Medium |
| Security | Data governance | Job failure data may contain model names, user information | Low |

**Concerns and Mitigations** — For each team with High influence, document the specific concern and how the program addresses it. Example: ML Platform's concern about auto-cordon is addressed by Phase 2 requiring an explicit policy approval gate, with ML Platform as an Approver on that milestone.

**RACI Matrix** — Map key program decisions to Responsible / Accountable / Consulted / Informed. Key decisions: go-live sign-off, cordon policy scope, alert severity changes, hardware refresh threshold definition.

**Communication Plan** — How and how often each stakeholder group is updated. Weekly status to ops team; monthly dashboard review with ML Platform and FinOps; quarterly business review with leadership.

---

### 4. Write `docs/risk_register.md` — Program Risk Register

Risk registers are a basic TPM deliverable. This one is notably absent.

Format each risk with: Risk Description | Likelihood (H/M/L) | Impact (H/M/L) | Mitigation | Owner | Status.

Risks to include at minimum:

- **Classification accuracy below threshold in production** — Simulator scenarios are clean; real logs have noise, partial entries, and failure modes not in the training set. Mitigation: shadow mode in Phase 1, human override in Phase 2.
- **Oncall team ignores classifier output** — If early classifications are wrong, oncall engineers stop trusting the tool. Mitigation: accuracy dashboard surfaced to oncall lead weekly; explicit accuracy SLA.
- **Auto-cordon causes more disruption than it prevents** — Premature node drain during a valid training run is worse than a delayed response. Mitigation: auto-cordon gated behind Phase 2 approval, requires 5-minute sustained signal before trigger.
- **Prometheus retention gaps** — Pre-failure signals rely on DCGM data in Prometheus. If retention is shorter than job duration, correlation is lost. Mitigation: validate Prometheus retention setting before Phase 1 go-live.
- **Log format changes break classification** — CUDA, NCCL, and driver upgrades change log string formats. Mitigation: regression test suite run on each dependency upgrade; pattern library versioned separately from classifier.
- **Stakeholder misalignment on cordon authority** — Infra team and ML Platform may have conflicting views on who can drain a node during a running job. Mitigation: resolve in writing before Phase 2 milestone 2.1; escalate to engineering director if unresolved at Week 12.

---

### 5. Write `docs/launch_criteria.md` — Production Readiness Checklist

At Google this is a PRR (Production Readiness Review). Every service that touches production needs one. Its absence signals the candidate hasn't shipped production infrastructure before — or hasn't done so formally.

Sections:

**Functional Readiness**
- [ ] Classification accuracy ≥ 90% on labeled real-cluster failure set (not just simulator)
- [ ] Zero false-positive P1 classifications in 72-hour shadow run
- [ ] All 8 failure categories covered in ground truth evaluation
- [ ] Human-override mechanism available for oncall corrections

**Operational Readiness**
- [ ] Escalation runbook reviewed and signed off by oncall lead
- [ ] All oncall engineers have completed simulator-ui training (passing score ≥ 80%)
- [ ] Classifier health metric (`classifier_last_run_age_seconds`) alerting in Prometheus
- [ ] Grafana dashboards reviewed with ops team; thresholds agreed
- [ ] AlertManager routing validated: P1 fires PagerDuty, P2 fires Slack, P3 Slack only

**Dependency Readiness**
- [ ] Prometheus retention ≥ 8 hours (covers pre-failure signal window for all scenarios)
- [ ] PostgreSQL backup policy confirmed
- [ ] Log volume estimate at target cluster size reviewed; parser performance validated

**Security and Access**
- [ ] Database credentials rotated from defaults (fleet/fleet123)
- [ ] Access to `job_events` table restricted to classifier service account and read-only reporting role
- [ ] Job failure data reviewed with security team for any PII / model IP exposure

**Rollback Plan**
- [ ] Classifier can be disabled without impacting Grafana dashboards (dashboards degrade gracefully to "no data")
- [ ] Database schema migration is backwards-compatible
- [ ] Rollback procedure documented and tested

---

## Priority 2 — Important: Strengthen Existing Artifacts

These exist but send a weaker signal than they should.

---

### 6. Rewrite the opening of `README.md`

Current: leads with tech stack and "what it does" (a system description).
Needed: leads with the problem and the business case.

Replace the first paragraph with something like:

> GPU cluster oncall engineers typically learn what caused a job failure by reading logs manually — a process that takes 20–40 minutes and requires deep familiarity with Slurm, CUDA, and NCCL internals. On a 64-GPU cluster running LLM training jobs at ~$100/hour, that triage window represents significant wasted compute and a full iteration cycle for the ML team.
>
> This system classifies Slurm job failures automatically by root cause, correlates failures against pre-failure GPU health signals with up to 90 minutes of lead time, and surfaces results to oncall engineers through Grafana dashboards and AlertManager. The goal is to reduce P1 incident MTTR from ~40 minutes to under 10.

Then add a dedicated **Operator Training** section explaining the simulator-ui as a structured oncall readiness program — not just "a React frontend." Describe what it tests, what a passing score means, and how it fits into the Phase 1 go-live gate.

---

### 7. Restructure `design.md` as a proper design document

Current: an implementation spec. Captures *what to build* thoroughly, but not *why these decisions were made*.

Add the following sections at the top, before the existing technical content:

- **Background** — Why is this problem worth solving? Reference the business case from the PRD in one paragraph.
- **Goals** — Three bullet points. Reference the PRD goals.
- **Non-Goals** — Two bullet points. Explicitly scope out real-time remediation and capacity planning.

Add the following sections at the bottom, after the existing technical content:

- **Alternatives Considered** — At minimum two: (1) Parse logs with an LLM rather than regex — rejected because latency, cost, and non-determinism are unacceptable for oncall tooling; regex rules are auditable and fast. (2) Use only sacct state for classification without log parsing — rejected because sacct state is too coarse; FAILED covers NCCL failures, storage failures, and user errors identically.
- **Open Questions** — Three unresolved design questions with a proposed resolution and a note on who needs to decide. Examples: (a) Should the classifier own the alerting path or should it write to a queue that AlertManager consumes? (b) At what log volume does the current full-file re-parse approach need to be replaced with offset tracking or log streaming? (c) Who defines the per-GPU-SKU thermal threshold table?
- **Future Work** — Two or three items that are explicitly deferred to Phase 2/3. Tie back to the roadmap.

---

### 8. Add a classifier SLO to `docs/sla_document.md`

The SLA document defines SLOs for the GPU fleet. It does not define SLOs for the classifier itself. A service with no SLO is unmonitored by definition.

Add a section:

**Classifier Service SLOs**

| Metric | Target | Measurement |
|---|---|---|
| Classification latency | Job classified within 20 min of end_time | `job_events.created_at - job_events.end_time` p95 |
| Classifier availability | ≥ 99.5% of 15-min windows result in a successful run | `classifier_runs_total` vs `classifier_errors_total` in Prometheus |
| Classification accuracy | ≥ 90% correct category on P1/P2 failures | Weekly review of human overrides vs. automated classifications |
| Dashboard data freshness | Grafana panels reflect data ≤ 20 min old | `classifier_last_run_age_seconds` alert threshold |

---

## Priority 3 — Code Changes: Close the Gaps Flagged in Review

These are engineering fixes that have operational correctness implications. A SrTPM who ships a system with known correctness problems undermines their own program.

---

### 9. Change `USER_ERROR` catch-all to `UNKNOWN` in `classifier/classifier.py`

**File:** `classifier/classifier.py`, function `classify()`

Current behavior: any `FAILED` job with no matching log patterns is classified as `USER_ERROR / LOW`. This is operationally wrong — on a real cluster, the majority of unclassified failures are infrastructure issues, not user mistakes. It will cause oncall to blame researchers for infrastructure problems.

Change both fallback return paths to `('UNKNOWN', 'LOW', patterns)`.

Update the priority list constant `PRIORITY` to include `'UNKNOWN'` at the end, and update the SLA document to map `UNKNOWN` to P3 (automated notification + ops investigation, not user self-service).

---

### 10. Add classifier health metrics to Prometheus

**File:** `classifier/classifier.py`, function `run_once()`

The classifier has no observability of its own health. If it silently stops running, the Grafana dashboards show stale data with no alert.

After each `run_once()` call, push the following metrics to the pushgateway (same pattern as `dcgm_sim.py`):

- `classifier_runs_total` — counter, incremented each successful run
- `classifier_errors_total` — counter, incremented on exception
- `classifier_jobs_classified_total` — counter, incremented per job written
- `classifier_last_run_timestamp` — gauge, Unix epoch of last successful run

Add a Prometheus alert rule: fire a P2 alert if `time() - classifier_last_run_timestamp > 1800` (classifier hasn't run in 30 minutes).

---

### 11. Add a `classifier_health` table to `db/schema.sql`

Persist classifier run history to the database so the ops team can query it without Prometheus:

```sql
CREATE TABLE classifier_runs (
    id              SERIAL PRIMARY KEY,
    run_at          TIMESTAMPTZ DEFAULT NOW(),
    jobs_written    INTEGER,
    jobs_skipped    INTEGER,
    errors          INTEGER,
    duration_ms     INTEGER
);
```

This enables a Grafana panel showing classifier run history and a simple query to detect gaps.

---

## Priority 4 — Polish

Minor items. Each takes under an hour but meaningfully affects first impression.

---

### 12. Add a project-level `docs/` index or update the README `docs/` description

The README says "Three documents in `docs/`" — after adding PRD, roadmap, stakeholder map, risk register, and launch criteria, that description will be wrong. Update it to describe the full docs directory with one line per document and what audience it's for (e.g., "PRD — business case and goals for stakeholders and hiring reviewers").

### 13. Update incident date in `docs/postmortem_template.md`

The postmortem is dated 2024-03-15. The project was built in 2025–2026. A sharp interviewer will notice the date doesn't match the project history and may ask why. Update to a date consistent with the project's git history.

### 14. Add a `ARCHITECTURE.md` or architecture diagram

A one-page data flow diagram (even ASCII) showing: simulators → logs/sacct → classifier → PostgreSQL → Grafana, plus the Prometheus path (DCGM sim → pushgateway → Prometheus → correlation engine + Grafana). This makes the system legible in 30 seconds, which matters when a hiring committee is reviewing 6 portfolios in a row.

---

## Summary Checklist

| # | Item | Type | Priority |
|---|---|---|---|
| 1 | `docs/PRD.md` | New file | P0 |
| 2 | `docs/roadmap.md` | New file | P0 |
| 3 | `docs/stakeholder_map.md` | New file | P0 |
| 4 | `docs/risk_register.md` | New file | P1 |
| 5 | `docs/launch_criteria.md` | New file | P1 |
| 6 | Rewrite `README.md` opening + add Simulator UI section | Edit existing | P1 |
| 7 | Restructure `design.md` with Background / Alternatives / Open Questions | Edit existing | P1 |
| 8 | Add classifier SLOs to `docs/sla_document.md` | Edit existing | P1 |
| 9 | Change `USER_ERROR` fallback → `UNKNOWN` in `classifier.py` | Code change | P1 |
| 10 | Add classifier health metrics to Prometheus | Code change | P2 |
| 11 | Add `classifier_runs` table to `db/schema.sql` | Code change | P2 |
| 12 | Update `docs/` description in README | Edit existing | P3 |
| 13 | Fix incident date in postmortem | Edit existing | P3 |
| 14 | Add `ARCHITECTURE.md` or diagram | New file | P3 |

**Estimated effort:** P0 items (1–3) are the heaviest — each is a 2–4 hour writing exercise. P1 code changes (9) take under 30 minutes. Everything else is 1–2 hours. Total: ~16–20 hours of focused work.
