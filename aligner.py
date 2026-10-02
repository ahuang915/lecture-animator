"""Find where each planned scene lives inside ONE uploaded narration recording.

align_scene_spans() does a coarse word-level alignment (no LLM): it tokenizes the
concatenated planned narrations and the ASR transcript, then walks
difflib.SequenceMatcher's matching blocks to assign each planned word a position in
the transcript. Each scene's audio span is read off as (earliest matched word start,
latest matched word end). Robust to small drift; degrades to "not covered" when the
speaker skips a scene. binder.py uses the spans to cut the recording per scene.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

# Words for lexical alignment: case-folded, punctuation-stripped. Apostrophes are
# kept inside tokens so "don't" stays one token (and matches the ASR's "don't").
_WORD_RE = re.compile(r"[A-Za-z0-9']+")

# Minimum fraction of planned words that must match before phase 1 trusts the
# span. Below this we treat the scene as not covered — otherwise stopwords ("the",
# "is", "of") in unrelated speech produce phantom spans for scenes the speaker
# never touched.
MIN_COVERAGE_SCORE = 0.25


# --------------------------------------------------------------------- dataclasses


@dataclass
class TranscriptSentence:
    text: str
    start: float
    end: float


@dataclass
class TranscriptWord:
    text: str
    start: float
    end: float


@dataclass
class Transcript:
    words: list[TranscriptWord]
    sentences: list[TranscriptSentence]


@dataclass
class SceneSpan:
    """Phase-1 result: where in the upload does this scene live?"""
    scene_id: str
    span_start: float | None         # None when the scene wasn't covered
    span_end: float | None
    new_sentence_start_idx: int | None   # inclusive index into Transcript.sentences
    new_sentence_end_idx: int | None     # inclusive
    score: float                          # 0..1, fraction of planned words matched
    note: str


# --------------------------------------------------------------------- transcript loading


def load_transcript(path: Path) -> Transcript:
    """Load a transcript.json written by the ASR step.

    Schema: {"words": [{"text","start","end"}, ...], "sentences": [...]}
    """
    raw = json.loads(Path(path).read_text())
    words = [
        TranscriptWord(text=w["text"], start=float(w["start"]), end=float(w["end"]))
        for w in raw.get("words", [])
    ]
    sentences = [
        TranscriptSentence(text=s["text"], start=float(s["start"]), end=float(s["end"]))
        for s in raw.get("sentences", [])
    ]
    return Transcript(words=words, sentences=sentences)


# --------------------------------------------------------------------- phase 1: coarse word alignment


def _tokens(text: str) -> list[str]:
    return [m.group(0).lower() for m in _WORD_RE.finditer(text or "")]


def align_scene_spans(transcript: Transcript, plan_scenes: list[dict]) -> list[SceneSpan]:
    """Estimate each scene's wall-clock span in the upload.

    Runs ONE global SequenceMatcher over (concatenated planned narrations) vs
    (transcript words). A per-scene run would lose monotonicity and let scene 5
    steal matches from scene 2. The matcher already enforces ordered matches.
    """
    # Stream A: planned narration tokens, tagged with their owning scene.
    a_tokens: list[str] = []
    a_scene: list[str] = []
    for scene in plan_scenes:
        sid = scene["id"]
        for tok in _tokens(scene.get("narration", "")):
            a_tokens.append(tok)
            a_scene.append(sid)

    # Stream B: transcript words, keeping each word's full record parallel to tokens.
    b_tokens: list[str] = []
    b_words: list[TranscriptWord] = []
    for w in transcript.words:
        toks = _tokens(w.text)
        if not toks:
            continue
        # An ASR "word" with multiple letter runs (rare; e.g. "U.S.A") becomes one
        # token entry so the parallel lists stay aligned with B's word records.
        b_tokens.append(toks[0])
        b_words.append(w)

    matcher = SequenceMatcher(None, a_tokens, b_tokens, autojunk=False)
    a_to_b: list[int] = [-1] * len(a_tokens)
    for i, j, n in matcher.get_matching_blocks():
        for k in range(n):
            a_to_b[i + k] = j + k

    word_to_sentence = _word_index_to_sentence_index(b_words, transcript.sentences)

    spans: list[SceneSpan] = []
    for scene in plan_scenes:
        sid = scene["id"]
        planned = sum(1 for s in a_scene if s == sid)
        matched_b = [b_idx for s, b_idx in zip(a_scene, a_to_b) if s == sid and b_idx >= 0]

        if planned == 0:
            spans.append(SceneSpan(
                scene_id=sid, span_start=None, span_end=None,
                new_sentence_start_idx=None, new_sentence_end_idx=None,
                score=0.0, note="scene has no planned narration",
            ))
            continue
        if not matched_b:
            spans.append(SceneSpan(
                scene_id=sid, span_start=None, span_end=None,
                new_sentence_start_idx=None, new_sentence_end_idx=None,
                score=0.0, note="not covered by upload",
            ))
            continue

        score = len(matched_b) / planned
        if score < MIN_COVERAGE_SCORE:
            spans.append(SceneSpan(
                scene_id=sid, span_start=None, span_end=None,
                new_sentence_start_idx=None, new_sentence_end_idx=None,
                score=round(score, 3),
                note=f"not covered (only {len(matched_b)}/{planned} planned words matched — likely stopwords)",
            ))
            continue

        first, last = min(matched_b), max(matched_b)
        spans.append(SceneSpan(
            scene_id=sid,
            span_start=float(b_words[first].start),
            span_end=float(b_words[last].end),
            new_sentence_start_idx=word_to_sentence[first],
            new_sentence_end_idx=word_to_sentence[last],
            score=round(score, 3),
            note=f"matched {len(matched_b)}/{planned} planned words",
        ))
    return spans


def _word_index_to_sentence_index(
    words: list[TranscriptWord], sentences: list[TranscriptSentence]
) -> dict[int, int]:
    """For each cleaned-word index, return the sentence index it belongs to.

    Two-pointer walk: sentences are ordered by start time, words are ordered by
    start time, so we just advance the sentence pointer whenever the next sentence
    has begun by the word's start time.
    """
    out: dict[int, int] = {}
    if not sentences:
        return out
    s_idx = 0
    for i, w in enumerate(words):
        t = w.start
        while s_idx + 1 < len(sentences) and t >= sentences[s_idx + 1].start:
            s_idx += 1
        out[i] = s_idx
    return out
