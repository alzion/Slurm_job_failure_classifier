#!/usr/bin/env python3
"""
sacct JSON parser.
Supports two formats selected by the SACCT_FORMAT env var (default: auto-detect):

  simulator  — flat JSON array written by sacct_sim.py
               [{"JobID": 847293, "JobName": "...", "State": "FAILED", ...}, ...]

  real       — native sacct --json output (Slurm 21.08+)
               {"meta": {...}, "jobs": [{"job_id": 847293, "name": "...", ...}]}

  auto       — detects format by checking whether the top-level value is a list
               (simulator) or a dict with a "jobs" key (real).
"""

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SACCT_FORMAT = os.environ.get('SACCT_FORMAT', 'auto')  # auto | simulator | real


@dataclass
class SacctJob:
    job_id: str
    job_name: str
    user: str
    account: str
    state: str
    exit_code: str
    node_list_raw: str
    node_list: list[str]
    gpu_count: int
    submit_time: Optional[datetime]
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    elapsed_seconds: int
    req_mem: str
    max_rss: str
    raw: dict


_BRACKET_RE = re.compile(r'^(\D+)\[(\d+)-(\d+)\]$')
_SINGLE_RE  = re.compile(r'^(\D+)\[(\d+)\]$')


def expand_nodelist(nodelist: str) -> list[str]:
    """
    Expand Slurm compact node notation to a flat list of hostnames.
      gpu[03-10] → [gpu03, gpu04, ..., gpu10]
      gpu[01]    → [gpu01]
      gpu01      → [gpu01]
    """
    nodelist = nodelist.strip()
    m = _BRACKET_RE.match(nodelist)
    if m:
        prefix = m.group(1)
        start  = int(m.group(2))
        end    = int(m.group(3))
        width  = len(m.group(2))
        return [f"{prefix}{i:0{width}d}" for i in range(start, end + 1)]
    m = _SINGLE_RE.match(nodelist)
    if m:
        return [f"{m.group(1)}{m.group(2)}"]
    return [nodelist]


# ---------------------------------------------------------------------------
# Simulator format helpers
# ---------------------------------------------------------------------------

def _parse_ts(s: Optional[str]) -> Optional[datetime]:
    if not s or s in ('Unknown', 'None', ''):
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _parse_elapsed(s: str) -> int:
    """HH:MM:SS → seconds."""
    try:
        parts = s.split(':')
        h, m, sec = int(parts[0]), int(parts[1]), int(parts[2])
        return h * 3600 + m * 60 + sec
    except (ValueError, IndexError):
        return 0


def _parse_gpu_count(alloc_gres: str) -> int:
    """'gpu:8' → 8"""
    m = re.search(r'gpu:(\d+)', alloc_gres or '')
    return int(m.group(1)) if m else 0


