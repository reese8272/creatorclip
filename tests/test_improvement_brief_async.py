"""
Integration tests for the async improvement-brief flow — Issue 78d.

The ~120s Claude + web_search brief moved off the request path: POST enqueues a
Celery task (202) and writes a ``pending`` row; the worker task fills it in; GET
polls the stored row. These tests drive the real Postgres row lifecycle and the
worker async impl directly, mocking only the LLM function boundary.

Requires Postgres + the Alembic schema. Marked ``integration`` (excluded from the
default ``-m "not integration"`` run).
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from auth import SESSION_COOKIE, create_session_token
from config import settings
from models import (
    Creator,
    ImprovementBrief,
    ImprovementBriefStatus,
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
    avg_views: int = 1_000,
    n_videos: int = 3,
) -> Creator:
    creator = Creator(
        google_sub=f"test_brief_{suffix}_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_brief_{suffix}",
        channel_title=f"Channel {suffix}",
        onboarding_state=OnboardingState.active,
    )
    session.add(creator)
    await session.flush()

    now = datetime.now(UTC)
    for i in range(n_videos):
        video = Video(
            creator_id=creator.id,
            youtube_video_id=f"vb_{suffix}_{uuid.uuid4().hex[:6]}_{i}",
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


async def _cleanup(session: AsyncSession, *creator_ids: uuid.UUID) -> None:
    await session.execute(delete(Creator).where(Creator.id.in_(creator_ids)))
    await session.commit()


async def _row(session: AsyncSession, creator_id: uuid.UUID) -> ImprovementBrief | None:
    return await session.scalar(
        select(ImprovementBrief).where(ImprovementBrief.creator_id == creator_id)
    )


# ── POST: 202 + pending row, debounce ─────────────────────────────────────────


@pytest.mark.integration
async def test_post_returns_202_and_creates_pending_row(db_session, client, mocker):
    creator = await _seed_creator(db_session, suffix="post")
    fake_task = MagicMock()
    fake_task.id = "task-abc-123"
    delay = mocker.patch("worker.tasks.generate_improvement_brief.delay", return_value=fake_task)

    token = create_session_token(creator.id)
    try:
        resp = client.post(
            "/creators/me/improvement-brief", cookies={SESSION_COOKIE: token}
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == "pending"
        assert body["task_id"] == "task-abc-123"
        delay.assert_called_once_with(str(creator.id))

        row = await _row(db_session, creator.id)
        assert row is not None
        assert row.status == ImprovementBriefStatus.pending
        assert row.job_id == "task-abc-123"
        assert row.brief_text is None
    finally:
        await _cleanup(db_session, creator.id)


@pytest.mark.integration
async def test_post_debounces_when_already_pending(db_session, client, mocker):
    """A second POST while a build is pending returns 202 without re-enqueuing."""
    creator = await _seed_creator(db_session, suffix="debounce")
    fake_task = MagicMock()
    fake_task.id = "task-first"
    delay = mocker.patch("worker.tasks.generate_improvement_brief.delay", return_value=fake_task)

    token = create_session_token(creator.id)
    try:
        first = client.post("/creators/me/improvement-brief", cookies={SESSION_COOKIE: token})
        assert first.status_code == 202
        second = client.post("/creators/me/improvement-brief", cookies={SESSION_COOKIE: token})
        assert second.status_code == 202
        assert second.json()["task_id"] == "task-first"
        # Debounced: the task was enqueued exactly once across both requests.
        delay.assert_called_once()
    finally:
        await _cleanup(db_session, creator.id)


@pytest.mark.integration
async def test_post_no_metrics_returns_400(db_session, client):
    """A creator with no metrics gets an honest 400 — no pending job that can only fail."""
    creator = Creator(
        google_sub=f"test_brief_nodata_{uuid.uuid4().hex[:8]}",
        channel_id="UC_brief_nodata",
        channel_title="No Data",
        onboarding_state=OnboardingState.active,
    )
    db_session.add(creator)
    await db_session.commit()

    token = create_session_token(creator.id)
    try:
        resp = client.post("/creators/me/improvement-brief", cookies={SESSION_COOKIE: token})
        assert resp.status_code == 400
        assert "not enough data" in resp.json()["detail"].lower()
        assert await _row(db_session, creator.id) is None
    finally:
        await _cleanup(db_session, creator.id)


# ── GET: poll target ──────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_get_returns_none_then_ready(db_session, client, mocker):
    """GET reports 'none' before any build, then 'ready' after the task fills the row."""
    creator = await _seed_creator(db_session, suffix="getready")
    token = create_session_token(creator.id)
    try:
        # No row yet.
        before = client.get("/creators/me/improvement-brief", cookies={SESSION_COOKIE: token})
        assert before.status_code == 200
        assert before.json()["status"] == "none"

        # Enqueue (mock the broker) then run the task body against the real DB.
        fake_task = MagicMock()
        fake_task.id = "job-getready"
        mocker.patch("worker.tasks.generate_improvement_brief.delay", return_value=fake_task)
        client.post("/creators/me/improvement-brief", cookies={SESSION_COOKIE: token})

        mocker.patch(
            "improvement.brief.generate_improvement_brief",
            return_value="Your hooks land best in the first 3 seconds.",
        )
        from worker.tasks import _generate_improvement_brief_async

        await _generate_improvement_brief_async("job-getready", str(creator.id))

        after = client.get("/creators/me/improvement-brief", cookies={SESSION_COOKIE: token})
        assert after.status_code == 200
        data = after.json()
        assert data["status"] == "ready"
        assert data["brief"] == "Your hooks land best in the first 3 seconds."
        assert data["completed_at"] is not None
    finally:
        await _cleanup(db_session, creator.id)


# ── Worker task: success, failure, isolation ──────────────────────────────────


@pytest.mark.integration
async def test_task_marks_failed_with_safe_error_on_llm_exception(db_session, client, mocker):
    """An LLM failure leaves status=failed and a SAFE message — no exception text leaks."""
    creator = await _seed_creator(db_session, suffix="fail")
    fake_task = MagicMock()
    fake_task.id = "job-fail"
    mocker.patch("worker.tasks.generate_improvement_brief.delay", return_value=fake_task)
    token = create_session_token(creator.id)
    try:
        client.post("/creators/me/improvement-brief", cookies={SESSION_COOKIE: token})

        mocker.patch(
            "improvement.brief.generate_improvement_brief",
            side_effect=RuntimeError("anthropic 401 secret-token-leak"),
        )
        from worker.tasks import _generate_improvement_brief_async

        with pytest.raises(RuntimeError):
            await _generate_improvement_brief_async("job-fail", str(creator.id))

        row = await _row(db_session, creator.id)
        assert row is not None
        assert row.status == ImprovementBriefStatus.failed
        # The stored error is a generic message — never the raised exception text.
        assert "secret-token-leak" not in (row.error or "")
        assert "401" not in (row.error or "")
    finally:
        await _cleanup(db_session, creator.id)


@pytest.mark.integration
async def test_task_is_scoped_to_requesting_creator(db_session, client, mocker):
    """SEV-0 isolation (moved with the logic): the task feeds Claude only this creator's data."""
    creator_a = await _seed_creator(db_session, suffix="A", avg_views=1_000)
    creator_b = await _seed_creator(db_session, suffix="B", avg_views=999_999)

    captured: dict = {}

    def _capture(*, channel_title, analytics, dna_brief):
        captured["channel_title"] = channel_title
        captured["analytics"] = analytics
        return "stubbed brief text"

    mocker.patch("improvement.brief.generate_improvement_brief", side_effect=_capture)

    fake_task = MagicMock()
    fake_task.id = "job-iso"
    mocker.patch("worker.tasks.generate_improvement_brief.delay", return_value=fake_task)
    token = create_session_token(creator_a.id)
    try:
        client.post("/creators/me/improvement-brief", cookies={SESSION_COOKIE: token})
        from worker.tasks import _generate_improvement_brief_async

        await _generate_improvement_brief_async("job-iso", str(creator_a.id))

        assert captured["channel_title"] == "Channel A"
        assert captured["analytics"]["videos_in_db"] == 3  # A's count, not A+B
        assert captured["analytics"]["avg_views"] == pytest.approx(1_000.0)
    finally:
        await _cleanup(db_session, creator_a.id, creator_b.id)


@pytest.mark.integration
async def test_task_idempotent_on_redelivery(db_session, client, mocker):
    """A redelivery of the same job after success no-ops — no second paid LLM call."""
    creator = await _seed_creator(db_session, suffix="idem")
    fake_task = MagicMock()
    fake_task.id = "job-idem"
    mocker.patch("worker.tasks.generate_improvement_brief.delay", return_value=fake_task)
    token = create_session_token(creator.id)
    try:
        client.post("/creators/me/improvement-brief", cookies={SESSION_COOKIE: token})
        build = mocker.patch(
            "improvement.brief.generate_improvement_brief", return_value="first brief"
        )
        from worker.tasks import _generate_improvement_brief_async

        await _generate_improvement_brief_async("job-idem", str(creator.id))
        assert build.call_count == 1

        # Redelivery of the same job id — already ready, so the LLM is not called again.
        await _generate_improvement_brief_async("job-idem", str(creator.id))
        assert build.call_count == 1
    finally:
        await _cleanup(db_session, creator.id)
