// API helper + settings that live only in this browser (never sent anywhere except
// to this app's own backend, as request headers).

const KEYS = {
  anthropic: "la.anthropicKey",
  elevenlabs: "la.elevenlabsKey",
  openai: "la.openaiKey",
  model: "la.model",
  effort: "la.effort",
  quality: "la.quality",
  advanced: "la.advanced"
};

function read(key, fallback = "") {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

function write(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode: settings just won't persist */
  }
}

export function loadSettings() {
  return {
    anthropicKey: read(KEYS.anthropic),
    elevenlabsKey: read(KEYS.elevenlabs),
    openaiKey: read(KEYS.openai),
    model: read(KEYS.model),
    effort: read(KEYS.effort),
    quality: read(KEYS.quality, "qh"),
    advanced: read(KEYS.advanced) === "1"
  };
}

export function saveSettings(settings) {
  write(KEYS.anthropic, settings.anthropicKey || "");
  write(KEYS.elevenlabs, settings.elevenlabsKey || "");
  write(KEYS.openai, settings.openaiKey || "");
  write(KEYS.model, settings.model || "");
  write(KEYS.effort, settings.effort || "");
  write(KEYS.quality, settings.quality || "qh");
  write(KEYS.advanced, settings.advanced ? "1" : "0");
}

export async function api(path, { method = "GET", json, form } = {}) {
  const settings = loadSettings();
  const headers = {};
  if (settings.anthropicKey) headers["X-Anthropic-Key"] = settings.anthropicKey;
  if (settings.elevenlabsKey) headers["X-ElevenLabs-Key"] = settings.elevenlabsKey;
  if (settings.openaiKey) headers["X-OpenAI-Key"] = settings.openaiKey;
  let body;
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  } else if (form) {
    body = form;
  }
  let response;
  try {
    response = await fetch(path, { method, headers, body });
  } catch {
    throw new Error("Can't reach the app's server. Is it still running?");
  }
  const isJson = (response.headers.get("content-type") || "").includes("application/json");
  const payload = isJson ? await response.json() : null;
  if (!response.ok) {
    const detail = payload?.detail;
    const message = typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : `Request failed (${response.status})`;
    throw new Error(message);
  }
  return payload;
}

export function formData(fields = {}, files = {}) {
  const fd = new FormData();
  for (const [key, value] of Object.entries(fields)) {
    if (value !== undefined && value !== null) fd.append(key, String(value));
  }
  for (const [key, list] of Object.entries(files)) {
    for (const file of list || []) fd.append(key, file);
  }
  return fd;
}

// One AI key (Anthropic or OpenAI) plans/animates; one of ElevenLabs or OpenAI
// transcribes. A single OpenAI key covers both.
export function keysReady(settings) {
  const ai = Boolean(settings.anthropicKey || settings.openaiKey);
  const transcribe = Boolean(settings.elevenlabsKey || settings.openaiKey);
  return ai && transcribe;
}

export function money(value) {
  const n = Number(value || 0);
  return n < 0.01 && n > 0 ? "<$0.01" : `$${n.toFixed(2)}`;
}

export function clock(seconds) {
  if (seconds === null || seconds === undefined) return "—";
  const total = Math.max(0, Math.round(Number(seconds)));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}
