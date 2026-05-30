"""
Integration tests for Batch 4a (Issues 66 + 67) against a real Postgres.

Both endpoints used to run a blocking call directly on the API event loop. These
assert the blocking work is now offloaded via asyncio.to_thread (so it can't pin
the loop), while staying behavior-preserving. External services (Anthropic, R2,
Celery enqueue) are mocked; the DB is real (per the no-DB-mock rule).

Marked `integration` (excluded from the default run — see pytest.ini).
"""

import io
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from auth import SESSION_COOKIE, create_session_token
from config import settings
from models import Creator, OnboardingState, Video, VideoKind, VideoMetrics

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _offload_recorder():
    """A passthrough replacement for asyncio.to_thread that records the offloaded
    callable and runs it synchronously (fine in a test)."""
    calls: list = []

    async def _to_thread(fn, *args, **kwargs):
        calls.append(fn)
        return fn(*args, **kwargs)

    return calls, _to_thread


@pytest.mark.asyncio
async def test_improvement_brief_is_offloaded(db_session: AsyncSession, client, mocker):
    """The ~120s brief call is offloaded via to_thread inside the worker task.

    The call moved off the request path into a Celery task (Issue 78d); it still
    runs through asyncio.to_thread so it can't pin the worker's event loop.
    """
    creator = Creator(
        google_sub=f"test_off_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_off_{uuid.uuid4().hex[:6]}",
        channel_title="Offload Channel",
        onboarding_state=OnboardingState.active,
    )
    db_session.add(creator)
    await db_session.flush()
    video = Video(
        creator_id=creator.id,
        youtube_video_id=f"yt_{uuid.uuid4().hex[:8]}",
        title="v",
        kind=VideoKind.long,
    )
    db_session.add(video)
    await db_session.flush()
    db_session.add(
        VideoMetrics(
            video_id=video.id,
            views=1000,
            engagement_rate=0.05,
            fetched_at=datetime.now(UTC),
        )
    )
    await db_session.commit()

    from models import ImprovementBrief, ImprovementBriefStatus

    db_session.add(
        ImprovementBrief(
            creator_id=creator.id, status=ImprovementBriefStatus.pending, job_id="job-off"
        )
    )
    await db_session.commit()

    brief_mock = mocker.patch(
        "improvement.brief.generate_improvement_brief", return_value="stub brief"
    )
    calls, fake_to_thread = _offload_recorder()
    mocker.patch("asyncio.to_thread", new=fake_to_thread)

    try:
        from worker.tasks import _generate_improvement_brief_async

        await _generate_improvement_brief_async("job-off", str(creator.id))
        # The 120s LLM call was offloaded, not run on the loop.
        assert brief_mock in calls
    finally:
        await db_session.execute(delete(Creator).where(Creator.id == creator.id))
        await db_session.commit()


@pytest.mark.asyncio
async def test_upload_storage_write_is_offloaded(db_session: AsyncSession, client, mocker):
    creator = Creator(
        google_sub=f"test_upl_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_upl_{uuid.uuid4().hex[:6]}",
        channel_title="Upload Channel",
        onboarding_state=OnboardingState.active,
        minutes_balance=100,
    )
    db_session.add(creator)
    await db_session.commit()

    upload_mock = mocker.patch("routers.videos.upload_file", return_value="source/x.mp4")
    mocker.patch("routers.videos.start_pipeline")  # don't enqueue a real Celery job
    calls, fake_to_thread = _offload_recorder()
    mocker.patch("asyncio.to_thread", new=fake_to_thread)

    token = create_session_token(creator.id)
    try:
        resp = client.post(
            "/videos/upload",
            cookies={SESSION_COOKIE: token},
            data={"youtube_video_id": "abc12345678"},
            files={"file": ("v.mp4", io.BytesIO(b"tiny fake video"), "video/mp4")},
        )
        assert resp.status_code == 200, resp.text
        # The R2 PUT / disk copy was offloaded off the event loop.
        assert upload_mock in calls
    finally:
        await db_session.execute(delete(Video).where(Video.creator_id == creator.id))
        await db_session.execute(delete(Creator).where(Creator.id == creator.id))
        await db_session.commit()
