"""Per-scene narration recordings — attach a separate audio file to each scene.

This is the path for users whose narration is already split by scene (files like
scene_1.mp3, scene_2.mp3, ...). Unlike `binder.py` (which slices ONE master
recording via alignment), here each file already corresponds to one scene, so
there is no cross-scene alignment to get wrong: we transcribe the file on its own
(its timings are already scene-local, starting at 0) and use them directly.

attach_scene_audio() writes narration.mp3 + audio_meta.json (voice_id="recording")
for a scene, so the scene is generated already synced to the recording.

Filename → scene mapping (scene_number_from_filename) pulls the trailing integer
so "scene_1", "scene_01", "narration 1", "3.wav" all resolve to a scene index.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

import asr
import session as sess_mod


RECORDING_VOICE_ID = "recording"
RECORDING_MODEL_ID = "scribe"


_TRAILING_INT_RE = re.compile(r"(\d+)(?!.*\d)")  # last run of digits in the name


def scene_number_from_filename(filename: str) -> int | None:
    """Return the 1-based scene number encoded in an audio filename, or None.

    Uses the LAST group of digits in the stem so "lecture_scene_04.mp3" → 4 and
    "scene_1.wav" → 1. Returns None when the name has no digits.
    """
    stem = Path(filename).stem
    m = _TRAILING_INT_RE.search(stem)
    return int(m.group(1)) if m else None


def transcribe_bytes(
    audio_bytes: bytes, filename: str, mime: str, *, elevenlabs_key: str = "", openai_key: str = ""
) -> dict:
    """Transcribe raw audio bytes (ElevenLabs, else OpenAI Whisper). Returns the ASR dict.

    The returned sentence timings are scene-local (the file starts at 0), so they
    can be written straight into audio_meta without any offset math.
    """
    suffix = Path(filename).suffix.lower() or ".mp3"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = Path(tmp.name)
    try:
        return asr.transcribe_any(tmp_path, mime, elevenlabs_key=elevenlabs_key, openai_key=openai_key)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def to_mp3(audio_bytes: bytes, filename: str) -> bytes:
    """Transcode arbitrary uploaded audio to mp3 bytes.

    narration.mp3 is the canonical per-scene filename the muxer/stitcher read, so
    we normalize wav/m4a/etc. to mp3 rather than trusting the extension. Already-mp3
    input is re-encoded too (cheap) to guarantee a clean, seekable stream.
    """
    src_suffix = Path(filename).suffix.lower() or ".bin"
    with tempfile.NamedTemporaryFile(suffix=src_suffix, delete=False) as src:
        src.write(audio_bytes)
        src_path = Path(src.name)
    out_path = src_path.with_suffix(".out.mp3")
    try:
        cmd = ["ffmpeg", "-y", "-i", str(src_path), "-vn",
               "-c:a", "libmp3lame", "-b:a", "192k", str(out_path)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not out_path.exists():
            raise RuntimeError(f"ffmpeg transcode to mp3 failed: {result.stderr[-800:]}")
        return out_path.read_bytes()
    finally:
        for p in (src_path, out_path):
            try:
                p.unlink()
            except OSError:
                pass


def _sentences_local(transcript: dict) -> list[dict]:
    return [
        {"text": s.get("text", ""), "start": round(float(s["start"]), 3), "end": round(float(s["end"]), 3)}
        for s in transcript.get("sentences", [])
    ]


def attach_scene_audio(session: sess_mod.Session, scene_id: str, *, transcript: dict, mp3_bytes: bytes) -> dict:
    """Write narration.mp3 + audio_meta.json for `scene_id` from a per-scene file.

    Overwrites any existing narration audio for the scene. Returns the audio_meta.
    """
    sentences = _sentences_local(transcript)
    narration = " ".join(s["text"].strip() for s in sentences).strip()
    duration = float(transcript.get("duration_seconds") or (sentences[-1]["end"] if sentences else 0.0))
    meta = sess_mod.save_scene_narration_audio(
        session, scene_id, mp3_bytes,
        narration_text=narration,
        duration_seconds=duration,
        voice_id=RECORDING_VOICE_ID,
        model_id=RECORDING_MODEL_ID,
        sentences=sentences,
    )
    return meta


def _scene_title(session: sess_mod.Session, scene_id: str) -> str:
    for entry in session.plan.get("scenes", []):
        if entry.get("id") == scene_id:
            return entry.get("title", "") or scene_id
    return scene_id
