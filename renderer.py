"""Render a generated Manim scene with the manim command-line tool.

The path layout is per-scene/per-iteration so scenes and versions don't collide:

    projects/<sid>/scenes/<scene_id>/iterations/v{N}/
      code.py            <- script we write
      render.log         <- stdout+stderr from manim
      video.mp4          <- copied from manim's media dir for stable lookup

Manim's own media tree still lives under projects/<sid>/scenes/<scene_id>/_manim/
but the caller doesn't need to know that.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import subprocess
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
QUALITY_DIR = {"ql": "480p15", "qm": "720p30", "qh": "1080p60"}

# Quality every render uses unless a caller overrides it. Rendering is 100% local
# (no API cost), so we target 1080p60. Dial back to "qm" (720p30) or "ql" (480p15)
# here if the generate→feedback loop feels sluggish on this machine.
DEFAULT_RENDER_QUALITY = "qh"

# Color names Claude habitually borrows from CSS/X11/matplotlib that ManimCE does NOT
# export from `from manim import *` (verified against manim 0.20.1). A hallucinated one
# of these costs a whole render with `NameError: name 'CYAN' is not defined`, so
# ensure_color_compat() injects guarded fallbacks for any that the script references.
FAKE_COLOR_HEXES = {
    "CYAN": "#00FFFF",
    "AQUA": "#00FFFF",
    "MAGENTA": "#FF00FF",
    "FUCHSIA": "#FF00FF",
    "ORANGE_RED": "#FF4500",
    "DARK_ORANGE": "#FF8C00",
    "BROWN": "#A52A2A",
    "LIGHT_BLUE": "#ADD8E6",
    "SKY_BLUE": "#87CEEB",
    "ROYAL_BLUE": "#4169E1",
    "STEEL_BLUE": "#4682B4",
    "NAVY": "#000080",
    "TURQUOISE": "#40E0D0",
    "DARK_GREEN": "#006400",
    "FOREST_GREEN": "#228B22",
    "LIGHT_GREEN": "#90EE90",
    "LIME": "#00FF00",
    "LIME_GREEN": "#32CD32",
    "OLIVE": "#808000",
    "DARK_RED": "#8B0000",
    "CRIMSON": "#DC143C",
    "SALMON": "#FA8072",
    "CORAL": "#FF7F50",
    "HOT_PINK": "#FF69B4",
    "VIOLET": "#EE82EE",
    "INDIGO": "#4B0082",
    "LAVENDER": "#E6E6FA",
    "SILVER": "#C0C0C0",
    "SLATE_GRAY": "#708090",
    "SLATE_GREY": "#708090",
    "KHAKI": "#F0E68C",
    "TAN": "#D2B48C",
    "BEIGE": "#F5F5DC",
}


def ensure_color_compat(code: str) -> str:
    """Inject fallback definitions for non-existent ManimCE color constants.

    Only names the script actually references (and doesn't define itself) are injected,
    right after the manim import. Each definition is guarded with hasattr() so if a
    future manim version adds the constant, the real one wins. Returns the code
    unchanged when nothing risky is referenced — the common case.
    """
    referenced = {
        name: hex_value
        for name, hex_value in FAKE_COLOR_HEXES.items()
        if re.search(rf"\b{name}\b", code) and not re.search(rf"^\s*{name}\s*=", code, re.MULTILINE)
    }
    if not referenced:
        return code

    entries = "\n".join(f'    "{name}": "{hex_value}",' for name, hex_value in referenced.items())
    shim = (
        "# --- injected by renderer: fallbacks for color names this ManimCE build may not define ---\n"
        "import manim as _manim_mod\n"
        "for _color_name, _color_hex in {\n"
        f"{entries}\n"
        "}.items():\n"
        "    if not hasattr(_manim_mod, _color_name):\n"
        "        globals()[_color_name] = _manim_mod.ManimColor(_color_hex)\n"
        "del _manim_mod, _color_name, _color_hex\n"
        "# --- end injected fallbacks ---"
    )

    lines = code.splitlines()
    insert_at = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("from manim import") or stripped.startswith("import manim"):
            insert_at = index + 1
    lines.insert(insert_at, shim)
    return "\n".join(lines) + ("\n" if code.endswith("\n") else "")


def _find_manim_bin() -> str:
    """Resolve the manim executable: next to the running Python, then a local venv,
    then the system PATH (the Docker image installs it system-wide)."""
    candidates = [
        Path(sys.executable).parent / "manim",
        PROJECT_ROOT / ".venv" / "bin" / "manim",
        PROJECT_ROOT / "venv" / "bin" / "manim",
    ]
    for local in candidates:
        if local.exists():
            return str(local)
    found = shutil.which("manim")
    if found:
        return found
    raise RuntimeError(
        "manim binary not found. Install via `pip install manim` or activate the venv."
    )


def _path_with_latex(existing: str) -> str:
    if shutil.which("latex"):
        return existing
    mac_basictex = "/Library/TeX/texbin"
    if Path(mac_basictex, "latex").exists():
        return f"{mac_basictex}:{existing}"
    return existing


@dataclass
class RenderResult:
    ok: bool
    log: str
    video_path: Path | None
    script_path: Path
    mux_ok: bool | None = None  # None = no narration audio existed, so no mux was attempted


def _probe_duration(path: Path) -> float | None:
    """Duration of an audio/video file in seconds via ffprobe, or None if it can't be read."""
    if not shutil.which("ffprobe"):
        return None
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return None
    out = r.stdout.strip()
    if r.returncode != 0 or not out:
        return None
    try:
        return float(out)
    except ValueError:
        return None


def mux_audio_onto_video(video_path: Path, audio_path: Path) -> tuple[bool, str]:
    """Mux `audio_path`'s audio onto `video_path`'s video, making both end at the same time.

    Returns (ok, log_message). Used both at render time and as a standalone re-mux action
    when a scene's audio is generated after the Manim render is already on disk.

    Durations are matched by PADDING the shorter stream, never by truncating the longer
    one (the old `-shortest` clipped whichever was shorter — usually cutting narration off
    mid-sentence). Concretely:
      - audio longer than video → freeze the video's last frame (`tpad`) until the audio ends.
      - video longer than audio → pad the audio with trailing silence (`apad`) to the video.
    The result is one clip whose video and audio tracks are the same length, with no speech
    or animation lost.
    """
    if not shutil.which("ffmpeg"):
        return False, "[mux: ffmpeg not found on PATH]"
    if not video_path.exists():
        return False, f"[mux: video missing at {video_path}]"
    if not audio_path.exists():
        return False, f"[mux: audio missing at {audio_path}]"

    v_dur = _probe_duration(video_path)
    a_dur = _probe_duration(audio_path)

    tmp_out = video_path.with_suffix(".muxed.mp4")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
    ]
    note = ""
    # Pad whichever track is shorter so both end together. Need real durations to know
    # which; if probing failed, fall back to `-shortest` (old behavior) rather than guess.
    if v_dur is not None and a_dur is not None and abs(v_dur - a_dur) > 0.05:
        if a_dur > v_dur:
            # Hold the final video frame until the narration finishes.
            cmd += [
                "-filter_complex", f"[0:v]tpad=stop_mode=clone:stop_duration={a_dur - v_dur:.3f}[v]",
                "-map", "[v]", "-map", "1:a:0",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
            ]
            note = f" [held last frame +{a_dur - v_dur:.2f}s to fit narration]"
        else:
            # Let the animation finish over trailing silence.
            cmd += [
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy",
                "-af", f"apad=pad_dur={v_dur - a_dur:.3f}",
            ]
            note = f" [padded audio +{v_dur - a_dur:.2f}s of silence to fit animation]"
    else:
        cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-shortest"]

    # +faststart moves the moov atom to the front so browser <video> players can
    # begin playback without first fetching the end of the file (otherwise the
    # scene preview shows an unplayable/"no" icon when served without range support).
    cmd += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(tmp_out)]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired as e:
        return False, f"[mux: ffmpeg timed out: {e}]"
    if r.returncode != 0 or not tmp_out.exists():
        return False, f"[mux: ffmpeg failed]\n{r.stderr[-1000:]}"
    tmp_out.replace(video_path)
    return True, f"[mux: audio muxed onto video{note}]"


