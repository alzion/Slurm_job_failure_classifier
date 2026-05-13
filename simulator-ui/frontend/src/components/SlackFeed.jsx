import React from "react";

const S = {
  feed: {
    display: "flex", flexDirection: "column", gap: 12, padding: 12,
    overflowY: "auto", height: "100%",
  },
  msg: {
    display: "flex", gap: 10, alignItems: "flex-start",
  },
  avatar: {
    width: 32, height: 32, borderRadius: 6, background: "#3b4a6b",
    display: "flex", alignItems: "center", justifyContent: "center",
    fontWeight: 700, fontSize: 14, flexShrink: 0, color: "#93c5fd",
  },
  bubble: { flex: 1 },
  name: { fontSize: 12, fontWeight: 700, color: "#60a5fa", marginBottom: 2 },
  role: { fontSize: 10, color: "#64748b", marginLeft: 6 },
  text: { fontSize: 13, color: "#cbd5e1", lineHeight: 1.5 },
  label: { fontSize: 11, color: "#475569", padding: "6px 12px", fontStyle: "italic" },
};

export default function SlackFeed({ messages }) {
  if (!messages || messages.length === 0) {
    return <div style={S.feed}><div style={S.label}>No messages yet.</div></div>;
  }
  return (
    <div style={S.feed}>
      {messages.map((m, i) => (
        <div key={i} style={S.msg}>
          <div style={S.avatar}>{m.avatar}</div>
          <div style={S.bubble}>
            <div style={S.name}>
              {m.display_name}
              {m.role && <span style={S.role}>{m.role}</span>}
            </div>
            <div style={S.text}>{m.text}</div>
          </div>
        </div>
      ))}
    </div>
  );
}
