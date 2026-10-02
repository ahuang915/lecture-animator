import { useRef, useState } from "react";

// Drag-the-edges crop overlay. Reports the crop rectangle as fractions (0-1) of
// the image, so the backend can crop with ffmpeg without pixel math.
export default function CropModal({ url, title, onCancel, onApply, busy }) {
  const stageRef = useRef(null);
  const drag = useRef(null);
  const [rect, setRect] = useState({ x: 0.08, y: 0.08, w: 0.84, h: 0.84 });

  function beginDrag(event, mode) {
    event.preventDefault();
    event.stopPropagation();
    const wrap = stageRef.current.getBoundingClientRect();
    drag.current = { mode, wrap, startX: event.clientX, startY: event.clientY, start: rect };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", endDrag);
  }
  function onMove(event) {
    const d = drag.current;
    if (!d) return;
    const dx = (event.clientX - d.startX) / d.wrap.width;
    const dy = (event.clientY - d.startY) / d.wrap.height;
    const MIN = 0.05;
    let { x, y, w, h } = d.start;
    if (d.mode === "move") {
      x = Math.min(Math.max(0, x + dx), 1 - w);
      y = Math.min(Math.max(0, y + dy), 1 - h);
    } else {
      if (d.mode.includes("l")) { const nx = Math.min(Math.max(0, x + dx), x + w - MIN); w = x + w - nx; x = nx; }
      if (d.mode.includes("r")) { w = Math.max(MIN, Math.min(w + dx, 1 - x)); }
      if (d.mode.includes("t")) { const ny = Math.min(Math.max(0, y + dy), y + h - MIN); h = y + h - ny; y = ny; }
      if (d.mode.includes("b")) { h = Math.max(MIN, Math.min(h + dy, 1 - y)); }
    }
    setRect({ x, y, w, h });
  }
  function endDrag() {
    drag.current = null;
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", endDrag);
  }

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal crop-modal" onClick={(e) => e.stopPropagation()}>
        <h2>Crop {title}</h2>
        <p className="muted">Drag the box or its edges, then Apply.</p>
        <div className="crop-stage" ref={stageRef}>
          <img src={url} alt={title} draggable={false} />
          <div
            className="crop-rect"
            style={{ left: `${rect.x * 100}%`, top: `${rect.y * 100}%`, width: `${rect.w * 100}%`, height: `${rect.h * 100}%` }}
            onPointerDown={(e) => beginDrag(e, "move")}
          >
            {["tl", "tr", "bl", "br", "t", "b", "l", "r"].map((m) => (
              <span key={m} className={`crop-handle crop-h-${m}`} onPointerDown={(e) => beginDrag(e, m)} />
            ))}
          </div>
        </div>
        <div className="modal-actions">
          <button onClick={onCancel} disabled={busy}>Cancel</button>
          <button className="primary" onClick={() => onApply(rect)} disabled={busy}>Apply crop</button>
        </div>
      </div>
    </div>
  );
}
