#!/usr/bin/env python3
"""
Correlation engine.
For each failed job in job_events, queries Prometheus for pre-failure DCGM
signals, computes lead times, and upserts into correlation_results.
Runs every 15 minutes.
"""

import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras
import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

PROMETHEUS          = os.environ.get('PROMETHEUS_URL',        'http://prometheus:9090')
DCGM_HOSTNAME_LABEL = os.environ.get('DCGM_HOSTNAME_LABEL',   'hostname')
DB_HOST             = os.environ.get('POSTGRES_HOST',          'postgres')
DB_PORT      = int(os.environ.get('POSTGRES_PORT', '5432'))
DB_NAME      = os.environ.get('POSTGRES_DB',       'fleetdb')
DB_USER      = os.environ.get('POSTGRES_USER',     'fleet')
DB_PASS      = os.environ.get('POSTGRES_PASSWORD', 'fleet123')
RUN_INTERVAL = int(os.environ.get('RUN_INTERVAL',  900))

STEP_S = 60  # Prometheus range step in seconds

# ---------------------------------------------------------------------------
# Signal detection rules
# (metric_name, detection_type, threshold, direction)
# detection_type:
#   'gauge'        — raw value crosses threshold
#   'rate_per_hour'— counter rate (counts/hr) crosses threshold
#   'increment'    — counter delta per step crosses threshold
#   'pct_drop'     — gauge drops by ≥ threshold fraction below its first value
# ---------------------------------------------------------------------------
SIGNAL_RULES: list[tuple[str, str, float, str]] = [
    ('DCGM_FI_DEV_ECC_SBE_VOL_TOTAL',               'rate_per_hour', 30.0, 'above'),
    ('DCGM_FI_DEV_ECC_DBE_VOL_TOTAL',               'increment',      0.5,  'above'),
    ('DCGM_FI_DEV_NVLINK_CRC_FLIT_ERROR_COUNT_TOTAL','increment',     0.0,  'above'),
    ('DCGM_FI_DEV_GPU_TEMP',                         'gauge',         82.0, 'above'),
    ('DCGM_FI_DEV_SM_CLOCK',                         'pct_drop',      0.15, 'below'),
    ('DCGM_FI_DEV_XID_ERRORS',                       'gauge',          0.0, 'above'),
]


# ---------------------------------------------------------------------------
# Prometheus helpers
# ---------------------------------------------------------------------------

def _prom_range(url: str, query: str, start: int, end: int) -> list[dict]:
    try:
        r = requests.get(
            f'{url}/api/v1/query_range',
            params={'query': query, 'start': start, 'end': end, 'step': STEP_S},
            timeout=15,
        )
        return r.json().get('data', {}).get('result', [])
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Signal detection
# ---------------------------------------------------------------------------

def _first_crossing(
    pairs: list[tuple[float, float]],
    rule_type: str,
    threshold: float,
    direction: str,
) -> tuple[Optional[float], float, float, float]:
    """
    Scan a time series for the first threshold crossing.
    Returns (onset_unix_ts, baseline, peak_anomaly_value, anomaly_ratio).
    onset_unix_ts is None when no crossing is found.
    """
    if not pairs:
        return None, 0.0, 0.0, 0.0

    times  = [t for t, _ in pairs]
    values = [v for _, v in pairs]
    bl     = values[0]

    if rule_type == 'gauge':
        peak = max(values) if direction == 'above' else min(values)
        for ts, v in pairs:
            if direction == 'above' and v > threshold:
                return ts, bl, peak, (v / threshold) if threshold else 0.0
        return None, bl, peak, 0.0

    elif rule_type == 'rate_per_hour':
        peak_rate = 0.0
        for i in range(1, len(pairs)):
            delta = max(0.0, values[i] - values[i - 1])  # ignore counter resets
            rate  = delta / (STEP_S / 3600.0)
            if rate > peak_rate:
                peak_rate = rate
            if rate > threshold:
                return times[i], bl, peak_rate, rate / threshold
        return None, bl, peak_rate, 0.0

    elif rule_type == 'increment':
        total_increase = max(0.0, values[-1] - bl)
        for i in range(1, len(pairs)):
            delta = max(0.0, values[i] - values[i - 1])
            if delta > threshold:
                return times[i], bl, bl + total_increase, total_increase / (threshold + 1e-9)
        return None, bl, bl + total_increase, 0.0

    elif rule_type == 'pct_drop':
        if bl == 0.0:
            return None, bl, 0.0, 0.0
        peak_drop_v = bl
        for ts, v in pairs:
            if v < peak_drop_v:
                peak_drop_v = v
            drop_frac = (bl - v) / bl
            if drop_frac > threshold:
                return ts, bl, peak_drop_v, drop_frac
        return None, bl, peak_drop_v, (bl - peak_drop_v) / bl if bl else 0.0

    return None, bl, bl, 0.0


