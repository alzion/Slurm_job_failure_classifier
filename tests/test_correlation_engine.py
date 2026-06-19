"""
Unit tests for classifier/correlation_engine.py

Tests cover _first_crossing, the core signal detection function, across
all four detection modes:

  gauge        — raw value crosses a threshold
  rate_per_hour— counter rate (counts/hr) crosses a threshold
  increment    — counter delta per step crosses a threshold
  pct_drop     — gauge drops by ≥ threshold fraction from its baseline

Each mode is tested for: crossing found, no crossing, edge cases.
STEP_S = 60 (1-minute Prometheus step) is used in rate calculations.
"""

import pytest

from classifier.correlation_engine import _first_crossing, _extract_gpu_index, STEP_S


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def crossing(result):
    """Return True if _first_crossing found a crossing (onset_ts is not None)."""
    onset, _, _, _ = result
    return onset is not None


def onset_ts(result):
    return result[0]

def baseline(result):
    return result[1]

def peak(result):
    return result[2]

def ratio(result):
    return result[3]


# ---------------------------------------------------------------------------
# _extract_gpu_index
# ---------------------------------------------------------------------------

class TestExtractGpuIndex:

    def test_standard_gpu_label(self):
        assert _extract_gpu_index({'hostname': 'gpu03', 'gpu': '3'}) == 3

    def test_gpu_zero(self):
        assert _extract_gpu_index({'gpu': '0'}) == 0

    def test_mig_gpu_instance_label(self):
        assert _extract_gpu_index({'GPU_I_ID': '2'}) == 2

    def test_configured_label_wins_over_default(self):
        import classifier.correlation_engine as ce
        original = ce.DCGM_GPU_IDX_LABEL
        try:
            ce.DCGM_GPU_IDX_LABEL = 'device'
            assert _extract_gpu_index({'device': '5', 'gpu': '0'}) == 5
        finally:
            ce.DCGM_GPU_IDX_LABEL = original

    def test_no_gpu_label_returns_none(self):
        assert _extract_gpu_index({'hostname': 'gpu03', '__name__': 'DCGM_FI_DEV_GPU_TEMP'}) is None

    def test_empty_labels_returns_none(self):
        assert _extract_gpu_index({}) is None

    def test_non_integer_label_skipped(self):
        assert _extract_gpu_index({'gpu': 'N/A'}) is None

    def test_integer_returned_not_string(self):
        result = _extract_gpu_index({'gpu': '7'})
        assert isinstance(result, int)
        assert result == 7


# ---------------------------------------------------------------------------
# gauge, direction='above'
# ---------------------------------------------------------------------------

class TestGaugeAbove:

    THRESHOLD = 82.0

    def _run(self, values):
        # timestamps: 1000, 1060, 1120, ...
        pairs = [(1000.0 + i * STEP_S, v) for i, v in enumerate(values)]
        return _first_crossing(pairs, 'gauge', self.THRESHOLD, 'above')

    def test_crossing_at_second_point(self):
        result = self._run([80.0, 83.0, 85.0])
        assert crossing(result)
        assert onset_ts(result) == pytest.approx(1060.0)

    def test_crossing_at_first_point(self):
        result = self._run([83.0, 80.0])
        assert crossing(result)
        assert onset_ts(result) == pytest.approx(1000.0)

    def test_no_crossing(self):
        result = self._run([80.0, 81.0, 79.0])
        assert not crossing(result)

    def test_exactly_at_threshold_is_not_a_crossing(self):
        result = self._run([80.0, 82.0])   # == threshold, not >
        assert not crossing(result)

    def test_baseline_is_first_value(self):
        result = self._run([75.0, 90.0])
        assert baseline(result) == pytest.approx(75.0)

    def test_peak_is_maximum(self):
        result = self._run([70.0, 85.0, 90.0, 83.0])
        assert peak(result) == pytest.approx(90.0)

    def test_anomaly_ratio(self):
        result = self._run([70.0, 82.1])
        assert ratio(result) == pytest.approx(82.1 / 82.0, rel=1e-4)

    def test_empty_series(self):
        result = _first_crossing([], 'gauge', self.THRESHOLD, 'above')
        assert not crossing(result)
        assert baseline(result) == 0.0
        assert peak(result) == 0.0
        assert ratio(result) == 0.0

    def test_single_point_crossing(self):
        result = _first_crossing([(1000.0, 90.0)], 'gauge', self.THRESHOLD, 'above')
        assert crossing(result)


# ---------------------------------------------------------------------------
# rate_per_hour, direction='above'
# STEP_S = 60 → rate = delta / (60 / 3600) = delta * 60  counts/hr
# ---------------------------------------------------------------------------

class TestRatePerHour:

    THRESHOLD = 30.0   # 30 ECC SBE errors per hour

    def _pairs(self, values):
        return [(1000.0 + i * STEP_S, v) for i, v in enumerate(values)]

    def _run(self, values):
        return _first_crossing(self._pairs(values), 'rate_per_hour', self.THRESHOLD, 'above')

    def test_rate_exceeds_threshold(self):
        # delta=1 per step → rate = 1 * 60 = 60/hr > 30 → crossing at second point
        result = self._run([0, 1, 2])
        assert crossing(result)
        assert onset_ts(result) == pytest.approx(1060.0)

    def test_rate_below_threshold(self):
        # delta=0.1 per step → rate = 6/hr < 30 → no crossing
        result = self._run([0.0, 0.1, 0.2, 0.3])
        assert not crossing(result)

    def test_rate_exactly_at_threshold_is_not_crossing(self):
        # delta = 0.5 → rate = 30.0 exactly → not > threshold
        result = self._run([0.0, 0.5])
        assert not crossing(result)

    def test_counter_reset_ignored(self):
        # Counter drops (reset) then rises slowly — reset step should not count
        result = self._run([10.0, 5.0, 5.1, 5.2])   # drop from 10→5 is a reset
        assert not crossing(result)   # subsequent deltas are 0.1, well below threshold

    def test_single_large_jump_triggers(self):
        # Large jump in one step
        result = self._run([0, 100])
        assert crossing(result)

    def test_onset_is_first_step_that_crosses(self):
        # First step: delta=0.1 (6/hr, no cross). Second: delta=1 (60/hr, cross).
        result = self._run([0, 0.1, 1.1])
        assert onset_ts(result) == pytest.approx(1120.0)   # third point

    def test_empty_series(self):
        result = _first_crossing([], 'rate_per_hour', self.THRESHOLD, 'above')
        assert not crossing(result)


