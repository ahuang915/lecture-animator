"""Shared helpers for the API routes: API keys, project loading, file URLs.

API keys are never stored on the server. The browser keeps them (localStorage) and
sends them on every request as headers:

    X-Anthropic-Key:  sk-ant-...
    X-ElevenLabs-Key: sk_...
    X-OpenAI-Key:     sk-...

An environment variable (ANTHROPIC_API_KEY / ELEVENLABS_API_KEY / OPENAI_API_KEY) is
used only as a fallback, for people who prefer to configure the server that way.

Either AI key works for planning/animating (Claude preferred when both are set), and
either ElevenLabs or OpenAI works for transcription (ElevenLabs preferred).
"""

from __future__ import annotations

import os
from pathlib import Path

import anthropic
from fastapi import Header, HTTPException

import llm
import session as sess_mod
from generator import MODEL as CLAUDE_PLANNER_MODEL


def anthropic_key(x_anthropic_key: str | None = Header(default=None)) -> str:
    return (x_anthropic_key or "").strip() or os.environ.get("ANTHROPIC_API_KEY", "").strip()


def elevenlabs_key(x_elevenlabs_key: str | None = Header(default=None)) -> str:
    return (x_elevenlabs_key or "").strip() or os.environ.get("ELEVENLABS_API_KEY", "").strip()


def openai_key(x_openai_key: str | None = Header(default=None)) -> str:
    return (x_openai_key or "").strip() or os.environ.get("OPENAI_API_KEY", "").strip()


class Clients:
    """The AI clients this request can use (either may be None, not both)."""

    def __init__(self, anthropic_client, openai_client):
        self.anthropic = anthropic_client
        self.openai = openai_client

    @property
    def planner_model(self) -> str:
        return CLAUDE_PLANNER_MODEL if self.anthropic else llm.DEFAULT_OPENAI_MODEL

    def scene_model(self, requested: str | None, default_claude: str) -> str:
        """The requested model if its provider's key is present, else a sensible default."""
        if requested and llm.is_openai(requested) and self.openai:
            return requested
        if requested and not llm.is_openai(requested) and self.anthropic:
            return requested
        return default_claude if self.anthropic else llm.DEFAULT_OPENAI_MODEL


def require_ai(anthropic_api_key: str, openai_api_key: str) -> Clients:
    if not anthropic_api_key and not openai_api_key:
        raise HTTPException(
            status_code=400,
            detail="Add an Anthropic or OpenAI API key in Settings (top right) — one is needed to plan and animate.",
        )
    anthropic_client = require_anthropic_client(anthropic_api_key) if anthropic_api_key else None
    openai_client = None
    if openai_api_key:
        from openai import OpenAI
        openai_client = OpenAI(api_key=openai_api_key, timeout=1800.0)
    return Clients(anthropic_client, openai_client)


def require_transcription_keys(elevenlabs_api_key: str, openai_api_key: str) -> dict:
    if not elevenlabs_api_key and not openai_api_key:
        raise HTTPException(
            status_code=400,
            detail="Add an ElevenLabs or OpenAI API key in Settings (top right) — one is needed to transcribe recordings.",
        )
    return {"elevenlabs_key": elevenlabs_api_key, "openai_key": openai_api_key}


def require_anthropic_client(key: str) -> anthropic.Anthropic:
    if not key:
        raise HTTPException(
            status_code=400,
            detail="Add your Anthropic API key in Settings (top right) — it's needed to plan and animate.",
        )
    # The SDK default timeout (600s) is too short for a long scene: an 11-minute
    # narration has taken ~10 minutes to generate. An overrun would surface as an
    # unhandled connection error, so allow 30 minutes.
    return anthropic.Anthropic(api_key=key, timeout=1800.0)


def require_elevenlabs_key(key: str) -> str:
    if not key:
        raise HTTPException(
            status_code=400,
            detail="Add your ElevenLabs API key in Settings (top right) — it's needed to transcribe recordings.",
        )
    return key


