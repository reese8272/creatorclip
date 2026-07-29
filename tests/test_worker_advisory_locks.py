"""Advisory-lock hardening (ready-pass W2 — OFF_COURSE 2026-06-30 row).

``_rollback_then_unlock`` is the shared ``finally`` epilogue for every
``pg_try_advisory_lock`` site in worker/tasks.py. If its rollback (or the
unlock execute) raises, the session-level advisory lock would leak onto the
pooled backend — silently no-oping that sweep until ``pool_recycle``. The
hardened helper invalidates the session instead: the connection is discarded,
and PostgreSQL frees session-level advisory locks at session end.

``_try_advisory_lock`` is the DRY acquire: a ``False`` (skip) is observable
via a ``beat_lock_skip`` event + the ``BEAT_LOCK_SKIPS_TOTAL`` counter.

Unit lane: sessions faked at the boundary — no Postgres needed.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from observability import BEAT_LOCK_SKIPS_TOTAL
from worker.tasks import _rollback_then_unlock, _try_advisory_lock

# ── _rollback_then_unlock ─────────────────────────────────────────────────────


def _session(calls: list[str]) -> AsyncMock:
    session = AsyncMock()
    session.rollback = AsyncMock(side_effect=lambda: calls.append("rollback"))
    session.execute = AsyncMock(side_effect=lambda *a, **kw: calls.append("unlock"))
    session.invalidate = AsyncMock(side_effect=lambda: calls.append("invalidate"))
    return session


async def test_happy_path_rolls_back_then_unlocks_without_invalidate() -> None:
    calls: list[str] = []
    session = _session(calls)

    await _rollback_then_unlock(session, "sweep_x")

    assert calls == ["rollback", "unlock"]
    session.invalidate.assert_not_awaited()


async def test_rollback_failure_invalidates_connection_and_does_not_raise() -> None:
    """A rollback failure must invalidate the connection (Postgres then frees
    the session-level lock at session end) and must NOT raise — the helper runs
    in ``finally`` blocks, where a raise would mask the sweep's own error."""
    calls: list[str] = []
    session = _session(calls)
    session.rollback.side_effect = RuntimeError("connection went away")

    with patch("worker.tasks.log_event") as log_event:
        await _rollback_then_unlock(session, "sweep_x")

    session.invalidate.assert_awaited_once()
    assert "unlock" not in calls  # execute never ran after the rollback failure
    log_event.assert_called_once_with("beat_lock_unlock_failed", lock_key="sweep_x")


async def test_unlock_failure_invalidates_connection() -> None:
    calls: list[str] = []
    session = _session(calls)
    session.execute.side_effect = RuntimeError("failed txn state")

    await _rollback_then_unlock(session, "sweep_y")

    assert calls == ["rollback", "invalidate"]


async def test_invalidate_failure_is_swallowed() -> None:
    """Even a failing invalidate must not raise out of a caller's finally."""
    session = _session([])
    session.rollback.side_effect = RuntimeError("boom")
    session.invalidate.side_effect = RuntimeError("also boom")

    await _rollback_then_unlock(session, "sweep_z")  # must not raise


# ── _try_advisory_lock ────────────────────────────────────────────────────────


def _acquire_session(acquired: bool) -> AsyncMock:
    result = MagicMock()
    result.scalar_one = MagicMock(return_value=acquired)
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    return session


async def test_acquired_returns_true_without_skip_signal() -> None:
    session = _acquire_session(True)
    with patch("worker.tasks.log_event") as log_event:
        assert await _try_advisory_lock(session, "poll_clip_outcomes") is True
    log_event.assert_not_called()


async def test_skip_emits_event_and_increments_counter() -> None:
    session = _acquire_session(False)
    before = BEAT_LOCK_SKIPS_TOTAL.labels(task="poll_clip_outcomes")._value.get()

    with patch("worker.tasks.log_event") as log_event:
        assert await _try_advisory_lock(session, "poll_clip_outcomes") is False

    log_event.assert_called_once_with(
        "beat_lock_skip", task="poll_clip_outcomes", lock_key="poll_clip_outcomes"
    )
    after = BEAT_LOCK_SKIPS_TOTAL.labels(task="poll_clip_outcomes")._value.get()
    assert after == before + 1


async def test_skip_strips_per_creator_suffix_from_metric_label() -> None:
    """Per-creator lock keys (retrain:{uuid}) must not explode metric
    cardinality — the counter label is the task portion only."""
    cid = uuid.uuid4()
    session = _acquire_session(False)
    before = BEAT_LOCK_SKIPS_TOTAL.labels(task="retrain")._value.get()

    with patch("worker.tasks.log_event") as log_event:
        await _try_advisory_lock(session, f"retrain:{cid}")

    log_event.assert_called_once_with(
        "beat_lock_skip", task="retrain", lock_key=f"retrain:{cid}"
    )
    assert BEAT_LOCK_SKIPS_TOTAL.labels(task="retrain")._value.get() == before + 1
