"""
Integration test for Issue 47 — analytics-refresh fairness under quota
exhaustion against a real Postgres.

Scenario:
  - 5 creators, none refreshed (last_analytics_refreshed_at IS NULL).
  - Per-cycle budget: 2 successful refreshes; the 3rd raises QuotaExhaustedError.
  - Run the refresh task 3 times in a row.
  - Assertion: every creator has refresh attempts honoured at least once across
    the 3 cycles. Without the ORDER BY fix, the same 2 creators would refresh
    every cycle and the other 3 would starve forever.

Marked `integration` — requires alembic revision `d4e5f6a7b8c9` applied to the
test DB (see pytest.ini).
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from models import Creator, OnboardingState
from youtube.quota import QuotaExhaustedError

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed_creators(session: AsyncSession, n: int) -> list[uuid.UUID]:
    ids: list[uuid.UUID] = []
    for i in range(n):
        c = Creator(
            google_sub=f"fairness_{uuid.uuid4().hex[:8]}",
            channel_id=f"UC_fair_{i}_{uuid.uuid4().hex[:4]}",
            channel_title=f"Fair Creator {i}",
            onboarding_state=OnboardingState.active,
        )
        session.add(c)
        await session.flush()
        ids.append(c.id)
    await session.commit()
    return ids


async def _cleanup(session: AsyncSession, ids: list[uuid.UUID]) -> None:
    await session.execute(delete(Creator).where(Creator.id.in_(ids)))
    await session.commit()


@pytest.mark.asyncio
async def test_quota_exhaustion_does_not_starve_creators(db_session):
    """5 creators, budget of 2 per cycle, 3 cycles → all 5 refreshed at least once."""
    from worker.tasks import _refresh_youtube_analytics_async

    ids = await _seed_creators(db_session, 5)

    # Per-creator refresh attempts seen by the (mocked) sync_audience_data call.
    attempts: list[uuid.UUID] = []

    cycle_state = {"calls": 0}

    async def fake_audience(_session, creator, _token):
        cycle_state["calls"] += 1
        if cycle_state["calls"] > 2:
            raise QuotaExhaustedError("daily cap hit")
        attempts.append(creator.id)

    try:
        for _ in range(3):
            cycle_state["calls"] = 0
            with (
                patch(
                    "youtube.oauth.get_valid_access_token",
                    new_callable=AsyncMock,
                    return_value="tok",
                ),
                patch("youtube.analytics.sync_video_analytics", new_callable=AsyncMock),
                patch(
                    "youtube.analytics.sync_audience_data",
                    new=fake_audience,
                ),
                patch("worker.tasks.remaining", new_callable=AsyncMock, return_value=5000),
            ):
                await _refresh_youtube_analytics_async()

        # Every seeded creator must have been refreshed at least once.
        attempted = set(attempts)
        seeded = set(ids)
        missed = seeded - attempted
        assert not missed, f"creators starved across 3 cycles: {missed}"

        # And the DB rows reflect the timestamp stamping.
        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            rows = (
                await s.execute(select(Creator).where(Creator.id.in_(ids)))
            ).scalars().all()
            stamped = {c.id for c in rows if c.last_analytics_refreshed_at is not None}
        await engine.dispose()

        assert seeded.issubset(stamped), f"creators never stamped: {seeded - stamped}"
    finally:
        await _cleanup(db_session, ids)
