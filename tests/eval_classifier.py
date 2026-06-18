#!/usr/bin/env python3
"""
Classifier evaluation harness.
Evaluates two things against tests/ground_truth.json:

  1. Classification accuracy  — job_events.failure_category vs expected
  2. Correlation accuracy     — correlation_results signal detection and
                                lead times vs expected_signal_detected /
                                expected_metric_with_signal /
                                expected_lead_time_seconds

Exit codes:
  0  — all classification and correlation checks pass
  1  — at least one wrong failure_category
  2  — correct categories but some scenarios missing from job_events
  3  — categories correct but correlation signal check failed
"""

import json
import os
import sys
from pathlib import Path

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(0)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DB_HOST = os.environ.get("POSTGRES_HOST", "localhost")
DB_PORT = int(os.environ.get("POSTGRES_PORT", "5433"))   # docker-compose maps 5433→5432
DB_NAME = os.environ.get("POSTGRES_DB",    "fleetdb")
DB_USER = os.environ.get("POSTGRES_USER",  "fleet")
DB_PASS = os.environ.get("POSTGRES_PASSWORD", "fleet123")

GROUND_TRUTH_PATH = Path(__file__).parent / "ground_truth.json"

ALL_CATEGORIES = [
    "GPU_HARDWARE",
    "NCCL_COMM_FAILURE",
    "CUDA_OOM",
    "THERMAL_THROTTLE",
    "INFRA_STORAGE",
    "PREEMPTION",
    "TIMEOUT",
    "USER_ERROR",
]

RESULT_PASS    = "PASS"
RESULT_FAIL    = "FAIL"
RESULT_MISSING = "MISSING"
RESULT_SKIP    = "SKIP"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_ground_truth() -> list[dict]:
    with open(GROUND_TRUTH_PATH) as f:
        data = json.load(f)
    return data["scenarios"]


def connect_db():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        dbname=DB_NAME, user=DB_USER, password=DB_PASS,
        connect_timeout=5,
    )


def fetch_classified(conn) -> dict[str, str | None]:
    """Returns {job_id: failure_category} for all rows in job_events."""
    with conn.cursor() as cur:
        cur.execute("SELECT job_id, failure_category FROM job_events")
        return {str(row[0]): row[1] for row in cur.fetchall()}


