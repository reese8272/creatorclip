"""Auto chapter marker generation from silence gaps + transcript (Issue 131).

Prompt structure (one cached system block):
  Block 1 — static instructions (cache_control breakpoint): title rules, JSON schema.

No DNA required. Claude titles transcript segments delimited by silence boundaries.
The first chapter is always 0:00. Minimum 4 chapters; maximum 1 per 3 minutes.

Returns JSON string with ChapterList schema; caller parses via parse_chapters().
"""

import json
import logging

import httpx
from anthropic import Anthropic, APIConnectionError, APIStatusError, RateLimitError

from config import settings
from observability import record_llm_metric

logger = logging.getLogger(__name__)

_ANTHROPIC = Anthropic(
    api_key=settings.ANTHROPIC_API_KEY,
    timeout=httpx.Timeout(60.0, connect=10.0),
    max_retries=2,
)

MIN_CHAPTERS = 4
MAX_CHAPTER_PERIOD_S = 180.0  # 1 chapter per 3 minutes max density
SILENCE_THRESHOLD_S = 2.0
_SEGMENT_MAX_CHARS = 300  # text per chapter segment sent to Claude

_SYSTEM_INSTRUCTIONS = """\
You are a YouTube chapter generator. Given video transcript segments and their
timestamps, generate concise chapter titles for each segment.

Rules:
  - Each title must be at most 40 characters
  - Titles must be YouTube-compliant (plain text, no special chars that break the format)
  - The first chapter (0:00) should be a brief intro label like "Intro" or a concise topic label
  - Titles should describe the segment content, not be generic placeholders
  - Use concise noun phrases or short verb phrases — not full sentences
  - All timestamps are provided to you; do not change them

Return ONLY a valid JSON object with this exact schema:
{
  "chapters": [
    {
      "timestamp_s": <float>,
      "timestamp_formatted": "<YouTube format, e.g. 0:00 or 4:23 or 1:02:45>",
      "title": "<string, max 40 chars>"
    }
  ]
}

Return valid JSON ONLY — no preamble, no markdown code fences, no extra text."""


def format_timestamp(seconds: float) -> str:
    """Convert seconds to YouTube chapter format: 0:00, 4:23, 1:02:45."""
    total = int(max(0, seconds))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def find_chapter_boundaries(
    timeline_jsonb: dict | None,
    video_duration_s: float,
) -> list[float]:
    """Extract chapter boundary timestamps from silence gaps in the signal timeline.

    Always includes 0.0 as first boundary. Silences >= SILENCE_THRESHOLD_S are candidates.
    Enforces MAX_CHAPTER_PERIOD_S minimum gap between chapters.
    Fills up to MIN_CHAPTERS if the video has few silences.
    """
    raw: set[float] = {0.0}

    if timeline_jsonb:
        for silence in timeline_jsonb.get("silences", []):
            start = float(silence.get("start_s", 0))
            end = float(silence.get("end_s", 0))
            if end - start >= SILENCE_THRESHOLD_S and 0 < start < video_duration_s:
                raw.add(round(start, 1))

    sorted_bs = sorted(raw)
    filtered: list[float] = [sorted_bs[0]]
    for b in sorted_bs[1:]:
        if b - filtered[-1] >= MAX_CHAPTER_PERIOD_S:
            filtered.append(b)

    # Fill up to MIN_CHAPTERS with evenly spaced boundaries if needed
    if len(filtered) < MIN_CHAPTERS and video_duration_s > MIN_CHAPTERS * 30:
        step = video_duration_s / MIN_CHAPTERS
        for i in range(1, MIN_CHAPTERS):
            candidate = round(i * step, 1)
            if candidate < video_duration_s and all(
                abs(candidate - f) > MAX_CHAPTER_PERIOD_S / 2 for f in filtered
            ):
                filtered.append(candidate)
        filtered.sort()

    return filtered


