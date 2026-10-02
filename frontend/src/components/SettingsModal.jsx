import { useState } from "react";
import { api, keysReady, saveSettings } from "../api.js";

const KEY_FIELDS = [
  {
    field: "anthropicKey",
    label: "Anthropic API key",
    note: "Claude — plans and animates (recommended)",
    placeholder: "sk-ant-…",
    link: "https://console.anthropic.com/settings/keys",
    check: "anthropic"
  },
  {
    field: "elevenlabsKey",
    label: "ElevenLabs API key",
    note: "transcribes your recording (most precise timing)",
    placeholder: "sk_…",
    link: "https://elevenlabs.io/app/settings/api-keys",
    check: "elevenlabs"
  },
  {
    field: "openaiKey",
    label: "OpenAI API key",
    note: "can do either job — use it instead of, or alongside, the others",
    placeholder: "sk-…",
    link: "https://platform.openai.com/api-keys",
    check: "openai"
  }
];

export default function SettingsModal({ settings, config, onClose, onChange }) {
  const [draft, setDraft] = useState(settings);
  const [show, setShow] = useState(false);
  const [check, setCheck] = useState(null);
  const [checking, setChecking] = useState(false);

  function update(field, value) {
    setDraft((d) => ({ ...d, [field]: value }));
    setCheck(null);
  }

  function save() {
    saveSettings(draft);
    onChange(draft);
    onClose();
  }

  async function test() {
    saveSettings(draft); // the check reads keys from storage like every request
    onChange(draft);
    setChecking(true);
    try {
      setCheck(await api("/api/keys/check", { method: "POST" }));
    } catch (err) {
      setCheck({ error: err.message });
    } finally {
      setChecking(false);
    }
  }

  // Models for the keys entered: Claude models need an Anthropic key, GPT models an OpenAI key.
  const models = {
    ...(draft.anthropicKey ? config?.scene_models || {} : {}),
    ...(draft.openaiKey ? config?.openai_models || {} : {})
  };
  const defaultModel = draft.anthropicKey ? config?.default_scene_model : config?.default_openai_model;
  const qualities = config?.render_qualities || { "1080p (best)": "qh", "720p (faster)": "qm" };
  const aiWho = draft.anthropicKey ? "Claude (Anthropic)" : draft.openaiKey ? "GPT (OpenAI)" : null;
  const asrWho = draft.elevenlabsKey ? "ElevenLabs" : draft.openaiKey ? "OpenAI Whisper" : null;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Settings">
        <h2>Settings</h2>
        <p className="muted">
          The app uses paid AI services with <strong>your own</strong> API keys, kept only in this browser and sent
          only to this app's server on your computer. You need one key that can <strong>animate</strong> (Anthropic
          or OpenAI) and one that can <strong>transcribe</strong> (ElevenLabs or OpenAI) — a single OpenAI key
          covers both.
        </p>

        {KEY_FIELDS.map((k) => (
          <label className="field" key={k.field}>
            <span>{k.label} <em>({k.note})</em></span>
            <input
              type={show ? "text" : "password"}
              value={draft[k.field] || ""}
              onChange={(e) => update(k.field, e.target.value.trim())}
              placeholder={k.placeholder}
              autoComplete="off"
            />
            {check && !check.error && draft[k.field] ? (
              <span className={check[k.check]?.ok ? "good" : "bad"}>{check[k.check]?.message}</span>
            ) : (
              <a href={k.link} target="_blank" rel="noreferrer">Get a key →</a>
            )}
          </label>
        ))}

        <div className="row">
          <label className="inline">
            <input type="checkbox" checked={show} onChange={(e) => setShow(e.target.checked)} /> Show keys
          </label>
          <button onClick={test} disabled={checking}>{checking ? "Testing…" : "Test keys"}</button>
        </div>
        {check?.error ? <p className="bad">{check.error}</p> : null}
        <p className={keysReady(draft) ? "good" : "muted"}>
          {keysReady(draft)
            ? `Ready: ${aiWho} animates, ${asrWho} transcribes.`
            : `Still needed: ${!aiWho ? "an Anthropic or OpenAI key (to animate)" : ""}${!aiWho && !asrWho ? " and " : ""}${!asrWho ? "an ElevenLabs or OpenAI key (to transcribe)" : ""}.`}
        </p>

        <details className="advanced" open={draft.advanced}>
          <summary onClick={(e) => { e.preventDefault(); update("advanced", !draft.advanced); }}>
            Advanced options {draft.advanced ? "(on)" : ""}
          </summary>
          <p className="muted">
            Turning these on also shows the code editor and "start from a previous project". The defaults work well.
          </p>
          <label className="field">
            <span>Animation model</span>
            <select value={draft.model && models[Object.keys(models).find((l) => models[l] === draft.model)] ? draft.model : defaultModel || ""} onChange={(e) => update("model", e.target.value)}>
              {Object.entries(models).map(([label, id]) => (
                <option key={id} value={id}>{label}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Thinking effort (lower it if a scene comes back with no code)</span>
            <select value={draft.effort} onChange={(e) => update("effort", e.target.value)}>
              <option value="">Default (high)</option>
              {(config?.scene_efforts || []).map((e) => (
                <option key={e} value={e}>{e}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Video quality</span>
            <select value={draft.quality} onChange={(e) => update("quality", e.target.value)}>
              {Object.entries(qualities).map(([label, id]) => (
                <option key={id} value={id}>{label}</option>
              ))}
            </select>
          </label>
        </details>

        <div className="modal-actions">
          <button onClick={onClose}>Cancel</button>
          <button className="primary" onClick={save}>Save</button>
        </div>
      </div>
    </div>
  );
}
