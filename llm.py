"""One call site for Claude (Anthropic) or GPT (OpenAI) models.

The planner and the scene generator build requests in Anthropic's message format:
system text blocks, and messages whose content is text / image / document (PDF)
blocks. `complete()` sends that to whichever provider the model id belongs to and
returns an object shaped like Anthropic's final message (`.content` blocks with
`.type`/`.text`, `.usage`, `.stop_reason`, `.model`), so callers read the result
the same way for both.

OpenAI model ids carry an `openai:` prefix (e.g. "openai:gpt-5"); anything else is
an Anthropic model id.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import anthropic

OPENAI_PREFIX = "openai:"

# Offered in Settings → Advanced when an OpenAI key is present. Any other
# `openai:<id>` the account has access to also works.
OPENAI_MODELS: dict[str, str] = {
    "GPT-5 (OpenAI)": "openai:gpt-5",
    "GPT-5 mini (OpenAI, cheaper)": "openai:gpt-5-mini",
}
DEFAULT_OPENAI_MODEL = "openai:gpt-5"


def is_openai(model: str) -> bool:
    return model.startswith(OPENAI_PREFIX)


@dataclass
class Block:
    type: str
    text: str | None = None
    thinking: str | None = None


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class Final:
    content: list[Block] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    stop_reason: str | None = None
    model: str = ""


class MissingKeyError(RuntimeError):
    pass


def complete(
    anthropic_client: anthropic.Anthropic | None,
    openai_client,
    *,
    model: str,
    max_tokens: int,
    system: list[dict],
    messages: list[dict],
    thinking: dict | None = None,
    output_config: dict | None = None,
    effort: str | None = None,
):
    """Run one completion and return the final message (Anthropic's, or a lookalike)."""
    if is_openai(model):
        if openai_client is None:
            raise MissingKeyError("An OpenAI model was chosen, but no OpenAI API key is set.")
        return _openai_complete(openai_client, model[len(OPENAI_PREFIX):], system, messages, max_tokens, effort)
    if anthropic_client is None:
        raise MissingKeyError("A Claude model was chosen, but no Anthropic API key is set.")
    kwargs: dict = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
    if thinking:
        kwargs["thinking"] = thinking
    if output_config:
        kwargs["output_config"] = output_config
    with anthropic_client.messages.stream(**kwargs) as stream:
        return stream.get_final_message()


# ------------------------------------------------------------------ OpenAI


def _openai_parts(content) -> list[dict] | str:
    """Anthropic content blocks -> Chat Completions content parts."""
    if isinstance(content, str):
        return content
    parts: list[dict] = []
    for block in content:
        kind = block.get("type")
        if kind == "text":
            parts.append({"type": "text", "text": block.get("text", "")})
        elif kind == "image":
            src = block.get("source", {})
            parts.append({"type": "image_url", "image_url": {
                "url": f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}"}})
        elif kind == "document":
            src = block.get("source", {})
            parts.append({"type": "file", "file": {
                "filename": "reference.pdf",
                "file_data": f"data:application/pdf;base64,{src.get('data', '')}"}})
    return parts


def _assistant_text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content if b.get("type") == "text")


# Our effort levels -> OpenAI reasoning_effort (reasoning models only).
_EFFORT_MAP = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}


def _openai_complete(client, model_id: str, system: list[dict], messages: list[dict], max_tokens: int, effort: str | None):
    import openai

    chat: list[dict] = [{"role": "system", "content": "\n\n".join(b.get("text", "") for b in system)}]
    for message in messages:
        if message["role"] == "assistant":
            chat.append({"role": "assistant", "content": _assistant_text(message["content"])})
        else:
            chat.append({"role": "user", "content": _openai_parts(message["content"])})

    kwargs: dict = {"model": model_id, "messages": chat, "max_completion_tokens": max_tokens}
    reasoning = model_id.startswith(("gpt-5", "o1", "o3", "o4"))
    if reasoning:
        kwargs["reasoning_effort"] = _EFFORT_MAP.get(effort or "high", "high")

    # Models differ in their output-token ceiling; step down rather than fail.
    for limit in (max_tokens, 32000, 16000):
        kwargs["max_completion_tokens"] = min(limit, max_tokens)
        try:
            resp = client.chat.completions.create(**kwargs)
            break
        except openai.BadRequestError as exc:
            text = str(exc).lower()
            if "reasoning_effort" in text and "reasoning_effort" in kwargs:
                kwargs.pop("reasoning_effort")
                resp = client.chat.completions.create(**kwargs)
                break
            if "max" in text and "token" in text and limit > 16000:
                continue
            raise

    choice = resp.choices[0]
    usage = resp.usage
    cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
    prompt = getattr(usage, "prompt_tokens", 0) or 0
    return Final(
        content=[Block(type="text", text=choice.message.content or "")],
        usage=Usage(
            input_tokens=prompt - cached,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            cache_read_input_tokens=cached,
        ),
        stop_reason=choice.finish_reason,
        model=OPENAI_PREFIX + (resp.model or model_id),
    )
