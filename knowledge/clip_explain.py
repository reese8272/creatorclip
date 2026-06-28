"""Per-clip "Explain this clip" — Why-This-Clip LLM narrative (Issue 325).

Generates a 2–4 sentence plain-language explanation of why THIS moment fits
THIS creator's channel, grounded in the creator's DNA brief + the clip's score
breakdown + the named clipping principle the engine already cited.

The explanation MUST reference one of the canonical named principles from
docs/CLIPPING_PRINCIPLES.md. The list of valid principles is embedded in the
static system block below and verified by a structural test.

Prompt structure (three system blocks):
  Block 1 — static instructions + principle list + UNTRUSTED_CONTENT_POLICY.
  Block 2 — DNA brief (cache_control ttl=1h breakpoint).
  Block 3 — per-clip factual context (clip score, principle, timing). Uncached.

Clip transcript is UNTRUSTED — wrapped via wrap_untrusted in the user turn.
Honesty disclaimer always appended by Python, never by the model.
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

_DNA_BRIEF_MAX_CHARS = 3000
_TRANSCRIPT_MAX_CHARS = 1200

DISCLAIMER = (
    "This explanation is an estimate grounded in your channel data and the clip's "
    "score breakdown. AutoClip does not promise specific performance outcomes."
)

# Canonical principle names from docs/CLIPPING_PRINCIPLES.md.
# The structural test (tests/test_clip_explain.py) verifies that the system prompt
# contains these names — update both files together when CLIPPING_PRINCIPLES.md changes.
VALID_PRINCIPLES: frozenset[str] = frozenset(
    [
        "Hook in the first 3 seconds",
        "Clip the setup, not the aftermath",
        "Tension and release",
        "Pattern interrupt",
        "Dead-air elimination",
        "Retention curve is ground truth",
        "Loop-ability",
        "Front-load value",
        "One idea per Short",
        "Native length over generic length",
        "Audience-fit over generic virality",
        "Clean Context Boundary",
    ]
)

# Block 1: static — byte-identical across all calls. Embeds the principle list so
# the model can only cite a real principle from CLIPPING_PRINCIPLES.md.
_SYSTEM_INSTRUCTIONS = f"""\
{UNTRUSTED_CONTENT_POLICY}
You are CreatorClip's transparency engine. Your task: write a 2–4 sentence \
plain-language explanation of why a specific clip moment was selected for THIS \
creator's channel. The explanation must be grounded in the creator's DNA profile \
and must explicitly cite ONE of the following named clipping principles:

Named principles (cite EXACTLY one by its full name):
  - Hook in the first 3 seconds
  - Clip the setup, not the aftermath
  - Tension and release
  - Pattern interrupt
  - Dead-air elimination
  - Retention curve is ground truth
  - Loop-ability
  - Front-load value
  - One idea per Short
  - Native length over generic length
  - Audience-fit over generic virality
  - Clean Context Boundary

Honesty constraints (strictly enforced):
  - Use hedged language: "likely", "suggests", "based on channel data", "estimated".
  - NEVER say "will", "guaranteed", "promise", "go viral", or any virality language.
  - The explanation is an estimate grounded in this creator's data — not a guarantee.

Return ONLY a valid JSON object with this exact schema — no preamble, no commentary:
{{
  "explanation": "<2–4 sentences grounded in DNA + score; cites the principle by exact name>",
  "cited_principle": "<exact principle name from the list above>"
}}