def _segment_text(
    segments: list[dict],
    start_s: float,
    end_s: float,
) -> str:
    """Slice transcript segments in [start_s, end_s) and return joined text."""
    parts = [
        seg.get("text", "").strip()
        for seg in segments
        if float(seg.get("start", 0)) >= start_s
        and float(seg.get("start", 0)) < end_s
        and seg.get("text", "").strip()
    ]
    return " ".join(parts)[:_SEGMENT_MAX_CHARS]


def parse_chapters(raw_json: str) -> dict:
    """Parse and validate Claude's ChapterList JSON.

    Returns dict with 'chapters' (list) and 'description_block' (str).
    Raises ValueError on malformed JSON or missing required fields.
    """
    data = json.loads(raw_json)
    chapters = data.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        raise ValueError("ChapterList missing 'chapters' list")

    validated_chapters = []
    for c in chapters:
        timestamp_s = float(c.get("timestamp_s", 0))
        timestamp_formatted = str(c.get("timestamp_formatted", format_timestamp(timestamp_s)))
        title = str(c.get("title", "")).strip()[:40]
        if not title:
            title = "Chapter"
        validated_chapters.append(
            {
                "timestamp_s": timestamp_s,
                "timestamp_formatted": timestamp_formatted,
                "title": title,
            }
        )

    description_block = str(data.get("description_block", "")).strip()
    if not description_block:
        description_block = "\n".join(
            f"{c['timestamp_formatted']} {c['title']}" for c in validated_chapters
        )

    return {"chapters": validated_chapters, "description_block": description_block}


def generate_chapters(
    boundaries: list[float],
    segments: list[dict],
    video_duration_s: float,
    task_id: str,
) -> tuple[str, dict]:
    """Call Claude to generate chapter titles; stream tokens to the SSE consumer.

    Returns ``(raw_json, usage)`` where usage is the token-count dict from
    ``stream_and_emit``. Callers should pass usage to ``billing.ledger.record_llm_usage``.
    Raises on network / API errors so the Celery task can retry.
    """
    # Build segment blocks for the user message
    segment_lines: list[str] = []
    for i, b in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else video_duration_s
        text = _segment_text(segments, b, end)
        ts = format_timestamp(b)
        segment_lines.append(f"[{ts}] {text or '(no transcript in this segment)'}")

    # Audit fix (Issue-135 audit): cache_control breakpoint removed. System
    # prompt ≈ 175 tokens, far below Haiku 4.5's 4096-token cacheable-prefix
    # floor. Marker was inert; token log silently reported `cache_read=0`.
    # See docs/DECISIONS.md for the precedent (improvement/brief.py).
    system: list[dict] = [
        {
            "type": "text",
            "text": _SYSTEM_INSTRUCTIONS,
        }
    ]
    messages = [
        {
            "role": "user",
            "content": (
                f"Generate chapter titles for a {format_timestamp(video_duration_s)} video.\n\n"
                "Segments:\n" + "\n".join(segment_lines)
            ),
        }
    ]

    from worker.anthropic_stream import stream_and_emit

    client = _ANTHROPIC.with_options(timeout=60.0)
    try:
        final_text, usage = stream_and_emit(
            client,
            task_id,
            model=settings.ANTHROPIC_MODEL_CHAPTERS,
            # 2000 (was 512): a 1h+ video yields 20+ chapters whose JSON exceeded
            # the 512-token cap, truncating the response and failing all 3 retries
            # identically. description_block was also dropped from the output schema
            # (parse_chapters rebuilds it in Python), so the model spends its budget
            # only on the chapters array.
            max_tokens=2000,
            system=system,
            messages=messages,
        )
    except (RateLimitError, APIStatusError, APIConnectionError) as exc:
        logger.error(
            "generate_chapters LLM error task=%s exc_type=%s", task_id, type(exc).__name__
        )
        raise
    logger.info(
        "generate_chapters tokens: in=%d cached_read=%d cached_write=%d out=%d",
        usage["input_tokens"],
        usage["cache_read"],
        usage["cache_creation"],
        usage["output_tokens"],
    )
    record_llm_metric(settings.ANTHROPIC_MODEL_CHAPTERS, usage)
    return final_text, usage
