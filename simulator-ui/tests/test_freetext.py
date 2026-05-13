"""
Integration: submit freetext on a session that is in a requires_freetext phase.
Tests the full round-trip: action → resolved phase → freetext → score delta returned.
"""
import pytest
from backend.incidents import load_scenario, get_phase


class TestFreetextRoundTrip:
    def _advance_to_freetext_phase(self, client, sid):
        """Drive incident 2 to its resolved phase via the happy path."""
        client.post(f"/api/v1/sessions/{sid}/next")           # past orientation → incident 1
        client.post(f"/api/v1/sessions/{sid}/next")           # incident 1 → incident 2
        client.post(f"/api/v1/sessions/{sid}/action",
                    json={"action_id": "check_grafana"})       # initial → investigating
        client.post(f"/api/v1/sessions/{sid}/action",
                    json={"action_id": "reduce_concurrency"})  # investigating → resolved

    def test_good_update_scores_higher_than_vague_update(self, client, session_factory):
        sid_good = session_factory(email="good@test.com")
        sid_vague = session_factory(email="vague@test.com")

        self._advance_to_freetext_phase(client, sid_good)
        self._advance_to_freetext_phase(client, sid_vague)

        good = client.post(f"/api/v1/sessions/{sid_good}/freetext",
            json={"text": "Job 4471 was thermal throttling on gpu-04. "
                          "Reduced concurrency, temp back to 76C."})
        vague = client.post(f"/api/v1/sessions/{sid_vague}/freetext",
            json={"text": "I looked at the dashboard and fixed it."})

        assert good.json()["score_delta"] > vague.json()["score_delta"]

    def test_matched_keywords_returned_in_response(self, client, session_factory):
        sid = session_factory()
        self._advance_to_freetext_phase(client, sid)
        r = client.post(f"/api/v1/sessions/{sid}/freetext",
            json={"text": "thermal throttle on gpu-04 fixed"})
        assert "matched_keywords" in r.json()
        assert len(r.json()["matched_keywords"]) > 0

    def test_freetext_score_included_in_total(self, client, session_factory):
        sid = session_factory()
        self._advance_to_freetext_phase(client, sid)
        client.post(f"/api/v1/sessions/{sid}/freetext",
            json={"text": "Job 4471 thermal throttling on gpu-04, reduced concurrency."})
        score = client.get(f"/api/v1/sessions/{sid}/score").json()
        inc2 = next(i for i in score["by_incident"] if i["incident_idx"] == 2)
        assert inc2["communication"] > 0


class TestYAMLIntegrity:
    """Validate all scenario files load without errors and satisfy schema contracts."""

    def test_all_scenarios_load(self):
        from backend.incidents import load_scenario
        for fname in ["00_orientation", "01_cuda_oom", "02_thermal_throttle",
                      "03_nccl_failure", "04_xid_error", "05_cascading_failure"]:
            s = load_scenario(fname)
            assert s["id"] is not None

    def test_scored_field_present_on_all(self):
        from backend.incidents import load_scenario
        for fname in ["00_orientation", "01_cuda_oom", "02_thermal_throttle",
                      "03_nccl_failure", "04_xid_error", "05_cascading_failure"]:
            s = load_scenario(fname)
            assert "scored" in s

    def test_orientation_is_not_scored(self):
        from backend.incidents import load_scenario
        s = load_scenario("00_orientation")
        assert s["scored"] is False

    def test_all_log_files_exist(self):
        import os
        from backend.incidents import load_scenario, SCENARIO_DIR
        for fname in ["01_cuda_oom", "02_thermal_throttle", "03_nccl_failure",
                      "04_xid_error", "05_cascading_failure"]:
            s = load_scenario(fname)
            for phase in s["phases"]:
                if "log_file" in phase:
                    path = os.path.join(SCENARIO_DIR, "logs", phase["log_file"])
                    assert os.path.exists(path), f"Missing log file: {phase['log_file']}"

    def test_all_character_refs_valid(self):
        import yaml, os
        from backend.incidents import load_scenario, SCENARIO_DIR
        with open(os.path.join(SCENARIO_DIR, "characters.yaml")) as f:
            chars = yaml.safe_load(f)["characters"]
        for fname in ["01_cuda_oom", "02_thermal_throttle", "03_nccl_failure",
                      "04_xid_error", "05_cascading_failure"]:
            s = load_scenario(fname)
            for phase in s["phases"]:
                for msg in phase.get("slack_messages", []):
                    assert msg["from"] in chars, \
                        f"Unknown character '{msg['from']}' in {fname}"
