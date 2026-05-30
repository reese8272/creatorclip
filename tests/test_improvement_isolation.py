"""
Integration tests for /creators/me/improvement-brief — Issue 33 (SEV-0).

Verifies that the improvement-brief endpoint computes analytics from ONLY the
requesting creator's metrics. The prior unscoped query (`select(VideoMetrics).limit(50)`
with no creator filter) blended other creators' data into the Claude prompt.

Requires a running Postgres + Redis (see docker-compose.yml). Excluded from default
pytest run by pytest.ini `-m "not integration"`; runs in `integration.yml` CI workflow.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from auth import SESSION_COOKIE, create_session_token
from config import settings
from models import (
    Creator,
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
    session: AsyncSession,
    *,
    suffix: str,
    avg_views: int,
    n_videos: int = 5,
) -> Creator:
    creator = Creator(
        google_sub=f"test_iso_{suffix}_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_iso_{suffix}",
        channel_title=f"Channel {suffix}",
        onboarding_state=OnboardingState.active,
    )
    session.add(creator)
    await session.flush()

    now = datetime.now(UTC)
    for i in range(n_videos):
        video = Video(
            creator_id=creator.id,
            youtube_video_id=f"vid_{suffix}_{uuid.uuid4().hex[:8]}_{i}",
            kind=VideoKind.long,
            ingest_status=IngestStatus.done,
            duration_s=600.0,
        )
        session.add(video)
        await session.flush()
        session.add(
            VideoMetrics(
                video_id=video.id,
                views=avg_views,
                watch_time_s=avg_views * 100,
                avg_view_duration_s=120.0,
                engagement_rate=0.05,
                fetched_at=now,
            )
        )
    await session.commit()
    return creator


@pytest.mark.integration
async def test_improvement_brief_is_scoped_to_requesting_creator(
    db_session: AsyncSession, client, mocker
):
    """SEV-0 isolation: creator A's brief receives only A's metrics, never B's.

    The analytics build moved from the GET handler into the worker task (Issue 78d),
    so this drives the task path; the assertion is unchanged from the Issue-33 fix.
    """
    creator_a = await _seed_creator(db_session, suffix="A", avg_views=1_000)
    creator_b = await _seed_creator(db_session, suffix="B", avg_views=999_999)

    captured: dict = {}

    def _capture(*, channel_title, analytics, dna_brief):
        captured["channel_title"] = channel_title
        captured["analytics"] = analytics
        return "stubbed brief text"

    mocker.patch("improvement.brief.generate_improvement_brief", side_effect=_capture)
    fake_task = MagicMock()
    fake_task.id = "job-iso-legacy"
    mocker.patch("worker.tasks.generate_improvement_brief.delay", return_value=fake_task)

    token = create_session_token(creator_a.id)
    try:
        start = client.post(
            "/creators/me/improvement-brief", cookies={SESSION_COOKIE: token}
        )
        assert start.status_code == 202, start.text

        from worker.tasks import _generate_improvement_brief_async

        await _generate_improvement_brief_async("job-iso-legacy", str(creator_a.id))

        # The analytics summary fed to Claude must reflect ONLY creator A's data.
        assert captured["channel_title"] == "Channel A"
        assert captured["analytics"]["videos_in_db"] == 5  # A's count, not A+B = 10
        # Crucially: not B's 999_999 and not a blended ~500_500.
        assert captured["analytics"]["avg_views"] == pytest.approx(1_000.0)
    finally:
        await db_session.execute(
            delete(Creator).where(Creator.id.in_([creator_a.id, creator_b.id]))
        )
        await db_session.commit()


@pytest.mark.integration
async def test_improvement_brief_zero_data_returns_400(db_session: AsyncSession, client):
    """Honest behavior: a creator with no metrics gets 400, not a hallucinated brief."""
    creator = Creator(
        google_sub=f"test_zero_{uuid.uuid4().hex[:8]}",
        channel_id="UC_zero",
        channel_title="Zero Channel",
        onboarding_state=OnboardingState.active,
    )
    db_session.add(creator)
    await db_session.commit()

    token = create_session_token(creator.id)
    try:
        resp = client.post(
            "/creators/me/improvement-brief",
            cookies={SESSION_COOKIE: token},
        )
        assert resp.status_code == 400
        assert "not enough data" in resp.json()["detail"].lower()
    finally:
        await db_session.execute(delete(Creator).where(Creator.id == creator.id))
        await db_session.commit()
