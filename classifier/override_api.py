#!/usr/bin/env python3
"""
Override API — FastAPI service for engineer feedback on classifier decisions.

Endpoints:
  POST /api/v1/override          — submit a correction
  GET  /api/v1/accuracy          — accuracy stats by category (rolling N days)
  GET  /api/v1/explain/{job_id}  — full reasoning chain for a job
  GET  /health                   — liveness check

Run:
  python -m classifier.override_api
  (or via Docker: command: python -m classifier.override_api)
"""

import logging
import os
from datetime import datetime
from typing import Optional

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from classifier.explain import explain_job

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

DB_HOST = os.environ.get('POSTGRES_HOST',    'postgres')
DB_PORT = int(os.environ.get('POSTGRES_PORT', '5432'))
DB_NAME = os.environ.get('POSTGRES_DB',      'fleetdb')
DB_USER = os.environ.get('POSTGRES_USER',    'fleet')
DB_PASS = os.environ.get('POSTGRES_PASSWORD','fleet123')

VALID_CATEGORIES = {
    'GPU_HARDWARE', 'NCCL_COMM_FAILURE', 'CUDA_OOM', 'THERMAL_THROTTLE',
    'INFRA_STORAGE', 'PREEMPTION', 'TIMEOUT', 'USER_ERROR', 'UNKNOWN',
}

app = FastAPI(title='Classifier Override API', version='1.0.0')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
)


def _connect():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        dbname=DB_NAME, user=DB_USER, password=DB_PASS,
    )


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class OverrideRequest(BaseModel):
    job_id: str
    corrected_category: str
    overridden_by: str
    reason: Optional[str] = None


class OverrideResponse(BaseModel):
    job_id: str
    original_category: Optional[str]
    corrected_category: str
    overridden_by: str
    status: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post('/api/v1/override', response_model=OverrideResponse)
def submit_override(req: OverrideRequest):
    if req.corrected_category not in VALID_CATEGORIES:
        raise HTTPException(
            400,
            f"Invalid category '{req.corrected_category}'. "
            f"Valid: {sorted(VALID_CATEGORIES)}"
        )

    conn = _connect()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                'SELECT failure_category, is_overridden FROM job_events WHERE job_id = %s',
                (req.job_id,)
            )
            row = cur.fetchone()
            if row is None:
                raise HTTPException(404, f'Job {req.job_id} not found in job_events')

            original = row['failure_category']

            if not row['is_overridden']:
                # First override: preserve original
                cur.execute(
                    '''UPDATE job_events
                       SET failure_category          = %s,
                           original_failure_category = %s,
                           is_overridden             = TRUE
                       WHERE job_id = %s''',
                    (req.corrected_category, original, req.job_id)
                )
            else:
                # Subsequent correction: only update category
                cur.execute(
                    'UPDATE job_events SET failure_category = %s WHERE job_id = %s',
                    (req.corrected_category, req.job_id)
                )

            cur.execute(
                '''INSERT INTO classification_overrides
                   (job_id, original_category, corrected_category, overridden_by, reason)
                   VALUES (%s, %s, %s, %s, %s)''',
                (req.job_id, original, req.corrected_category,
                 req.overridden_by, req.reason)
            )

        conn.commit()
        log.info(
            f'Override: job {req.job_id}  {original} → {req.corrected_category}'
            f'  by {req.overridden_by}'
        )
        return OverrideResponse(
            job_id             = req.job_id,
            original_category  = original,
            corrected_category = req.corrected_category,
            overridden_by      = req.overridden_by,
            status             = 'recorded',
        )

    except HTTPException:
        raise
    except Exception as exc:
        conn.rollback()
        log.error(f'Override failed: {exc}')
        raise HTTPException(500, str(exc))
    finally:
        conn.close()


@app.get('/api/v1/accuracy')
def get_accuracy(days: int = 30):
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f'''SELECT failure_category, COUNT(*) AS total
                    FROM job_events
                    WHERE end_time > NOW() - INTERVAL '{days} days'
                      AND failure_category IS NOT NULL
                    GROUP BY failure_category'''
            )
            totals = {r['failure_category']: int(r['total']) for r in cur.fetchall()}

            cur.execute(
                f'''SELECT original_category, COUNT(*) AS cnt
                    FROM classification_overrides
                    WHERE overridden_at > NOW() - INTERVAL '{days} days'
                    GROUP BY original_category'''
            )
            override_counts = {r['original_category']: int(r['cnt']) for r in cur.fetchall()}

        stats = []
        for cat in sorted(set(list(totals) + list(override_counts))):
            total      = totals.get(cat, 0)
            overridden = override_counts.get(cat, 0)
            accuracy   = round((1 - overridden / total) * 100, 1) if total > 0 else None
            stats.append({
                'category':          cat,
                'total_classified':  total,
                'overridden':        overridden,
                'accuracy_pct':      accuracy,
            })

        total_all       = sum(totals.values())
        total_overrides = sum(override_counts.values())

        return {
            'period_days': days,
            'overall': {
                'total_classified':  total_all,
                'total_overridden':  total_overrides,
                'override_rate_pct': round(total_overrides / total_all * 100, 1)
                                     if total_all > 0 else 0.0,
            },
            'by_category': stats,
        }
    finally:
        conn.close()


@app.get('/api/v1/explain/{job_id}')
def explain_endpoint(job_id: str):
    result = explain_job(job_id)
    if 'error' in result:
        raise HTTPException(404, result['error'])
    return result


@app.get('/health')
def health():
    return {'status': 'ok'}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import uvicorn
    port = int(os.environ.get('OVERRIDE_API_PORT', '8002'))
    uvicorn.run(app, host='0.0.0.0', port=port, log_level='info')
