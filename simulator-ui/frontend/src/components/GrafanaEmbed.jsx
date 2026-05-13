import React from "react";

const S = {
  wrapper: { height: "100%", display: "flex", flexDirection: "column" },
  iframe: { flex: 1, border: "none", width: "100%", background: "#1e2130" },
  placeholder: {
    flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
    color: "#475569", fontSize: 13, fontStyle: "italic",
  },
};

export default function GrafanaEmbed({ url }) {
  if (!url) {
    return (
      <div style={S.wrapper}>
        <div style={S.placeholder}>No dashboard for this phase.</div>
      </div>
    );
  }

  const src = `${url}?kiosk=tv&theme=dark`;
  return (
    <div style={S.wrapper}>
      <iframe
        src={src}
        style={S.iframe}
        title="Grafana Dashboard"
        sandbox="allow-scripts allow-same-origin allow-forms"
      />
    </div>
  );
}
