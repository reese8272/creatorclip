"""Per-clip caption-hook / thumbnail-text concepts (Issue 323).

Generates 3–5 short on-screen overlay-text options for a clip's thumbnail /
caption, grounded in the creator's channel thumbnail-text patterns (from DNA)
and the clip's opening hook (transcript excerpt).

Prompt structure (three system blocks):
  Block 1 — static instructions + UNTRUSTED_CONTENT_POLICY.
  Block 2 — DNA brief (cache_control ttl=1h breakpoint).
  Block 3 — per-clip context (channel label). Uncached.

The clip transcript excerpt is UNTRUSTED content — wrapped via wrap_untrusted
in the user turn (OWASP LLM01; Issue 224). Honesty disclaimer always appended
by Python, never by the model.
"""

from __future__ import annotations

import json
import logging

import httpx
from anthropic import Anthropic, APIConnectionError, APIStatusError, RateLimitError

from config import settings
from knowledge.util import UNTRUSTED_CONTENT_POLICY, wrap_untrusted
from observability import record_llm_metric, warn_if_truncated

logger = logging.getLogger(__name__)

_ANTHROPIC = Anthropic(
    api_key=settings.ANTHROPIC_API_KEY,
    timeout=httpx.Timeout(60.0, connect=10.0),
    max_retries=2,
)

OVERLAY_TEXT_MAX_WORDS = 6  # Practical on-screen text limit for Shorts
SURFACE_N = 5
_DNA_BRIEF_MAX_CHARS = 3000
_TRANSCRIPT_MAX_CHARS = 800  # Opening hook only — first ~30 seconds

DISCLAIMER = (
    "These overlay-text suggestions are estimates grounded in your channel patterns. "
    "AutoClip cannot guarantee specific engagement or CTR outcomes."
)

# Block 1: static — never contains per-creator data.
_SYSTEM_INSTRUCTIONS = f"""\
{UNTRUSTED_CONTENT_POLICY}
You are a YouTube Shorts thumbnail-text and caption-hook strategist. Your task: \
given a clip's opening transcript hook and the creator's DNA profile, generate \
exactly 5 short on-screen overlay-text options for the clip's thumbnail or caption.

Each option must be ≤{OVERLAY_TEXT_MAX_WORDS} words, punchy, and grounded in this \
creator's channel thumbnail-text patterns.

Honesty constraints (strictly enforced):
  - Every rationale MUST use hedged language: "likely", "estimated", "suggests", \
"based on channel patterns". NEVER say "will", "guaranteed", "promise", or virality language.

Return ONLY a valid JSON object with this exact schema — no preamble, no commentary:
{{
  "options": [
    {{
      "text": "<overlay text, ≤{OVERLAY_TEXT_MAX_WORDS} words>",
      "rationale": "<one sentence, hedged, citing channel pattern or clip content>"
    }}
  ]
}}

Rules:
  - Produce exactly 5 options ranked 1–5 from strongest predicted fit for this channel.
  - Each option ≤{OVERLAY_TEXT_MAX_WORDS} words — short enough to be readable at a glance.
  - Options should vary in angle (curiosity, emotion, outcome, question, statement).
  - Return valid JSON ONLY."""


