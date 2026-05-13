import pytest
from backend.scoring import (
    compute_incident_score, compute_freetext_score,
    compute_total_score, ESCALATION_CAP,
)


class TestEscalationScore:
    def test_positive_deltas_sum(self):
        decisions = [
            {"incident_idx": 1, "action_id": "check_grafana", "score_delta": 5},
            {"incident_idx": 1, "action_id": "reduce_concurrency", "score_delta": 30},
        ]
        score = compute_incident_score(decisions, incident_idx=1)
        assert score["escalation"] == 35

    def test_negative_delta_reduces_score(self):
        decisions = [
            {"incident_idx": 1, "action_id": "requeue_job", "score_delta": -10},
            {"incident_idx": 1, "action_id": "reduce_concurrency", "score_delta": 30},
        ]
        score = compute_incident_score(decisions, incident_idx=1)
        assert score["escalation"] == 20

    def test_escalation_floored_at_zero(self):
        decisions = [
            {"incident_idx": 1, "action_id": "bad1", "score_delta": -40},
            {"incident_idx": 1, "action_id": "bad2", "score_delta": -40},
        ]
        score = compute_incident_score(decisions, incident_idx=1)
        assert score["escalation"] == 0

    def test_escalation_capped_at_max(self):
        decisions = [
            {"incident_idx": 1, "action_id": "a", "score_delta": 50},
            {"incident_idx": 1, "action_id": "b", "score_delta": 50},
        ]
        score = compute_incident_score(decisions, incident_idx=1)
        assert score["escalation"] == ESCALATION_CAP  # 60


class TestRootCauseScore:
    def test_resolved_via_investigating_scores_30(self):
        decisions = [
            {"incident_idx": 1, "action_id": "check_grafana", "phase": "initial",
             "next_phase": "investigating"},
            {"incident_idx": 1, "action_id": "reduce_concurrency", "phase": "investigating",
             "next_phase": "resolved"},
        ]
        score = compute_incident_score(decisions, incident_idx=1)
        assert score["root_cause"] == 30

    def test_resolved_without_investigating_scores_15(self):
        decisions = [
            {"incident_idx": 1, "action_id": "lucky_guess", "phase": "initial",
             "next_phase": "resolved"},
        ]
        score = compute_incident_score(decisions, incident_idx=1)
        assert score["root_cause"] == 15

    def test_never_resolved_scores_0(self):
        decisions = [
            {"incident_idx": 1, "action_id": "bad1", "phase": "initial",
             "next_phase": None},
        ]
        score = compute_incident_score(decisions, incident_idx=1)
        assert score["root_cause"] == 0


class TestFreetextScore:
    def test_all_keyword_groups_matched(self):
        keywords = [
            ["4471", "run 4471"],
            ["throttl", "thermal"],
            ["concurren", "gpu-04"],
        ]
        text = "Job 4471 was thermal throttling on gpu-04, reduced concurrency."
        score = compute_freetext_score(text, keywords, max_points=10)
        assert score == 10

    def test_partial_match_scores_proportionally(self):
        keywords = [
            ["4471"],
            ["throttl"],
            ["concurren"],
        ]
        text = "job 4471 throttling"  # matches 2 of 3 groups
        score = compute_freetext_score(text, keywords, max_points=10)
        assert score == round((2 / 3) * 10)

    def test_empty_text_scores_zero(self):
        keywords = [["4471"], ["throttl"]]
        score = compute_freetext_score("", keywords, max_points=10)
        assert score == 0

    def test_matching_is_case_insensitive(self):
        keywords = [["THROTTL"]]
        score = compute_freetext_score("gpu throttling detected", keywords, max_points=10)
        assert score == 10

    def test_no_keywords_defined_scores_zero(self):
        score = compute_freetext_score("anything", [], max_points=10)
        assert score == 0


class TestTotalScore:
    def test_unscored_incident_excluded(self):
        decisions = [
            {"incident_idx": 0, "action_id": "x", "score_delta": 999},
            {"incident_idx": 1, "action_id": "a", "score_delta": 30,
             "phase": "initial", "next_phase": "resolved"},
        ]
        total = compute_total_score(decisions)
        assert total <= 100  # only incident 1 counted
