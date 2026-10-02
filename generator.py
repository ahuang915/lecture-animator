"""Claude API integration: turn one scene brief into ManimCE code.

- Three cached system blocks: SYSTEM_PROMPT, few-shot blob, shared_style. The
  shared_style is the same string for every scene in a project, so caching it as
  part of the prefix means scenes 2..N hit the cache for the whole static prefix.
- generate_scene_code() takes a scene_class_name (so the renderer can target it)
  and a per-scene conversation list (independent from other scenes).
- Few-shot scripts live under few_shot/.
"""

from __future__ import annotations

import ast
import base64
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import anthropic

import llm



# USD per 1M tokens (input, output), matched by longest prefix. Used only for the
# cost readout in the UI; OpenAI figures are list prices and approximate.
_PRICES_PER_1M: dict[str, tuple[float, float]] = {
    "claude-opus": (5.0, 25.0),
    "claude-sonnet": (3.0, 15.0),
    "claude-haiku": (1.0, 5.0),
    "openai:gpt-5-mini": (0.25, 2.0),
    "openai:gpt-5": (1.25, 10.0),
    "openai:gpt-4.1": (2.0, 8.0),
    "openai:gpt-4o": (2.5, 10.0),
    "openai:o3": (2.0, 8.0),
    "openai:": (1.25, 10.0),
}


def builtin_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    rate_in, rate_out = next(
        (rates for prefix, rates in sorted(_PRICES_PER_1M.items(), key=lambda kv: -len(kv[0]))
         if model.startswith(prefix)),
        _PRICES_PER_1M["claude-opus"],
    )
    return (input_tokens * rate_in + output_tokens * rate_out) / 1_000_000

MODEL = "claude-opus-4-7"

# Models the user can pick from the sidebar for per-scene generation.
# Planner always uses MODEL (Opus) — see planner.py — because shared_style quality
# affects every scene downstream and the planner only runs once per session.
SCENE_MODELS: dict[str, str] = {
    "Opus 4.7 (best quality, most expensive)": "claude-opus-4-7",
    "Sonnet 4.6 (balanced)": "claude-sonnet-4-6",
    "Haiku 4.5 (cheapest, fastest)": "claude-haiku-4-5",
}
# Reasoning depth for scene generation (Opus only — see _supports_adaptive_thinking).
# Lower this for a scene that comes back with no code; see generate_scene_code.
SCENE_EFFORTS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")
DEFAULT_SCENE_EFFORT = "high"
DEFAULT_SCENE_MODEL = MODEL

# https://platform.claude.com/docs/en/build-with-claude/prompt-caching
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10


def _supports_adaptive_thinking(model: str) -> bool:
    """Adaptive thinking + output_config.effort are Opus 4.x features.

    Haiku/Sonnet return a 400 "adaptive thinking is not supported on this model"
    if we forward those params. Gate them on the model id so the toggle just works.
    """
    return model.startswith("claude-opus-")


