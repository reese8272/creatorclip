"""Thumbnail pattern analysis and concept generation (Issue 129).

Two Claude calls:
  1. analyze_thumbnail_patterns — multimodal, analyzes up to 10 YouTube
     thumbnails from top-performing videos via URL source (no download needed).
     Returns a ChannelThumbnailPatterns dict.
  2. generate_thumbnail_concepts — grounded generation using channel patterns,
     transcript hook, DNA brief, and web_search. Streams tokens via SSE.
     Returns the raw JSON string for the caller to parse.

Design decisions (logged in docs/DECISIONS.md 2026-06-07):
  - CTR data (video_thumbnail_impressions_ctr) requires YouTube Reporting API
    bulk export (24-48h delay). Uses DNA top_video_ids_jsonb as high-performer
    proxy — avoids new API scope + infrastructure.
  - Visual pattern extraction uses Claude multimodal instead of a CV pipeline.
    Eliminates OpenCV/MediaPipe dependency (deferred to Phase 2 in docs/SOT.md).
"""

import json
import logging

import httpx
from anthropic import Anthropic, APIConnectionError, APIStatusError, RateLimitError

from config import settings
from knowledge.util import UNTRUSTED_CONTENT_POLICY, extract_json_block, wrap_untrusted
from knowledge.util import extract_transcript_text as _extract_transcript_text

logger = logging.getLogger(__name__)

_ANTHROPIC = Anthropic(
    api_key=settings.ANTHROPIC_API_KEY,
    timeout=httpx.Timeout(120.0, connect=10.0),
    max_retries=2,
)

CONCEPT_SURFACE_N = 5
PATTERNS_CACHE_KEY_PREFIX = "thumbnail_patterns:"
PATTERNS_CACHE_TTL = 86400  # 24 hours

_THUMBNAIL_URL_TEMPLATE = "https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
_DNA_BRIEF_MAX_CHARS = 3000

_DISCLAIMER = (
    "These thumbnail concepts are estimates grounded in your channel's visual patterns "
    "and current niche trends. AutoClip cannot guarantee specific CTR outcomes."
)

_SYSTEM_INSTRUCTIONS = f"""\
{UNTRUSTED_CONTENT_POLICY}
You are a YouTube thumbnail strategist with access to real-time web search and the
creator's visual channel patterns.

Your task: generate exactly 5 thumbnail concepts for a YouTube video, each grounded
in the channel's own top-performing thumbnail patterns and current niche trends.

IMPORTANT — honesty constraints:
  - Every predicted_ctr_rationale MUST use hedged language: "likely", "estimated",
    "suggests", "based on channel patterns". NEVER say "will", "guaranteed", or "promise".
  - Concepts describe visual direction only — no image rendering is produced.

Use the web_search tool to research (2 searches maximum):
  1. Current thumbnail trends and high-CTR visual patterns in this creator's niche
  2. Color and composition styles performing well on YouTube in this category right now

After searching, generate 5 thumbnail concepts. Return ONLY a valid JSON object:
{{
  "concepts": [
    {{
      "composition": "<describe subject placement, background, framing in one sentence>",
      "text_overlay": "<short overlay text max 4 words, or null if no text recommended>",
      "dominant_emotion": "<one emotion the thumbnail should convey>",
      "color_direction": "<2-3 dominant colors, described or as hex values>",
      "predicted_ctr_rationale": "<one sentence, hedged, citing channel pattern or trend>",
      "based_on_pattern": "<which of this channel's successful patterns this draws from>"
    }}
  ]
}}

RULES:
  - Rank concepts 1-5 from highest to lowest predicted fit for THIS channel
  - Each concept must be concretely actionable — a designer can execute it directly
  - based_on_pattern must reference the channel's actual observed patterns, not generic advice
  - Return valid JSON ONLY — no preamble, no explanation outside the JSON object"""


def _thumbnail_url(youtube_video_id: str) -> str:
    return _THUMBNAIL_URL_TEMPLATE.format(video_id=youtube_video_id)


def _empty_patterns() -> dict:
    return {
        "face_present": "unknown",
        "dominant_emotions": [],
        "text_overlay_style": "unknown",
        "typical_colors": "unknown",
        "composition_pattern": "unknown",
        "channel_thumbnail_signature": "Insufficient data to identify patterns.",
    }


