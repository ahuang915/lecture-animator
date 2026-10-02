import { useEffect, useState } from "react";
import { api, loadSettings } from "../api.js";

function when(ts) {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export default function Home({ onOpen, run, busy, setError }) {
  const [projects, setProjects] = useState(null);
  const [name, setName] = useState("");
  const [cloneFrom, setCloneFrom] = useState("");
  const advanced = loadSettings().advanced;

  async function refresh() {
    try {
      const data = await api("/api/projects");
      setProjects(data.projects);
    } catch (err) {
      setError(err.message);
      setProjects([]);
    }
  }
  useEffect(() => { refresh(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function create() {
    const payload = cloneFrom
      ? await run("Creating project", () =>
          api(`/api/projects/${cloneFrom}/clone`, { method: "POST", json: { name: name.trim() } }))
      : await run("Creating project", () =>
          api("/api/projects", { method: "POST", json: { name: name.trim() || "Untitled project" } }));
    if (payload?.project) onOpen(payload.project);
  }

  async function open(id) {
    const payload = await run("Opening project", () => api(`/api/projects/${id}`));
    if (payload?.project) onOpen(payload.project);
  }

  async function remove(p) {
    if (!window.confirm(`Delete “${p.name || p.id}” and all its files? This can't be undone.`)) return;
    await run("Deleting project", () => api(`/api/projects/${p.id}`, { method: "DELETE" }));
    refresh();
  }

  return (
    <div className="home">
      <section className="card hero">
        <h1>Turn a narration recording into an animated lecture</h1>
        <ol className="how">
          <li><strong>Upload</strong> your recorded narration (one file, or one file per scene), plus optional slides.</li>
          <li><strong>Review</strong> the scene plan the app writes from what you said.</li>
          <li><strong>Animate</strong> each scene — the visuals are timed to your voice — and request changes in plain language.</li>
          <li><strong>Export</strong> one MP4.</li>
        </ol>
        <div className="new-project">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Project name, e.g. “Lecture 5: Transformers”"
            onKeyDown={(e) => e.key === "Enter" && !busy && create()}
          />
          <button className="primary" onClick={create} disabled={busy}>New project</button>
        </div>
        {advanced && projects?.length ? (
          <label className="field compact">
            <span>Start from a previous project (reuses its visual style, notes and images)</span>
            <select value={cloneFrom} onChange={(e) => setCloneFrom(e.target.value)}>
              <option value="">— No, start fresh —</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>{p.name || p.id}</option>
              ))}
            </select>
          </label>
        ) : null}
      </section>

      <section className="card">
        <h2>Your projects</h2>
        {projects === null ? <p className="muted">Loading…</p> : null}
        {projects?.length === 0 ? <p className="muted">No projects yet — create one above.</p> : null}
        <ul className="project-list">
          {(projects || []).map((p) => (
            <li key={p.id}>
              <button className="project-open" onClick={() => open(p.id)} disabled={busy}>
                <strong>{p.name || p.id}</strong>
                <span className="muted">
                  {p.scene_count ? `${p.scene_count} scenes` : "not planned yet"}
                  {p.has_video ? " · video ready" : ""} · {when(p.updated_at)}
                </span>
              </button>
              <button className="link danger" onClick={() => remove(p)} disabled={busy}>Delete</button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
