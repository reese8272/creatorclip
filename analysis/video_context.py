"""Whole-video context pass (Issue 415, L26 Track A).

ONE Sonnet call over the FULL transcript — no chunking (a 120-min transcript is
~26K tokens, well inside the window). The model returns a validated context
payload: summary, structure, narrative arcs, tone, audience relevance, and up
to ``LLM_CANDIDATES_MAX`` proposed clip *moments* judged on the VALUE IN THE
WORDS — the capability the audio-energy heuristic cannot see (a great story
delivered flat). Moments feed the Issue-416 hybrid candidate merge.

Prompt-cache structure (Issue 315 discipline):
  Block 1 — static instructions + the 12 named principles + output guidance.
            Byte-identical across all creators and calls.
  Block 2 — per-creator identity (dna/identity.py::format_for_prompt) + DNA
            brief. cache_control {ephemeral, ttl:1h} is attached only when the
            measured block1+block2 prefix clears Sonnet 4.6's 1024-token
            cacheable floor (chars/4 rule — the scoring.py gate).
  User msg — volatile per-video transcript, wrap_untrusted-wrapped (OWASP
            LLM01; Issue 224). Never cached.

Structured output is forced via ``output_config.format`` (json_schema with
``additionalProperties: false`` throughout) so a markdown fence can never break
parsing; ``extract_json_block`` stays as defense-in-depth (Issue 342 pattern).

Transcript rendering: ~30s paragraphs prefixed with ``[512s]`` second markers.
Over ``VIDEO_CONTEXT_TRANSCRIPT_MAX_CHARS`` the render is coarsened
PER-PARAGRAPH (each paragraph's text capped proportionally — the chapters.py
per-segment-cap precedent) so coverage spans the whole video; the tail is never
truncated away.
"""

from __future__ import annotations

import json
import logging

import httpx
from anthropic import APIConnectionError, APIStatusError, AsyncAnthropic, RateLimitError

from config import settings
from knowledge.util import (
    UNTRUSTED_CONTENT_POLICY,
    extract_json_block,
    has_1h_cache_marker,
    wrap_untrusted,
)
from observability import record_llm_metric, warn_if_truncated

logger = logging.getLogger(__name__)

# Module-level AsyncAnthropic singleton (Issue 82a) — prefork-safe lazy pool bind.
_ANTHROPIC = AsyncAnthropic(
    api_key=settings.ANTHROPIC_API_KEY,
    timeout=httpx.Timeout(180.0, connect=10.0),
    max_retries=2,
)

# Bump whenever the prompt or context_jsonb schema changes (stored on the row).
PROMPT_VERSION = 1

# Paragraph granularity for the rendered transcript (seconds).
_PARAGRAPH_S = 30.0

# The 12 named principles from docs/CLIPPING_PRINCIPLES.md. Moment validation
# rejects any citation outside this registry (exact-match, same contract as the
# scorer and the eval harness's NAMED_PRINCIPLES).
CLIPPING_PRINCIPLES: tuple[str, ...] = (
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
)

# Sonnet 4.6's minimum cacheable-prefix size (tokens), chars/4 estimate — the
# scoring.py Issue-315 gate.
_CACHE_FLOOR_TOKENS = 1024

# Block 1: static — never contains per-creator or per-video data. Byte-identical
# across all calls so the cache marker on block 2 is never invalidated.
_SYSTEM_STATIC = (
    UNTRUSTED_CONTENT_POLICY
    + """\
You are a long-form video analyst for a clip-selection engine.

You will receive a creator's FULL video transcript, rendered as timestamped
paragraphs ("[512s]" = the paragraph starts 512 seconds in). Analyze the whole
video and return a structured context payload.

NAMED PRINCIPLES (moment citations must use exactly one of these):
{principles}

ANALYSIS TASK — return a JSON object with:
- summary: 2-4 sentences on what this video is and delivers.
- structure: coarse sections of the video as {{start_s, end_s, label}} entries
  (chronological, non-overlapping, covering the video).
- narrative_arcs: the stories/threads that build and resolve across the video.
- tone: the delivery style in one short phrase.
- audience_relevance: 1-2 sentences on who this serves and why they stay.
- moments: up to 4 clip-worthy moments, each {{start_s, end_s, reason,
  principle, confidence}}.

MOMENT SELECTION — the part only you can do:
- Judge the VALUE IN THE WORDS, not the audio energy. A complete story, a
  sharp insight, a vulnerable admission, or a surprising claim delivered
  calmly is exactly what you must surface — the signal pipeline already
  catches loud moments.
- Each moment must be a self-contained 30-90 second span: the setup AND the
  payoff both inside [start_s, end_s].
- start_s begins at the setup of the idea, never mid-thought and never at the
  aftermath or reaction.
- principle: cite EXACTLY one named principle from the list above.
- reason: one sentence on why THIS span works for THIS creator's audience.
- confidence: 0.0-1.0 — your estimate of fit for this creator, not a promise
  of performance. Never promise virality.
- Fewer, stronger moments beat four weak ones. Return an empty list if
  nothing qualifies.
"""
)

