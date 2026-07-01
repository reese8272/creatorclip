"""
Generate a content-improvement brief using Claude with the web_search tool.

Prompt structure (Issue 69): a STATIC instruction block carries the
``cache_control`` breakpoint; the per-creator analytics are a SEPARATE, uncached
block after it. (The static prefix is below Sonnet 4.6's minimum cacheable size,
so the cache does not engage for this low-frequency call — see docs/DECISIONS.md.)
Claude calls web_search, so the response interleaves text/tool_use blocks — the
FINAL text block is the answer.

The honesty disclaimer is always appended by Python — never left to the LLM.
"""

import json
import logging
from collections.abc import Mapping

import httpx
from anthropic import Anthropic, APIConnectionError, APIStatusError, RateLimitError

from config import settings
from knowledge.util import UNTRUSTED_CONTENT_POLICY
from observability import log_llm_error, record_llm_metric, warn_if_truncated

logger = logging.getLogger(__name__)

_ANTHROPIC = Anthropic(
    api_key=settings.ANTHROPIC_API_KEY,
    timeout=httpx.Timeout(60.0, connect=10.0),
    max_retries=2,
)

_DISCLAIMER = (
    "\n\n---\n"
    "*These recommendations are estimates grounded in your channel data and publicly "
    "available information. AutoClip does not promise virality or specific growth outcomes.*"
)

# Static instruction block — no per-creator data, carries the cache breakpoint. (Issue 69)
_SYSTEM_INSTRUCTIONS = f"""\
{UNTRUSTED_CONTENT_POLICY}
You are a YouTube growth strategist with access to real-time web search.

The creator's analytics and DNA profile data are provided in the next block. Use them
as your primary source of truth about what works for this specific channel.

Then use the web_search tool to find current YouTube algorithm guidance, recent
platform changes, and trending content strategies relevant to this creator's niche.

Synthesise both into 3–5 specific, actionable improvements. For each:
- State the recommendation clearly
- Cite the creator's data (engagement rate, retention, hook performance) as evidence
- Note any current algorithm factor that makes this timely
- Estimate the likely impact (high / medium / low) — never promise virality

Keep total length under 600 words. Phrase predictions as likelihood estimates, not guarantees."""


# DNA brief is sliced to this many chars before being folded into the
# Anthropic prompt so the system block stays under the prompt-token cap
# (and would-be cacheable, if the prefix were long enough to engage — see
# brief.py docstring on the Sonnet 4.6 1024-token floor). (Issue 108)
_DNA_BRIEF_MAX_CHARS = 1000


def _build_request(
    channel_title: str,
    analytics: Mapping[str, object],
    dna_brief: str | None,
) -> tuple:
    """Assemble (system, tools, messages) for both .create and .stream paths.

    Extracted (Issue 92) so the streaming wrapper reuses the exact same shape —
    keeps cache breakpoints identical across both call paths, mirroring the
    Issue-86 split in ``dna/brief.py``.
    """
    payload: dict[str, object] = {"channel": channel_title, "analytics": analytics}
    if dna_brief:
        payload["dna_summary"] = dna_brief[:_DNA_BRIEF_MAX_CHARS]

    analytics_json = json.dumps(payload, indent=2, default=str)

    system: list[dict] = [
        # Stable prefix — carries the cache breakpoint.
        {
            "type": "text",
            "text": _SYSTEM_INSTRUCTIONS,
            # `system` is typed as `list[dict]` here (not the SDK's TextBlockParam),
            # so cache_control on a generic dict needs no type: ignore — the SDK
            # accepts the field at runtime regardless of stub-typed-dict membership.
            "cache_control": {"type": "ephemeral"},
        },
        # Volatile per-creator analytics — AFTER the breakpoint, never cached.
        {"type": "text", "text": f"CREATOR ANALYTICS DATA:\n{analytics_json}"},
    ]
    tools: list[dict] = [
        {"type": settings.ANTHROPIC_WEB_SEARCH_TOOL, "name": "web_search", "max_uses": 5}
    ]
    messages = [
        {
            "role": "user",
            "content": (
                f"Generate the improvement brief for '{channel_title}'. "
                "Search for the most current YouTube algorithm guidance relevant "
                "to this channel's niche before writing your recommendations."
            ),
        }
    ]
    return system, tools, messages


