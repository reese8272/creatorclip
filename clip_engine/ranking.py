"""
Rank scored clip candidates and persist them to the clips table.
"""

import logging
import uuid

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from clip_engine.candidates import extract_candidates
from clip_engine.scoring import score_candidates
from models import Clip, ClipFormat, RenderStatus

logger = logging.getLogger(__name__)


def rank_candidates(candidates: list[dict]) -> list[dict]:
    """Sort by score descending, assign rank (1 = best). Pure function."""
    ranked = sorted(candidates, key=lambda c: c.get("score", 0.0), reverse=True)
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
    from preference.train import load_latest

    scorer = await load_latest(session, creator_id)
    if scorer is None:
        return clips

    for clip in clips:
        feats_dict = (clip.signals_jsonb or {}).get("features", {})
        feats = clip_features(
            signal_density=feats_dict.get("signal_density", 0.0),
            hook_energy=feats_dict.get("hook_energy", 0.0),
            silence_ratio=feats_dict.get("silence_ratio", 0.0),
            dna_match=clip.dna_match,
            clip_duration_s=feats_dict.get("clip_duration_s", 0.0),
            setup_length_s=feats_dict.get("setup_length_s", 0.0),
            has_retention_spike=feats_dict.get("has_retention_spike", False),
            has_laughter=feats_dict.get("has_laughter", False),
        )
        pref_score = scorer.predict_score(feats)
        # Blend DNA score with preference score (equal weight)
        clip.score = 0.5 * (clip.score or 0.0) + 0.5 * pref_score

    clips.sort(key=lambda c: c.score or 0.0, reverse=True)
    for i, clip in enumerate(clips):
        clip.rank = i + 1

    return clips


async def generate_and_rank_clips(
    session: AsyncSession,
    video_id: uuid.UUID,
    creator_id: uuid.UUID,
    timeline: dict,
    dna_brief: str | None = None,
    transcript_segments: list | None = None,
    max_candidates: int = 8,
) -> list[Clip]:
    """
    Extract candidates → score → rank → persist to the clips table.

    Any existing clips for this video are replaced.
    Returns persisted Clip ORM objects in rank order.
    """
    candidates = extract_candidates(timeline, max_candidates=max_candidates)
    if not candidates:
        logger.info("No candidates found for video %s", video_id)
        return []

    scored = await score_candidates(candidates, timeline, dna_brief, transcript_segments)
    ranked = rank_candidates(scored)

    # Replace existing clips for this video — but preserve any clip that is
    # already rendering or rendered (Issue 46). A late retry of generate_clips
    # must not orphan R2 objects or break the ClipOutcome FK chain on done rows.
    await session.execute(
        delete(Clip).where(
            Clip.video_id == video_id,
            Clip.render_status.notin_([RenderStatus.done, RenderStatus.running]),
        )
    )

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
            dna_match=c.get("score"),  # seed; refined when preference model is trained
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

    logger.info("Generated %d ranked clips for video %s", len(clips), video_id)
    return clips
