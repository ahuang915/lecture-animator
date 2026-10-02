"""Disk-backed session persistence for the lecture-animations app.

Layout (single source of truth for the backend and frontend):

    projects/<sid>/
      name.txt                             # user-facing project name
      lecture_input.txt
      attachments/...
      plan.json                            # {shared_style, scenes:[...]}
      scenes/<scene_id>/
        metadata.json                      # {title, brief, key_visuals, status, ...}
        conversation.json                  # message list sent to Claude for THIS scene
        iterations/v{N}/
          user_turn.json                   # the user content block that produced this iter
          code.py
          reply.md
          thinking.md
          usage.json
          cost.json
          render.log
          video.mp4                        # copied from manim's output dir
        narration.mp3 + audio_meta.json    # this scene's slice of the recording + timings
        selection.json                     # optional: pinned iteration
      master/                              # the uploaded whole recording + transcript
      final/
        concat_list.txt
        lecture.mp4

All JSON writes go to a sibling `*.tmp` then `os.replace()` — atomic on crash.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Where projects live on disk. Docker points this at a mounted volume so projects
# survive container restarts; a local run keeps them in ./projects.
SESSIONS_ROOT = Path(
    os.environ.get("LECTURE_ANIMATOR_DATA") or (Path(__file__).resolve().parent / "projects")
)


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    os.replace(tmp, path)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text())


# --------------------------------------------------------------------- dataclasses

@dataclass
class SceneState:
    id: str
    metadata: dict        # title, brief, key_visuals, recurring_objects, est_seconds, status
    conversation: list    # message list sent to Claude (user/assistant turns)
    iterations: list      # list of dicts (one per render attempt) — see iteration_dict()


@dataclass
class Session:
    id: str
    dir: Path
    plan: dict = field(default_factory=dict)   # {shared_style, scenes:[...]}
    scenes: dict[str, SceneState] = field(default_factory=dict)
    lecture_text: str = ""
    planner_notes: str = ""
    name: str = ""       # user-facing label; the id stays the durable key
    inherited_shared_style: str = ""   # carried from a source session on clone
    cloned_from: dict | None = None     # {"source_id", "source_name"} when cloned


# --------------------------------------------------------------------- session lifecycle

def new_session() -> Session:
    sid = uuid.uuid4().hex[:8]
    sdir = SESSIONS_ROOT / sid
    sdir.mkdir(parents=True, exist_ok=True)
    return Session(id=sid, dir=sdir)


def list_sessions() -> list[str]:
    if not SESSIONS_ROOT.exists():
        return []
    return sorted(p.name for p in SESSIONS_ROOT.iterdir() if p.is_dir())


def load_session_name(sid: str) -> str:
    p = SESSIONS_ROOT / sid / "name.txt"
    try:
        return p.read_text().strip() if p.exists() else ""
    except OSError:
        return ""


def save_session_name(session: Session, name: str) -> None:
    name = (name or "").strip()
    p = session.dir / "name.txt"
    if not name:
        p.unlink(missing_ok=True)
    else:
        _atomic_write_text(p, name)
    session.name = name


def clone_session(source: "Session") -> Session:
    """Start a fresh session pre-seeded from `source` for a lecture series.

    Carries over the style-defining pieces so lecture N+1 matches lecture N:
      - planner_notes (verbatim)
      - every image the source used — its shared assets AND each scene's own
        assets — seeded into the new session's shared pool so any of them can be
        reused (the source often keeps photos per-scene, e.g. a portrait on the
        intro scene; those must become shared to carry across)
      - the source's shared_style, stored as inherited_style.txt so the planner
        reuses it verbatim (see planner.generate_plan's shared_style_override)

    The lecture text, plan, and scenes are NOT copied — the new lecture has its
    own content. Returns the new (empty-content) session.
    """
    new = new_session()

    if source.planner_notes.strip():
        save_planner_notes(new, source.planner_notes)

    # Collect the source's images from both the shared pool and every scene,
    # deduped by filename (first occurrence wins). Unwanted ones can be deleted
    # at intake; the planner only uses images that fit a scene.
    seen: set[str] = set()
    payloads: list[tuple[str, bytes]] = []
    candidate_paths = list(list_shared_assets(source))
    for scene_id in source.scenes:
        candidate_paths.extend(list_scene_assets(source, scene_id))
    for p in candidate_paths:
        if p.name in seen:
            continue
        try:
            payloads.append((p.name, p.read_bytes()))
            seen.add(p.name)
        except OSError:
            continue
    if payloads:
        save_shared_assets(new, payloads)

    style = ((source.plan or {}).get("shared_style") or "").strip()
    if not style:
        style = source.inherited_shared_style.strip()
    if style:
        _atomic_write_text(new.dir / "inherited_style.txt", style)
        new.inherited_shared_style = style

    provenance = {"source_id": source.id, "source_name": source.name}
    _atomic_write_json(new.dir / "cloned_from.json", provenance)
    new.cloned_from = provenance
    return new


def load_session(sid: str) -> Session | None:
    sdir = SESSIONS_ROOT / sid
    if not sdir.is_dir():
        return None
    sess = Session(id=sid, dir=sdir)
    sess.name = load_session_name(sid)

    inherited = sdir / "inherited_style.txt"
    if inherited.exists():
        try:
            sess.inherited_shared_style = inherited.read_text().strip()
        except OSError:
            pass
    cloned_from = sdir / "cloned_from.json"
    if cloned_from.exists():
        try:
            data = _read_json(cloned_from)
            if isinstance(data, dict):
                sess.cloned_from = data
        except (ValueError, OSError):
            pass

    lec = sdir / "lecture_input.txt"
    if lec.exists():
        sess.lecture_text = lec.read_text()

    notes_path = sdir / "planner_notes.txt"
    if notes_path.exists():
        sess.planner_notes = notes_path.read_text()

    plan_path = sdir / "plan.json"
    if plan_path.exists():
        sess.plan = _read_json(plan_path)

    scenes_root = sdir / "scenes"
    if scenes_root.is_dir():
        for scene_dir in sorted(scenes_root.iterdir()):
            if not scene_dir.is_dir():
                continue
            scene_id = scene_dir.name
            meta_path = scene_dir / "metadata.json"
            conv_path = scene_dir / "conversation.json"
            if not meta_path.exists():
                continue
            metadata = _read_json(meta_path)
            conversation = _read_json(conv_path) if conv_path.exists() else []
            iterations = _load_iterations(scene_dir / "iterations")
            sess.scenes[scene_id] = SceneState(
                id=scene_id,
                metadata=metadata,
                conversation=conversation,
                iterations=iterations,
            )
    return sess


def _load_iterations(iter_root: Path) -> list[dict]:
    if not iter_root.is_dir():
        return []
    out: list[dict] = []
    for vdir in sorted(iter_root.iterdir(), key=lambda p: int(p.name[1:]) if p.name.startswith("v") and p.name[1:].isdigit() else 0):
        if not vdir.is_dir() or not vdir.name.startswith("v"):
            continue
        try:
            n = int(vdir.name[1:])
        except ValueError:
            continue
        cost_json = _read_json(vdir / "cost.json") if (vdir / "cost.json").exists() else {}
        it = {
            "iter": n,
            "code": (vdir / "code.py").read_text() if (vdir / "code.py").exists() else "",
            "reply": (vdir / "reply.md").read_text() if (vdir / "reply.md").exists() else "",
            "thinking": (vdir / "thinking.md").read_text() if (vdir / "thinking.md").exists() else "",
            "usage": _read_json(vdir / "usage.json") if (vdir / "usage.json").exists() else {},
            "cost_usd": cost_json.get("cost_usd", {}).get("total", 0.0),
            "log": (vdir / "render.log").read_text() if (vdir / "render.log").exists() else "",
            "video_path": str(vdir / "video.mp4") if (vdir / "video.mp4").exists() else None,
            "ok": (vdir / "video.mp4").exists(),
            "user_turn": _read_json(vdir / "user_turn.json") if (vdir / "user_turn.json").exists() else None,
            "script_path": str(vdir / "code.py"),
            # api_request/response.json stay on disk for debugging but aren't loaded:
            # they can be megabytes each and nothing in the app reads them back.
        }
        out.append(it)
    return out


# --------------------------------------------------------------------- writes

def save_lecture_input(session: Session, text: str) -> None:
    _atomic_write_text(session.dir / "lecture_input.txt", text)
    session.lecture_text = text


def save_planner_notes(session: Session, notes: str) -> None:
    _atomic_write_text(session.dir / "planner_notes.txt", notes or "")
    session.planner_notes = notes or ""


def save_attachments(
    session: Session,
    script_pdfs: list[bytes],
    slide_pdfs: list[bytes],
    images: list[tuple[bytes, str]],
) -> None:
    """Persist uploaded files so a resumed session can re-show / re-attach them.

    Script PDFs (lecture transcript/notes) are digested by the planner once and
    NOT re-sent to per-scene generations — saved with `script_` prefix.
    Slide PDFs (graphics references) ARE re-sent to every scene's first generation
    so the model can match the slide visuals — saved with `slide_` prefix.
    """
    att = session.dir / "attachments"
    if att.exists():
        for path in att.iterdir():
            if path.is_file():
                path.unlink()
    att.mkdir(parents=True, exist_ok=True)
    for i, data in enumerate(script_pdfs, start=1):
        (att / f"script_{i:02d}.pdf").write_bytes(data)
    for i, data in enumerate(slide_pdfs, start=1):
        (att / f"slide_{i:02d}.pdf").write_bytes(data)
    for i, (data, mime) in enumerate(images, start=1):
        ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}.get(mime, "png")
        (att / f"img_{i:02d}.{ext}").write_bytes(data)


def save_plan(session: Session, plan: dict) -> None:
    _atomic_write_json(session.dir / "plan.json", plan)
    session.plan = plan


def save_planner_log(
    session: Session,
    request_dump: dict,
    response_dump: dict,
    request_dump_full: dict | None = None,
) -> None:
    """Dump the planner's exact API request/response under the session root.

    Overwritten on every plan re-run — the planner only runs once per intended plan,
    and seeing the most recent attempt is what's useful for auditing caching.

    `request_dump_full` (optional) is the un-redacted request — full system text and
    full base64 attachments — written to planner_request_full.json so byte-level
    prefix diffs are possible. Can be many MB; gitignored along with the rest of sessions/.
    """
    _atomic_write_json(session.dir / "planner_request.json", request_dump)
    _atomic_write_json(session.dir / "planner_response.json", response_dump)
    if request_dump_full is not None:
        _atomic_write_json(session.dir / "planner_request_full.json", request_dump_full)


def archive_scenes(session: Session) -> Path | None:
    """Move scenes/ aside (to replaced_scenes/<timestamp>/) before a fresh plan.

    Re-planning reuses ids scene_01, scene_02, ... for different content, so old
    renders and conversations must not carry over. Nothing is deleted.
    """
    scenes_root = session.dir / "scenes"
    if not scenes_root.is_dir() or not any(scenes_root.iterdir()):
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = session.dir / "replaced_scenes" / stamp
    dest.parent.mkdir(parents=True, exist_ok=True)
    scenes_root.rename(dest)
    session.scenes = {}
    for name in ("final",):
        old = session.dir / name
        if old.is_dir():
            old.rename(dest / name)
    return dest


def init_scenes_from_plan(session: Session) -> None:
    """Create per-scene metadata.json + empty conversation.json for every scene in the plan.

    Idempotent: existing scenes (with their iterations) are preserved. Only NEW scene ids
    get fresh metadata; scenes that vanished from the plan are left on disk (the UI just
    won't show them) so the user doesn't lose work to a plan edit.
    """
    for entry in session.plan.get("scenes", []):
        sid = entry["id"]
        scene_dir = session.dir / "scenes" / sid
        meta_path = scene_dir / "metadata.json"
        if meta_path.exists():
            existing = _read_json(meta_path)
            existing.update({
                "title": entry.get("title", existing.get("title", "")),
                "brief": entry.get("brief", existing.get("brief", "")),
                "narration": entry.get("narration", existing.get("narration", "")),
                "key_visuals": entry.get("key_visuals", existing.get("key_visuals", "")),
                "recurring_objects": entry.get("recurring_objects", existing.get("recurring_objects", "")),
                "est_seconds": entry.get("est_seconds", existing.get("est_seconds", 30)),
            })
            _atomic_write_json(meta_path, existing)
            if sid in session.scenes:
                session.scenes[sid].metadata = existing
            continue

        metadata = {
            "id": sid,
            "title": entry.get("title", ""),
            "brief": entry.get("brief", ""),
            "narration": entry.get("narration", ""),
            "key_visuals": entry.get("key_visuals", ""),
            "recurring_objects": entry.get("recurring_objects", ""),
            "est_seconds": entry.get("est_seconds", 30),
            "status": "pending",
        }
        _atomic_write_json(meta_path, metadata)
        _atomic_write_json(scene_dir / "conversation.json", [])
        session.scenes[sid] = SceneState(id=sid, metadata=metadata, conversation=[], iterations=[])


def scene_assets_dir(session: Session, scene_id: str) -> Path:
    return session.dir / "scenes" / scene_id / "assets"


def shared_assets_dir(session: Session) -> Path:
    return session.dir / "shared_assets"


def list_shared_assets(session: Session) -> list[Path]:
    d = shared_assets_dir(session)
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_file() and not p.name.startswith("."))


def save_shared_assets(session: Session, assets: list[tuple[str, bytes]]) -> list[Path]:
    """Add/overwrite shared assets by filename; existing ones are left in place.

    Additive (not replace-all) so re-generating the plan without re-attaching
    doesn't wipe assets, and so cloned/inherited assets survive intake.
    """
    d = shared_assets_dir(session)
    d.mkdir(parents=True, exist_ok=True)

    saved: list[Path] = []
    for filename, data in assets:
        safe = Path(filename).name or "asset.png"
        dest = d / safe
        dest.write_bytes(data)
        saved.append(dest)
    return saved


def delete_shared_asset(session: Session, filename: str) -> bool:
    safe = Path(filename).name
    dest = shared_assets_dir(session) / safe
    if not dest.is_file():
        return False
    dest.unlink()
    return True


def list_scene_assets(session: Session, scene_id: str) -> list[Path]:
    d = scene_assets_dir(session, scene_id)
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_file() and not p.name.startswith("."))


def save_scene_asset(session: Session, scene_id: str, filename: str, data: bytes) -> Path:
    """Save one uploaded asset under the scene's assets/ dir; return its path."""
    d = scene_assets_dir(session, scene_id)
    d.mkdir(parents=True, exist_ok=True)
    safe = Path(filename).name  # strip any path components
    dest = d / safe
    dest.write_bytes(data)
    return dest


def delete_scene_asset(session: Session, scene_id: str, filename: str) -> bool:
    d = scene_assets_dir(session, scene_id)
    target = d / Path(filename).name
    if target.exists() and target.is_file():
        target.unlink()
        return True
    return False


def scene_narration_paths(session: Session, scene_id: str) -> tuple[Path, Path]:
    """(mp3_path, meta_json_path) for the scene's narration audio. May not exist yet."""
    scene_dir = session.dir / "scenes" / scene_id
    return scene_dir / "narration.mp3", scene_dir / "audio_meta.json"


def load_scene_audio_meta(session: Session, scene_id: str) -> dict | None:
    mp3, meta = scene_narration_paths(session, scene_id)
    if not (mp3.exists() and meta.exists()):
        return None
    return _read_json(meta)


def save_scene_narration_audio(
    session: Session,
    scene_id: str,
    audio_bytes: bytes,
    *,
    narration_text: str,
    duration_seconds: float,
    voice_id: str,
    model_id: str,
    sentences: list[dict] | None = None,
) -> dict:
    """Persist narration.mp3 + audio_meta.json under scenes/<sid>/. Returns the meta dict.

    `sentences` is a list of {text, start, end} from the recording's transcript. The
    generator gives Claude these per-sentence timestamps so the animation pacing
    tracks the voice-over.
    """
    mp3_path, meta_path = scene_narration_paths(session, scene_id)
    mp3_path.parent.mkdir(parents=True, exist_ok=True)
    mp3_path.write_bytes(audio_bytes)
    meta = {
        "narration": narration_text,
        "duration_seconds": float(duration_seconds),
        "voice_id": voice_id,
        "model_id": model_id,
        "sentences": sentences or [],
    }
    _atomic_write_json(meta_path, meta)
    return meta


# --------------------------------------------------------------------- scene selection

# Which cut of a scene is "final" — for the preview player, the export zip, and
# the stitched lecture. Exactly one pointer per scene, stored at
# scenes/<sid>/selection.json:
#
#     {"kind": "iteration", "ref": 9}              pin iterations/v9
#
# Absent file = default: the latest iteration with a rendered video. Everything
# that needs "the scene's video/code" goes through resolve_scene_video() /
# resolve_scene_code() so there is exactly one precedence rule in the codebase.

SELECTION_KINDS = ("iteration",)


def selection_path(session: Session, scene_id: str) -> Path:
    return session.dir / "scenes" / scene_id / "selection.json"


def load_selection(session: Session, scene_id: str) -> dict | None:
    """The scene's explicit selection {"kind", "ref"}, or None for the default."""
    p = selection_path(session, scene_id)
    if not p.exists():
        return None
    try:
        data = _read_json(p)
    except (ValueError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    kind = data.get("kind")
    ref = data.get("ref")
    if kind == "iteration" and isinstance(ref, int) and ref > 0:
        return {"kind": kind, "ref": ref}
    return None


def set_selection(
    session: Session, scene_id: str, kind: str | None, ref: int | str | None = None
) -> None:
    """Pin the scene to one iteration, or clear back to the default (latest render).

    Passing kind=None removes selection.json so the scene resolves to its latest
    rendered iteration. Raises ValueError when the target doesn't exist on disk.
    """
    p = selection_path(session, scene_id)
    if kind is None:
        if p.exists():
            p.unlink()
        return
    if kind == "iteration":
        if not isinstance(ref, int) or ref < 1:
            raise ValueError(f"iteration selection needs a positive iteration number, got {ref!r}")
        vdir = session.dir / "scenes" / scene_id / "iterations" / f"v{ref}"
        if not (vdir / "video.mp4").exists():
            raise ValueError(f"iteration v{ref} for scene {scene_id!r} has no rendered video")
    else:
        raise ValueError(f"unknown selection kind: {kind!r}")
    _atomic_write_json(p, {"kind": kind, "ref": ref})


def _iteration_dirs_desc(session: Session, scene_id: str) -> list[Path]:
    iter_root = session.dir / "scenes" / scene_id / "iterations"
    if not iter_root.is_dir():
        return []
    vdirs = [
        d for d in iter_root.iterdir()
        if d.is_dir() and d.name.startswith("v") and d.name[1:].isdigit()
    ]
    vdirs.sort(key=lambda d: int(d.name[1:]), reverse=True)
    return vdirs


def resolve_scene_video(session: Session, scene_id: str) -> Path | None:
    """The video this scene contributes to previews/stitch, honoring the selection.

    A selection whose target has gone missing on disk falls through to the
    default rather than failing the whole stitch.
    """
    sel = load_selection(session, scene_id)
    if sel is not None:
        candidate = (
            session.dir / "scenes" / scene_id / "iterations" / f"v{sel['ref']}" / "video.mp4"
        )
        if candidate.exists():
            return candidate
    for vdir in _iteration_dirs_desc(session, scene_id):
        candidate = vdir / "video.mp4"
        if candidate.exists():
            return candidate
    return None


def resolve_scene_code(session: Session, scene_id: str) -> Path | None:
    """The code.py matching resolve_scene_video()."""
    sel = load_selection(session, scene_id)
    if sel is not None and sel["kind"] == "iteration":
        vdir = session.dir / "scenes" / scene_id / "iterations" / f"v{sel['ref']}"
        if (vdir / "video.mp4").exists() and (vdir / "code.py").exists():
            return vdir / "code.py"
    for vdir in _iteration_dirs_desc(session, scene_id):
        if (vdir / "video.mp4").exists() and (vdir / "code.py").exists():
            return vdir / "code.py"
    return None


def master_dir(session: Session) -> Path:
    return session.dir / "master"


def find_master_audio_file(session: Session) -> Path | None:
    """Return the uploaded master audio file path, regardless of extension.

    Stored as `master/upload.<ext>` where ext is whatever the user uploaded
    (mp3, wav, m4a, ...). Returns None if no upload exists yet.
    """
    d = master_dir(session)
    if not d.is_dir():
        return None
    for path in d.iterdir():
        if path.is_file() and path.stem == "upload":
            return path
    return None


def master_audio_meta_path(session: Session) -> Path:
    return master_dir(session) / "meta.json"


def load_master_audio_meta(session: Session) -> dict | None:
    """Return the master audio's meta dict, or None if nothing uploaded."""
    meta = master_audio_meta_path(session)
    if not meta.exists():
        return None
    try:
        return _read_json(meta)
    except (ValueError, OSError):
        return None


def save_master_audio(
    session: Session,
    audio_bytes: bytes,
    *,
    filename: str,
    mime: str,
    duration_seconds: float,
) -> dict:
    """Persist an uploaded narration recording under master/. Returns the meta dict.

    Any previous upload is removed — a new upload restarts the workflow from scratch.
    """
    d = master_dir(session)
    if d.exists():
        for path in d.iterdir():
            if path.is_file():
                path.unlink()
    d.mkdir(parents=True, exist_ok=True)

    safe_name = Path(filename).name or "upload"
    ext = Path(safe_name).suffix.lower() or ".mp3"
    audio_path = d / f"upload{ext}"
    audio_path.write_bytes(audio_bytes)

    meta = {
        "filename": safe_name,
        "mime": mime,
        "duration_seconds": float(duration_seconds),
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_write_json(master_audio_meta_path(session), meta)
    return meta


def clear_master_audio(session: Session) -> None:
    """Remove the master audio + meta. No-op if nothing was uploaded."""
    d = master_dir(session)
    if not d.exists():
        return
    for path in d.iterdir():
        if path.is_file():
            path.unlink()


# --------------------------------------------------------------------- staged per-scene audio
#
# Per-scene recordings uploaded at intake are parked under master/staged/ until the
# plan is built from them. Each staged clip is a normalized mp3
# plus its transcript, keyed by the scene number parsed from the filename.

def staged_dir(session: Session) -> Path:
    return master_dir(session) / "staged"


def save_staged_scene_audio(
    session: Session, number: int, *, mp3_bytes: bytes, transcript: dict, filename: str
) -> None:
    """Park a per-scene recording + its transcript for a not-yet-planned scene."""
    d = staged_dir(session)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"scene_{number:02d}.mp3").write_bytes(mp3_bytes)
    _atomic_write_json(d / f"scene_{number:02d}.json", {
        "number": int(number),
        "filename": filename,
        "transcript": transcript,
    })


def list_staged_scenes(session: Session) -> list[dict]:
    """Staged clips sorted by number: [{number, filename, transcript, mp3_path}]."""
    d = staged_dir(session)
    if not d.is_dir():
        return []
    out: list[dict] = []
    for meta_path in sorted(d.glob("scene_*.json")):
        try:
            rec = _read_json(meta_path)
        except (ValueError, OSError):
            continue
        mp3 = meta_path.with_suffix(".mp3")
        if not mp3.exists():
            continue
        rec["mp3_path"] = mp3
        out.append(rec)
    out.sort(key=lambda r: r.get("number", 0))
    return out


def clear_staged_scenes(session: Session) -> None:
    d = staged_dir(session)
    if not d.is_dir():
        return
    for p in d.iterdir():
        if p.is_file():
            p.unlink()


def clear_scene_narration_audio(session: Session, scene_id: str) -> None:
    mp3, meta = scene_narration_paths(session, scene_id)
    for p in (mp3, meta):
        if p.exists():
            p.unlink()


def save_scene_metadata(session: Session, scene_id: str) -> None:
    scene = session.scenes[scene_id]
    _atomic_write_json(session.dir / "scenes" / scene_id / "metadata.json", scene.metadata)


def save_conversation(session: Session, scene_id: str) -> None:
    scene = session.scenes[scene_id]
    _atomic_write_json(session.dir / "scenes" / scene_id / "conversation.json", scene.conversation)


def save_iteration(
    session: Session,
    scene_id: str,
    *,
    iter_n: int,
    user_turn: list[dict],
    code: str,
    reply: str,
    thinking: str,
    usage: dict,
    cost_breakdown_dict: dict,
    render_log: str,
    video_src: Path | None,
    api_request: dict | None = None,
    api_response: dict | None = None,
    api_request_full: dict | None = None,
) -> dict:
    """Persist one iteration's artifacts and return the dict appended to scene.iterations.

    `api_request` / `api_response` are the (redacted) Claude API payloads for this
    iteration — they get dumped to api_request.json / api_response.json so you can
    audit cache breakpoints and exactly what went over the wire.

    `api_request_full` (optional) is the un-redacted request — full system text and
    full base64 attachments — written to api_request_full.json. Useful for byte-level
    prefix diffs to confirm a cache miss is TTL-driven, not content-drift driven.
    """
    vdir = session.dir / "scenes" / scene_id / "iterations" / f"v{iter_n}"
    vdir.mkdir(parents=True, exist_ok=True)

    _atomic_write_json(vdir / "user_turn.json", user_turn)
    _atomic_write_text(vdir / "code.py", code)
    _atomic_write_text(vdir / "reply.md", reply)
    _atomic_write_text(vdir / "thinking.md", thinking)
    _atomic_write_json(vdir / "usage.json", usage)
    _atomic_write_json(vdir / "cost.json", cost_breakdown_dict)
    _atomic_write_text(vdir / "render.log", render_log)
    if api_request is not None:
        _atomic_write_json(vdir / "api_request.json", api_request)
    if api_response is not None:
        _atomic_write_json(vdir / "api_response.json", api_response)
    if api_request_full is not None:
        _atomic_write_json(vdir / "api_request_full.json", api_request_full)

    video_dst = vdir / "video.mp4"
    if video_src and Path(video_src).exists():
        video_dst.write_bytes(Path(video_src).read_bytes())

    ok = video_dst.exists()

    it = {
        "iter": iter_n,
        "code": code,
        "reply": reply,
        "thinking": thinking,
        "usage": usage,
        "cost_usd": cost_breakdown_dict.get("cost_usd", {}).get("total", 0.0),
        "log": render_log,
        "video_path": str(video_dst) if ok else None,
        "ok": ok,
        "user_turn": user_turn,
        "script_path": str(vdir / "code.py"),
        "api_request": api_request,
        "api_response": api_response,
    }
    session.scenes[scene_id].iterations.append(it)

    # update status
    meta = session.scenes[scene_id].metadata
    meta["status"] = "rendered" if ok else "failed"
    meta["current_iter"] = iter_n
    save_scene_metadata(session, scene_id)

    return it
