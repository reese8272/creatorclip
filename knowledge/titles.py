"""Generate ranked title suggestions using Claude with web_search (Issue 128).

Prompt structure (three system blocks):
  Block 1 — static instructions: role, task, CTR principles, neutral-band definition.
  Block 2 — DNA brief: per-creator, stable across calls. No cache_control — the
            block 1 + block 2 prefix (~1,550 tokens) is below Sonnet 4.6's
            2048-token cacheable-prefix floor, so a marker would be inert (SEV1 #6).
  Block 3 — per-video context: video title + transcript summary.

Claude generates 10 ranked candidates; the caller surfaces the top 5.
Results stream via stream_and_emit and are returned as a JSON string for the
caller to parse and emit as an SSE result event.

The honesty disclaimer is always appended by Python — never left to the LLM.
"""

import json
import logging

import httpx
from anthropic import Anthropic

from config import settings
from knowledge.util import extract_transcript_text as _extract_transcript_text
from knowledge.util import wrap_untrusted

logger = logging.getLogger(__name__)

_ANTHROPIC = Anthropic(
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
_SYSTEM_INSTRUCTIONS = """\
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
{
  "candidates": [
    {
      "title": "<string, max 100 chars>",
      "rationale": "<one sentence, must use hedged language: likely / estimated / suggests>",
      "ctr_signal": "up" | "neutral" | "down",
      "search_grounded": true | false
    }
  ]
}

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

    No cache_control breakpoint: the static instructions + DNA brief prefix
    (~1,550 tokens) is below Sonnet 4.6's 2048-token cacheable-prefix floor, so
    a marker would be inert (SEV1 #6). Per-video context is block 3.
    """
    dna_text = (dna_brief or "No DNA profile available yet.")[:_DNA_BRIEF_MAX_CHARS]

    # Issue 224: stated_identity is attacker-influenceable creator free-text and
    # must not go in the system role. Build video context without it; it moves to
    # the user turn, JSON-wrapped via wrap_untrusted.
    video_context_parts: list[str] = []
    if video_title:
        video_context_parts.append(f"Video title: {video_title}")
    if transcript_summary:
        video_context_parts.append(f"Transcript excerpt:\n{transcript_summary}")
    video_context_parts.append(f"Channel: {channel_title}")
    video_context = "\n\n".join(video_context_parts)

    system: list[dict] = [
        # Block 1: static instructions.
        {"type": "text", "text": _SYSTEM_INSTRUCTIONS},
        # Block 2: DNA brief — stable per-creator. NO cache_control: the static
        # instructions + DNA brief together run ~1,550 tokens, BELOW Sonnet 4.6's
        # 2048-token cacheable-prefix floor, so a marker here is inert — it only
        # pays the 1.25x write premium for zero reads. (SEV1 #6; same precedent
        # as knowledge/hooks.py / improvement/brief.py — see docs/DECISIONS.md.)
        {
            "type": "text",
            "text": f"CREATOR DNA PROFILE:\n{dna_text}",
        },
        # Block 3: per-video factual context — no creator free-text.
        {"type": "text", "text": f"VIDEO TO TITLE:\n{video_context}"},
    ]
    tools: list[dict] = [{"type": settings.ANTHROPIC_WEB_SEARCH_TOOL, "name": "web_search"}]

    # stated_identity travels in the user turn so the model receives it from the
    # user role, not as trusted operator instructions. JSON-wrapped to prevent
    # quote/bracket break-out (OWASP LLM01; Anthropic prompt-injection guide).
    user_preamble = ""
    if stated_identity:
        user_preamble = wrap_untrusted("creator_stated_identity", stated_identity)
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
    data = json.loads(raw_json)
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


def generate_title_suggestions(
    channel_title: str,
    dna_brief: str | None,
    stated_identity: str | None,
    video_title: str | None,
    transcript_summary: str,
    task_id: str,
) -> str:
    """Call Claude with web_search; stream tokens to the SSE consumer.

    Always uses the streaming path (task_id is mandatory for this feature —
    the caller emits the result event from the returned JSON string).

    Returns the raw JSON string from Claude's final text block. Raises on
    network / API errors so the Celery task can retry.
    """
    system, tools, messages = _build_request(
        channel_title, dna_brief, stated_identity, video_title, transcript_summary
    )

    from worker.anthropic_stream import stream_and_emit

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
        "title_suggestions tokens: in=%d cached_read=%d cached_write=%d out=%d",
        usage["input_tokens"],
        usage["cache_read"],
        usage["cache_creation"],
        usage["output_tokens"],
    )
    return final_text
