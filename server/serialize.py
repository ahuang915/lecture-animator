"""Turn on-disk project state into the JSON the frontend renders."""

from __future__ import annotations

import json
from typing import Any

import cards
import session as sess_mod
from server.deps import file_url


def _request_text(user_turn: Any) -> str:
    """The human-readable part of the prompt that produced an iteration.

    For feedback iterations this is the user's feedback; the audio/asset blocks
    appended after a `---` separator are dropped.
    """
    if not isinstance(user_turn, list):
        return ""
    for block in user_turn:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text") or ""
            return text.split("\n---\n", 1)[0].strip()
    return ""


def _log_tail(log: str, lines: int = 40) -> str:
    return "\n".join((log or "").splitlines()[-lines:])


def serialize_iteration(session: sess_mod.Session, iteration: dict, first: bool) -> dict:
    request = _request_text(iteration.get("user_turn"))
    if first:
        kind, note = "generate", "First version"
    elif request.startswith("[manual edit]"):
        kind, note = "code", request.removeprefix("[manual edit]").strip() or "Code edit"
    else:
        kind, note = "feedback", request
    return {
        "iter": iteration.get("iter"),
        "ok": bool(iteration.get("ok")),
        "kind": kind,
        "note": note,
        "cost_usd": iteration.get("cost_usd", 0.0),
        "video_url": file_url(session, iteration.get("video_path")),
        "code": iteration.get("code", ""),
        "log_tail": "" if iteration.get("ok") else _log_tail(iteration.get("log", "")),
    }


def serialize_scene(session: sess_mod.Session, scene_id: str, entry: dict) -> dict:
    scene = session.scenes.get(scene_id)
    audio_meta = sess_mod.load_scene_audio_meta(session, scene_id)
    mp3_path, _ = sess_mod.scene_narration_paths(session, scene_id)
    iterations = [
        serialize_iteration(session, it, first=(i == 0))
        for i, it in enumerate(scene.iterations if scene else [])
    ]
    selection = sess_mod.load_selection(session, scene_id)
    display = sess_mod.resolve_scene_video(session, scene_id)

    if not iterations:
        status = "ready" if audio_meta else "no-audio"
    elif iterations[-1]["ok"]:
        status = "done"
    else:
        status = "failed"

    return {
        "id": scene_id,
        "title": entry.get("title", ""),
        "brief": entry.get("brief", ""),
        "key_visuals": entry.get("key_visuals", ""),
        "recurring_objects": entry.get("recurring_objects", ""),
        "narration": (audio_meta or {}).get("narration") or entry.get("narration", ""),
        "audio_url": file_url(session, mp3_path) if audio_meta else None,
        "audio_seconds": (audio_meta or {}).get("duration_seconds"),
        "status": status,
        "iterations": iterations,
        "pinned_iter": selection["ref"] if selection else None,
        "video_url": file_url(session, display),
        "assets": [
            {"name": p.name, "url": file_url(session, p)}
            for p in sess_mod.list_scene_assets(session, scene_id)
        ],
        "cost_usd": sum(it["cost_usd"] or 0.0 for it in iterations),
    }


def _recording(session: sess_mod.Session) -> dict | None:
    meta = sess_mod.load_master_audio_meta(session)
    if meta is None:
        return None
    transcript_path = sess_mod.master_dir(session) / "transcript.json"
    text = ""
    if transcript_path.exists():
        try:
            transcript = json.loads(transcript_path.read_text())
            text = " ".join(s.get("text", "") for s in transcript.get("sentences", [])).strip()
        except (OSError, ValueError):
            pass
    return {
        "filename": meta.get("filename"),
        "duration_seconds": meta.get("duration_seconds"),
        "audio_url": file_url(session, sess_mod.find_master_audio_file(session)),
        "transcript": text,
    }


def _source(session: sess_mod.Session) -> str | None:
    marker = session.dir / "source.txt"
    try:
        return marker.read_text().strip() or None
    except OSError:
        return None


def serialize_project(session: sess_mod.Session) -> dict:
    plan_scenes = session.plan.get("scenes", []) if session.plan else []
    scenes = [serialize_scene(session, entry["id"], entry) for entry in plan_scenes if entry.get("id")]
    final_video = session.dir / "final" / "lecture.mp4"
    attachments = session.dir / "attachments"
    return {
        "id": session.id,
        "name": session.name,
        "source": _source(session),            # "recording" | "scenes" | None
        "notes": session.planner_notes,
        "recording": _recording(session),
        "style": (session.plan or {}).get("shared_style", "") or session.inherited_shared_style,
        "inherited_style": bool(session.inherited_shared_style),
        "cloned_from": session.cloned_from,
        "scenes": scenes,
        "shared_assets": [
            {"name": p.name, "url": file_url(session, p)} for p in sess_mod.list_shared_assets(session)
        ],
        "reference_files": sorted(p.name for p in attachments.iterdir()) if attachments.is_dir() else [],
        "cards": cards.load_cards(session),
        "final_video_url": file_url(session, final_video),
        "total_cost_usd": sum(s["cost_usd"] for s in scenes),
    }


def project_summary(sid: str) -> dict:
    sdir = sess_mod.SESSIONS_ROOT / sid
    plan_path = sdir / "plan.json"
    scene_count = 0
    if plan_path.exists():
        try:
            scene_count = len(json.loads(plan_path.read_text()).get("scenes", []))
        except (OSError, ValueError):
            pass
    # A few cheap probes instead of walking every render on disk.
    probes = [sdir, plan_path, sdir / "scenes", sdir / "final" / "lecture.mp4", sdir / "name.txt"]
    updated = max(p.stat().st_mtime for p in probes if p.exists())
    return {
        "id": sid,
        "name": sess_mod.load_session_name(sid),
        "scene_count": scene_count,
        "has_video": (sdir / "final" / "lecture.mp4").exists(),
        "updated_at": updated,
    }
