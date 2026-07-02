"""
Rank scored clip candidates and persist them to the clips table.
"""

import asyncio
import logging
import math
import uuid
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clip_engine.candidates import extract_candidates
from clip_engine.scoring import score_candidates
from models import Clip, ClipFormat, RenderStatus

logger = logging.getLogger(__name__)


def _safe_score(value: object) -> float:
    """Coerce a clip score to a sortable float, mapping NaN/None/garbage to -inf.

    Python's sort places NaN unpredictably (comparisons return False), so a single
    NaN score makes the whole ranking order undefined. Sorting NaN/missing scores to
    the bottom deterministically keeps a broken score from leaking to rank 1. (Issue 328)
    """
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("-inf")
    return f if math.isfinite(f) else float("-inf")


def rank_candidates(candidates: list[dict]) -> list[dict]:
    """Sort by score descending, assign rank (1 = best). Pure function."""
    ranked = sorted(candidates, key=lambda c: _safe_score(c.get("score", 0.0)), reverse=True)
    for i, c in enumerate(ranked):
        c["rank"] = i + 1
    return ranked


async def rerank_with_preference(
    clips: list[Clip],
    session: AsyncSession,
    creator_id: uuid.UUID,
) -> list[Clip]:
    """
    Re-rank an already-scored clip list using the creator's preference model.
    Falls back silently if no model is trained yet (below threshold).
    """
    from preference.features import clip_features
    from preference.model import preference_weight
    from preference.train import load_latest

    scorer = await load_latest(session, creator_id)
    if scorer is None:
        return clips

    # Honest personalization threshold: below it the model gets weight 0, so the
    # DNA + signal ranking is returned unchanged (no false personalization). The
    # weight ramps with the creator's own feedback volume. (Issue 60)
    weight = preference_weight(scorer.label_count)
    if weight == 0.0:
        return clips

    def _features(clip: Clip) -> list[float]:
        feats_dict = (clip.signals_jsonb or {}).get("features", {})
        return clip_features(
            signal_density=feats_dict.get("signal_density", 0.0),
            hook_energy=feats_dict.get("hook_energy", 0.0),
            silence_ratio=feats_dict.get("silence_ratio", 0.0),
            dna_match=clip.dna_match,
            clip_duration_s=feats_dict.get("clip_duration_s", 0.0),
            setup_length_s=feats_dict.get("setup_length_s", 0.0),
            has_retention_spike=feats_dict.get("has_retention_spike", False),
            has_laughter=feats_dict.get("has_laughter", False),
        )

    # Score everything BEFORE mutating, so a broken model leaves the DNA ranking
    # untouched (honest fallback) instead of half-blended. (Issue 71)
    try:
        pref_scores = [scorer.predict_score(_features(clip)) for clip in clips]
    except Exception as exc:
        logger.warning("Preference rerank failed (%s) — keeping DNA ranking", exc)
        return clips

    # A non-finite prediction (NaN/inf) would blend into clip.score and make the
    # subsequent sort order undefined. Treat it as a broken model and fall back to
    # the DNA ranking untouched — the same honest behavior as a predict exception.
    if not all(math.isfinite(s) for s in pref_scores):
        logger.warning(
            "Preference model produced a non-finite score for creator %s — keeping DNA ranking",
            creator_id,
        )
        return clips

    for clip, pref_score in zip(clips, pref_scores, strict=True):
        clip.score = (1.0 - weight) * (clip.score or 0.0) + weight * pref_score

    clips.sort(key=lambda c: _safe_score(c.score), reverse=True)
    for i, clip in enumerate(clips):
        clip.rank = i + 1

    return clips


async def load_existing_clips(
    session: AsyncSession,
    video_id: uuid.UUID,
    creator_id: uuid.UUID,
) -> list[Clip]:
    """Return this creator's already-persisted clips for ``video_id`` in rank order.

    The Issue-61 idempotency guard: callers check this BEFORE spending LLM tokens
    on scoring, and ``persist_ranked_clips`` re-checks it before inserting so a
    Celery redelivery / concurrent request can never delete+reinsert (which would
    cascade-delete Clip.feedback / Clip.outcome). Per-creator isolation: the
    creator_id predicate backs the RLS policy rather than replacing it.
    """
    result = await session.execute(
        select(Clip)
        .where(Clip.video_id == video_id, Clip.creator_id == creator_id)
        .order_by(Clip.rank)
    )
    return list(result.scalars().all())