def _build_request(
    channel_title: str,
    dna_brief: str | None,
    clip_hook: str,
) -> tuple[list[dict], list[dict]]:
    """Assemble (system_blocks, messages) for the per-clip caption-hook call.

    Block 2 carries the cache breakpoint (ttl=1h): static + DNA prefix clears
    Sonnet 4.6's 1024-token cacheable-prefix floor. Clip hook is JSON-wrapped
    in the user turn to prevent prompt-injection break-out. (Issue 323 / 218)
    """
    dna_text = (dna_brief or "No DNA profile available yet.")[:_DNA_BRIEF_MAX_CHARS]

    system: list[dict] = [
        # Block 1: static instructions — byte-identical across all calls.
        {"type": "text", "text": _SYSTEM_INSTRUCTIONS},
        # Block 2: DNA brief — cached per-creator for 1h.
        {
            "type": "text",
            "text": f"CREATOR DNA PROFILE:\n{dna_text}",
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
        # Block 3: per-clip factual label — uncached.
        {"type": "text", "text": f"CHANNEL: {channel_title}"},
    ]

    # Clip transcript hook is untrusted content — JSON-wrap in the user turn.
    user_content = (
        wrap_untrusted("clip_transcript_hook", clip_hook)
        + f"Generate 5 on-screen overlay-text options for this clip from channel "
        f"'{channel_title}'. Return the JSON object."
    )
    messages: list[dict] = [{"role": "user", "content": user_content}]
    return system, messages


def _parse_result(raw_json: str) -> dict:
    """Parse and validate the structured JSON response.

    Returns a dict with ``options`` (list of up to SURFACE_N items) and
    ``disclaimer``. Raises ValueError on malformed JSON or empty options list.
    """
    data = json.loads(raw_json)
    raw_options = data.get("options")
    if not isinstance(raw_options, list) or not raw_options:
        raise ValueError("Response missing 'options' list")

    options: list[dict] = []
    for o in raw_options:
        text = str(o.get("text", "")).strip()
        if not text:
            continue
        options.append(
            {
                "text": text,
                "rationale": str(o.get("rationale", "")).strip(),
            }
        )

    return {
        "options": options[:SURFACE_N],
        "disclaimer": DISCLAIMER,
    }


def generate_clip_caption_hooks(
    channel_title: str,
    dna_brief: str | None,
    clip_hook: str,
) -> tuple[dict, dict]:
    """Call Claude synchronously; return ``(parsed_result, usage)``.

    Intended to be called via ``asyncio.to_thread`` from async route handlers.
    Uses ``messages.create`` (non-streaming) — short-latency direct response.

    Args:
        channel_title: The creator's YouTube channel name.
        dna_brief: The creator's plain-language DNA brief, or None if not built yet.
        clip_hook: The clip's opening transcript excerpt (untrusted content).

    Returns:
        A tuple of (result_dict, usage_dict). result_dict has keys ``options``
        and ``disclaimer``. usage_dict has keys ``input_tokens``, ``output_tokens``,
        ``cache_read``, ``cache_creation``.

    Raises:
        RateLimitError, APIStatusError, APIConnectionError: propagated to caller.
        ValueError: if Claude returns malformed JSON or an empty options list.
    """
    hook_excerpt = clip_hook[:_TRANSCRIPT_MAX_CHARS]
    system, messages = _build_request(channel_title, dna_brief, hook_excerpt)

    from verbose import vlog_llm_request, vlog_llm_response

    vlog_llm_request(
        "clip_caption_hooks",
        model=settings.ANTHROPIC_MODEL_CLIP_CAPTIONS,
        max_tokens=512,
        system=system,
        messages=messages,
    )
    try:
        response = _ANTHROPIC.messages.create(
            model=settings.ANTHROPIC_MODEL_CLIP_CAPTIONS,
            max_tokens=512,
            system=system,  # type: ignore[arg-type]
            messages=messages,  # type: ignore[arg-type]
        )
    except (RateLimitError, APIStatusError, APIConnectionError) as exc:
        logger.error("clip_caption_hooks LLM error exc_type=%s", type(exc).__name__)
        raise
    vlog_llm_response("clip_caption_hooks", response=response)

    usage_dict = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_read": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        "cache_creation": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
    }
    logger.info(
        "clip_caption_hooks tokens: in=%d cached_read=%d cached_write=%d out=%d",
        usage_dict["input_tokens"],
        usage_dict["cache_read"],
        usage_dict["cache_creation"],
        usage_dict["output_tokens"],
    )
    record_llm_metric(settings.ANTHROPIC_MODEL_CLIP_CAPTIONS, usage_dict)
    warn_if_truncated(
        settings.ANTHROPIC_MODEL_CLIP_CAPTIONS, getattr(response, "stop_reason", None)
    )

    raw = next((b.text for b in response.content if b.type == "text"), "")
    result = _parse_result(raw)
    return result, usage_dict
