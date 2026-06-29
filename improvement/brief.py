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
from observability import record_llm_metric, warn_if_truncated

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
    tools: list[dict] = [{"type": settings.ANTHROPIC_WEB_SEARCH_TOOL, "name": "web_search"}]
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
            ``task:{task_id}:events`` via ``worker.anthropic_stream.stream_and_emit``.
            Mirrors ``dna/brief.py::generate_brief``'s Issue-86 pattern. When None
            (default), uses the legacy ``.create()`` path so existing tests and
            non-progress-aware callers keep working unchanged.
    """
    system, tools, messages = _build_request(channel_title, analytics, dna_brief)

    if task_id is not None:
        # Streaming path (Issue 92) — forwards message_start.usage + text_delta
        # events to the SSE consumer. Same prompt structure AND same tools as
        # the .create() path so the LLM sees an identical contract on both
        # paths (cache breakpoints + web_search both intact). The tools=tools
        # kwarg matters: without it, the brief loses web_search grounding —
        # Wave-3 Fix A closed that SEV1.
        from worker.anthropic_stream import stream_and_emit

        # The 120s timeout matters more here than on the streaming path
        # (streaming returns first byte fast), but pass it through for parity.
        client = _ANTHROPIC.with_options(timeout=120.0)
        try:
            final_text, usage = stream_and_emit(
                client,
                task_id,
                model=settings.ANTHROPIC_MODEL_IMPROVEMENT,
                max_tokens=2000,
                system=system,
                messages=messages,
                tools=tools,
            )
        except (RateLimitError, APIStatusError, APIConnectionError) as exc:
            logger.error(
                "improvement_brief LLM error task=%s exc_type=%s", task_id, type(exc).__name__
            )
            raise
        logger.info(
            "improvement_brief streaming tokens: in=%d cached_read=%d cached_write=%d out=%d",
            usage["input_tokens"],
            usage["cache_read"],
            usage["cache_creation"],
            usage["output_tokens"],
        )
        record_llm_metric(settings.ANTHROPIC_MODEL_IMPROVEMENT, usage)
        # web_search interleaves text + tool_use blocks under streaming too;
        # stream_and_emit returns the LAST text block (the synthesised
        # answer), matching the Issue 69 pattern the .create() path uses.
        return final_text + _DISCLAIMER, usage

    # web_search tool can take 60-120s; override the default 60s timeout per-call.
    from verbose import vlog_llm_request, vlog_llm_response

    vlog_llm_request(
        "improvement_brief",
        model=settings.ANTHROPIC_MODEL_IMPROVEMENT,
        max_tokens=2000,
        system=system,
        messages=messages,
        tools=tools,
    )
    try:
        response = _ANTHROPIC.with_options(timeout=120.0).messages.create(
            model=settings.ANTHROPIC_MODEL_IMPROVEMENT,
            max_tokens=2000,
            system=system,
            tools=tools,
            messages=messages,
        )
    except (RateLimitError, APIStatusError, APIConnectionError) as exc:
        logger.error("improvement_brief LLM error exc_type=%s", type(exc).__name__)
        raise
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
