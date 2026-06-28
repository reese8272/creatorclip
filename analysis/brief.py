"""Video performance analysis via Claude (Issue 121).

Prompt structure (Issue 218; cache marker removed Issue 315):
  Block 1 — static instructions: role + task (stable, creator-agnostic). No
            cache_control: ~410 tokens alone, below Sonnet 4.6's 1024-token floor.
  Block 2 — DNA brief (when available): per-creator context, capped at 1000 chars
            (~250 tokens). Block 1 + Block 2 max ≈ 660 tokens — still below the
            1024-token cacheable floor. Cache marker dropped in Issue 315: an inert
            marker incurs the write-premium charge without any cache reads.
  Block 3 — per-video data: metrics, retention, channel averages. Uncached.

The analysis does NOT use web_search — the creator's own metrics + DNA are the
authority. No tools means shorter latency and a single text block in the response.

The honesty disclaimer is always appended by Python.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from anthropic import Anthropic, APIConnectionError, APIStatusError, RateLimitError

from config import settings
from knowledge.util import UNTRUSTED_CONTENT_POLICY
from observability import record_llm_metric

logger = logging.getLogger(__name__)

_ANTHROPIC = Anthropic(
    api_key=settings.ANTHROPIC_API_KEY,
    timeout=httpx.Timeout(120.0, connect=10.0),
    max_retries=2,
)

_DISCLAIMER = (
    "\n\n---\n"
    "*This analysis is grounded in your channel data. "
    "AutoClip does not promise virality or specific growth outcomes.*"
)

# Static instruction block — carries the cache breakpoint (same pattern as
# improvement/brief.py and dna/brief.py).
_SYSTEM_INSTRUCTIONS = f"""\
{UNTRUSTED_CONTENT_POLICY}
You are a YouTube content strategist who deeply understands why videos succeed or fail.

The creator's DNA profile (their channel patterns, audience, and style) and available
metrics for the specific video are provided in the next block. Use them as your primary
source of truth — this is about THIS creator's channel, not generic advice.

Your job is to answer the creator's specific question directly and honestly:
- Reference exact numbers (views, retention, engagement) when available
- Compare against this creator's own channel averages, not industry benchmarks
- Identify concrete, specific reasons — hook quality, pacing, topic fit, publish timing, etc.
- If data is limited (e.g. video not yet in their catalog), say what you can infer and
  what you cannot determine from available information
- Be conversational, not listy — this is a conversation, not a report

