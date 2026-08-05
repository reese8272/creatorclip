"""Batched auto-metadata — suggested title/description/hook per clip (Issue 417).

ONE structured-output Sonnet call per video covers ALL ranked clips, each
grounded in its OWN transcript window (``extract_transcript_window``, Issue
414). The pipeline persists the results as ``clips.suggested_*`` (migration
0054); ``applied_*`` stays creator-typed only, and publish falls back
applied_* → suggested_* → (video.title | "#Shorts").

Prompt structure (Issue 315 discipline):
  Block 1 — static honesty rubric + output rules (creator-independent,
            byte-identical across all calls).
  Block 2 — DNA brief via ``dna_system_block`` (1h cache marker floor-gated).
  Block 3 — video-context summary (per-video, uncached — changes every video).
  User msg — per-clip payloads; each clip's transcript window rides through
            ``wrap_untrusted`` (OWASP LLM01; Issue 224).

Python clamps every field to the YouTube videos.insert limits BEFORE storage:
title ≤100 chars, description ≤5000 UTF-8 BYTES (char-boundary safe) with
``#Shorts`` guaranteed and angle brackets stripped, hook ≤200 chars. NO
disclaimer is persisted — disclaimers belong to suggestion UIs and stay
Python-appended on the on-demand ("Regenerate") endpoints.
"""

from __future__ import annotations

import json
import logging

import httpx
from anthropic import APIConnectionError, APIStatusError, AsyncAnthropic, RateLimitError

from config import settings
from knowledge.util import (
    UNTRUSTED_CONTENT_POLICY,
    dna_system_block,
    extract_json_block,
    has_1h_cache_marker,
    wrap_untrusted,
)
from observability import record_llm_metric, warn_if_truncated

logger = logging.getLogger(__name__)

# Module-level AsyncAnthropic singleton (Issue 82a) — prefork-safe lazy pool bind.
_ANTHROPIC = AsyncAnthropic(
    api_key=settings.ANTHROPIC_API_KEY,
    timeout=httpx.Timeout(120.0, connect=10.0),
    max_retries=2,
)

TITLE_MAX_CHARS = 100  # YouTube hard limit
DESCRIPTION_MAX_BYTES = 5000  # YouTube hard limit (UTF-8 bytes)
HOOK_MAX_CHARS = 200

_DNA_BRIEF_MAX_CHARS = 3000
_CONTEXT_SUMMARY_MAX_CHARS = 1200
_WINDOW_MAX_CHARS = 1200

# Block 1: static — never contains per-creator or per-video data.
_SYSTEM_INSTRUCTIONS = f"""\
{UNTRUSTED_CONTENT_POLICY}
You are a YouTube Shorts metadata writer. For EVERY clip in the batch, write:
  - title: a Short-format title (≤{TITLE_MAX_CHARS} chars) — punchy, front-loaded, \
scannable in a feed, fitted to THIS creator's channel style and audience.
  - description: 1-3 sentences plus at most 3 hashtags INCLUDING #Shorts. No links, \
no angle brackets, ≤4500 characters.
  - hook: a spoken/overlay opening line for the clip (≤{HOOK_MAX_CHARS} chars) that \
front-loads the clip's value.

Honesty constraints (strictly enforced):
  - Ground every field in the clip's OWN transcript window — never invent claims \
the clip does not make.
  - NEVER write "will go viral", "guaranteed", "promise", or any virality language.
  - Titles and hooks are estimates of fit for this creator's audience, not \
predictions of performance.

Return ONLY a valid JSON object:
{{
  "clips": [
    {{
      "clip_id": "<the id string given in the payload>",
      "title": "<≤{TITLE_MAX_CHARS} chars>",
      "description": "<1-3 sentences + ≤3 hashtags incl. #Shorts>",
      "hook": "<≤{HOOK_MAX_CHARS} chars>"
    }}
  ]
}}

Rules:
  - Return EXACTLY one entry per clip in the payload, keyed by its clip_id.
  - No preamble, no commentary, valid JSON only."""

# Native structured-output schema (additionalProperties: false throughout).
_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "clip_id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "hook": {"type": "string"},
                },
                "required": ["clip_id", "title", "description", "hook"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["clips"],
    "additionalProperties": False,
}


def clamp_title(raw: str) -> str:
    """Title ≤100 chars, angle brackets stripped (YouTube rejects them)."""
    return raw.replace("<", "").replace(">", "").strip()[:TITLE_MAX_CHARS]


def clamp_description(raw: str) -> str:
    """Description ≤5000 UTF-8 bytes (char-boundary safe), no angle brackets,
    ``#Shorts`` guaranteed present."""
    text = raw.replace("<", "").replace(">", "").strip()
    if "#shorts" not in text.lower():
        text = f"{text}\n\n#Shorts" if text else "#Shorts"
    encoded = text.encode("utf-8")
    if len(encoded) > DESCRIPTION_MAX_BYTES:
        # Truncate on a character boundary: decode ignoring the split rune.
        text = encoded[:DESCRIPTION_MAX_BYTES].decode("utf-8", errors="ignore").rstrip()
    return text


