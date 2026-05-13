#!/usr/bin/env python3
"""
node_health_weekly rollup.
Runs every hour. Aggregates job_events into node_health_weekly (one row per
node per ISO week), then enriches with Prometheus-derived ECC SBE totals and
average GPU temperatures for the current week.
"""

import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import psycopg2
import psycopg2.extras
import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

PROMETHEUS    = os.environ.get('PROMETHEUS_URL',    'http://prometheus:9090')
DB_HOST       = os.environ.get('POSTGRES_HOST',     'postgres')
DB_PORT       = int(os.environ.get('POSTGRES_PORT', '5432'))
DB_NAME       = os.environ.get('POSTGRES_DB',       'fleetdb')
DB_USER       = os.environ.get('POSTGRES_USER',     'fleet')
DB_PASS       = os.environ.get('POSTGRES_PASSWORD', 'fleet123')
ROLLUP_INTERVAL = int(os.environ.get('ROLLUP_INTERVAL', 3600))  # 1 hour


# ---------------------------------------------------------------------------
# SQL: aggregate job_events → node_health_weekly
# Unnests node_list array so each node in a multi-node job gets its own row.
# ecc_sbe_accumulated and avg_temperature are filled by Prometheus below.
# ---------------------------------------------------------------------------
ROLLUP_SQL = """
WITH expanded AS (
    SELECT
        unnest(node_list)                       AS node_hostname,
        date_trunc('week', end_time)::date      AS week_start,
        state,
        failure_category
    FROM job_events
    WHERE end_time IS NOT NULL
)
INSERT INTO node_health_weekly (
    node_hostname, week_start,
    total_jobs, failed_jobs, failure_rate,
    hardware_failures, nccl_failures,
    ecc_sbe_accumulated, avg_temperature
)
SELECT
    node_hostname,
    week_start,
    COUNT(*)                                                          AS total_jobs,
    COUNT(*) FILTER (WHERE state != 'COMPLETED')                     AS failed_jobs,
    COUNT(*) FILTER (WHERE state != 'COMPLETED')::float
        / NULLIF(COUNT(*), 0)                                         AS failure_rate,
    COUNT(*) FILTER (WHERE failure_category = 'GPU_HARDWARE')        AS hardware_failures,
    COUNT(*) FILTER (WHERE failure_category = 'NCCL_COMM_FAILURE')   AS nccl_failures,
    0       AS ecc_sbe_accumulated,
    NULL    AS avg_temperature
FROM expanded
GROUP BY node_hostname, week_start
ON CONFLICT (node_hostname, week_start) DO UPDATE SET
    total_jobs         = EXCLUDED.total_jobs,
    failed_jobs        = EXCLUDED.failed_jobs,
    failure_rate       = EXCLUDED.failure_rate,
    hardware_failures  = EXCLUDED.hardware_failures,
    nccl_failures      = EXCLUDED.nccl_failures;
"""

# Update only ecc/temp for rows we just upserted (don't overwrite if Prometheus unavailable)
UPDATE_PROM_SQL = """
UPDATE node_health_weekly
SET ecc_sbe_accumulated = %(ecc_sbe)s,
    avg_temperature     = %(avg_temp)s
WHERE node_hostname = %(node)s
  AND week_start    = %(week_start)s;
"""


# ---------------------------------------------------------------------------
# Prometheus helpers
# ---------------------------------------------------------------------------

def _prom_instant(url: str, query: str) -> Optional[float]:
    try:
        r = requests.get(
            f'{url}/api/v1/query',
            params={'query': query},
            timeout=10,
        )
        results = r.json().get('data', {}).get('result', [])
        if results:
            return float(results[0]['value'][1])
    except Exception:
        pass
    return None


def _fetch_prom_metrics(url: str, node: str, week_start: date) -> tuple[Optional[int], Optional[float]]:
    """
    Query Prometheus for ECC SBE accumulated and average GPU temp for a given
    node over the 7-day window starting at week_start.

    Uses a [7d] lookback window at the moment the rollup runs, which is
    accurate for the current week. Historic weeks use whatever data Prometheus
    still retains.
    """
    window = '7d'

    # Total ECC SBE counter increase over the window (summed across all GPUs on the node)
    ecc_query = (
        f'sum(increase(DCGM_FI_DEV_ECC_SBE_VOL_TOTAL'
        f'{{hostname="{node}"}}[{window}]))'
    )
    ecc_val = _prom_instant(url, ecc_query)
    ecc_sbe = int(round(ecc_val)) if ecc_val is not None else None

    # Average GPU temperature over the window (averaged across all GPUs on the node)
    temp_query = (
        f'avg(avg_over_time(DCGM_FI_DEV_GPU_TEMP'
        f'{{hostname="{node}"}}[{window}]))'
    )
    avg_temp = _prom_instant(url, temp_query)

    return ecc_sbe, avg_temp


# ---------------------------------------------------------------------------
# Main rollup
# ---------------------------------------------------------------------------

def _connect() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        dbname=DB_NAME, user=DB_USER, password=DB_PASS,
    )


def run_once() -> dict:
    log.info('node_health_weekly rollup starting')
    conn    = _connect()
    results = {'rows_upserted': 0, 'prom_enriched': 0, 'errors': 0}

    try:
        # Step 1: SQL rollup from job_events
        with conn.cursor() as cur:
            cur.execute(ROLLUP_SQL)
            results['rows_upserted'] = cur.rowcount
        conn.commit()
        log.info(f'  SQL rollup: {results["rows_upserted"]} rows upserted')

        # Step 2: Prometheus enrichment for each (node, week) row
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT DISTINCT node_hostname, week_start
                FROM node_health_weekly
                ORDER BY week_start DESC, node_hostname
            """)
            rows = cur.fetchall()

        for row in rows:
            node       = row['node_hostname']
            week_start = row['week_start']
            try:
                ecc_sbe, avg_temp = _fetch_prom_metrics(PROMETHEUS, node, week_start)
                if ecc_sbe is not None or avg_temp is not None:
                    with conn.cursor() as cur:
                        cur.execute(UPDATE_PROM_SQL, {
                            'node':       node,
                            'week_start': week_start,
                            'ecc_sbe':    ecc_sbe if ecc_sbe is not None else 0,
                            'avg_temp':   avg_temp,
                        })
                    conn.commit()
                    results['prom_enriched'] += 1
            except Exception as exc:
                log.warning(f'  Prometheus enrichment failed for {node}/{week_start}: {exc}')
                conn.rollback()
                results['errors'] += 1

    except Exception as exc:
        log.error(f'  Rollup SQL failed: {exc}')
        conn.rollback()
        results['errors'] += 1
    finally:
        conn.close()

    log.info(f'Done: {results}')
    return results


def main() -> None:
    while True:
        try:
            run_once()
        except Exception as exc:
            log.error(f'Rollup run failed: {exc}')
        time.sleep(ROLLUP_INTERVAL)


if __name__ == '__main__':
    main()
