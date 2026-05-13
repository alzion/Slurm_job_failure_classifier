import React, { useState } from "react";

const S = {
  panel: {
    padding: "12px 16px", display: "flex", flexDirection: "column", gap: 10,
    borderTop: "1px solid #1e293b",
  },
  consequence: {
    background: "#1e293b", borderRadius: 6, padding: "8px 12px",
    fontSize: 13, color: "#94a3b8", lineHeight: 1.5,
    borderLeft: "3px solid #3b82f6",
  },
  actions: { display: "flex", flexWrap: "wrap", gap: 8 },
  btn: {
    padding: "8px 16px", borderRadius: 6, border: "1px solid #334155",
    background: "#1e293b", color: "#e2e8f0", cursor: "pointer",
    fontSize: 13, fontWeight: 500, transition: "all .15s",
  },
  btnHover: { background: "#2563eb", borderColor: "#2563eb" },
  continueBtn: {
    padding: "8px 20px", borderRadius: 6, border: "none",
    background: "#2563eb", color: "#fff", cursor: "pointer",
    fontSize: 13, fontWeight: 600,
  },
  freetextArea: {
    width: "100%", minHeight: 72, background: "#1e293b", border: "1px solid #334155",
    borderRadius: 6, color: "#e2e8f0", fontSize: 13, padding: 10, resize: "vertical",
    fontFamily: "inherit",
  },
  freetextRow: { display: "flex", gap: 8, alignItems: "flex-end" },
  prompt: { fontSize: 12, color: "#64748b", marginBottom: 4 },
  submitBtn: {
    padding: "8px 16px", borderRadius: 6, border: "none",
    background: "#10b981", color: "#fff", cursor: "pointer",
    fontSize: 13, fontWeight: 600, whiteSpace: "nowrap",
  },
  label: { fontSize: 11, color: "#475569", fontStyle: "italic" },
};

export default function DecisionPanel({
  actions,
  consequence,
  requiresFreetext,
  freetextPrompt,
  isOrientation,
  onAction,
  onFreetext,
  onContinue,
  loading,
}) {
  const [hovered, setHovered] = useState(null);
  const [ftText, setFtText] = useState("");
  const [ftResult, setFtResult] = useState(null);

  async function handleFreetext() {
    if (!ftText.trim()) return;
    const result = await onFreetext(ftText);
    setFtResult(result);
  }

  if (isOrientation) {
    return (
      <div style={S.panel}>
        <span style={S.label}>Explore the Slack feed, dashboard, and logs above. No decision required.</span>
        <div>
          <button style={S.continueBtn} onClick={onContinue} disabled={loading}>
            Continue →
          </button>
        </div>
      </div>
    );
  }

  return (
    <div style={S.panel}>
      {consequence && <div style={S.consequence}>{consequence}</div>}

      {requiresFreetext ? (
        ftResult ? (
          <div style={S.consequence}>
            Score: +{ftResult.score_delta} pts — matched: {ftResult.matched_keywords?.join(", ") || "none"}
            <br />
            <button style={{ ...S.continueBtn, marginTop: 8 }} onClick={onContinue}>
              View debrief →
            </button>
          </div>
        ) : (
          <div>
            {freetextPrompt && <div style={S.prompt}>{freetextPrompt}</div>}
            <div style={S.freetextRow}>
              <textarea
                style={S.freetextArea}
                value={ftText}
                onChange={e => setFtText(e.target.value)}
                placeholder="Type your update here…"
              />
              <button style={S.submitBtn} onClick={handleFreetext} disabled={loading || !ftText.trim()}>
                Submit
              </button>
            </div>
          </div>
        )
      ) : (
        <div style={S.actions}>
          {(actions || []).map(a => (
            <button
              key={a.id}
              style={{
                ...S.btn,
                ...(hovered === a.id ? S.btnHover : {}),
              }}
              onMouseEnter={() => setHovered(a.id)}
              onMouseLeave={() => setHovered(null)}
              onClick={() => onAction(a.id)}
              disabled={loading}
            >
              {a.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