def cost_breakdown(model: str, usage: dict) -> dict:
    input_tokens = usage.get("input_tokens", 0)
    cache_write = usage.get("cache_creation_input_tokens", 0) or 0
    cache_read = usage.get("cache_read_input_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0)

    input_per_1m = builtin_cost(model, 1_000_000, 0)
    output_per_1m = builtin_cost(model, 0, 1_000_000)

    cost_input = builtin_cost(model, input_tokens, 0)
    cost_output = builtin_cost(model, 0, output_tokens)
    cost_cache_write = builtin_cost(model, cache_write, 0) * CACHE_WRITE_MULTIPLIER
    cost_cache_read = builtin_cost(model, cache_read, 0) * CACHE_READ_MULTIPLIER
    total = cost_input + cost_output + cost_cache_write + cost_cache_read

    return {
        "model": model,
        "rates_per_1m_usd": {"input": input_per_1m, "output": output_per_1m},
        "cache_multipliers": {
            "write_5m": CACHE_WRITE_MULTIPLIER,
            "read": CACHE_READ_MULTIPLIER,
        },
        "tokens": {
            "input_uncached": input_tokens,
            "output": output_tokens,
            "cache_write": cache_write,
            "cache_read": cache_read,
        },
        "cost_usd": {
            "input_uncached": cost_input,
            "output": cost_output,
            "cache_write": cost_cache_write,
            "cache_read": cost_cache_read,
            "total": total,
        },
    }


def usage_to_cost_usd(model: str, usage: dict) -> float:
    return cost_breakdown(model, usage)["cost_usd"]["total"]


FEW_SHOT_FILES = [
    "attention.py",
    "sentence_attention.py",
    "transformer_layers.py",
    "digit9_recognition.py",
    "digit9_strokes.py",
]


SYSTEM_PROMPT = """You generate ONE scene of a multi-scene Manim Community Edition (ManimCE) lecture.

CONTEXT
  The user is building a longer lecture made of many scenes that will be concatenated together.
  You will be told a shared style spec (recurring objects, colors, notation) that applies to
  every scene in the lecture, and then a single scene brief. Produce a self-contained scene
  that follows the shared style so it visually matches its siblings.

INPUT
  - A shared style spec (cached, same across all scenes this session).
  - A scene brief: title, what to show, key visuals, target runtime.
  - Optionally, attached PDFs/images.

OUTPUT
  Exactly one fenced Python code block (```python ... ```) containing one runnable script.
  Brief prose before the block is OK; nothing should follow the block.

HARD CONSTRAINTS
  - `from manim import *` at the top of the file; numpy is fine; no other third-party imports.
  - Exactly one Scene subclass, with the EXACT class name you are told to use
    (the renderer targets it by name). Helper methods on that class are fine.
  - No external files except image assets explicitly listed as available for this scene
    (load those with `ImageMobject("./assets/<filename>")`). Otherwise generate everything
    with primitives.
  - COLORS: use ONLY constants that ManimCE actually exports from `from manim import *`:
    WHITE, BLACK, GRAY/GREY (+_A.._E), RED/BLUE/GREEN/YELLOW/TEAL/PURPLE/GOLD/MAROON (+_A.._E),
    ORANGE, PINK, LIGHT_PINK, LIGHT_BROWN, DARK_BROWN, DARK_BLUE, DARKER_GRAY, LIGHT_GRAY,
    PURE_RED, PURE_GREEN, PURE_BLUE.
    Names like CYAN, MAGENTA, ORANGE_RED, BROWN, LIGHT_BLUE, or DARK_GREEN DO NOT EXIST and
    crash the render with NameError. For any shade outside the palette, define it yourself:
    `CYAN = ManimColor("#00FFFF")` or pass a hex string literal like `color="#FF4500"`.
  - LaTeX (MathTex / Matrix) is available. Default Scene background is black, so use light
    colors against it (WHITE, RED, BLUE_B, YELLOW, ORANGE, etc.); avoid pure BLACK strokes
    unless on a white background.
  - Use `np.random.seed(0)` (or similar) if you use randomness, so the render is reproducible.
  - Respect the shared style: if recurring objects/colors/notation are defined, use them
    EXACTLY so this scene cuts cleanly against the others.
  - Keep runtime close to the requested seconds (±20%).

QUALITY BAR
  - Open with a Text title for the scene (so viewers know where they are in the lecture).
  - Use ShowPassingFlash for energy/data flow along Lines/Arrows.
  - Use Indicate for emphasis pulses.
  - Use TransformFromCopy when content visibly moves from one place to another.
  - When embedding a sub-mobject inside a circle/box, size it via
    `min(max_size / icon.height, max_size / icon.width)`.
  - For LaggedStart, lag_ratio between 0.1 and 0.3 reads comfortably.
  - Match the style of the few-shot examples: small helper methods, named stages, captions
    that explain what the viewer is seeing.

You will be given the few-shot examples next, then the shared style for this lecture, then the scene brief.
"""


def load_few_shot_examples(base_dir: str | Path | None = None) -> str:
    """Concatenate the example scripts into one text blob, ready to ship as a system block."""
    if base_dir is None:
        base = Path(__file__).resolve().parent / "few_shot"
    else:
        base = Path(base_dir)
    blocks: list[str] = []
    for fname in FEW_SHOT_FILES:
        path = base / fname
        if not path.exists():
            continue
        blocks.append(f"### Example: {fname}\n```python\n{path.read_text()}\n```")
    if not blocks:
        return "(no few-shot examples available)"
    return "Here are reference scripts in the desired style:\n\n" + "\n\n".join(blocks)


def build_user_content(
    text: str,
    pdfs: list[bytes] | None = None,
    images: list[tuple[bytes, str]] | None = None,
) -> list[dict]:
    content: list[dict] = []
    for pdf_bytes in pdfs or []:
        content.append({
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(pdf_bytes).decode("ascii"),
            },
        })
    for img_bytes, mime in images or []:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mime,
                "data": base64.standard_b64encode(img_bytes).decode("ascii"),
            },
        })
    content.append({"type": "text", "text": text.strip()})
    return content


