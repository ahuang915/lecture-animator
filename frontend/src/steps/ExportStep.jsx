import { useEffect, useState } from "react";
import { api } from "../api.js";

export default function ExportStep({ project, run, setStep, busy }) {
  const [cards, setCards] = useState(project.cards);
  const [dirty, setDirty] = useState(false);
  useEffect(() => { setCards(project.cards); setDirty(false); }, [project.id, project.cards]);

  const notReady = project.scenes.filter((s) => s.status !== "done" && !s.iterations.some((it) => it.ok));

  function edit(field, value) {
    setCards((c) => ({ ...c, [field]: value }));
    setDirty(true);
  }

  async function build() {
    if (dirty) {
      const saved = await run("Saving title and credits", () =>
        api(`/api/projects/${project.id}/cards`, { method: "PUT", json: cards }));
      if (!saved) return;
    }
    run("Creating the final video", () => api(`/api/projects/${project.id}/stitch`, { method: "POST" }), {
      hint: "Joining all scenes into one MP4. Usually under a few minutes.",
      done: (p) => (p.skipped?.length ? `Video created. Skipped (no finished version): ${p.skipped.join(", ")}.` : "Your video is ready.")
    });
  }

  const fileUrl = project.final_video_url;
  const downloadUrl = fileUrl ? fileUrl.replace(/\?v=/, "?download=1&v=") : null;

  return (
    <div className="step-body">
      {notReady.length ? (
        <section className="card warn-card">
          <p>
            {notReady.length} scene{notReady.length === 1 ? " has" : "s have"} no finished version yet and will be left out:{" "}
            {notReady.map((s) => s.title || s.id).join(", ")}.
          </p>
          <button onClick={() => setStep("animate")}>← Back to Animate</button>
        </section>
      ) : null}

      <section className="card">
        <h2>Title and credits (optional)</h2>
        <p className="muted">Leave blank to skip. The title card shows for 5 seconds at the start, the credits for 6 seconds at the end.</p>
        <div className="grid2">
          <label className="field">
            <span>Title</span>
            <textarea rows={2} value={cards.title} onChange={(e) => edit("title", e.target.value)} placeholder="Reinforcement Learning" />
          </label>
          <div>
            <label className="field">
              <span>Subtitle</span>
              <input value={cards.subtitle} onChange={(e) => edit("subtitle", e.target.value)} placeholder="Lecture 4 · Basic Principles of AI" />
            </label>
            <label className="field">
              <span>Author line</span>
              <input value={cards.author} onChange={(e) => edit("author", e.target.value)} placeholder="Your name · Your institution" />
            </label>
          </div>
        </div>
        <label className="field">
          <span>Credits (one line each)</span>
          <textarea rows={3} value={cards.credits} onChange={(e) => edit("credits", e.target.value)} placeholder={"Lecture by …\nAnimation produced by …"} />
        </label>
      </section>

      <section className="card">
        <h2>Final video</h2>
        <button className="primary big" onClick={build} disabled={busy}>
          {fileUrl ? "Re-create final video" : "Create final video"}
        </button>
        <p className="muted">Uses the version marked “In final video” for each scene. Re-create it after any change.</p>
        {fileUrl ? (
          <>
            <video key={fileUrl} controls src={fileUrl} className="player" />
            <div className="row">
              <a className="button primary" href={downloadUrl}>Download MP4</a>
              <a className="button" href={`/api/projects/${project.id}/export.zip`}>Download everything (ZIP: video, per-scene clips, code, audio)</a>
            </div>
          </>
        ) : null}
      </section>
    </div>
  );
}
