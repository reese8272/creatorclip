"""
Score clip candidates against creator DNA (or signal features if no DNA present).

When a DNA brief is available, Claude is called once per video with the brief as
a prompt-cached prefix and all candidates as a single batched user message, keeping
token cost minimal.  Without DNA, signal features produce a cold-start score.

Every returned candidate includes a named principle from CLIPPING_PRINCIPLES.md.
"""

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime

import httpx
from anthropic import AsyncAnthropic
from sqlalchemy.ext.asyncio import AsyncSession

from billing.ledger import _estimate_cost_usd, increment_usage
from clip_engine.window import RESOLUTION_S, build_signal_array
from config import settings
from knowledge.util import UNTRUSTED_CONTENT_POLICY, wrap_untrusted

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

# Static, creator-independent instructions. Kept as a SEPARATE system block placed
# BEFORE the per-creator DNA so the stable bytes lead the prefix (prompt-caching best
# practice: stable content first, volatile content after the last breakpoint). The DNA
# block below carries the cache breakpoint. (Issue 78b)
_SYSTEM_STATIC = (
    UNTRUSTED_CONTENT_POLICY
    + """\
You are a clip-selection expert for YouTube content creation.

Evaluate the candidate clips against the creator's DNA profile (provided below) to find
the best fits for their audience and proven style.

NAMED SCORING PRINCIPLES (cite exactly one per clip):
{principles}
"""
)

