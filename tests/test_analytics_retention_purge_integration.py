"""
Integration test for Issue 75b — YouTube analytics retention purge against a real
Postgres. The YouTube API Services Developer Policies require stored Authorized Data
to be refreshed or deleted within 30 days, and deleted if authorization can't be
re-verified. `purge_stale_analytics` deletes metrics/retention/activity/demographics
for creators whose last successful refresh (authorization re-verification) is older
than ANALYTICS_RETENTION_DAYS; active creators are untouched.

Marked `integration` (default pytest run excludes it — see pytest.ini).
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from models import (
    AudienceActivity,
    Creator,
    Demographics,
    IngestStatus,
    OnboardingState,
    RetentionCurve,
    Video,
    VideoKind,
    VideoMetrics,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed_creator_with_analytics(
    session: AsyncSession, *, suffix: str, last_refreshed: datetime | None, created: datetime
) -> Creator:
    creator = Creator(
        google_sub=f"test_ret_{suffix}_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_ret_{suffix}",
        channel_title=f"Retention {suffix}",
        onboarding_state=OnboardingState.active,
        created_at=created,
        last_analytics_refreshed_at=last_refreshed,
    )
    session.add(creator)
    await session.flush()

    video = Video(
        creator_id=creator.id,
        youtube_video_id=f"vid_{suffix}_{uuid.uuid4().hex[:6]}"[:11],
        kind=VideoKind.long,
        ingest_status=IngestStatus.done,
        duration_s=600.0,
    )
    session.add(video)
    await session.flush()
    now = datetime.now(UTC)
    session.add(VideoMetrics(video_id=video.id, views=100, fetched_at=now))
    session.add(RetentionCurve(video_id=video.id, timestamp_s=0.0, audience_watch_ratio=1.0))
    session.add(
        AudienceActivity(
            creator_id=creator.id, day_of_week=1, hour=12, activity_index=0.5, fetched_at=now
        )
    )
    session.add(Demographics(creator_id=creator.id, payload_jsonb={"a": 1}, fetched_at=now))
    await session.commit()
    return creator


async def _counts(session: AsyncSession, creator: Creator) -> tuple[int, int, int, int]:
    vids = select(Video.id).where(Video.creator_id == creator.id)
    m = await session.scalar(
        select(func.count()).select_from(VideoMetrics).where(VideoMetrics.video_id.in_(vids))
    )
    r = await session.scalar(
        select(func.count()).select_from(RetentionCurve).where(RetentionCurve.video_id.in_(vids))
    )
    a = await session.scalar(
        select(func.count())
        .select_from(AudienceActivity)
        .where(AudienceActivity.creator_id == creator.id)
    )
    d = await session.scalar(
        select(func.count()).select_from(Demographics).where(Demographics.creator_id == creator.id)
    )
    return m, r, a, d


@pytest.mark.asyncio
async def test_purge_deletes_stale_keeps_fresh(db_session: AsyncSession):
    from worker.tasks import _purge_stale_analytics_async

    now = datetime.now(UTC)
    window = settings.ANALYTICS_RETENTION_DAYS
    # Stale: last refresh older than the window → can't re-verify → must be purged.
    stale = await _seed_creator_with_analytics(
        db_session,
        suffix="stale",
        last_refreshed=now - timedelta(days=window + 5),
        created=now - timedelta(days=window + 30),
    )
    # Fresh: refreshed yesterday → active, must be kept.
    fresh = await _seed_creator_with_analytics(
        db_session,
        suffix="fresh",
        last_refreshed=now - timedelta(days=1),
        created=now - timedelta(days=window + 30),
    )

    try:
        await _purge_stale_analytics_async()

        # Re-read in a clean session (the task committed in its own session).
        assert await _counts(db_session, stale) == (0, 0, 0, 0)
        assert await _counts(db_session, fresh) == (1, 1, 1, 1)
    finally:
        await db_session.execute(delete(Creator).where(Creator.id.in_([stale.id, fresh.id])))
        await db_session.commit()


@pytest.mark.asyncio
async def test_purge_keeps_new_creator_never_refreshed(db_session: AsyncSession):
    """A new creator (NULL last_refreshed, recent created_at) is protected via COALESCE."""
    from worker.tasks import _purge_stale_analytics_async

    now = datetime.now(UTC)
    new = await _seed_creator_with_analytics(
        db_session, suffix="new", last_refreshed=None, created=now - timedelta(days=1)
    )
    try:
        await _purge_stale_analytics_async()
        assert await _counts(db_session, new) == (1, 1, 1, 1)
    finally:
        await db_session.execute(delete(Creator).where(Creator.id == new.id))
        await db_session.commit()
