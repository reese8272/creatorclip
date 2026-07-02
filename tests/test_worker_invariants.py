"""Worker invariants (Issue 352 Batch F).

Pins three SEV2 fixes without a DB:
1. Advisory-lock release survives a failed transaction — `_rollback_then_unlock`
   rolls the session back BEFORE issuing pg_advisory_unlock, so an aborted
   transaction cannot leak the lock until connection recycle.
2. A soft-timeout in the ingest chain (ingest_video / transcribe_video /
   build_signals) marks the video `failed` (no perpetual "running" spinner)
   and re-raises WITHOUT retry so RefundOnFailureTask.on_failure fires.
3. `_keyset_batches` paginates by primary key so the daily analytics refresh
   and the GDPR export never load an unbounded result set at once.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from celery.exceptions import SoftTimeLimitExceeded

from models import IngestStatus

# ── 1. Rollback-before-unlock on a crafted failure inside the locked section ──


async def test_failed_locked_section_rolls_back_then_unlocks() -> None:
    """Representative path: _purge_stale_youtube_analytics_async's body raises →
    the finally must roll back FIRST, then release the advisory lock, and the
    original error must still propagate."""
    calls: list[str] = []

    lock_result = MagicMock()
    lock_result.scalar_one.return_value = True

    async def _execute(stmt, *args, **kwargs):
        sql = str(stmt)
        if "pg_try_advisory_lock" in sql:
            calls.append("lock")
            return lock_result
        if "pg_advisory_unlock" in sql:
            calls.append("unlock")
            return MagicMock()
        calls.append("query")
        raise RuntimeError("boom inside locked section")

    async def _rollback():
        calls.append("rollback")

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)
    session.rollback = AsyncMock(side_effect=_rollback)

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)

    from worker.tasks import _purge_stale_youtube_analytics_async

    with patch("worker.tasks.db.AdminSessionLocal", return_value=cm), pytest.raises(RuntimeError):
        await _purge_stale_youtube_analytics_async()

    assert calls == ["lock", "query", "rollback", "unlock"], calls


# ── 2. Ingest-chain soft-timeout marks the video failed, no retry ─────────────


@pytest.mark.parametrize(
    ("task_name", "body_name"),
    [
        ("ingest_video", "_ingest_async"),
        ("transcribe_video", "_transcribe_async"),
        ("build_signals", "_signals_async"),
    ],
)
def test_ingest_chain_soft_timeout_marks_failed_without_retry(
    monkeypatch, task_name: str, body_name: str
) -> None:
    from worker import tasks

    statuses: list[tuple[IngestStatus, str | None]] = []

    async def _creator(video_id):
        return "creator-1"

    async def _set_status(video_id, status, reason=None):
        statuses.append((status, reason))

    async def _soft_timeout(video_id):
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(tasks, "_creator_id_for_video", _creator)
    monkeypatch.setattr(tasks, "_set_status", _set_status)
    monkeypatch.setattr(tasks, body_name, _soft_timeout)

    task = getattr(tasks, task_name)

    def _no_retry(**kwargs):
        raise AssertionError("soft-timeout must not retry")

    monkeypatch.setattr(task, "retry", _no_retry)

    with pytest.raises(SoftTimeLimitExceeded):
        task(str(uuid.uuid4()))

    assert len(statuses) == 1, f"{task_name} must set exactly one status on soft-timeout"
    status, reason = statuses[0]
    assert status == IngestStatus.failed
    assert reason and "timed out" in reason


# ── 3. Keyset pagination bounds the sweep/export loads ────────────────────────


async def test_keyset_batches_paginates_by_pk_and_stops() -> None:
    from sqlalchemy import select

    from models import Video
    from worker.tasks import _keyset_batches

    batch1 = [MagicMock(id=1), MagicMock(id=2)]
    batch2 = [MagicMock(id=3)]  # short batch → pagination ends without an extra query

    def _result(rows):
        r = MagicMock()
        r.scalars.return_value = list(rows)
        return r

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[_result(batch1), _result(batch2)])

    seen = []
    async for batch in _keyset_batches(
        session, select(Video).where(Video.creator_id == uuid.uuid4()), Video.id, batch_size=2
    ):
        seen.extend(batch)

    assert seen == batch1 + batch2
    assert session.execute.await_count == 2
    # Every query is LIMITed; the second resumes strictly after the last-seen key.
    first_stmt = str(session.execute.await_args_list[0].args[0])
    second_stmt = str(session.execute.await_args_list[1].args[0])
    assert "LIMIT" in first_stmt and "LIMIT" in second_stmt
    assert "videos.id >" in second_stmt and "videos.id >" not in first_stmt
