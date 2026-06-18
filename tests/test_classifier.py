"""
Unit tests for classifier/classifier.py

Tests cover:
  - _best_category: priority ordering, empty input, unknown categories
  - _evidence_for_job: Tier-1 (job_id), Tier-2 (node+time), claimed-node exclusion,
    outside-window exclusion, orphan exclusion
  - _assign_orphan_evidence: nearest-job assignment, too-far exclusion
  - classify: all 8 failure categories, confidence levels, COMPLETED bypass,
    thermal throttle path, priority resolution when log and state hint conflict
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from classifier.log_parser import LogEvidence
from classifier.sacct_parser import SacctJob
from classifier.classifier import (
    PRIORITY,
    _assign_orphan_evidence,
    _best_category,
    _evidence_for_job,
    classify,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _dt(offset_minutes: int = 0) -> datetime:
    base = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(minutes=offset_minutes)


def _job(
    job_id: str = '100',
    state: str = 'FAILED',
    node_list: list[str] | None = None,
    start_offset: int = 0,
    end_offset: int = 60,
    elapsed: int = 3600,
) -> SacctJob:
    return SacctJob(
        job_id          = job_id,
        job_name        = f'job-{job_id}',
        user            = 'researcher',
        account         = 'ml',
        state           = state,
        exit_code       = '1:0',
        node_list_raw   = 'gpu01',
        node_list       = node_list or ['gpu01'],
        gpu_count       = 8,
        submit_time     = _dt(start_offset - 5),
        start_time      = _dt(start_offset),
        end_time        = _dt(end_offset),
        elapsed_seconds = elapsed,
        req_mem         = '64G',
        max_rss         = '',
        raw             = {},
    )


def _evidence(
    category: str,
    ts_offset: int = 30,
    job_id: str | None = None,
    node: str | None = None,
    detail: str | None = None,
    raw_line: str = 'some log line',
    source: str = 'slurmctld.log',
) -> LogEvidence:
    return LogEvidence(
        timestamp     = _dt(ts_offset),
        category_hint = category,
        source_file   = source,
        raw_line      = raw_line,
        job_id        = job_id,
        node          = node,
        detail        = detail,
    )


# ---------------------------------------------------------------------------
# _best_category
# ---------------------------------------------------------------------------

class TestBestCategory:

    def test_gpu_hardware_beats_all(self):
        assert _best_category(['UNKNOWN', 'TIMEOUT', 'GPU_HARDWARE']) == 'GPU_HARDWARE'

    def test_nccl_beats_cuda_oom(self):
        assert _best_category(['CUDA_OOM', 'NCCL_COMM_FAILURE']) == 'NCCL_COMM_FAILURE'

    def test_preemption_beats_timeout(self):
        assert _best_category(['TIMEOUT', 'PREEMPTION']) == 'PREEMPTION'

    def test_single_category(self):
        assert _best_category(['INFRA_STORAGE']) == 'INFRA_STORAGE'

    def test_empty_returns_none(self):
        assert _best_category([]) is None

    def test_unknown_category_ignored(self):
        assert _best_category(['NOT_A_REAL_CATEGORY']) is None

    def test_unknown_mixed_with_valid(self):
        assert _best_category(['NOT_REAL', 'TIMEOUT']) == 'TIMEOUT'

    def test_duplicates_handled(self):
        assert _best_category(['CUDA_OOM', 'CUDA_OOM']) == 'CUDA_OOM'

    def test_full_priority_order(self):
        for i, higher in enumerate(PRIORITY[:-1]):
            lower = PRIORITY[i + 1]
            assert _best_category([lower, higher]) == higher, \
                f'{higher} should beat {lower}'


# ---------------------------------------------------------------------------
# _evidence_for_job
# ---------------------------------------------------------------------------

class TestEvidenceForJob:

    def test_tier1_job_id_match(self):
        job = _job(job_id='100')
        ev  = _evidence('GPU_HARDWARE', job_id='100')
        matched = _evidence_for_job(job, [ev])
        assert ev in matched

    def test_tier1_wrong_job_id_excluded(self):
        job = _job(job_id='100')
        ev  = _evidence('GPU_HARDWARE', job_id='999')
        matched = _evidence_for_job(job, [ev])
        assert ev not in matched

    def test_tier2_node_and_time_window(self):
        job = _job(job_id='100', node_list=['gpu01'], start_offset=0, end_offset=60)
        ev  = _evidence('NCCL_COMM_FAILURE', node='gpu01', ts_offset=30)
        matched = _evidence_for_job(job, [ev])
        assert ev in matched

    def test_tier2_outside_time_window_excluded(self):
        job = _job(job_id='100', start_offset=0, end_offset=60)
        ev  = _evidence('CUDA_OOM', node='gpu01', ts_offset=90)  # after end
        matched = _evidence_for_job(job, [ev])
        assert ev not in matched

    def test_tier2_wrong_node_excluded(self):
        job = _job(job_id='100', node_list=['gpu01'])
        ev  = _evidence('GPU_HARDWARE', node='gpu99', ts_offset=30)
        matched = _evidence_for_job(job, [ev])
        assert ev not in matched

    def test_claimed_node_excluded_for_other_job(self):
        job_a = _job(job_id='100', node_list=['gpu01'], start_offset=0, end_offset=60)
        job_b = _job(job_id='200', node_list=['gpu01'], start_offset=0, end_offset=60)
        ev    = _evidence('GPU_HARDWARE', node='gpu01', ts_offset=30)
        claimed = {'gpu01': '100'}   # gpu01 already claimed by job 100
        matched = _evidence_for_job(job_b, [ev], claimed_nodes=claimed)
        assert ev not in matched

    def test_claimed_node_included_for_owning_job(self):
        job = _job(job_id='100', node_list=['gpu01'])
        ev  = _evidence('GPU_HARDWARE', node='gpu01', ts_offset=30)
        claimed = {'gpu01': '100'}
        matched = _evidence_for_job(job, [ev], claimed_nodes=claimed)
        assert ev in matched

    def test_orphan_evidence_not_included(self):
        # No job_id, no node → handled by _assign_orphan_evidence, not here
        job = _job(job_id='100')
        ev  = _evidence('INFRA_STORAGE', job_id=None, node=None)
        matched = _evidence_for_job(job, [ev])
        assert ev not in matched

    def test_no_start_time_skips_tier2(self):
        job = _job(job_id='100', node_list=['gpu01'])
        job = SacctJob(**{**job.__dict__, 'start_time': None, 'end_time': None})
        ev  = _evidence('GPU_HARDWARE', node='gpu01', ts_offset=30)
        matched = _evidence_for_job(job, [ev])
        assert ev not in matched

    def test_multiple_evidence_all_returned(self):
        job = _job(job_id='100', node_list=['gpu01', 'gpu02'])
        ev1 = _evidence('GPU_HARDWARE', job_id='100')
        ev2 = _evidence('NCCL_COMM_FAILURE', node='gpu01', ts_offset=30)
        matched = _evidence_for_job(job, [ev1, ev2])
        assert ev1 in matched
        assert ev2 in matched


# ---------------------------------------------------------------------------
# _assign_orphan_evidence
# ---------------------------------------------------------------------------

class TestAssignOrphanEvidence:

    def test_assigned_to_nearest_job(self):
        job_a = _job(job_id='100', end_offset=60)
        job_b = _job(job_id='200', end_offset=120)
        # orphan at T+62 — closer to job_b (end T+120 → delta 58)
        #                   than job_a (end T+60 → delta 2)? No, 2 < 58.
        orphan = _evidence('CUDA_OOM', job_id=None, node=None, ts_offset=62)
        result = _assign_orphan_evidence([job_a, job_b], [orphan])
        assert orphan in result['100']   # closest: delta=2 (job_a ends at 60min)

    def test_excluded_when_too_far(self):
        job = _job(job_id='100', end_offset=60)
        # orphan more than 600s (10 min) away from job end
        orphan = _evidence('CUDA_OOM', job_id=None, node=None, ts_offset=75)
        result = _assign_orphan_evidence([job], [orphan])
        assert result['100'] == []

    def test_non_orphan_evidence_ignored(self):
        job = _job(job_id='100', end_offset=60)
        ev  = _evidence('GPU_HARDWARE', job_id='100', ts_offset=30)  # has job_id → not orphan
        result = _assign_orphan_evidence([job], [ev])
        assert result['100'] == []

    def test_empty_evidence(self):
        job = _job(job_id='100')
        result = _assign_orphan_evidence([job], [])
        assert result == {'100': []}


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------

class TestClassify:

    def test_completed_job_returns_none(self):
        job = _job(state='COMPLETED')
        cat, conf, _ = classify(job, [])
        assert cat is None
        assert conf is None

    # — GPU_HARDWARE —

    def test_gpu_hardware_from_log_evidence(self):
        job = _job(state='FAILED')
        ev  = _evidence('GPU_HARDWARE')
        cat, conf, _ = classify(job, [ev])
        assert cat == 'GPU_HARDWARE'
        assert conf == 'HIGH'

    def test_gpu_hardware_from_node_fail_state(self):
        job = _job(state='NODE_FAIL')
        cat, conf, _ = classify(job, [])
        assert cat == 'GPU_HARDWARE'
        assert conf == 'MEDIUM'

    def test_gpu_hardware_log_plus_node_fail_is_high(self):
        job = _job(state='NODE_FAIL')
        ev  = _evidence('GPU_HARDWARE')
        cat, conf, _ = classify(job, [ev])
        assert cat == 'GPU_HARDWARE'
        assert conf == 'HIGH'

    # — NCCL_COMM_FAILURE —

    def test_nccl_from_log(self):
        job = _job(state='FAILED')
        ev  = _evidence('NCCL_COMM_FAILURE')
        cat, conf, _ = classify(job, [ev])
        assert cat == 'NCCL_COMM_FAILURE'
        assert conf == 'HIGH'

    # — CUDA_OOM —

    def test_cuda_oom_from_state(self):
        job = _job(state='OUT_OF_MEMORY')
        cat, conf, _ = classify(job, [])
        assert cat == 'CUDA_OOM'
        assert conf == 'MEDIUM'

    def test_cuda_oom_from_log(self):
        job = _job(state='FAILED')
        ev  = _evidence('CUDA_OOM')
        cat, conf, _ = classify(job, [ev])
        assert cat == 'CUDA_OOM'
        assert conf == 'HIGH'

    # — THERMAL_THROTTLE —

    def test_thermal_throttle_from_log(self):
        job = _job(state='FAILED')
        ev  = _evidence('THERMAL_THROTTLE')
        cat, conf, _ = classify(job, [ev])
        assert cat == 'THERMAL_THROTTLE'
        assert conf == 'HIGH'

    def test_thermal_throttle_from_prometheus(self):
        job = _job(state='FAILED')
        with patch('classifier.classifier._is_thermal_throttle', return_value=True):
            cat, conf, _ = classify(job, [])
        assert cat == 'THERMAL_THROTTLE'
        assert conf == 'MEDIUM'

    # — INFRA_STORAGE —

    def test_infra_storage_from_log(self):
        job = _job(state='FAILED')
        ev  = _evidence('INFRA_STORAGE')
        cat, conf, _ = classify(job, [ev])
        assert cat == 'INFRA_STORAGE'
        assert conf == 'HIGH'

    # — PREEMPTION —

    def test_preemption_from_state(self):
        job = _job(state='PREEMPTED')
        cat, conf, _ = classify(job, [])
        assert cat == 'PREEMPTION'
        assert conf == 'MEDIUM'

    # — TIMEOUT —

    def test_timeout_from_state(self):
        job = _job(state='TIMEOUT')
        cat, conf, _ = classify(job, [])
        assert cat == 'TIMEOUT'
        assert conf == 'MEDIUM'

    # — USER_ERROR —

    def test_user_error_from_log(self):
        job = _job(state='FAILED')
        ev  = _evidence('USER_ERROR')
        cat, conf, _ = classify(job, [ev])
        assert cat == 'USER_ERROR'
        assert conf == 'HIGH'

    # — UNKNOWN —

    def test_unknown_when_no_evidence_no_thermal(self):
        job = _job(state='FAILED')
        with patch('classifier.classifier._is_thermal_throttle', return_value=False):
            cat, conf, _ = classify(job, [])
        assert cat == 'UNKNOWN'
        assert conf == 'LOW'

    # — Priority resolution —

    def test_gpu_hardware_beats_nccl_in_log(self):
        job  = _job(state='FAILED')
        ev_g = _evidence('GPU_HARDWARE')
        ev_n = _evidence('NCCL_COMM_FAILURE')
        cat, _, _ = classify(job, [ev_g, ev_n])
        assert cat == 'GPU_HARDWARE'

    def test_gpu_hardware_beats_nccl_even_when_state_differs(self):
        # State hint says TIMEOUT, but log has both GPU_HARDWARE and NCCL
        job  = _job(state='TIMEOUT')
        ev_g = _evidence('GPU_HARDWARE')
        ev_n = _evidence('NCCL_COMM_FAILURE')
        cat, _, _ = classify(job, [ev_g, ev_n])
        assert cat == 'GPU_HARDWARE'

    def test_log_category_beats_state_hint_when_higher_priority(self):
        # State=NODE_FAIL (→ GPU_HARDWARE), log says GPU_HARDWARE → still GPU_HARDWARE, HIGH
        job = _job(state='NODE_FAIL')
        ev  = _evidence('GPU_HARDWARE')
        cat, conf, _ = classify(job, [ev])
        assert cat == 'GPU_HARDWARE'
        assert conf == 'HIGH'

    def test_patterns_returned(self):
        job = _job(state='FAILED')
        ev  = _evidence('CUDA_OOM', raw_line='CUDA out of memory: 20GB')
        _, _, patterns = classify(job, [ev])
        assert 'CUDA out of memory: 20GB' in patterns

    def test_confidence_low_when_state_hint_disagrees_with_log(self):
        # State hint → GPU_HARDWARE, but log says USER_ERROR (lower priority)
        # best = GPU_HARDWARE (from state hint), log_cats = ['USER_ERROR'] (doesn't include best)
        job = _job(state='NODE_FAIL')
        ev  = _evidence('USER_ERROR')
        _, conf, _ = classify(job, [ev])
        # best = GPU_HARDWARE (state hint); log_cats = ['USER_ERROR'] → best not in log_cats
        # state_hint == best → MEDIUM
        assert conf == 'MEDIUM'
