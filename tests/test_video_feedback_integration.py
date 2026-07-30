"""Integration tests for video_feedback RLS isolation (Issue 370, migration 0048).

Requires real Postgres with the RLS roles (docker-compose dev / integration CI),
so it is marked `integration` and deselected from the default unit lane.
Mirrors tests/test_clip_impressions_integration.py's role-switch strategy.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from models import (
    Creator,
    IngestStatus,
    OnboardingState,
    Video,
    VideoFeedback,
    VideoKind,
    VideoSentiment,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def admin_engine():
    eng = create_async_engine(settings.database_migration_url, pool_pre_ping=True)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db_session(admin_engine):
    factory = async_sessionmaker(admin_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


async def _seed_creator_with_feedback(session: AsyncSession, label: str):
    creator = Creator(
        google_sub=f"test_vf_{label}_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_vf_{label}_{uuid.uuid4().hex[:6]}",
        channel_title=f"VideoFeedback Test {label}",
        onboarding_state=OnboardingState.active,
        minutes_balance=100,
    )
    session.add(creator)
    await session.flush()
    video = Video(
        creator_id=creator.id,
        youtube_video_id=f"yt_{uuid.uuid4().hex[:8]}",
        title="style-review fixture",
        kind=VideoKind.long,
        duration_s=120.0,
        ingest_status=IngestStatus.done,
    )
    session.add(video)
    await session.flush()
    fb = VideoFeedback(
        creator_id=creator.id,
        video_id=video.id,
        sentiment=VideoSentiment.like,
        feedback_tags=["pacing_feels_right"],
        feedback_note=f"note-{label}",
    )
    session.add(fb)
    await session.commit()
    return creator, video, fb


async def _cleanup(session: AsyncSession, creator_ids: list[uuid.UUID]) -> None:
    for model in (VideoFeedback, Video):
        await session.execute(delete(model).where(model.creator_id.in_(creator_ids)))
    await session.execute(delete(Creator).where(Creator.id.in_(creator_ids)))
    await session.commit()


@pytest.mark.asyncio
async def test_video_feedback_row_roundtrip(db_session):
    creator, video, _ = await _seed_creator_with_feedback(db_session, "rt")
    try:
        row = (
            await db_session.execute(
                select(VideoFeedback).where(VideoFeedback.creator_id == creator.id)
            )
        ).scalar_one()
        assert row.video_id == video.id
        assert row.sentiment == VideoSentiment.like
        assert row.feedback_tags == ["pacing_feels_right"]
    finally:
        await _cleanup(db_session, [creator.id])


@pytest.mark.asyncio
async def test_video_feedback_isolation_blocks_cross_tenant(admin_engine, db_session):
    """Under the creatorclip_app role with creator A's GUC set, an unfiltered scan
    of video_feedback must never return creator B's rows (tenant_isolation RLS)."""
    creator_a, _, _ = await _seed_creator_with_feedback(db_session, "A")
    creator_b, _, _ = await _seed_creator_with_feedback(db_session, "B")
    try:
        factory = async_sessionmaker(admin_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            await s.execute(text("SET LOCAL ROLE creatorclip_app"))
            await s.execute(
                text("SELECT set_config('app.creator_id', :cid, true)"),
                {"cid": str(creator_a.id)},
            )
            rows = (await s.execute(text("SELECT creator_id FROM video_feedback"))).all()
            visible = {r[0] for r in rows}
            assert creator_b.id not in visible, "RLS leak: creator B feedback visible to A"
    finally:
        await _cleanup(db_session, [creator_a.id, creator_b.id])
