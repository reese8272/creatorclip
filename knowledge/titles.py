"""Generate ranked title suggestions using Claude with web_search (Issue 128).

Prompt structure (three system blocks):
  Block 1 — static instructions: role, task, CTR principles, neutral-band definition.
  Block 2 — DNA brief. The cache_control marker (ttl=1h) is attached only when the
            measured block 1 + block 2 prefix clears Sonnet 4.6's 1024-token
            cacheable-prefix floor — which requires a populated DNA brief; with an
            empty brief the prefix is ~720 tokens and a marker would be inert
            (Issues 218 / 315 / 352).
  Block 3 — per-video factual context: channel name only. Uncached.

Untrusted creator content (video title, transcript summary, stated identity) travels
in the USER turn, JSON-wrapped via wrap_untrusted — never in the system role
(OWASP LLM01; Issues 224 / 352).

Claude generates 10 ranked candidates; the caller surfaces the top 5.
Results stream via stream_message (with a pause_turn continuation loop for
multi-round web_search — Issue 350) and are returned as a JSON string for the
caller to parse and emit as an SSE result event.

The honesty disclaimer is always appended by Python — never left to the LLM.
"""

import json
import logging

import httpx
from anthropic import APIConnectionError, APIStatusError, AsyncAnthropic, RateLimitError

from config import settings
from knowledge.util import (
    UNTRUSTED_CONTENT_POLICY,
    dna_system_block,
    extract_json_block,
    wrap_untrusted,
)
from knowledge.util import extract_transcript_text as _extract_transcript_text
from observability import log_llm_error, record_llm_metric

logger = logging.getLogger(__name__)

# Module-level AsyncAnthropic singleton (Issue 82a) — prefork-safe lazy pool bind.
_ANTHROPIC = AsyncAnthropic(
    api_key=settings.ANTHROPIC_API_KEY,
    timeout=httpx.Timeout(120.0, connect=10.0),
    max_retries=2,
)

TITLE_MAX_CHARS = 100  # YouTube hard limit
_GENERATE_N = 10
SURFACE_N = 5

_DISCLAIMER = (
    "These title suggestions are estimates grounded in your channel data and "
    "current search trends. AutoClip cannot guarantee specific CTR or view outcomes."
)

# Static block — never contains per-creator data. The "neutral" CTR band is
# defined here to avoid label drift.
_SYSTEM_INSTRUCTIONS = f"""\
{UNTRUSTED_CONTENT_POLICY}
You are a YouTube title strategist with access to real-time web search.

Your task: generate exactly 10 title candidates for a YouTube video, ranked from highest
to lowest predicted CTR fit for this specific creator's channel. You have access to the
creator's DNA profile (their channel voice, audience, and top-performing content patterns).

IMPORTANT — CTR signal definitions for this task:
  "up"      = structural patterns likely to outperform this channel's average CTR by more than 0.5%
  "neutral" = within ±0.5% of the channel's average CTR; solid but not likely to stand out
  "down"    = patterns likely to underperform the channel average CTR

CTR principles to apply:
  - Front-load the hook and primary keyword in the first 40 characters
  - Specificity outperforms vagueness (numbers, named outcomes, named results)
  - Curiosity gaps drive clicks when the payoff is implied but not revealed
  - Match the creator's established tone — sudden style shifts reduce CTR for their existing audience
  - Use the web_search results to incorporate currently trending keywords in this niche

Use the web_search tool to research (3 searches maximum):
  1. Currently trending titles and topics in this creator's niche (last 30 days)
  2. High-CTR title patterns for this content format
  3. Active YouTube algorithm signals affecting title performance in this category

After searching, generate 10 title candidates. Return ONLY a valid JSON object with this schema:
{{
  "candidates": [
    {{
      "title": "<string, max 100 chars>",
      "rationale": "<one sentence, must use hedged language: likely / estimated / suggests>",
      "ctr_signal": "up" | "neutral" | "down",
      "search_grounded": true | false
    }}
  ]
}}

RULES:
  - Every title must be at most 100 characters
  - Rank candidates 1-10 from highest to lowest predicted CTR fit for THIS channel
  - rationale must never contain "guaranteed", "will", "promise", or virality language
  - search_grounded=true only when the title directly uses a finding from your web search
  - Return valid JSON ONLY — no preamble, no explanation outside the JSON object"""

_DNA_BRIEF_MAX_CHARS = 3000


def _extract_transcript_summary(segments_jsonb: dict | None, max_chars: int = 1500) -> str:
    """Extract a plain-text summary from the transcript segments_jsonb blob."""
    return _extract_transcript_text(segments_jsonb, max_chars)


