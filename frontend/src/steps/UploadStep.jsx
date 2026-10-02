import { useEffect, useState } from "react";
import { api, clock, formData, money } from "../api.js";

const AUDIO_ACCEPT = "audio/*,video/mp4,video/quicktime,.m4a,.mp3,.wav,.aac,.ogg,.flac,.mp4,.mov";

// "scene 10" should sort after "scene 9".
const naturalSort = (a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" });

export default function UploadStep({ project, run, setStep, busy }) {
  const [mode, setMode] = useState(project.source === "scenes" ? "scenes" : "recording");
  const [recordingFile, setRecordingFile] = useState(null);
  const [sceneFiles, setSceneFiles] = useState([]);
  const [numbers, setNumbers] = useState([]);
  const [pdfs, setPdfs] = useState([]);
  const [images, setImages] = useState([]);
  const [notes, setNotes] = useState(project.notes || "");

  useEffect(() => setNotes(project.notes || ""), [project.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const hasPlan = project.scenes.length > 0;
  const rec = project.recording;

  function confirmReplan() {
    return !hasPlan || window.confirm(
      "This replaces the current scene plan and scenes. Earlier versions are kept in a backup folder inside the project. Continue?"
    );
  }

  async function uploadRecording() {
    if (!recordingFile) return;
    const payload = await run(
      "Uploading and transcribing your recording",
      () => api(`/api/projects/${project.id}/recording`, { method: "POST", form: formData({}, { file: [recordingFile] }) }),
      { hint: "Transcription usually takes under a minute per 10 minutes of audio.", done: "Recording transcribed. Add optional slides or notes below, then create the scene plan." }
    );
    if (payload) setRecordingFile(null);
  }

  async function planFromRecording() {
    if (!confirmReplan()) return;
    const payload = await run(
      "Planning scenes from your recording",
      () => api(`/api/projects/${project.id}/plan`, {
        method: "POST",
        form: formData({ notes }, { pdfs, images })
      }),
      {
        hint: "Claude reads your transcript and splits it into scenes, then the audio is cut per scene. Usually 1–4 minutes.",
        done: (p) =>
          `Plan ready (${p.project.scenes.length} scenes, ${money(p.cost_usd)}).` +
          (p.uncovered?.length ? ` No audio matched: ${p.uncovered.join(", ")}.` : "")
      }
    );
    if (payload) {
      setPdfs([]);
      setImages([]);
      setStep("plan");
    }
  }

  function pickSceneFiles(list) {
    const files = [...list].sort(naturalSort);
    setSceneFiles(files);
    setNumbers(files.map((_, i) => i + 1));
  }

  async function planFromScenes() {
    if (!sceneFiles.length || !confirmReplan()) return;
    if (new Set(numbers).size !== numbers.length) {
      window.alert("Give every file a different scene number.");
      return;
    }
    const payload = await run(
      `Transcribing ${sceneFiles.length} recordings and planning the scenes`,
      () => api(`/api/projects/${project.id}/scene-recordings`, {
        method: "POST",
        form: formData({ notes, numbers: numbers.join(",") }, { files: sceneFiles, pdfs, images })
      }),
      { hint: "Each file is transcribed, then Claude designs the visuals for each scene. Usually 1–4 minutes.", done: (p) => `Plan ready (${p.project.scenes.length} scenes, ${money(p.cost_usd)}).` }
    );
    if (payload) {
      setSceneFiles([]);
      setNumbers([]);
      setPdfs([]);
      setImages([]);
      setStep("plan");
    }
  }

  return (
    <div className="step-body">
      <section className="card">
        <h2>How is your narration recorded?</h2>
        <div className="choice">
          <button className={`choice-card ${mode === "recording" ? "active" : ""}`} onClick={() => setMode("recording")}>
            <strong>One recording</strong>
            <span>The whole lecture in a single audio or video file. The app splits it into scenes for you.</span>
          </button>
          <button className={`choice-card ${mode === "scenes" ? "active" : ""}`} onClick={() => setMode("scenes")}>
            <strong>One file per scene</strong>
            <span>You recorded each section separately (scene_1.m4a, scene_2.m4a, …). Each file becomes one scene.</span>
          </button>
        </div>
      </section>

      {mode === "recording" ? (
        <section className="card">
          <h2>Your recording</h2>
          {rec ? (
            <div className="recording">
              <div className="muted">{rec.filename} · {clock(rec.duration_seconds)}</div>
              <audio controls src={rec.audio_url} />
              <details>
                <summary>Transcript ({rec.transcript.split(/\s+/).filter(Boolean).length} words)</summary>
                <p className="transcript">{rec.transcript}</p>
              </details>
            </div>
          ) : (
            <p className="muted">MP3, M4A, WAV or MP4 all work. A phone voice memo is fine.</p>
          )}
          <div className="row">
            <input type="file" accept={AUDIO_ACCEPT} onChange={(e) => setRecordingFile(e.target.files?.[0] || null)} />
            <button className={rec ? "" : "primary"} onClick={uploadRecording} disabled={!recordingFile || busy}>
              {rec ? "Replace recording" : "Upload & transcribe"}
            </button>
          </div>
        </section>
      ) : (
        <section className="card">
          <h2>Your scene recordings</h2>
          <p className="muted">Select all the files at once. Check the scene numbers — they're guessed from the file names.</p>
          <input type="file" multiple accept={AUDIO_ACCEPT} onChange={(e) => pickSceneFiles(e.target.files || [])} />
          {sceneFiles.length ? (
            <table className="file-table">
              <thead><tr><th>File</th><th>Scene #</th></tr></thead>
              <tbody>
                {sceneFiles.map((f, i) => (
                  <tr key={f.name + i}>
                    <td>{f.name}</td>
                    <td>
                      <input
                        type="number"
                        min="1"
                        value={numbers[i] ?? i + 1}
                        onChange={(e) => setNumbers((n) => n.map((v, j) => (j === i ? Number(e.target.value) : v)))}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </section>
      )}

      {(mode === "scenes" || rec) ? (
        <section className="card">
          <h2>Optional: help the planner</h2>
          <label className="field">
            <span>Slides or notes (PDF) — the animation will follow their diagrams and terms</span>
            <input type="file" multiple accept="application/pdf" onChange={(e) => setPdfs([...(e.target.files || [])])} />
            {project.reference_files.length ? (
              <span className="muted">Already added: {project.reference_files.join(", ")} (new PDFs replace them)</span>
            ) : null}
          </label>
          <label className="field">
            <span>Images the animation may show (photos, diagrams, logos)</span>
            <input type="file" multiple accept="image/*" onChange={(e) => setImages([...(e.target.files || [])])} />
            {project.shared_assets.length ? (
              <span className="muted">Already added: {project.shared_assets.map((a) => a.name).join(", ")}</span>
            ) : null}
          </label>
          <label className="field">
            <span>Notes for the planner</span>
            <textarea
              rows={3}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="e.g. Audience: first-year students. White background. Keep scenes about 1–2 minutes. Use the photo of me in the intro."
            />
          </label>
          <div className="actions">
            {mode === "recording" ? (
              <button className="primary big" onClick={planFromRecording} disabled={busy}>
                {hasPlan ? "Re-plan scenes" : "Create scene plan →"}
              </button>
            ) : (
              <button className="primary big" onClick={planFromScenes} disabled={busy || !sceneFiles.length}>
                {hasPlan ? "Re-plan from these files" : "Transcribe & create plan →"}
              </button>
            )}
            {hasPlan ? <button onClick={() => setStep("plan")}>Keep current plan →</button> : null}
          </div>
        </section>
      ) : null}
    </div>
  );
}
