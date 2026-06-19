#!/usr/bin/env python3
"""
Explain mode: run the classifier on a specific job and print the full
reasoning chain — evidence found, category decision, why that category won.

CLI usage:
  python -m classifier.explain <job_id>

Also importable by override_api.py for the GET /api/v1/explain/{job_id} endpoint.
"""

import os
import sys
from typing import Optional

from classifier.log_parser   import parse_logs
from classifier.sacct_parser import parse_sacct
from classifier.classifier   import (
    _evidence_for_job,
    _assign_orphan_evidence,
    classify,
    STATE_HINTS,
    PRIORITY,
    LOG_DIR,
    SACCT_PATH,
    SACCT_FMT,
)

try:
    import psycopg2
    import psycopg2.extras
    _DB_AVAILABLE = True
except ImportError:
    _DB_AVAILABLE = False

DB_HOST = os.environ.get('POSTGRES_HOST',    'postgres')
DB_PORT = int(os.environ.get('POSTGRES_PORT', '5432'))
DB_NAME = os.environ.get('POSTGRES_DB',      'fleetdb')
DB_USER = os.environ.get('POSTGRES_USER',    'fleet')
DB_PASS = os.environ.get('POSTGRES_PASSWORD','fleet123')


def _fetch_stored(job_id: str) -> Optional[dict]:
    """Query job_events + correlation_results + overrides for what was stored."""
    if not _DB_AVAILABLE:
        return None
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT,
            dbname=DB_NAME, user=DB_USER, password=DB_PASS,
            connect_timeout=3,
        )
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                '''SELECT failure_category, classification_confidence,
                          original_failure_category, is_overridden,
                          log_patterns_matched
                   FROM job_events WHERE job_id = %s''',
                (job_id,)
            )
            job_row = cur.fetchone()

            cur.execute(
                '''SELECT metric_name, node_hostname, gpu_index, signal_detected,
                          lead_time_seconds, peak_anomaly_value
                   FROM correlation_results
                   WHERE job_id = %s AND signal_detected = TRUE
                   ORDER BY lead_time_seconds DESC NULLS LAST''',
                (job_id,)
            )
            signals = cur.fetchall()

            cur.execute(
                '''SELECT corrected_category, overridden_by, overridden_at, reason
                   FROM classification_overrides
                   WHERE job_id = %s ORDER BY overridden_at DESC''',
                (job_id,)
            )
            overrides = cur.fetchall()

        conn.close()
        return {
            'job_row':  dict(job_row) if job_row else None,
            'signals':  [dict(r) for r in signals],
            'overrides':[dict(r) for r in overrides],
        }
    except Exception:
        return None


def explain_job(job_id: str) -> dict:
    """
    Re-run classifier logic on a specific job and return full reasoning.
    Also queries DB for stored result and any overrides.
    Returns a dict suitable for JSON serialization or pretty-printing.
    """
    all_evidence = parse_logs(LOG_DIR)
    jobs         = parse_sacct(SACCT_PATH, fmt=None if SACCT_FMT == 'auto' else SACCT_FMT)

    job = next((j for j in jobs if j.job_id == job_id), None)
    if job is None:
        return {'error': f'Job {job_id} not found in sacct data at {SACCT_PATH}'}

    claimed_nodes = {e.node: e.job_id for e in all_evidence if e.job_id and e.node}
    orphan_map    = _assign_orphan_evidence(jobs, all_evidence)
    job_evidence  = (
        _evidence_for_job(job, all_evidence, claimed_nodes)
        + orphan_map.get(job_id, [])
    )

    category, confidence, patterns = classify(job, job_evidence)

    state_hint = STATE_HINTS.get(job.state)
    log_cats   = [e.category_hint for e in job_evidence]

    priority_note = (
        f'{category} (index {PRIORITY.index(category)} in priority chain)'
        if category and category in PRIORITY else 'no match'
    )

    stored = _fetch_stored(job_id)

    return {
        'job_id':   job_id,
        'job_name': job.job_name,
        'nodes':    job.node_list,
        'state':    job.state,
        'elapsed_seconds': job.elapsed_seconds,
        'live_classification': {
            'category':   category,
            'confidence': confidence,
        },
        'reasoning': {
            'sacct_state_hint':        state_hint,
            'log_evidence_categories': sorted(set(log_cats)),
            'priority_resolution':     priority_note,
        },
        'evidence': [
            {
                'timestamp':    e.timestamp.isoformat(),
                'source_file':  e.source_file,
                'category_hint':e.category_hint,
                'detail':       e.detail,
                'match_type':   'job_id' if (e.job_id == job_id) else ('node' if e.node else 'orphan'),
                'raw_line':     e.raw_line[:200],
            }
            for e in job_evidence
        ],
        'stored': stored,
    }