def _parse_sim_record(r: dict) -> Optional[SacctJob]:
    try:
        nodes_raw = r.get('NodeList', '')
        return SacctJob(
            job_id          = str(r['JobID']),
            job_name        = r.get('JobName', ''),
            user            = r.get('User', ''),
            account         = r.get('Account', ''),
            state           = r.get('State', ''),
            exit_code       = r.get('ExitCode', '0:0'),
            node_list_raw   = nodes_raw,
            node_list       = expand_nodelist(nodes_raw),
            gpu_count       = _parse_gpu_count(r.get('AllocGRES', '')),
            submit_time     = _parse_ts(r.get('Submit')),
            start_time      = _parse_ts(r.get('Start')),
            end_time        = _parse_ts(r.get('End')),
            elapsed_seconds = _parse_elapsed(r.get('Elapsed', '0:0:0')),
            req_mem         = r.get('ReqMem', ''),
            max_rss         = r.get('MaxRSS', ''),
            raw             = r,
        )
    except (KeyError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Real sacct --json format helpers (Slurm 21.08+)
# ---------------------------------------------------------------------------

def _parse_ts_unix(val) -> Optional[datetime]:
    """Unix timestamp (int/float) → datetime."""
    if not val or val == 0:
        return None
    try:
        return datetime.fromtimestamp(float(val), tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


def _parse_state_real(state_val) -> str:
    """
    Extract state string from sacct --json state field.
    Handles: {"current": ["FAILED"], ...} | {"current": "FAILED"} | "FAILED"
    """
    if isinstance(state_val, str):
        return state_val
    if isinstance(state_val, dict):
        current = state_val.get('current', '')
        if isinstance(current, list):
            return current[0] if current else 'UNKNOWN'
        return str(current)
    return 'UNKNOWN'


def _parse_exit_code_real(ec_val) -> str:
    """{"status": [...], "return_code": 1} → "1:0" """
    if isinstance(ec_val, dict):
        return f"{ec_val.get('return_code', 0)}:0"
    return '0:0'


def _parse_gpu_count_tres(tres_list: list) -> int:
    """Extract GPU count from sacct --json tres allocated list."""
    for item in (tres_list or []):
        if isinstance(item, dict) and item.get('type') == 'gres' and item.get('name') == 'gpu':
            return int(item.get('count', 0))
    return 0


def _parse_real_record(r: dict) -> Optional[SacctJob]:
    try:
        nodes_raw  = r.get('nodes', '') or ''
        time_info  = r.get('time', {}) or {}
        tres       = r.get('tres', {}) or {}
        allocated  = tres.get('allocated', []) if isinstance(tres, dict) else []
        req        = r.get('required', {}) or {}
        req_mem_mb = req.get('memory', 0) if isinstance(req, dict) else 0

        elapsed_raw = time_info.get('elapsed', 0) if isinstance(time_info, dict) else 0

        return SacctJob(
            job_id          = str(r['job_id']),
            job_name        = r.get('name', ''),
            user            = r.get('user', ''),
            account         = r.get('account', ''),
            state           = _parse_state_real(r.get('state', '')),
            exit_code       = _parse_exit_code_real(r.get('exit_code', {})),
            node_list_raw   = nodes_raw,
            node_list       = expand_nodelist(nodes_raw),
            gpu_count       = _parse_gpu_count_tres(allocated),
            submit_time     = _parse_ts_unix(time_info.get('submission') if isinstance(time_info, dict) else None),
            start_time      = _parse_ts_unix(time_info.get('start') if isinstance(time_info, dict) else None),
            end_time        = _parse_ts_unix(time_info.get('end') if isinstance(time_info, dict) else None),
            elapsed_seconds = int(elapsed_raw) if elapsed_raw else 0,
            req_mem         = f'{req_mem_mb}M' if req_mem_mb else '',
            max_rss         = '',
            raw             = r,
        )
    except (KeyError, TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

def _detect_format(data) -> str:
    if isinstance(data, dict) and 'jobs' in data:
        return 'real'
    return 'simulator'


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_sacct(sacct_path: str, fmt: Optional[str] = None) -> list[SacctJob]:
    """
    Parse sacct_data.json. Returns list of SacctJob sorted by end_time.

    fmt overrides SACCT_FORMAT env var. Pass None to use the env var.
    Format is auto-detected when both fmt and SACCT_FORMAT are 'auto'.
    """
    path = Path(sacct_path)
    if not path.exists():
        return []

    with open(path) as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            return []

    effective_fmt = fmt or SACCT_FORMAT
    if effective_fmt == 'auto':
        effective_fmt = _detect_format(data)

    if effective_fmt == 'real':
        records = data.get('jobs', []) if isinstance(data, dict) else []
        jobs = [j for r in records if (j := _parse_real_record(r)) is not None]
    else:
        records = data if isinstance(data, list) else []
        jobs = [j for r in records if (j := _parse_sim_record(r)) is not None]

    jobs.sort(key=lambda j: j.end_time or datetime.min.replace(tzinfo=timezone.utc))
    return jobs
