"""
Integration test for Issue 64 — grant_minutes money-credit idempotency.

Stripe delivers checkout.session.completed at-least-once and can deliver it
concurrently. grant_minutes must credit a creator exactly once per
stripe_session_id, even when two deliveries race. Mirrors the deduct_for_video
concurrency test.

Marked `integration` (excluded from the default run — see pytest.ini).
"""

import asyncio
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from billing.ledger import grant_minutes
from config import settings
from models import Creator, MinutePack, OnboardingState

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed_creator(session: AsyncSession) -> Creator:
    creator = Creator(
        google_sub=f"test_grant_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_grant_{uuid.uuid4().hex[:6]}",
        channel_title="Grant Test",
        onboarding_state=OnboardingState.active,
        minutes_balance=0,
    )
    session.add(creator)
    await session.commit()
    return creator


@pytest.mark.asyncio
async def test_concurrent_grants_credit_once(db_session: AsyncSession):
    creator = await _seed_creator(db_session)
    sid = f"cs_test_{uuid.uuid4().hex}"
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _attempt() -> None:
        async with sf() as session:
            await grant_minutes(
                creator_id=creator.id,
                minutes=100,
                reason="purchase",
                session=session,
                pack_id="pack_100",
                stripe_session_id=sid,
                price_cents=999,
            )
            await session.commit()

    try:
        await asyncio.gather(_attempt(), _attempt())  # two concurrent deliveries

        async with sf() as fresh:
            balance = await fresh.scalar(
                select(Creator.minutes_balance).where(Creator.id == creator.id)
            )
            packs = await fresh.scalar(
                select(func.count())
                .select_from(MinutePack)
                .where(MinutePack.stripe_session_id == sid)
            )
        assert balance == 100  # credited exactly once, not 200
        assert packs == 1  # exactly one MinutePack row
    finally:
        await engine.dispose()
        await db_session.execute(delete(MinutePack).where(MinutePack.creator_id == creator.id))
        await db_session.execute(delete(Creator).where(Creator.id == creator.id))
        await db_session.commit()
