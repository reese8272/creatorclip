"""
Minute balance management.

All mutations use an atomic UPDATE … WHERE … RETURNING pattern so concurrent
Celery tasks cannot double-spend or over-grant without a DB-level race.

Deductions are additionally guarded by the MinuteDeduction ledger's UNIQUE(video_id)
constraint — Celery's at-least-once delivery (task_acks_late=True) can re-invoke an
ingest task; the constraint prevents a second deduction from inserting (Issue 34).
"""

import logging
import math
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models import Creator, MinuteDeduction, MinutePack

logger = logging.getLogger(__name__)


def video_minutes(duration_s: float) -> int:
    """Round a video duration up to the nearest whole minute (minimum 1)."""
    return max(1, math.ceil(duration_s / 60))


async def get_balance(creator_id: uuid.UUID, session: AsyncSession) -> int:
    balance = await session.scalar(select(Creator.minutes_balance).where(Creator.id == creator_id))
    if balance is None:
        raise HTTPException(status_code=404, detail="Creator not found")
    return balance


async def grant_minutes(
    creator_id: uuid.UUID,
    minutes: int,
    reason: str,
    session: AsyncSession,
    *,
    pack_id: str = "grant",
    stripe_session_id: str | None = None,
    price_cents: int = 0,
) -> None:
    """Add minutes to a creator's balance and record the grant."""
    session.add(
        MinutePack(
            creator_id=creator_id,
            pack_id=pack_id,
            minutes_granted=minutes,
            price_cents=price_cents,
            stripe_session_id=stripe_session_id,
            reason=reason,
            granted_at=datetime.now(UTC),
        )
    )
    await session.execute(
        update(Creator)
        .where(Creator.id == creator_id)
        .values(minutes_balance=Creator.minutes_balance + minutes)
    )
    logger.info("billing grant creator=%s minutes=%d reason=%s", creator_id, minutes, reason)


async def deduct_for_video(
    video_id: uuid.UUID,
    creator_id: uuid.UUID,
    duration_s: float,
    session: AsyncSession,
) -> int:
    """Idempotently deduct minutes for a video.

    The UNIQUE(video_id) constraint on MinuteDeduction is the idempotency key.
    Returns minutes deducted on the first call; 0 on every subsequent call for the
    same video_id (whether sequential or concurrent).

    Raises HTTPException(402) if the balance is insufficient — the deduction
    record is rolled back via SAVEPOINT, so a 402 leaves the ledger clean.

    Both the deduction-record INSERT and the balance UPDATE occur inside a
    SAVEPOINT (session.begin_nested) — either both succeed or neither does.
    Caller is responsible for committing the outer transaction.
    """
    minutes = video_minutes(duration_s)

    # Fast-path: already charged? Skip without opening a SAVEPOINT.
    existing = await session.scalar(
        select(MinuteDeduction.id).where(MinuteDeduction.video_id == video_id)
    )
    if existing is not None:
        return 0

    try:
        async with session.begin_nested():
            session.add(
                MinuteDeduction(
                    video_id=video_id,
                    creator_id=creator_id,
                    minutes_deducted=minutes,
                    duration_s=duration_s,
                )
            )
            await session.flush()  # forces the INSERT — surfaces UNIQUE conflicts now

            result = await session.execute(
                update(Creator)
                .where(Creator.id == creator_id, Creator.minutes_balance >= minutes)
                .values(minutes_balance=Creator.minutes_balance - minutes)
                .returning(Creator.minutes_balance)
            )
            row = result.fetchone()
            if row is None:
                # Insufficient balance — SAVEPOINT rolls back on exception, undoing the INSERT.
                raise HTTPException(
                    status_code=402,
                    detail=(
                        "Insufficient minutes balance. "
                        "Purchase a pack at /pricing to continue processing."
                    ),
                )
    except IntegrityError:
        # Concurrent retry won the UNIQUE(video_id) race — idempotent skip.
        return 0

    logger.info(
        "billing deduct video=%s creator=%s minutes=%d remaining=%d",
        video_id,
        creator_id,
        minutes,
        row[0],
    )
    return minutes


async def check_positive_balance(creator_id: uuid.UUID, session: AsyncSession) -> None:
    """Pre-flight guard: raises 402 if the creator has no minutes remaining."""
    balance = await get_balance(creator_id, session)
    if balance <= 0:
        raise HTTPException(
            status_code=402,
            detail=("No minutes remaining. Purchase a pack at /pricing to process videos."),
        )
