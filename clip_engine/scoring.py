"""
Score clip candidates against creator DNA (or signal features if no DNA present).

When a DNA brief is available, Claude is called once per video with the brief as
a prompt-cached prefix and all candidates as a single batched user message, keeping
token cost minimal.  Without DNA, signal features produce a cold-start score.

Every returned candidate includes a named principle from CLIPPING_PRINCIPLES.md.
"""

import json
import logging

import httpx
from anthropic import AsyncAnthropic

from clip_engine.window import RESOLUTION_S, build_signal_array
from config import settings

logger = logging.getLogger(__name__)

_ANTHROPIC = AsyncAnthropic(
    api_key=settings.ANTHROPIC_API_KEY,
    timeout=httpx.Timeout(60.0, connect=10.0),
    max_retries=2,
)

_PRINCIPLES = [
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
]

_SYSTEM_PREFIX = """\
You are a clip-selection expert for YouTube content creation.

Evaluate the candidate clips below against this creator's DNA profile to find the best
fits for their audience and proven style.

CREATOR DNA:
{dna_brief}

NAMED SCORING PRINCIPLES (cite exactly one per clip):
{principles}
"""

_USER_TEMPLATE = """\
Score each clip candidate from 0.0 (poor fit) to 1.0 (excellent fit) for this creator.

Candidates:
{candidates_json}

Return ONLY a valid JSON array — no prose, no markdown fences. Each element:
{{"index": <int>, "score": <float 0-1>, "principle": "<exact principle name>", "reasoning": "<one sentence>"}}
"""


def compute_features(candidate: dict, timeline: dict) -> dict:
    """Compute signal-based features for a candidate window."""
    _, signal = build_signal_array(timeline)
    duration_s = timeline.get("duration_s", 0.0)

    if len(signal) == 0 or duration_s <= 0:
        return {
            "signal_density": 0.0,
            "hook_energy": 0.0,
            "silence_ratio": 0.0,
            "has_retention_spike": False,
            "has_laughter": False,
            "clip_duration_s": 0.0,
            "setup_length_s": 0.0,
        }

    setup_s = float(candidate["setup_start_s"])
    end_s = float(candidate["end_s"])
    peak_s = float(candidate["peak_s"])

    i0 = max(0, int(setup_s / RESOLUTION_S))
    i1 = min(len(signal), int(end_s / RESOLUTION_S) + 1)
    clip_sig = signal[i0:i1]

    hook_i1 = min(len(signal), int((setup_s + 5.0) / RESOLUTION_S) + 1)
    hook_sig = signal[i0:hook_i1]

    events = timeline.get("events", [])

    def _in_window(e: dict) -> bool:
        return setup_s <= e.get("start_s", 0.0) <= end_s

    silence_events = [e for e in events if e.get("type") == "silence" and _in_window(e)]
    silence_duration = sum(
        e.get("end_s", e.get("start_s", 0.0)) - e.get("start_s", 0.0) for e in silence_events
    )
    clip_dur = max(0.1, end_s - setup_s)

    return {
        "signal_density": float(clip_sig.mean()) if len(clip_sig) else 0.0,
        "hook_energy": float(hook_sig.mean()) if len(hook_sig) else 0.0,
        "silence_ratio": silence_duration / clip_dur,
        "has_retention_spike": any(
            e.get("type") == "retention_spike" and _in_window(e) for e in events
        ),
        "has_laughter": any(e.get("type") == "laughter" and _in_window(e) for e in events),
        "clip_duration_s": clip_dur,
        "setup_length_s": peak_s - setup_s,
    }


def _signal_score(features: dict) -> float:
    """Signal-only score for the cold-start path (no DNA profile)."""
    density = min(1.0, max(0.0, features["signal_density"] / 5.0))
    hook = min(1.0, max(0.0, features["hook_energy"] / 3.0))
    spike = 0.30 if features["has_retention_spike"] else 0.0
    laugh = 0.10 if features["has_laughter"] else 0.0
    return min(1.0, 0.40 * density + 0.20 * hook + spike + laugh)


