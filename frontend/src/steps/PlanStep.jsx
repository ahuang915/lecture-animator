import { useEffect, useState } from "react";
import { api, clock } from "../api.js";
import ImageGrid from "../components/ImageGrid.jsx";

function draftFrom(project) {
  return {
    style: project.style || "",
    scenes: project.scenes.map((s) => ({
      id: s.id,
      title: s.title,
      brief: s.brief,
      key_visuals: s.key_visuals,
      recurring_objects: s.recurring_objects
    }))
  };
}

export default function PlanStep({ project, settings, run, setStep, busy }) {
  const [draft, setDraft] = useState(() => draftFrom(project));
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    setDraft(draftFrom(project));
    setDirty(false);
  }, [project]);

  function editScene(i, field, value) {
    setDraft((d) => ({ ...d, scenes: d.scenes.map((s, j) => (j === i ? { ...s, [field]: value } : s)) }));
    setDirty(true);
  }

  async function save(next) {
    if (dirty) {
      const payload = await run("Saving plan", () => api(`/api/projects/${project.id}/plan`, { method: "PUT", json: draft }));
      if (!payload) return;
    }
    if (next) setStep("animate");
  }

  const totalSeconds = project.scenes.reduce((sum, s) => sum + (s.audio_seconds || 0), 0);
  const missingAudio = project.scenes.filter((s) => !s.audio_url);

  return (
    <div className="step-body">
      <section className="card">
        <h2>Review the plan</h2>
        <p className="muted">
          {project.scenes.length} scenes · {clock(totalSeconds)} of narration. Each scene already has its slice of your
          recording. Adjust what each scene should <em>show</em> — the clearer the description, the better the animation.
          You can also request changes after animating.
        </p>
        {missingAudio.length ? (
          <p className="warn">
            No audio was matched for: {missingAudio.map((s) => s.title || s.id).join(", ")}. These scenes will be
            animated without narration. To fix, re-plan, or upload a recording for that scene in the Animate step.
          </p>
        ) : null}
        <label className="field">
          <span>Visual style (applies to every scene: colors, background, recurring objects, layout)</span>
          <textarea rows={4} value={draft.style} onChange={(e) => { setDraft((d) => ({ ...d, style: e.target.value })); setDirty(true); }} />
        </label>
      </section>

      {project.scenes.map((scene, i) => {
        const d = draft.scenes[i] || {};
        return (
          <section className="card scene-plan" key={scene.id}>
            <div className="scene-plan-head">
              <span className="badge">Scene {i + 1}</span>
              <input
                className="scene-title"
                value={d.title || ""}
                onChange={(e) => editScene(i, "title", e.target.value)}
                aria-label={`Scene ${i + 1} title`}
              />
              <span className="muted">{clock(scene.audio_seconds)}</span>
            </div>
            {scene.audio_url ? <audio controls preload="metadata" src={scene.audio_url} /> : <p className="warn">No audio for this scene.</p>}
            <details>
              <summary>What's said in this scene</summary>
              <p className="transcript">{scene.narration}</p>
            </details>
            <label className="field">
              <span>What this scene shows</span>
              <textarea rows={3} value={d.brief || ""} onChange={(e) => editScene(i, "brief", e.target.value)} />
            </label>
            <label className="field">
              <span>Key visuals</span>
              <input value={d.key_visuals || ""} onChange={(e) => editScene(i, "key_visuals", e.target.value)} />
            </label>
            {settings.advanced ? (
              <label className="field">
                <span>Recurring objects</span>
                <input value={d.recurring_objects || ""} onChange={(e) => editScene(i, "recurring_objects", e.target.value)} />
              </label>
            ) : null}
          </section>
        );
      })}

      <section className="card">
        <h2>Images for all scenes</h2>
        <ImageGrid
          images={project.shared_assets}
          base={`/api/projects/${project.id}/images`}
          run={run}
          busy={busy}
          emptyText="None yet. Add photos, diagrams or logos the animation may place on screen; mention them by name in a scene's description or feedback."
        />
      </section>

      <div className="sticky-actions">
        {dirty ? <button onClick={() => save(false)} disabled={busy}>Save changes</button> : null}
        <button className="primary big" onClick={() => save(true)} disabled={busy}>
          {dirty ? "Save & continue to Animate →" : "Continue to Animate →"}
        </button>
      </div>
    </div>
  );
}