_CODE_RE = re.compile(r"```python\s*\n(.*?)```", re.DOTALL)


def extract_code(reply: str) -> str:
    m = _CODE_RE.search(reply)
    if m:
        return m.group(1).strip()
    return reply.strip()


@dataclass
class Generation:
    code: str
    full_reply: str
    thinking_summary: str
    usage: dict
    stop_reason: str | None = None
    request_dump: dict = field(default_factory=dict)
    request_dump_full: dict = field(default_factory=dict)
    response_dump: dict = field(default_factory=dict)


def _redact_content_blocks(content) -> list[dict]:
    """Strip base64 payloads from a user-content list so the request dump stays readable.

    Cache markers (`cache_control`) are preserved on purpose — they're the whole point of
    the dump. PDFs and images get replaced with a placeholder that records their size and
    media type so you can still see *which* blocks were attached and where the cache
    breakpoint landed.
    """
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    out: list[dict] = []
    for block in content or []:
        if not isinstance(block, dict):
            out.append({"type": "unknown", "repr": repr(block)})
            continue
        btype = block.get("type")
        if btype in ("document", "image"):
            src = block.get("source") or {}
            data = src.get("data") or ""
            redacted = {
                "type": btype,
                "source": {
                    "type": src.get("type"),
                    "media_type": src.get("media_type"),
                    "data": f"<base64 omitted: {len(data)} chars>",
                },
            }
            if "cache_control" in block:
                redacted["cache_control"] = block["cache_control"]
            out.append(redacted)
        else:
            out.append(block)
    return out


def _redact_messages(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        out.append({
            "role": m.get("role"),
            "content": _redact_content_blocks(m.get("content")),
        })
    return out


def _redact_system(system: list[dict]) -> list[dict]:
    """Truncate the huge cached few-shot blob so the dump stays under a few KB."""
    out: list[dict] = []
    for block in system or []:
        if not isinstance(block, dict):
            out.append({"repr": repr(block)})
            continue
        text = block.get("text", "")
        redacted_text = (
            text if len(text) <= 600
            else text[:300] + f"\n\n... <{len(text) - 600} chars omitted> ...\n\n" + text[-300:]
        )
        entry = {"type": block.get("type"), "text": redacted_text, "text_chars": len(text)}
        if "cache_control" in block:
            entry["cache_control"] = block["cache_control"]
        out.append(entry)
    return out


def _annotate_last_user_for_cache(messages: list[dict]) -> list[dict]:
    """Place a cache breakpoint on the last content block of the most recent user turn."""
    msgs = [dict(m) for m in messages]
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i]["role"] != "user":
            continue
        content = msgs[i]["content"]
        if isinstance(content, str):
            msgs[i]["content"] = [{
                "type": "text",
                "text": content,
                "cache_control": {"type": "ephemeral"},
            }]
        else:
            content = [dict(b) for b in content]
            content[-1] = {**content[-1], "cache_control": {"type": "ephemeral"}}
            msgs[i]["content"] = content
        break
    return msgs