def ai_error(exc: Exception) -> HTTPException | None:
    """Translate an Anthropic/OpenAI SDK error into a message the user can act on.

    Returns None for exceptions that aren't API errors (callers re-raise those).
    """
    if isinstance(exc, llm.MissingKeyError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, anthropic.APIError):
        return claude_error(exc)
    try:
        import openai
    except ImportError:
        return None
    if isinstance(exc, openai.AuthenticationError):
        return HTTPException(status_code=401, detail="OpenAI rejected the API key — re-check it in Settings.")
    if isinstance(exc, openai.RateLimitError):
        message = str(exc)
        if "quota" in message.lower():
            return HTTPException(status_code=402, detail="Your OpenAI account is out of credit or over its quota.")
        return HTTPException(status_code=429, detail="OpenAI rate limit hit — wait a minute and retry.")
    if isinstance(exc, openai.NotFoundError):
        return HTTPException(status_code=400, detail=f"OpenAI doesn't offer that model to this key: {exc}")
    if isinstance(exc, openai.APIConnectionError):
        return HTTPException(status_code=502, detail="Could not reach the OpenAI API — check your internet connection and retry.")
    if isinstance(exc, openai.APIError):
        return HTTPException(status_code=502, detail=f"OpenAI API error: {exc}")
    return None


def claude_error(exc: anthropic.APIError) -> HTTPException:
    """Translate an Anthropic SDK error into a message the user can act on."""
    if isinstance(exc, anthropic.AuthenticationError):
        return HTTPException(status_code=401, detail="Anthropic rejected the API key — re-check it in Settings.")
    if isinstance(exc, anthropic.PermissionDeniedError):
        return HTTPException(status_code=403, detail="This Anthropic API key isn't allowed to use the model.")
    if isinstance(exc, anthropic.RateLimitError):
        return HTTPException(status_code=429, detail="Anthropic rate limit hit — wait a minute and retry.")
    if isinstance(exc, anthropic.APIStatusError):
        message = getattr(exc, "message", str(exc))
        if "credit balance" in message.lower():
            return HTTPException(status_code=402, detail="Your Anthropic account is out of credit — add credit in the Anthropic console.")
        return HTTPException(status_code=exc.status_code or 502, detail=f"Claude API error: {message}")
    if isinstance(exc, anthropic.APIConnectionError):
        return HTTPException(status_code=502, detail="Could not reach the Anthropic API — check your internet connection and retry.")
    return HTTPException(status_code=502, detail=f"Claude API error: {exc}")


def load_project(project_id: str) -> sess_mod.Session:
    session = sess_mod.load_session(project_id.strip())
    if session is None:
        raise HTTPException(status_code=404, detail=f"Project `{project_id}` not found.")
    return session


def require_scene(session: sess_mod.Session, scene_id: str) -> sess_mod.SceneState:
    scene = session.scenes.get(scene_id)
    if scene is None:
        raise HTTPException(status_code=404, detail=f"Scene `{scene_id}` not found.")
    return scene


def plan_scene(session: sess_mod.Session, scene_id: str) -> dict:
    for entry in session.plan.get("scenes", []):
        if entry.get("id") == scene_id:
            return entry
    raise HTTPException(status_code=404, detail=f"Scene `{scene_id}` is not in the plan.")


def file_url(session: sess_mod.Session, path: Path | str | None) -> str | None:
    """URL the browser can load for a file inside the project, cache-busted by mtime."""
    if not path:
        return None
    candidate = Path(path).resolve()
    root = session.dir.resolve()
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.exists():
        return None
    version = int(candidate.stat().st_mtime)
    return f"/api/projects/{session.id}/files/{relative.as_posix()}?v={version}"


def safe_project_path(session: sess_mod.Session, relative_path: str) -> Path:
    candidate = (session.dir / relative_path).resolve()
    root = session.dir.resolve()
    if candidate != root and root not in candidate.parents:
        raise HTTPException(status_code=400, detail="Invalid file path.")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="File not found.")
    return candidate
