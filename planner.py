"""Lecture script -> editable scene plan via Claude.

Returns a JSON-shaped plan for the frontend to edit before generation. Same
model/thinking/effort as generator.py.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import anthropic

import llm

from generator import (
    MODEL,
    _redact_messages,
    _redact_system,
    build_user_content,
    cost_breakdown,
    usage_to_cost_usd,
)


PLANNER_SYSTEM_PROMPT = """You segment a lecture script into a sequence of animateable scenes for a Manim lecture video.

INPUT
  Free-text lecture script (and optionally PDFs/images for context). Treat any attached
  PDF/images as the source of truth for the lecture's content; the text may be a transcript
  or notes.

OUTPUT
  Exactly one fenced JSON code block (```json ... ```). Nothing else after it. The JSON must match this schema:

  {
    "shared_style": "<one paragraph describing recurring objects (e.g. the attention matrix, the neural network), color conventions (e.g. queries red, keys blue), notation (e.g. subscripts for token indices), layout (title at top, captions at bottom), and pacing (e.g. ~30s per scene). EVERY scene will receive this string verbatim, so put anything that must stay consistent here.>",
    "scenes": [
      {
        "id": "scene_01",
        "title": "<short scene title shown to the viewer>",
        "brief": "<2-5 sentence description of what THIS scene shows, in narrative order>",
        "narration": "<the exact spoken narration for THIS scene, copied VERBATIM from the input transcript. Do not paraphrase: this text is matched word-by-word against the narrator's recording to cut the audio into scenes.>",
        "key_visuals": "<comma-separated list of the concrete visual elements: matrices, arrows, labels, plots>",
        "recurring_objects": "<which objects from the shared style appear here, and any new ones introduced>",
        "est_seconds": <integer, 15-90>
      },
      ...
    ]
  }

CONSTRAINTS
  - 3 to 25 scenes. Aim for one scene per major concept transition; don't split a single idea across scenes.
  - Scene ids are zero-padded sequential: scene_01, scene_02, ...
  - Each scene must be self-contained (no "as we saw before, here is X" without showing X again briefly).
  - The shared_style must define ALL recurring visual elements so individual scene generations don't drift.
  - Prefer fewer, longer scenes (45-90s) over many tiny scenes for a lecture format.
  - **Narration is the source of truth for pacing.** Together, the narration strings across all scenes
    should reconstruct the input script almost word-for-word. Do not summarize the script in the narration;
    keep the speaker's actual sentences. `est_seconds` should be your best estimate of how long the
    narration will take to speak at a natural pace (rough rule: ~150 words per minute → ~2.5 words/sec).
"""


SEGMENT_ENRICH_SYSTEM_PROMPT = """You design the visuals for a sequence of lecture scenes whose boundaries and narration are ALREADY FIXED (each scene is one pre-recorded narration clip).

You are given N scenes, in order, each with its exact spoken narration. Do NOT merge, split, reorder, or rewrite the narration — the segmentation is final. Your only job is to describe, for each scene, what the animation should show, consistent with the shared visual style.

OUTPUT
  Exactly one fenced JSON code block (```json ... ```). Nothing after it. Schema:

  {
    "scenes": [
      {
        "title": "<short scene title shown to the viewer>",
        "brief": "<2-5 sentence description of what THIS scene shows, in narrative order, tracking its narration>",
        "key_visuals": "<comma-separated concrete visual elements: matrices, arrows, labels, plots>",
        "recurring_objects": "<which objects from the shared style appear here, and any new ones introduced>",
        "est_seconds": <integer — the clip's spoken duration, provided below, rounded>
      },
      ...
    ]
  }

If the user message says there is no shared_style yet, ALSO include a top-level
"shared_style" string in the JSON (same role as in a full lecture plan: one paragraph
defining recurring objects, color conventions, notation, layout, and pacing that every
scene will receive verbatim), and make every scene conform to it.

RULES
  - Output EXACTLY N scene objects, in the same order as the input. One per input scene.
  - Do NOT include a narration field — the narration is fixed and owned by the caller.
  - Every scene must conform to the shared_style: reuse its colors, notation, layout, and recurring objects.
  - Each scene must be self-contained (re-introduce any prior object it references).
