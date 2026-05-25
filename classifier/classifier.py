#!/usr/bin/env python3
"""
Failure classifier.
Runs every 15 minutes. Reads sacct records + Slurm logs, applies the
8-category taxonomy from the spec, and upserts into job_events.

Classification priority (first match wins):
  GPU_HARDWARE > NCCL_COMM_FAILURE > CUDA_OOM > THERMAL_THROTTLE >
  INFRA_STORAGE > PREEMPTION > TIMEOUT > USER_ERROR
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras
import requests

from classifier.log_parser  import parse_logs, LogEvidence
from classifier.sacct_parser import parse_sacct, SacctJob

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
LOG_DIR       = os.environ.get('LOG_DIR',        '/logs')
SACCT_PATH    = os.environ.get('SACCT_PATH',     '/logs/sacct_data.json')
PROMETHEUS    = os.environ.get('PROMETHEUS_URL', 'http://prometheus:9090')
PUSHGATEWAY   = os.environ.get('PUSHGATEWAY_URL', 'http://pushgateway:9091')
DB_HOST       = os.environ.get('POSTGRES_HOST',  'postgres')
DB_PORT       = int(os.environ.get('POSTGRES_PORT', '5432'))
DB_NAME       = os.environ.get('POSTGRES_DB',    'fleetdb')
DB_USER       = os.environ.get('POSTGRES_USER',  'fleet')
DB_PASS       = os.environ.get('POSTGRES_PASSWORD', 'fleet123')
RUN_INTERVAL  = int(os.environ.get('RUN_INTERVAL', 900))   # 15 min


# ---------------------------------------------------------------------------
# Sacct state → primary category mapping (before log refinement)
# ---------------------------------------------------------------------------
STATE_HINTS: dict[str, str] = {
    'NODE_FAIL':      'GPU_HARDWARE',
    'OUT_OF_MEMORY':  'CUDA_OOM',
    'PREEMPTED':      'PREEMPTION',
    'TIMEOUT':        'TIMEOUT',
}

# Category priority order (index = priority, lower = higher priority)
PRIORITY = [
    'GPU_HARDWARE',
    'NCCL_COMM_FAILURE',
    'CUDA_OOM',
    'THERMAL_THROTTLE',
    'INFRA_STORAGE',
    'PREEMPTION',
    'TIMEOUT',
    'USER_ERROR',
    'UNKNOWN',   # lowest priority: used when no pattern matches and no thermal signal
]


# ---------------------------------------------------------------------------
# Evidence matching
# ---------------------------------------------------------------------------

def _evidence_for_job(
    job: SacctJob,
    all_evidence: list[LogEvidence],
    claimed_nodes: dict[str, str] | None = None,
) -> list[LogEvidence]:
    """
    Tier-1 and Tier-2 evidence only (strong matches):
      1. Direct job_id match.
      2. Evidence has a node AND that node is in the job's node list AND
         the timestamp falls within [start_time, end_time] AND the node
         has not been claimed by a different job via a Tier-1 match.
    Orphan evidence (no job_id, no node) is handled separately in run_once
    via _assign_orphan_evidence to avoid cross-contamination.

    claimed_nodes maps node → job_id for nodes already linked to a specific
    job by direct evidence (e.g. _job_requeue). A node claimed by job X
    will not Tier-2 match any other job, preventing a single node-down event
    from contaminating every other job that happened to share that node.
    """
    matched: list[LogEvidence] = []
    for e in all_evidence:
        if e.job_id and e.job_id == job.job_id:
            matched.append(e)
            continue
        if e.job_id and e.job_id != job.job_id:
            continue  # already owned by a different job
        if e.node is not None and e.node in job.node_list:
            # Skip if this node was claimed by a different job via Tier-1
            if claimed_nodes and e.node in claimed_nodes and claimed_nodes[e.node] != job.job_id:
                continue
            if job.start_time and job.end_time:
                if job.start_time <= e.timestamp <= job.end_time:
                    matched.append(e)
    return matched


def _assign_orphan_evidence(
    jobs: list[SacctJob],
    all_evidence: list[LogEvidence],
    max_delta_s: int = 600,
) -> dict[str, list[LogEvidence]]:
    """
    For evidence records with neither job_id nor node, assign each to the
    job whose end_time is closest (within max_delta_s seconds).
    Returns {job_id: [evidence]}.
    """
    assignment: dict[str, list[LogEvidence]] = {j.job_id: [] for j in jobs}
    orphans = [e for e in all_evidence if not e.job_id and not e.node]

    for e in orphans:
        best_job: Optional[SacctJob] = None
        best_delta = max_delta_s
        for job in jobs:
            if not job.end_time:
                continue
            delta = abs((job.end_time - e.timestamp).total_seconds())
            if delta < best_delta:
                best_delta = delta
                best_job   = job
        if best_job:
            assignment[best_job.job_id].append(e)

    return assignment


def _best_category(candidates: list[str]) -> Optional[str]:
    """Return the highest-priority category from a list of candidates."""
    best_idx = len(PRIORITY)
    best_cat = None
    for cat in candidates:
        if cat in PRIORITY:
            idx = PRIORITY.index(cat)
            if idx < best_idx:
                best_idx = idx
                best_cat = cat
    return best_cat


# ---------------------------------------------------------------------------
# Thermal throttle detection via Prometheus
# ---------------------------------------------------------------------------

def _prom_instant(url: str, query: str) -> Optional[float]:
    try:
        r = requests.get(f'{url}/api/v1/query', params={'query': query}, timeout=5)
        data = r.json()
        results = data.get('data', {}).get('result', [])
        if results:
            return float(results[0]['value'][1])
    except Exception:
        pass
    return None


def _is_thermal_throttle(job: SacctJob) -> bool:
    """
    Check if any node in the job had GPU_TEMP > 82°C during the job window.
    Only called for FAILED jobs with no stronger log pattern.
    """
    if not job.end_time:
        return False
    end_ts   = int(job.end_time.timestamp())
    start_ts = end_ts - job.elapsed_seconds

    for node in job.node_list:
        query = (
            f'max_over_time(DCGM_FI_DEV_GPU_TEMP{{'
            f'hostname="{node}"}}[{job.elapsed_seconds}s])'
        )
        val = _prom_instant(PROMETHEUS, query)
        if val is not None and val > 82.0:
            return True
    return False


# ---------------------------------------------------------------------------
# Classification logic
# ---------------------------------------------------------------------------

def classify(job: SacctJob, evidence: list[LogEvidence]) -> tuple[str, str, list[str]]:
    """
    Returns (failure_category, confidence, patterns_matched).
    confidence: 'HIGH' | 'MEDIUM' | 'LOW'
    """
    if job.state == 'COMPLETED':
        return (None, None, [])

    log_cats    = [e.category_hint for e in evidence]
    patterns    = list({e.raw_line.strip() for e in evidence})
    state_hint  = STATE_HINTS.get(job.state)

    # Gather all candidate categories
    candidates: list[str] = []
    if state_hint:
        candidates.append(state_hint)
    candidates.extend(log_cats)

    best = _best_category(candidates)

    # FAILED with no distinguishing log patterns → check thermal, else UNKNOWN.
    # NOTE: do NOT fall back to USER_ERROR here. On a real cluster the majority
    # of unclassified FAILED jobs are infrastructure issues, not user mistakes.
    # Returning USER_ERROR for unclassified failures would mislead on-call into
    # blaming researchers for infra problems and suppress further investigation.
    if job.state == 'FAILED' and not log_cats:
        if _is_thermal_throttle(job):
            return ('THERMAL_THROTTLE', 'MEDIUM', [])
        return ('UNKNOWN', 'LOW', [])

    if best is None:
        return ('UNKNOWN', 'LOW', patterns)

    # Confidence: HIGH if log evidence agrees with state hint or log is the sole signal
    if log_cats and (state_hint is None or best in log_cats):
        confidence = 'HIGH'
    elif state_hint == best:
        confidence = 'MEDIUM'
    else:
        confidence = 'LOW'

    return (best, confidence, patterns)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def _connect() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        dbname=DB_NAME, user=DB_USER, password=DB_PASS,
    )


UPSERT_SQL = """
INSERT INTO job_events (
    job_id, job_name, account, state, exit_code, node_list, gpu_count,
    start_time, end_time, elapsed_seconds,
    failure_category, classification_confidence, log_patterns_matched
) VALUES (
    %(job_id)s, %(job_name)s, %(account)s, %(state)s, %(exit_code)s,
    %(node_list)s, %(gpu_count)s,
    %(start_time)s, %(end_time)s, %(elapsed_seconds)s,
    %(failure_category)s, %(classification_confidence)s, %(log_patterns_matched)s
)
ON CONFLICT (job_id) DO UPDATE SET
    failure_category          = EXCLUDED.failure_category,
    classification_confidence = EXCLUDED.classification_confidence,
    log_patterns_matched      = EXCLUDED.log_patterns_matched;