def _build_system_blocks(few_shot_blob: str, shared_style: str, scene_class_name: str) -> list[dict]:
    """SYSTEM + few-shot (cached) + shared_style (cached) + class-name pin.

    The class-name pin is uncached on purpose (it's tiny and varies per scene), so
    moving it OUT of the cached prefix into the user message would also work — but
    keeping it in `system` reads more naturally to the model as a directive.
    """
    return [
        {"type": "text", "text": SYSTEM_PROMPT},
        {
            "type": "text",
            "text": few_shot_blob,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": f"### Shared style spec for THIS lecture (use exactly across all scenes)\n\n{shared_style}",
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": f"For this scene, name your Scene subclass exactly `{scene_class_name}` (the renderer targets it by name).",
        },
    ]


def generate_scene_code(
    client: anthropic.Anthropic,
    conversation: list[dict],
    few_shot_blob: str,
    shared_style: str,
    scene_class_name: str = "MainScene",
    max_tokens: int = 64000,
    model: str = DEFAULT_SCENE_MODEL,
    effort: str | None = None,
    openai_client=None,
) -> Generation:
    """Generate Manim code for one scene's latest conversation state.

    `model` may be a Claude id (uses `client`) or an `openai:` id (uses `openai_client`).
    """
    system_blocks = _build_system_blocks(few_shot_blob, shared_style, scene_class_name)
    messages = _annotate_last_user_for_cache(conversation)

    # Opus 4.x supports adaptive thinking + output_config.effort. Sonnet/Haiku reject
    # those params with a 400, so we drop them for non-Opus models.
    #
    # Effort is the lever that matters when a scene comes back with NO code at all:
    # adaptive thinking expands to fill whatever budget it is given, so on a very
    # long scene it can spend the entire max_tokens reasoning and emit zero text
    # blocks (empty reply -> nothing for extract_code to find). xhigh did this
    # routinely, which is why the default dropped to high; lecture 3's scene_03
    # (11.5 min, 70 sentences) showed high can still do it, non-deterministically —
    # the same request succeeded on one run and produced no code on another.
    # Raising max_tokens does NOT help (it feeds the runaway); lowering effort does.
    stream_kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_blocks,
        "messages": messages,
    }
    if _supports_adaptive_thinking(model):
        stream_kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
        stream_kwargs["output_config"] = {"effort": effort or DEFAULT_SCENE_EFFORT}

    started_at = time.time()
    final = llm.complete(client, openai_client, effort=effort, **stream_kwargs)
    finished_at = time.time()

    text_parts: list[str] = []
    thinking_parts: list[str] = []
    for block in final.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "thinking":
            thinking_parts.append(getattr(block, "thinking", "") or "")
    full_reply = "\n".join(text_parts).strip()
    thinking_summary = "\n".join(p for p in thinking_parts if p).strip()
    code = extract_code(full_reply)

    usage = {
        "input_tokens": final.usage.input_tokens,
        "output_tokens": final.usage.output_tokens,
        "cache_creation_input_tokens": getattr(final.usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(final.usage, "cache_read_input_tokens", 0) or 0,
    }
    stop_reason = getattr(final, "stop_reason", None)

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
        "system": _redact_system(system_blocks),
        "messages": _redact_messages(messages),
        "timing": timing,
    }
    request_dump_full = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_blocks,
        "messages": messages,
        "timing": timing,
    }
    if "thinking" in stream_kwargs:
        request_dump["thinking"] = stream_kwargs["thinking"]
        request_dump_full["thinking"] = stream_kwargs["thinking"]
    if "output_config" in stream_kwargs:
        request_dump["output_config"] = stream_kwargs["output_config"]
        request_dump_full["output_config"] = stream_kwargs["output_config"]
    response_dump = {
        "model": getattr(final, "model", model),
        "stop_reason": stop_reason,
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

    return Generation(
        code=code,
        full_reply=full_reply,
        thinking_summary=thinking_summary,
        usage=usage,
        stop_reason=stop_reason,
        request_dump=request_dump,
        request_dump_full=request_dump_full,
        response_dump=response_dump,
    )


def check_syntax(code: str) -> str | None:
    """Return a formatted SyntaxError message if `code` doesn't parse, else None.

    Catches malformed code (mismatched brackets, bad indentation, etc.) BEFORE it
    reaches manim, so we can ask the model to repair it instead of burning a full
    render that's guaranteed to crash at import.
    """
    try:
        ast.parse(code)
        return None
    except SyntaxError as exc:
        location = f"line {exc.lineno}" + (f", column {exc.offset}" if exc.offset else "")
        detail = f"{exc.msg} ({location})"
        if exc.text:
            detail += f"\n    {exc.text.rstrip()}"
        return detail


def _merge_usage(into: dict, extra: dict) -> dict:
    for key, value in extra.items():
        into[key] = into.get(key, 0) + (value or 0)
    return into


def generate_scene_code_checked(
    client: anthropic.Anthropic,
    conversation: list[dict],
    few_shot_blob: str,
    shared_style: str,
    scene_class_name: str = "MainScene",
    max_tokens: int = 64000,
    model: str = DEFAULT_SCENE_MODEL,
    effort: str | None = None,
    max_syntax_repairs: int = 2,
    openai_client=None,
) -> Generation:
    """generate_scene_code + an automatic syntax-repair loop.

    If the generated code fails to parse, append the parser error as a user turn and
    regenerate (up to `max_syntax_repairs` times). The conversation list is mutated in
    place with each (assistant, repair-user) pair so the model sees its own broken code
    and the exact error. Usage is accumulated across all calls so cost accounting covers
    the repair round-trips. The returned Generation reflects the final attempt; if every
    attempt still fails to parse, the last one is returned unchanged (the renderer will
    log the SyntaxError as before — no worse than today).
    """
    working = list(conversation)
    generation = generate_scene_code(
        client, working, few_shot_blob, shared_style,
        scene_class_name=scene_class_name, max_tokens=max_tokens, model=model, effort=effort, openai_client=openai_client,
    )

    total_usage = dict(generation.usage)
    repairs = 0
    while generation.code.strip() and repairs < max_syntax_repairs:
        error = check_syntax(generation.code)
        if error is None:
            break
        repairs += 1
        working.append({"role": "assistant", "content": generation.full_reply})
        working.append({
            "role": "user",
            "content": (
                "That code has a Python syntax error and will not run:\n\n"
                f"{error}\n\n"
                "Return the COMPLETE corrected script in one ```python``` block. Fix only the "
                "syntax error(s); keep the animation and all other code identical."
            ),
        })
        generation = generate_scene_code(
            client, working, few_shot_blob, shared_style,
            scene_class_name=scene_class_name, max_tokens=max_tokens, model=model, effort=effort, openai_client=openai_client,
        )
        _merge_usage(total_usage, generation.usage)

    generation.usage = total_usage
    if repairs:
        generation.response_dump = {
            **generation.response_dump,
            "syntax_repairs": repairs,
        }
    return generation


def format_assets_block(asset_filenames: list[str]) -> str:
    """Render the 'Available assets' block to inject into a scene turn.

    The renderer symlinks scene assets into the script's cwd under ./assets/, so
    `ImageMobject('./assets/<filename>')` resolves at render time.
    """
    if not asset_filenames:
        return ""
    bullets = "\n".join(f"  - `./assets/{n}`" for n in asset_filenames)
    return (
        "\n\n**Available image assets** (use these instead of procedural fallbacks; "
        "load with `ImageMobject('./assets/<filename>')`):\n"
        f"{bullets}\n"
        "Animate them with `FadeIn`, `.scale_to_fit_height(...)`, `SurroundingRectangle`, etc. "
        "Do NOT invent or search other paths — these exact paths work at render time."
    )


def format_audio_block(
    narration: str,
    audio_duration: float | None,
    sentences: list[dict] | None = None,
) -> str:
    """Render the narration + audio-duration + per-sentence-timestamps block.

    When `sentences` is provided (list of {text, start, end} from the transcribed
    alignment), we surface a per-sentence schedule so Claude can pin each animation
    beat to the moment its corresponding sentence is being spoken. Empty string
    if there's nothing to say.
    """
    narration = (narration or "").strip()
    if not narration and audio_duration is None:
        return ""
    parts: list[str] = ["", "---", "", "**Spoken narration over this scene** (will be played as audio, do NOT render it as on-screen text):"]
    if narration:
        parts += ["", f"> {narration}"]
    if audio_duration is not None:
        parts += [
            "",
            f"**Audio duration:** {audio_duration:.2f} seconds. **This is a HARD target** — the animation's "
            f"total `self.play(...)` + `self.wait(...)` time should land within ±0.5s of this value so the "
            f"visuals stay in sync with the voice-over.",
        ]
    if sentences:
        parts += [
            "",
            "**Per-sentence timing** (measured from the narrator's actual recording — use these to schedule "
            "each beat of the animation so on-screen elements appear *as the corresponding sentence is "
            "spoken*, not before or after):",
            "",
            "```",
        ]
        for s in sentences:
            start = float(s.get("start", 0.0))
            end = float(s.get("end", start))
            text = (s.get("text") or "").strip().replace("\n", " ")
            parts.append(f"[{start:6.2f}s – {end:6.2f}s] {text}")
        parts += [
            "```",
            "",
            "Concretely: each `self.play(...)` block that introduces or transforms an element should "
            "begin around the start-time of the sentence that describes it, with its run_time chosen so "
            "the transformation completes by (or shortly after) the sentence ends. Bridge any gap to the "
            "next sentence with `self.wait(...)`. Aim for ±0.3s alignment per sentence; cumulative drift "
            "across the scene should stay under 1s.",
            "",
            "**How to hit those times reliably** — schedule against Manim's own clock, never a counter you "
            "maintain yourself. Put this helper on the Scene class and call it before each beat:",
            "",
            "```python",
            "def wait_until(self, t):",
            "    \"\"\"Wait until t seconds into the scene (no-op if already past t).\"\"\"",
            "    dt = t - self.renderer.time",
            "    if dt > 0.01:",
            "        self.wait(dt)",
            "```",
            "",
            "Do NOT override `play()` or `wait()` to keep your own elapsed-time variable: `Scene.wait()` "
            "calls `play()` internally, so such a counter double-counts every wait and the visuals drift "
            "ahead of the voice.",
        ]
    elif narration:
        parts += [
            "",
            "Pace key beats to roughly match the narration's sentence boundaries; if a sentence introduces "
            "a new visual element, that element should appear around the time that sentence is spoken.",
        ]
    return "\n".join(parts)


def build_scene_first_turn(
    scene_index: int,
    title: str,
    brief: str,
    key_visuals: str,
    recurring_objects: str,
    est_seconds: int,
    pdfs: list[bytes] | None = None,
    images: list[tuple[bytes, str]] | None = None,
    asset_filenames: list[str] | None = None,
    narration: str = "",
    audio_duration: float | None = None,
    sentences: list[dict] | None = None,
) -> list[dict]:
    """Compose the user content for a scene's first generation turn."""
    parts: list[str] = [
        f"### Scene {scene_index}: {title}",
        "",
        brief.strip() if brief else "",
    ]
    if key_visuals.strip():
        parts += ["", f"**Key visuals:** {key_visuals.strip()}"]
    if recurring_objects.strip():
        parts += ["", f"**Recurring objects (re-use from shared style):** {recurring_objects.strip()}"]
    # When audio is known, the audio_duration overrides est_seconds as the runtime target.
    if audio_duration is None:
        parts += ["", f"**Target runtime:** ~{int(est_seconds)} seconds"]
    text = (
        "\n".join(parts)
        + format_audio_block(narration, audio_duration, sentences)
        + format_assets_block(asset_filenames or [])
    )
    return build_user_content(text, pdfs=pdfs, images=images)
