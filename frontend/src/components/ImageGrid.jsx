import { useState } from "react";
import { api, formData } from "../api.js";
import CropModal from "./CropModal.jsx";

// Thumbnails with add / crop / delete. `base` is the API path for this image set:
//   /api/projects/<id>/images                 (shared by every scene)
//   /api/projects/<id>/scenes/<sid>/images    (this scene only)
export default function ImageGrid({ images, base, run, busy, emptyText }) {
  const [cropping, setCropping] = useState(null);

  function add(files) {
    if (!files.length) return;
    run("Adding images", () => api(base, { method: "POST", form: formData({}, { files }) }), { done: "Images added." });
  }
  function remove(name) {
    if (!window.confirm(`Remove ${name}?`)) return;
    run("Removing image", () => api(`${base}/${encodeURIComponent(name)}`, { method: "DELETE" }));
  }
  async function crop(rect) {
    const target = cropping;
    const payload = await run("Cropping", () =>
      api(`${base}/${encodeURIComponent(target.name)}/crop`, { method: "POST", json: rect }));
    if (payload) setCropping(null);
  }

  return (
    <div>
      {images.length ? (
        <ul className="image-grid">
          {images.map((img) => (
            <li key={img.name}>
              <img src={img.url} alt={img.name} />
              <span className="image-name" title={img.name}>{img.name}</span>
              <span className="image-actions">
                <button className="link" onClick={() => setCropping(img)} disabled={busy}>Crop</button>
                <button className="link danger" onClick={() => remove(img.name)} disabled={busy}>Remove</button>
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">{emptyText}</p>
      )}
      <label className="file-button">
        + Add images
        <input type="file" multiple accept="image/*" hidden onChange={(e) => { add([...(e.target.files || [])]); e.target.value = ""; }} />
      </label>
      {cropping ? (
        <CropModal url={cropping.url} title={cropping.name} busy={busy} onCancel={() => setCropping(null)} onApply={crop} />
      ) : null}
    </div>
  );
}