Rules:
  - ``explanation``: 2–4 sentences, plain language, hedged, audience-specific.
  - ``cited_principle``: MUST be one of the exact names from the list above — copy it verbatim.
  - Return valid JSON ONLY."""


def _build_request(
    channel_title: str,
    dna_brief: str | None,
    clip_principle: str,
    clip_score: float | None,
    clip_start_s: float,
    clip_end_s: float,
    clip_transcript: str,
) -> tuple[list[dict], list[dict]]:
    """Assemble (system_blocks, messages) for the clip-explain call.

    Block 2 carries the cache breakpoint (ttl=1h) so same-creator calls share
    the cached DNA-prefix read. Clip transcript is JSON-wrapped in the user turn
    to prevent prompt-injection break-out. (Issue 325 / 218)
    """
    dna_text = (dna_brief or "No DNA profile available yet.")[:_DNA_BRIEF_MAX_CHARS]

    score_text = f"{clip_score:.2f}" if clip_score is not None else "not scored"
    timing_text = (
        f"{clip_start_s:.1f}s – {clip_end_s:.1f}s "
        f"(duration: {clip_end_s - clip_start_s:.1f}s)"
    )

    system: list[dict] = [
        # Block 1: static instructions + principle list — never contains creator data.
        {"type": "text", "text": _SYSTEM_INSTRUCTIONS},
        # Block 2: DNA brief — stable per-creator, cached 1h.
        {
            "type": "text",
            "text": f"CREATOR DNA PROFILE:\n{dna_text}",
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
        # Block 3: per-clip factual context — uncached.
        {
            "type": "text",
            "text": (
                f"CLIP DETAILS:\n"
                f"Channel: {channel_title}\n"
                f"Score (fit estimate): {score_text}\n"
                f"Principle cited by engine: {clip_principle or 'not specified'}\n"
                f"Timing: {timing_text}"
            ),
        },
    ]

    # Clip transcript is untrusted attacker-influenceable content.
    user_content = (
        wrap_untrusted("clip_transcript", clip_transcript)
        + f"Explain why this clip was selected for '{channel_title}'. "
        "Return the JSON object."
    )
    messages: list[dict] = [{"role": "user", "content": user_content}]
    return system, messages


def _parse_result(raw_json: str) -> dict:
    """Parse and validate the structured JSON response.

    Verifies that ``cited_principle`` is one of the canonical VALID_PRINCIPLES.
    Raises ValueError on malformed JSON, missing fields, or an unknown principle —
    this prevents the model from hallucinating principle names.
    """
    data = json.loads(raw_json)

    explanation = str(data.get("explanation", "")).strip()
    if not explanation:
        raise ValueError("Response missing 'explanation' field")

    cited = str(data.get("cited_principle", "")).strip()
    if cited not in VALID_PRINCIPLES:
        raise ValueError(
            f"Model cited unknown principle {cited!r}. "
            f"Must be one of: {sorted(VALID_PRINCIPLES)}"
        )

    return {
        "explanation": explanation,
        "cited_principle": cited,
        "disclaimer": DISCLAIMER,
    }


def generate_clip_explanation(
    channel_title: str,
    dna_brief: str | None,
    clip_principle: str,
    clip_score: float | None,
    clip_start_s: float,
    clip_end_s: float,
    clip_transcript: str,
) -> tuple[dict, dict]:
    """Call Claude synchronously; return ``(parsed_result, usage)``.

    Intended to be called via ``asyncio.to_thread`` from async route handlers.
    Uses ``messages.create`` (non-streaming) — short-latency direct response.

    The returned dict always includes ``cited_principle``, which must be one of
    the canonical principles from docs/CLIPPING_PRINCIPLES.md. A ValueError is
    raised if the model deviates — the caller should surface a 502.

    Args:
        channel_title: The creator's YouTube channel name.
        dna_brief: The creator's plain-language DNA brief, or None.
        clip_principle: The principle name the clip-engine cited for this clip.
        clip_score: The clip's fit score (0–1), or None if not scored.
        clip_start_s: Clip start time in seconds.
        clip_end_s: Clip end time in seconds.
        clip_transcript: The clip's transcript excerpt (untrusted content).

    Returns:
        A tuple of (result_dict, usage_dict). result_dict has keys
        ``explanation``, ``cited_principle``, and ``disclaimer``.

    Raises:
        RateLimitError, APIStatusError, APIConnectionError: propagated to caller.
        ValueError: malformed JSON or unknown cited principle.
    """
    transcript_excerpt = clip_transcript[:_TRANSCRIPT_MAX_CHARS]
    system, messages = _build_request(
        channel_title,
        dna_brief,
        clip_principle,
        clip_score,
        clip_start_s,
        clip_end_s,
        transcript_excerpt,
    )

    try:
        response = _ANTHROPIC.messages.create(
            model=settings.ANTHROPIC_MODEL_CLIP_EXPLAIN,
            max_tokens=512,
            system=system,  # type: ignore[arg-type]
            messages=messages,  # type: ignore[arg-type]
        )
    except (RateLimitError, APIStatusError, APIConnectionError) as exc:
        logger.error("clip_explain LLM error exc_type=%s", type(exc).__name__)
        raise

    usage_dict = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_read": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        "cache_creation": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
    }
    logger.info(
        "clip_explain tokens: in=%d cached_read=%d cached_write=%d out=%d",
        usage_dict["input_tokens"],
        usage_dict["cache_read"],
        usage_dict["cache_creation"],
        usage_dict["output_tokens"],
    )
    record_llm_metric(settings.ANTHROPIC_MODEL_CLIP_EXPLAIN, usage_dict)
    warn_if_truncated(settings.ANTHROPIC_MODEL_CLIP_EXPLAIN, getattr(response, "stop_reason", None))

    raw = next((b.text for b in response.content if b.type == "text"), "")
    result = _parse_result(raw)
    return result, usage_dict
