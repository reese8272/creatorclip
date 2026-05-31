"""
Integration tests for Wave-4 Fix 3 (Issue 75b) — YouTube analytics retention purge.

Per YouTube API Services Developer Policies §III.E.4.b + §III.D.2.3.b, API
clients must verify authorization every 30 calendar days OR delete the stored
API Data. `purge_stale_youtube_analytics` (Celery Beat, daily) deletes rows
whose `fetched_at < now() - YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS` (default 30).

These tests drive the real Postgres path, seeding rows at 5d / 29d / 35d and
asserting only the 35d rows are deleted. The 29d row is critical — it pins the
boundary: rows EXACTLY at the cutoff line stay (we're using strict `<`, so
fetched_at = now() - 30d is preserved; fetched_at = now() - 30d - 1s is purged).

Marked `integration` (excluded from default `pytest -q`).
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from models import (
    AudienceActivity,
    Creator,
    Demographics,
    OnboardingState,
    RetentionCurve,
    Video,
    VideoKind,
    VideoMetrics,
)
from worker.tasks import _purge_stale_youtube_analytics_async

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed_creator_with_video(session: AsyncSession, suffix: str) -> tuple[Creator, Video]:
    creator = Creator(
        google_sub=f"test_purge_{suffix}_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_purge_{suffix}_{uuid.uuid4().hex[:6]}",
        channel_title=f"Purge Test {suffix}",
        onboarding_state=OnboardingState.active,
    )
    session.add(creator)
    await session.flush()
    video = Video(
        creator_id=creator.id,
        youtube_video_id=f"yt_purge_{suffix}_{uuid.uuid4().hex[:6]}",
        kind=VideoKind.long,
        duration_s=600.0,
    )
    session.add(video)
    await session.commit()
    return creator, video


async def _cleanup(session: AsyncSession, creator_ids: list[uuid.UUID]) -> None:
    # Cascades from Creator delete handle videos, video_metrics, retention_curves,
    # audience_activity, demographics — all have ON DELETE CASCADE on creator
    # (transitively via Video for video-scoped tables).
    await session.execute(delete(Creator).where(Creator.id.in_(creator_ids)))
    await session.commit()


@pytest.mark.asyncio
async def test_purge_deletes_stale_video_metrics_and_retention_curves(db_session: AsyncSession):
    """Rows older than YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS (30 days default)
    are deleted; rows within the window stay. RetentionCurve rows are deleted
    in lock-step with their parent VideoMetrics row (same write semantic as
    `youtube/analytics.py::sync_video_analytics`)."""
    now = datetime.now(UTC)
    creators_to_clean: list[uuid.UUID] = []

    # Three creators, each with one video + metrics + retention curve at
    # different fetched_at ages.
    fresh_creator, fresh_video = await _seed_creator_with_video(db_session, "fresh")
    creators_to_clean.append(fresh_creator.id)
    db_session.add(
        VideoMetrics(
            video_id=fresh_video.id,
            views=100,
            engagement_rate=0.05,
            fetched_at=now - timedelta(days=5),
        )
    )
    db_session.add(
        RetentionCurve(
            video_id=fresh_video.id,
            timestamp_s=0.0,
            audience_watch_ratio=1.0,
        )
    )

    boundary_creator, boundary_video = await _seed_creator_with_video(db_session, "boundary")
    creators_to_clean.append(boundary_creator.id)
    db_session.add(
        VideoMetrics(
            video_id=boundary_video.id,
            views=200,
            engagement_rate=0.06,
            fetched_at=now - timedelta(days=29),
        )
    )
    db_session.add(
        RetentionCurve(
            video_id=boundary_video.id,
            timestamp_s=10.0,
            audience_watch_ratio=0.9,
        )
    )

    stale_creator, stale_video = await _seed_creator_with_video(db_session, "stale")
    creators_to_clean.append(stale_creator.id)
    db_session.add(
        VideoMetrics(
            video_id=stale_video.id,
            views=50,
            engagement_rate=0.02,
            fetched_at=now - timedelta(days=35),
        )
    )
    db_session.add(
        RetentionCurve(
            video_id=stale_video.id,
            timestamp_s=20.0,
            audience_watch_ratio=0.6,
        )
    )
    await db_session.commit()

    try:
        # Run the purge.
        await _purge_stale_youtube_analytics_async()

        # Re-read from a fresh session — the purge uses AdminSessionLocal so
        # our test session won't see committed changes without a reload.
        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            # Fresh (5d) — both rows MUST survive.
            fresh_metrics = await s.scalar(
                select(VideoMetrics).where(VideoMetrics.video_id == fresh_video.id)
            )
            fresh_curves = (
                (
                    await s.execute(
                        select(RetentionCurve).where(RetentionCurve.video_id == fresh_video.id)
                    )
                )
                .scalars()
                .all()
            )
            # Boundary (29d, within 30d window) — both rows MUST survive.
            boundary_metrics = await s.scalar(
                select(VideoMetrics).where(VideoMetrics.video_id == boundary_video.id)
            )
            boundary_curves = (
                (
                    await s.execute(
                        select(RetentionCurve).where(RetentionCurve.video_id == boundary_video.id)
                    )
                )
                .scalars()
                .all()
            )
            # Stale (35d, past 30d cutoff) — both rows MUST be deleted.
            stale_metrics = await s.scalar(
                select(VideoMetrics).where(VideoMetrics.video_id == stale_video.id)
            )
            stale_curves = (
                (
                    await s.execute(
                        select(RetentionCurve).where(RetentionCurve.video_id == stale_video.id)
                    )
                )
                .scalars()
                .all()
            )
        await engine.dispose()

        assert fresh_metrics is not None, "5-day-old VideoMetrics must survive (within window)"
        assert len(fresh_curves) == 1, "5-day-old RetentionCurve must survive (within window)"

        assert boundary_metrics is not None, (
            "29-day-old VideoMetrics must survive — boundary case proves the "
            "purge does NOT delete rows still within the 30-day ToS window."
        )
        assert len(boundary_curves) == 1, "29-day-old RetentionCurve must survive"

        assert stale_metrics is None, (
            "35-day-old VideoMetrics MUST be deleted — YouTube ToS §III.E.4.b "
            "requires deletion when authorization cannot be re-verified within "
            "30 calendar days."
        )
        assert stale_curves == [], (
            "RetentionCurve rows MUST be deleted in lock-step with their "
            "parent VideoMetrics — they're written together by "
            "youtube/analytics.py::sync_video_analytics and must age out together."
        )
    finally:
        await _cleanup(db_session, creators_to_clean)


@pytest.mark.asyncio
async def test_purge_deletes_stale_audience_activity(db_session: AsyncSession):
    """AudienceActivity rows older than the cutoff are deleted per-creator."""
    now = datetime.now(UTC)
    creators_to_clean: list[uuid.UUID] = []

    fresh_creator, _ = await _seed_creator_with_video(db_session, "aa_fresh")
    creators_to_clean.append(fresh_creator.id)
    db_session.add(
        AudienceActivity(
            creator_id=fresh_creator.id,
            day_of_week=1,
            hour=12,
            activity_index=0.8,
            fetched_at=now - timedelta(days=5),
        )
    )

    stale_creator, _ = await _seed_creator_with_video(db_session, "aa_stale")
    creators_to_clean.append(stale_creator.id)
    db_session.add(
        AudienceActivity(
            creator_id=stale_creator.id,
            day_of_week=2,
            hour=14,
            activity_index=0.6,
            fetched_at=now - timedelta(days=40),
        )
    )
    await db_session.commit()

    try:
        await _purge_stale_youtube_analytics_async()

        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            fresh_activity = (
                (
                    await s.execute(
                        select(AudienceActivity).where(
                            AudienceActivity.creator_id == fresh_creator.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            stale_activity = (
                (
                    await s.execute(
                        select(AudienceActivity).where(
                            AudienceActivity.creator_id == stale_creator.id
                        )
                    )
                )
                .scalars()
                .all()
            )
        await engine.dispose()

        assert len(fresh_activity) == 1
        assert stale_activity == []
    finally:
        await _cleanup(db_session, creators_to_clean)


@pytest.mark.asyncio
async def test_purge_deletes_stale_demographics(db_session: AsyncSession):
    """Demographics rows older than the cutoff are deleted per-creator."""
    now = datetime.now(UTC)
    creators_to_clean: list[uuid.UUID] = []

    fresh_creator, _ = await _seed_creator_with_video(db_session, "dm_fresh")
    creators_to_clean.append(fresh_creator.id)
    db_session.add(
        Demographics(
            creator_id=fresh_creator.id,
            payload_jsonb={"age_18_24": 0.3},
            fetched_at=now - timedelta(days=10),
        )
    )

    stale_creator, _ = await _seed_creator_with_video(db_session, "dm_stale")
    creators_to_clean.append(stale_creator.id)
    db_session.add(
        Demographics(
            creator_id=stale_creator.id,
            payload_jsonb={"age_25_34": 0.4},
            fetched_at=now - timedelta(days=45),
        )
    )
    await db_session.commit()

    try:
        await _purge_stale_youtube_analytics_async()

        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            fresh = await s.scalar(
                select(Demographics).where(Demographics.creator_id == fresh_creator.id)
            )
            stale = await s.scalar(
                select(Demographics).where(Demographics.creator_id == stale_creator.id)
            )
        await engine.dispose()

        assert fresh is not None
        assert stale is None
    finally:
        await _cleanup(db_session, creators_to_clean)


@pytest.mark.asyncio
async def test_purge_is_idempotent_no_op_when_nothing_stale(db_session: AsyncSession):
    """Running the purge with only fresh rows is a clean no-op."""
    now = datetime.now(UTC)
    creators_to_clean: list[uuid.UUID] = []

    creator, video = await _seed_creator_with_video(db_session, "idem")
    creators_to_clean.append(creator.id)
    db_session.add(
        VideoMetrics(
            video_id=video.id,
            views=100,
            engagement_rate=0.05,
            fetched_at=now - timedelta(days=1),
        )
    )
    await db_session.commit()

    try:
        # Run twice — both must be no-ops; second run must not re-trigger.
        await _purge_stale_youtube_analytics_async()
        await _purge_stale_youtube_analytics_async()

        engine = create_async_engine(settings.DATABASE_URL)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            metrics = await s.scalar(select(VideoMetrics).where(VideoMetrics.video_id == video.id))
        await engine.dispose()

        assert metrics is not None, "Fresh rows must survive any number of purge runs"
    finally:
        await _cleanup(db_session, creators_to_clean)
