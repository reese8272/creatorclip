"""
Integration tests for per-video minute deduction idempotency — Issue 34 (SEV-0).

Verifies that `billing.ledger.deduct_for_video` charges a creator exactly once per
video, even under Celery at-least-once retry and concurrent invocation. The prior
`deduct_minutes` had no per-video key and could charge 2–4× on transient ingest
failures.

Requires a running Postgres + Alembic schema (see docker-compose.yml + integration.yml).
"""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from billing.ledger import deduct_for_video
from config import settings
from models import (
    Creator,
    IngestStatus,
    MinuteDeduction,
    OnboardingState,
    Video,
    VideoKind,
)


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


async def _seed_creator_with_video(
    session: AsyncSession,
    *,
    minutes_balance: int,
    duration_s: float = 600.0,
) -> tuple[Creator, Video]:
    creator = Creator(
        google_sub=f"test_dedup_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_dedup_{uuid.uuid4().hex[:6]}",
        channel_title="Test Channel",
        onboarding_state=OnboardingState.active,
        minutes_balance=minutes_balance,
    )
    session.add(creator)
    await session.flush()
    video = Video(
        creator_id=creator.id,
        youtube_video_id=f"vid_{uuid.uuid4().hex[:8]}",
        kind=VideoKind.long,
        ingest_status=IngestStatus.running,
        duration_s=duration_s,
    )
    session.add(video)
    await session.flush()
    await session.commit()
    return creator, video


async def _cleanup(session: AsyncSession, creator_id: uuid.UUID) -> None:
    # CASCADE on creator → video → minute_deduction handles the rest
    await session.execute(delete(Creator).where(Creator.id == creator_id))
    await session.commit()


async def _balance(session: AsyncSession, creator_id: uuid.UUID) -> int:
    return await session.scalar(select(Creator.minutes_balance).where(Creator.id == creator_id))


async def _deduction_count(session: AsyncSession, video_id: uuid.UUID) -> int:
    return await session.scalar(
        select(func.count(MinuteDeduction.id)).where(MinuteDeduction.video_id == video_id)
    )


@pytest.mark.integration
async def test_deduct_for_video_is_idempotent_on_sequential_retry(db_session: AsyncSession):
    """First call deducts; every subsequent call for same video_id is a no-op."""
    creator, video = await _seed_creator_with_video(db_session, minutes_balance=100)
    try:
        # First call: 600s = 10 minutes deducted
        n1 = await deduct_for_video(video.id, creator.id, 600.0, db_session)
        await db_session.commit()
        assert n1 == 10
        assert await _balance(db_session, creator.id) == 90

        # Second call: same video → idempotent skip
        n2 = await deduct_for_video(video.id, creator.id, 600.0, db_session)
        await db_session.commit()
        assert n2 == 0
        assert await _balance(db_session, creator.id) == 90  # unchanged

        # Third call (a third Celery retry would do this): still idempotent
        n3 = await deduct_for_video(video.id, creator.id, 600.0, db_session)
        await db_session.commit()
        assert n3 == 0
        assert await _balance(db_session, creator.id) == 90

        # Exactly one ledger row exists for this video
        assert await _deduction_count(db_session, video.id) == 1
    finally:
        await _cleanup(db_session, creator.id)


@pytest.mark.integration
async def test_deduct_for_video_concurrent_calls_charge_once(db_session: AsyncSession):
    """Two coroutines racing on the same video_id → exactly one deduction lands."""
    creator, video = await _seed_creator_with_video(db_session, minutes_balance=100)

    async def _attempt(session_factory, video_id, creator_id) -> int:
        # Each task uses its own session — simulates two Celery workers.
        async with session_factory() as session:
            n = await deduct_for_video(video_id, creator_id, 600.0, session)
            await session.commit()
            return n

    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        results = await asyncio.gather(
            _attempt(sf, video.id, creator.id),
            _attempt(sf, video.id, creator.id),
        )
        # Exactly one of them deducted; the other returned 0
        assert sorted(results) == [0, 10]

        # Re-read balance from a fresh session so we see the committed state
        async with sf() as fresh:
            assert await _balance(fresh, creator.id) == 90
            assert await _deduction_count(fresh, video.id) == 1
    finally:
        await engine.dispose()
        await _cleanup(db_session, creator.id)


@pytest.mark.integration
async def test_deduct_for_video_402_on_insufficient_balance_leaves_ledger_clean(
    db_session: AsyncSession,
):
    """402 path rolls back the SAVEPOINT — no orphan MinuteDeduction row."""
    creator, video = await _seed_creator_with_video(db_session, minutes_balance=5)

    try:
        with pytest.raises(HTTPException) as exc_info:
            # 600s = 10 minutes; creator has only 5
            await deduct_for_video(video.id, creator.id, 600.0, db_session)
        await db_session.commit()

        assert exc_info.value.status_code == 402
        # Balance untouched
        assert await _balance(db_session, creator.id) == 5
        # SAVEPOINT rolled back: no ledger row
        assert await _deduction_count(db_session, video.id) == 0
    finally:
        await _cleanup(db_session, creator.id)


@pytest.mark.integration
async def test_deduct_for_video_ledger_row_carries_audit_fields(db_session: AsyncSession):
    """Deduction record stores minutes, duration, and timestamp for billing audit."""
    creator, video = await _seed_creator_with_video(db_session, minutes_balance=100)
    before = datetime.now(UTC)

    try:
        await deduct_for_video(video.id, creator.id, 145.0, db_session)
        await db_session.commit()

        row = await db_session.scalar(
            select(MinuteDeduction).where(MinuteDeduction.video_id == video.id)
        )
        assert row is not None
        assert row.minutes_deducted == 3  # ceil(145/60) = 3
        assert row.duration_s == 145.0
        assert row.creator_id == creator.id
        assert row.deducted_at >= before
    finally:
        await _cleanup(db_session, creator.id)
