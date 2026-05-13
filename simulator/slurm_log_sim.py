#!/usr/bin/env python3
"""
Slurm log simulator.
Generates slurmctld.log and slurmd.log for all 13 scenarios from the spec.
Replays every 30 minutes (REPLAY_INTERVAL) with incrementing job IDs.
Timestamps are anchored to the shared 4-hour cycle so they align with dcgm_sim.
"""

import os
import time
from datetime import datetime, timedelta, timezone

LOG_DIR = os.environ.get("LOG_DIR", "/logs")
REPLAY_INTERVAL = int(os.environ.get("REPLAY_INTERVAL", 1800))  # 30 min
CYCLE = 14400  # 4-hour cycle, shared with dcgm_sim

# Base job IDs from spec; incremented by 100 each replay cycle.
BASE_JOBS = {
    "S01": 847293,
    "S02": 847301,
    "S03": 847310,
    "S04": 847318,
    "S05": 847325,
    "S06": 847332,
    "S07": 847340,
    "S08": 847348,
    "S09": 847350,
    "S10": 847351,
    "S11": 847352,
    "S12": 847353,
    "S13": 847354,
}

# Failure time offsets (seconds from cycle start) — match dcgm_sim.py constants.
S01_T = 10800   # 3 h
S02_T = 11700   # 3 h 15 m
S03_T = 11400   # 3 h 10 m
S04_T = 12600   # 3 h 30 m
S05_T = 12000   # 3 h 20 m
S06_T = 12300   # 3 h 25 m
S07_T = 11100   # 3 h 05 m
S08_T = 13200   # 3 h 40 m
S09_T = 13500
S10_T = 13560
S11_T = 13620
S12_T = 13680
S13_T = 13740

SCENARIO_CONFIG = [
    # (sid, category, primary_node, failure_offset, duration_s, node_list_str)
    ("S01", "GPU_HARDWARE",      "gpu03", S01_T,  8640, "gpu[03-10]"),  # 2h24m
    ("S02", "NCCL_COMM_FAILURE", "gpu01", S02_T,  3600, "gpu[01-08]"),  # 1h
    ("S03", "CUDA_OOM",          "gpu05", S03_T,  1800, "gpu[05-06]"),  # 30m
    ("S04", "THERMAL_THROTTLE",  "gpu07", S04_T,  2700, "gpu[07-08]"),  # 45m
    ("S05", "INFRA_STORAGE",     "gpu02", S05_T,  1800, "gpu[02-04]"),  # 30m
    ("S06", "PREEMPTION",        "gpu09", S06_T,  1200, "gpu[09-10]"),  # 20m
    ("S07", "USER_ERROR",        "gpu01", S07_T,   600, "gpu[01]"),      # 10m
    ("S08", "TIMEOUT",           "gpu03", S08_T,  7200, "gpu[03-06]"),  # 2h
    ("S09", None,                "gpu01", S09_T,  3600, "gpu[01-02]"),
    ("S10", None,                "gpu03", S10_T,  2700, "gpu[03-04]"),
    ("S11", None,                "gpu05", S11_T,  1800, "gpu[05-06]"),
    ("S12", None,                "gpu07", S12_T,  3600, "gpu[07-08]"),
    ("S13", None,                "gpu09", S13_T,  1800, "gpu[09-10]"),
]


def slurm_ts(dt: datetime) -> str:
    return dt.strftime("[%Y-%m-%dT%H:%M:%S.000]")


def write_gpu_hardware(fc, fd, job_id: int, node: str, t_fail: datetime) -> None:
    t_pre = t_fail - timedelta(minutes=5)
    fd.write(
        f"{slurm_ts(t_pre)} error: NVRM: Xid (PCI:0000:03:00): 48, "
        f"pid='<unknown>', name=<unknown>\n"
    )
    fd.write(
        f"{slurm_ts(t_pre)} error: ECC Double Bit Error detected on GPU 0\n"
    )
    fc.write(
        f"{slurm_ts(t_fail)} slurmctld: _node_down: node {node} is DOWN: "
        f"Not responding\n"
    )
    fc.write(
        f"{slurm_ts(t_fail)} slurmctld: _job_requeue: requeueing job {job_id} "
        f"due to node failure {node}\n"
    )


