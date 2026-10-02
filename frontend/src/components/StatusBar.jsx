import { useEffect, useState } from "react";
import { clock } from "../api.js";

export default function StatusBar({ busy, error, notice, onDismiss }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!busy) return undefined;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [busy]);

  if (busy) {
    return (
      <div className="status busy" role="status">
        <span className="spinner" aria-hidden="true" />
        <div>
          <strong>{busy.label}…</strong>
          <span className="elapsed"> {clock((now - busy.startedAt) / 1000)} elapsed</span>
          {busy.hint ? <div className="hint">{busy.hint}</div> : null}
        </div>
      </div>
    );
  }
  if (error) {
    return (
      <div className="status error" role="alert">
        <div>{error}</div>
        <button className="link" onClick={onDismiss}>Dismiss</button>
      </div>
    );
  }
  if (notice) {
    return (
      <div className="status notice" role="status">
        <div>{notice}</div>
        <button className="link" onClick={onDismiss}>OK</button>
      </div>
    );
  }
  return null;
}
