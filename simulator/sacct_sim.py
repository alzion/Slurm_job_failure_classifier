#!/usr/bin/env python3
"""
sacct simulator.
Generates sacct JSON records for all 13 scenarios, appending to
/logs/sacct_data.json every REPLAY_INTERVAL seconds.
Timestamps are anchored to the shared 4-hour cycle (matches slurm_log_sim and dcgm_sim).
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone

LOG_DIR = os.environ.get("LOG_DIR", "/logs")
REPLAY_INTERVAL = int(os.environ.get("REPLAY_INTERVAL", 1800))
CYCLE = 14400  # 4-hour cycle, shared with dcgm_sim

# Failure time offsets in seconds from cycle start — must match slurm_log_sim.py.
S01_T = 10800
S02_T = 11700
S03_T = 11400
S04_T = 12600
S05_T = 12000
S06_T = 12300
S07_T = 11100
S08_T = 13200
S09_T = 13500
S10_T = 13560
S11_T = 13620
S12_T = 13680
S13_T = 13740

# (sid, base_job_id, job_name, user, account, state, exit_code, derived_exit_code,
#  reason, node_list, alloc_gres, req_mem, max_rss, fail_offset_s, duration_s, submit_ahead_s)
SCENARIOS = [
    (
        "S01", 847293,
        "llama3-70b-finetune", "researcher-01", "nlp-team",
        "NODE_FAIL", "1:0", "1:0", "NodeDown",
        "gpu[03-10]", "gpu:8", "320G", "298G",
        S01_T, 8640, 3600,
    ),
    (
        "S02", 847301,
        "gpt4-distributed-train", "researcher-02", "llm-team",
        "FAILED", "1:0", "1:0", "NonZeroExitCode",
        "gpu[01-08]", "gpu:8", "320G", "285G",
        S02_T, 3600, 1800,
    ),
    (
        "S03", 847310,
        "bert-large-pretrain", "researcher-03", "cv-team",
        "OUT_OF_MEMORY", "1:0", "1:0", "OutOfMemory",
        "gpu[05-06]", "gpu:2", "80G", "79G",
        S03_T, 1800, 900,
    ),
    (
        "S04", 847318,
        "stable-diffusion-xl-train", "researcher-04", "cv-team",
        "FAILED", "1:0", "1:0", "NonZeroExitCode",
        "gpu[07-08]", "gpu:2", "160G", "140G",
        S04_T, 2700, 1800,
    ),
    (
        "S05", 847325,
        "data-preprocessing-run42", "researcher-05", "data-team",
        "FAILED", "1:0", "1:0", "NonZeroExitCode",
        "gpu[02-04]", "gpu:3", "120G", "95G",
        S05_T, 1800, 600,
    ),
    (
        "S06", 847332,
        "whisper-v3-finetune", "researcher-06", "audio-team",
        "PREEMPTED", "0:9", "0:9", "HighPriorityJob",
        "gpu[09-10]", "gpu:2", "80G", "62G",
        S06_T, 1200, 1200,
    ),
    (
        "S07", 847340,
        "custom-env-test", "researcher-07", "nlp-team",
        "FAILED", "1:0", "1:0", "NonZeroExitCode",
        "gpu[01]", "gpu:1", "40G", "12G",
        S07_T, 600, 300,
    ),
    (
        "S08", 847348,
        "llama2-70b-eval", "researcher-08", "llm-team",
        "TIMEOUT", "0:15", "0:15", "TimeLimit",
        "gpu[03-06]", "gpu:4", "160G", "158G",
        S08_T, 7200, 3600,
    ),
    (
        "S09", 847350,
        "roberta-base-finetune", "researcher-09", "nlp-team",
        "COMPLETED", "0:0", "0:0", "None",
        "gpu[01-02]", "gpu:2", "80G", "71G",
        S09_T, 3600, 1800,
    ),
    (
        "S10", 847351,
        "vit-large-imagenet", "researcher-10", "cv-team",
        "COMPLETED", "0:0", "0:0", "None",
        "gpu[03-04]", "gpu:2", "80G", "68G",
        S10_T, 2700, 900,
    ),
    (
        "S11", 847352,
        "t5-xl-summarization", "researcher-01", "nlp-team",
        "COMPLETED", "0:0", "0:0", "None",
        "gpu[05-06]", "gpu:2", "80G", "74G",
        S11_T, 1800, 600,
    ),
    (
        "S12", 847353,
        "codellama-34b-instruct", "researcher-03", "llm-team",
        "COMPLETED", "0:0", "0:0", "None",
        "gpu[07-08]", "gpu:2", "160G", "142G",
        S12_T, 3600, 1200,
    ),
    (
        "S13", 847354,
        "clip-vit-finetune", "researcher-05", "cv-team",
        "COMPLETED", "0:0", "0:0", "None",
        "gpu[09-10]", "gpu:2", "80G", "67G",
        S13_T, 1800, 900,
    ),
]


def fmt_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def fmt_elapsed(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def build_records(cycle_num: int, now_ts: float) -> list[dict]:
    cycle_start_ts = now_ts - (now_ts % CYCLE)
    cycle_start_dt = datetime.fromtimestamp(cycle_start_ts, tz=timezone.utc)
    records = []

    for row in SCENARIOS:
        (
            sid, base_job_id,
            job_name, user, account,
            state, exit_code, derived_exit_code, reason,
            node_list, alloc_gres, req_mem, max_rss,
            fail_offset_s, duration_s, submit_ahead_s,
        ) = row

        job_id  = base_job_id + cycle_num * 100
        end_dt  = cycle_start_dt + timedelta(seconds=fail_offset_s)

        # Skip scenarios whose failure time hasn't been reached yet this cycle.
        if end_dt.timestamp() > now_ts:
            continue

        start_dt  = end_dt - timedelta(seconds=duration_s)
        submit_dt = start_dt - timedelta(seconds=submit_ahead_s)

        records.append({
            "JobID":           str(job_id),
            "JobName":         job_name,
            "User":            user,
            "Account":         account,
            "State":           state,
            "ExitCode":        exit_code,
            "DerivedExitCode": derived_exit_code,
            "Reason":          reason,
            "NodeList":        node_list,
            "Submit":          fmt_ts(submit_dt),
            "Start":           fmt_ts(start_dt),
            "End":             fmt_ts(end_dt),
            "Elapsed":         fmt_elapsed(duration_s),
            "AllocGRES":       alloc_gres,
            "ReqMem":          req_mem,
            "MaxRSS":          max_rss,
        })

    return records


def run_cycle(cycle_num: int) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    now_ts  = time.time()
    records = build_records(cycle_num, now_ts)

    if not records:
        print(
            f"[{datetime.now(timezone.utc).isoformat()}] "
            f"Cycle {cycle_num}: no scenarios ready yet, skipping write."
        )
        return

    sacct_path = os.path.join(LOG_DIR, "sacct_data.json")

    # Load existing records, append new ones, write back.
    existing: list[dict] = []
    if os.path.exists(sacct_path):
        try:
            with open(sacct_path) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            existing = []

    # Avoid exact duplicates (same JobID already present).
    existing_ids = {r["JobID"] for r in existing}
    new_records  = [r for r in records if r["JobID"] not in existing_ids]
    combined     = existing + new_records

    with open(sacct_path, "w") as f:
        json.dump(combined, f, indent=2)

    print(
        f"[{datetime.now(timezone.utc).isoformat()}] "
        f"Cycle {cycle_num}: appended {len(new_records)} records "
        f"(total {len(combined)}) to {sacct_path}"
    )


def main() -> None:
    cycle_num = 0
    while True:
        run_cycle(cycle_num)
        cycle_num += 1
        time.sleep(REPLAY_INTERVAL)


if __name__ == "__main__":
    main()