"""


def upsert_job(conn, job: SacctJob, category: Optional[str],
               confidence: Optional[str], patterns: list[str]) -> None:
    with conn.cursor() as cur:
        cur.execute(UPSERT_SQL, {
            'job_id':                    job.job_id,
            'job_name':                  job.job_name,
            'account':                   job.account,
            'state':                     job.state,
            'exit_code':                 job.exit_code,
            'node_list':                 job.node_list,
            'gpu_count':                 job.gpu_count,
            'start_time':                job.start_time,
            'end_time':                  job.end_time,
            'elapsed_seconds':           job.elapsed_seconds,
            'failure_category':          category,
            'classification_confidence': confidence,
            'log_patterns_matched':      json.dumps(patterns),
        })
    conn.commit()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

_INSERT_RUN_SQL = """
INSERT INTO classifier_runs (run_at, jobs_written, jobs_skipped, errors, duration_ms)
VALUES (NOW(), %(jobs_written)s, %(jobs_skipped)s, %(errors)s, %(duration_ms)s);
"""


def run_once() -> dict:
    log.info('Classifier run starting')
    t_start  = time.monotonic()
    evidence = parse_logs(LOG_DIR)
    jobs     = parse_sacct(SACCT_PATH)
    log.info(f'  {len(evidence)} log evidence records, {len(jobs)} sacct jobs')

    orphan_map    = _assign_orphan_evidence(jobs, evidence)
    # Nodes explicitly linked to a job via direct job_id+node evidence (e.g. _job_requeue).
    # Used to block Tier-2 cross-contamination on shared nodes.
    claimed_nodes = {e.node: e.job_id for e in evidence if e.job_id and e.node}

    conn    = _connect()
    results = {'written': 0, 'skipped': 0, 'errors': 0}

    try:
        for job in jobs:
            job_evidence = _evidence_for_job(job, evidence, claimed_nodes) + orphan_map.get(job.job_id, [])
            category, confidence, patterns = classify(job, job_evidence)
            try:
                upsert_job(conn, job, category, confidence, patterns)
                if category is not None:
                    log.info(f'  {job.job_id} → {category} ({confidence})')
                results['written'] += 1
            except Exception as exc:
                log.warning(f'  DB error for {job.job_id}: {exc}')
                conn.rollback()
                results['errors'] += 1

        # Persist run summary to classifier_runs table
        duration_ms = int((time.monotonic() - t_start) * 1000)
        try:
            with conn.cursor() as cur:
                cur.execute(_INSERT_RUN_SQL, {
                    'jobs_written': results['written'],
                    'jobs_skipped': results['skipped'],
                    'errors':       results['errors'],
                    'duration_ms':  duration_ms,
                })
            conn.commit()
        except Exception as exc:
            log.warning(f'  Failed to write classifier_runs row: {exc}')
            conn.rollback()
    finally:
        conn.close()

    log.info(f'Done: {results}')
    return results


# ---------------------------------------------------------------------------
# Classifier health metrics (Prometheus pushgateway)
# ---------------------------------------------------------------------------

# Cumulative counters — kept in process memory across runs.
_runs_total       = 0
_errors_total     = 0
_classified_total = 0


def _push_metrics(results: dict) -> None:
    """
    Push classifier health metrics to the Prometheus pushgateway.
    Uses the text exposition format so there are no additional dependencies.
    Fails silently — a metrics push failure must not abort a classifier run.
    """
    global _runs_total, _errors_total, _classified_total

    _runs_total       += 1
    _errors_total     += results.get('errors', 0)
    _classified_total += results.get('written', 0)

    now_ts = time.time()

    payload = (
        f'# HELP classifier_runs_total Total classifier run attempts\n'
        f'# TYPE classifier_runs_total counter\n'
        f'classifier_runs_total {_runs_total}\n'
        f'# HELP classifier_errors_total Total runs that raised an exception\n'
        f'# TYPE classifier_errors_total counter\n'
        f'classifier_errors_total {_errors_total}\n'
        f'# HELP classifier_jobs_classified_total Total jobs written to job_events\n'
        f'# TYPE classifier_jobs_classified_total counter\n'
        f'classifier_jobs_classified_total {_classified_total}\n'
        f'# HELP classifier_last_run_timestamp Unix epoch of last successful run\n'
        f'# TYPE classifier_last_run_timestamp gauge\n'
        f'classifier_last_run_timestamp {now_ts}\n'
    )

    try:
        url = f'{PUSHGATEWAY}/metrics/job/classifier'
        resp = requests.post(url, data=payload, timeout=5)
        resp.raise_for_status()
        log.debug(f'Metrics pushed to pushgateway: runs={_runs_total} errors={_errors_total}')
    except Exception as exc:
        log.warning(f'Failed to push metrics to pushgateway: {exc}')


def main() -> None:
    while True:
        results: dict = {'written': 0, 'skipped': 0, 'errors': 0}
        try:
            results = run_once()
        except Exception as exc:
            log.error(f'Classifier run failed: {exc}')
            results['errors'] += 1
        finally:
            _push_metrics(results)
        time.sleep(RUN_INTERVAL)


if __name__ == '__main__':
    main()
