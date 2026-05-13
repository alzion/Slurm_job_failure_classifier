const BASE = "/api/v1";

async function req(method, path, body) {
  const opts = {
    method,
    headers: { "Content-Type": "application/json" },
  };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch(`${BASE}${path}`, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw Object.assign(new Error(err.detail || "Request failed"), { status: res.status });
  }
  return res.json();
}

export const api = {
  createSession:  ()           => req("POST", "/sessions"),
  getSession:     (id)         => req("GET",  `/sessions/${id}`),
  takeAction:     (id, action) => req("POST", `/sessions/${id}/action`, { action_id: action }),
  submitFreetext: (id, text)   => req("POST", `/sessions/${id}/freetext`, { text }),
  nextIncident:   (id)         => req("POST", `/sessions/${id}/next`),
  getScore:       (id)         => req("GET",  `/sessions/${id}/score`),
  getLog:         (filename)   => fetch(`${BASE}/logs/${filename}`).then(r => r.text()),
};