# ---------------------------------------------------------------------------
# increment, direction='above'
# ---------------------------------------------------------------------------

class TestIncrement:

    THRESHOLD = 0.5

    def _run(self, values):
        pairs = [(1000.0 + i * STEP_S, v) for i, v in enumerate(values)]
        return _first_crossing(pairs, 'increment', self.THRESHOLD, 'above')

    def test_increment_exceeds_threshold(self):
        result = self._run([0, 1])
        assert crossing(result)
        assert onset_ts(result) == pytest.approx(1060.0)

    def test_increment_below_threshold(self):
        result = self._run([0, 0.3])
        assert not crossing(result)

    def test_zero_threshold_any_increment_triggers(self):
        result = _first_crossing(
            [(1000.0, 0.0), (1060.0, 0.001)], 'increment', 0.0, 'above'
        )
        assert crossing(result)

    def test_no_change_no_crossing(self):
        result = self._run([5.0, 5.0, 5.0])
        assert not crossing(result)

    def test_decrement_ignored(self):
        result = self._run([10.0, 5.0])  # delta = max(0, 5-10) = 0
        assert not crossing(result)

    def test_empty_series(self):
        result = _first_crossing([], 'increment', self.THRESHOLD, 'above')
        assert not crossing(result)

    def test_onset_at_correct_step(self):
        # First step delta=0.3 (below), second delta=0.8 (above)
        result = self._run([0, 0.3, 1.1])
        assert onset_ts(result) == pytest.approx(1120.0)


# ---------------------------------------------------------------------------
# pct_drop, direction='below'
# ---------------------------------------------------------------------------

class TestPctDrop:

    THRESHOLD = 0.15   # 15% drop

    def _run(self, values):
        pairs = [(1000.0 + i * STEP_S, v) for i, v in enumerate(values)]
        return _first_crossing(pairs, 'pct_drop', self.THRESHOLD, 'below')

    def test_drop_exceeds_threshold(self):
        # 1000 → 800: 20% drop > 15%
        result = self._run([1000.0, 800.0])
        assert crossing(result)
        assert onset_ts(result) == pytest.approx(1060.0)

    def test_drop_below_threshold(self):
        # 1000 → 900: 10% drop < 15%
        result = self._run([1000.0, 900.0])
        assert not crossing(result)

    def test_exactly_at_threshold_is_not_crossing(self):
        # 1000 → 850: exactly 15% drop → not > threshold
        result = self._run([1000.0, 850.0])
        assert not crossing(result)

    def test_zero_baseline_no_crossing(self):
        # Division by zero guard: baseline=0 → return None
        result = _first_crossing([(1000.0, 0.0), (1060.0, 0.0)], 'pct_drop', self.THRESHOLD, 'below')
        assert not crossing(result)

    def test_baseline_is_first_value(self):
        result = self._run([1200.0, 900.0])
        assert baseline(result) == pytest.approx(1200.0)

    def test_peak_drop_at_crossing_step(self):
        # _first_crossing returns at the first crossing; the reported peak is the
        # lowest value seen UP TO that step, not the eventual minimum.
        # 1000 → 700 (20% drop, crosses 15% threshold at step 2).
        # At that step peak_drop_v = 700.  The subsequent 600 is never reached.
        result = self._run([1000.0, 700.0, 600.0, 700.0])
        assert onset_ts(result) == pytest.approx(1060.0)   # crossing at step 2
        assert peak(result) == pytest.approx(700.0)        # value at crossing step

    def test_empty_series(self):
        result = _first_crossing([], 'pct_drop', self.THRESHOLD, 'below')
        assert not crossing(result)

    def test_gradual_drop_onset_at_first_crossing(self):
        # 1000 → 920 (8%, no) → 830 (17%, yes)
        result = self._run([1000.0, 920.0, 830.0])
        assert onset_ts(result) == pytest.approx(1120.0)


# ---------------------------------------------------------------------------
# Cross-cutting: return value structure
# ---------------------------------------------------------------------------

class TestReturnValueStructure:

    def test_returns_four_tuple(self):
        result = _first_crossing([(1000.0, 90.0)], 'gauge', 82.0, 'above')
        assert len(result) == 4

    def test_onset_is_float_when_crossing_found(self):
        onset, _, _, _ = _first_crossing(
            [(1000.0, 85.0)], 'gauge', 82.0, 'above'
        )
        assert isinstance(onset, float)

    def test_onset_is_none_when_no_crossing(self):
        onset, _, _, _ = _first_crossing(
            [(1000.0, 80.0)], 'gauge', 82.0, 'above'
        )
        assert onset is None

    def test_ratio_positive_when_crossing(self):
        _, _, _, r = _first_crossing(
            [(1000.0, 0.0), (1060.0, 1.0)], 'increment', 0.5, 'above'
        )
        assert r > 0