def _transcript_excerpt(setup_s: float, end_s: float, segments: list | None) -> str:
    """Extract transcript text for the candidate window."""
    if not segments:
        return ""
    parts = [
        seg.get("text", "").strip()
        for seg in segments
        if seg.get("start", 0.0) >= setup_s and seg.get("end", 0.0) <= end_s
    ]
    return " ".join(parts)[:300]


async def score_candidates(
    candidates: list[dict],
    timeline: dict,
    dna_brief: str | None = None,
    transcript_segments: list | None = None,
) -> list[dict]:
    """
    Score and annotate candidates in-place. Returns the enriched list.

    Cold-start (no dna_brief): signal features only, principle = "Retention curve is ground truth".
    DNA path: single batched Claude call with DNA brief as cached prefix.
    """
    if not candidates:
        return []

    for c in candidates:
        c["features"] = compute_features(c, timeline)

    if not dna_brief:
        for c in candidates:
            c["score"] = _signal_score(c["features"])
            c["principle"] = "Retention curve is ground truth"
            c["reasoning"] = "Scored on signal density — DNA profile not available yet."
        return candidates

    # Build payload for Claude
    payload = [
        {
            "index": i,
            "setup_start_s": c["setup_start_s"],
            "peak_s": c["peak_s"],
            "end_s": c["end_s"],
            "duration_s": round(c["features"]["clip_duration_s"], 1),
            "has_retention_spike": c["features"]["has_retention_spike"],
            "has_laughter": c["features"]["has_laughter"],
            "hook_energy": round(c["features"]["hook_energy"], 3),
            "signal_density": round(c["features"]["signal_density"], 3),
            "transcript_excerpt": _transcript_excerpt(
                c["setup_start_s"], c["end_s"], transcript_segments
            ),
        }
        for i, c in enumerate(candidates)
    ]

    system_text = _SYSTEM_PREFIX.format(
        dna_brief=dna_brief,
        principles="\n".join(f"- {p}" for p in _PRINCIPLES),
    )
    user_text = _USER_TEMPLATE.format(candidates_json=json.dumps(payload, indent=2))

    response = await _ANTHROPIC.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=1200,
        # The system prefix (instructions + this creator's DNA brief + principles) is
        # byte-identical across all of one creator's videos, so it prompt-caches and
        # is reused video-to-video. A creator's backlog is scored as a burst when the
        # worker drains their ingest queue (onboarding/backfill), which routinely spans
        # more than the 5-minute default TTL — so use the 1h TTL to keep the prefix warm
        # across the whole burst. (cache only engages once the prefix clears Sonnet 4.6's
        # 2048-token floor; a static-only breakpoint wouldn't — the static text is ~230
        # tokens — so the per-creator prefix is the only worthwhile cache point. See
        # docs/DECISIONS.md 2026-05-29, verified via /claude-api.)
        # cache_control is valid on the wire API for prompt caching but absent from
        # the SDK's TextBlockParam TypedDict.
        system=[
            {  # type: ignore[typeddict-unknown-key]
                "type": "text",
                "text": system_text,
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            }
        ],
        messages=[{"role": "user", "content": user_text}],
    )

    logger.info(
        "clip_scoring tokens: in=%d cached_read=%d cached_write=%d out=%d",
        response.usage.input_tokens,
        getattr(response.usage, "cache_read_input_tokens", 0),
        getattr(response.usage, "cache_creation_input_tokens", 0),
        response.usage.output_tokens,
    )

    text = next((b.text for b in response.content if b.type == "text"), "[]")
    try:
        scored = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Claude scoring returned non-JSON; falling back to signal scores")
        scored = []

    score_map = {
        item["index"]: item for item in scored if isinstance(item, dict) and "index" in item
    }
    for i, c in enumerate(candidates):
        hit = score_map.get(i)
        if hit:
            c["score"] = min(1.0, max(0.0, float(hit.get("score", 0.5))))
            c["principle"] = hit.get("principle", "Audience-fit over generic virality")
            c["reasoning"] = hit.get("reasoning", "")
        else:
            c["score"] = _signal_score(c["features"])
            c["principle"] = "Retention curve is ground truth"
            c["reasoning"] = "Fallback: signal-only score"

    return candidates
