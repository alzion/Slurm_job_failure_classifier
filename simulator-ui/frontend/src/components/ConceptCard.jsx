import React from "react";

const S = {
  page: {
    minHeight: "100vh", background: "#0f1117",
    display: "flex", alignItems: "center", justifyContent: "center",
    padding: "40px 24px",
  },
  card: {
    maxWidth: 680, width: "100%",
    display: "flex", flexDirection: "column", gap: 28,
  },
  eyebrow: {
    fontSize: 11, fontWeight: 700, color: "#10b981",
    textTransform: "uppercase", letterSpacing: 2, marginBottom: 8,
  },
  title: { fontSize: 30, fontWeight: 800, color: "#f1f5f9", lineHeight: 1.3 },
  body: {
    fontSize: 15, color: "#cbd5e1", lineHeight: 1.8, whiteSpace: "pre-wrap",
    background: "#1e293b", borderRadius: 10, padding: "24px 28px",
    borderLeft: "3px solid #10b981",
  },
  footer: {
    display: "flex", alignItems: "center", justifyContent: "space-between",
    flexWrap: "wrap", gap: 12,
  },
  hint: { fontSize: 13, color: "#475569", maxWidth: 380, lineHeight: 1.5 },
  readyBtn: {
    padding: "12px 32px", borderRadius: 8, border: "none",
    background: "#2563eb", color: "#fff", cursor: "pointer",
    fontSize: 15, fontWeight: 700, whiteSpace: "nowrap",
  },
};

export default function ConceptCard({ scenario, onReady }) {
  const cc = scenario?.concept_card || {};
  return (
    <div style={S.page}>
      <div style={S.card}>
        <div>
          <div style={S.eyebrow}>Concept card · Incident {scenario?.incident_idx}</div>
          <div style={S.title}>{cc.title}</div>
        </div>
        <div style={S.body}>{cc.body}</div>
        <div style={S.footer}>
          <span style={S.hint}>
            Read this before you begin — you will need this vocabulary to diagnose the incident.
          </span>
          <button style={S.readyBtn} onClick={onReady}>I'm ready →</button>
        </div>
      </div>
    </div>
  );
}