async def score_and_rank(
    video_id: uuid.UUID,
    creator_id: uuid.UUID,
    timeline: dict,
    dna_brief: str | None = None,
    transcript_segments: list | None = None,
    max_candidates: int = 8,
    ledger_session_factory: Callable[[], AsyncSession] | None = None,
) -> list[dict]:
    """Extract candidates → score (per-candidate LLM call) → rank. Session-free.

    Split out of the old ``generate_and_rank_clips`` (Issue 82b): the LLM round-trip
    here can take 30–120 s, so no DB session may be held across it — callers release
    their session first and persist the returned ranking via ``persist_ranked_clips``
    on a freshly acquired session. ``ledger_session_factory`` lets the scoring layer
    open a short-lived session AFTER the LLM call for the usage-ledger write.
    """
    # Candidate extraction is CPU-bound (numpy array build + scipy find_peaks over
    # duration/0.5 samples). Offload it so it can't stall the API event loop and the
    # other concurrent requests on this worker. (Issue C)
    #
    # Flat word list extracted from transcript segments for sentence-boundary snapping
    # (principle #12). Empty list → snapping skipped gracefully. (Issue 127)
    words = [w for seg in (transcript_segments or []) for w in seg.get("words", [])]
    candidates = await asyncio.to_thread(
        lambda: extract_candidates(timeline, max_candidates, words=words or None)
    )
    if not candidates:
        logger.info("No candidates found for video %s", video_id)
        return []

    scored = await score_candidates(
        candidates,
        timeline,
        dna_brief,
        transcript_segments,
        creator_id=creator_id,
        ledger_session_factory=ledger_session_factory,
    )
    return rank_candidates(scored)


async def persist_ranked_clips(
    session: AsyncSession,
    video_id: uuid.UUID,
    creator_id: uuid.UUID,
    ranked: list[dict],
) -> list[Clip]:
    """Persist a pre-computed ranking to the clips table. DB-only — no LLM calls.

    Idempotent (Issue 61): if clips already exist for this video the call is a
    no-op and the existing clips are returned in rank order. The pipeline
    generates clips exactly once; a Celery redelivery (at-least-once) must NOT
    delete+reinsert, because Clip.feedback / Clip.outcome cascade-delete and that
    would destroy the creator's feedback labels and published-clip outcomes.
    Returns persisted Clip ORM objects in rank order.
    """
    existing = await load_existing_clips(session, video_id, creator_id)
    if existing:
        logger.info(
            "Clips already exist for video %s (%d) — skipping regeneration", video_id, len(existing)
        )
        return existing

    if not ranked:
        return []

    # The existing-clips early-return above short-circuits when ANY clip exists
    # for this video (Issue 61 idempotency). Local Issue 46's selective-DELETE
    # branch was unreachable under that guard and is dropped during the merge —
    # the stronger guarantee here is "never delete+reinsert", which makes the
    # 46-style protection for done/running clips automatic.

    clips: list[Clip] = []
    for c in ranked:
        clip = Clip(
            video_id=video_id,
            creator_id=creator_id,
            setup_start_s=c["setup_start_s"],
            start_s=c["start_s"],
            end_s=c["end_s"],
            peak_s=c["peak_s"],
            score=c.get("score"),
            # dna_match is the raw DNA-only fit from Claude, NOT the composite score —
            # seeding it with the composite would make it collinear with its own
            # label-generating signal in the preference feature vector. (Issue 103 #5)
            dna_match=c.get("dna_match"),
            signals_jsonb={
                "features": c.get("features", {}),
                "principle": c.get("principle", ""),
                "reasoning": c.get("reasoning", ""),
            },
            format=ClipFormat.short,
            render_status=RenderStatus.pending,
            rank=c["rank"],
        )
        session.add(clip)
        clips.append(clip)

    await session.commit()
    for clip in clips:
        await session.refresh(clip)

    # Apply the creator's preference model on top of the DNA/signal ranking.
    # No-ops below the personalization threshold or when no model is trained. (Issue 60)
    clips = await rerank_with_preference(clips, session, creator_id)
    await session.commit()

    logger.info("Generated %d ranked clips for video %s", len(clips), video_id)
    return clips
