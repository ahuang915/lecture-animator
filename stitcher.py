"""Concatenate per-scene MP4s into a single lecture MP4 via ffmpeg's concat filter.

Uses the concat filter (not the concat demuxer with `-c copy`) so timestamps are
regenerated cleanly across inputs. The demuxer approach is faster but breaks when
inputs have mismatched timebases or container-level start offsets — even when codec,
framerate, and resolution all match — producing an output where only the first scene
plays as video while audio continues for the rest. Manim renders muxed with recorded
audio have exactly this kind of mismatch, so we re-encode every time.

Also produces a downloadable code bundle (one file per scene + a combined script
with renamed classes) so the user can grab the source after a stitched render.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path

import cards
import session as sess_mod
from renderer import video_has_audio


@dataclass
class StitchResult:
    ok: bool
    output_path: Path | None
    log: str
    missing_scene_ids: list[str]   # scenes with no successful iteration (skipped)


def _probe_video_params(path: Path) -> tuple[int, int, float] | None:
    """(width, height, fps) of a video's first stream, or None when unprobeable."""
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height,r_frame_rate",
                "-of", "csv=p=0", str(path),
            ],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        w, h, rate = out.split(",")
        num, _, den = rate.partition("/")
        fps = float(num) / float(den or 1)
        return int(w), int(h), fps
    except (subprocess.CalledProcessError, ValueError, OSError, ZeroDivisionError):
        return None


_CLASS_RENAME_RE = re.compile(r"\bclass\s+MainScene\b")


def _rename_main_scene(code: str, new_name: str) -> str:
    """Rename the `MainScene` class in `code` to `new_name`.

    The few-shot examples and SYSTEM_PROMPT pin every scene's class name to MainScene,
    so a combined file with N scenes would have N classes all named MainScene and only
    the last one would survive. We rewrite the class definition; the rendering pipeline
    targets the class by name so other references are rare, but we also rewrite
    `MainScene(` constructor calls to be safe.
    """
    code = _CLASS_RENAME_RE.sub(f"class {new_name}", code)
    code = re.sub(r"\bMainScene\(", f"{new_name}(", code)
    return code


def bundle_scene_code(session: sess_mod.Session, plan: dict) -> tuple[Path | None, list[str]]:
    """Zip each scene's selected code.py plus a combined script.

    Returns (zip_path, missing_scene_ids). The zip layout is:
      scenes/scene_01.py          # verbatim code from the rendered iteration
      scenes/scene_02.py
      ...
      lecture_combined.py         # all scenes concatenated; classes renamed
                                  # MainScene -> Scene_01, Scene_02, ... so they
                                  # coexist in one file
    """
    final_dir = session.dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    zip_path = final_dir / "lecture_code.zip"

    missing: list[str] = []
    scene_codes: list[tuple[str, str, str]] = []   # (scene_id, class_name, code)
    for entry in plan.get("scenes", []):
        sid = entry["id"]
        code_file = sess_mod.resolve_scene_code(session, sid)
        if code_file is None:
            missing.append(sid)
            continue
        # The scene_id is already zero-padded scene_01, scene_02 (planner convention);
        # for the renamed class we capitalize to Scene_NN.
        class_name = "Scene_" + sid.removeprefix("scene_") if sid.startswith("scene_") else sid.title().replace("_", "")
        scene_codes.append((sid, class_name, code_file.read_text()))

    if not scene_codes:
        return None, missing

    combined = _build_combined_script(scene_codes)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for sid, _class_name, code in scene_codes:
            zf.writestr(f"scenes/{sid}.py", code)
        zf.writestr("lecture_combined.py", combined)
    return zip_path, missing


def _build_combined_script(scene_codes: list[tuple[str, str, str]]) -> str:
    """Stitch per-scene code into one file with unique class names.

    Each scene's full file is appended with its class renamed; we keep the `from manim
    import *` / `import numpy as np` at the top from the first scene only (idempotent
    duplicates would work but are noise) and strip those lines from later scenes.
    """
    header_lines: list[str] = []
    body_chunks: list[str] = []
    seen_imports: set[str] = set()
    for i, (sid, class_name, code) in enumerate(scene_codes):
        renamed = _rename_main_scene(code, class_name)
        kept_lines: list[str] = []
        for line in renamed.splitlines():
            stripped = line.strip()
            if stripped.startswith(("from manim", "import manim", "import numpy", "from numpy")):
                if stripped not in seen_imports:
                    seen_imports.add(stripped)
                    header_lines.append(line)
                continue
            kept_lines.append(line)
        body_chunks.append(
            f"# ===== {sid} -> class {class_name} =====\n" + "\n".join(kept_lines).strip("\n")
        )
    return "\n".join(header_lines) + "\n\n\n" + "\n\n\n".join(body_chunks) + "\n"


