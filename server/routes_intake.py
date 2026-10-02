"""Getting audio in and turning it into a scene plan.

Two ways in, both ending with every scene holding its own slice of the voice
(scenes/<id>/narration.mp3 + audio_meta.json with sentence timings):

  One recording   POST /recording      upload + transcribe
                  POST /plan           Claude splits the transcript into scenes,
                                       then the recording is cut per scene (binder)

  One file/scene  POST /scene-recordings   transcribe each file; each file is one
                                           scene; Claude designs the visuals

Plus POST /scenes/{id}/recording to replace a single scene's audio later (e.g. a
re-recorded section).
"""

from __future__ import annotations

import json
import mimetypes
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

import asr
import binder
import perscene
import planner
import session as sess_mod
from server.deps import (
    ai_error,
    anthropic_key,
    elevenlabs_key,
    load_project,
    openai_key,
    require_ai,
    require_scene,
    require_transcription_keys,
)
from server.serialize import serialize_project

router = APIRouter(prefix="/api/projects/{project_id}")

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _set_source(session: sess_mod.Session, source: str) -> None:
    sess_mod._atomic_write_text(session.dir / "source.txt", source)


async def _save_reference_files(
    session: sess_mod.Session,
    pdfs: list[UploadFile] | None,
    images: list[UploadFile] | None,
) -> None:
    """PDFs become reference slides (sent to the planner and every scene); images
    become shared assets the animation may place on screen. New PDFs replace the
    old set; images are added to the existing pool."""
    pdf_bytes = [data for data in [await f.read() for f in pdfs or []] if data]
    if pdf_bytes:
        sess_mod.save_attachments(session, [], pdf_bytes, [])
    named = []
    for upload in images or []:
        data = await upload.read()
        name = Path(upload.filename or "image.png").name
        if data and Path(name).suffix.lower() in _IMAGE_EXTS:
            named.append((name, data))
    if named:
        sess_mod.save_shared_assets(session, named)


def _reference_pdfs(session: sess_mod.Session) -> list[bytes]:
    att = session.dir / "attachments"
    if not att.is_dir():
        return []
    return [p.read_bytes() for p in sorted(att.glob("*.pdf"))]


def _transcript_text(transcript: dict) -> str:
    return " ".join((s.get("text") or "").strip() for s in transcript.get("sentences", [])).strip()


@router.post("/recording")
async def upload_recording(
    project_id: str,
    file: UploadFile = File(...),
    elevenlabs_api_key: str = Depends(elevenlabs_key),
    openai_api_key: str = Depends(openai_key),
) -> dict:
    """Upload the whole narration recording and transcribe it right away."""
    session = load_project(project_id)
    keys = require_transcription_keys(elevenlabs_api_key, openai_api_key)
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    filename = file.filename or "recording"
    mime = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    suffix = Path(filename).suffix.lower() or ".mp3"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        try:
            duration = asr.probe_duration(tmp_path)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=400,
                detail=f"Couldn't read that file as audio or video — try MP3, M4A, WAV or MP4. ({exc})",
            ) from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    sess_mod.save_master_audio(session, data, filename=filename, mime=mime, duration_seconds=duration)
    audio_path = sess_mod.find_master_audio_file(session)
    try:
        transcript = asr.transcribe_any(audio_path, mime, **keys)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Transcription failed: {exc}") from exc
    (sess_mod.master_dir(session) / "transcript.json").write_text(json.dumps(transcript, indent=2))
    _set_source(session, "recording")
    return {"project": serialize_project(session)}


@router.post("/plan")
async def plan_from_recording(
    project_id: str,
    notes: str = Form(default=""),
    pdfs: list[UploadFile] | None = File(default=None),
    images: list[UploadFile] | None = File(default=None),
    anthropic_api_key: str = Depends(anthropic_key),
    openai_api_key: str = Depends(openai_key),
) -> dict:
    """Split the transcribed recording into scenes, then cut the audio per scene.

    Running it again re-plans from scratch; earlier scenes are moved to
    replaced_scenes/ (never deleted).
    """
    session = load_project(project_id)
    clients = require_ai(anthropic_api_key, openai_api_key)
    transcript_path = sess_mod.master_dir(session) / "transcript.json"
    recording_path = sess_mod.find_master_audio_file(session)
    if not transcript_path.exists() or recording_path is None:
        raise HTTPException(status_code=400, detail="Upload your recording first.")
    transcript = json.loads(transcript_path.read_text())

    await _save_reference_files(session, pdfs, images)
    sess_mod.save_planner_notes(session, notes)
    text = _transcript_text(transcript)
    sess_mod.save_lecture_input(session, text)

    try:
        result = planner.generate_plan(
            clients.anthropic,
            text,
            _reference_pdfs(session),
            [],
            user_notes=notes,
            shared_style_override=session.inherited_shared_style,
            available_assets=[p.name for p in sess_mod.list_shared_assets(session)],
            model=clients.planner_model,
            openai_client=clients.openai,
        )
    except Exception as exc:  # noqa: BLE001
        mapped = ai_error(exc)
        if mapped is None:
            raise
        raise mapped from exc
    if result.request_dump is not None and result.response_dump is not None:
        sess_mod.save_planner_log(session, result.request_dump, result.response_dump)
    if result.plan is None:
        raise HTTPException(status_code=502, detail=result.parse_error or "The planner returned an invalid plan — try again.")

    sess_mod.archive_scenes(session)
    sess_mod.save_plan(session, result.plan)
    sess_mod.init_scenes_from_plan(session)
    try:
        reports = binder.bind_recording_to_scenes(
            session,
            recording_path=recording_path,
            transcript_data=transcript,
            plan_scenes=session.plan.get("scenes", []),
        )
    except (RuntimeError, FileNotFoundError) as exc:
        raise HTTPException(status_code=500, detail=f"Couldn't cut the recording into scenes: {exc}") from exc

    return {
        "project": serialize_project(session),
        "uncovered": [r.scene_id for r in reports if not r.covered],
        "cost_usd": result.cost_usd,
    }


