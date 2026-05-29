"""
CRUD for the creator_dna table.

DNA profiles are versioned and never deleted:  draft → confirmed → superseded.
Only one row per creator can be in 'confirmed' state at a time.
"""

import logging
import uuid

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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
    build_job_id: str | None = None,
    *,
    commit: bool = True,
) -> CreatorDna:
    """Create a new draft DNA profile at version max+1.

    Args:
        session: Active async database session.
        creator_id: UUID of the owning creator.
        patterns: DNA pattern dict from the builder.
        top_video_ids: IDs of top-performing videos.
        bottom_video_ids: IDs of bottom-performing videos.
        brief_text: Plain-language creator brief text.
        optimal_clip_len_s: Derived optimal clip length, if available.
        best_source_region: Derived best source region, if available.
        optimal_upload_gap_h: Derived optimal upload gap, if available.
        commit: When True (default), commit immediately after adding the row.
            Pass ``commit=False`` when the caller manages the transaction boundary
            and will commit after additional writes (e.g. embeddings) so that the
            draft INSERT and embedding INSERTs form a single atomic unit.
    """
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
        build_job_id=build_job_id,
        status=DnaStatus.draft,
    )
    session.add(dna)
    if commit:
        await session.commit()
        await session.refresh(dna)
        logger.info("DNA draft v%d created for creator %s", dna.version, creator_id)
    return dna


async def confirm_draft(session: AsyncSession, creator_id: uuid.UUID) -> CreatorDna:
    """
    Confirm the latest draft, superseding any previously confirmed profile.
    Advances creator onboarding_state from dna_pending → active.

    Idempotent + concurrency-safe (Issue 63): the creator's DNA rows are locked
    `FOR UPDATE` so two concurrent confirmations serialize; the
    `uq_one_confirmed_dna_per_creator` partial unique index is the DB-level backstop.
    A double-confirm (no newer draft, already confirmed) is a no-op returning the
    confirmed row.

    Raises ValueError only if there is neither a draft to confirm nor an existing
    confirmed profile.
    """
    rows = (
        (
            await session.execute(
                select(CreatorDna)
                .where(CreatorDna.creator_id == creator_id)
                .order_by(CreatorDna.version.desc())
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    latest_draft = next((r for r in rows if r.status == DnaStatus.draft), None)
    confirmed = next((r for r in rows if r.status == DnaStatus.confirmed), None)

    if latest_draft is None:
        if confirmed is not None:
            return confirmed  # already confirmed — idempotent no-op
        raise ValueError(f"No draft DNA profile to confirm for creator {creator_id}")

    # Supersede the current confirmed BEFORE promoting — the partial unique index is
    # non-deferrable, so there must never be two 'confirmed' rows even transiently.
    if confirmed is not None:
        confirmed.status = DnaStatus.superseded
        await session.flush()
    latest_draft.status = DnaStatus.confirmed

    creator = await session.get(Creator, creator_id)
    if creator and creator.onboarding_state == OnboardingState.dna_pending:
        creator.onboarding_state = OnboardingState.active

    try:
        await session.commit()
    except IntegrityError:
        # A concurrent confirm won the partial-unique race — return its confirmed row.
        await session.rollback()
        existing = (
            (
                await session.execute(
                    select(CreatorDna).where(
                        CreatorDna.creator_id == creator_id,
                        CreatorDna.status == DnaStatus.confirmed,
                    )
                )
            )
            .scalars()
            .first()
        )
        if existing is None:
            raise
        return existing

    await session.refresh(latest_draft)
    logger.info("DNA v%d confirmed for creator %s", latest_draft.version, creator_id)
    return latest_draft


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