def check_signals(
    job_id: str,
    nodes: list[str],
    start_ts: int,
    end_ts: int,
    prom_url: str,
) -> list[dict]:
    """
    For each node × SIGNAL_RULES, query Prometheus and detect crossings.
    Returns one result dict per (node, metric) that has Prometheus data.
    """
    results = []
    for node in nodes:
        for metric, rule_type, threshold, direction in SIGNAL_RULES:
            query      = f'{metric}{{{DCGM_HOSTNAME_LABEL}="{node}"}}'
            raw_series = _prom_range(prom_url, query, start_ts, end_ts)
            if not raw_series:
                continue
            # There may be multiple GPU time series per node; take the first crossing
            best_onset: Optional[float] = None
            best_bl = best_peak = best_ratio = 0.0
            for ts_obj in raw_series:
                pairs = [(float(t), float(v)) for t, v in ts_obj.get('values', []) if v != 'NaN']
                if not pairs:
                    continue
                onset, bl, peak, ratio = _first_crossing(pairs, rule_type, threshold, direction)
                if onset is not None:
                    if best_onset is None or onset < best_onset:
                        best_onset = onset
                        best_bl    = bl
                        best_peak  = peak
                        best_ratio = ratio

            lead_time = int(end_ts - best_onset) if best_onset is not None else None
            results.append({
                'job_id':             job_id,
                'node_hostname':      node,
                'metric_name':        metric,
                'signal_detected':    best_onset is not None,
                'signal_onset_time':  datetime.fromtimestamp(best_onset, tz=timezone.utc) if best_onset else None,
                'lead_time_seconds':  lead_time,
                'baseline_value':     best_bl,
                'peak_anomaly_value': best_peak,
                'anomaly_ratio':      best_ratio,
            })
    return results


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def _connect() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        dbname=DB_NAME, user=DB_USER, password=DB_PASS,
    )


UPSERT_SQL = """
INSERT INTO correlation_results (
    job_id, node_hostname, metric_name, signal_detected,
    signal_onset_time, lead_time_seconds, baseline_value,
    peak_anomaly_value, anomaly_ratio
) VALUES (
    %(job_id)s, %(node_hostname)s, %(metric_name)s, %(signal_detected)s,
    %(signal_onset_time)s, %(lead_time_seconds)s, %(baseline_value)s,
    %(peak_anomaly_value)s, %(anomaly_ratio)s
)
ON CONFLICT (job_id, node_hostname, metric_name) DO UPDATE SET
    signal_detected    = EXCLUDED.signal_detected,
    signal_onset_time  = EXCLUDED.signal_onset_time,
    lead_time_seconds  = EXCLUDED.lead_time_seconds,
    baseline_value     = EXCLUDED.baseline_value,
    peak_anomaly_value = EXCLUDED.peak_anomaly_value,
    anomaly_ratio      = EXCLUDED.anomaly_ratio;
"""


def _fetch_failed_jobs(conn) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT job_id, node_list, start_time, end_time, elapsed_seconds, failure_category
            FROM job_events
            WHERE state != 'COMPLETED' AND failure_category IS NOT NULL
              AND start_time IS NOT NULL AND end_time IS NOT NULL
        """)
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_once() -> dict:
    log.info('Correlation engine run starting')
    conn    = _connect()
    results = {'processed': 0, 'signals_found': 0, 'errors': 0}

    try:
        jobs = _fetch_failed_jobs(conn)
        log.info(f'  {len(jobs)} failed jobs to correlate')

        for job in jobs:
            try:
                start_ts = int(job['start_time'].timestamp())
                end_ts   = int(job['end_time'].timestamp())
                nodes    = list(job['node_list']) if job['node_list'] else []

                signal_rows = check_signals(
                    job['job_id'], nodes, start_ts, end_ts, PROMETHEUS
                )

                with conn.cursor() as cur:
                    for row in signal_rows:
                        cur.execute(UPSERT_SQL, row)
                conn.commit()

                found = sum(1 for r in signal_rows if r['signal_detected'])
                results['processed']    += 1
                results['signals_found'] += found
                if found:
                    log.info(f"  {job['job_id']} ({job['failure_category']}): "
                             f"{found} signal(s) detected")
            except Exception as exc:
                log.warning(f"  Error on job {job['job_id']}: {exc}")
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
            log.error(f'Correlation engine run failed: {exc}')
        time.sleep(RUN_INTERVAL)


if __name__ == '__main__':
    main()
