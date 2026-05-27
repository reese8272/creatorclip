"""
Minute balance management.

All mutations use an atomic UPDATE … WHERE … RETURNING pattern so concurrent
Celery tasks cannot double-spend or over-grant without a DB-level race.
"""

import logging
import math
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models import Creator, MinutePack

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


async def deduct_minutes(
    creator_id: uuid.UUID,
    duration_s: float,
    session: AsyncSession,
) -> int:
    """
    Atomically deduct minutes for a processed video.

    Raises HTTP 402 if the balance is insufficient. Returns the number of
    minutes deducted.
    """
    minutes = video_minutes(duration_s)
    result = await session.execute(
        update(Creator)
        .where(Creator.id == creator_id, Creator.minutes_balance >= minutes)
        .values(minutes_balance=Creator.minutes_balance - minutes)
        .returning(Creator.minutes_balance)
    )
    row = result.fetchone()
    if row is None:
        raise HTTPException(
            status_code=402,
            detail=(
                "Insufficient minutes balance. Purchase a pack at /pricing to continue processing."
            ),
        )
    logger.info("billing deduct creator=%s minutes=%d remaining=%d", creator_id, minutes, row[0])
    return minutes


async def check_positive_balance(creator_id: uuid.UUID, session: AsyncSession) -> None:
    """Pre-flight guard: raises 402 if the creator has no minutes remaining."""
    balance = await get_balance(creator_id, session)
    if balance <= 0:
        raise HTTPException(
            status_code=402,
            detail=("No minutes remaining. Purchase a pack at /pricing to process videos."),
        )
