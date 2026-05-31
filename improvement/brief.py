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

import httpx
from anthropic import Anthropic

from config import settings

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
_SYSTEM_INSTRUCTIONS = """\
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


def _build_request(channel_title: str, analytics: dict, dna_brief: str | None) -> tuple:
    """Assemble (system, tools, messages) for both .create and .stream paths.

    Extracted (Issue 92) so the streaming wrapper reuses the exact same shape —
    keeps cache breakpoints identical across both call paths, mirroring the
    Issue-86 split in ``dna/brief.py``.
    """
    payload: dict[str, object] = {"channel": channel_title, "analytics": analytics}
    if dna_brief:
        payload["dna_summary"] = dna_brief[:1000]  # cap so system block stays cacheable

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
    analytics: dict,
    dna_brief: str | None = None,
    task_id: str | None = None,
) -> str:
    """
    Call Claude with web_search to generate a data + research grounded improvement brief.
    Returns brief_text with disclaimer appended.

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
        final_text, usage = stream_and_emit(
            client,
            task_id,
            model=settings.ANTHROPIC_MODEL,
            max_tokens=2000,
            system=system,
            messages=messages,
            tools=tools,
        )
        logger.info(
            "improvement_brief streaming tokens: in=%d cached_read=%d cached_write=%d out=%d",
            usage["input_tokens"],
            usage["cache_read"],
            usage["cache_creation"],
            usage["output_tokens"],
        )
        # web_search interleaves text + tool_use blocks under streaming too;
        # stream_and_emit returns the LAST text block (the synthesised
        # answer), matching the Issue 69 pattern the .create() path uses.
        return final_text + _DISCLAIMER

    # web_search tool can take 60-120s; override the default 60s timeout per-call.
    response = _ANTHROPIC.with_options(timeout=120.0).messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=2000,
        system=system,
        tools=tools,
        messages=messages,
    )

    logger.info(
        "improvement_brief tokens: in=%d cached_read=%d cached_write=%d out=%d",
        response.usage.input_tokens,
        getattr(response.usage, "cache_read_input_tokens", 0),
        getattr(response.usage, "cache_creation_input_tokens", 0),
        response.usage.output_tokens,
    )

    text_blocks = [b for b in response.content if b.type == "text"]
    if not text_blocks:
        raise RuntimeError("Claude returned no text in improvement brief generation")

    # web_search interleaves blocks (preamble text → tool_use → final answer); the
    # FINAL text block is the synthesised brief, not the "let me search…" preamble. (Issue 69)
    return text_blocks[-1].text + _DISCLAIMER
