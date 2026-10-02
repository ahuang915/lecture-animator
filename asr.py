"""ElevenLabs scribe — transcribe an uploaded narration recording with word-level timestamps.

Single entry point: `transcribe(audio_path, mime, api_key) -> dict` returning

    {
      "words":     [{"text", "start", "end"}, ...],
      "sentences": [{"text", "start", "end"}, ...],
      "language_code": "eng",
      "duration_seconds": float,
    }

matching the schema aligner.load_transcript expects. Sentences are derived by
walking the word list and breaking on sentence-ending punctuation in the word
text — scribe returns punctuation attached to the preceding word, so this
groups cleanly without needing the API's "text" field.

Errors from ElevenLabs are translated into plain-English messages (bad key,
quota, network) so the user knows what to do next.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import requests


# ElevenLabs error `detail.status` codes mapped to plain-English reasons. They end up
# in the UI error banner, so they should tell the user what to actually do.
_API_ERROR_REASONS = {
    "invalid_api_key": "The ElevenLabs API key is invalid — re-paste it in Settings.",
    "missing_permissions": "The ElevenLabs API key lacks permission for speech-to-text — check the key's scopes in the ElevenLabs dashboard.",
    "quota_exceeded": "Your ElevenLabs quota for this billing period is used up.",
    "too_many_concurrent_requests": "Too many simultaneous ElevenLabs requests — wait a moment and retry.",
    "rate_limit_exceeded": "ElevenLabs rate limit hit — wait a moment and retry.",
    "detected_unusual_activity": "ElevenLabs flagged this account for unusual activity and suspended free-tier usage.",
}


def _friendly_api_error(resp) -> RuntimeError:
    """Turn an ElevenLabs error response into an actionable, human-readable error."""
    code = ""
    message = ""
    try:
        detail = resp.json().get("detail")
        if isinstance(detail, dict):
            code = str(detail.get("status") or "")
            message = str(detail.get("message") or "")
        elif isinstance(detail, str):
            message = detail
    except ValueError:
        message = resp.text[:300]

    reason = _API_ERROR_REASONS.get(code)
    if reason is None:
        if resp.status_code == 401:
            reason = "ElevenLabs rejected the API key (unauthorized)."
        elif resp.status_code == 422:
            reason = "ElevenLabs rejected the request as invalid (unsupported or corrupt audio file?)."
        elif resp.status_code == 429:
            reason = "ElevenLabs rate or concurrency limit hit — retry shortly."
        elif resp.status_code >= 500:
            reason = "ElevenLabs had a server-side error — retry shortly."
        else:
            reason = f"ElevenLabs returned HTTP {resp.status_code}."

    parts = [reason]
    if message:
        parts.append(f'ElevenLabs said: "{message[:300]}"')
    parts.append(f"[{code or 'no error code'}, HTTP {resp.status_code}]")
    return RuntimeError(" ".join(parts))


def _post_elevenlabs(url: str, *, timeout_seconds: int, **kwargs):
    """requests.post with network failures translated into actionable errors."""
    try:
        return requests.post(url, timeout=timeout_seconds, **kwargs)
    except requests.Timeout as exc:
        raise RuntimeError(
            f"ElevenLabs did not respond within {timeout_seconds}s — the service may be "
            "overloaded or the upload too large. Retry, or try a smaller file."
        ) from exc
    except requests.ConnectionError as exc:
        raise RuntimeError(
            "Could not reach api.elevenlabs.io — check your internet connection (or proxy/VPN) and retry."
        ) from exc


def probe_duration(path: Path) -> float:
    """Return the duration of an audio/video file in seconds via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip()}")
    return float(result.stdout.strip())


DEFAULT_SCRIBE_MODEL = "scribe_v1"


def transcribe(
    audio_path: Path,
    mime: str,
    api_key: str,
    *,
    model_id: str = DEFAULT_SCRIBE_MODEL,
    timeout_seconds: int = 600,
) -> dict:
    """Send the file at `audio_path` to scribe and return word + sentence timings.

    `mime` is the MIME type recorded at upload time (e.g. "audio/mpeg"). The
    timeout is generous because scribe takes ~5–10% of audio duration to run
    server-side, so a 30-minute lecture can spend a couple of minutes there.
    """
    if not api_key:
        raise ValueError("ELEVENLABS_API_KEY is required")
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"audio file not found at {audio_path}")

    url = "https://api.elevenlabs.io/v1/speech-to-text"
    headers = {"xi-api-key": api_key}
    data = {"model_id": model_id}
    with audio_path.open("rb") as fp:
        files = {"file": (audio_path.name, fp, mime or "application/octet-stream")}
        resp = _post_elevenlabs(
            url,
            headers=headers,
            data=data,
            files=files,
            timeout_seconds=timeout_seconds,
        )
    if resp.status_code != 200:
        raise _friendly_api_error(resp)
    try:
        body = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"ElevenLabs scribe returned non-JSON body: {exc}") from exc

    words = _extract_words(body)
    sentences = _words_to_sentences(words)
    duration = max((w["end"] for w in words), default=0.0)

    return {
        "words": words,
        "sentences": sentences,
        "language_code": body.get("language_code") or "",
        "duration_seconds": float(duration),
    }


