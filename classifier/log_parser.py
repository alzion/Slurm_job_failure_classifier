#!/usr/bin/env python3
"""
Slurm log parser.
Reads slurmctld.log, slurmd.log, and supplementary logs (dmesg.log, kern.log,
dcgm.log, syslog.log, messages.log); returns a list of LogEvidence records,
one per matched pattern. The classifier resolves these to specific jobs.

Timestamp formats supported (tried in order per line):
  1. Slurm bracket  [2026-05-16T12:34:56.000] body
  2. ISO plain      2026-05-16T12:34:56[.mmm] body          (journald, DCGM)
  3. Syslog         May 16 12:34:56 hostname process: body   (rsyslog, kern.log)
  4. RFC-3339       2026-05-16T12:34:56.000+00:00 body

Lines whose timestamp cannot be parsed are silently skipped.
Raw kernel-uptime dmesg lines (e.g. "[12345.678] ...") have no wall-clock
anchor and are therefore skipped; use journald or rsyslog forwarding to
attach real timestamps before writing to disk.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_CURRENT_YEAR = datetime.now(timezone.utc).year

# ---------------------------------------------------------------------------
# Timestamp parsers — (compiled_re, format_tag); tried in order.
# Group 1 = timestamp string, Group 2 = body (where pattern matching happens).
# ---------------------------------------------------------------------------
_TS_PARSERS: list[tuple[re.Pattern, str]] = [
    # Slurm: [2026-05-16T12:34:56.000] body
    (re.compile(r'^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.\d+\]\s*(.*)'), 'iso'),
    # ISO plain (journald / DCGM / nvidia-smi log): 2026-05-16T12:34:56[.mmm][±offset] body
    (re.compile(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2}|Z)?)\s+(.*)'), 'iso'),
    # Syslog (rsyslog, dmesg via syslog): May 16 12:34:56 hostname process[pid]: body
    (re.compile(r'^(\w{3}\s{1,2}\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+\S+\s+\S+[\[:].*?:\s*(.*)'), 'syslog'),
    # Syslog simplified (no process field): May 16 12:34:56 body
    (re.compile(r'^(\w{3}\s{1,2}\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+(.*)'), 'syslog'),
    # dmesg -T: [Fri May 16 12:34:56 2026] body
    (re.compile(r'^\[(\w{3}\s+\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4})\]\s*(.*)'), 'dmesg_human'),
]


def _parse_ts(ts_str: str, fmt: str) -> Optional[datetime]:
    try:
        if fmt == 'iso':
            # Strip trailing Z or ±offset before fromisoformat (Python < 3.11 compat)
            s = ts_str.rstrip('Z')
            if s.endswith('+00:00') or s.endswith('-00:00'):
                s = s[:-6]
            dt = datetime.fromisoformat(s)
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        if fmt == 'syslog':
            dt = datetime.strptime(f'{_CURRENT_YEAR} {ts_str.strip()}', '%Y %b %d %H:%M:%S')
            return dt.replace(tzinfo=timezone.utc)
        if fmt == 'dmesg_human':
            dt = datetime.strptime(ts_str.strip(), '%a %b %d %H:%M:%S %Y')
            return dt.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError):
        pass
    return None


def _split_line(line: str) -> tuple[Optional[datetime], str]:
    """Return (wall_clock_ts, body) for a log line, or (None, '') if unparseable."""
    for pattern, fmt in _TS_PARSERS:
        m = pattern.match(line)
        if not m:
            continue
        ts = _parse_ts(m.group(1), fmt)
        if ts is not None:
            return ts, m.group(2)
    return None, ''


# ---------------------------------------------------------------------------
# Pattern rules: (compiled_regex, category_hint, extract_fn)
# extract_fn(match, line) → dict with optional keys: job_id, node, detail
# First matching rule per line wins.
# ---------------------------------------------------------------------------
_RULES: list[tuple[re.Pattern, str, callable]] = [

    # ==== GPU_HARDWARE ====

    # Slurm requeue with explicit job + node (Tier-1 evidence)
    (
        re.compile(r'_job_requeue: requeueing job (\d+) due to node failure (\S+)'),
        'GPU_HARDWARE',
        lambda m, _: {'job_id': m.group(1), 'node': m.group(2)},
    ),
    # Slurm node-down event
    (
        re.compile(r'_node_down: node (\S+) is DOWN'),
        'GPU_HARDWARE',
        lambda m, _: {'node': m.group(1)},
    ),
    # NVRM XID errors (kernel driver)
    (
        re.compile(r'NVRM: Xid[^:]*?: (\d+)'),
        'GPU_HARDWARE',
        lambda m, _: {'detail': f'XID={m.group(1)}'},
    ),
    # Bare "Xid ...: NN" — seen when syslog strips the leading "NVRM:" prefix
    (
        re.compile(r'\bXid\b.*?: (\d+)'),
        'GPU_HARDWARE',
        lambda m, _: {'detail': f'XID={m.group(1)}'},
    ),
    # ECC double-bit error (uncorrectable, always hardware)
    (
        re.compile(r'ECC Double Bit Error|uncorrectable ECC error', re.IGNORECASE),
        'GPU_HARDWARE',
        lambda m, _: {'detail': 'ECC_DBE'},
    ),
    # GPU board / RmInitAdapter failures
    (
        re.compile(r'NVRM: GPU Board Error|RmInitAdapter failed', re.IGNORECASE),
        'GPU_HARDWARE',
        lambda m, _: {'detail': 'GPU_INIT_FAIL'},
    ),
    # GPU disappears from PCIe (hot-reset, PCIe link failure)
    (
        re.compile(r'GPU-\S+ not found|GPU.*disappeared from bus', re.IGNORECASE),
        'GPU_HARDWARE',
        lambda m, _: {'detail': 'GPU_MISSING'},
    ),
    # NVIDIA UVM (unified memory) fatal fault
    (
        re.compile(r'nvidia-uvm.*fatal|UVM.*fatal fault', re.IGNORECASE),
        'GPU_HARDWARE',
        lambda m, _: {'detail': 'UVM_FATAL'},
    ),
    # Kernel hardware error (MCE / GPU-related edac messages)
    (
        re.compile(r'Hardware Error|Machine check exception', re.IGNORECASE),
        'GPU_HARDWARE',
        lambda m, _: {'detail': 'HW_MCE'},
    ),

    # ==== NCCL_COMM_FAILURE ====

    # Original patterns
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
    # Other ncclError codes
    (
        re.compile(r'nccl(?:Internal|Remote|UnhandledCuda|Invalid(?:Usage|Argument))Error'),
        'NCCL_COMM_FAILURE',
        lambda m, _: {},
    ),
    # NCCL WARN-level timeout messages
    (
        re.compile(r'NCCL WARN.*?[Tt]imeout|[Tt]imeout waiting for.*?NCCL'),
        'NCCL_COMM_FAILURE',
        lambda m, _: {},
    ),
    # NCCL bootstrap / network init failure
    (
        re.compile(r'NCCL.*?Bootstrap.*?no socket interface|NCCL.*?net.*?socket.*?error', re.IGNORECASE),
        'NCCL_COMM_FAILURE',
        lambda m, _: {},
    ),
    # ==== NCCL_NETWORK_HARDWARE ====
    # Physical interconnect failures — NVLink CRC/flit errors and fabric manager
    # crashes require hardware inspection and node drain, not a config fix.

    # NVLink CRC / flit errors (DCGM daemon log, kernel messages, nvidia-smi output)
    (
        re.compile(r'nvlink.*?(?:crc|flit).*?error|NVLink.*?error', re.IGNORECASE),
        'NCCL_NETWORK_HARDWARE',
        lambda m, _: {'detail': 'NVLINK_CRC'},
    ),
    # NVSwitch errors
    (
        re.compile(r'NVSwitch.*?error|nvswitch.*?fail', re.IGNORECASE),
        'NCCL_NETWORK_HARDWARE',
        lambda m, _: {'detail': 'NVSWITCH'},
    ),
    # NVIDIA fabric manager crash (manages NVLink/NVSwitch topology)
    (
        re.compile(r'nvidia-fabricmanager.*?(?:died|crash|fail|error)', re.IGNORECASE),
        'NCCL_NETWORK_HARDWARE',
        lambda m, _: {'detail': 'FABRIC_MGR'},
    ),
    # NCCL generic WARN/error line (catch-all after specific patterns)
    (
        re.compile(r'NCCL WARN|ncclError\b'),
        'NCCL_COMM_FAILURE',
        lambda m, _: {},
    ),
    # MPI collective failure — usually caused by NCCL/network breakdown
    (
        re.compile(r'Fatal error in MPI_(Allreduce|AllGather|Broadcast|Barrier|Send|Recv)'),
        'NCCL_COMM_FAILURE',
        lambda m, _: {'detail': f'MPI_{m.group(1)}_FATAL'},
    ),
    # MPI rank death / abort
    (
        re.compile(r'\[mpirun\].*rank \d+.*died|MPI_ABORT.*called'),
        'NCCL_COMM_FAILURE',
        lambda m, _: {},
    ),
    # Rank lost contact (distributed training frameworks)
    (
        re.compile(r'[Rr]ank \d+.*?lost contact|[Rr]ank.*?timed? ?out'),
        'NCCL_COMM_FAILURE',
        lambda m, _: {},
    ),

    # ==== CUDA_OOM ====

    # PyTorch (original)
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
    # CUDA driver error code
    (
        re.compile(r'CUDA_ERROR_OUT_OF_MEMORY'),
        'CUDA_OOM',
        lambda m, _: {},
    ),
    # TensorFlow OOM
    (
        re.compile(r'OOM when allocating tensor'),
        'CUDA_OOM',
        lambda m, _: {},
    ),
    # JAX / XLA OOM
    (
        re.compile(r'ResourceExhaustedError.*OOM|(?:XLA|jax).*out of memory', re.IGNORECASE),
        'CUDA_OOM',
        lambda m, _: {},
    ),
    # NVRM / driver-level allocation failure
    (
        re.compile(r'NVRM.*insufficient memory|insufficient.*device memory', re.IGNORECASE),
        'CUDA_OOM',
        lambda m, _: {},
    ),
    # cudaMalloc failure
    (
        re.compile(r'cudaMalloc.*failed|Failed to allocate.*device memory'),
        'CUDA_OOM',
        lambda m, _: {},
    ),

    # ==== THERMAL_THROTTLE (log-based; Prometheus is the primary detector) ====

    (
        re.compile(r'(?:GPU|nvidia).*thermal.*throttl|thermal.*throttl.*(?:GPU|nvidia)', re.IGNORECASE),
        'THERMAL_THROTTLE',
        lambda m, _: {},
    ),
    (
        re.compile(r'Clocks Throttle Reasons.*Sw Thermal|HW Thermal Slowdown', re.IGNORECASE),
        'THERMAL_THROTTLE',
        lambda m, _: {},
    ),

    # ==== INFRA_STORAGE ====

    # Lustre stale handle with job_id (Tier-1 evidence)
    (
        re.compile(r"Stale file handle: '.*?job_(\d+)'"),
        'INFRA_STORAGE',
        lambda m, _: {'job_id': m.group(1)},
    ),
    # Generic Lustre / NFS stale handle
    (
        re.compile(r'Stale file handle|lustre|NFS'),
        'INFRA_STORAGE',
        lambda m, _: {},
    ),
    # Kernel I/O error (EIO)
    (
        re.compile(r'\bInput/output error\b|\bEIO\b'),
        'INFRA_STORAGE',
        lambda m, _: {},
    ),
    # Read-only filesystem (EROFS)
    (
        re.compile(r'Read-only file system|\bEROFS\b'),
        'INFRA_STORAGE',
        lambda m, _: {},
    ),
    # NFS v4 errors
    (
        re.compile(r'NFS4ERR|nfs4.*?error', re.IGNORECASE),
        'INFRA_STORAGE',
        lambda m, _: {},
    ),
    # BeeGFS / GPFS / Weka errors
    (
        re.compile(r'BeeGFS.*?error|beegfs.*?fail|GPFS.*?error|weka.*?error', re.IGNORECASE),
        'INFRA_STORAGE',
        lambda m, _: {},
    ),
    # Socket disconnected (often Lustre / NFS client losing server)
    (
        re.compile(r'Transport endpoint is not connected'),
        'INFRA_STORAGE',
        lambda m, _: {},
    ),
    # Disk full / quota exceeded
    (
        re.compile(r'No space left on device|Disk quota exceeded'),
        'INFRA_STORAGE',
        lambda m, _: {},
    ),

    # ==== USER_ERROR ====

    # Slurm execve failure with job_id (Tier-1 evidence)
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
    # Python import / module errors
    (
        re.compile(r'ModuleNotFoundError|ImportError:.*No module named'),
        'USER_ERROR',
        lambda m, _: {},
    ),
    # Python syntax / name errors (job script bugs)
    (
        re.compile(r'\b(?:SyntaxError|IndentationError|NameError|AttributeError)\b'),
        'USER_ERROR',
        lambda m, _: {},
    ),
    # Script or binary not found / not executable
    (
        re.compile(r'Permission denied.*\.\w+|command not found'),
        'USER_ERROR',
        lambda m, _: {},
    ),
    # Slurm submission / configuration errors
    (
        re.compile(r'sbatch: error:|srun: error:.*(?:No such|Invalid (?:partition|account|qos))'),
        'USER_ERROR',
        lambda m, _: {},
    ),
    # Container / Singularity image errors
    (
        re.compile(r'FATAL:.*image|singularity.*?error.*?image', re.IGNORECASE),
        'USER_ERROR',
        lambda m, _: {},
    ),
]


# ---------------------------------------------------------------------------
# Core data type
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Per-file read-position state
#
# Maps absolute path → (inode, byte_offset).
# The classifier is a long-running process; this dict persists across
# parse_logs() calls so each call reads only the bytes written since the
# last call, rather than re-scanning the entire file.
#
# Rotation detection: if the file's current inode differs from the stored
# inode, the log was rotated and we start from byte 0 of the new file.
# Truncation guard: if the stored offset exceeds the current file size
# (truncate-and-reuse rather than rename-and-recreate), we also reset to 0.
# ---------------------------------------------------------------------------

_file_state: dict[str, tuple[int, int]] = {}  # abs_path → (inode, offset)


def _reset_file_state() -> None:
    """Clear all tracked file positions. Called between test cases."""
    _file_state.clear()


# ---------------------------------------------------------------------------
# File parsers
# ---------------------------------------------------------------------------

def _parse_file(path: Path) -> list[LogEvidence]:
    evidence: list[LogEvidence] = []
    if not path.exists():
        return evidence

    abs_path = str(path.resolve())
    stat     = path.stat()

    stored_inode, stored_offset = _file_state.get(abs_path, (None, 0))

    if stored_inode is not None and stored_inode == stat.st_ino:
        # Same file — resume from last position, but guard against truncation.
        start_offset = stored_offset if stored_offset <= stat.st_size else 0
    else:
        # First read, or the file was rotated (new inode) — start from scratch.
        start_offset = 0

    # Binary mode: seek/tell are unambiguous byte offsets on all platforms.
    with open(path, 'rb') as f:
        f.seek(start_offset)
        for raw in f:
            line = raw.decode('utf-8', errors='replace').rstrip()
            ts, body = _split_line(line)
            if ts is None:
                continue

            for pattern, category, extract in _RULES:
                m = pattern.search(body)
                if not m:
                    continue
                extras = extract(m, line)
                evidence.append(LogEvidence(
                    timestamp     = ts,
                    category_hint = category,
                    source_file   = path.name,
                    raw_line      = line,
                    job_id        = extras.get('job_id'),
                    node          = extras.get('node'),
                    detail        = extras.get('detail'),
                ))
                break  # first matching rule wins per line

        _file_state[abs_path] = (stat.st_ino, f.tell())

    return evidence


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Primary Slurm logs always scanned.
_PRIMARY_LOGS = ['slurmctld.log', 'slurmd.log']

# Supplementary logs scanned when present; silence if missing.
# Covers: kernel/dmesg forwarded via rsyslog, DCGM daemon log, generic syslog.
_SUPPLEMENTARY_LOGS = [
    'dmesg.log',
    'kern.log',
    'dcgm.log',
    'syslog.log',
    'messages.log',
    'nvidia-smi.log',
]


def parse_logs(log_dir: str) -> list[LogEvidence]:
    """
    Parse all known log files in log_dir.

    Incremental: on repeated calls within the same process, only bytes
    written since the last call are parsed. File rotation is detected by
    inode comparison; a new inode triggers a full re-read from offset 0.

    Returns list of LogEvidence sorted by timestamp.
    """
    base = Path(log_dir)
    evidence: list[LogEvidence] = []
    for name in _PRIMARY_LOGS + _SUPPLEMENTARY_LOGS:
        evidence.extend(_parse_file(base / name))
    evidence.sort(key=lambda e: e.timestamp)
    return evidence


def summarise(evidence: list[LogEvidence]) -> dict[str, list[LogEvidence]]:
    """Group evidence by category_hint for quick inspection."""
    out: dict[str, list[LogEvidence]] = {}
    for e in evidence:
        out.setdefault(e.category_hint, []).append(e)
    return out
