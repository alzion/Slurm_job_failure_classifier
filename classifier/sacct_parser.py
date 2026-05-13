#!/usr/bin/env python3
"""
sacct JSON parser.
Reads /logs/sacct_data.json, expands node lists, returns SacctJob records.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


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


_BRACKET_RE  = re.compile(r'^(\D+)\[(\d+)-(\d+)\]$')   # gpu[03-10]
_SINGLE_RE   = re.compile(r'^(\D+)\[(\d+)\]$')          # gpu[01]


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
        width  = len(m.group(2))       # preserve leading zeros
        return [f"{prefix}{i:0{width}d}" for i in range(start, end + 1)]

    m = _SINGLE_RE.match(nodelist)
    if m:
        prefix = m.group(1)
        num    = m.group(2)
        return [f"{prefix}{num}"]

    return [nodelist]


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


def parse_sacct(sacct_path: str) -> list[SacctJob]:
    """
    Parse sacct_data.json. Returns list of SacctJob, sorted by end_time.
    Silently skips malformed records.
    """
    path = Path(sacct_path)
    if not path.exists():
        return []

    with open(path) as f:
        try:
            records = json.load(f)
        except json.JSONDecodeError:
            return []

    jobs: list[SacctJob] = []
    for r in records:
        try:
            nodes_raw = r.get('NodeList', '')
            jobs.append(SacctJob(
                job_id       = str(r['JobID']),
                job_name     = r.get('JobName', ''),
                user         = r.get('User', ''),
                account      = r.get('Account', ''),
                state        = r.get('State', ''),
                exit_code    = r.get('ExitCode', '0:0'),
                node_list_raw= nodes_raw,
                node_list    = expand_nodelist(nodes_raw),
                gpu_count    = _parse_gpu_count(r.get('AllocGRES', '')),
                submit_time  = _parse_ts(r.get('Submit')),
                start_time   = _parse_ts(r.get('Start')),
                end_time     = _parse_ts(r.get('End')),
                elapsed_seconds = _parse_elapsed(r.get('Elapsed', '0:0:0')),
                req_mem      = r.get('ReqMem', ''),
                max_rss      = r.get('MaxRSS', ''),
                raw          = r,
            ))
        except (KeyError, TypeError):
            continue

    jobs.sort(key=lambda j: j.end_time or datetime.min.replace(tzinfo=timezone.utc))
    return jobs
