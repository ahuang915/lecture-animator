"""Title/credits cards, stitching the final video, downloads, and file serving."""

from __future__ import annotations

import io
import mimetypes
import zipfile

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

import cards
import session as sess_mod
from server.deps import load_project, safe_project_path
from server.serialize import serialize_project
from stitcher import stitch_lecture

router = APIRouter(prefix="/api/projects/{project_id}")


class CardsRequest(BaseModel):
    title: str = ""
    subtitle: str = ""
    author: str = ""
    credits: str = ""


@router.put("/cards")
def save_cards(project_id: str, request: CardsRequest) -> dict:
    session = load_project(project_id)
    cards.save_cards(session, request.model_dump())
    return {"project": serialize_project(session)}


@router.post("/stitch")
def stitch(project_id: str) -> dict:
    """Join every scene's chosen version (plus optional title/credits cards) into one MP4."""
    session = load_project(project_id)
    result = stitch_lecture(session, session.plan)
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.log[-1500:])
    return {"project": serialize_project(session), "skipped": result.missing_scene_ids}


@router.get("/export.zip")
def export_zip(project_id: str) -> Response:
    """The final video plus, per scene, the chosen version's video, code and audio."""
    session = load_project(project_id)
    root = (session.name or f"project_{session.id}").strip().replace("/", "-") or session.id
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        final = session.dir / "final" / "lecture.mp4"
        if final.exists():
            archive.write(final, f"{root}/lecture.mp4")
        for entry in session.plan.get("scenes", []):
            sid = entry["id"]
            video = sess_mod.resolve_scene_video(session, sid)
            code = sess_mod.resolve_scene_code(session, sid)
            mp3, meta = sess_mod.scene_narration_paths(session, sid)
            for path, name in ((video, "video.mp4"), (code, "code.py"), (mp3, "narration.mp3"), (meta, "timings.json")):
                if path and path.exists():
                    archive.write(path, f"{root}/scenes/{sid}/{name}")
    if buf.tell() == 0:
        raise HTTPException(status_code=400, detail="Nothing to export yet.")
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{root}.zip"'},
    )


@router.get("/files/{relative_path:path}")
def get_file(project_id: str, relative_path: str, download: bool = False):
    session = load_project(project_id)
    path = safe_project_path(session, relative_path)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if download:
        name = f"{(session.name or 'lecture').strip() or 'lecture'}{path.suffix}"
        return FileResponse(path, media_type=media_type, filename=name)
    return FileResponse(path, media_type=media_type)
