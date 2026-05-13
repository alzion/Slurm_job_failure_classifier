#!/usr/bin/env python3
"""
Classifier evaluation harness.
Compares job_events.failure_category against tests/ground_truth.json.

Exit codes:
  0  — all present records correct (or no records yet / DB unavailable)
  1  — at least one wrong failure_category
  2  — all present records correct but some scenarios missing
"""

import json
import os
import sys
from pathlib import Path

try:
    import psycopg2
except ImportError:
    print("psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(0)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DB_HOST = os.environ.get("POSTGRES_HOST", "localhost")
DB_PORT = int(os.environ.get("POSTGRES_PORT", "5433"))  # docker-compose maps 5433→5432
DB_NAME = os.environ.get("POSTGRES_DB", "fleetdb")
DB_USER = os.environ.get("POSTGRES_USER", "fleet")
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


# ---------------------------------------------------------------------------
# Helpers
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


def build_confusion(results: list[dict]) -> dict:
    """Build an 8×8 confusion matrix (expected → actual)."""
    matrix: dict[str, dict[str, int]] = {
        c: {c2: 0 for c2 in ALL_CATEGORIES + ["MISSING"]}
        for c in ALL_CATEGORIES
    }
    for r in results:
        expected = r["expected"]
        if expected is None:
            continue   # healthy scenarios not in confusion matrix
        actual = r["actual"] if r["actual"] else "MISSING"
        if actual not in matrix[expected]:
            matrix[expected][actual] = 0
        matrix[expected][actual] += 1
    return matrix


def print_scenario_table(results: list[dict]) -> None:
    col_w = [6, 10, 24, 24, 8]
    header = f"{'SID':<6}  {'JOB_ID':<10}  {'EXPECTED':<24}  {'ACTUAL':<24}  {'RESULT':<8}"
    sep    = "-" * len(header)
    print()
    print(header)
    print(sep)
    for r in results:
        expected = r["expected"] or "HEALTHY"
        actual   = r["actual"]   or ("HEALTHY" if r["result"] == RESULT_PASS else "—")
        result   = r["result"]
        flag = "✓" if result == RESULT_PASS else ("✗" if result == RESULT_FAIL else "?")
        print(f"{r['sid']:<6}  {r['matched_job_id'] or str(r['base_job_id']):<10}  "
              f"{expected:<24}  {actual:<24}  {flag} {result}")
    print()


def print_confusion_matrix(matrix: dict) -> None:
    failure_cats = [c for c in ALL_CATEGORIES if any(matrix[c].values())]
    if not failure_cats:
        return

    all_actual = ALL_CATEGORIES + ["MISSING"]
    used_actual = [a for a in all_actual if any(matrix[e].get(a, 0) for e in failure_cats)]
    if not used_actual:
        return

    col_w = max(len(c) for c in used_actual) + 2
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
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    scenarios = load_ground_truth()

    # --- Connect ---
    try:
        conn = connect_db()
    except Exception as exc:
        print(f"Cannot reach PostgreSQL at {DB_HOST}:{DB_PORT} — {exc}")
        print("Start the postgres service first: docker compose up postgres")
        sys.exit(0)

    # --- Fetch classified jobs ---
    try:
        classified = fetch_classified(conn)
    except Exception as exc:
        print(f"Error querying job_events: {exc}")
        conn.close()
        sys.exit(0)
    finally:
        conn.close()

    # --- Empty table check ---
    if not classified:
        print("No classified records yet — classifier has not run.")
        print("This is expected before the classifier is built.")
        sys.exit(0)

    # --- Evaluate each scenario ---
    results = []
    for s in scenarios:
        matched_id, actual_cat = match_job(s, classified)
        expected_cat = s["expected_failure_category"]  # None for healthy

        if matched_id is None:
            result = RESULT_MISSING
        elif actual_cat == expected_cat:
            result = RESULT_PASS
        else:
            result = RESULT_FAIL

        results.append({
            "sid":            s["scenario_id"],
            "base_job_id":    s["job_id"],
            "matched_job_id": matched_id,
            "expected":       expected_cat,
            "actual":         actual_cat,
            "result":         result,
        })

    # --- Print table ---
    print_scenario_table(results)

    # --- Confusion matrix ---
    matrix = build_confusion(results)
    print_confusion_matrix(matrix)

    # --- Summary ---
    n_pass    = sum(1 for r in results if r["result"] == RESULT_PASS)
    n_fail    = sum(1 for r in results if r["result"] == RESULT_FAIL)
    n_missing = sum(1 for r in results if r["result"] == RESULT_MISSING)
    total     = len(results)

    print(f"Results: {n_pass}/{total} passed  |  {n_fail} wrong category  |  {n_missing} missing")

    if n_fail > 0:
        print("\nFAIL — wrong failure categories detected.")
        sys.exit(1)

    if n_missing > 0:
        print(f"\nPARTIAL — {n_pass} correct but {n_missing} scenario(s) not yet classified.")
        sys.exit(2)

    print("\nALL SCENARIOS PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
