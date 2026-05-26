"""
CRUD for the creator_dna table.

DNA profiles are versioned and never deleted:  draft → confirmed → superseded.
Only one row per creator can be in 'confirmed' state at a time.
"""

import logging
import uuid

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Creator, CreatorDna, DnaStatus, OnboardingState

logger = logging.getLogger(__name__)


async def create_draft(
    session: AsyncSession,
    creator_id: uuid.UUID,
    patterns: dict,
    top_video_ids: list[uuid.UUID],
    bottom_video_ids: list[uuid.UUID],
    brief_text: str,
    optimal_clip_len_s: float | None = None,
    best_source_region: str | None = None,
    optimal_upload_gap_h: float | None = None,
) -> CreatorDna:
    """Create a new draft DNA profile at version max+1."""
    result = await session.execute(
        select(sa.func.max(CreatorDna.version)).where(CreatorDna.creator_id == creator_id)
    )
    max_version = result.scalar() or 0

    dna = CreatorDna(
        creator_id=creator_id,
        version=max_version + 1,
        brief_text=brief_text,
        patterns_jsonb=patterns,
        top_video_ids_jsonb=[str(v) for v in top_video_ids],
        bottom_video_ids_jsonb=[str(v) for v in bottom_video_ids],
        optimal_clip_len_s=optimal_clip_len_s,
        best_source_region=best_source_region,
        optimal_upload_gap_h=optimal_upload_gap_h,
        status=DnaStatus.draft,
    )
    session.add(dna)
    await session.commit()
    await session.refresh(dna)
    logger.info("DNA draft v%d created for creator %s", dna.version, creator_id)
    return dna


async def confirm_draft(session: AsyncSession, creator_id: uuid.UUID) -> CreatorDna:
    """
    Confirm the latest draft, superseding any previously confirmed profile.
    Advances creator onboarding_state from dna_pending → active.

    Raises ValueError if no draft exists.
    """
    # Supersede existing confirmed profiles
    result = await session.execute(
        select(CreatorDna).where(
            CreatorDna.creator_id == creator_id,
            CreatorDna.status == DnaStatus.confirmed,
        )
    )
    for old in result.scalars():
        old.status = DnaStatus.superseded

    # Promote latest draft
    result = await session.execute(
        select(CreatorDna)
        .where(
            CreatorDna.creator_id == creator_id,
            CreatorDna.status == DnaStatus.draft,
        )
        .order_by(CreatorDna.version.desc())
    )
    draft = result.scalars().first()
    if not draft:
        raise ValueError(f"No draft DNA profile to confirm for creator {creator_id}")
    draft.status = DnaStatus.confirmed

    creator = await session.get(Creator, creator_id)
    if creator and creator.onboarding_state == OnboardingState.dna_pending:
        creator.onboarding_state = OnboardingState.active

    await session.commit()
    await session.refresh(draft)
    logger.info("DNA v%d confirmed for creator %s", draft.version, creator_id)
    return draft


async def get_active(session: AsyncSession, creator_id: uuid.UUID) -> CreatorDna | None:
    """Return the confirmed profile; fall back to latest draft if none confirmed."""
    result = await session.execute(
        select(CreatorDna)
        .where(
            CreatorDna.creator_id == creator_id,
            CreatorDna.status.in_([DnaStatus.confirmed, DnaStatus.draft]),
        )
        .order_by(CreatorDna.version.desc())
    )
    rows = list(result.scalars())
    confirmed = next((r for r in rows if r.status == DnaStatus.confirmed), None)
    return confirmed or (rows[0] if rows else None)


async def get_version(
    session: AsyncSession, creator_id: uuid.UUID, version: int
) -> CreatorDna | None:
    """Fetch a specific DNA version for a creator."""
    result = await session.execute(
        select(CreatorDna).where(
            CreatorDna.creator_id == creator_id,
            CreatorDna.version == version,
        )
    )
    return result.scalars().first()
