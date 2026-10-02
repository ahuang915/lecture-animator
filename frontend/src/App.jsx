import { useCallback, useEffect, useState } from "react";
import { api, keysReady, loadSettings } from "./api.js";
import Home from "./components/Home.jsx";
import SettingsModal from "./components/SettingsModal.jsx";
import StatusBar from "./components/StatusBar.jsx";
import UploadStep from "./steps/UploadStep.jsx";
import PlanStep from "./steps/PlanStep.jsx";
import AnimateStep from "./steps/AnimateStep.jsx";
import ExportStep from "./steps/ExportStep.jsx";

const STEPS = [
  { id: "upload", label: "1. Upload audio" },
  { id: "plan", label: "2. Review plan" },
  { id: "animate", label: "3. Animate" },
  { id: "export", label: "4. Export" }
];

function startingStep(project) {
  if (!project?.scenes?.length) return "upload";
  if (project.final_video_url) return "export";
  if (project.scenes.some((s) => s.iterations.length)) return "animate";
  return "plan";
}

export default function App() {
  const [settings, setSettings] = useState(loadSettings);
  const [config, setConfig] = useState(null);
  const [project, setProject] = useState(null);
  const [step, setStep] = useState("upload");
  const [busy, setBusy] = useState(null); // {label, hint, startedAt}
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [showSettings, setShowSettings] = useState(false);

  const keysMissing = !keysReady(settings);

  useEffect(() => {
    api("/api/config").then(setConfig).catch((e) => setError(e.message));
    if (!keysReady(settings)) setShowSettings(true);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Run one backend action with a shared busy/error/notice treatment. `fn` returns
  // the API payload; a returned `project` replaces the open project.
  const run = useCallback(async (label, fn, { hint = "", done = "" } = {}) => {
    setBusy({ label, hint, startedAt: Date.now() });
    setError("");
    setNotice("");
    try {
      const payload = await fn();
      if (payload?.project) setProject(payload.project);
      if (done) setNotice(typeof done === "function" ? done(payload) : done);
      return payload;
    } catch (err) {
      setError(err.message);
      return null;
    } finally {
      setBusy(null);
    }
  }, []);

  function openProject(p) {
    setProject(p);
    setStep(startingStep(p));
    setError("");
    setNotice("");
  }

  const canOpen = (id) => id === "upload" || Boolean(project?.scenes?.length);
  const shared = { project, settings, config, run, setStep, setError, setNotice, busy: Boolean(busy) };

  return (
    <div className="app">
      <header className="topbar">
        <button className="brand" onClick={() => setProject(null)} title="All projects">
          Lecture Animator
        </button>
        {project ? (
          <nav className="steps" aria-label="Steps">
            {STEPS.map((s) => (
              <button
                key={s.id}
                className={`step ${step === s.id ? "active" : ""}`}
                disabled={!canOpen(s.id)}
                onClick={() => setStep(s.id)}
              >
                {s.label}
              </button>
            ))}
          </nav>
        ) : (
          <span />
        )}
        <button className={`settings-btn ${keysMissing ? "attention" : ""}`} onClick={() => setShowSettings(true)}>
          {keysMissing ? "⚠ Add API keys" : "Settings"}
        </button>
      </header>

      <main className="content">
        {!project ? (
          <Home onOpen={openProject} run={run} busy={Boolean(busy)} setError={setError} />
        ) : (
          <>
            <ProjectTitle project={project} run={run} onBack={() => setProject(null)} />
            {step === "upload" && <UploadStep {...shared} />}
            {step === "plan" && <PlanStep {...shared} />}
            {step === "animate" && <AnimateStep {...shared} />}
            {step === "export" && <ExportStep {...shared} />}
          </>
        )}
      </main>

      <StatusBar busy={busy} error={error} notice={notice} onDismiss={() => { setError(""); setNotice(""); }} />

      {showSettings && (
        <SettingsModal
          settings={settings}
          config={config}
          onClose={() => setShowSettings(false)}
          onChange={setSettings}
        />
      )}
    </div>
  );
}

function ProjectTitle({ project, run, onBack }) {
  const [name, setName] = useState(project.name);
  useEffect(() => setName(project.name), [project.id, project.name]);
  function save() {
    if (name.trim() && name !== project.name) {
      run("Renaming", () => api(`/api/projects/${project.id}/name`, { method: "PUT", json: { name } }));
    }
  }
  return (
    <div className="project-title">
      <button className="link" onClick={onBack}>← All projects</button>
      <input
        className="title-input"
        value={name}
        onChange={(e) => setName(e.target.value)}
        onBlur={save}
        onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
        aria-label="Project name"
      />
    </div>
  );
}