_USER_TEMPLATE = """\
Score each clip candidate from 0.0 (poor fit) to 1.0 (excellent fit) for this creator.

Each candidate includes a transcript_context with three labeled sections:
  [BEFORE] — what happened before the clip (setup / lead-in)
  [CLIP]   — the candidate window itself
  [AFTER]  — what immediately followed (payoff / reaction)

Use BEFORE to judge whether the clip captures a complete thought or starts mid-idea.
Use AFTER to check if the real payoff lands just outside the window (score lower if so).

Candidates:
{candidates_json}

Return ONLY a valid JSON array — no prose, no markdown fences. Each element:
{{"index": <int>, "dna_score": <float 0-1 — DNA-only fit, before any signal blending>, "score": <float 0-1 — final composite>, "principle": "<exact principle name>", "reasoning": "<one sentence>"}}
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


_CONTEXT_BEFORE_S = 60.0  # seconds of lead-in context before the clip window
_CONTEXT_AFTER_S = 30.0  # seconds of follow-on context after the clip window


def _transcript_context(setup_s: float, end_s: float, segments: list | None) -> str:
    """Three-section transcript context for the candidate window (Issue 127).

    Returns [BEFORE] / [CLIP] / [AFTER] sections so Claude can judge whether the
    clip captures a complete thought and whether the payoff lands inside the window.
    """
    if not segments:
        return ""

    before_start = max(0.0, setup_s - _CONTEXT_BEFORE_S)
    after_end = end_s + _CONTEXT_AFTER_S

    def _gather(start_min: float, end_max: float, cap: int) -> str:
        parts = [
            seg.get("text", "").strip()
            for seg in segments
            if seg.get("start", 0.0) >= start_min and seg.get("end", 0.0) <= end_max
        ]
        return " ".join(parts)[:cap]

    before = _gather(before_start, setup_s, 200)
    clip = _gather(setup_s, end_s, 250)
    after = _gather(end_s, after_end, 150)

    # Issue 224: route each section through wrap_untrusted so label tokens cannot
    # be spoofed by transcript content. The outer candidates payload is already
    # json.dumps'd at build time, but the inner text values are still raw strings
    # inside that JSON — wrap_untrusted JSON-encodes each section so a transcript
    # that contains "[CLIP]:" cannot inject a fake section label (incremental
    # hardening; primary risk is low since payload is already JSON-serialized).
    sections = []
    if before:
        sections.append(wrap_untrusted("transcript_before", before).rstrip())
    if clip:
        sections.append(wrap_untrusted("transcript_clip", clip).rstrip())
    if after:
        sections.append(wrap_untrusted("transcript_after", after).rstrip())

    return "\n".join(sections)


async def score_candidates(
    candidates: list[dict],
    timeline: dict,
    dna_brief: str | None = None,
    transcript_segments: list | None = None,
    creator_id: uuid.UUID | None = None,
    session: AsyncSession | None = None,
) -> list[dict]:
    """
    Score and annotate candidates in-place. Returns the enriched list.

    Cold-start (no dna_brief): signal features only, principle = "Retention curve is ground truth".
    DNA path: single batched Claude call with DNA brief as cached prefix.
    """
    if not candidates:
        return []

    # Feature computation is CPU-bound (signal-array build per candidate). Offload it
    # so scoring never blocks the event loop on this worker. (Issue C)
    def _compute_features_all() -> None:
        for c in candidates:
            c["features"] = compute_features(c, timeline)

    await asyncio.to_thread(_compute_features_all)

    if not dna_brief:
        for c in candidates:
            c["score"] = _signal_score(c["features"])
            # No DNA profile — dna_match stays None so the preference feature vector
            # zero-defaults it (preference/features.py:24) rather than seeding it with
            # a collinear composite signal. (Issue 103 fix #5)
            c["dna_match"] = None
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
            "transcript_context": _transcript_context(
                c["setup_start_s"], c["end_s"], transcript_segments
            ),
        }
        for i, c in enumerate(candidates)
    ]

    static_text = _SYSTEM_STATIC.format(principles="\n".join(f"- {p}" for p in _PRINCIPLES))
    user_text = _USER_TEMPLATE.format(candidates_json=json.dumps(payload, indent=2))

    # Two-block system: static instructions first (identical across all creators), then the
    # per-creator DNA brief carrying the cache breakpoint. The brief is constant across a
    # creator's videos, so caching it lets repeat scorings within the window skip re-billing
    # the prefix at full price. The 1h TTL widens that window past the default 5 min, so a
    # creator's batch of videos (ingested/scored over a longer span) still hits the cache.
    # The volatile per-video candidates stay in the uncached user message. (Issue 78b)
    response = await _ANTHROPIC.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=1200,
        system=[
            {"type": "text", "text": static_text},
            {
                "type": "text",
                "text": f"CREATOR DNA:\n{dna_brief}",
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            },
        ],
        messages=[{"role": "user", "content": user_text}],
    )

    # anthropic>=0.105 exposes per-TTL cache-write tiers on usage.cache_creation;
    # cached_write_1h lets us confirm the ttl:"1h" breakpoint actually lands in
    # the 1-hour tier (not the default 5-min one). Defensive getattrs keep the
    # line working if usage.cache_creation is None (no write this call).
    _cache_creation = getattr(response.usage, "cache_creation", None)
    _tokens_in = response.usage.input_tokens
    _tokens_out = response.usage.output_tokens
    _cache_read_tokens = getattr(response.usage, "cache_read_input_tokens", 0) or 0
    _cache_write_tokens = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
    logger.info(
        "clip_scoring tokens: in=%d cached_read=%d cached_write=%d cached_write_1h=%d out=%d",
        _tokens_in,
        getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        getattr(_cache_creation, "ephemeral_1h_input_tokens", 0) or 0,
        _tokens_out,
    )

    if creator_id is not None and session is not None:
        try:
            cost = _estimate_cost_usd(
                _tokens_in,
                _tokens_out,
                settings.COST_PER_MTOK_IN_SONNET,
                settings.COST_PER_MTOK_OUT_SONNET,
                cache_read_tokens=_cache_read_tokens,
                cache_creation_tokens=_cache_write_tokens,
                # DNA-brief block is cached with ttl:"1h" (above) → 2× write premium.
                cache_write_multiplier=2.0,
            )
            await increment_usage(
                session,
                creator_id,
                datetime.now(UTC).strftime("%Y-%m"),
                _tokens_in,
                _tokens_out,
                cost,
            )
        except Exception as _exc:  # noqa: BLE001 — best-effort; never block scoring
            logger.warning("clip_scoring usage ledger write failed: %s", _exc)

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
            # Persist the raw DNA-only fit separately from the composite score so the
            # preference feature vector is not seeded with its own label-generating signal
            # (collinearity fix — Issue 103 #5). Claude returns both fields; fall back to
            # the composite score if the model omits dna_score (graceful degradation).
            raw_dna = hit.get("dna_score", hit.get("score", 0.5))
            c["dna_match"] = min(1.0, max(0.0, float(raw_dna)))
            c["score"] = min(1.0, max(0.0, float(hit.get("score", 0.5))))
            c["principle"] = hit.get("principle", "Audience-fit over generic virality")
            c["reasoning"] = hit.get("reasoning", "")
        else:
            c["score"] = _signal_score(c["features"])
            c["dna_match"] = None
            c["principle"] = "Retention curve is ground truth"
            c["reasoning"] = "Fallback: signal-only score"

    return candidates