Keep the response focused on what the creator asked. Do not pad with generic advice."""

_DNA_BRIEF_MAX_CHARS = 1000


def _build_request(
    channel_title: str,
    youtube_video_id: str,
    video_title: str | None,
    query: str,
    video_metrics: dict[str, Any] | None,
    retention_summary: dict[str, Any] | None,
    channel_avg: dict[str, Any] | None,
    dna_brief: str | None,
) -> tuple:
    """Assemble (system, messages) for the video analysis call.

    Three-block system when a DNA brief is available, two-block otherwise:
      Block 1: static instructions (stable, creator-agnostic). ~410 tokens.
      Block 2: DNA brief (stable per-creator, capped at 1000 chars ≈ 250 tokens).
               No cache_control — block 1 + block 2 max ≈ 660 tokens, below
               Sonnet 4.6's 1024-token cacheable floor. (Issue 315 — marker removed.)
      Block 3: per-video data (metrics, retention, channel averages).

    When no DNA brief is available, block 2 is omitted (two-block call, uncached).
    """
    video_data: dict[str, Any] = {"youtube_video_id": youtube_video_id}
    if video_title:
        video_data["title"] = video_title
    if video_metrics:
        video_data["metrics"] = video_metrics
    if retention_summary:
        video_data["retention_curve_checkpoints"] = retention_summary

    context: dict[str, Any] = {
        "channel": channel_title,
        "video": video_data,
    }
    if channel_avg:
        context["channel_averages"] = channel_avg

    context_json = json.dumps(context, indent=2, default=str)

    system: list[dict] = [{"type": "text", "text": _SYSTEM_INSTRUCTIONS}]

    if dna_brief:
        # DNA brief is a stable per-creator block — no cache_control: block 1 (~410 tok)
        # + block 2 (~250 tok max) = ~660 tokens, below Sonnet 4.6's 1024-token cacheable
        # floor. An inert marker incurs the write-premium charge with zero cache reads.
        # Marker dropped Issue 315. (Issue 218 originally added it; measurement showed
        # the floor was never cleared.)
        system.append(
            {
                "type": "text",
                "text": f"CREATOR DNA:\n{dna_brief[:_DNA_BRIEF_MAX_CHARS]}",
            }
        )

    # Per-video data is always in the final, uncached block.
    system.append({"type": "text", "text": f"CREATOR AND VIDEO DATA:\n{context_json}"})

    messages = [{"role": "user", "content": query}]
    return system, messages


def generate_video_analysis(
    channel_title: str,
    youtube_video_id: str,
    video_title: str | None,
    query: str,
    video_metrics: dict[str, Any] | None = None,
    retention_summary: dict[str, Any] | None = None,
    channel_avg: dict[str, Any] | None = None,
    dna_brief: str | None = None,
    task_id: str | None = None,
) -> tuple[str, dict]:
    """Call Claude to analyze a video's performance, optionally streaming via SSE.

    Args:
        task_id: When set, switches to the streaming path — token deltas flow to
            ``task:{task_id}:events`` via ``worker.anthropic_stream.stream_and_emit``,
            exactly as the improvement brief does. When None, uses ``.create()``.

    Returns ``(analysis_text, usage)`` where usage is the token-count dict. Callers
    should pass usage to ``billing.ledger.record_llm_usage`` to populate the cost ledger.
    """
    system, messages = _build_request(
        channel_title,
        youtube_video_id,
        video_title,
        query,
        video_metrics,
        retention_summary,
        channel_avg,
        dna_brief,
    )

    if task_id is not None:
        from worker.anthropic_stream import stream_and_emit

        client = _ANTHROPIC.with_options(timeout=120.0)
        try:
            final_text, usage = stream_and_emit(
                client,
                task_id,
                model=settings.ANTHROPIC_MODEL_ANALYSIS,
                max_tokens=2000,
                system=system,
                messages=messages,
            )
        except (RateLimitError, APIStatusError, APIConnectionError) as exc:
            logger.error(
                "video_analysis LLM error task=%s exc_type=%s", task_id, type(exc).__name__
            )
            raise
        logger.info(
            "video_analysis streaming tokens: in=%d cache_read=%d cache_write=%d out=%d",
            usage["input_tokens"],
            usage["cache_read"],
            usage["cache_creation"],
            usage["output_tokens"],
        )
        record_llm_metric(settings.ANTHROPIC_MODEL_ANALYSIS, usage)
        return final_text + _DISCLAIMER, usage

    try:
        response = _ANTHROPIC.with_options(timeout=120.0).messages.create(
            model=settings.ANTHROPIC_MODEL_ANALYSIS,
            max_tokens=2000,
            system=system,
            messages=messages,
        )
    except (RateLimitError, APIStatusError, APIConnectionError) as exc:
        logger.error("video_analysis LLM error exc_type=%s", type(exc).__name__)
        raise
    _tokens_in = response.usage.input_tokens
    _tokens_out = response.usage.output_tokens
    logger.info(
        "video_analysis tokens: in=%d cache_read=%d cache_write=%d out=%d",
        _tokens_in,
        getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        _tokens_out,
    )
    text_blocks = [b for b in response.content if b.type == "text"]
    if not text_blocks:
        raise RuntimeError("Claude returned no text in video analysis")
    _usage = {
        "input_tokens": _tokens_in,
        "output_tokens": _tokens_out,
        "cache_read": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        "cache_creation": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
    }
    record_llm_metric(settings.ANTHROPIC_MODEL_ANALYSIS, _usage)
    return text_blocks[-1].text + _DISCLAIMER, _usage
