"""
Integration tests for Issue 63 — DNA build idempotency + single-confirmed invariant.

Marked `integration` (excluded from the default run — see pytest.ini).
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from dna.profile import confirm_draft, create_draft
from models import Creator, CreatorDna, DnaStatus, OnboardingState

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
        google_sub=f"test_dna_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_dna_{uuid.uuid4().hex[:6]}",
        channel_title="DNA Test",
        onboarding_state=OnboardingState.dna_pending,
    )
    session.add(creator)
    await session.commit()
    return creator


@pytest.mark.asyncio
async def test_build_dna_redelivery_is_noop(db_session: AsyncSession):
    """Same Celery job_id twice → one draft, and the second run does NOT re-spend."""
    from worker.tasks import _build_dna_async

    creator = await _seed_creator(db_session)
    job_id = f"task_{uuid.uuid4().hex}"

    patterns = {"dummy": True}
    with (
        patch(
            "dna.builder.build_patterns",
            new=AsyncMock(return_value=(patterns, [], [], None, None, None)),
        ),
        patch("dna.brief.generate_brief", return_value="brief") as mock_brief,
        patch("dna.embeddings.embed_patterns", new=AsyncMock()),
        patch("dna.embeddings.embed_brief", new=AsyncMock()),
    ):
        await _build_dna_async(str(creator.id), job_id)
        await _build_dna_async(str(creator.id), job_id)  # redelivery

    try:
        n = await db_session.scalar(
            select(func.count()).select_from(CreatorDna).where(CreatorDna.creator_id == creator.id)
        )
        assert n == 1  # exactly one draft despite two runs
        assert mock_brief.call_count == 1  # no duplicate paid LLM spend on redelivery
    finally:
        await db_session.execute(delete(CreatorDna).where(CreatorDna.creator_id == creator.id))
        await db_session.execute(delete(Creator).where(Creator.id == creator.id))
        await db_session.commit()


@pytest.mark.asyncio
async def test_confirm_draft_keeps_single_confirmed(db_session: AsyncSession):
    creator = await _seed_creator(db_session)
    try:
        await create_draft(
            db_session,
            creator_id=creator.id,
            patterns={},
            top_video_ids=[],
            bottom_video_ids=[],
            brief_text="v1",
        )
        first = await confirm_draft(db_session, creator.id)
        assert first.version == 1

        # Double-confirm with no newer draft → idempotent no-op.
        again = await confirm_draft(db_session, creator.id)
        assert again.id == first.id

        # A new draft + confirm supersedes the old one — still exactly one confirmed.
        await create_draft(
            db_session,
            creator_id=creator.id,
            patterns={},
            top_video_ids=[],
            bottom_video_ids=[],
            brief_text="v2",
        )
        await confirm_draft(db_session, creator.id)

        confirmed = await db_session.scalar(
            select(func.count())
            .select_from(CreatorDna)
            .where(CreatorDna.creator_id == creator.id, CreatorDna.status == DnaStatus.confirmed)
        )
        assert confirmed == 1
    finally:
        await db_session.execute(delete(CreatorDna).where(CreatorDna.creator_id == creator.id))
        await db_session.execute(delete(Creator).where(Creator.id == creator.id))
        await db_session.commit()