"""


@dataclass
class SegmentPlanResult:
    scenes: list[dict] | None   # one enrichment dict per input segment, or None on failure
    shared_style: str           # the style used: the caller's, or one written by the model
    raw_reply: str
    parse_error: str | None
    usage: dict
    cost_usd: float
    request_dump: dict | None = None
    response_dump: dict | None = None


def plan_from_segments(
    client: anthropic.Anthropic,
    segments: list[dict],
    shared_style: str,
    *,
    user_notes: str = "",
    pdfs: list[bytes] | None = None,
    images: list[tuple[bytes, str]] | None = None,
    available_assets: list[str] | None = None,
    model: str = MODEL,
    openai_client=None,
    max_tokens: int = 12000,
) -> SegmentPlanResult:
    """Enrich a FIXED list of scenes (each a {narration, est_seconds}) with visuals.

    Unlike `generate_plan`, this never re-segments: it returns exactly one visual
    description per input segment, in order, reusing `shared_style` verbatim. Used
    by the per-scene-recording path, where the audio files already define the scenes.

    `pdfs`/`images` are reference material (typically the lecture's slide deck) —
    the segmentation still comes from the recordings, but the slides tell Claude
    what each scene should actually show. `available_assets` names real image files
    the animation can place, exactly as in `generate_plan`.
    """
    seg_lines = []
    for i, seg in enumerate(segments):
        secs = seg.get("est_seconds")
        dur = f" (~{int(secs)}s)" if secs else ""
        seg_lines.append(f"### Scene {i + 1}{dur}\n{(seg.get('narration') or '').strip()}")
    body = "\n\n".join(seg_lines)

    notes = (user_notes or "").strip()
    notes_block = (
        f"### Notes from the user (apply to the visuals; do NOT restate them in any scene):\n{notes}\n\n"
        if notes else ""
    )
    assets = [a for a in (available_assets or []) if a]
    assets_block = ""
    if assets:
        asset_lines = "\n".join(f"  - {name}" for name in assets)
        assets_block = (
            "### Available image assets (real files the animation can place with ImageMobject):\n"
            f"{asset_lines}\n"
            "When a scene should show one of these, name the exact filename in that scene's "
            "`key_visuals` (and `recurring_objects` if it recurs). Only use an asset where it "
            "genuinely fits — don't force one into every scene.\n\n"
        )
    slides_block = ""
    if pdfs or images:
        slides_block = (
            "### Attached slides / reference material\n"
            "These are the lecture's own slides. The scene split is FIXED by the recordings "
            "below — do not re-segment to match the slides. Use them to decide what each scene "
            "should show: pull the real diagrams, equations, labels, and terminology from them "
            "so the animation matches what the lecturer presented.\n\n"
        )
    style_block = (
        f"### shared_style (reuse EXACTLY — every scene conforms to this):\n{shared_style.strip()}\n\n"
        if shared_style.strip()
        else "### shared_style: none yet — write one (top-level \"shared_style\" key) and follow it.\n\n"
    )
    user_text = (
        f"{style_block}"
        f"{notes_block}"
        f"{assets_block}"
        f"{slides_block}"
        f"### The {len(segments)} fixed scenes (narration is final — describe visuals only):\n\n{body}\n"
    )

    system_blocks = [{"type": "text", "text": SEGMENT_ENRICH_SYSTEM_PROMPT}]
    user_content = build_user_content(user_text, pdfs=pdfs, images=images)
    user_content[-1] = {**user_content[-1], "cache_control": {"type": "ephemeral"}}
    messages = [{"role": "user", "content": user_content}]
    started_at = time.time()
    final = llm.complete(
        client, openai_client, model=model, max_tokens=max_tokens,
        system=system_blocks, messages=messages, effort="high",
        **({} if llm.is_openai(model) else {
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": "high"},
        }),
    )
    finished_at = time.time()

    reply = "\n".join(b.text for b in final.content if b.type == "text").strip()
    parsed, err = _extract_json(reply)
    scenes: list[dict] | None = None
    if parsed is not None and err is None:
        raw_scenes = parsed.get("scenes") if isinstance(parsed, dict) else None
        if not isinstance(raw_scenes, list) or len(raw_scenes) != len(segments):
            err = f"expected {len(segments)} scenes, got {len(raw_scenes) if isinstance(raw_scenes, list) else 'none'}"
        else:
            scenes = []
            for i, s in enumerate(raw_scenes):
                s = s if isinstance(s, dict) else {}
                try:
                    est = int(s.get("est_seconds") or segments[i].get("est_seconds") or 30)
                except (TypeError, ValueError):
                    est = int(segments[i].get("est_seconds") or 30)
                scenes.append({
                    "title": str(s.get("title") or f"Scene {i + 1}"),
                    "brief": str(s.get("brief") or ""),
                    "key_visuals": str(s.get("key_visuals") or ""),
                    "recurring_objects": str(s.get("recurring_objects") or ""),
                    "est_seconds": est,
                })

    usage = {
        "input_tokens": final.usage.input_tokens,
        "output_tokens": final.usage.output_tokens,
        "cache_creation_input_tokens": getattr(final.usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(final.usage, "cache_read_input_tokens", 0) or 0,
    }
    request_dump = {
        "model": model, "max_tokens": max_tokens,
        "system": _redact_system(system_blocks), "messages": _redact_messages(messages),
        "timing": {
            "started_at_iso": datetime.fromtimestamp(started_at, timezone.utc).isoformat(),
            "finished_at_iso": datetime.fromtimestamp(finished_at, timezone.utc).isoformat(),
            "elapsed_seconds": finished_at - started_at,
        },
    }
    response_dump = {"reply": reply, "usage": usage}
    style_out = shared_style.strip()
    if not style_out and isinstance(parsed, dict):
        style_out = str(parsed.get("shared_style") or "").strip()
    return SegmentPlanResult(
        scenes=scenes, shared_style=style_out, raw_reply=reply, parse_error=err, usage=usage,
        cost_usd=usage_to_cost_usd(model, usage),
        request_dump=request_dump, response_dump=response_dump,
    )


@dataclass
class PlanResult:
    plan: dict | None       # parsed JSON, or None on parse failure
    raw_reply: str
    parse_error: str | None
    usage: dict
    cost_usd: float
    cost_breakdown_dict: dict
    request_dump: dict | None = None
    request_dump_full: dict | None = None
    response_dump: dict | None = None


_JSON_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)


def _extract_json(reply: str) -> tuple[dict | None, str | None]:
    m = _JSON_RE.search(reply)
    candidate = m.group(1).strip() if m else reply.strip()
    try:
        return json.loads(candidate), None
    except json.JSONDecodeError as e:
        return None, f"JSON parse failed: {e}"


def _validate_plan(plan: dict) -> str | None:
    if not isinstance(plan, dict):
        return "plan is not an object"
    if "shared_style" not in plan or not isinstance(plan["shared_style"], str):
        return "missing or non-string `shared_style`"
    scenes = plan.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return "missing or empty `scenes` array"
    seen_ids: set[str] = set()
    for i, s in enumerate(scenes):
        if not isinstance(s, dict):
            return f"scene {i} is not an object"
        for field in ("id", "title", "brief"):
            if field not in s or not isinstance(s[field], str):
                return f"scene {i} missing string field `{field}`"
        if s["id"] in seen_ids:
            return f"duplicate scene id `{s['id']}`"
        seen_ids.add(s["id"])
        # tolerate missing optional fields — fill defaults
        s.setdefault("key_visuals", "")
        s.setdefault("recurring_objects", "")
        s.setdefault("narration", "")
        s.setdefault("est_seconds", 30)
        try:
            s["est_seconds"] = int(s["est_seconds"])
        except (TypeError, ValueError):
            s["est_seconds"] = 30
    return None


def generate_plan(
    client: anthropic.Anthropic,
    lecture_text: str,
    pdfs: list[bytes] | None = None,
    images: list[tuple[bytes, str]] | None = None,
    max_tokens: int = 16000,
    user_notes: str = "",
    shared_style_override: str = "",
    available_assets: list[str] | None = None,
    model: str = MODEL,
    openai_client=None,
) -> PlanResult:
    # PDF-only intake is supported: when there's no text, give Claude a clear instruction
    # so the trailing text block isn't empty.
    if not lecture_text.strip():
        lecture_text = (
            "Plan the lecture purely from the attached PDF(s)/image(s) — they are the "
            "full source of truth for content, ordering, and visuals."
        )
    assets = [a for a in (available_assets or []) if a]
    if assets:
        # The generator can composite these image files into scenes via
        # ImageMobject. Tell the planner they exist by filename so it can route
        # each one to the scene where it belongs and resolve notes that name an
        # image by description (e.g. "the image of the man" -> man.png).
        asset_lines = "\n".join(f"  - {name}" for name in assets)
        lecture_text = (
            "### Available image assets (real files the animation can place with ImageMobject):\n"
            f"{asset_lines}\n"
            "When a scene should show one of these, name the exact filename in that scene's "
            "`key_visuals` (and `recurring_objects` if it recurs). If the user's notes refer to an "
            "image by description (e.g. 'the photo of the man', 'the diagram'), match it to the "
            "closest filename above and place it in the relevant scene. Only use an asset where it "
            "genuinely fits — don't force one into every scene.\n\n"
            f"{lecture_text}"
        )
    override = (shared_style_override or "").strip()
    if override:
        # This lecture belongs to a series: reuse the prior lecture's style verbatim
        # so colors/notation/layout stay identical. We also force it into the output
        # after parsing, but telling the planner up front makes the scene briefs and
        # recurring_objects consistent with it.
        lecture_text = (
            "### REQUIRED shared_style (this lecture is part of a series — reuse it EXACTLY):\n"
            "Copy the following string verbatim into the plan's `shared_style` field, and design "
            "every scene's visuals to conform to it. Do NOT rewrite, summarize, or 'improve' it:\n\n"
            f"{override}\n\n"
            "### Lecture source follows:\n\n"
            f"{lecture_text}"
        )
    notes = (user_notes or "").strip()
    if notes:
        # Put notes BEFORE the lecture so the model sees the constraints first, then applies
        # them as it segments. Marked clearly so Claude doesn't fold them into a scene brief.
        lecture_text = (
            "### Notes from the user (apply these to the plan; do NOT include them in any scene's content):\n"
            f"{notes}\n\n"
            "### Lecture source follows:\n\n"
            f"{lecture_text}"
        )
    user_content = build_user_content(lecture_text, pdfs=pdfs, images=images)
    # Cache the lecture+attachments — useful if the user re-runs the planner with tweaks.
    user_content[-1] = {**user_content[-1], "cache_control": {"type": "ephemeral"}}

    system_blocks = [{"type": "text", "text": PLANNER_SYSTEM_PROMPT}]
    messages = [{"role": "user", "content": user_content}]
    started_at = time.time()
    final = llm.complete(
        client, openai_client, model=model, max_tokens=max_tokens,
        system=system_blocks, messages=messages, effort="xhigh",
        **({} if llm.is_openai(model) else {
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": "xhigh"},
        }),
    )
    finished_at = time.time()

    reply_parts = [b.text for b in final.content if b.type == "text"]
    reply = "\n".join(reply_parts).strip()

    plan, err = _extract_json(reply)
    if plan is not None and err is None:
        err = _validate_plan(plan)
        if err:
            plan = None
    # Guarantee series consistency even if the model paraphrased the style anyway.
    if plan is not None and override:
        plan["shared_style"] = override

    usage = {
        "input_tokens": final.usage.input_tokens,
        "output_tokens": final.usage.output_tokens,
        "cache_creation_input_tokens": getattr(final.usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(final.usage, "cache_read_input_tokens", 0) or 0,
    }
    br = cost_breakdown(model, usage)
    timing = {
        "started_at": started_at,
        "finished_at": finished_at,
        "started_at_iso": datetime.fromtimestamp(started_at, timezone.utc).isoformat(),
        "finished_at_iso": datetime.fromtimestamp(finished_at, timezone.utc).isoformat(),
        "elapsed_seconds": finished_at - started_at,
    }
    request_dump = {
        "model": model,
        "max_tokens": max_tokens,
        "thinking": {"type": "adaptive", "display": "summarized"},
        "output_config": {"effort": "xhigh"},
        "system": _redact_system(system_blocks),
        "messages": _redact_messages(messages),
        "timing": timing,
    }
    request_dump_full = {
        "model": model,
        "max_tokens": max_tokens,
        "thinking": {"type": "adaptive", "display": "summarized"},
        "output_config": {"effort": "xhigh"},
        "system": system_blocks,
        "messages": messages,
        "timing": timing,
    }
    response_dump = {
        "model": getattr(final, "model", model),
        "stop_reason": getattr(final, "stop_reason", None),
        "usage": usage,
        "content": [
            {
                "type": b.type,
                "text": getattr(b, "text", None),
                "thinking": getattr(b, "thinking", None),
            }
            for b in final.content
        ],
    }
    return PlanResult(
        plan=plan,
        raw_reply=reply,
        parse_error=err,
        usage=usage,
        cost_usd=usage_to_cost_usd(model, usage),
        cost_breakdown_dict=br,
        request_dump=request_dump,
        request_dump_full=request_dump_full,
        response_dump=response_dump,
    )
