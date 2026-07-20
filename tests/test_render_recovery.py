"""Tests for Issue 359: stale-`running` render recovery.

Covers:
  - ais_render_stale marker semantics (absent → stale, fresh → live,
    old → stale, Redis error → fail-closed fresh)
  - render_stale_after_s derives from the Celery soft limit + hard margin
  - _sweep_stale_renders_async flips only stale `running` clips/summaries to
    `failed` and skips cleanly when the advisory lock is held
  - the Beat schedule registers the sweep

All tests are DB-free (mock-based); real Postgres + Beat integration are
staging-pending, mirroring tests/test_scheduled_publish.py.
"""

import asyncio
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from models import RenderStatus


class _SessionCM:
    """Minimal async context-manager wrapper for a mock session."""

    def __init__(self, session: MagicMock) -> None:
        self._session = session

    async def __aenter__(self) -> MagicMock:
        return self._session

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _row(status: RenderStatus = RenderStatus.running) -> MagicMock:
    row = MagicMock()
    row.id = uuid.uuid4()
    row.creator_id = uuid.uuid4()
    row.render_status = status
    return row


def _mock_session(
    *,
    lock_acquired: bool = True,
    clips: list[MagicMock] | None = None,
    summaries: list[MagicMock] | None = None,
) -> MagicMock:
    """Mock AsyncSession: advisory lock, clip select, summary select, unlock."""
    session = MagicMock()

    lock_result = MagicMock()
    lock_result.scalar_one = MagicMock(return_value=lock_acquired)

    def _rows_result(rows: list[MagicMock] | None) -> MagicMock:
        result = MagicMock()
        scalars = MagicMock()
        scalars.all = MagicMock(return_value=rows or [])
        result.scalars = MagicMock(return_value=scalars)
        return result

    session.execute = AsyncMock(
        side_effect=[lock_result, _rows_result(clips), _rows_result(summaries), MagicMock()]
    )
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


# ── ais_render_stale ──────────────────────────────────────────────────────────


def _redis_with_get(value: object) -> MagicMock:
    client = MagicMock()
    if isinstance(value, Exception):
        client.get = AsyncMock(side_effect=value)
    else:
        client.get = AsyncMock(return_value=value)
    return client


def test_absent_marker_reports_stale():
    """No render-start marker (pre-fix stuck row / Redis flush) → recoverable."""
    from worker.tasks import ais_render_stale

    with patch("worker.tasks._worker_redis", return_value=_redis_with_get(None)):
        assert asyncio.run(ais_render_stale("clip-1")) is True


def test_fresh_marker_reports_live():
    from worker.tasks import ais_render_stale

    with patch("worker.tasks._worker_redis", return_value=_redis_with_get(str(time.time()))):
        assert asyncio.run(ais_render_stale("clip-1")) is False


def test_old_marker_reports_stale():
    from worker.tasks import ais_render_stale, render_stale_after_s

    old = str(time.time() - render_stale_after_s() - 10)
    with patch("worker.tasks._worker_redis", return_value=_redis_with_get(old)):
        assert asyncio.run(ais_render_stale("clip-1")) is True


def test_redis_error_fails_closed_as_fresh():
    """A Redis outage must not trigger a duplicate-render storm."""
    import redis

    from worker.tasks import ais_render_stale

    with patch(
        "worker.tasks._worker_redis",
        return_value=_redis_with_get(redis.RedisError("down")),
    ):
        assert asyncio.run(ais_render_stale("clip-1")) is False


def test_stale_threshold_derived_from_celery_limits():
    """Threshold = soft limit + hard margin + sweep margin — never hardcoded."""
    from config import settings
    from worker.celery_app import HARD_LIMIT_MARGIN_S
    from worker.tasks import _RENDER_STALE_MARGIN_S, render_stale_after_s

    assert render_stale_after_s() == (
        settings.CELERY_SOFT_TIME_LIMIT_S + HARD_LIMIT_MARGIN_S + _RENDER_STALE_MARGIN_S
    )


# ── _sweep_stale_renders_async ────────────────────────────────────────────────


def test_sweep_skips_when_lock_not_acquired():
    from worker.tasks import _sweep_stale_renders_async

    session = _mock_session(lock_acquired=False)

    with patch("worker.tasks.db.AdminSessionLocal", lambda: _SessionCM(session)):
        asyncio.run(_sweep_stale_renders_async())

    # Only the lock attempt — no row query, no commit.
    assert session.execute.call_count == 1
    session.commit.assert_not_called()


def test_sweep_flips_only_stale_rows_to_failed():
    """Stale clip + stale summary flip to failed; a live clip is untouched."""
    from worker.tasks import _sweep_stale_renders_async

    stale_clip = _row()
    live_clip = _row()
    stale_summary = _row()
    session = _mock_session(clips=[stale_clip, live_clip], summaries=[stale_summary])

    async def _staleness(entity_id: str) -> bool:
        return entity_id != str(live_clip.id)

    with (
        patch("worker.tasks.db.AdminSessionLocal", lambda: _SessionCM(session)),
        patch("worker.tasks.ais_render_stale", side_effect=_staleness),
    ):
        asyncio.run(_sweep_stale_renders_async())

    assert stale_clip.render_status == RenderStatus.failed
    assert stale_summary.render_status == RenderStatus.failed
    assert live_clip.render_status == RenderStatus.running
    session.commit.assert_awaited_once()


def test_sweep_no_running_rows_is_a_noop():
    from worker.tasks import _sweep_stale_renders_async

    session = _mock_session(clips=[], summaries=[])

    with (
        patch("worker.tasks.db.AdminSessionLocal", lambda: _SessionCM(session)),
        patch("worker.tasks.ais_render_stale", AsyncMock(return_value=True)) as staleness,
    ):
        asyncio.run(_sweep_stale_renders_async())

    staleness.assert_not_called()
    session.commit.assert_awaited_once()


# ── Beat registration ─────────────────────────────────────────────────────────


def test_beat_schedule_registers_stale_render_sweep():
    import worker.schedule  # noqa: F401 — registers beat_schedule
    from worker.celery_app import celery

    assert "sweep-stale-renders" in celery.conf.beat_schedule
    entry = celery.conf.beat_schedule["sweep-stale-renders"]
    assert entry["task"] == "worker.tasks.sweep_stale_renders"
