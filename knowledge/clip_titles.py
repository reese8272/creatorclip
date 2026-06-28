"""Per-clip Short-title + hook-rewrite suggestions (Issue 322).

Prompt structure (three system blocks):
  Block 1 — static instructions + UNTRUSTED_CONTENT_POLICY (creator-independent).
  Block 2 — DNA brief (cache_control ttl=1h breakpoint). The static + DNA prefix
            clears Sonnet 4.6's 1024-token cacheable-prefix floor, so repeat calls
            within the same creator session benefit from prompt-cache reads.
  Block 3 — per-clip context (transcript excerpt). Uncached.

The clip transcript is UNTRUSTED content — wrapped via wrap_untrusted in the user
turn so it cannot break out of its structural bounds (OWASP LLM01; Issue 224).

Returns N ranked Short-title candidates + 1–2 hook-rewrite options as a validated
dict. The honesty disclaimer is always appended by Python — never by the model.
"""

from __future__ import annotations

import json
import logging

import httpx
from anthropic import Anthropic, APIConnectionError, APIStatusError, RateLimitError

from config import settings
from knowledge.util import UNTRUSTED_CONTENT_POLICY, wrap_untrusted
from observability import record_llm_metric

logger = logging.getLogger(__name__)

_ANTHROPIC = Anthropic(
    api_key=settings.ANTHROPIC_API_KEY,
    timeout=httpx.Timeout(60.0, connect=10.0),
    max_retries=2,
)

TITLE_MAX_CHARS = 100  # YouTube hard limit
HOOK_MAX_CHARS = 200  # opening-line rewrite cap
SURFACE_TITLES_N = 5

_DNA_BRIEF_MAX_CHARS = 3000
_TRANSCRIPT_MAX_CHARS = 1500

DISCLAIMER = (
    "These suggestions are estimates grounded in your channel data. "
    "AutoClip cannot guarantee specific CTR or view outcomes."
)

# Block 1: static — never contains per-creator data. Byte-identical across all
# calls so the cache_control marker on block 2 is never invalidated.
_SYSTEM_INSTRUCTIONS = f"""\
{UNTRUSTED_CONTENT_POLICY}
You are a YouTube Shorts title strategist. Your task: given a clip's transcript \
excerpt and the creator's DNA profile, generate exactly 5 ranked Short-title candidates \
(≤{TITLE_MAX_CHARS} chars each) and 1–2 punchy hook-rewrite options for the opening \
line of the clip.

Ranking criteria:
  - Titles: ranked highest-to-lowest predicted fit for THIS creator's channel style and audience.
  - Hook rewrites: punchier, more front-loaded alternatives to the clip's actual opening.

Honesty constraints (strictly enforced):
  - Every rationale MUST use hedged language: "likely", "estimated", "suggests", "based on".
  - NEVER write "will", "guaranteed", "promise", or any virality language.
  - Titles are Short-format: concise, hook-forward, ≤{TITLE_MAX_CHARS} characters.

Return ONLY a valid JSON object with this exact schema — no preamble, no commentary:
{{
  "titles": [
    {{
      "title": "<string, ≤{TITLE_MAX_CHARS} chars>",
      "rationale": "<one sentence, hedged, citing channel pattern or clip content>",
      "ctr_signal": "up" | "neutral" | "down"
    }}
  ],
  "hook_rewrites": [
    {{
      "rewrite": "<opening-line rewrite, ≤{HOOK_MAX_CHARS} chars>",
      "rationale": "<one sentence explaining why this opening is stronger>"
    }}
  ]
}}

Rules:
  - Produce exactly 5 titles and 1–2 hook rewrites.
  - Every title ≤{TITLE_MAX_CHARS} characters; every rewrite ≤{HOOK_MAX_CHARS} characters.
  - Titles must fit the SHORT format — punchy, front-loaded, scannable in a feed.
  - Rank titles 1–5 from highest to lowest predicted fit for THIS channel.
  - Return valid JSON ONLY."""


