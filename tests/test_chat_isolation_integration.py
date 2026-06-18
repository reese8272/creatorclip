"""Integration: Pro-chatbot tools are scoped to the requesting creator (Issue 152).

The load-bearing guarantee — a creator can only ever read their OWN channel
through the chatbot's tools — is enforced by the ``creator_id`` filter in every
``chat.tools`` executor. This drives each executor against a real Postgres with
two creators seeded and asserts creator A never sees creator B's data.

Requires Postgres (see docker-compose.yml). Excluded from the default run by
pytest.ini ``-m "not integration"``; runs in the integration CI workflow.
"""

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from chat.tools import execute_tool
from config import settings
from models import (
    AudienceActivity,
    Creator,
    CreatorDna,
    DnaStatus,
    IngestStatus,
    OnboardingState,
    Video,
    VideoKind,
    VideoMetrics,
)


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


async def _seed_creator(
    session: AsyncSession, *, suffix: str, views: int, activity_hour: int
) -> Creator:
    creator = Creator(
        google_sub=f"chat_iso_{suffix}_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_chat_{suffix}",
        channel_title=f"Channel {suffix}",
        onboarding_state=OnboardingState.active,
    )
    session.add(creator)
    await session.flush()

    now = datetime.now(UTC)
    for i in range(3):
        video = Video(
            creator_id=creator.id,
            youtube_video_id=f"yt_{suffix}_{uuid.uuid4().hex[:6]}{i}"[:32],
            title=f"{suffix} video {i}",
            kind=VideoKind.long,
            ingest_status=IngestStatus.done,
            duration_s=600.0,
            published_at=now,
        )
        session.add(video)
        await session.flush()
        session.add(
            VideoMetrics(
                video_id=video.id,
                views=views,
                watch_time_s=views * 60,
                avg_view_duration_s=120.0,
                engagement_rate=0.05,
                fetched_at=now,
            )
        )

    session.add(
        AudienceActivity(
            creator_id=creator.id,
            day_of_week=1,
            hour=activity_hour,
            activity_index=9.0,
            fetched_at=now,
        )
    )
    session.add(
        CreatorDna(
            creator_id=creator.id,
            version=1,
            brief_text=f"{suffix} channel brief.",
            status=DnaStatus.confirmed,  # get_active() returns the confirmed profile
        )
    )
    await session.commit()
    return creator


@pytest.mark.integration
async def test_chat_tools_are_creator_scoped(db_session: AsyncSession):
    a = await _seed_creator(db_session, suffix="A", views=100, activity_hour=9)
    b = await _seed_creator(db_session, suffix="B", views=999_999, activity_hour=20)

    # B's youtube id — A must NOT be able to reach it.
    b_video = await db_session.scalar(
        Video.__table__.select().where(Video.creator_id == b.id).limit(1)
    )
    b_youtube_id = b_video.youtube_video_id

    try:
        import json

        # get_recent_videos: only A's titles.
        recent = json.loads(await execute_tool("get_recent_videos", {}, a.id, db_session))
        assert recent["count"] == 3
        assert all(v["title"].startswith("A ") for v in recent["videos"])
        assert all(v["views"] == 100 for v in recent["videos"])

        # get_channel_averages: A's number, never blended with B's 999_999.
        avg = json.loads(await execute_tool("get_channel_averages", {}, a.id, db_session))
        assert avg["available"] is True
        assert avg["avg_views"] == pytest.approx(100.0)
        assert avg["sample_size"] == 3

        # get_video_performance: B's video id is unreachable for A.
        perf = json.loads(
            await execute_tool(
                "get_video_performance", {"video_query": b_youtube_id}, a.id, db_session
            )
        )
        assert perf["found"] is False

        # ...but A can reach A's own video.
        own = json.loads(
            await execute_tool(
                "get_video_performance", {"video_query": "A video"}, a.id, db_session
            )
        )
        assert own["found"] is True
        assert own["title"].startswith("A ")

        # get_upload_timing: from A's activity (hour 9), not B's (hour 20).
        timing = json.loads(await execute_tool("get_upload_timing", {}, a.id, db_session))
        assert timing["available"] is True
        assert all(w["hour"] == 9 for w in timing["best_windows"])

        # get_channel_dna: A's brief only.
        dna = json.loads(await execute_tool("get_channel_dna", {}, a.id, db_session))
        assert dna["available"] is True
        assert dna["brief"] == "A channel brief."
    finally:
        await db_session.execute(delete(Creator).where(Creator.id.in_([a.id, b.id])))
        await db_session.commit()
