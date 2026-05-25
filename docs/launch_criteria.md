# Launch Criteria — GPU Fleet Failure Classifier (Phase 1 Production Readiness)

| Field | Value |
|-------|-------|
| **Program Owner** | Infrastructure Platform Team |
| **Last Updated** | 2026-05-01 |
| **Target Go-live** | Phase 1, Week 8 |
| **Sign-off Required** | Infra Lead, On-call Lead |

All items must be checked before Phase 1 go-live. Any unchecked item is a launch blocker unless explicitly waived with written justification.

---

## Functional Readiness

- [ ] Classification accuracy ≥ 90% on manually labeled real-cluster failure set (not simulator-only data)
- [ ] Zero false-positive P1 classifications in a 72-hour shadow run on production traffic
- [ ] All 8 failure categories covered in ground truth evaluation set (`tests/ground_truth.json`)
- [ ] `UNKNOWN` category handled correctly for unclassified FAILED jobs (not defaulting to `USER_ERROR`)
- [ ] Human-override mechanism available for on-call engineers to correct misclassifications (Phase 2 feature, but logged if absent)
- [ ] Classifier correctly handles duplicate job IDs (ON CONFLICT upsert validated)

---

## Operational Readiness

- [ ] Escalation runbook (`docs/escalation_runbook.md`) reviewed and signed off by on-call lead
- [ ] All on-call engineers have completed simulator-ui training with passing score ≥ 80%
- [ ] Classifier health metric (`classifier_last_run_age_seconds`) alerting in Prometheus, tested with a deliberate classifier pause
- [ ] P2 alert fires if classifier has not run in 30 minutes (`time() - classifier_last_run_timestamp > 1800`)
- [ ] Grafana dashboards reviewed with ops team; alert thresholds agreed and documented
- [ ] AlertManager routing validated end-to-end: P1 fires PagerDuty (immediate), P2 fires Slack #gpu-alerts (15 min delay), P3/P4 Slack only

---

## Dependency Readiness

- [ ] Prometheus retention ≥ 8 hours confirmed on target cluster (covers pre-failure signal window for all scenarios)
- [ ] PostgreSQL backup policy confirmed; automated backup tested with restore drill
- [ ] Log volume estimate at target cluster size reviewed; parser performance validated at 10× expected log rate
- [ ] pushgateway reachability confirmed from classifier container (metrics path end-to-end tested)

---

## Security and Access

- [ ] Database credentials rotated from defaults (`fleet` / `fleet123`) to secrets-managed values
- [ ] Access to `job_events` table restricted to classifier service account + read-only reporting role; no direct developer access in production
- [ ] Job failure data (job names, user fields, node lists) reviewed with security team for PII / model IP exposure; data classification confirmed
- [ ] Grafana access restricted to authenticated users; anonymous access disabled in production

---

## Rollback Plan

- [ ] Classifier can be disabled (container stopped) without impacting Grafana dashboards — panels degrade gracefully to "no data" rather than showing stale values or errors
- [ ] Database schema migration is backwards-compatible; downgrade to previous classifier version does not corrupt existing `job_events` rows
- [ ] Rollback procedure documented in runbook and tested in staging: `docker compose stop classifier` + `docker compose start classifier` with no data loss

---

## Sign-off

| Role | Name | Date | Notes |
|------|------|------|-------|
| Infra Lead | — | — | |
| On-call Lead | — | — | |
| ML Platform Lead | — | — | Informed; not a blocker |
| Security | — | — | Required if data classification review found sensitive fields |