def analyze_thumbnail_patterns(
    youtube_video_ids: list[str],
    channel_title: str,
) -> dict:
    """Analyze top-performing thumbnails using Claude multimodal vision.

    Passes thumbnail image URLs directly to Claude — no download required,
    no CV pipeline needed. Intended to be called via asyncio.to_thread.

    Returns a dict with keys: face_present, dominant_emotions,
    text_overlay_style, typical_colors, composition_pattern,
    channel_thumbnail_signature.
    """
    if not youtube_video_ids:
        return _empty_patterns()

    urls = [_thumbnail_url(vid) for vid in youtube_video_ids[:10]]

    content: list[dict] = []
    for url in urls:
        content.append({"type": "image", "source": {"type": "url", "url": url}})
    content.append(
        {
            "type": "text",
            "text": (
                f"These are the top-performing thumbnails from the YouTube channel "
                f"'{channel_title}'.\n\n"
                "Analyze the visual patterns across all of them and return a JSON object "
                "with this exact schema:\n"
                "{\n"
                '  "face_present": "always" | "often" | "rarely" | "never",\n'
                '  "dominant_emotions": ["<emotion>"],\n'
                '  "text_overlay_style": "bold_caps" | "minimal" | "question" | "none" | "mixed",\n'
                '  "typical_colors": "<describe the color palette in one sentence>",\n'
                '  "composition_pattern": "<describe the layout/framing in one sentence>",\n'
                '  "channel_thumbnail_signature": "<one sentence describing the overall visual style>"\n'
                "}\n"
                "Return ONLY valid JSON — no preamble or explanation outside the JSON object."
            ),
        }
    )

    try:
        response = _ANTHROPIC.messages.create(
            model=settings.ANTHROPIC_MODEL_THUMBNAILS,
            max_tokens=512,
            messages=[{"role": "user", "content": content}],  # type: ignore[typeddict-item]  # SDK stub doesn't model a locally-built list[dict] of content blocks as valid content
        )
    except (RateLimitError, APIStatusError, APIConnectionError) as exc:
        logger.error("thumbnail_patterns LLM error exc_type=%s", type(exc).__name__)
        raise

    logger.info(
        "thumbnail_patterns tokens: in=%d out=%d",
        response.usage.input_tokens,
        response.usage.output_tokens,
    )

    raw = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.warning("thumbnail_patterns: could not parse Claude response; returning empty")
        return _empty_patterns()


def _extract_transcript_hook(segments_jsonb: dict | None, max_chars: int = 500) -> str:
    """Extract the opening transcript text (the hook) from segments_jsonb."""
    return _extract_transcript_text(segments_jsonb, max_chars)


def _build_concepts_request(
    channel_title: str,
    dna_brief: str | None,
    patterns: dict,
    transcript_hook: str,
    stated_identity: str | None,
) -> tuple:
    """Assemble (system, tools, messages) for concept generation.

    Block 2 carries the cache breakpoint (ttl=1h): static instructions + DNA brief
    prefix totals ~1,550 tokens, which clears Sonnet 4.6's 1024-token cacheable-
    prefix floor (confirmed 2026-06-23). Cross-call sharing: if a titles call already
    wrote the cache, a subsequent thumbnail call within the 1h window will read it
    for free (same block 1 + block 2 byte-identical prefix). (Issue 218)
    Per-video patterns and context are in the uncached block 3.
    """
    dna_text = (dna_brief or "No DNA profile available yet.")[:_DNA_BRIEF_MAX_CHARS]

    pattern_lines = [
        f"Face in thumbnail: {patterns.get('face_present', 'unknown')}",
        f"Dominant emotions: {', '.join(patterns.get('dominant_emotions', [])) or 'unknown'}",
        f"Text overlay style: {patterns.get('text_overlay_style', 'unknown')}",
        f"Typical colors: {patterns.get('typical_colors', 'unknown')}",
        f"Composition: {patterns.get('composition_pattern', 'unknown')}",
        f"Channel thumbnail signature: {patterns.get('channel_thumbnail_signature', 'unknown')}",
    ]
    pattern_text = "\n".join(pattern_lines)

    # Issue 224: stated_identity is attacker-influenceable creator free-text and
    # must not go in the system role. Build video context without it; it moves to
    # the user turn, JSON-wrapped via wrap_untrusted.
    video_context_parts: list[str] = []
    if transcript_hook:
        video_context_parts.append(f"Video opening (hook):\n{transcript_hook}")
    video_context_parts.append(f"Channel: {channel_title}")
    video_context = "\n\n".join(video_context_parts)

    system: list[dict] = [
        # Block 1: static instructions.
        {"type": "text", "text": _SYSTEM_INSTRUCTIONS},
        # Block 2: DNA brief — stable per-creator. cache_control with 1h TTL so
        # all brief calls (titles → hooks → thumbnails) within a creator session
        # share the cached prefix. ~1,550-token prefix clears Sonnet 4.6's 1024-
        # token cacheable-prefix floor (confirmed 2026-06-23). (Issue 218)
        {
            "type": "text",
            "text": f"CREATOR DNA PROFILE:\n{dna_text}",
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
        # Block 3: per-video factual context — no creator free-text.
        {
            "type": "text",
            "text": f"CHANNEL THUMBNAIL PATTERNS:\n{pattern_text}\n\nVIDEO CONTEXT:\n{video_context}",
        },
    ]
    # allowed_callers=["direct"]: this call must return a parseable JSON text block.
    # web_search_20260209's default dynamic filtering (programmatic tool calling) routes
    # output into tool/code blocks, leaving the streamed text empty — a live failure the
    # mocked tests missed. Direct calling keeps the model's JSON answer in the text stream.
    tools: list[dict] = [
        {
            "type": settings.ANTHROPIC_WEB_SEARCH_TOOL,
            "name": "web_search",
            "allowed_callers": ["direct"],
        }
    ]

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
                + f"Generate 5 thumbnail concepts for this video from '{channel_title}'. "
                "Search for current thumbnail trends in this niche, then produce the ranked JSON."
            ),
        }
    ]
    return system, tools, messages


