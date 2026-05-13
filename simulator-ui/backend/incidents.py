"""State machine engine — generic, no incident-specific logic."""
import os
import yaml

SCENARIO_DIR = os.environ.get("SCENARIO_DIR", os.path.join(os.path.dirname(__file__), "scenarios"))


def load_scenario(name: str) -> dict:
    path = os.path.join(SCENARIO_DIR, f"{name}.yaml")
    with open(path) as f:
        return yaml.safe_load(f)


def load_characters() -> dict:
    path = os.path.join(SCENARIO_DIR, "characters.yaml")
    with open(path) as f:
        return yaml.safe_load(f)["characters"]


def get_phase(scenario: dict, phase_id: str) -> dict:
    for phase in scenario["phases"]:
        if phase["id"] == phase_id:
            return phase
    raise KeyError(f"Phase '{phase_id}' not found in scenario '{scenario['id']}'")


def apply_action(scenario: dict, current_phase: str, action_id: str) -> dict:
    phase = get_phase(scenario, current_phase)
    actions = phase.get("available_actions", [])
    action = next((a for a in actions if a["id"] == action_id), None)
    if action is None:
        raise ValueError(f"invalid action '{action_id}' for phase '{current_phase}'")

    next_phase_id = action.get("next_phase")
    if next_phase_id:
        next_phase = get_phase(scenario, next_phase_id)
    else:
        next_phase = phase

    characters = load_characters()
    enriched_messages = []
    for msg in next_phase.get("slack_messages", []):
        char_key = msg["from"]
        char = characters.get(char_key, {})
        enriched_messages.append({
            "from": char_key,
            "display_name": char.get("display_name", char_key),
            "avatar": char.get("avatar", char_key[0].upper()),
            "order": msg.get("order", 0),
            "text": msg["text"],
        })
    enriched_messages.sort(key=lambda m: m["order"])

    return {
        "consequence": action["consequence"],
        "score_delta": action.get("score_delta", 0),
        "next_phase": next_phase_id,
        "available_actions": next_phase.get("available_actions", []),
        "slack_messages": enriched_messages,
        "grafana_dashboard": next_phase.get("grafana_dashboard", ""),
        "log_file": next_phase.get("log_file", ""),
        "requires_freetext": next_phase.get("requires_freetext", False),
        "freetext_prompt": next_phase.get("freetext_prompt", ""),
    }


SCENARIO_ORDER = [
    "00_orientation",
    "01_cuda_oom",
    "02_thermal_throttle",
    "03_nccl_failure",
    "04_xid_error",
    "05_cascading_failure",
]


def get_scenario_by_idx(idx: int) -> dict:
    return load_scenario(SCENARIO_ORDER[idx])


def scenario_count() -> int:
    return len(SCENARIO_ORDER)