def write_nccl(fd, job_id: int, node: str, t_fail: datetime) -> None:
    fd.write(
        f"{slurm_ts(t_fail)} error: [ncclSystemError] Socket: Connection timed out "
        f"<net/socket.cc:490>\n"
    )
    fd.write(
        f"{slurm_ts(t_fail)} error: NCCL version 2.18.3 - unhandled system error "
        f"(ncclSystemError)\n"
    )


def write_cuda_oom(fd, job_id: int, node: str, t_fail: datetime) -> None:
    fd.write(
        f"{slurm_ts(t_fail)} RuntimeError: CUDA out of memory. "
        f"Tried to allocate 18.50 GiB\n"
    )
    fd.write(
        f"{slurm_ts(t_fail)} error: CUDA error: out of memory (error 2)\n"
    )


def write_infra_storage(fd, job_id: int, node: str, t_fail: datetime) -> None:
    fd.write(
        f"{slurm_ts(t_fail)} error: /scratch/lustre: Stale file handle\n"
    )
    fd.write(
        f"{slurm_ts(t_fail)} OSError: [Errno 116] Stale file handle: "
        f"'/lustre/scratch/job_{job_id}'\n"
    )


def write_user_error(fd, job_id: int, node: str, t_fail: datetime) -> None:
    fd.write(
        f"{slurm_ts(t_fail)} srun: error: Task launch for StepId={job_id}.0 "
        f"failed on node {node}: execve failed\n"
    )
    fd.write(
        f"{slurm_ts(t_fail)} error: execve(): /usr/bin/python3.11: "
        f"No such file or directory\n"
    )


WRITERS = {
    "GPU_HARDWARE":      write_gpu_hardware,
    "NCCL_COMM_FAILURE": write_nccl,
    "CUDA_OOM":          write_cuda_oom,
    "INFRA_STORAGE":     write_infra_storage,
    "USER_ERROR":        write_user_error,
}


def run_cycle(cycle_num: int) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)

    # Anchor timestamps to the current 4-hour cycle boundary so dcgm_sim aligns.
    now_ts = time.time()
    cycle_start_ts = now_ts - (now_ts % CYCLE)
    cycle_start_dt = datetime.fromtimestamp(cycle_start_ts, tz=timezone.utc)

    slurmctld_path = os.path.join(LOG_DIR, "slurmctld.log")
    slurmd_path    = os.path.join(LOG_DIR, "slurmd.log")

    with open(slurmctld_path, "a") as fc, open(slurmd_path, "a") as fd:
        for sid, category, node, fail_offset, duration_s, nodelist in SCENARIO_CONFIG:
            job_id    = BASE_JOBS[sid] + cycle_num * 100
            t_fail_dt = cycle_start_dt + timedelta(seconds=fail_offset)

            # Only write log entries for scenarios whose failure time has passed.
            if t_fail_dt.timestamp() > now_ts:
                continue

            writer = WRITERS.get(category)
            if writer is None:
                # THERMAL_THROTTLE, PREEMPTION, TIMEOUT, COMPLETED — no log entries.
                continue

            if category == "GPU_HARDWARE":
                writer(fc, fd, job_id, node, t_fail_dt)
            else:
                writer(fd, job_id, node, t_fail_dt)

    print(
        f"[{datetime.now(timezone.utc).isoformat()}] "
        f"Cycle {cycle_num}: wrote logs (cycle_start={cycle_start_dt.isoformat()})"
    )


def main() -> None:
    cycle_num = 0
    while True:
        run_cycle(cycle_num)
        cycle_num += 1
        time.sleep(REPLAY_INTERVAL)


if __name__ == "__main__":
    main()
