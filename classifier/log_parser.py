#!/usr/bin/env python3
"""
Slurm log parser.
Reads slurmctld.log and slurmd.log; returns a list of LogEvidence records,
one per matched pattern. The classifier resolves these to specific jobs.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

LOG_TS_RE = re.compile(r'^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.\d+\]\s*(.*)')

# Each rule: (compiled_regex, category_hint, extract_fn)
# extract_fn(match, line) → dict with optional keys: job_id, node, detail
_RULES: list[tuple[re.Pattern, str, callable]] = [
    # ---- GPU_HARDWARE ----
    (
        re.compile(r'_job_requeue: requeueing job (\d+) due to node failure (\S+)'),
        'GPU_HARDWARE',
        lambda m, _: {'job_id': m.group(1), 'node': m.group(2)},
    ),
    (
        re.compile(r'_node_down: node (\S+) is DOWN'),
        'GPU_HARDWARE',
        lambda m, _: {'node': m.group(1)},
    ),
    (
        re.compile(r'NVRM: Xid.*?: (\d+)'),
        'GPU_HARDWARE',
        lambda m, _: {'detail': f'XID={m.group(1)}'},
    ),
    (
        re.compile(r'ECC Double Bit Error'),
        'GPU_HARDWARE',
        lambda m, _: {'detail': 'ECC_DBE'},
    ),
    # ---- NCCL_COMM_FAILURE ----
    (
        re.compile(r'ncclSystemError'),
        'NCCL_COMM_FAILURE',
        lambda m, _: {},
    ),
    (
        re.compile(r'Socket: Connection timed out.*socket\.cc'),
        'NCCL_COMM_FAILURE',
        lambda m, _: {},
    ),
    # ---- CUDA_OOM ----
    (
        re.compile(r'CUDA out of memory'),
        'CUDA_OOM',
        lambda m, _: {},
    ),
    (
        re.compile(r'CUDA error: out of memory|cudaErrorMemoryAllocation'),
        'CUDA_OOM',
        lambda m, _: {},
    ),
    # ---- INFRA_STORAGE ----
    (
        re.compile(r"Stale file handle: '.*job_(\d+)'"),
        'INFRA_STORAGE',
        lambda m, _: {'job_id': m.group(1)},
    ),
    (
        re.compile(r'Stale file handle|lustre|NFS'),
        'INFRA_STORAGE',
        lambda m, _: {},
    ),
    # ---- USER_ERROR ----
    (
        re.compile(r'Task launch for StepId=(\d+)\.\d+ failed.*execve failed'),
        'USER_ERROR',
        lambda m, _: {'job_id': m.group(1)},
    ),
    (
        re.compile(r'execve\(\):.*No such file or directory'),
        'USER_ERROR',
        lambda m, _: {},
    ),
]


@dataclass
class LogEvidence:
    timestamp: datetime
    category_hint: str
    source_file: str
    raw_line: str
    job_id: Optional[str] = None
    node: Optional[str] = None
    detail: Optional[str] = None

    def patterns_matched(self) -> list[str]:
        parts = [self.category_hint]
        if self.detail:
            parts.append(self.detail)
        return parts


def _parse_file(path: Path) -> list[LogEvidence]:
    evidence: list[LogEvidence] = []
    if not path.exists():
        return evidence

    with open(path, errors='replace') as f:
        for raw in f:
            line = raw.rstrip()
            ts_match = LOG_TS_RE.match(line)
            if not ts_match:
                continue
            ts_str, body = ts_match.group(1), ts_match.group(2)
            try:
                ts = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
            except ValueError:
                continue

            for pattern, category, extract in _RULES:
                m = pattern.search(body)
                if not m:
                    continue
                extras = extract(m, line)
                evidence.append(LogEvidence(
                    timestamp    = ts,
                    category_hint= category,
                    source_file  = path.name,
                    raw_line     = line,
                    job_id       = extras.get('job_id'),
                    node         = extras.get('node'),
                    detail       = extras.get('detail'),
                ))
                break   # first matching rule wins per line

    return evidence


def parse_logs(log_dir: str) -> list[LogEvidence]:
    """
    Parse slurmctld.log and slurmd.log in log_dir.
    Returns list of LogEvidence, one per matched line.
    """
    base = Path(log_dir)
    evidence = []
    evidence.extend(_parse_file(base / 'slurmctld.log'))
    evidence.extend(_parse_file(base / 'slurmd.log'))
    evidence.sort(key=lambda e: e.timestamp)
    return evidence


def summarise(evidence: list[LogEvidence]) -> dict[str, list[LogEvidence]]:
    """Group evidence by category_hint for quick inspection."""
    out: dict[str, list[LogEvidence]] = {}
    for e in evidence:
        out.setdefault(e.category_hint, []).append(e)
    return out