@router.post("/scene-recordings")
async def plan_from_scene_recordings(
    project_id: str,
    files: list[UploadFile] = File(...),
    numbers: str = Form(default=""),
    notes: str = Form(default=""),
    pdfs: list[UploadFile] | None = File(default=None),
    images: list[UploadFile] | None = File(default=None),
    anthropic_api_key: str = Depends(anthropic_key),
    elevenlabs_api_key: str = Depends(elevenlabs_key),
    openai_api_key: str = Depends(openai_key),
) -> dict:
    """One audio file per scene. `numbers` (comma-separated, parallel to `files`)
    gives each file's scene number; when empty, files are taken in upload order."""
    session = load_project(project_id)
    keys = require_transcription_keys(elevenlabs_api_key, openai_api_key)
    clients = require_ai(anthropic_api_key, openai_api_key)

    order = list(range(1, len(files) + 1))
    if numbers.strip():
        try:
            order = [int(part) for part in numbers.split(",") if part.strip()]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Scene numbers must be whole numbers.") from exc
        if len(order) != len(files) or len(set(order)) != len(order) or min(order) < 1:
            raise HTTPException(status_code=400, detail="Give every file a different scene number (1, 2, 3, ...).")

    await _save_reference_files(session, pdfs, images)
    sess_mod.save_planner_notes(session, notes)
    sess_mod.clear_staged_scenes(session)
    for upload, number in zip(files, order):
        data = await upload.read()
        if not data:
            raise HTTPException(status_code=400, detail=f"`{upload.filename}` is empty.")
        filename = upload.filename or f"scene_{number}.mp3"
        mime = upload.content_type or mimetypes.guess_type(filename)[0] or "audio/mpeg"
        try:
            transcript = perscene.transcribe_bytes(data, filename, mime, **keys)
            mp3_bytes = perscene.to_mp3(data, filename)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"Couldn't process `{filename}`: {exc}") from exc
        sess_mod.save_staged_scene_audio(session, number, mp3_bytes=mp3_bytes, transcript=transcript, filename=filename)

    staged = sess_mod.list_staged_scenes(session)
    segments = []
    for st in staged:
        transcript = st.get("transcript") or {}
        segments.append({
            "narration": _transcript_text(transcript),
            "est_seconds": int(round(float(transcript.get("duration_seconds") or 0.0))) or 30,
        })

    try:
        res = planner.plan_from_segments(
            clients.anthropic,
            segments,
            session.inherited_shared_style,
            user_notes=notes,
            pdfs=_reference_pdfs(session),
            available_assets=[p.name for p in sess_mod.list_shared_assets(session)],
            model=clients.planner_model,
            openai_client=clients.openai,
        )
    except Exception as exc:  # noqa: BLE001
        mapped = ai_error(exc)
        if mapped is None:
            raise
        raise mapped from exc
    if not res.scenes:
        raise HTTPException(status_code=502, detail=res.parse_error or "The planner returned an invalid plan — try again.")

    scenes = []
    for i, (seg, visual) in enumerate(zip(segments, res.scenes), start=1):
        scenes.append({
            "id": f"scene_{i:02d}",
            "title": visual.get("title") or f"Scene {i}",
            "brief": visual.get("brief", ""),
            "narration": seg["narration"],
            "key_visuals": visual.get("key_visuals", ""),
            "recurring_objects": visual.get("recurring_objects", ""),
            "est_seconds": visual.get("est_seconds", seg["est_seconds"]),
        })

    sess_mod.archive_scenes(session)
    sess_mod.save_plan(session, {"shared_style": res.shared_style, "scenes": scenes})
    sess_mod.init_scenes_from_plan(session)
    for st, scene in zip(staged, scenes):
        perscene.attach_scene_audio(
            session, scene["id"], transcript=st["transcript"], mp3_bytes=Path(st["mp3_path"]).read_bytes()
        )
    sess_mod.clear_staged_scenes(session)
    _set_source(session, "scenes")
    return {"project": serialize_project(session), "uncovered": [], "cost_usd": res.cost_usd}


@router.post("/scenes/{scene_id}/recording")
async def replace_scene_recording(
    project_id: str,
    scene_id: str,
    file: UploadFile = File(...),
    elevenlabs_api_key: str = Depends(elevenlabs_key),
    openai_api_key: str = Depends(openai_key),
) -> dict:
    """Replace one scene's audio with a new recording (e.g. a re-recorded section).

    Existing versions of the scene stay in its history; regenerate (or send
    feedback) to re-time the animation to the new audio.
    """
    session = load_project(project_id)
    require_scene(session, scene_id)
    keys = require_transcription_keys(elevenlabs_api_key, openai_api_key)
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    filename = file.filename or "recording.mp3"
    mime = file.content_type or mimetypes.guess_type(filename)[0] or "audio/mpeg"
    try:
        transcript = perscene.transcribe_bytes(data, filename, mime, **keys)
        mp3_bytes = perscene.to_mp3(data, filename)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Couldn't process `{filename}`: {exc}") from exc
    perscene.attach_scene_audio(session, scene_id, transcript=transcript, mp3_bytes=mp3_bytes)
    for entry in session.plan.get("scenes", []):
        if entry.get("id") == scene_id:
            entry["narration"] = _transcript_text(transcript)
    sess_mod.save_plan(session, session.plan)
    return {"project": serialize_project(session)}
