"""
Integration tests for Issue 57 — automatic refund on terminal ingest failure.

End-to-end against a real Postgres:

- A creator with a MinuteDeduction for a failed video gets refunded → net
  balance change = 0 → exactly 1 compensating MinutePack row with
  reason='refund' and pack_id='refund:<video_id>'.
- A duplicate `on_failure` invocation does NOT double-refund.
- A failure before the deduct step (no MinuteDeduction row) is a clean no-op.

Marked `integration` so it only runs against a live Postgres.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from billing.ledger import deduct_for_video
from billing.refund import refund_for_video
from config import settings
from models import (
    Creator,
    IngestStatus,
    MinuteDeduction,
    MinutePack,
    OnboardingState,
    Video,
    VideoKind,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(
    session: AsyncSession, *, balance: int, duration_s: float = 600.0
) -> tuple[Creator, Video]:
    creator = Creator(
        google_sub=f"test_refund_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_refund_{uuid.uuid4().hex[:6]}",
        channel_title="Refund Test Channel",
        onboarding_state=OnboardingState.active,
        minutes_balance=balance,
    )
    session.add(creator)
    await session.flush()
    video = Video(
        creator_id=creator.id,
        youtube_video_id=f"yt_{uuid.uuid4().hex[:8]}",
        kind=VideoKind.long,
        ingest_status=IngestStatus.failed,
        duration_s=duration_s,
    )
    session.add(video)
    await session.commit()
    return creator, video


async def _cleanup(session: AsyncSession, creator_id: uuid.UUID) -> None:
    # MinutePack does NOT cascade from creator delete in the model — clear explicitly.
    await session.execute(delete(MinutePack).where(MinutePack.creator_id == creator_id))
    await session.execute(delete(Creator).where(Creator.id == creator_id))
    await session.commit()


async def _balance(session: AsyncSession, creator_id: uuid.UUID) -> int:
    return await session.scalar(select(Creator.minutes_balance).where(Creator.id == creator_id))


async def _refund_packs(session: AsyncSession, video_id: uuid.UUID) -> list[MinutePack]:
    result = await session.execute(
        select(MinutePack).where(MinutePack.pack_id == f"refund:{video_id}")
    )
    return list(result.scalars())


@pytest.mark.asyncio
async def test_refund_for_video_compensates_deduction(db_session: AsyncSession):
    """Deduct → refund → net balance change = 0; 1 deduction row + 1 refund pack."""
    creator, video = await _seed(db_session, balance=100, duration_s=600.0)
    try:
        # Deduct: 600s → 10 minutes
        deducted = await deduct_for_video(video.id, creator.id, 600.0, db_session)
        await db_session.commit()
        assert deducted == 10
        assert await _balance(db_session, creator.id) == 90

        # Refund. Uses its own session internally via db.AsyncSessionLocal.
        refunded = await refund_for_video(video.id)
        assert refunded == 10

        # Re-read in a fresh session.
        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            balance = await _balance(s, creator.id)
            deductions = await s.scalar(
                select(func.count(MinuteDeduction.id)).where(MinuteDeduction.video_id == video.id)
            )
            refund_packs = await _refund_packs(s, video.id)
        await engine.dispose()

        assert balance == 100, "net balance change must be zero after refund"
        assert deductions == 1, "deduction row is immutable — must NOT be deleted"
        assert len(refund_packs) == 1
        assert refund_packs[0].reason == "refund"
        assert refund_packs[0].minutes_granted == 10
        assert refund_packs[0].price_cents == 0
    finally:
        await _cleanup(db_session, creator.id)


@pytest.mark.asyncio
async def test_refund_for_video_is_idempotent(db_session: AsyncSession):
    """Two refund calls for the same video → exactly 1 refund pack, balance unchanged."""
    creator, video = await _seed(db_session, balance=100, duration_s=600.0)
    try:
        await deduct_for_video(video.id, creator.id, 600.0, db_session)
        await db_session.commit()

        first = await refund_for_video(video.id)
        second = await refund_for_video(video.id)

        assert first == 10
        assert second == 0  # no-op

        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            refund_packs = await _refund_packs(s, video.id)
            balance = await _balance(s, creator.id)
        await engine.dispose()

        assert len(refund_packs) == 1, "duplicate on_failure must not double-refund"
        assert balance == 100
    finally:
        await _cleanup(db_session, creator.id)


@pytest.mark.asyncio
async def test_refund_for_video_noop_when_no_deduction(db_session: AsyncSession):
    """Failure before the deduct step → no deduction row → refund is a clean no-op."""
    creator, video = await _seed(db_session, balance=100)
    try:
        # No deduct_for_video call — simulating failure pre-deduction.
        refunded = await refund_for_video(video.id)
        assert refunded == 0

        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            refund_packs = await _refund_packs(s, video.id)
            balance = await _balance(s, creator.id)
        await engine.dispose()

        assert refund_packs == [], "no spurious refund row when nothing was deducted"
        assert balance == 100, "balance unchanged"
    finally:
        await _cleanup(db_session, creator.id)


# ── Wave-4 Fix 2: concurrent refund race closed by partial UNIQUE ────────────


@pytest.mark.asyncio
async def test_refund_for_video_concurrent_race_no_double_credit(db_session: AsyncSession):
    """Wave-4 Fix 2: two concurrent refund_for_video calls for the same video
    MUST result in exactly one MinutePack refund row + net balance change = 0.

    Before migration 0013's partial UNIQUE on (pack_id) WHERE reason='refund',
    the read-then-write idempotency guard had a TOCTOU window: both calls
    could SELECT no existing refund, both INSERT, both commit → double-credit.
    Celery's task_acks_late=True + worker preemption made this reachable for
    real (Issue 57's docstring acknowledged the race; this test pins it
    closed at the DB level).
    """
    import asyncio

    creator, video = await _seed(db_session, balance=100, duration_s=600.0)
    try:
        # Deduct: 600s → 10 minutes, leaving 90 balance.
        await deduct_for_video(video.id, creator.id, 600.0, db_session)
        await db_session.commit()

        # Fire two concurrent refund attempts. With the partial UNIQUE in place,
        # one wins and inserts the refund row; the other loses the UNIQUE race,
        # catches IntegrityError, and returns 0.
        results = await asyncio.gather(
            refund_for_video(video.id),
            refund_for_video(video.id),
            return_exceptions=False,
        )

        # Exactly one call returned 10 (the winner); the other returned 0.
        assert sorted(results) == [0, 10], (
            f"Wave-4 Fix 2: exactly one of two concurrent refund calls must "
            f"succeed (return 10); the other must lose the race (return 0). "
            f"Got {results}."
        )

        # Re-read in a fresh session to verify the on-disk state.
        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            refund_packs = await _refund_packs(s, video.id)
            balance = await _balance(s, creator.id)
        await engine.dispose()

        # The load-bearing assertion: exactly ONE refund row, NOT two.
        assert len(refund_packs) == 1, (
            f"Wave-4 Fix 2: concurrent refund race must NOT create duplicate "
            f"MinutePack rows. The partial UNIQUE index "
            f"uq_minute_packs_refund_pack_id should catch the second INSERT. "
            f"Got {len(refund_packs)} rows."
        )
        # And exactly one refund's worth of minutes credited (10), not two (20).
        assert balance == 100, (
            f"Net balance change must equal exactly one refund (back to 100). "
            f"Got {balance} — would be 110 if both refunds succeeded."
        )
    finally:
        await _cleanup(db_session, creator.id)