def video_has_audio(video_path: Path) -> bool:
    """True if ffprobe finds at least one audio stream in the file."""
    if not video_path.exists() or not shutil.which("ffprobe"):
        return False
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=codec_type",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    return r.returncode == 0 and bool(r.stdout.strip())


def _prepare_assets_dir(iter_dir: Path, asset_dirs: list[Path]) -> None:
    """Expose merged asset directories under ./assets/ for the render script.

    Later directories win on filename collisions, which lets scene-specific assets
    override session-level shared assets when needed.
    """
    assets_dir = iter_dir / "assets"
    # A real directory must be rmtree'd; a symlink (or file) is unlinked. Branch on the
    # type rather than catching an exception: on macOS unlink() of a directory raises
    # PermissionError, not IsADirectoryError, so the old except clause never fired and a
    # stale assets/ dir blocked re-renders.
    if assets_dir.is_dir() and not assets_dir.is_symlink():
        shutil.rmtree(assets_dir)
    elif assets_dir.is_symlink() or assets_dir.exists():
        assets_dir.unlink()

    existing_dirs = [d for d in asset_dirs if d.is_dir()]
    if not existing_dirs:
        return

    assets_dir.mkdir(parents=True, exist_ok=True)
    for source_dir in existing_dirs:
        for asset in sorted(source_dir.iterdir()):
            if not asset.is_file() or asset.name.startswith("."):
                continue
            target = assets_dir / asset.name
            if target.is_symlink() or target.exists():
                target.unlink()
            target.symlink_to(asset)


