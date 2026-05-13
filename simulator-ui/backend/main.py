import os
import pathlib
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Depends, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from .models import init_db, get_db
from .session import create_session, get_session, update_session, append_decision
from .incidents import (
    get_scenario_by_idx, get_phase, apply_action,
    scenario_count, load_scenario, SCENARIO_ORDER,
)
from .scoring import (
    compute_incident_score, compute_freetext_score,
    compute_freetext_matched, compute_total_score, COMMUNICATION_MAX,
)

SCENARIO_DIR = os.environ.get("SCENARIO_DIR", os.path.join(os.path.dirname(__file__), "scenarios"))
LOG_DIR = os.path.join(SCENARIO_DIR, "logs")


@asynccontextmanager
async def lifespan(app):
    init_db()
    yield


app = FastAPI(title="AI Infra TPM Simulator", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ActionRequest(BaseModel):
    action_id: str


class FreetextRequest(BaseModel):
    text: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _session_state(session, scenario):
    """Build the full state dict returned to the client."""
    phase = get_phase(scenario, session.phase_id)
    return {
        "session_id": session.id,
        "incident_idx": session.incident_idx,
        "incident_id": scenario["id"],
        "incident_title": scenario["title"],
        "incident_scored": scenario.get("scored", True),
        "phase_id": session.phase_id,
        "slack_messages": _enrich_messages(phase.get("slack_messages", [])),
        "grafana_dashboard": phase.get("grafana_dashboard", ""),
        "log_file": phase.get("log_file", ""),
        "available_actions": phase.get("available_actions", []),
        "requires_freetext": phase.get("requires_freetext", False),
        "freetext_prompt": phase.get("freetext_prompt", ""),
        "completed": session.completed,
        "narrative_intro": scenario.get("narrative", {}).get("intro", ""),
        "total_incidents": scenario_count(),
        "debrief": scenario.get("debrief", {}),
        "concept_card": scenario.get("concept_card", {}),
    }


def _enrich_messages(messages):
    from .incidents import load_characters
    chars = load_characters()
    result = []
    for msg in messages:
        char = chars.get(msg["from"], {})
        result.append({
            "from": msg["from"],
            "display_name": char.get("display_name", msg["from"]),
            "avatar": char.get("avatar", msg["from"][0].upper()),
            "order": msg.get("order", 0),
            "text": msg["text"],
        })
    result.sort(key=lambda m: m["order"])
    return result


def _require_email(x_auth_request_email: str = Header(default=None)) -> str:
    # In production, Caddy injects this header via forward_auth.
    # Fall back to a local-dev identity when running without the proxy.
    return x_auth_request_email or "local@dev"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/v1/sessions")
def create_new_session(
    email: str = Depends(_require_email),
    db: DBSession = Depends(get_db),
):
    session = create_session(db, email)
    scenario = get_scenario_by_idx(0)
    return _session_state(session, scenario)


@app.get("/api/v1/sessions/{session_id}")
def resume_session(
    session_id: str,
    db: DBSession = Depends(get_db),
):
    session = get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    scenario = get_scenario_by_idx(session.incident_idx)
    return _session_state(session, scenario)


@app.post("/api/v1/sessions/{session_id}/action")
def take_action(
    session_id: str,
    body: ActionRequest,
    db: DBSession = Depends(get_db),
):
    session = get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.completed:
        raise HTTPException(status_code=400, detail="Session already completed")

    scenario = get_scenario_by_idx(session.incident_idx)

    try:
        result = apply_action(scenario, session.phase_id, body.action_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    new_phase_id = result["next_phase"] if result["next_phase"] else session.phase_id

    decision = {
        "incident_idx": session.incident_idx,
        "action_id": body.action_id,
        "score_delta": result["score_delta"],
        "phase": session.phase_id,
        "next_phase": result["next_phase"],
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    append_decision(db, session, decision)
    update_session(db, session, phase_id=new_phase_id)

    return {
        "consequence": result["consequence"],
        "score_delta": result["score_delta"],
        "next_phase": result["next_phase"],
        "slack_messages": result["slack_messages"],
        "grafana_dashboard": result["grafana_dashboard"],
        "log_file": result["log_file"],
        "requires_freetext": result["requires_freetext"],
        "freetext_prompt": result["freetext_prompt"],
        "available_actions": result["available_actions"],
    }


@app.post("/api/v1/sessions/{session_id}/freetext")
def submit_freetext(
    session_id: str,
    body: FreetextRequest,
    db: DBSession = Depends(get_db),
):
    session = get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not body.text or not body.text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty")

    scenario = get_scenario_by_idx(session.incident_idx)
    phase = get_phase(scenario, session.phase_id)

    if not phase.get("requires_freetext", False):
        raise HTTPException(status_code=422, detail="Current phase does not require freetext")

    keywords = phase.get("freetext_keywords", [])
    score_delta = compute_freetext_score(body.text, keywords, max_points=COMMUNICATION_MAX)
    matched = compute_freetext_matched(body.text, keywords)

    decision = {
        "incident_idx": session.incident_idx,
        "action_id": "__freetext__",
        "score_delta": score_delta,
        "phase": session.phase_id,
        "next_phase": None,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    append_decision(db, session, decision)

    return {
        "score_delta": score_delta,
        "matched_keywords": matched,
    }


@app.post("/api/v1/sessions/{session_id}/next")
def advance_to_next_incident(
    session_id: str,
    db: DBSession = Depends(get_db),
):
    session = get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    next_idx = session.incident_idx + 1
    if next_idx >= scenario_count():
        update_session(db, session, completed=True)
        return {"completed": True}

    scenario = get_scenario_by_idx(next_idx)
    update_session(db, session, incident_idx=next_idx, phase_id="initial")
    return _session_state(session, scenario)


@app.get("/api/v1/sessions/{session_id}/score")
def get_score(
    session_id: str,
    db: DBSession = Depends(get_db),
):
    session = get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    decisions = session.decisions or []
    by_incident = []
    for idx in range(1, scenario_count()):
        scenario = get_scenario_by_idx(idx)
        if not scenario.get("scored", True):
            continue
        score = compute_incident_score(decisions, idx)
        by_incident.append({
            "incident_idx": idx,
            "incident_id": scenario["id"],
            "incident_title": scenario["title"],
            **score,
        })

    total = compute_total_score(decisions)
    return {"by_incident": by_incident, "total": total}


@app.get("/api/v1/logs/{filename}")
def serve_log(filename: str):
    # Block path traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = pathlib.Path(LOG_DIR) / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Log file not found")
    return PlainTextResponse(path.read_text())


@app.get("/api/v1/scenarios")
def list_scenarios():
    result = []
    for name in SCENARIO_ORDER:
        try:
            s = load_scenario(name)
            result.append({"id": s["id"], "title": s["title"], "scored": s.get("scored", True)})
        except Exception as e:
            result.append({"id": name, "error": str(e)})
    return result


# ---------------------------------------------------------------------------
# Grafana reverse proxy — forwards /grafana/* to the Grafana container.
# In production Caddy handles this; here it covers direct port-8080 access.
# ---------------------------------------------------------------------------

GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://grafana:3000")

_grafana_client = httpx.AsyncClient(base_url=GRAFANA_URL, timeout=10.0, follow_redirects=True)


@app.api_route("/grafana/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def grafana_proxy(path: str, request: Request):
    # Preserve the /grafana/ prefix — Grafana expects it when SERVE_FROM_SUB_PATH=true
    url = f"/grafana/{path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    headers = dict(request.headers)
    headers.pop("host", None)
    # Strip Grafana session cookies so stale browser cookies don't cause 401s.
    # Anonymous auth kicks in cleanly when no session token is present.
    headers.pop("cookie", None)
    resp = await _grafana_client.request(
        method=request.method,
        url=url,
        headers=headers,
        content=await request.body(),
    )
    return StreamingResponse(
        resp.aiter_bytes(),
        status_code=resp.status_code,
        headers=dict(resp.headers),
    )


# Serve React static files (built frontend)
_static_dir = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(_static_dir):
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")
