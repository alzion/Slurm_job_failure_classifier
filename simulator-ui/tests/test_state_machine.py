import pytest
from backend.incidents import load_scenario, get_phase, apply_action


class TestPhaseTransition:
    def test_valid_action_advances_phase(self, thermal_scenario):
        result = apply_action(thermal_scenario, current_phase="initial",
                              action_id="check_grafana")
        assert result["next_phase"] == "investigating"

    def test_null_next_phase_stays_in_phase(self, thermal_scenario):
        result = apply_action(thermal_scenario, current_phase="initial",
                              action_id="requeue_job")
        assert result["next_phase"] is None

    def test_null_phase_returns_same_actions(self, thermal_scenario):
        result = apply_action(thermal_scenario, current_phase="initial",
                              action_id="requeue_job")
        phase = get_phase(thermal_scenario, "initial")
        assert set(a["id"] for a in result["available_actions"]) == \
               set(a["id"] for a in phase["available_actions"])

    def test_invalid_action_raises(self, thermal_scenario):
        with pytest.raises(ValueError, match="invalid action"):
            apply_action(thermal_scenario, current_phase="initial",
                         action_id="nonexistent_action")

    def test_consequence_always_returned(self, thermal_scenario):
        result = apply_action(thermal_scenario, current_phase="initial",
                              action_id="check_grafana")
        assert isinstance(result["consequence"], str)
        assert len(result["consequence"]) > 0

    def test_slack_messages_ordered(self, thermal_scenario):
        phase = get_phase(thermal_scenario, "resolved")
        orders = [m["order"] for m in phase["slack_messages"]]
        assert orders == sorted(orders)

    def test_requires_freetext_phase_has_prompt(self, thermal_scenario):
        phase = get_phase(thermal_scenario, "resolved")
        assert phase["requires_freetext"] is True
        assert len(phase["freetext_prompt"]) > 0

    def test_orientation_has_no_actions(self):
        scenario = load_scenario("00_orientation")
        for phase in scenario["phases"]:
            assert "available_actions" not in phase or \
                   len(phase["available_actions"]) == 0

    def test_all_next_phases_exist(self, thermal_scenario):
        """No action references a phase_id that doesn't exist in the scenario."""
        phase_ids = {p["id"] for p in thermal_scenario["phases"]}
        for phase in thermal_scenario["phases"]:
            for action in phase.get("available_actions", []):
                if action["next_phase"] is not None:
                    assert action["next_phase"] in phase_ids, \
                        f"Action {action['id']} references unknown phase {action['next_phase']}"