# Native structured-output schema — every object carries additionalProperties:
# false (required by the json_schema format).
_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "structure": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_s": {"type": "number"},
                    "end_s": {"type": "number"},
                    "label": {"type": "string"},
                },
                "required": ["start_s", "end_s", "label"],
                "additionalProperties": False,
            },
        },
        "narrative_arcs": {"type": "array", "items": {"type": "string"}},
        "tone": {"type": "string"},
        "audience_relevance": {"type": "string"},
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_s": {"type": "number"},
                    "end_s": {"type": "number"},
                    "reason": {"type": "string"},
                    "principle": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["start_s", "end_s", "reason", "principle", "confidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "structure", "narrative_arcs", "tone", "audience_relevance", "moments"],
    "additionalProperties": False,
}

# Minimum/maximum moment span (seconds). Mirrors the engine's clip geometry:
# candidates.py MIN_CLIP_S / the 60-90s target window.
_MOMENT_MIN_S = 30.0
_MOMENT_MAX_S = 120.0


def render_transcript_paragraphs(
    segments: list[dict],
    max_chars: int | None = None,
    paragraph_s: float = _PARAGRAPH_S,
) -> str:
    """Render transcript segments as ~30s paragraphs with ``[512s]`` markers.

    Segments are bucketed by their start time; each paragraph line is
    ``[<start>s] <joined text>``. When the full render exceeds ``max_chars``,
    each paragraph's text is capped at a proportional per-paragraph budget
    (chapters.py precedent) — coarser everywhere, full coverage, never a
    truncated tail.
    """
    if max_chars is None:
        max_chars = settings.VIDEO_CONTEXT_TRANSCRIPT_MAX_CHARS

    paragraphs: list[tuple[int, list[str]]] = []
    current_idx = -1
    for seg in segments:
        text = seg.get("text", "").strip()
        if not text:
            continue
        idx = int(float(seg.get("start", 0.0)) // paragraph_s)
        if idx != current_idx:
            paragraphs.append((idx, []))
            current_idx = idx
        paragraphs[-1][1].append(text)

    if not paragraphs:
        return ""

    def _render(cap: int | None) -> str:
        lines = []
        for idx, parts in paragraphs:
            body = " ".join(parts)
            if cap is not None:
                body = body[:cap]
            lines.append(f"[{int(idx * paragraph_s)}s] {body}")
        return "\n".join(lines)

    rendered = _render(None)
    if len(rendered) <= max_chars:
        return rendered

    # Coarsen per-paragraph: marker overhead ~12 chars/line; keep a sane floor
    # so a pathological paragraph count still yields usable text per paragraph.
    per_paragraph = max(40, max_chars // len(paragraphs) - 12)
    coarse = _render(per_paragraph)
    logger.info(
        "video_context transcript coarsened: %d chars -> %d chars (%d paragraphs)",
        len(rendered),
        len(coarse),
        len(paragraphs),
    )
    return coarse


def validate_context(data: dict, duration_s: float) -> dict:
    """Validate + clamp the model payload into the stored ``context_jsonb``.

    Moments are bounds-clamped to ``[0, duration_s]`` and DROPPED when the
    principle is not one of the 12 named principles, the span is outside the
    30-120s geometry after clamping, or the fields are malformed. At most
    ``settings.LLM_CANDIDATES_MAX`` moments survive, kept by confidence
    descending. Never raises on malformed entries — invalid items are dropped,
    matching the never-fail posture of the chain member.
    """
    principles = set(CLIPPING_PRINCIPLES)

    structure: list[dict] = []
    for entry in data.get("structure") or []:
        if not isinstance(entry, dict):
            continue
        try:
            start = max(0.0, float(entry.get("start_s", 0.0)))
            end = min(float(duration_s), float(entry.get("end_s", 0.0)))
        except (TypeError, ValueError):
            continue
        label = str(entry.get("label", "")).strip()
        if end > start and label:
            structure.append({"start_s": round(start, 2), "end_s": round(end, 2), "label": label})

    moments: list[dict] = []
    for m in data.get("moments") or []:
        if not isinstance(m, dict):
            continue
        try:
            start = max(0.0, float(m.get("start_s", 0.0)))
            end = min(float(duration_s), float(m.get("end_s", 0.0)))
            confidence = float(m.get("confidence", 0.0))
        except (TypeError, ValueError):
            continue
        principle = str(m.get("principle", "")).strip()
        reason = str(m.get("reason", "")).strip()
        if principle not in principles:
            logger.info("video_context moment dropped: unknown principle %r", principle[:80])
            continue
        span = end - start
        if span < _MOMENT_MIN_S or span > _MOMENT_MAX_S:
            logger.info("video_context moment dropped: span %.1fs outside 30-120s", span)
            continue
        moments.append(
            {
                "start_s": round(start, 2),
                "end_s": round(end, 2),
                "reason": reason,
                "principle": principle,
                "confidence": min(1.0, max(0.0, confidence)),
            }
        )
    moments.sort(key=lambda m: m["confidence"], reverse=True)
    moments = moments[: settings.LLM_CANDIDATES_MAX]
    # Chronological for downstream readability (merge re-sorts anyway).
    moments.sort(key=lambda m: m["start_s"])

    narrative_arcs = [str(a).strip() for a in (data.get("narrative_arcs") or []) if str(a).strip()]

    return {
        "version": PROMPT_VERSION,
        "summary": str(data.get("summary", "")).strip(),
        "structure": structure,
        "narrative_arcs": narrative_arcs,
        "tone": str(data.get("tone", "")).strip(),
        "audience_relevance": str(data.get("audience_relevance", "")).strip(),
        "moments": moments,
    }


def _build_request(
    transcript_rendered: str,
    duration_s: float,
    dna_brief: str | None,
    identity_text: str | None,
) -> tuple[list[dict], list[dict]]:
    """Assemble (system_blocks, messages) for the whole-video context call.

    Block 2 (identity + DNA) carries the 1h cache marker only when the measured
    block1+block2 prefix clears the 1024-token floor. When neither identity nor
    brief exists the block is omitted entirely (format_for_prompt convention —
    a "(none)" placeholder would only add volatile bytes).
    """
    static_text = _SYSTEM_STATIC.format(principles="\n".join(f"- {p}" for p in CLIPPING_PRINCIPLES))
    system: list[dict] = [{"type": "text", "text": static_text}]

    creator_parts = []
    if identity_text:
        creator_parts.append(identity_text)
    if dna_brief:
        creator_parts.append(f"CREATOR DNA:\n{dna_brief}")
    if creator_parts:
        creator_block: dict = {"type": "text", "text": "\n\n".join(creator_parts)}
        if (len(static_text) + len(creator_block["text"])) // 4 >= _CACHE_FLOOR_TOKENS:
            creator_block["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
        system.append(creator_block)

    user_content = (
        wrap_untrusted("video_transcript", transcript_rendered)
        + f"Video duration: {duration_s:.0f}s. Analyze the full transcript and "
        "return the structured context JSON."
    )
    return system, [{"role": "user", "content": user_content}]


async def build_video_context(
    segments: list[dict],
    duration_s: float,
    dna_brief: str | None = None,
    identity_text: str | None = None,
) -> tuple[dict, dict]:
    """Call Claude once over the full transcript; return ``(context, usage)``.

    ``context`` is the validated ``context_jsonb`` payload (see
    ``validate_context``); ``usage`` follows the house usage-dict shape
    (input/output/cache_read/cache_creation/cache_1h) so the caller can bill
    the ledger AFTER the round-trip (scoring.py pattern).

    Raises RateLimitError / APIStatusError / APIConnectionError / ValueError —
    the ``analyze_video_context`` task catches everything and degrades to the
    signal-only pipeline.
    """
    transcript_rendered = render_transcript_paragraphs(segments)
    if not transcript_rendered:
        raise ValueError("empty transcript — nothing to analyze")

    system, messages = _build_request(transcript_rendered, duration_s, dna_brief, identity_text)

    from verbose import vlog_llm_request, vlog_llm_response

    vlog_llm_request(
        "video_context",
        model=settings.ANTHROPIC_MODEL_VIDEO_CONTEXT,
        max_tokens=2000,
        system=system,
        messages=messages,
    )
    try:
        response = await _ANTHROPIC.messages.create(  # type: ignore[call-overload]
            model=settings.ANTHROPIC_MODEL_VIDEO_CONTEXT,
            max_tokens=2000,
            system=system,
            messages=messages,
            output_config={"format": {"type": "json_schema", "schema": _OUTPUT_SCHEMA}},
        )
    except (RateLimitError, APIStatusError, APIConnectionError) as exc:
        logger.error("video_context LLM error exc_type=%s", type(exc).__name__)
        raise
    vlog_llm_response("video_context", response=response)

    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_read": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        "cache_creation": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        # 1h-TTL cache writes bill 2× — billing passes cache_write_multiplier=2.0
        # only when the floor-gated marker actually attached.
        "cache_1h": has_1h_cache_marker(system),
    }
    logger.info(
        "video_context tokens: in=%d cached_read=%d cached_write=%d out=%d cache_1h=%s",
        usage["input_tokens"],
        usage["cache_read"],
        usage["cache_creation"],
        usage["output_tokens"],
        usage["cache_1h"],
    )
    record_llm_metric(settings.ANTHROPIC_MODEL_VIDEO_CONTEXT, usage)
    warn_if_truncated(
        settings.ANTHROPIC_MODEL_VIDEO_CONTEXT, getattr(response, "stop_reason", None)
    )

    raw = next((b.text for b in response.content if b.type == "text"), "")
    data = json.loads(extract_json_block(raw))  # ValueError/JSONDecodeError → caller degrades
    if not isinstance(data, dict):
        raise ValueError("video_context response is not a JSON object")
    return validate_context(data, duration_s), usage
