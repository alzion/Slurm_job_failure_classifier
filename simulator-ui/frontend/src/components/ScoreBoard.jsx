import React from "react";
import { LEARNING_OBJECTIVE } from "../constants.js";

const S = {
  page: {
    maxWidth: 760, margin: "0 auto", padding: "40px 24px",
    display: "flex", flexDirection: "column", gap: 24,
  },
  topBar: {
    display: "flex", alignItems: "flex-start", justifyContent: "space-between",
    flexWrap: "wrap", gap: 12,
  },
  title: { fontSize: 26, fontWeight: 800, color: "#f1f5f9" },
  elapsed: { fontSize: 13, color: "#475569", marginTop: 4 },
  startOverBtn: {
    padding: "8px 18px", borderRadius: 6, border: "1px solid #ef4444",
    background: "transparent", color: "#ef4444", cursor: "pointer",
    fontSize: 13, fontWeight: 600, whiteSpace: "nowrap", alignSelf: "flex-start",
  },
  totalBox: {
    background: "#172554", borderRadius: 10, padding: "24px 32px",
    display: "flex", alignItems: "center", gap: 24,
  },
  totalNum: { fontSize: 56, fontWeight: 900, color: "#60a5fa" },
  totalLabel: { fontSize: 14, color: "#93c5fd" },
  table: { width: "100%", borderCollapse: "collapse" },
  th: {
    textAlign: "left", fontSize: 11, fontWeight: 700, color: "#60a5fa",
    textTransform: "uppercase", letterSpacing: 1, padding: "8px 12px",
    borderBottom: "1px solid #1e293b",
  },
  td: { padding: "10px 12px", fontSize: 13, color: "#cbd5e1", borderBottom: "1px solid #1e293b" },
  revisitBtn: {
    padding: "3px 10px", borderRadius: 4, border: "1px solid #334155",
    background: "transparent", color: "#64748b", cursor: "pointer", fontSize: 11,
  },
  objective: {
    background: "#1e293b", borderRadius: 8, padding: "16px 20px",
    fontSize: 14, color: "#94a3b8", lineHeight: 1.6, fontStyle: "italic",
    borderLeft: "3px solid #10b981",
  },
};

export default function ScoreBoard({ scoreData, startedAt, history, onRevisit, onStartOver }) {
  const elapsed = startedAt
    ? Math.round((Date.now() - new Date(startedAt).getTime()) / 60000)
    : null;

  // Build a quick lookup: incident_idx → history entry
  const historyMap = {};
  (history || []).forEach(h => { historyMap[h.scenario.incident_idx] = h; });

  return (
    <div style={S.page}>
      <div style={S.topBar}>
        <div>
          <div style={S.title}>Simulation Complete</div>
          {elapsed && <div style={S.elapsed}>Time elapsed: {elapsed} min</div>}
        </div>
        <button style={S.startOverBtn} onClick={onStartOver}>
          ↺ Start Over
        </button>
      </div>

      <div style={S.totalBox}>
        <div>
          <div style={S.totalNum}>{scoreData?.total ?? "—"}</div>
          <div style={S.totalLabel}>
            Total score out of {(scoreData?.by_incident?.length || 5) * 100}
          </div>
        </div>
      </div>

      <table style={S.table}>
        <thead>
          <tr>
            <th style={S.th}>Incident</th>
            <th style={S.th}>Root cause</th>
            <th style={S.th}>Escalation</th>
            <th style={S.th}>Communication</th>
            <th style={S.th}>Total</th>
            <th style={S.th}></th>
          </tr>
        </thead>
        <tbody>
          {(scoreData?.by_incident || []).map(inc => {
            const entry = historyMap[inc.incident_idx];
            return (
              <tr key={inc.incident_idx}>
                <td style={S.td}>{inc.incident_title}</td>
                <td style={S.td}>{inc.root_cause} / 30</td>
                <td style={S.td}>{inc.escalation} / 60</td>
                <td style={S.td}>{inc.communication} / 10</td>
                <td style={{ ...S.td, fontWeight: 700, color: "#34d399" }}>{inc.total}</td>
                <td style={S.td}>
                  {entry && (
                    <button style={S.revisitBtn} onClick={() => onRevisit(entry)}>
                      Revisit
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <div style={S.objective}>
        <strong>Learning objective:</strong> {LEARNING_OBJECTIVE}
      </div>
    </div>
  );
}
