"""Projects (list / create / rename / delete / start-from-previous) and settings."""

from __future__ import annotations

import shutil

import anthropic
import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import session as sess_mod
from generator import DEFAULT_SCENE_MODEL, SCENE_EFFORTS, SCENE_MODELS
import llm
from server.deps import anthropic_key, elevenlabs_key, load_project, openai_key
from server.serialize import project_summary, serialize_project

router = APIRouter(prefix="/api")


class NameRequest(BaseModel):
    name: str = ""


@router.get("/config")
def get_config() -> dict:
    return {
        "scene_models": SCENE_MODELS,
        "openai_models": llm.OPENAI_MODELS,
        "default_scene_model": DEFAULT_SCENE_MODEL,
        "default_openai_model": llm.DEFAULT_OPENAI_MODEL,
        "scene_efforts": list(SCENE_EFFORTS),
        "render_qualities": {"1080p (best)": "qh", "720p (faster)": "qm"},
    }


def _check_anthropic(key: str) -> dict:
    if key.startswith("sk_") and not key.startswith("sk-ant-"):
        return {"ok": False, "message": "This looks like an ElevenLabs key — paste it in the ElevenLabs field instead."}
    try:
        anthropic.Anthropic(api_key=key, timeout=20.0).models.list(limit=1)
        return {"ok": True, "message": "Key works."}
    except anthropic.AuthenticationError:
        return {"ok": False, "message": "Anthropic rejected this key — check it's copied completely, or create a new one."}
    except anthropic.PermissionDeniedError:
        return {"ok": False, "message": "This key isn't allowed to use the API (check its workspace permissions)."}
    except anthropic.APIConnectionError:
        return {"ok": False, "message": "Could not reach Anthropic — check your internet connection."}
    except anthropic.APIError as exc:
        return {"ok": False, "message": f"Could not verify: {exc}"}


def _check_elevenlabs(key: str) -> dict:
    """Probe speech-to-text itself (the only ElevenLabs feature this app uses).

    A request with no audio file is rejected for missing input *after* the key and
    its speech-to-text permission are checked, so it costs nothing and also works
    for restricted keys that can't read account or model info.
    """
    if key.startswith("sk-ant-"):
        return {"ok": False, "message": "This looks like an Anthropic key — paste it in the Anthropic field instead."}
    try:
        resp = requests.post(
            "https://api.elevenlabs.io/v1/speech-to-text",
            headers={"xi-api-key": key},
            data={"model_id": "scribe_v1"},
            timeout=20,
        )
    except requests.RequestException:
        return {"ok": False, "message": "Could not reach ElevenLabs — check your internet connection."}
    try:
        detail = resp.json().get("detail")
    except ValueError:
        detail = None
    status = detail.get("status") if isinstance(detail, dict) else ""
    if status == "invalid_parameters" or resp.status_code in (400, 422) and status not in ("invalid_api_key", "missing_permissions"):
        return {"ok": True, "message": "Key works."}
    if status == "missing_permissions":
        return {"ok": False, "message": "The key is valid but doesn't allow speech-to-text — enable it for this key in ElevenLabs."}
    if status == "invalid_api_key" or resp.status_code == 401:
        return {"ok": False, "message": "ElevenLabs rejected this key — check it's copied completely, or create a new one."}
    if status == "quota_exceeded":
        return {"ok": False, "message": "The key works, but your ElevenLabs quota is used up."}
    return {"ok": False, "message": f"ElevenLabs returned HTTP {resp.status_code}."}


def _check_openai(key: str) -> dict:
    if key.startswith("sk-ant-"):
        return {"ok": False, "message": "This looks like an Anthropic key — paste it in the Anthropic field instead."}
    try:
        from openai import OpenAI
        import openai

        OpenAI(api_key=key, timeout=20.0).models.list()
        return {"ok": True, "message": "Key works."}
    except openai.AuthenticationError:
        return {"ok": False, "message": "OpenAI rejected this key — check it's copied completely, or create a new one."}
    except openai.PermissionDeniedError:
        # Restricted keys may not list models but can still call them.
        return {"ok": True, "message": "Key accepted (restricted key — model listing not allowed)."}
    except openai.APIConnectionError:
        return {"ok": False, "message": "Could not reach OpenAI — check your internet connection."}
    except openai.APIError as exc:
        return {"ok": False, "message": f"Could not verify: {exc}"}


@router.post("/keys/check")
def check_keys(
    anthropic_api_key: str = Depends(anthropic_key),
    elevenlabs_api_key: str = Depends(elevenlabs_key),
    openai_api_key: str = Depends(openai_key),
) -> dict:
    """Validate the entered keys with free calls (no model usage, no transcription)."""
    missing = {"ok": False, "message": "No key entered."}
    return {
        "anthropic": _check_anthropic(anthropic_api_key) if anthropic_api_key else missing,
        "elevenlabs": _check_elevenlabs(elevenlabs_api_key) if elevenlabs_api_key else missing,
        "openai": _check_openai(openai_api_key) if openai_api_key else missing,
    }


@router.get("/projects")
def list_projects() -> dict:
    rows = [project_summary(sid) for sid in sess_mod.list_sessions()]
    rows.sort(key=lambda r: r["updated_at"], reverse=True)
    return {"projects": rows}


@router.post("/projects")
def create_project(request: NameRequest) -> dict:
    session = sess_mod.new_session()
    sess_mod.save_session_name(session, request.name or "Untitled project")
    return {"project": serialize_project(session)}


@router.post("/projects/{source_id}/clone")
def clone_project(source_id: str, request: NameRequest) -> dict:
    """Start a new project that reuses a previous project's visual style, notes and images."""
    source = load_project(source_id)
    new = sess_mod.clone_session(source)
    base = source.name or source.id
    sess_mod.save_session_name(new, request.name or f"{base} (next)")
    return {"project": serialize_project(new)}


@router.get("/projects/{project_id}")
def get_project(project_id: str) -> dict:
    return {"project": serialize_project(load_project(project_id))}


@router.put("/projects/{project_id}/name")
def rename_project(project_id: str, request: NameRequest) -> dict:
    session = load_project(project_id)
    sess_mod.save_session_name(session, request.name)
    return {"project": serialize_project(session)}


@router.delete("/projects/{project_id}")
def delete_project(project_id: str) -> dict:
    session = load_project(project_id)
    root = sess_mod.SESSIONS_ROOT.resolve()
    target = session.dir.resolve()
    if target.parent != root:
        raise HTTPException(status_code=400, detail="Refusing to delete outside the projects folder.")
    shutil.rmtree(target)
    return {"deleted": project_id}