def _build_request(
    channel_title: str,
    dna_brief: str | None,
    stated_identity: str | None,
    video_title: str | None,
    transcript_summary: str,
) -> tuple:
    """Assemble (system, tools, messages) for both .create and streaming paths.

    Block 2 carries the cache breakpoint (ttl=1h) only when the measured static +
    DNA prefix clears Sonnet 4.6's 1024-token cacheable-prefix floor — with a
    populated (near-cap) DNA brief the prefix is ~1,450 tokens; with no brief it
    is ~720 tokens and the marker is omitted (Issues 218 / 315 / 352). Within a
    1h window a creator's title/thumbnail calls share the cached prefix.
    Per-video trusted context is in the uncached block 3; untrusted content
    (video title, transcript, stated identity) travels in the user turn.
    """
    dna_text = (dna_brief or "No DNA profile available yet.")[:_DNA_BRIEF_MAX_CHARS]

    system: list[dict] = [
        # Block 1: static instructions.
        {"type": "text", "text": _SYSTEM_INSTRUCTIONS},
        # Block 2: DNA brief — 1h cache marker gated on the measured prefix floor.
        dna_system_block(_SYSTEM_INSTRUCTIONS, dna_text),
        # Block 3: per-video factual context — no creator free-text.
        {"type": "text", "text": f"CHANNEL: {channel_title}"},
    ]
    # allowed_callers=["direct"]: this call must return a parseable JSON text block.
    # web_search_20260209's default dynamic filtering (programmatic tool calling) routes
    # output into tool/code blocks, leaving the streamed text empty — a live failure the
    # mocked tests missed. Direct calling puts search results in context and keeps the
    # model's JSON answer in the normal text stream.
    tools: list[dict] = [
        {
            "type": settings.ANTHROPIC_WEB_SEARCH_TOOL,
            "name": "web_search",
            "allowed_callers": ["direct"],
        }
    ]

    # Untrusted creator content (stated_identity, video_title, transcript_summary)
    # travels in the user turn so the model receives it from the user role, not as
    # trusted operator instructions. JSON-wrapped to prevent quote/bracket
    # break-out (OWASP LLM01; Anthropic prompt-injection guide; Issues 224 / 352).
    user_preamble = ""
    if stated_identity:
        user_preamble += wrap_untrusted("creator_stated_identity", stated_identity)
    if video_title:
        user_preamble += wrap_untrusted("video_title", video_title)
    if transcript_summary:
        user_preamble += wrap_untrusted("video_transcript", transcript_summary)
    messages = [
        {
            "role": "user",
            "content": (
                user_preamble
                + f"Generate 10 title candidates for this video from '{channel_title}'. "
                "Search for trending titles in this niche first, then produce the ranked JSON."
            ),
        }
    ]
    return system, tools, messages


def parse_candidates(raw_json: str) -> list[dict]:
    """Parse and validate Claude's JSON response.

    Returns top SURFACE_N candidates. Raises ValueError on malformed JSON or
    missing required fields so the caller can surface an error event.
    """
    try:
        data = json.loads(extract_json_block(raw_json))
    except json.JSONDecodeError as exc:
        logger.warning(
            "titles.parse_candidates: malformed JSON from LLM (truncated response?): %s", exc
        )
        raise ValueError(f"Malformed JSON from LLM (truncated response?): {exc}") from exc
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("Claude response missing 'candidates' list")

    validated: list[dict] = []
    for c in candidates:
        title = str(c.get("title", "")).strip()[:TITLE_MAX_CHARS]
        rationale = str(c.get("rationale", "")).strip()
        ctr_signal = c.get("ctr_signal", "neutral")
        if ctr_signal not in ("up", "neutral", "down"):
            ctr_signal = "neutral"
        search_grounded = bool(c.get("search_grounded", False))
        if not title:
            continue
        validated.append(
            {
                "title": title,
                "rationale": rationale,
                "ctr_signal": ctr_signal,
                "search_grounded": search_grounded,
            }
        )

    return validated[:SURFACE_N]


async def generate_title_suggestions(
    channel_title: str,
    dna_brief: str | None,
    stated_identity: str | None,
    video_title: str | None,
    transcript_summary: str,
    task_id: str,
) -> tuple[str, dict]:
    """Call Claude with web_search; stream tokens to the SSE consumer.

    Always uses the streaming path (task_id is mandatory for this feature —
    the caller emits the result event from the returned JSON string).

    Returns ``(raw_json, usage)`` where usage sums the token counts across every
    ``stream_message`` round in the pause_turn loop. Callers should pass usage to
    ``billing.ledger.increment_usage`` to populate the aggregate cost ledger.
    Raises on network / API errors.
    """
    system, tools, messages = _build_request(
        channel_title, dna_brief, stated_identity, video_title, transcript_summary
    )

    # pause_turn loop: a long server-side web_search turn can pause; resume by
    # re-sending the assistant content with the SAME tools, bounded — mirrors
    # the shipped pattern in thumbnails.py / improvement/brief.py (Issue 350).
    from worker.anthropic_stream import stream_message

    _MAX_SEARCH_ROUNDS = 5

    client = _ANTHROPIC.with_options(timeout=120.0)
    usage: dict[str, int] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read": 0,
        "cache_creation": 0,
    }
    loop_messages = messages
    final_msg = None
    msg = None
    try:
        for _round in range(_MAX_SEARCH_ROUNDS + 1):
            msg, round_usage = await stream_message(
                client,
                task_id,
                model=settings.ANTHROPIC_MODEL_TITLES,
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
                "generate_title_suggestions: hit max web_search rounds (%d)", _MAX_SEARCH_ROUNDS
            )
            final_msg = msg
    except (RateLimitError, APIStatusError, APIConnectionError) as exc:
        log_llm_error(logger, exc, task=task_id)
        raise
    if final_msg is None:
        raise RuntimeError("Claude returned no message in title suggestion generation")
    text_blocks = [b for b in final_msg.content if getattr(b, "type", None) == "text"]
    if not text_blocks:
        raise RuntimeError("Claude returned no text in title suggestion generation")
    final_text = text_blocks[-1].text
    logger.info(
        "title_suggestions tokens: in=%d cached_read=%d cached_write=%d out=%d",
        usage["input_tokens"],
        usage["cache_read"],
        usage["cache_creation"],
        usage["output_tokens"],
    )
    record_llm_metric(settings.ANTHROPIC_MODEL_TITLES, usage)
    return final_text, usage