def generate_improvement_brief(
    channel_title: str,
    analytics: Mapping[str, object],
    dna_brief: str | None = None,
    task_id: str | None = None,
) -> tuple[str, dict]:
    """
    Call Claude with web_search to generate a data + research grounded improvement brief.

    Returns ``(brief_text, usage)`` — brief_text with disclaimer appended, usage is the
    token-count dict. Callers should pass usage to ``billing.ledger.record_llm_usage``.

    Args:
        task_id: Optional Celery task id (Issue 92). When set, switches to the
            streaming path — cache hit/miss + text deltas flow to
            ``task:{task_id}:events`` via ``worker.anthropic_stream.stream_message``
            with a ``pause_turn`` loop for multi-round web_search (Issue 350).
            When None (default), uses the ``.create()`` path with the same loop.
    """
    system, tools, messages = _build_request(channel_title, analytics, dna_brief)

    _MAX_SEARCH_ROUNDS = 5

    if task_id is not None:
        # Streaming path — uses stream_message (returns full Message with stop_reason)
        # so we can detect pause_turn and continue the agentic loop when web_search
        # fires server-side. stream_and_emit only returns text and cannot see stop_reason.
        from worker.anthropic_stream import stream_message

        client = _ANTHROPIC.with_options(timeout=120.0)
        usage: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read": 0,
            "cache_creation": 0,
        }
        loop_messages = messages
        final_msg = None
        try:
            for _round in range(_MAX_SEARCH_ROUNDS + 1):
                msg, round_usage = stream_message(
                    client,
                    task_id,
                    model=settings.ANTHROPIC_MODEL_IMPROVEMENT,
                    max_tokens=2000,
                    system=system,
                    messages=loop_messages,
                    tools=tools,
                )
                for k in usage:
                    usage[k] += round_usage.get(k, 0)
                if getattr(msg, "stop_reason", None) != "pause_turn":
                    final_msg = msg
                    break
                loop_messages = loop_messages + [{"role": "assistant", "content": msg.content}]
            else:
                logger.warning(
                    "improvement_brief streaming: hit max search rounds (%d)", _MAX_SEARCH_ROUNDS
                )
                final_msg = msg  # type: ignore[possibly-undefined]  # loop always runs ≥1 iter
        except (RateLimitError, APIStatusError, APIConnectionError) as exc:
            log_llm_error(logger, exc, task=task_id)
            raise
        if final_msg is None:
            raise RuntimeError("Claude returned no message in improvement brief streaming")
        text_blocks_s = [b for b in final_msg.content if getattr(b, "type", None) == "text"]
        if not text_blocks_s:
            raise RuntimeError("Claude returned no text in improvement brief streaming")
        final_text = text_blocks_s[-1].text
        logger.info(
            "improvement_brief streaming tokens: in=%d cached_read=%d cached_write=%d out=%d",
            usage["input_tokens"],
            usage["cache_read"],
            usage["cache_creation"],
            usage["output_tokens"],
        )
        record_llm_metric(settings.ANTHROPIC_MODEL_IMPROVEMENT, usage)
        return final_text + _DISCLAIMER, usage

    # Non-streaming (.create) path — pause_turn loop so server-side web_search
    # results are folded back and the model synthesises a final answer. (Issue 350)
    from verbose import vlog_llm_request, vlog_llm_response

    vlog_llm_request(
        "improvement_brief",
        model=settings.ANTHROPIC_MODEL_IMPROVEMENT,
        max_tokens=2000,
        system=system,
        messages=messages,
        tools=tools,
    )
    _client = _ANTHROPIC.with_options(timeout=120.0)
    loop_messages = messages
    response = None
    try:
        for _round in range(_MAX_SEARCH_ROUNDS + 1):
            response = _client.messages.create(
                model=settings.ANTHROPIC_MODEL_IMPROVEMENT,
                max_tokens=2000,
                system=system,
                tools=tools,
                messages=loop_messages,
            )
            if getattr(response, "stop_reason", None) != "pause_turn":
                break
            loop_messages = loop_messages + [{"role": "assistant", "content": response.content}]
        else:
            logger.warning("improvement_brief: hit max search rounds (%d)", _MAX_SEARCH_ROUNDS)
    except (RateLimitError, APIStatusError, APIConnectionError) as exc:
        log_llm_error(logger, exc)
        raise
    if response is None:
        raise RuntimeError("Claude returned no response in improvement brief generation")
    vlog_llm_response("improvement_brief", response=response)

    _tokens_in = response.usage.input_tokens
    _tokens_out = response.usage.output_tokens
    logger.info(
        "improvement_brief tokens: in=%d cached_read=%d cached_write=%d out=%d",
        _tokens_in,
        getattr(response.usage, "cache_read_input_tokens", 0),
        getattr(response.usage, "cache_creation_input_tokens", 0),
        _tokens_out,
    )

    text_blocks = [b for b in response.content if b.type == "text"]
    if not text_blocks:
        raise RuntimeError("Claude returned no text in improvement brief generation")

    _usage = {
        "input_tokens": _tokens_in,
        "output_tokens": _tokens_out,
        "cache_read": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        "cache_creation": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
    }
    record_llm_metric(settings.ANTHROPIC_MODEL_IMPROVEMENT, _usage)
    warn_if_truncated(settings.ANTHROPIC_MODEL_IMPROVEMENT, getattr(response, "stop_reason", None))
    # web_search interleaves blocks (preamble text → tool_use → final answer); the
    # FINAL text block is the synthesised brief, not the "let me search…" preamble. (Issue 69)
    return text_blocks[-1].text + _DISCLAIMER, _usage