def format_explain(result: dict) -> str:
    """Render explain result as human-readable text for CLI output."""
    if 'error' in result:
        return f"Error: {result['error']}"

    lines = []
    lines.append(f"Job {result['job_id']}: {result['job_name']}")
    lines.append(f"  Nodes:   {', '.join(result['nodes'])}")
    lines.append(f"  State:   {result['state']} ({result['elapsed_seconds']}s elapsed)")
    lines.append('')

    lc = result['live_classification']
    lines.append(f"  Live verdict:  {lc['category']} ({lc['confidence']})")

    stored = result.get('stored') or {}
    job_row = stored.get('job_row')
    if job_row:
        stored_cat = job_row.get('failure_category', '—')
        overridden = job_row.get('is_overridden', False)
        orig       = job_row.get('original_failure_category')
        if overridden and orig:
            lines.append(f"  Stored verdict: {stored_cat}  (overridden from {orig})")
        else:
            lines.append(f"  Stored verdict: {stored_cat}")
    lines.append('')

    r = result['reasoning']
    if r['sacct_state_hint']:
        lines.append(f"  sacct state hint:    {r['sacct_state_hint']}")
    cats = ', '.join(r['log_evidence_categories']) or 'none'
    lines.append(f"  Log evidence cats:   {cats}")
    lines.append(f"  Priority resolution: {r['priority_resolution']}")
    lines.append('')

    evidence = result['evidence']
    if evidence:
        lines.append(f"  Evidence ({len(evidence)} records):")
        for e in evidence:
            lines.append(
                f"    [{e['source_file']:16s} {e['timestamp'][11:19]}]"
                f"  [{e['category_hint']:20s}]  ({e['match_type']})"
            )
            lines.append(f"      {e['raw_line'][:120]}")
    else:
        lines.append('  No log evidence matched for this job.')
    lines.append('')

    signals = stored.get('signals', [])
    if signals:
        lines.append(f"  Pre-failure signals ({len(signals)} detected):")
        for s in signals:
            lead     = f"{s['lead_time_seconds'] // 60}min" if s['lead_time_seconds'] else '?'
            gpu_str  = f"/GPU{s['gpu_index']}" if s.get('gpu_index') is not None else ''
            lines.append(
                f"    {s['metric_name']:45s}  node={s['node_hostname']}{gpu_str}"
                f"  lead={lead}  peak={s['peak_anomaly_value']:.1f}"
            )
    else:
        lines.append('  No pre-failure signals found in correlation results.')

    overrides = stored.get('overrides', [])
    if overrides:
        lines.append('')
        lines.append('  Override history:')
        for o in overrides:
            ts = str(o['overridden_at'])[:19]
            lines.append(
                f"    [{ts}] {o['overridden_by']}: → {o['corrected_category']}"
                + (f"  ({o['reason']})" if o['reason'] else '')
            )

    return '\n'.join(lines)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python -m classifier.explain <job_id>')
        sys.exit(1)
    result = explain_job(sys.argv[1])
    print(format_explain(result))