def _build_request(
    channel_title: str,
    dna_brief: str | None,
    clip_transcript: str,
) -> tuple[list[dict], list[dict]]:
    """Assemble (system_blocks, messages) for the clip-titles call.

    Block 2 carries the cache breakpoint (ttl=1h): static instructions + DNA brief
    prefix clears Sonnet 4.6's 1024-token cacheable-prefix floor, so same-creator
    calls within the session window benefit from cache reads. (Issue 322 / Issue 218)
    The clip transcript is in the user turn, JSON-wrapped via wrap_untrusted to
    prevent prompt-injection break-out (OWASP LLM01; Anthropic guide 2026-06-23).
    """
    dna_text = (dna_brief or "No DNA profile available yet.")[:_DNA_BRIEF_MAX_CHARS]

    system: list[dict] = [
        # Block 1: static instructions — never contains per-creator data.
        {"type": "text", "text": _SYSTEM_INSTRUCTIONS},
        # Block 2: DNA brief — stable per-creator within the ttl=1h window.
        {
            "type": "text",
            "text": f"CREATOR DNA PROFILE:\n{dna_text}",
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
        # Block 3: per-clip factual context — uncached; changes every call.
        {"type": "text", "text": f"CHANNEL: {channel_title}"},
    ]

    # Clip transcript is untrusted attacker-influenceable content. JSON-wrap it
    # in the user turn so it cannot escape its structural bounds.
    user_content = (
        wrap_untrusted("clip_transcript", clip_transcript)
        + f"Generate 5 Short-title candidates and 1–2 hook rewrites for this clip "
        f"from channel '{channel_title}'. Return the JSON object."
    )
    messages: list[dict] = [{"role": "user", "content": user_content}]
    return system, messages


def _parse_result(raw_json: str) -> dict:
    """Parse and validate the structured JSON response.

    Returns a dict with ``titles`` (list, up to SURFACE_TITLES_N) and
    ``hook_rewrites`` (list, 1–2 items). Raises ValueError on malformed JSON or
    schema violations so the caller can surface a 502 error cleanly.
    """
    data = json.loads(raw_json)

    raw_titles = data.get("titles")
    if not isinstance(raw_titles, list) or not raw_titles:
        raise ValueError("Response missing 'titles' list")

    titles: list[dict] = []
    for t in raw_titles:
        title_text = str(t.get("title", "")).strip()[:TITLE_MAX_CHARS]
        if not title_text:
            continue
        ctr_signal = t.get("ctr_signal", "neutral")
        if ctr_signal not in ("up", "neutral", "down"):
            ctr_signal = "neutral"
        titles.append(
            {
                "title": title_text,
                "rationale": str(t.get("rationale", "")).strip(),
                "ctr_signal": ctr_signal,
            }
        )

    raw_rewrites = data.get("hook_rewrites")
    hook_rewrites: list[dict] = []
    if isinstance(raw_rewrites, list):
        for r in raw_rewrites[:2]:
            rewrite_text = str(r.get("rewrite", "")).strip()[:HOOK_MAX_CHARS]
            if rewrite_text:
                hook_rewrites.append(
                    {
                        "rewrite": rewrite_text,
                        "rationale": str(r.get("rationale", "")).strip(),
                    }
                )

    return {
        "titles": titles[:SURFACE_TITLES_N],
        "hook_rewrites": hook_rewrites,
        "disclaimer": DISCLAIMER,
    }


def generate_clip_title_suggestions(
    channel_title: str,
    dna_brief: str | None,
    clip_transcript: str,
) -> tuple[dict, dict]:
    """Call Claude synchronously; return ``(parsed_result, usage)``.

    Intended to be called via ``asyncio.to_thread`` from async route handlers
    and chat tool executors. Uses ``messages.create`` (non-streaming) because
    per-clip suggestions are short-latency requests returned directly in the
    HTTP response — no SSE task queue needed.

    Args:
        channel_title: The creator's YouTube channel name.
        dna_brief: The creator's plain-language DNA brief, or None if not yet built.
        clip_transcript: The clip's transcript excerpt (treated as untrusted content).

    Returns:
        A tuple of (result_dict, usage_dict). result_dict has keys ``titles``,
        ``hook_rewrites``, and ``disclaimer``. usage_dict has keys
        ``input_tokens``, ``output_tokens``, ``cache_read``, ``cache_creation``.

    Raises:
        RateLimitError, APIStatusError, APIConnectionError: propagated to the
        caller so it can handle them with the appropriate HTTP status code.
        ValueError: if Claude returns malformed JSON or a schema-invalid response.
    """
    transcript_excerpt = clip_transcript[:_TRANSCRIPT_MAX_CHARS]
    system, messages = _build_request(channel_title, dna_brief, transcript_excerpt)

    try:
        response = _ANTHROPIC.messages.create(
            model=settings.ANTHROPIC_MODEL_CLIP_TITLES,
            max_tokens=1024,
            system=system,  # type: ignore[arg-type]
            messages=messages,  # type: ignore[arg-type]
        )
    except (RateLimitError, APIStatusError, APIConnectionError) as exc:
        logger.error("clip_title_suggestions LLM error exc_type=%s", type(exc).__name__)
        raise

    usage_dict = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_read": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        "cache_creation": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
    }
    logger.info(
        "clip_title_suggestions tokens: in=%d cached_read=%d cached_write=%d out=%d",
        usage_dict["input_tokens"],
        usage_dict["cache_read"],
        usage_dict["cache_creation"],
        usage_dict["output_tokens"],
    )
    record_llm_metric(settings.ANTHROPIC_MODEL_CLIP_TITLES, usage_dict)

    raw = next((b.text for b in response.content if b.type == "text"), "")
    result = _parse_result(raw)
    return result, usage_dict