def stitch_lecture(session: sess_mod.Session, plan: dict) -> StitchResult:
    if not shutil.which("ffmpeg"):
        return StitchResult(
            ok=False,
            output_path=None,
            log="ffmpeg not found on PATH. Install via `brew install ffmpeg`.",
            missing_scene_ids=[],
        )

    final_dir = session.dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)

    scene_paths: list[tuple[str, Path]] = []
    missing: list[str] = []
    for entry in plan.get("scenes", []):
        sid = entry["id"]
        video = sess_mod.resolve_scene_video(session, sid)
        if video is None:
            missing.append(sid)
            continue
        scene_paths.append((sid, video))

    if not scene_paths:
        return StitchResult(
            ok=False,
            output_path=None,
            log="No scenes have a successful render yet — nothing to stitch.",
            missing_scene_ids=missing,
        )

    # Kept on disk for debugging — humans can inspect the input list even though
    # the concat filter (below) consumes the files via -i directly, not this list.
    concat_list = final_dir / "concat_list.txt"
    concat_list.write_text(
        "\n".join(f"file '{video.as_posix()}'" for _, video in scene_paths) + "\n"
    )

    out_path = final_dir / "lecture.mp4"

    # The concat filter also requires every video input to share the same resolution,
    # SAR and frame rate — it does NOT rescale for you. Scenes can now be a mix of
    # resolutions (e.g. some re-rendered at 1080p60, some still 480p15), so we pick a
    # common target (the largest frame among the scenes, defaulting to 1080p60) and
    # scale+pad+re-fps every input to it before concatenating.
    probed = [p for p in (_probe_video_params(v) for _, v in scene_paths) if p is not None]
    if probed:
        target_w = max(p[0] for p in probed)
        target_h = max(p[1] for p in probed)
        target_fps = max(p[2] for p in probed)
    else:
        target_w, target_h, target_fps = 1920, 1080, 60.0

    def _normalize_v(src_label: str, out_label: str) -> str:
        return (
            f"{src_label}scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,"
            f"fps={target_fps:g},format=yuv420p{out_label}"
        )

    # The concat filter requires every input to declare the same set of streams;
    # if one scene's MP4 has no audio track, declaring [N:a] in the filter graph
    # fails. We synthesize a silent track for those scenes by adding an anullsrc
    # input pair per audio-less scene so the concat n=count line-up still works.
    inputs: list[str] = []
    filter_parts: list[str] = []
    audio_labels: list[str] = []
    video_labels: list[str] = []
    silence_input_indexes: list[int] = []
    for i, (_sid, video) in enumerate(scene_paths):
        inputs += ["-i", str(video)]
        norm_label = f"[nv{i}]"
        filter_parts.append(_normalize_v(f"[{i}:v]", norm_label))
        video_labels.append(norm_label)
        if video_has_audio(video):
            audio_labels.append(f"[{i}:a]")
        else:
            # Placeholder — index assigned after the loop once we know how many
            # real video inputs there are.
            silence_input_indexes.append(i)
            audio_labels.append(None)  # type: ignore[arg-type]

    next_input_index = len(scene_paths)
    for slot in silence_input_indexes:
        inputs += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
        audio_labels[slot] = f"[{next_input_index}:a]"
        next_input_index += 1

    # Optional title / credits cards (cards.py): silent still segments, scaled and
    # padded to the same target as the scenes so the concat streams line up.
    card_notes: list[str] = []

    def still_segment(image: Path, seconds: float, label: str) -> tuple[str, str]:
        nonlocal next_input_index
        img_index = next_input_index
        inputs.extend(["-loop", "1", "-t", f"{seconds}", "-i", str(image)])
        sil_index = img_index + 1
        inputs.extend([
            "-f", "lavfi", "-t", f"{seconds}",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        ])
        next_input_index += 2
        filter_parts.append(
            f"[{img_index}:v]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:color=white,setsar=1,"
            f"fps={target_fps:g},format=yuv420p[{label}]"
        )
        return f"[{label}]", f"[{sil_index}:a]"

    title_image = cards.render_title_card(session)
    if title_image is not None:
        v, a = still_segment(title_image, cards.TITLE_SECONDS, "titlev")
        video_labels.insert(0, v)
        audio_labels.insert(0, a)
        card_notes.append(f"title card ({cards.TITLE_SECONDS:g}s)")
    credits_image = cards.render_credits_card(session)
    if credits_image is not None:
        v, a = still_segment(credits_image, cards.CREDITS_SECONDS, "creditv")
        video_labels.append(v)
        audio_labels.append(a)
        card_notes.append(f"credits card ({cards.CREDITS_SECONDS:g}s)")
    credits_note = f"\n[cards] added {', '.join(card_notes)}." if card_notes else ""

    # Build the filter: [0:v][0:a][1:v][1:a]...concat=n=N:v=1:a=1[v][a]
    pairs = "".join(v + a for v, a in zip(video_labels, audio_labels))
    filter_parts.append(f"{pairs}concat=n={len(video_labels)}:v=1:a=1[v][a]")

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", ";".join(filter_parts),
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    log = (result.stdout or "") + "\n" + (result.stderr or "") + credits_note
    if result.returncode != 0 or not out_path.exists():
        return StitchResult(ok=False, output_path=None, log=log, missing_scene_ids=missing)
    return StitchResult(ok=True, output_path=out_path, log=log, missing_scene_ids=missing)