def _extract_words(body: dict) -> list[dict]:
    """Pull word entries from scribe's response.

    Scribe returns a `words` array where each entry has type ∈ {"word",
    "spacing", "audio_event"}; we keep only the actual words. Some entries
    may lack `start`/`end` if the segment was inaudible — drop those too.
    """
    out: list[dict] = []
    for entry in body.get("words") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") not in (None, "word"):
            continue
        text = (entry.get("text") or "").strip()
        if not text:
            continue
        start = entry.get("start")
        end = entry.get("end")
        if start is None or end is None:
            continue
        out.append({
            "text": text,
            "start": float(start),
            "end": float(end),
        })
    return out


_SENTENCE_END = (".", "!", "?")


def _words_to_sentences(words: list[dict]) -> list[dict]:
    """Group words into sentences by walking forward and splitting on .!? tokens.

    A sentence-ending word is one whose text *ends with* a sentence terminator.
    The recovered sentence text uses the original tokens joined by spaces — not
    necessarily byte-equal to scribe's `text` field, but good enough for the
    aligner, which only needs sentence boundaries and timestamps.
    """
    sentences: list[dict] = []
    buffer: list[dict] = []
    for w in words:
        buffer.append(w)
        last_char = w["text"][-1] if w["text"] else ""
        if last_char in _SENTENCE_END:
            sentences.append(_flush_sentence(buffer))
            buffer = []
    if buffer:
        sentences.append(_flush_sentence(buffer))
    return sentences


def _flush_sentence(buffer: list[dict]) -> dict:
    return {
        "text": " ".join(w["text"] for w in buffer),
        "start": float(buffer[0]["start"]),
        "end": float(buffer[-1]["end"]),
    }


# ------------------------------------------------------------------ OpenAI (Whisper)

OPENAI_ASR_MODEL = "whisper-1"
_OPENAI_CHUNK_SECONDS = 20 * 60   # 20 min of 48 kbps mono mp3 ≈ 7 MB, well under the 25 MB limit


def _norm(token: str) -> str:
    return re.sub(r"[^\w']", "", token.lower())


def _punctuate(words: list[dict], segments: list[dict]) -> list[dict]:
    """Whisper's word timings come without punctuation; its segment text has it.

    Walk the words and the segment tokens together, taking each token's punctuated
    spelling when it matches the word (looking a few tokens ahead to step over
    small disagreements), so sentence splitting on .!? works as with ElevenLabs.
    """
    tokens = [t for seg in segments for t in (seg.get("text") or "").split()]
    out: list[dict] = []
    j = 0
    for w in words:
        text = (w.get("word") or "").strip()
        for k in range(j, min(j + 6, len(tokens))):
            if _norm(tokens[k]) == _norm(text):
                text, j = tokens[k], k + 1
                break
        if text:
            out.append({"text": text, "start": float(w["start"]), "end": float(w["end"])})
    return out


def _as_dict(obj) -> dict:
    return obj if isinstance(obj, dict) else obj.model_dump()


def transcribe_openai(audio_path: Path, api_key: str, *, timeout_seconds: int = 900) -> dict:
    """Transcribe with OpenAI Whisper (word timestamps). Same return shape as transcribe().

    The audio is re-encoded to small mono mp3 and sent in 20-minute chunks to stay
    under OpenAI's 25 MB upload limit; chunk timings are offset back to the original.
    """
    import tempfile

    from openai import OpenAI

    if not api_key:
        raise ValueError("OPENAI_API_KEY is required")
    client = OpenAI(api_key=api_key, timeout=timeout_seconds)
    duration = probe_duration(Path(audio_path))
    words: list[dict] = []
    language = ""
    with tempfile.TemporaryDirectory() as tmp:
        offset = 0.0
        index = 0
        while offset < duration - 0.05:
            chunk = Path(tmp) / f"chunk_{index:03d}.mp3"
            proc = subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-ss", f"{offset:.3f}", "-t", str(_OPENAI_CHUNK_SECONDS),
                 "-i", str(audio_path), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", str(chunk)],
                capture_output=True, text=True,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"Could not prepare audio for OpenAI: {proc.stderr[-300:]}")
            with chunk.open("rb") as fp:
                resp = client.audio.transcriptions.create(
                    model=OPENAI_ASR_MODEL,
                    file=fp,
                    response_format="verbose_json",
                    timestamp_granularities=["word", "segment"],
                )
            data = _as_dict(resp)
            language = language or data.get("language") or ""
            chunk_words = [_as_dict(w) for w in data.get("words") or []]
            segments = [_as_dict(s) for s in data.get("segments") or []]
            for w in _punctuate(chunk_words, segments):
                words.append({"text": w["text"], "start": w["start"] + offset, "end": w["end"] + offset})
            offset += _OPENAI_CHUNK_SECONDS
            index += 1

    return {
        "words": words,
        "sentences": _words_to_sentences(words),
        "language_code": language,
        "duration_seconds": float(max((w["end"] for w in words), default=0.0)),
    }


def transcribe_any(audio_path: Path, mime: str, *, elevenlabs_key: str = "", openai_key: str = "") -> dict:
    """ElevenLabs when its key is set (most precise timings), otherwise OpenAI Whisper."""
    if elevenlabs_key:
        return transcribe(audio_path, mime, elevenlabs_key)
    if openai_key:
        try:
            return transcribe_openai(audio_path, openai_key)
        except Exception as exc:  # noqa: BLE001 — surface OpenAI errors in plain words
            raise RuntimeError(f"OpenAI transcription failed: {exc}") from exc
    raise ValueError("Add an ElevenLabs or OpenAI API key in Settings to transcribe.")
