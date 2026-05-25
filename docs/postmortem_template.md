# Postmortem: GPU_HARDWARE — Job 847293

| Field | Value |
|-------|-------|
| **Incident ID** | INC-2026-0315-001 |
| **Severity** | P1 |
| **Date** | 2026-03-15 |
| **Duration** | 2h 24m (14:23 → 16:47) |
| **Job** | 847293 — llama3-70b-finetune |
| **Nodes affected** | gpu03 (primary), gpu03-10 (job allocation) |
| **GPUs lost** | 8 × A100-SXM4-80GB |

## Impact
Training run terminated at ~35% completion. Approximately 19.3 GPU-hours lost (~$38.60 at spot rate). Research team lost one day of iteration cycle.

## Timeline

| Time | Event |
|------|-------|
| T−90m (13:53) | ECC_SBE rate on gpu03/GPU0 crosses 30/hr threshold (first signal) |
| T−5m (16:42) | ECC_DBE count jumps to 1 on gpu03/GPU0 |
| T−5m (16:42) | XID error 48 (ECC DBE) fires on gpu03 |
| T+0 (16:47) | Slurm marks gpu03 DOWN; job 847293 NODE_FAIL |
| T+2m (16:49) | PagerDuty alert fires; on-call paged |
| T+12m (16:59) | On-call acknowledges; node drained |
| T+28m (17:15) | Vendor ticket opened with NVIDIA |
| T+74h | GPU0 on gpu03 replaced; node returned to service |

## Root Cause
GPU0 on gpu03 developed progressive ECC single-bit errors beginning ~90 minutes before failure, consistent with a degrading SRAM cell in HBM2e memory. The error rate escalated until a double-bit error (uncorrectable) occurred, triggering an XID 48 and forcing Slurm to mark the node down.

## Contributing Factors
- No automated cordon policy on ECC_SBE rate > 30/hr — signal was visible but not acted on
- Job was not checkpointing; full 2h24m of compute was lost rather than resuming from a checkpoint
- Vendor escalation took 8 minutes longer than SLA due to unclear ticket routing

## 5 Whys
1. Why did the job fail? → gpu03 was marked DOWN by Slurm due to an uncorrectable ECC error
2. Why did ECC reach DBE? → Progressive SBE accumulation for 90 minutes went unaddressed
3. Why wasn't the node drained at SBE threshold? → No automated drain rule existed for ECC_SBE rate
4. Why didn't the operator drain it manually? → ECC_SBE alert was P3 (no page); operator wasn't aware
5. Why was the alert P3? → SBE alone was not considered actionable; policy predated the pre-failure signal analysis

## Action Items

| # | Action | Owner | Due |
|---|--------|-------|-----|
| 1 | Add Prometheus alert rule: drain node automatically when ECC_SBE rate > 30/hr for 5 min | Infra | 2026-03-22 |
| 2 | Require checkpoint save every 30 min for jobs > 4 GPU-hours | MLPlatform | 2026-03-29 |
| 3 | Upgrade ECC_SBE sustained alert from P3 → P2 in AlertManager config | On-call lead | 2026-03-19 |

## What the Pre-Failure Analyzer Showed (Retrospective)
The signal was detectable at **T−90m** (13:53) when `DCGM_FI_DEV_ECC_SBE_VOL_TOTAL` on gpu03/GPU0 first crossed 30/hr. Had an automated cordon been triggered at that point, the job could have been checkpointed and migrated with **0 GPU-hours lost** instead of 19.3. Lead time available: **5,400 seconds (1h 30m)**.
