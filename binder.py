"""Cut one uploaded narration recording into per-scene audio after planning.

Runs BEFORE any animation exists: it slices the recording into per-scene narration
clips and writes each scene's `audio_meta.json` from the recording's real sentence
timings. The generator then produces animation already synced to the actual voice.

Flow:

    recording.mp3
      │  asr.transcribe → transcript.json (words + sentences)
      ▼
    planner splits the transcript text into scenes (narration verbatim)
      │
      ▼
    aligner.align_scene_spans → each scene's [span_start, span_end] in the upload
      │  ffmpeg atrim slice + shift sentences to scene-local 0
      ▼
    scenes/<id>/narration.mp3 + audio_meta.json

Scenes the speaker never covered (no span found) come back as `covered=False` so the
UI can tell the user which scenes have no audio.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import aligner
import session as sess_mod


# Marker values written into each bound scene's audio_meta so the rest of the app
# (and the user) can tell a scene's audio came from the recording rather than TTS.
RECORDING_VOICE_ID = "recording"
RECORDING_MODEL_ID = "scribe"


@dataclass
class SceneBindReport:
    scene_id: str
    covered: bool
    duration_seconds: float
    span_start: float | None
    span_end: float | None
    score: float
    sentence_count: int
    note: str


def _slice_audio(src: Path, start: float, end: float, out_mp3: Path) -> None:
    """Cut [start, end] out of `src` and write an mp3 to `out_mp3`.

    Output seeking (`-ss`/`-to` after `-i`) is frame-accurate for audio and keeps
    the timestamps aligned with the ASR words, which matters because the sentence
    timings we bake into audio_meta are just shifted copies of those timestamps.
    """
    out_mp3.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-ss", f"{max(0.0, start):.4f}",
        "-to", f"{max(start, end):.4f}",
        "-vn",
        "-c:a", "libmp3lame", "-b:a", "192k",
        str(out_mp3),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not out_mp3.exists():
        raise RuntimeError(f"ffmpeg slice failed for {src.name} [{start:.2f}-{end:.2f}]: {result.stderr[-800:]}")


def bind_recording_to_scenes(
    session: sess_mod.Session,
    *,
    recording_path: Path,
    transcript_data: dict,
    plan_scenes: list[dict],
    only_scene_ids: list[str] | None = None,
) -> list[SceneBindReport]:
    """Slice `recording_path` into per-scene narration + audio_meta for `plan_scenes`.

    `transcript_data` is the ASR dict ({"words", "sentences", ...}) as written to
    master/transcript.json. `plan_scenes` is `session.plan["scenes"]`.

    When `only_scene_ids` is given, alignment still runs over the full plan (so the
    global word-matching stays monotonic), but audio is only written for those
    scenes — used to (re)bind a single scene without touching the rest.
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not on PATH — cannot slice the recording.")
    if not recording_path.exists():
        raise FileNotFoundError(f"recording not found at {recording_path}")

    transcript = aligner.Transcript(
        words=[aligner.TranscriptWord(**w) for w in transcript_data.get("words", [])],
        sentences=[aligner.TranscriptSentence(**s) for s in transcript_data.get("sentences", [])],
    )
    spans = aligner.align_scene_spans(transcript, plan_scenes)
    span_by_id = {s.scene_id: s for s in spans}
    wanted = set(only_scene_ids) if only_scene_ids else None

    reports: list[SceneBindReport] = []
    for scene in plan_scenes:
        sid = scene["id"]
        span = span_by_id.get(sid)
        if wanted is not None and sid not in wanted:
            continue

        if span is None or span.span_start is None or span.new_sentence_start_idx is None:
            reports.append(SceneBindReport(
                scene_id=sid, covered=False, duration_seconds=0.0,
                span_start=None, span_end=None,
                score=(span.score if span else 0.0), sentence_count=0,
                note=(span.note if span else "no alignment produced"),
            ))
            continue

        span_start = float(span.span_start)
        span_end = float(span.span_end)
        scene_sentences = transcript.sentences[
            span.new_sentence_start_idx : span.new_sentence_end_idx + 1
        ]
        # Shift to a scene-local timeline (starts at 0), matching what TTS writes.
        local_sentences = [
            {
                "text": s.text,
                "start": round(s.start - span_start, 3),
                "end": round(s.end - span_start, 3),
            }
            for s in scene_sentences
        ]
        narration_text = " ".join(s.text.strip() for s in scene_sentences).strip()
        duration = round(span_end - span_start, 3)

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            _slice_audio(recording_path, span_start, span_end, tmp_path)
            audio_bytes = tmp_path.read_bytes()
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

        sess_mod.save_scene_narration_audio(
            session,
            sid,
            audio_bytes,
            narration_text=narration_text,
            duration_seconds=duration,
            voice_id=RECORDING_VOICE_ID,
            model_id=RECORDING_MODEL_ID,
            sentences=local_sentences,
        )
        reports.append(SceneBindReport(
            scene_id=sid, covered=True, duration_seconds=duration,
            span_start=round(span_start, 3), span_end=round(span_end, 3),
            score=span.score, sentence_count=len(local_sentences),
            note=span.note,
        ))
    return reports


def bind_report_to_dict(reports: list[SceneBindReport]) -> list[dict]:
    return [
        {
            "scene_id": r.scene_id,
            "covered": r.covered,
            "duration_seconds": r.duration_seconds,
            "span_start": r.span_start,
            "span_end": r.span_end,
            "score": r.score,
            "sentence_count": r.sentence_count,
            "note": r.note,
        }
        for r in reports
    ]
