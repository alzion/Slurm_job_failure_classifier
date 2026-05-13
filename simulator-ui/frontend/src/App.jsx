import React, { useState, useEffect } from "react";
import { api } from "./api.js";
import { LEARNING_OBJECTIVE } from "./constants.js";
import SlackFeed from "./components/SlackFeed.jsx";
import LogViewer from "./components/LogViewer.jsx";
import GrafanaEmbed from "./components/GrafanaEmbed.jsx";
import DecisionPanel from "./components/DecisionPanel.jsx";
import Debrief from "./components/Debrief.jsx";
import ScoreBoard from "./components/ScoreBoard.jsx";

const S = {
  app: { minHeight: "100vh", background: "#0f1117", color: "#e2e8f0" },
  welcome: {
    maxWidth: 640, margin: "0 auto", padding: "80px 24px",
    display: "flex", flexDirection: "column", gap: 24, alignItems: "center",
    textAlign: "center",
  },
  welcomeTitle: { fontSize: 32, fontWeight: 800, color: "#f1f5f9" },
  welcomeSub: { fontSize: 15, color: "#64748b", lineHeight: 1.6 },
  objective: {
    background: "#1e293b", borderRadius: 8, padding: "16px 20px",
    fontSize: 14, color: "#94a3b8", lineHeight: 1.6, fontStyle: "italic",
    borderLeft: "3px solid #10b981", textAlign: "left", width: "100%",
  },
  beginBtn: {
    padding: "12px 32px", borderRadius: 8, border: "none",
    background: "#2563eb", color: "#fff", cursor: "pointer",
    fontSize: 16, fontWeight: 700,
  },
  header: {
    display: "flex", alignItems: "center", justifyContent: "space-between",
    padding: "10px 20px", background: "#0d1117", borderBottom: "1px solid #1e293b",
  },
  headerTitle: { fontSize: 15, fontWeight: 700, color: "#f1f5f9" },
  headerRight: { display: "flex", alignItems: "center", gap: 16 },
  incBadge: { fontSize: 12, color: "#64748b" },
  progress: { display: "flex", gap: 4 },
  progressDot: (active, done) => ({
    width: 8, height: 8, borderRadius: "50%",
    background: done ? "#10b981" : active ? "#2563eb" : "#334155",
  }),
  grid: {
    display: "grid",
    gridTemplateRows: "1fr auto",
    gridTemplateColumns: "280px 1fr 1fr",
    height: "calc(100vh - 49px)",
  },
  panel: (col) => ({
    gridColumn: col, gridRow: "1",
    overflow: "hidden", display: "flex", flexDirection: "column",
    borderRight: col !== "3" ? "1px solid #1e293b" : "none",
    borderBottom: "1px solid #1e293b",
  }),
  panelLabel: {
    fontSize: 10, fontWeight: 700, color: "#475569", textTransform: "uppercase",
    letterSpacing: 1, padding: "6px 12px", borderBottom: "1px solid #1e293b",
    background: "#0d1117", flexShrink: 0,
  },
  decisionRow: { gridColumn: "1 / -1", gridRow: "2", borderTop: "1px solid #1e293b" },
  error: { color: "#f87171", padding: 24, fontSize: 14 },
};

const TOTAL_INCIDENTS = 6;

