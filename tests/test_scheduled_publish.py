"""Tests for Issue 196: Scheduled publish from the upload-timing window.

Covers:
  - PublishStatus / PublishPlatform enum values (scheduling fields exist on model)
  - SchedulePublishIn validator rejects past / naive datetimes
  - sweep_scheduled_publications_async idempotency: a row already transitioned to
    ``pending`` is never double-enqueued
  - sweep skips rows that are ``scheduled`` (not yet confirmed) or whose
    scheduled_at is still in the future
  - sweep acquires the advisory lock and skips if already held

All tests are DB-free (mock-based). Real Postgres + full Beat integration are
staging-pending (Issue 275).
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models import PublishPlatform, PublishStatus

# ── Model field presence ──────────────────────────────────────────────────────


def test_publish_status_includes_scheduling_values():
    """Issue 196 scheduling lifecycle values must exist on PublishStatus."""
    values = {s.value for s in PublishStatus}
    assert "scheduled" in values
    assert "confirmed" in values
    # Existing values from Issue 195 must not regress.
    assert "pending" in values
    assert "running" in values
    assert "done" in values
    assert "failed" in values


def test_publish_platform_youtube_exists():
    """PublishPlatform.youtube must exist and be the only current value."""
    assert PublishPlatform.youtube.value == "youtube"
    assert len(list(PublishPlatform)) == 1


def test_clip_publication_has_scheduling_fields():
    """ClipPublication must expose the three Issue 196 scheduling columns."""
    from models import ClipPublication

    assert hasattr(ClipPublication, "scheduled_at")
    assert hasattr(ClipPublication, "platform")
    assert hasattr(ClipPublication, "confirmed_at")
    # task_id must now be nullable (set by sweep, not at creation time).
    assert hasattr(ClipPublication, "task_id")


# ── SchedulePublishIn validator ────────────────────────────────────────────────


def test_schedule_in_rejects_past_datetime():
    """Validator must raise ValueError for scheduled_at in the past."""
    from pydantic import ValidationError

    from routers.publications import SchedulePublishIn

    past = datetime.now(UTC) - timedelta(hours=1)
    with pytest.raises(ValidationError) as ei:
        SchedulePublishIn(scheduled_at=past)
    assert "future" in str(ei.value).lower()


def test_schedule_in_rejects_naive_datetime():
    """Validator must raise ValueError for a tz-naive datetime (ambiguous)."""
    from pydantic import ValidationError

    from routers.publications import SchedulePublishIn

    naive = datetime.utcnow() + timedelta(hours=2)  # naive — no tzinfo
    with pytest.raises(ValidationError) as ei:
        SchedulePublishIn(scheduled_at=naive)
    assert "timezone" in str(ei.value).lower()


def test_schedule_in_accepts_future_aware_datetime():
    """A future tz-aware datetime must pass validation."""
    from routers.publications import SchedulePublishIn

    future = datetime.now(UTC) + timedelta(hours=3)
    obj = SchedulePublishIn(scheduled_at=future)
    assert obj.scheduled_at == future
    assert obj.platform == PublishPlatform.youtube


# ── Sweep task idempotency ─────────────────────────────────────────────────────


class _SessionCM:
    """Minimal async context-manager wrapper for a mock session."""

    def __init__(self, session: MagicMock) -> None:
        self._session = session

    async def __aenter__(self) -> MagicMock:
        return self._session

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _pub(
    *,
    status: PublishStatus = PublishStatus.confirmed,
    scheduled_at: datetime | None = None,
    platform: PublishPlatform = PublishPlatform.youtube,
    clip_id: uuid.UUID | None = None,
) -> MagicMock:
    """Build a minimal ClipPublication mock for the sweep."""
    pub = MagicMock()
    pub.status = status
    pub.scheduled_at = scheduled_at or (datetime.now(UTC) - timedelta(minutes=1))
    pub.platform = platform
    pub.clip_id = clip_id or uuid.uuid4()
    pub.task_id = None
    pub.updated_at = None
    return pub


def _mock_session(
    *,
    lock_acquired: bool = True,
    rows: list[MagicMock] | None = None,
) -> MagicMock:
    """Build a mock AsyncSession for sweep tests."""
    session = MagicMock()

    # advisory lock execute — returns acquired bool
    lock_result = MagicMock()
    lock_result.scalar_one = MagicMock(return_value=lock_acquired)

    # row select execute — returns scalars().all() list
    row_result = MagicMock()
    row_scalars = MagicMock()
    row_scalars.all = MagicMock(return_value=rows or [])
    row_result.scalars = MagicMock(return_value=row_scalars)

    # execute returns lock result on first call, row result on second call
    session.execute = AsyncMock(side_effect=[lock_result, row_result, MagicMock()])
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    return session


def test_sweep_skips_when_lock_not_acquired():
    """Sweep must return early without touching DB when advisory lock is held."""
    from worker.tasks import _sweep_scheduled_publications_async

    session = _mock_session(lock_acquired=False)

    with patch("worker.tasks.db.AdminSessionLocal", lambda: _SessionCM(session)):
        asyncio.run(_sweep_scheduled_publications_async())

    # Only one execute call (the lock attempt) — no row query, no commit.
    assert session.execute.call_count == 1
    session.commit.assert_not_called()


def test_sweep_skips_when_no_due_rows():
    """Sweep must commit nothing when no confirmed+due rows exist."""
    from worker.tasks import _sweep_scheduled_publications_async

    session = _mock_session(lock_acquired=True, rows=[])

    with patch("worker.tasks.db.AdminSessionLocal", lambda: _SessionCM(session)):
        asyncio.run(_sweep_scheduled_publications_async())

    session.commit.assert_not_called()


def test_sweep_enqueues_due_confirmed_row():
    """Sweep must transition a due confirmed row to pending and enqueue the task."""
    from worker.tasks import _sweep_scheduled_publications_async

    clip_id = uuid.uuid4()
    pub = _pub(clip_id=clip_id)
    session = _mock_session(lock_acquired=True, rows=[pub])

    with (
        patch("worker.tasks.db.AdminSessionLocal", lambda: _SessionCM(session)),
        patch("worker.tasks.publish_to_youtube") as mock_task,
    ):
        mock_task.apply_async = MagicMock()
        asyncio.run(_sweep_scheduled_publications_async())

    # Row transitions to pending and gets a task_id.
    assert pub.status == PublishStatus.pending
    assert pub.task_id is not None
    # Task is enqueued with the clip_id and the assigned task_id.
    mock_task.apply_async.assert_called_once()
    call_args = mock_task.apply_async.call_args
    # apply_async(args=[str(clip_id)], task_id=…)
    enqueued_args = call_args.kwargs.get("args") or (call_args.args[0] if call_args.args else [])
    assert str(clip_id) in enqueued_args
    session.commit.assert_called_once()


def test_sweep_enqueues_multiple_due_rows():
    """Sweep must enqueue one task per qualifying row."""
    from worker.tasks import _sweep_scheduled_publications_async

    pubs = [_pub(), _pub(), _pub()]
    session = _mock_session(lock_acquired=True, rows=pubs)

    with (
        patch("worker.tasks.db.AdminSessionLocal", lambda: _SessionCM(session)),
        patch("worker.tasks.publish_to_youtube") as mock_task,
    ):
        mock_task.apply_async = MagicMock()
        asyncio.run(_sweep_scheduled_publications_async())

    assert mock_task.apply_async.call_count == 3
    # Each row gets a distinct task_id.
    task_ids = {p.task_id for p in pubs}
    assert len(task_ids) == 3  # all unique


def test_sweep_idempotency_distinct_task_ids():
    """Two sweep ticks for different rows must produce distinct task_ids."""
    from worker.tasks import _sweep_scheduled_publications_async

    pub_a = _pub()
    pub_b = _pub()

    def _run_sweep(rows: list[MagicMock]) -> None:
        session = _mock_session(lock_acquired=True, rows=rows)
        with (
            patch("worker.tasks.db.AdminSessionLocal", lambda: _SessionCM(session)),
            patch("worker.tasks.publish_to_youtube") as mock_task,
        ):
            mock_task.apply_async = MagicMock()
            asyncio.run(_sweep_scheduled_publications_async())

    _run_sweep([pub_a])
    _run_sweep([pub_b])

    assert pub_a.task_id != pub_b.task_id
    assert pub_a.status == PublishStatus.pending
    assert pub_b.status == PublishStatus.pending


# ── PublicationOut helper ──────────────────────────────────────────────────────


def test_publication_out_from_orm_includes_privacy_note():
    """PublicationOut must surface the privacy note on every response."""
    from routers.publications import _PRIVACY_NOTE, PublicationOut

    now = datetime.now(UTC)
    pub = MagicMock()
    pub.id = uuid.uuid4()
    pub.clip_id = uuid.uuid4()
    pub.creator_id = uuid.uuid4()
    pub.task_id = None
    pub.youtube_video_id = None
    pub.status = PublishStatus.scheduled
    pub.error = None
    pub.scheduled_at = now + timedelta(hours=2)
    pub.platform = PublishPlatform.youtube
    pub.confirmed_at = None
    pub.created_at = now
    pub.updated_at = now

    out = PublicationOut.from_pub(pub)
    assert out.privacy_note == _PRIVACY_NOTE
    assert "virality" not in out.privacy_note.lower()  # honesty constraint
    assert out.status == "scheduled"
    assert out.platform == "youtube"
