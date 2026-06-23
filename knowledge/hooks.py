"""First-30s hook analysis grounded in the creator's own retention curves (Issue 130).

Prompt structure (three system blocks):
  Block 1 — static instructions: role, task, honesty constraints.
  Block 2 — DNA brief (cache_control breakpoint): per-creator, stable across calls.
  Block 3 — per-video hook data: retention drop + transcript. Uncached.

Claude receives the creator's DNA brief, computed retention drop point, transcript
excerpt, and searches for current niche hook patterns via web_search.

Returns JSON string with HookReport schema; caller parses via parse_hook_report().
"""

import json
import logging

import httpx
import numpy as np
from anthropic import Anthropic

from config import settings
from observability import record_llm_tokens

logger = logging.getLogger(__name__)

_HAIKU_MODEL = "claude-haiku-4-5-20251001"

_ANTHROPIC = Anthropic(
    api_key=settings.ANTHROPIC_API_KEY,
    timeout=httpx.Timeout(120.0, connect=10.0),
    max_retries=2,
)

HOOK_WINDOW_S = 30
TRANSCRIPT_EXCERPT_S = 60.0
DROP_THRESHOLD = 0.10  # 10 percentage points below creator median
_DNA_BRIEF_MAX_CHARS = 3000

_SYSTEM_INSTRUCTIONS = """\
You are a YouTube hook performance analyst with access to real-time web search.

Your task: analyze the first-30s hook of a video using the creator's own retention
data and current niche hook research.

You will receive:
  - The creator's DNA brief (their style, audience, top-performing patterns)
  - Computed retention analytics: where and by how much retention drops in the first 30s
  - A transcript excerpt of the first 60 seconds
  - The creator's channel niche for targeted search

Use the web_search tool (1–2 searches maximum) to research:
  1. Current high-performing hook patterns in this creator's niche
  2. Retention recovery techniques relevant to this content type

Then analyze the data and return ONLY a valid JSON object with this exact schema:
{
  "retention_drop_at_s": <float|null>,
  "retention_at_drop": <float|null>,
  "transcript_at_drop": "<string>",
  "diagnosis": "<string>",
  "rewrite_suggestion": "<string>",
  "honesty_disclaimer": "<string>"
}

RULES:
  - diagnosis: 2–4 sentences. Cite the specific retention number and timestamp.
    Use hedged language: "suggests", "may be", "appears to" — never "will" or "guaranteed".
  - rewrite_suggestion: 2–4 sentences. Use "suggestion" not "fix" or "guarantee".
    Ground it in the creator's own top-performing patterns when possible.
  - honesty_disclaimer: Always include exactly:
    "This analysis is grounded in your channel's retention data — it reflects patterns,
    not a guarantee of future performance."
  - If retention_drop_at_s is null (no significant drop in the first 30s), diagnosis
    acknowledges this and gives general hook-strengthening guidance based on the data.
  - Return valid JSON ONLY — no preamble, no markdown code fences, no extra text."""


def compute_retention_drop(
    video_curves: list[tuple[float, float]],
    creator_curves: list[list[tuple[float, float]]],
    window_s: int = HOOK_WINDOW_S,
    threshold: float = DROP_THRESHOLD,
) -> tuple[float | None, float | None]:
    """Find the earliest second where this video's retention falls >threshold below the creator median.

    video_curves: [(timestamp_s, audience_watch_ratio)] for the target video
    creator_curves: list of curves (same format) for other creator videos

    Returns (drop_timestamp_s, retention_at_drop) or (None, None) if no significant drop.
    The creator median baseline is computed at each second using linear interpolation.
    """
    if not video_curves or not creator_curves:
        return None, None

    grid = np.arange(0.0, window_s + 1.0, 1.0)

    def _interp_curve(pairs: list[tuple[float, float]]) -> np.ndarray | None:
        in_window = [(t, r) for t, r in pairs if t <= window_s + 10]
        if len(in_window) < 2:
            return None
        sorted_pairs = sorted(in_window)
        ts = [p[0] for p in sorted_pairs]
        rs = [p[1] for p in sorted_pairs]
        if ts[0] > 0:
            ts = [0.0] + ts
            rs = [1.0] + rs
        return np.interp(grid, ts, rs)

    video_interp = _interp_curve(video_curves)
    if video_interp is None:
        return None, None

    creator_interps = [interp for c in creator_curves if (interp := _interp_curve(c)) is not None]
    if not creator_interps:
        return None, None

    creator_median = np.median(creator_interps, axis=0)
    diff = creator_median - video_interp
    drop_indices = np.where(diff > threshold)[0]

    if len(drop_indices) == 0:
        return None, None

    idx = int(drop_indices[0])
    return float(grid[idx]), float(video_interp[idx])