export default function App() {
  const [screen, setScreen]             = useState("welcome");
  const [session, setSession]           = useState(null);
  const [state, setState]               = useState(null);
  const [consequence, setConsequence]   = useState("");
  const [loading, setLoading]           = useState(false);
  const [error, setError]               = useState("");
  const [debriefScore, setDebriefScore] = useState(null);
  const [finalScore, setFinalScore]     = useState(null);
  // history: [{scenario, score}] — one entry per completed scored incident
  const [history, setHistory]           = useState([]);
  // revisitEntry: non-null when viewing a past debrief
  const [revisitEntry, setRevisitEntry] = useState(null);

  // Resume on mount
  useEffect(() => {
    const sid = localStorage.getItem("session_id");
    if (!sid) return;
    api.getSession(sid)
      .then(s => {
        setSession(sid);
        setState(s);
        setScreen(s.completed ? "final" : "active");
      })
      .catch(() => localStorage.removeItem("session_id"));
  }, []);

  function startOver() {
    localStorage.removeItem("session_id");
    setSession(null);
    setState(null);
    setConsequence("");
    setError("");
    setDebriefScore(null);
    setFinalScore(null);
    setHistory([]);
    setRevisitEntry(null);
    setScreen("welcome");
  }

  async function begin() {
    setLoading(true);
    setError("");
    try {
      const s = await api.createSession();
      localStorage.setItem("session_id", s.session_id);
      setSession(s.session_id);
      setState(s);
      setScreen("active");
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleAction(actionId) {
    setLoading(true);
    setError("");
    try {
      const result = await api.takeAction(session, actionId);
      setConsequence(result.consequence);
      setState(prev => ({
        ...prev,
        phase_id:         result.next_phase || prev.phase_id,
        slack_messages:   result.slack_messages.length ? result.slack_messages : prev.slack_messages,
        grafana_dashboard: result.grafana_dashboard || prev.grafana_dashboard,
        log_file:         result.log_file || prev.log_file,
        available_actions: result.available_actions,
        requires_freetext: result.requires_freetext,
        freetext_prompt:   result.freetext_prompt,
      }));
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleFreetext(text) {
    setLoading(true);
    try {
      return await api.submitFreetext(session, text);
    } catch (e) {
      setError(e.message);
      return { score_delta: 0, matched_keywords: [] };
    } finally {
      setLoading(false);
    }
  }

  async function handleContinue() {
    if (state?.incident_idx === 0) {
      await advanceIncident();
      return;
    }
    setLoading(true);
    try {
      const score = await api.getScore(session);
      const incScore = score.by_incident.find(i => i.incident_idx === state.incident_idx);
      setDebriefScore(incScore || null);
      setRevisitEntry(null);
      setScreen("debrief");
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function advanceIncident() {
    // Save current incident's debrief to history before advancing
    if (state && debriefScore !== null && state.incident_scored !== false) {
      setHistory(prev => {
        const already = prev.some(h => h.scenario.incident_idx === state.incident_idx);
        if (already) return prev;
        return [...prev, { scenario: state, score: debriefScore }];
      });
    }
    setRevisitEntry(null);
    setLoading(true);
    setError("");
    setConsequence("");
    try {
      const next = await api.nextIncident(session);
      if (next.completed) {
        const score = await api.getScore(session);
        setFinalScore(score);
        setScreen("final");
        return;
      }
      setState(next);
      setScreen("active");
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  function openRevisit(entry) {
    setRevisitEntry(entry);
    setScreen("debrief");
  }

  function closeRevisit() {
    setRevisitEntry(null);
    // Go back to wherever we came from
    setScreen(finalScore ? "final" : "debrief");
  }

  // ── Welcome ────────────────────────────────────────────────────────────────

  if (screen === "welcome") {
    return (
      <div style={S.app}>
        <div style={S.welcome}>
          <div style={S.welcomeTitle}>AI Infra TPM Simulator</div>
          <div style={S.welcomeSub}>Five GPU incidents. Real signals. Defensible decisions.</div>
          <div style={S.objective}>
            <strong>Learning objective:</strong> {LEARNING_OBJECTIVE}
          </div>
          {error && <div style={S.error}>{error}</div>}
          <button style={S.beginBtn} onClick={begin} disabled={loading}>
            {loading ? "Starting…" : "Begin →"}
          </button>
        </div>
      </div>
    );
  }

  // ── Final score ────────────────────────────────────────────────────────────

  if (screen === "final") {
    return (
      <div style={S.app}>
        <ScoreBoard
          scoreData={finalScore}
          startedAt={state?.started_at}
          history={history}
          onRevisit={openRevisit}
          onStartOver={startOver}
        />
      </div>
    );
  }

  // ── Debrief (normal or revisit) ────────────────────────────────────────────

  if (screen === "debrief") {
    const debriefScenario  = revisitEntry ? revisitEntry.scenario : state;
    const debriefScoreShow = revisitEntry ? revisitEntry.score    : debriefScore;
    const isLast = !revisitEntry && state?.incident_idx === TOTAL_INCIDENTS - 1;

    return (
      <div style={S.app}>
        <Debrief
          scenario={debriefScenario}
          score={debriefScoreShow}
          history={history}
          isRevisit={!!revisitEntry}
          isLast={isLast}
          onNext={advanceIncident}
          onBack={closeRevisit}
          onRevisit={openRevisit}
        />
      </div>
    );
  }

  // ── Active incident ────────────────────────────────────────────────────────

  const isOrientation = state?.incident_idx === 0;
  const incidentNum   = state?.incident_idx ?? 0;

  return (
    <div style={S.app}>
      <div style={S.header}>
        <div style={S.headerTitle}>{state?.incident_title || "Loading…"}</div>
        <div style={S.headerRight}>
          {!isOrientation && (
            <span style={S.incBadge}>Incident {incidentNum} / {TOTAL_INCIDENTS - 1}</span>
          )}
          <div style={S.progress}>
            {Array.from({ length: TOTAL_INCIDENTS - 1 }, (_, i) => (
              <div key={i} style={S.progressDot(i + 1 === incidentNum, i + 1 < incidentNum)} />
            ))}
          </div>
        </div>
      </div>

      {error && <div style={S.error}>{error}</div>}

      <div style={S.grid}>
        <div style={S.panel("1")}>
          <div style={S.panelLabel}>Slack</div>
          <SlackFeed messages={state?.slack_messages || []} />
        </div>
        <div style={S.panel("2")}>
          <div style={S.panelLabel}>Grafana</div>
          <GrafanaEmbed url={state?.grafana_dashboard} />
        </div>
        <div style={S.panel("3")}>
          <div style={S.panelLabel}>Logs</div>
          <LogViewer filename={state?.log_file} />
        </div>
        <div style={S.decisionRow}>
          <DecisionPanel
            actions={state?.available_actions}
            consequence={consequence}
            requiresFreetext={state?.requires_freetext}
            freetextPrompt={state?.freetext_prompt}
            isOrientation={isOrientation}
            onAction={handleAction}
            onFreetext={handleFreetext}
            onContinue={handleContinue}
            loading={loading}
          />
        </div>
      </div>
    </div>
  );
}