def parse_concepts(raw_json: str) -> list[dict]:
    """Parse and validate Claude's JSON response.

    Returns up to CONCEPT_SURFACE_N concepts. Raises ValueError on malformed
    JSON or missing required fields so the caller can surface an error event.
    """
    data = json.loads(extract_json_block(raw_json))
    concepts = data.get("concepts")
    if not isinstance(concepts, list) or not concepts:
        raise ValueError("Claude response missing 'concepts' list")

    validated: list[dict] = []
    for c in concepts:
        composition = str(c.get("composition", "")).strip()
        if not composition:
            continue
        validated.append(
            {
                "composition": composition,
                "text_overlay": c.get("text_overlay"),
                "dominant_emotion": str(c.get("dominant_emotion", "")).strip(),
                "color_direction": str(c.get("color_direction", "")).strip(),
                "predicted_ctr_rationale": str(c.get("predicted_ctr_rationale", "")).strip(),
                "based_on_pattern": str(c.get("based_on_pattern", "")).strip(),
            }
        )

    return validated[:CONCEPT_SURFACE_N]


def generate_thumbnail_concepts(
    channel_title: str,
    dna_brief: str | None,
    patterns: dict,
    transcript_hook: str,
    stated_identity: str | None,
    task_id: str,
) -> tuple[str, dict]:
    """Call Claude with web_search; stream tokens to the SSE consumer.

    Returns ``(raw_json, usage)`` where usage is the token-count dict from
    ``stream_and_emit``. Callers should pass usage to ``billing.ledger.increment_usage``.
    Raises on network / API errors so the Celery task can retry.
    Intended to be called via asyncio.to_thread.
    """
    system, tools, messages = _build_concepts_request(
        channel_title, dna_brief, patterns, transcript_hook, stated_identity
    )

    from worker.anthropic_stream import stream_and_emit

    client = _ANTHROPIC.with_options(timeout=120.0)
    try:
        final_text, usage = stream_and_emit(
            client,
            task_id,
            model=settings.ANTHROPIC_MODEL_THUMBNAILS,
            max_tokens=2000,
            system=system,
            messages=messages,
            tools=tools,
        )
    except (RateLimitError, APIStatusError, APIConnectionError) as exc:
        logger.error(
            "thumbnail_concepts LLM error task=%s exc_type=%s", task_id, type(exc).__name__
        )
        raise
    logger.info(
        "thumbnail_concepts tokens: in=%d cached_read=%d cached_write=%d out=%d",
        usage["input_tokens"],
        usage["cache_read"],
        usage["cache_creation"],
        usage["output_tokens"],
    )
    return final_text, usage
