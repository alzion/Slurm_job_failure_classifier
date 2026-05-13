ESCALATION_CAP = 60
ROOT_CAUSE_MAX = 30
COMMUNICATION_MAX = 10


def compute_incident_score(decisions: list, incident_idx: int) -> dict:
    inc_decisions = [d for d in decisions if d.get("incident_idx") == incident_idx]

    # Escalation: sum of score_deltas, clamped to [0, ESCALATION_CAP]
    escalation = sum(d.get("score_delta", 0) for d in inc_decisions)
    escalation = max(0, min(escalation, ESCALATION_CAP))

    # Root cause: based on phase transitions
    phases_visited = set()
    reached_resolved = False
    for d in inc_decisions:
        if d.get("phase"):
            phases_visited.add(d["phase"])
        if d.get("next_phase") == "resolved":
            reached_resolved = True
        if d.get("next_phase"):
            phases_visited.add(d["next_phase"])

    if reached_resolved:
        if "investigating" in phases_visited:
            root_cause = ROOT_CAUSE_MAX
        else:
            root_cause = ROOT_CAUSE_MAX // 2
    else:
        root_cause = 0

    # Communication: from freetext decisions
    freetext_decisions = [d for d in inc_decisions if d.get("action_id") == "__freetext__"]
    communication = sum(d.get("score_delta", 0) for d in freetext_decisions)
    communication = max(0, min(communication, COMMUNICATION_MAX))

    return {
        "root_cause": root_cause,
        "escalation": escalation,
        "communication": communication,
        "total": root_cause + escalation + communication,
    }


def compute_freetext_score(text: str, keywords: list, max_points: int) -> int:
    if not keywords or not text:
        return 0

    text_lower = text.lower()
    matched_groups = 0
    for group in keywords:
        if any(kw.lower() in text_lower for kw in group):
            matched_groups += 1

    if matched_groups == 0:
        return 0

    return round((matched_groups / len(keywords)) * max_points)


def compute_freetext_matched(text: str, keywords: list) -> list:
    text_lower = text.lower()
    matched = []
    for group in keywords:
        for kw in group:
            if kw.lower() in text_lower:
                matched.append(group[0])
                break
    return matched


def compute_total_score(decisions: list, scored_indices: list = None) -> int:
    if scored_indices is None:
        scored_indices = list(range(1, 6))  # incidents 1–5

    total = 0
    for idx in scored_indices:
        score = compute_incident_score(decisions, idx)
        total += score["total"]
    return total