def parse_hook_report(raw_json: str) -> dict:
    """Parse and validate Claude's HookReport JSON.

    Returns a dict with all required HookReport fields.
    Raises ValueError on malformed JSON or missing required fields.
    """
    data = json.loads(raw_json)
    required = {"diagnosis", "rewrite_suggestion", "honesty_disclaimer", "transcript_at_drop"}
    missing = required - set(data.keys())
    if missing:
        raise ValueError(f"HookReport missing fields: {missing}")
    return {
        "retention_drop_at_s": data.get("retention_drop_at_s"),
        "retention_at_drop": data.get("retention_at_drop"),
        "transcript_at_drop": str(data.get("transcript_at_drop", "")),
        "diagnosis": str(data.get("diagnosis", "")),
        "rewrite_suggestion": str(data.get("rewrite_suggestion", "")),
        "honesty_disclaimer": str(data.get("honesty_disclaimer", "")),
    }


def analyze_hook(
    channel_title: str,
    dna_brief: str | None,
    retention_drop_at_s: float | None,
    retention_at_drop: float | None,
    creator_median_at_drop: float | None,
    transcript_excerpt: str,
    task_id: str,
) -> str:
    """Call Claude with web_search; stream tokens to the SSE consumer.

    Returns the raw JSON string from Claude's final text block.
    Raises on network / API errors so the Celery task can retry.
    """
    dna_text = (dna_brief or "No DNA profile available yet.")[:_DNA_BRIEF_MAX_CHARS]

    if retention_drop_at_s is not None:
        drop_info = (
            f"Retention drop detected at {retention_drop_at_s:.1f}s: "
            f"video at {retention_at_drop:.1%}, "
            f"creator median at {(creator_median_at_drop or 0):.1%} "
            f"({(((creator_median_at_drop or 0) - (retention_at_drop or 0)) * 100):.1f}pp below median)."
        )
    else:
        drop_info = "No significant retention drop detected in the first 30 seconds."

    # Audit fix (Issue-135 audit): cache_control breakpoint removed. The
    # static instructions + DNA brief ≈ 900 tokens, which is below Haiku
    # 4.5's 4096-token minimum cacheable-prefix size. The marker was inert
    # and the token log silently reported `cache_read=0`. Same precedent as
    # improvement/brief.py (see docs/DECISIONS.md). One-shot per video, so
    # the missed cache is also low-frequency — leaving uncached is correct.
    system: list[dict] = [
        {"type": "text", "text": _SYSTEM_INSTRUCTIONS},
        {
            "type": "text",
            "text": f"CREATOR DNA PROFILE:\n{dna_text}",
        },
        {
            "type": "text",
            "text": (
                f"CHANNEL: {channel_title}\n\n"
                f"RETENTION ANALYTICS:\n{drop_info}\n\n"
                f"TRANSCRIPT (first 60s):\n{transcript_excerpt or 'No transcript available.'}"
            ),
        },
    ]
    tools: list[dict] = [{"type": settings.ANTHROPIC_WEB_SEARCH_TOOL, "name": "web_search"}]
    messages = [
        {
            "role": "user",
            "content": (
                f"Analyze the hook for '{channel_title}'. "
                "Search for current hook patterns in this niche, then return the JSON report."
            ),
        }
    ]

    from worker.anthropic_stream import stream_and_emit

    client = _ANTHROPIC.with_options(timeout=120.0)
    final_text, usage = stream_and_emit(
        client,
        task_id,
        model=_HAIKU_MODEL,
        max_tokens=1024,
        system=system,
        messages=messages,
        tools=tools,
    )
    logger.info(
        "hook_analysis tokens: in=%d cached_read=%d cached_write=%d out=%d",
        usage["input_tokens"],
        usage["cache_read"],
        usage["cache_creation"],
        usage["output_tokens"],
    )
    record_llm_tokens(
        provider="anthropic",
        model=_HAIKU_MODEL,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        cache_read_tokens=usage["cache_read"],
        cache_creation_tokens=usage["cache_creation"],
    )
    return final_text
