import { useEffect, useState } from "react";
import { api, clock, formData, money } from "../api.js";
import ImageGrid from "../components/ImageGrid.jsx";

const STATUS_LABEL = {
  "no-audio": "No audio",
  ready: "Not animated",
  done: "Done",
  failed: "Needs a fix"
};

const GENERATE_HINT =
  "Claude writes the animation code, then it renders and is joined to your audio. Usually 2–8 minutes per scene — longer scenes take longer. Keep this tab open.";

function errorFeedback(logTail) {
  return `The animation failed to render with this error:\n\n${logTail}\n\nFix the cause of this error.`;
}

export default function AnimateStep({ project, settings, config, run, setStep, setError, setNotice, busy }) {
  const [activeId, setActiveId] = useState(project.scenes[0]?.id);
  const scene = project.scenes.find((s) => s.id === activeId) || project.scenes[0];
  const index = project.scenes.findIndex((s) => s.id === scene?.id);

  const options = {
    // Empty = the server picks the default for the keys present (Claude, else GPT).
    model: settings.model || undefined,
    effort: settings.effort || null,
    quality: settings.quality || "qh"
  };
  const remaining = project.scenes.filter((s) => !s.iterations.length);
  const doneCount = project.scenes.filter((s) => s.status === "done").length;

  async function animate(s) {
    const i = project.scenes.findIndex((x) => x.id === s.id) + 1;
    return run(`Animating scene ${i}: ${s.title}`, () =>
      api(`/api/projects/${project.id}/scenes/${s.id}/generate`, { method: "POST", json: options }),
      { hint: GENERATE_HINT });
  }

  async function animateAll() {
    const targets = remaining;
    if (!targets.length) return;
    if (!window.confirm(`Animate ${targets.length} scene${targets.length === 1 ? "" : "s"}? This uses your Anthropic credit and can take a while (roughly 2–8 minutes per scene).`)) return;
    const failures = [];
    for (const [k, s] of targets.entries()) {
      setActiveId(s.id);
      const i = project.scenes.findIndex((x) => x.id === s.id) + 1;
      const payload = await run(`Animating scene ${i} (${k + 1} of ${targets.length}): ${s.title}`, () =>
        api(`/api/projects/${project.id}/scenes/${s.id}/generate`, { method: "POST", json: options }),
        { hint: GENERATE_HINT });
      const after = payload?.project?.scenes?.find((x) => x.id === s.id);
      if (!after || after.status !== "done") failures.push(`${i}. ${s.title || s.id}`);
    }
    if (failures.length) setError(`Finished, but these scenes need a fix: ${failures.join(", ")}. Open each one (red dot) — the error is filled in, so just click “Make new version”.`);
    else setNotice("All scenes animated. Watch each one, request changes where needed, then go to Export.");
  }

  if (!scene) return <p className="muted">No scenes yet.</p>;

  return (
    <div className="animate">
      <aside className="scene-list card">
        <div className="scene-list-head">
          <strong>Scenes</strong>
          <span className="muted">{doneCount}/{project.scenes.length} done</span>
        </div>
        {remaining.length ? (
          <button className="primary full" onClick={animateAll} disabled={busy}>
            Animate all remaining ({remaining.length})
          </button>
        ) : (
          <button className="primary full" onClick={() => setStep("export")} disabled={busy}>Go to Export →</button>
        )}
        <ol>
          {project.scenes.map((s, i) => (
            <li key={s.id}>
              <button className={`scene-item ${s.id === scene.id ? "active" : ""}`} onClick={() => setActiveId(s.id)}>
                <span className={`dot ${s.status}`} title={STATUS_LABEL[s.status]} />
                <span className="scene-item-title">{i + 1}. {s.title || s.id}</span>
                <span className="muted small">{clock(s.audio_seconds)}</span>
              </button>
            </li>
          ))}
        </ol>
        <p className="muted small">Spent so far: {money(project.total_cost_usd)}</p>
      </aside>

      <SceneDetail
        key={scene.id}
        project={project}
        scene={scene}
        index={index}
        settings={settings}
        options={options}
        run={run}
        busy={busy}
        onAnimate={() => animate(scene)}
      />
    </div>
  );
}