def render_scene_code(
    code: str,
    session_dir: Path,
    scene_id: str,
    iteration: int,
    scene_class_name: str = "MainScene",
    quality: str = DEFAULT_RENDER_QUALITY,
    timeout_seconds: int = 600,
) -> RenderResult:
    """Write code to a per-iteration file, invoke manim, copy the MP4 next to the script."""
    iter_dir = session_dir / "scenes" / scene_id / "iterations" / f"v{iteration}"
    iter_dir.mkdir(parents=True, exist_ok=True)

    script_path = iter_dir / "code.py"
    script_path.write_text(code)

    # Per-scene media root keeps Manim's intermediate files out of the iteration dir.
    media_dir = session_dir / "scenes" / scene_id / "_manim"
    media_dir.mkdir(parents=True, exist_ok=True)

    # Expose both session-level shared assets and scene-specific assets under
    # ./assets/ so generated code can do ImageMobject('./assets/foo.png').
    shared_assets = session_dir / "shared_assets"
    scene_assets = session_dir / "scenes" / scene_id / "assets"
    _prepare_assets_dir(iter_dir, [shared_assets, scene_assets])

    env = os.environ.copy()
    env["PATH"] = _path_with_latex(env.get("PATH", ""))

    cmd = [
        _find_manim_bin(),
        f"-{quality}",
        "--disable_caching",
        "--media_dir", str(media_dir),
        str(script_path),
        scene_class_name,
    ]

    try:
        result = subprocess.run(
            cmd,
            env=env,
            cwd=str(iter_dir),       # so ./assets/ resolves to the symlink above
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as e:
        log = (e.stdout or "") + "\n" + (e.stderr or "")
        log_text = f"[render timed out after {timeout_seconds}s]\n{log}"
        (iter_dir / "render.log").write_text(log_text)
        return RenderResult(ok=False, log=log_text, video_path=None, script_path=script_path)

    log = (result.stdout or "") + "\n" + (result.stderr or "")
    (iter_dir / "render.log").write_text(log)

    if result.returncode != 0:
        return RenderResult(ok=False, log=log, video_path=None, script_path=script_path)

    qdir = QUALITY_DIR.get(quality, "480p15")
    src_mp4 = media_dir / "videos" / script_path.stem / qdir / f"{scene_class_name}.mp4"
    if not src_mp4.exists():
        log_extra = log + f"\n\n[expected MP4 not found at {src_mp4}]"
        (iter_dir / "render.log").write_text(log_extra)
        return RenderResult(ok=False, log=log_extra, video_path=None, script_path=script_path)

    dst_mp4 = iter_dir / "video.mp4"
    dst_mp4.write_bytes(src_mp4.read_bytes())

    # If the scene has a narration mp3, mux it on top of the just-rendered video.
    mux_ok: bool | None = None
    narration_mp3 = session_dir / "scenes" / scene_id / "narration.mp3"
    if narration_mp3.exists():
        mux_ok, mux_log = mux_audio_onto_video(dst_mp4, narration_mp3)
        log += "\n" + mux_log
        (iter_dir / "render.log").write_text(log)

    return RenderResult(ok=True, log=log, video_path=dst_mp4, script_path=script_path, mux_ok=mux_ok)
