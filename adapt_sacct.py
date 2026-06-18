#!/usr/bin/env python3
"""
Real-cluster sacct helper.
Fetches job records from the Slurm accounting database via `sacct --json`
and writes them to SACCT_PATH so the classifier can read them.

Two usage patterns:

  1. Epilog hook (per-job, called by Slurm at job completion):
       python adapt_sacct.py --job-id $SLURM_JOB_ID

  2. Cron / poll mode (all jobs in the last N minutes):
       python adapt_sacct.py --lookback 30

The output file is written atomically (via a temp file + rename) to avoid
the classifier reading a partially written file.

sacct --json is available in Slurm 21.08+. On older Slurm, use the
--parsable2 flag instead and preprocess the output separately.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

SACCT_PATH = os.environ.get('SACCT_PATH', '/logs/sacct_data.json')


def run_sacct(job_id: str | None, lookback_minutes: int) -> dict:
    cmd = ['sacct', '--json']
    if job_id:
        cmd += ['-j', str(job_id)]
    else:
        since = (
            datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
        ).strftime('%FT%T')
        cmd += ['--starttime', since]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        print(f'sacct error (exit {result.returncode}): {result.stderr.strip()}',
              file=sys.stderr)
        sys.exit(1)

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(f'sacct output is not valid JSON: {exc}', file=sys.stderr)
        sys.exit(1)


def write_atomic(path: str, data: dict) -> None:
    """Write JSON to a temp file in the same directory, then rename."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception:
        os.unlink(tmp)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Fetch sacct data for the GPU fleet failure classifier'
    )
    parser.add_argument('--job-id',  help='Classify a single job (epilog hook mode)')
    parser.add_argument('--lookback', type=int, default=30,
                        help='Minutes to look back when fetching all jobs (default: 30)')
    parser.add_argument('--output', default=SACCT_PATH,
                        help=f'Output path (default: {SACCT_PATH})')
    args = parser.parse_args()

    data = run_sacct(job_id=args.job_id, lookback_minutes=args.lookback)
    write_atomic(args.output, data)

    n = len(data.get('jobs', []))
    label = f'job {args.job_id}' if args.job_id else f'last {args.lookback}min'
    print(f'Wrote {n} job record(s) ({label}) to {args.output}')


if __name__ == '__main__':
    main()