function SceneDetail({ project, scene, index, settings, options, run, busy, onAnimate }) {
  const latest = scene.iterations[scene.iterations.length - 1];
  const shownDefault = scene.pinned_iter || latest?.iter || null;
  const [viewIter, setViewIter] = useState(shownDefault);
  const viewing = scene.iterations.find((it) => it.iter === viewIter) || latest;
  const [feedback, setFeedback] = useState("");
  const [feedbackImages, setFeedbackImages] = useState([]);
  const [code, setCode] = useState(viewing?.code || "");
  const base = `/api/projects/${project.id}/scenes/${scene.id}`;

  useEffect(() => setViewIter(shownDefault), [shownDefault]);
  useEffect(() => {
    setCode(viewing?.code || "");
    setFeedback(viewing && !viewing.ok ? errorFeedback(viewing.log_tail) : "");
  }, [viewing?.iter, viewing?.ok]); // eslint-disable-line react-hooks/exhaustive-deps

  const usedInFinal = scene.pinned_iter || [...scene.iterations].reverse().find((it) => it.ok)?.iter;

  async function sendFeedback() {
    if (!feedback.trim()) return;
    const payload = await run(`Making a new version of scene ${index + 1}`, () =>
      api(`${base}/feedback`, {
        method: "POST",
        form: formData(
          { feedback, base_iter: viewing?.iter, model: options.model, effort: options.effort || "", quality: options.quality },
          { images: feedbackImages }
        )
      }), { hint: GENERATE_HINT, done: "New version ready." });
    if (payload) {
      setFeedback("");
      setFeedbackImages([]);
    }
  }

  function pin(iter) {
    run("Saving choice", () => api(`${base}/pin`, { method: "PUT", json: { iter } }),
      { done: iter ? `Version ${iter} will be used in the final video.` : "The newest version will be used in the final video." });
  }

  function replaceAudio(file) {
    if (!file) return;
    if (!window.confirm("Replace this scene's narration with this file? Existing versions stay in the history; you'll need a new version to re-time the animation.")) return;
    run("Transcribing the new recording", () =>
      api(`${base}/recording`, { method: "POST", form: formData({}, { file: [file] }) }),
      { done: "Recording replaced. Use “Request changes” (e.g. “Re-time the animation to the new narration”) to sync the animation." });
  }

  function renderCode() {
    run(`Rendering edited code for scene ${index + 1}`, () =>
      api(`${base}/code`, { method: "POST", json: { code, note: "Code edit", quality: options.quality } }),
      { hint: "Rendering only (no Claude call).", done: "Edited code rendered as a new version." });
  }

  return (
    <section className="scene-detail">
      <div className="card">
        <div className="scene-detail-head">
          <h2>Scene {index + 1}: {scene.title}</h2>
          <span className={`status-pill ${scene.status}`}>{STATUS_LABEL[scene.status]}</span>
        </div>

        {viewing?.ok ? (
          <video key={viewing.video_url} controls src={viewing.video_url} className="player" />
        ) : viewing ? (
          <div className="placeholder bad">
            <p><strong>Version {viewing.iter} didn't render.</strong> The error is filled in below — send it to have Claude fix it.</p>
            <pre className="log">{viewing.log_tail}</pre>
          </div>
        ) : (
          <div className="placeholder">
            <p>This scene hasn't been animated yet.</p>
            <button className="primary big" onClick={onAnimate} disabled={busy}>Animate this scene</button>
            {!scene.audio_url ? <p className="warn">This scene has no audio, so it will be animated to an estimated length.</p> : null}
          </div>
        )}

        {viewing ? (
          <div className="feedback">
            <label className="field">
              <span>
                Request changes to version {viewing.iter}
                {viewing.iter !== latest?.iter ? " (an older version)" : ""}
              </span>
              <textarea
                rows={4}
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
                placeholder="Plain language works best, with times if you can. e.g. “At 0:40, show the arrows one by one as I name them.” “The figure on the right appears too early — show it when I say ‘random policy’.”"
              />
            </label>
            <div className="row">
              <label className="file-button small">
                Attach a screenshot{feedbackImages.length ? ` (${feedbackImages.length})` : ""}
                <input type="file" multiple accept="image/*" hidden onChange={(e) => setFeedbackImages([...(e.target.files || [])])} />
              </label>
              <button className="primary" onClick={sendFeedback} disabled={busy || !feedback.trim()}>Make new version</button>
            </div>
          </div>
        ) : null}
      </div>

      {scene.iterations.length ? (
        <div className="card">
          <h3>Versions</h3>
          <ul className="versions">
            {[...scene.iterations].reverse().map((it) => (
              <li key={it.iter} className={it.iter === viewing?.iter ? "viewing" : ""}>
                <button className="version-main" onClick={() => setViewIter(it.iter)}>
                  <strong>v{it.iter}</strong>
                  <span className={it.ok ? "good" : "bad"}>{it.ok ? "" : " failed"}</span>
                  <span className="version-note">{it.note}</span>
                </button>
                {it.ok ? (
                  it.iter === usedInFinal ? (
                    <span className="in-final">✓ In final video</span>
                  ) : (
                    <button className="link" onClick={() => pin(it.iter)} disabled={busy}>Use in final video</button>
                  )
                ) : null}
              </li>
            ))}
          </ul>
          {scene.pinned_iter ? (
            <button className="link" onClick={() => pin(null)} disabled={busy}>Always use the newest version instead</button>
          ) : null}
        </div>
      ) : null}

      <div className="card">
        <h3>Narration</h3>
        {scene.audio_url ? <audio controls preload="metadata" src={scene.audio_url} /> : <p className="muted">No audio.</p>}
        <details>
          <summary>Transcript</summary>
          <p className="transcript">{scene.narration}</p>
        </details>
        <label className="file-button small">
          Replace this scene's recording
          <input type="file" accept="audio/*,video/mp4,.m4a,.mp3,.wav" hidden onChange={(e) => { replaceAudio(e.target.files?.[0]); e.target.value = ""; }} />
        </label>
      </div>

      <div className="card">
        <h3>Images for this scene</h3>
        <ImageGrid
          images={scene.assets}
          base={`${base}/images`}
          run={run}
          busy={busy}
          emptyText="None. Images added here (or under Review plan → Images for all scenes) can be placed in the animation — mention them in your feedback."
        />
      </div>

      {settings.advanced && viewing ? (
        <div className="card">
          <h3>Edit code (advanced)</h3>
          <p className="muted">Edit the Manim code of version {viewing.iter} and render it as a new version. No Claude call, no cost.</p>
          <textarea className="code" rows={18} value={code} onChange={(e) => setCode(e.target.value)} spellCheck={false} />
          <button onClick={renderCode} disabled={busy || !code.trim()}>Render edited code</button>
        </div>
      ) : null}
    </section>
  );
}
