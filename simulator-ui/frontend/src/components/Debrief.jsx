import React from "react";

const S = {
  page: {
    maxWidth: 760, margin: "0 auto", padding: "40px 24px",
    display: "flex", flexDirection: "column", gap: 28,
  },
  topBar: {
    display: "flex", alignItems: "center", justifyContent: "space-between",
    flexWrap: "wrap", gap: 12,
  },
  title: { fontSize: 22, fontWeight: 700, color: "#f1f5f9" },
  backBtn: {
    padding: "6px 14px", borderRadius: 6, border: "1px solid #334155",
    background: "transparent", color: "#94a3b8", cursor: "pointer", fontSize: 13,
  },
  section: {
    background: "#1e293b", borderRadius: 8, padding: "16px 20px",
    display: "flex", flexDirection: "column", gap: 8,
  },
  label: {
    fontSize: 11, fontWeight: 700, color: "#60a5fa",
    textTransform: "uppercase", letterSpacing: 1,
  },
  body: { fontSize: 14, color: "#cbd5e1", lineHeight: 1.7, whiteSpace: "pre-wrap" },
  scoreRow: { display: "flex", gap: 16, flexWrap: "wrap" },
  scoreBox: {
    flex: 1, minWidth: 120, background: "#0f172a", borderRadius: 6,
    padding: "12px 16px", textAlign: "center",
  },
  scoreNum: { fontSize: 28, fontWeight: 800, color: "#34d399" },
  scoreLabel: { fontSize: 11, color: "#64748b", marginTop: 2 },
  highlight: { borderLeft: "3px solid #fbbf24" },
  actions: { display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" },
  nextBtn: {
    padding: "10px 24px", borderRadius: 6, border: "none",
    background: "#2563eb", color: "#fff", cursor: "pointer", fontSize: 14, fontWeight: 600,
  },
  // Past incidents nav
  pastNav: {
    display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center",
  },
  pastLabel: { fontSize: 11, color: "#475569", fontWeight: 700, textTransform: "uppercase", letterSpacing: 1 },
  pastBtn: {
    padding: "4px 10px", borderRadius: 4, border: "1px solid #334155",
    background: "transparent", color: "#64748b", cursor: "pointer", fontSize: 12,
  },
  pastBtnActive: {
    border: "1px solid #3b82f6", color: "#60a5fa",
  },
};

export default function Debrief({
  scenario, score, history, isRevisit, isLast,
  onNext, onBack, onRevisit,
}) {
  const d  = scenario?.debrief      || {};
  const cc = scenario?.concept_card || {};

  const pastIncidents = (history || []).filter(
    h => h.scenario.incident_idx !== scenario?.incident_idx
  );

  return (
    <div style={S.page}>
      {/* Top bar: title + back button when revisiting */}
      <div style={S.topBar}>
        <div style={S.title}>
          {isRevisit ? "↩ Revisiting — " : "Debrief — "}
          {scenario?.incident_title}
        </div>
        {isRevisit && (
          <button style={S.backBtn} onClick={onBack}>← Back</button>
        )}
      </div>

      {/* Past incidents nav (only during active debrief flow, not when revisiting) */}
      {!isRevisit && pastIncidents.length > 0 && (
        <div style={S.pastNav}>
          <span style={S.pastLabel}>Past debriefs:</span>
          {pastIncidents.map(h => (
            <button
              key={h.scenario.incident_idx}
              style={S.pastBtn}
              onClick={() => onRevisit(h)}
            >
              {h.scenario.incident_title}
            </button>
          ))}
        </div>
      )}

      <div style={S.section}>
        <div style={S.label}>What happened</div>
        <div style={S.body}>{d.what_happened}</div>
      </div>

      <div style={S.section}>
        <div style={S.label}>Correct diagnosis</div>
        <div style={S.body}>{d.correct_diagnosis}</div>
      </div>

      {cc.title && (
        <div style={S.section}>
          <div style={S.label}>Concept — {cc.title}</div>
          <div style={S.body}>{cc.body}</div>
        </div>
      )}

      {score && (
        <div style={S.section}>
          <div style={S.label}>Your score</div>
          <div style={S.scoreRow}>
            <div style={S.scoreBox}>
              <div style={S.scoreNum}>{score.root_cause}</div>
              <div style={S.scoreLabel}>Root cause / 30</div>
            </div>
            <div style={S.scoreBox}>
              <div style={S.scoreNum}>{score.escalation}</div>
              <div style={S.scoreLabel}>Escalation / 60</div>
            </div>
            <div style={S.scoreBox}>
              <div style={S.scoreNum}>{score.communication}</div>
              <div style={S.scoreLabel}>Communication / 10</div>
            </div>
            <div style={{ ...S.scoreBox, background: "#172554" }}>
              <div style={{ ...S.scoreNum, color: "#60a5fa" }}>{score.total}</div>
              <div style={S.scoreLabel}>Total / 100</div>
            </div>
          </div>
        </div>
      )}

      <div style={S.section}>
        <div style={S.label}>TPM lesson</div>
        <div style={S.body}>{d.tpm_lesson}</div>
      </div>

      <div style={S.section}>
        <div style={S.label}>Learning objective link</div>
        <div style={S.body}>{d.learning_objective_link}</div>
      </div>

      {d.top_performer_note && (
        <div style={{ ...S.section, ...S.highlight }}>
          <div style={S.label}>Top performer insight</div>
          <div style={S.body}>{d.top_performer_note}</div>
        </div>
      )}

      {/* Footer actions */}
      <div style={S.actions}>
        {isRevisit ? (
          <button style={S.backBtn} onClick={onBack}>← Back</button>
        ) : (
          <button style={S.nextBtn} onClick={onNext}>
            {isLast ? "See final score →" : "Next incident →"}
          </button>
        )}
      </div>
    </div>
  );
}
