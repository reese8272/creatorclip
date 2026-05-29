"""
Integration test for Issue 70 — poll_clip_outcomes no longer re-polls forever.

A 7d-checkpoint poll marks the outcome `final`; a `final` outcome is never polled
again (bounding the YouTube-quota drain). Marked `integration` (excluded from the
default run). Requires Alembic revision `a7b8c9d0e1f2`.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from models import Clip, ClipOutcome, Creator, OnboardingState, RenderStatus, Video, VideoKind
from worker.tasks import _poll_clip_outcomes_async

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed_outcome(session: AsyncSession, *, fetched_at: datetime, final: bool) -> tuple:
    creator = Creator(
        google_sub=f"test_poll_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_poll_{uuid.uuid4().hex[:6]}",
        channel_title="Poll Test",
        onboarding_state=OnboardingState.active,
    )
    session.add(creator)
    await session.flush()
    video = Video(
        creator_id=creator.id,
        youtube_video_id=f"yt_{uuid.uuid4().hex[:8]}",
        title="v",
        kind=VideoKind.long,
    )
    session.add(video)
    await session.flush()
    clip = Clip(
        video_id=video.id,
        creator_id=creator.id,
        setup_start_s=1.0,
        start_s=1.0,
        end_s=60.0,
        render_status=RenderStatus.done,
    )
    session.add(clip)
    await session.flush()
    session.add(
        ClipOutcome(
            clip_id=clip.id,
            published_youtube_id="ytClip00001",
            performed_well=False,  # 48h poll already happened
            fetched_at=fetched_at,
            final=final,
        )
    )
    await session.commit()
    return creator, clip


@pytest.mark.asyncio
async def test_7d_poll_marks_final_and_finalized_is_skipped(db_session: AsyncSession, mocker):
    now = datetime.now(UTC)
    # A non-final outcome last fetched 8 days ago → qualifies via the 7d branch.
    creator_a, clip_a = await _seed_outcome(
        db_session, fetched_at=now - timedelta(days=8), final=False
    )
    # An already-final outcome → must be excluded from the query entirely.
    creator_b, clip_b = await _seed_outcome(
        db_session, fetched_at=now - timedelta(days=8), final=True
    )

    mocker.patch("youtube.oauth.get_valid_access_token", new=AsyncMock(return_value="tok"))
    stats = mocker.patch(
        "youtube.data_api.get_video_stats", new=AsyncMock(return_value={"views": 123})
    )

    try:
        await _poll_clip_outcomes_async()

        # The non-final outcome was polled once and is now terminal.
        a = await db_session.get(ClipOutcome, clip_a.id)
        await db_session.refresh(a)
        assert a.final is True
        assert a.views == 123
        # Stats fetched exactly once — the finalized outcome was not polled.
        assert stats.await_count == 1
    finally:
        await db_session.execute(
            delete(Clip).where(Clip.creator_id.in_([creator_a.id, creator_b.id]))
        )
        await db_session.commit()
