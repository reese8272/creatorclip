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
    """Idempotently add minutes to a creator's balance and record the grant.

    Money-credit idempotency mirrors deduct_for_video (Issue 64): when
    ``stripe_session_id`` is set it is the idempotency key (UNIQUE on MinutePack).
    Stripe delivers ``checkout.session.completed`` at-least-once and can deliver it
    concurrently; a fast-path check plus a SAVEPOINT with IntegrityError-catch makes
    a duplicate delivery a clean no-op instead of an uncaught 500. Non-keyed grants
    (free trial, manual; stripe_session_id=None) are one-shot and unaffected.

    Both the MinutePack INSERT and the balance UPDATE occur inside a SAVEPOINT —
    either both land or neither does. Caller commits the outer transaction.
    """
    # Fast-path: already granted for this Stripe session? Skip without a SAVEPOINT.
    if stripe_session_id is not None:
        existing = await session.scalar(
            select(MinutePack.id).where(MinutePack.stripe_session_id == stripe_session_id)
        )
        if existing is not None:
            logger.info("billing grant skip (already granted) session=%s", stripe_session_id)
            return

    try:
        async with session.begin_nested():
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
            await session.flush()  # forces the INSERT — surfaces UNIQUE conflicts now
            await session.execute(
                update(Creator)
                .where(Creator.id == creator_id)
                .values(minutes_balance=Creator.minutes_balance + minutes)
            )
    except IntegrityError:
        if stripe_session_id is None:
            # Non-keyed grant (free trial / manual): there is no UNIQUE to race on, so
            # an IntegrityError here is a *real* fault (e.g. the creator row is gone),
            # not a duplicate delivery. Don't swallow it — that would silently drop a
            # legitimate grant (a new beta user getting 0 trial minutes). (Issue 76)
            raise
        # Concurrent duplicate delivery won the UNIQUE(stripe_session_id) race — no-op.
        logger.info("billing grant race skip session=%s", stripe_session_id)
        return

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


async def _trial_expired(creator_id: uuid.UUID, session: AsyncSession) -> bool:
    """Issue 126 — true iff the creator has a `trial_ends_at` that's already
    in the past. NULL `trial_ends_at` (legacy creators) is treated as "no
    trial" — they were never on one, so the 402 falls back to the generic
    "purchase a pack" copy."""
    row = await session.scalar(select(Creator.trial_ends_at).where(Creator.id == creator_id))
    if row is None:
        return False
    if row.tzinfo is None:
        row = row.replace(tzinfo=UTC)
    return row < datetime.now(UTC)


def _trial_ended_402_detail() -> str:
    """Standalone so the structural test in test_issue_126.py can pin the copy
    without importing the route handlers."""
    return "Your free trial has ended. Add minutes at /pricing to continue."


async def check_positive_balance(creator_id: uuid.UUID, session: AsyncSession) -> None:
    """Pre-flight guard: raises 402 if the creator has no minutes remaining.

    Used where the operation does not deduct minutes itself (e.g. /clips/render)
    — the gate is a usage floor, not a per-call cost check. For deducting paths
    (e.g. /videos/upload) use ``check_balance_for_minutes`` instead.

    Issue 126: differentiated copy when the trial expired AND balance hit zero,
    so the next action ("buy a pack") is unambiguous instead of generic.
    """
    balance = await get_balance(creator_id, session)
    if balance <= 0:
        if await _trial_expired(creator_id, session):
            detail = _trial_ended_402_detail()
        else:
            detail = "No minutes remaining. Purchase a pack at /pricing to process videos."
        raise HTTPException(status_code=402, detail=detail)


async def check_balance_for_minutes(
    creator_id: uuid.UUID, minutes_needed: int, session: AsyncSession
) -> None:
    """Pre-flight guard: raises 402 if the creator can't cover *minutes_needed*.

    Mirrors the predicate ``deduct_for_video`` enforces internally
    (``balance >= minutes``) — see ``billing/ledger.py:144``. Without this
    pre-check the upload path silently 402s inside the Celery task, leaving the
    user with a "failed" video and no actionable message. (Issue 89 — SEV-1
    spawned by Issue 88's display-vs-filter audit.)

    The user-facing 402 surfaces the concrete gap (needed vs. available) so the
    response copy is actionable rather than generic "Insufficient balance".
    Issue 126: when balance has hit zero AND the trial has expired, override
    with the trial-ended copy so the user knows trial is the reason, not
    mid-use exhaustion.
    """
    balance = await get_balance(creator_id, session)
    if balance < minutes_needed:
        if balance <= 0 and await _trial_expired(creator_id, session):
            detail = _trial_ended_402_detail()
        else:
            detail = (
                f"This video needs {minutes_needed} minutes; you have {balance}. "
                f"Purchase a pack at /pricing to continue."
            )
        raise HTTPException(status_code=402, detail=detail)
