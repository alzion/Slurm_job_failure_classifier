import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";

const S = {
  wrapper: { height: "100%", display: "flex", flexDirection: "column" },
  pre: {
    flex: 1, overflowY: "auto", padding: 12, margin: 0,
    fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
    fontSize: 11, lineHeight: 1.6, color: "#86efac", background: "transparent",
    whiteSpace: "pre-wrap", wordBreak: "break-word",
  },
  empty: { padding: 12, color: "#475569", fontStyle: "italic", fontSize: 12 },
};

export default function LogViewer({ filename }) {
  const [content, setContent] = useState("");
  const preRef = useRef();

  useEffect(() => {
    if (!filename) { setContent(""); return; }
    api.getLog(filename).then(setContent).catch(() => setContent("(log unavailable)"));
  }, [filename]);

  useEffect(() => {
    if (preRef.current) preRef.current.scrollTop = 0;
  }, [content]);

  if (!filename) return <div style={S.empty}>No log file for this phase.</div>;

  return (
    <div style={S.wrapper}>
      <pre ref={preRef} style={S.pre}>{content || "Loading…"}</pre>
    </div>
  );
}