def clamp_hook(raw: str) -> str:
    """Hook ≤200 chars."""
    return raw.replace("<", "").replace(">", "").strip()[:HOOK_MAX_CHARS]


def _build_request(
    channel_title: str,
    dna_brief: str | None,
    context_summary: str | None,
    clip_payloads: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Assemble (system_blocks, messages) for the batched metadata call.

    ``clip_payloads``: ``[{"clip_id", "rank", "window_text"}]`` — each window
    already extracted from the clip's own span. Window text is untrusted and
    wrapped per clip inside the user turn.
    """
    dna_text = (dna_brief or "No DNA profile available yet.")[:_DNA_BRIEF_MAX_CHARS]

    system: list[dict] = [
        {"type": "text", "text": _SYSTEM_INSTRUCTIONS},
        # Block 2: DNA brief — 1h cache marker gated on the measured prefix floor.
        dna_system_block(_SYSTEM_INSTRUCTIONS, dna_text),
    ]
    # Block 3: per-video context summary — uncached, after the cache breakpoint.
    if context_summary:
        system.append(
            {
                "type": "text",
                "text": "VIDEO CONTEXT:\n" + context_summary[:_CONTEXT_SUMMARY_MAX_CHARS],
            }
        )

    parts = [f"Channel: {channel_title}\nClips to write metadata for:\n"]
    for p in clip_payloads:
        parts.append(f"clip_id: {p['clip_id']} (rank {p.get('rank')})\n")
        parts.append(wrap_untrusted(f"clip_transcript_{p['clip_id']}", p.get("window_text", "")))
    parts.append("Write title/description/hook for every clip above. Return the JSON object.")
    messages: list[dict] = [{"role": "user", "content": "".join(parts)}]
    return system, messages


def _parse_result(raw_json: str, expected_ids: set[str]) -> dict[str, dict]:
    """Parse + clamp the batch response into ``{clip_id: {title, description,
    hook}}``. Unknown clip_ids are dropped; missing clips simply stay NULL in
    the DB (the fill-only redelivery can retry them). Raises ValueError on a
    structurally malformed response."""
    data = json.loads(extract_json_block(raw_json))
    rows = data.get("clips")
    if not isinstance(rows, list):
        raise ValueError("Response missing 'clips' list")

    out: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        clip_id = str(row.get("clip_id", "")).strip()
        if clip_id not in expected_ids:
            continue
        title = clamp_title(str(row.get("title", "")))
        if not title:
            continue
        out[clip_id] = {
            "title": title,
            "description": clamp_description(str(row.get("description", ""))),
            "hook": clamp_hook(str(row.get("hook", ""))),
        }
    return out


async def generate_clip_metadata_batch(
    channel_title: str,
    dna_brief: str | None,
    context_summary: str | None,
    clip_payloads: list[dict],
) -> tuple[dict[str, dict], dict]:
    """ONE batched call for all ranked clips; returns ``(results, usage)``.

    ``results`` maps clip_id → clamped ``{title, description, hook}``. ``usage``
    is the house usage-dict shape (input/output/cache_read/cache_creation/
    cache_1h) so the caller bills the ledger after the round-trip.

    Raises RateLimitError / APIStatusError / APIConnectionError / ValueError —
    the worker task lets Celery retry (max_retries=2); terminal failure leaves
    the suggested_* columns NULL and every clip fully usable.
    """
    if not clip_payloads:
        return {}, {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read": 0,
            "cache_creation": 0,
            "cache_1h": False,
        }

    system, messages = _build_request(channel_title, dna_brief, context_summary, clip_payloads)

    from verbose import vlog_llm_request, vlog_llm_response

    vlog_llm_request(
        "clip_metadata_batch",
        model=settings.ANTHROPIC_MODEL_CLIP_METADATA,
        max_tokens=2000,
        system=system,
        messages=messages,
    )
    try:
        response = await _ANTHROPIC.messages.create(  # type: ignore[call-overload]
            model=settings.ANTHROPIC_MODEL_CLIP_METADATA,
            max_tokens=2000,
            system=system,
            messages=messages,
            output_config={"format": {"type": "json_schema", "schema": _OUTPUT_SCHEMA}},
        )
    except (RateLimitError, APIStatusError, APIConnectionError) as exc:
        logger.error("clip_metadata_batch LLM error exc_type=%s", type(exc).__name__)
        raise
    vlog_llm_response("clip_metadata_batch", response=response)

    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_read": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        "cache_creation": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        "cache_1h": has_1h_cache_marker(system),
    }
    logger.info(
        "clip_metadata_batch tokens: in=%d cached_read=%d cached_write=%d out=%d clips=%d",
        usage["input_tokens"],
        usage["cache_read"],
        usage["cache_creation"],
        usage["output_tokens"],
        len(clip_payloads),
    )
    record_llm_metric(settings.ANTHROPIC_MODEL_CLIP_METADATA, usage)
    warn_if_truncated(
        settings.ANTHROPIC_MODEL_CLIP_METADATA, getattr(response, "stop_reason", None)
    )

    raw = next((b.text for b in response.content if b.type == "text"), "")
    expected_ids = {str(p["clip_id"]) for p in clip_payloads}
    return _parse_result(raw, expected_ids), usage