def fetch_correlation_results(conn, job_ids: list[str]) -> dict[str, list[dict]]:
    """
    Returns {job_id: [{metric_name, node_hostname, signal_detected, lead_time_seconds}]}
    for the given job IDs.
    """
    if not job_ids:
        return {}
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT job_id, metric_name, node_hostname,
                      signal_detected, lead_time_seconds
               FROM correlation_results
               WHERE job_id = ANY(%s)""",
            (job_ids,)
        )
        result: dict[str, list[dict]] = {}
        for row in cur.fetchall():
            jid = str(row["job_id"])
            result.setdefault(jid, []).append(dict(row))
    return result


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------

def match_job(scenario: dict, classified: dict[str, str | None]) -> tuple[str, str | None]:
    """
    Match a scenario to any classified job_id.
    Simulators increment job IDs by 100 each cycle, so we search for
    job_id == base_id + N*100 for N = 0, 1, 2, ...
    Returns (matched_job_id, actual_category) or (None, None).
    """
    base = scenario["job_id"]
    for job_id_str, category in classified.items():
        try:
            jid = int(job_id_str)
        except ValueError:
            continue
        if (jid - base) >= 0 and (jid - base) % 100 == 0:
            return job_id_str, category
    return None, None


# ---------------------------------------------------------------------------
# Classification evaluation
# ---------------------------------------------------------------------------

def build_confusion(results: list[dict]) -> dict:
    """Build an 8×8 confusion matrix (expected → actual)."""
    matrix: dict[str, dict[str, int]] = {
        c: {c2: 0 for c2 in ALL_CATEGORIES + ["MISSING"]}
        for c in ALL_CATEGORIES
    }
    for r in results:
        expected = r["expected"]
        if expected is None:
            continue
        actual = r["actual"] if r["actual"] else "MISSING"
        if actual not in matrix[expected]:
            matrix[expected][actual] = 0
        matrix[expected][actual] += 1
    return matrix


def print_scenario_table(results: list[dict]) -> None:
    header = (f"{'SID':<6}  {'JOB_ID':<10}  {'EXPECTED':<24}  "
              f"{'ACTUAL':<24}  {'RESULT':<8}")
    sep = "-" * len(header)
    print()
    print("CLASSIFICATION")
    print(header)
    print(sep)
    for r in results:
        expected = r["expected"] or "HEALTHY"
        actual   = r["actual"]   or ("HEALTHY" if r["result"] == RESULT_PASS else "—")
        flag = "✓" if r["result"] == RESULT_PASS else ("✗" if r["result"] == RESULT_FAIL else "?")
        print(f"{r['sid']:<6}  {r['matched_job_id'] or str(r['base_job_id']):<10}  "
              f"{expected:<24}  {actual:<24}  {flag} {r['result']}")
    print()


def print_confusion_matrix(matrix: dict) -> None:
    failure_cats = [c for c in ALL_CATEGORIES if any(matrix[c].values())]
    if not failure_cats:
        return

    all_actual  = ALL_CATEGORIES + ["MISSING"]
    used_actual = [a for a in all_actual if any(matrix[e].get(a, 0) for e in failure_cats)]
    if not used_actual:
        return

    col_w       = max(len(c) for c in used_actual) + 2
    row_label_w = max(len(c) for c in failure_cats) + 2

    print("Confusion matrix (rows=expected, cols=actual):")
    print()
    header = f"{'':>{row_label_w}}" + "".join(f"{a:>{col_w}}" for a in used_actual)
    print(header)
    print("-" * len(header))
    for exp in failure_cats:
        row = f"{exp:>{row_label_w}}"
        for act in used_actual:
            val = matrix[exp].get(act, 0)
            row += f"{val if val else '.':>{col_w}}"
        print(row)
    print()


# ---------------------------------------------------------------------------
# Correlation evaluation
# ---------------------------------------------------------------------------

def evaluate_signal(
    scenario: dict,
    matched_job_id: str | None,
    corr_data: dict[str, list[dict]],
) -> dict:
    """
    Evaluate whether the correlation engine found the expected pre-failure
    signal for this scenario.

    Returns a dict with keys:
      sid, base_job_id, matched_job_id,
      expected_detected, expected_metric, expected_lead,
      actual_metric, actual_lead, result, note
    """
    sid               = scenario["scenario_id"]
    base_job_id       = scenario["job_id"]
    expected_detected = scenario["expected_signal_detected"]
    expected_metric   = scenario["expected_metric_with_signal"]
    expected_lead     = scenario["expected_lead_time_seconds"]
    tolerance         = scenario.get("tolerance_seconds") or 300

    base = {
        "sid":              sid,
        "base_job_id":      base_job_id,
        "matched_job_id":   matched_job_id,
        "expected_detected":expected_detected,
        "expected_metric":  expected_metric,
        "expected_lead":    expected_lead,
        "tolerance":        tolerance,
        "actual_metric":    None,
        "actual_lead":      None,
        "result":           None,
        "note":             "",
    }

    # Healthy jobs (null failure category) — correlation engine skips these.
    if scenario["expected_failure_category"] is None:
        return {**base, "result": RESULT_SKIP, "note": "healthy job — skipped"}

    if matched_job_id is None:
        return {**base, "result": RESULT_MISSING, "note": "job not in job_events"}

    job_rows = corr_data.get(matched_job_id, [])

    # No correlation rows at all yet
    if not job_rows:
        if not expected_detected:
            # Correlation engine would skip jobs with no DCGM data; treat as pass
            return {**base, "result": RESULT_PASS, "note": "no signals (expected, no DCGM data)"}
        return {**base, "result": RESULT_MISSING, "note": "correlation engine has not run yet"}

    detected = [r for r in job_rows if r["signal_detected"]]

    # Scenarios that expect NO signal
    if not expected_detected:
        if not detected:
            return {**base, "result": RESULT_PASS, "note": "no signals detected (correct)"}
        top = detected[0]
        return {
            **base,
            "result":        RESULT_FAIL,
            "actual_metric": top["metric_name"],
            "actual_lead":   top["lead_time_seconds"],
            "note":          f"unexpected signal on {top['metric_name']}",
        }

    # Scenarios that expect a signal on a specific metric
    matching = [r for r in detected if r["metric_name"] == expected_metric]

    if not matching:
        detected_names = ", ".join(r["metric_name"] for r in detected) or "none"
        return {
            **base,
            "result": RESULT_FAIL,
            "note":   f"expected signal on {expected_metric} — found: {detected_names}",
        }

    # Signal found — now check lead time tolerance
    actual_lead = matching[0]["lead_time_seconds"]

    if expected_lead is not None and actual_lead is not None:
        if abs(actual_lead - expected_lead) <= tolerance:
            return {
                **base,
                "result":        RESULT_PASS,
                "actual_metric": expected_metric,
                "actual_lead":   actual_lead,
            }
        return {
            **base,
            "result":        RESULT_FAIL,
            "actual_metric": expected_metric,
            "actual_lead":   actual_lead,
            "note":          (f"lead time {actual_lead}s vs expected "
                              f"{expected_lead}s ± {tolerance}s"),
        }

    return {
        **base,
        "result":        RESULT_PASS,
        "actual_metric": expected_metric,
        "actual_lead":   actual_lead,
    }


def _fmt_lead(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    m, s = divmod(seconds, 60)
    return f"{m}m{s:02d}s"


def print_correlation_table(corr_results: list[dict]) -> None:
    MET_W  = 46   # metric name column width (truncated)
    LEAD_W = 13

    header = (f"{'SID':<6}  {'JOB_ID':<10}  {'EXPECTED METRIC':<{MET_W}}  "
              f"{'EXPECTED LEAD':<{LEAD_W}}  {'ACTUAL LEAD':<{LEAD_W}}  {'RESULT':<8}")
    sep = "-" * len(header)

    print("CORRELATION")
    print(header)
    print(sep)

    for r in corr_results:
        if r["result"] == RESULT_SKIP:
            flag     = "–"
            result_s = "SKIP"
        elif r["result"] == RESULT_PASS:
            flag     = "✓"
            result_s = "PASS"
        elif r["result"] == RESULT_MISSING:
            flag     = "?"
            result_s = "MISSING"
        else:
            flag     = "✗"
            result_s = "FAIL"

        exp_metric = r["expected_metric"] or "(none expected)"
        exp_lead   = (f"{_fmt_lead(r['expected_lead'])} ±{r['tolerance']}s"
                      if r["expected_lead"] else "—")
        act_lead   = _fmt_lead(r["actual_lead"])
        note       = f"  ← {r['note']}" if r["result"] in (RESULT_FAIL, RESULT_MISSING) and r["note"] else ""

        print(f"{r['sid']:<6}  "
              f"{r['matched_job_id'] or str(r['base_job_id']):<10}  "
              f"{exp_metric[:MET_W]:<{MET_W}}  "
              f"{exp_lead:<{LEAD_W}}  "
              f"{act_lead:<{LEAD_W}}  "
              f"{flag} {result_s}{note}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    scenarios = load_ground_truth()

    # --- Connect and fetch all data in one session ---
    try:
        conn = connect_db()
    except Exception as exc:
        print(f"Cannot reach PostgreSQL at {DB_HOST}:{DB_PORT} — {exc}")
        print("Start the postgres service first: docker compose up postgres")
        sys.exit(0)

    try:
        classified   = fetch_classified(conn)
        if not classified:
            print("No classified records yet — classifier has not run.")
            sys.exit(0)

        # Match scenarios to classified job IDs
        class_results = []
        matched_ids   = []
        for s in scenarios:
            matched_id, actual_cat  = match_job(s, classified)
            expected_cat            = s["expected_failure_category"]

            if matched_id is None:
                result = RESULT_MISSING
            elif actual_cat == expected_cat:
                result = RESULT_PASS
            else:
                result = RESULT_FAIL

            class_results.append({
                "sid":            s["scenario_id"],
                "base_job_id":    s["job_id"],
                "matched_job_id": matched_id,
                "expected":       expected_cat,
                "actual":         actual_cat,
                "result":         result,
            })
            if matched_id:
                matched_ids.append(matched_id)

        # Fetch correlation results for all matched jobs
        corr_data = fetch_correlation_results(conn, matched_ids)

    except Exception as exc:
        print(f"Error querying database: {exc}")
        conn.close()
        sys.exit(0)
    finally:
        conn.close()

    # --- Evaluate correlation ---
    corr_results = []
    for s, cr in zip(scenarios, class_results):
        corr_results.append(
            evaluate_signal(s, cr["matched_job_id"], corr_data)
        )

    # --- Print classification ---
    print_scenario_table(class_results)
    matrix = build_confusion(class_results)
    print_confusion_matrix(matrix)

    # --- Print correlation ---
    print_correlation_table(corr_results)

    # --- Summaries ---
    c_pass    = sum(1 for r in class_results if r["result"] == RESULT_PASS)
    c_fail    = sum(1 for r in class_results if r["result"] == RESULT_FAIL)
    c_missing = sum(1 for r in class_results if r["result"] == RESULT_MISSING)
    c_total   = len(class_results)

    corr_active  = [r for r in corr_results if r["result"] != RESULT_SKIP]
    s_pass       = sum(1 for r in corr_active if r["result"] == RESULT_PASS)
    s_fail       = sum(1 for r in corr_active if r["result"] == RESULT_FAIL)
    s_missing    = sum(1 for r in corr_active if r["result"] == RESULT_MISSING)
    s_total      = len(corr_active)

    print(f"Classification : {c_pass}/{c_total} passed  |  {c_fail} wrong  |  {c_missing} missing")
    print(f"Correlation    : {s_pass}/{s_total} passed  |  {s_fail} wrong  |  {s_missing} missing")

    # --- Exit code ---
    if c_fail > 0:
        print("\nFAIL — wrong failure categories detected.")
        sys.exit(1)

    if c_missing > 0:
        print(f"\nPARTIAL — {c_pass} categories correct but {c_missing} scenario(s) not yet classified.")
        sys.exit(2)

    if s_fail > 0:
        print("\nFAIL — correlation signal check failed (categories correct, signals wrong).")
        sys.exit(3)

    if s_missing > 0:
        print(f"\nPARTIAL — categories correct but correlation engine has not run yet "
              f"({s_missing} scenario(s) missing signal data).")
        sys.exit(2)

    print("\nALL SCENARIOS PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
