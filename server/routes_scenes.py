"""Plan edits, scene animation, feedback rounds, version pinning, and images."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

import llm
import session as sess_mod
from generator import (
    DEFAULT_SCENE_EFFORT,
    DEFAULT_SCENE_MODEL,
    SCENE_EFFORTS,
    SCENE_MODELS,
    build_scene_first_turn,
    build_user_content,
    cost_breakdown,
    format_assets_block,
    format_audio_block,
    generate_scene_code_checked,
    load_few_shot_examples,
)
from renderer import ensure_color_compat, render_scene_code
from server.deps import (
    Clients,
    ai_error,
    anthropic_key,
    load_project,
    plan_scene,
    openai_key,
    require_ai,
    require_scene,
)
from server.serialize import serialize_project

router = APIRouter(prefix="/api/projects/{project_id}")

FEW_SHOT_BLOB = load_few_shot_examples()
RENDER_QUALITIES = ("qh", "qm", "ql")


class PlanSceneEdit(BaseModel):
    id: str
    title: str = ""
    brief: str = ""
    key_visuals: str = ""
    recurring_objects: str = ""


class PlanEdit(BaseModel):
    style: str = ""
    scenes: list[PlanSceneEdit] = Field(default_factory=list)


class GenerateRequest(BaseModel):
    model: str | None = None   # None = default for the keys present (Claude, else GPT)
    effort: str | None = None
    quality: str = "qh"


class CodeEditRequest(BaseModel):
    code: str
    note: str = ""
    quality: str = "qh"


class PinRequest(BaseModel):
    iter: int | None = None    # None = always use the newest version


class CropRequest(BaseModel):
    # Crop rectangle as fractions (0-1) of the image: (x, y) top-left, (w, h) size.
    x: float = 0.0
    y: float = 0.0
    w: float = 1.0
    h: float = 1.0


# ------------------------------------------------------------------ plan


@router.put("/plan")
def edit_plan(project_id: str, request: PlanEdit) -> dict:
    """Edit the visual style and each scene's title/visual description.

    Narration is not editable here: it is the recording, and each scene's audio is
    already cut to it. Scene count/order come from the audio too.
    """
    session = load_project(project_id)
    if not session.plan.get("scenes"):
        raise HTTPException(status_code=400, detail="There is no plan yet.")
    edits = {scene.id: scene for scene in request.scenes}
    for entry in session.plan["scenes"]:
        edit = edits.get(entry.get("id"))
        if edit is None:
            continue
        entry["title"] = edit.title.strip()
        entry["brief"] = edit.brief.strip()
        entry["key_visuals"] = edit.key_visuals.strip()
        entry["recurring_objects"] = edit.recurring_objects.strip()
    session.plan["shared_style"] = request.style.strip()
    sess_mod.save_plan(session, session.plan)
    sess_mod.init_scenes_from_plan(session)
    return {"project": serialize_project(session)}


# ------------------------------------------------------------------ generation helpers


def _slide_pdfs(session: sess_mod.Session) -> list[bytes]:
    att = session.dir / "attachments"
    if not att.is_dir():
        return []
    return [p.read_bytes() for p in sorted(att.glob("slide_*.pdf"))]


def _asset_names(session: sess_mod.Session, scene_id: str) -> list[str]:
    names = [p.name for p in sess_mod.list_shared_assets(session)]
    for p in sess_mod.list_scene_assets(session, scene_id):
        if p.name not in names:
            names.append(p.name)
    return names


def _check_options(model: str | None, effort: str | None, quality: str) -> None:
    if model and model not in SCENE_MODELS.values() and not llm.is_openai(model):
        raise HTTPException(status_code=400, detail="Unknown model.")
    if effort is not None and effort not in SCENE_EFFORTS:
        raise HTTPException(status_code=400, detail=f"Unknown effort `{effort}`.")
    if quality not in RENDER_QUALITIES:
        raise HTTPException(status_code=400, detail=f"Unknown render quality `{quality}`.")


def _render_and_save(
    session: sess_mod.Session,
    scene_id: str,
    *,
    code: str,
    user_turn: list[dict],
    reply: str,
    thinking: str = "",
    usage: dict | None = None,
    cost: dict | None = None,
    quality: str,
    api_request: dict | None = None,
    api_request_full: dict | None = None,
    api_response: dict | None = None,
) -> dict:
    scene = session.scenes[scene_id]
    iter_n = len(scene.iterations) + 1
    # Shim hallucinated color constants before rendering; the shimmed code is also
    # what gets saved, so code.py on disk is exactly the script that rendered.
    safe_code = ensure_color_compat(code)
    result = render_scene_code(safe_code, session.dir, scene_id, iter_n, quality=quality, timeout_seconds=3600)
    iteration = sess_mod.save_iteration(
        session,
        scene_id,
        iter_n=iter_n,
        user_turn=user_turn,
        code=safe_code,
        reply=reply,
        thinking=thinking,
        usage=usage or {},
        cost_breakdown_dict=cost or {"cost_usd": {"total": 0.0}},
        render_log=result.log,
        video_src=result.video_path,
        api_request=api_request,
        api_request_full=api_request_full,
        api_response=api_response,
    )
    # A fresh successful render becomes what the scene shows and stitches.
    if iteration.get("ok"):
        sess_mod.set_selection(session, scene_id, None)
    return iteration


def _run_generation(
    session: sess_mod.Session,
    scene_id: str,
    clients: Clients,
    *,
    conversation: list[dict],
    model: str,
    effort: str | None,
    quality: str,
) -> dict:
    """Send `conversation` (ending in a user turn) to the model and render the result.

    The scene's saved conversation is only replaced once the model has returned code,
    so a failed call leaves the scene's history exactly as it was.
    """
    scene = session.scenes[scene_id]
    previous = scene.conversation
    scene.conversation = conversation
    try:
        generation = generate_scene_code_checked(
            clients.anthropic,
            scene.conversation,
            FEW_SHOT_BLOB,
            session.plan.get("shared_style", ""),
            scene_class_name="MainScene",
            model=model,
            effort=effort,
            openai_client=clients.openai,
        )
    except Exception as exc:  # noqa: BLE001
        scene.conversation = previous
        mapped = ai_error(exc)
        if mapped is None:
            raise
        raise mapped from exc

    if not generation.code.strip():
        scene.conversation = previous
        used = effort or DEFAULT_SCENE_EFFORT
        hint = (
            f" This usually means the model spent its whole budget thinking. Try again with a lower "
            f"effort than `{used}` (Settings → Advanced)."
            if used in ("high", "xhigh", "max") else " Try again."
        )
        raise HTTPException(
            status_code=502,
            detail=f"Claude returned no code for this scene (stop_reason={generation.stop_reason}).{hint}",
        )

    scene.conversation.append({"role": "assistant", "content": generation.full_reply})
    sess_mod.save_conversation(session, scene_id)
    user_turn = scene.conversation[-2]["content"]
    if isinstance(user_turn, str):
        user_turn = [{"type": "text", "text": user_turn}]
    return _render_and_save(
        session,
        scene_id,
        code=generation.code,
        user_turn=user_turn,
        reply=generation.full_reply,
        thinking=generation.thinking_summary,
        usage=generation.usage,
        cost=cost_breakdown(model, generation.usage),
        quality=quality,
        api_request=generation.request_dump,
        api_request_full=generation.request_dump_full,
        api_response=generation.response_dump,
    )


# ------------------------------------------------------------------ scenes


@router.post("/scenes/{scene_id}/generate")
def generate_scene(
    project_id: str,
    scene_id: str,
    request: GenerateRequest,
    anthropic_api_key: str = Depends(anthropic_key),
    openai_api_key: str = Depends(openai_key),
) -> dict:
    """First version of a scene, timed to its slice of the recording."""
    session = load_project(project_id)
    scene = require_scene(session, scene_id)
    _check_options(request.model, request.effort, request.quality)
    clients = require_ai(anthropic_api_key, openai_api_key)
    model = clients.scene_model(request.model, DEFAULT_SCENE_MODEL)
    if scene.conversation:
        raise HTTPException(
            status_code=400,
            detail="This scene already has a version — use “Request changes” to make the next one.",
        )

    entry = plan_scene(session, scene_id)
    index = next(i for i, e in enumerate(session.plan["scenes"], start=1) if e.get("id") == scene_id)
    audio = sess_mod.load_scene_audio_meta(session, scene_id) or {}
    content = build_scene_first_turn(
        scene_index=index,
        title=entry.get("title", ""),
        brief=entry.get("brief", ""),
        key_visuals=entry.get("key_visuals", ""),
        recurring_objects=entry.get("recurring_objects", ""),
        est_seconds=int(entry.get("est_seconds") or 30),
        pdfs=_slide_pdfs(session),
        images=[],
        asset_filenames=_asset_names(session, scene_id),
        narration=audio.get("narration") or entry.get("narration", ""),
        audio_duration=audio.get("duration_seconds"),
        sentences=audio.get("sentences") or [],
    )
    _run_generation(
        session, scene_id, clients,
        conversation=[{"role": "user", "content": content}],
        model=model, effort=request.effort, quality=request.quality,
    )
    return {"project": serialize_project(session)}


@router.post("/scenes/{scene_id}/feedback")
async def request_changes(
    project_id: str,
    scene_id: str,
    feedback: str = Form(...),
    base_iter: int | None = Form(default=None),
    model: str = Form(default=""),
    effort: str | None = Form(default=None),
    quality: str = Form(default="qh"),
    images: list[UploadFile] | None = File(default=None),
    anthropic_api_key: str = Depends(anthropic_key),
    openai_api_key: str = Depends(openai_key),
) -> dict:
    """Make the next version of a scene from plain-language feedback.

    `base_iter` picks which earlier version to revise (default: the latest). The
    scene's current audio timings are re-sent, so feedback after replacing a
    scene's recording re-times the animation to the new audio.
    """
    session = load_project(project_id)
    scene = require_scene(session, scene_id)
    if not feedback.strip():
        raise HTTPException(status_code=400, detail="Describe what should change.")
    effort = effort or None
    _check_options(model, effort, quality)
    clients = require_ai(anthropic_api_key, openai_api_key)
    model = clients.scene_model(model, DEFAULT_SCENE_MODEL)
    if not scene.iterations:
        raise HTTPException(status_code=400, detail="Animate the scene once before requesting changes.")

    latest = len(scene.iterations)
    base = base_iter or latest
    if base < 1 or base > latest:
        raise HTTPException(status_code=400, detail=f"Version {base} doesn't exist.")
    if base == latest and scene.conversation and scene.conversation[-1].get("role") == "assistant":
        # Revising the newest version: keep the whole back-and-forth as context.
        conversation = list(scene.conversation)
    else:
        # Revising an older version: start from the original request plus that
        # version's code, so the model edits exactly what the user is looking at.
        base_code = scene.iterations[base - 1].get("code", "")
        conversation = [
            scene.conversation[0],
            {"role": "assistant", "content": f"```python\n{base_code}\n```"},
        ]

    image_blocks = []
    for upload in images or []:
        data = await upload.read()
        if data:
            mime = upload.content_type or "image/png"
            image_blocks.append((data, "image/jpeg" if mime == "image/jpg" else mime))
    audio = sess_mod.load_scene_audio_meta(session, scene_id) or {}
    text = (
        feedback.strip()
        + format_audio_block(audio.get("narration", ""), audio.get("duration_seconds"), audio.get("sentences") or [])
        + format_assets_block(_asset_names(session, scene_id))
    )
    conversation.append({"role": "user", "content": build_user_content(text, [], image_blocks)})
    _run_generation(session, scene_id, clients, conversation=conversation, model=model, effort=effort, quality=quality)
    return {"project": serialize_project(session)}


@router.post("/scenes/{scene_id}/code")
def render_edited_code(project_id: str, scene_id: str, request: CodeEditRequest) -> dict:
    """Advanced: render hand-edited Manim code as a new version (no Claude call)."""
    session = load_project(project_id)
    scene = require_scene(session, scene_id)
    if not scene.iterations:
        raise HTTPException(status_code=400, detail="Animate the scene once before editing its code.")
    if request.quality not in RENDER_QUALITIES:
        raise HTTPException(status_code=400, detail=f"Unknown render quality `{request.quality}`.")
    code = (request.code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="The code is empty.")
    note = request.note.strip() or "Code edit"
    user_turn = [{"type": "text", "text": f"[manual edit] {note}"}]
    # Keep the conversation in step with the versions so later feedback still works.
    scene.conversation.append({"role": "user", "content": user_turn})
    scene.conversation.append({"role": "assistant", "content": f"[manual edit]\n```python\n{code}\n```"})
    sess_mod.save_conversation(session, scene_id)
    _render_and_save(session, scene_id, code=code, user_turn=user_turn, reply=f"[manual edit] {note}", quality=request.quality)
    return {"project": serialize_project(session)}


@router.put("/scenes/{scene_id}/pin")
def pin_version(project_id: str, scene_id: str, request: PinRequest) -> dict:
    """Choose which version of the scene goes into the final video."""
    session = load_project(project_id)
    require_scene(session, scene_id)
    try:
        sess_mod.set_selection(session, scene_id, "iteration" if request.iter else None, request.iter)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"project": serialize_project(session)}


# ------------------------------------------------------------------ images


async def _read_images(files: list[UploadFile] | None) -> list[tuple[str, bytes]]:
    out = []
    for upload in files or []:
        data = await upload.read()
        if data:
            out.append((Path(upload.filename or "image.png").name, data))
    return out


@router.post("/images")
async def add_shared_images(project_id: str, files: list[UploadFile] | None = File(default=None)) -> dict:
    """Images every scene may place on screen."""
    session = load_project(project_id)
    payloads = await _read_images(files)
    if payloads:
        sess_mod.save_shared_assets(session, payloads)
    return {"project": serialize_project(session)}


@router.delete("/images/{filename}")
def delete_shared_image(project_id: str, filename: str) -> dict:
    session = load_project(project_id)
    if not sess_mod.delete_shared_asset(session, filename):
        raise HTTPException(status_code=404, detail="Image not found.")
    return {"project": serialize_project(session)}


@router.post("/scenes/{scene_id}/images")
async def add_scene_images(project_id: str, scene_id: str, files: list[UploadFile] | None = File(default=None)) -> dict:
    session = load_project(project_id)
    require_scene(session, scene_id)
    for name, data in await _read_images(files):
        sess_mod.save_scene_asset(session, scene_id, name, data)
    return {"project": serialize_project(session)}


@router.delete("/scenes/{scene_id}/images/{filename}")
def delete_scene_image(project_id: str, scene_id: str, filename: str) -> dict:
    session = load_project(project_id)
    require_scene(session, scene_id)
    if not sess_mod.delete_scene_asset(session, scene_id, filename):
        raise HTTPException(status_code=404, detail="Image not found.")
    return {"project": serialize_project(session)}


def _crop_in_place(path: Path, crop: CropRequest) -> None:
    """Crop an image file in place (fractions of width/height) using ffmpeg."""
    if not shutil.which("ffmpeg"):
        raise HTTPException(status_code=500, detail="ffmpeg not found; cannot crop images.")
    x = min(max(crop.x, 0.0), 0.99)
    y = min(max(crop.y, 0.0), 0.99)
    w = min(max(crop.w, 0.02), 1.0 - x)
    h = min(max(crop.h, 0.02), 1.0 - y)
    fd, tmp_name = tempfile.mkstemp(suffix=path.suffix or ".png", dir=str(path.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    vf = f"crop=iw*{w:.6f}:ih*{h:.6f}:iw*{x:.6f}:ih*{y:.6f}"
    proc = subprocess.run(["ffmpeg", "-y", "-i", str(path), "-vf", vf, str(tmp)], capture_output=True, text=True)
    if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Crop failed: {(proc.stderr or '')[-400:]}")
    tmp.replace(path)


@router.post("/images/{filename}/crop")
def crop_shared_image(project_id: str, filename: str, request: CropRequest) -> dict:
    session = load_project(project_id)
    path = sess_mod.shared_assets_dir(session) / Path(filename).name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Image not found.")
    _crop_in_place(path, request)
    return {"project": serialize_project(session)}


@router.post("/scenes/{scene_id}/images/{filename}/crop")
def crop_scene_image(project_id: str, scene_id: str, filename: str, request: CropRequest) -> dict:
    session = load_project(project_id)
    path = sess_mod.scene_assets_dir(session, scene_id) / Path(filename).name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Image not found.")
    _crop_in_place(path, request)
    return {"project": serialize_project(session)}

