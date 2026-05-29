"""
Tests for Issue 17 — source media purge + YouTube analytics refresh.
Covers: Beat schedule entries, purge task clears source_uri past retention window,
analytics refresh iterates creators, no-op when nothing to purge.

Also covers Issue 43: the retention clock is `ingest_done_at` (not `created_at`),
and `_signals_async` stamps it exactly once when ingest succeeds (idempotent
across retries — original completion time wins).
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from youtube.quota import QuotaExhaustedError

# ── Beat schedule ─────────────────────────────────────────────────────────────


def test_purge_beat_schedule_registered():
    import worker.schedule  # noqa: F401 — registers beat_schedule as side-effect
    from worker.celery_app import celery

    assert "purge-stale-source-media-hourly" in celery.conf.beat_schedule


def test_analytics_beat_schedule_registered():
    import worker.schedule  # noqa: F401
    from worker.celery_app import celery

    assert "refresh-youtube-analytics-daily" in celery.conf.beat_schedule


def test_purge_stale_analytics_beat_schedule_registered():
    import worker.schedule  # noqa: F401
    from worker.celery_app import celery

    assert "purge-stale-analytics-daily" in celery.conf.beat_schedule


def test_purge_stale_analytics_runs_daily():
    from celery.schedules import timedelta as ctd

    import worker.schedule  # noqa: F401
    from worker.celery_app import celery

    entry = celery.conf.beat_schedule["purge-stale-analytics-daily"]
    assert entry["schedule"] == ctd(hours=24)


def test_purge_beat_runs_hourly():
    from celery.schedules import timedelta as ctd

    import worker.schedule  # noqa: F401
    from worker.celery_app import celery

    entry = celery.conf.beat_schedule["purge-stale-source-media-hourly"]
    assert entry["schedule"] == ctd(hours=1)


def test_analytics_beat_runs_daily():
    from celery.schedules import timedelta as ctd

    import worker.schedule  # noqa: F401
    from worker.celery_app import celery

    entry = celery.conf.beat_schedule["refresh-youtube-analytics-daily"]
    assert entry["schedule"] == ctd(hours=24)


# ── purge_stale_source_media task ─────────────────────────────────────────────


def test_purge_task_registered():
    from worker.celery_app import celery

    assert "worker.tasks.purge_stale_source_media" in celery.tasks


def test_purge_task_calls_async_impl():
    with patch("worker.tasks.run_async") as mock_run:
        from worker.tasks import purge_stale_source_media

        purge_stale_source_media()
        mock_run.assert_called_once()


def test_purge_clears_source_uri_past_retention():
    """Videos older than retention window should have source_uri nulled.
    The retention clock is ingest_done_at (Issue 43), not created_at."""
    from worker.tasks import _purge_stale_source_media_async

    vid = MagicMock()
    vid.id = uuid.uuid4()
    vid.source_uri = "s3://bucket/source/vid.mp4"
    vid.ingest_done_at = datetime.now(UTC) - timedelta(hours=100)

    mock_result = MagicMock()
    mock_result.scalars.return_value = [vid]

    async def run():
        with (
            patch("db.AsyncSessionLocal") as mock_ctx,
            patch("worker.storage.delete_file") as mock_delete,
        ):
            session = AsyncMock()
            session.execute = AsyncMock(return_value=mock_result)
            session.commit = AsyncMock()
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            await _purge_stale_source_media_async()

            mock_delete.assert_called_once_with("s3://bucket/source/vid.mp4")
            assert vid.source_uri is None
            session.commit.assert_called_once()

    asyncio.run(run())


def test_purge_noop_when_nothing_stale():
    """When the DB query returns no rows, nothing is deleted or committed."""
    from worker.tasks import _purge_stale_source_media_async

    mock_result = MagicMock()
    mock_result.scalars.return_value = []

    async def run():
        with (
            patch("db.AsyncSessionLocal") as mock_ctx,
            patch("worker.storage.delete_file") as mock_delete,
        ):
            session = AsyncMock()
            session.execute = AsyncMock(return_value=mock_result)
            session.commit = AsyncMock()
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            await _purge_stale_source_media_async()

            mock_delete.assert_not_called()
            session.commit.assert_not_called()

    asyncio.run(run())


def test_purge_continues_on_delete_error():
    """A failed delete should log a warning but not abort processing remaining videos."""
    from worker.tasks import _purge_stale_source_media_async

    vid1 = MagicMock()
    vid1.id = uuid.uuid4()
    vid1.source_uri = "s3://bucket/bad.mp4"
    vid1.ingest_done_at = datetime.now(UTC) - timedelta(hours=200)

    vid2 = MagicMock()
    vid2.id = uuid.uuid4()
    vid2.source_uri = "s3://bucket/good.mp4"
    vid2.ingest_done_at = datetime.now(UTC) - timedelta(hours=200)

    mock_result = MagicMock()
    mock_result.scalars.return_value = [vid1, vid2]

    call_count = {"n": 0}

    def delete_side_effect(uri):
        call_count["n"] += 1
        if uri == "s3://bucket/bad.mp4":
            raise OSError("S3 error")

    async def run():
        with (
            patch("db.AsyncSessionLocal") as mock_ctx,
            patch("worker.storage.delete_file", side_effect=delete_side_effect),
        ):
            session = AsyncMock()
            session.execute = AsyncMock(return_value=mock_result)
            session.commit = AsyncMock()
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            await _purge_stale_source_media_async()

        assert call_count["n"] == 2
        assert vid2.source_uri is None

    asyncio.run(run())


def test_purge_filter_gates_on_ingest_done_at():
    """Issue 43: retention clock is ingest_done_at, NOT created_at. The purge
    query must filter on (ingest_done_at IS NOT NULL AND ingest_done_at < cutoff)
    so an in-progress ingest of an old upload is not purged mid-pipeline."""
    from worker.tasks import _purge_stale_source_media_async

    captured: dict = {}

    mock_result = MagicMock()
    mock_result.scalars.return_value = []

    async def capture(stmt):
        captured["stmt"] = stmt
        return mock_result

    async def run():
        with (
            patch("db.AsyncSessionLocal") as mock_ctx,
            patch("worker.storage.delete_file"),
        ):
            session = AsyncMock()
            session.execute = AsyncMock(side_effect=capture)
            session.commit = AsyncMock()
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            await _purge_stale_source_media_async()

    asyncio.run(run())

    where_sql = str(captured["stmt"].whereclause).lower()
    assert "ingest_done_at" in where_sql, where_sql
    assert "is not null" in where_sql, where_sql
    # The old clock (created_at) must NOT appear in the predicate.
    assert "videos.created_at" not in where_sql, where_sql


# ── _signals_async: ingest_done_at is stamped exactly once (Issue 43) ────────
#
# These tests exercise just the write block at the end of _signals_async by
# mocking the upstream pipeline (audio extraction + signal-timeline build) so
# the function reaches the Video update. The contract being pinned is:
#   - First completion sets ingest_done_at ~= now()
#   - Retry of a previously-completed task does NOT overwrite ingest_done_at
#     (otherwise Celery's at-least-once redelivery silently extends retention).


def _signals_async_runner(video, *, retention_rows=()):
    """Drive _signals_async with mocked session + ingestion deps. Returns the
    asyncio.run() coroutine — caller awaits inside its own loop."""
    from worker.tasks import _signals_async

    retention_result = MagicMock()
    retention_result.scalars.return_value = list(retention_rows)

    async def fake_get(model, _id):
        from models import Video as VideoModel

        if model is VideoModel:
            return video
        return None

    session = AsyncMock()
    session.get = AsyncMock(side_effect=fake_get)
    session.execute = AsyncMock(return_value=retention_result)
    session.add = MagicMock()
    session.commit = AsyncMock()

    class FakeLocalPath:
        def __init__(self, _uri):
            pass

        def __enter__(self):
            return "/tmp/fake.mp4"

        def __exit__(self, *exc):
            return False

    async def run():
        with (
            patch("db.AsyncSessionLocal") as mock_ctx,
            patch("worker.storage.local_path", FakeLocalPath),
            patch("ingestion.audio.extract_audio_events", return_value={}),
            patch("ingestion.signals.build_signal_timeline", return_value={}),
        ):
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            await _signals_async(str(video.id))

    return run(), session


def test_signals_async_stamps_ingest_done_at_when_null():
    from models import IngestStatus, Video

    video = MagicMock(spec=Video)
    video.id = uuid.uuid4()
    video.ingest_status = IngestStatus.running
    video.ingest_done_at = None
    video.source_uri = "s3://bucket/source.mp4"

    coro, session = _signals_async_runner(video)
    before = datetime.now(UTC)
    asyncio.run(coro)
    after = datetime.now(UTC)

    assert video.ingest_status == IngestStatus.done
    assert video.ingest_done_at is not None
    assert before <= video.ingest_done_at <= after
    session.commit.assert_awaited_once()


def test_signals_async_preserves_ingest_done_at_on_retry():
    from models import IngestStatus, Video

    original_stamp = datetime.now(UTC) - timedelta(hours=12)

    video = MagicMock(spec=Video)
    video.id = uuid.uuid4()
    video.ingest_status = IngestStatus.done
    video.ingest_done_at = original_stamp
    video.source_uri = "s3://bucket/source.mp4"

    coro, _ = _signals_async_runner(video)
    asyncio.run(coro)

    assert video.ingest_done_at == original_stamp


# ── refresh_youtube_analytics task ───────────────────────────────────────────


def test_analytics_task_registered():
    from worker.celery_app import celery

    assert "worker.tasks.refresh_youtube_analytics" in celery.tasks


def test_analytics_task_calls_async_impl():
    with patch("worker.tasks.run_async") as mock_run:
        from worker.tasks import refresh_youtube_analytics

        refresh_youtube_analytics()
        mock_run.assert_called_once()


def test_analytics_refresh_iterates_creators():
    """Each creator should get sync_video_analytics + sync_audience_data called."""
    from worker.tasks import _refresh_youtube_analytics_async

    creator = MagicMock()
    creator.id = uuid.uuid4()

    creators_result = MagicMock()
    creators_result.scalars.return_value = [creator]

    video = MagicMock()
    video.creator_id = creator.id

    videos_result = MagicMock()
    videos_result.scalars.return_value = [video]

    async def run():
        with (
            patch("db.AsyncSessionLocal") as mock_ctx,
            patch(
                "youtube.oauth.get_valid_access_token", new_callable=AsyncMock, return_value="tok"
            ),
            patch(
                "youtube.analytics.sync_video_analytics", new_callable=AsyncMock
            ) as mock_sync_vid,
            patch("youtube.analytics.sync_audience_data", new_callable=AsyncMock) as mock_sync_aud,
            patch("worker.tasks.remaining", new_callable=AsyncMock, return_value=5000),
        ):
            session = AsyncMock()
            session.execute = AsyncMock(side_effect=[creators_result, videos_result])
            session.commit = AsyncMock()
            session.rollback = AsyncMock()
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            await _refresh_youtube_analytics_async()

            mock_sync_vid.assert_called_once()
            mock_sync_aud.assert_called_once()
            session.commit.assert_called_once()

    asyncio.run(run())


def test_analytics_refresh_skips_creator_on_token_error():
    """If token fetch fails, that creator is skipped without aborting the run."""
    from worker.tasks import _refresh_youtube_analytics_async

    creator = MagicMock()
    creator.id = uuid.uuid4()

    creators_result = MagicMock()
    creators_result.scalars.return_value = [creator]

    async def run():
        with (
            patch("db.AsyncSessionLocal") as mock_ctx,
            patch(
                "youtube.oauth.get_valid_access_token",
                new_callable=AsyncMock,
                side_effect=Exception("token expired"),
            ),
            patch(
                "youtube.analytics.sync_video_analytics", new_callable=AsyncMock
            ) as mock_sync_vid,
            patch("youtube.analytics.sync_audience_data", new_callable=AsyncMock) as mock_sync_aud,
            patch("worker.tasks.remaining", new_callable=AsyncMock, return_value=5000),
        ):
            session = AsyncMock()
            session.execute = AsyncMock(return_value=creators_result)
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            await _refresh_youtube_analytics_async()

            mock_sync_vid.assert_not_called()
            mock_sync_aud.assert_not_called()

    asyncio.run(run())


# ── Issue 47: fairness across runs (last_analytics_refreshed_at) ─────────────


def test_analytics_refresh_orders_by_last_refreshed_nulls_first():
    """The Creator SELECT must ORDER BY last_analytics_refreshed_at NULLS FIRST,
    then id. Without this, the daily beat job starves creators past the cutoff
    index."""
    from worker.tasks import _refresh_youtube_analytics_async

    captured: dict = {}
    empty_result = MagicMock()
    empty_result.scalars.return_value = []

    async def capture(stmt):
        captured.setdefault("stmts", []).append(stmt)
        return empty_result

    async def run():
        with (
            patch("db.AsyncSessionLocal") as mock_ctx,
            patch("worker.tasks.remaining", new_callable=AsyncMock, return_value=5000),
        ):
            session = AsyncMock()
            session.execute = AsyncMock(side_effect=capture)
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            await _refresh_youtube_analytics_async()

    asyncio.run(run())

    creator_select = captured["stmts"][0]
    order_sql = " ".join(str(c) for c in creator_select._order_by_clauses).lower()
    assert "last_analytics_refreshed_at" in order_sql, order_sql
    assert "nulls first" in order_sql, order_sql
    # id is the deterministic tiebreak
    assert "creators.id" in order_sql, order_sql


def test_analytics_refresh_stamps_last_refreshed_on_success():
    """A successful per-creator refresh stamps creator.last_analytics_refreshed_at
    so the next run sees them at the back of the queue."""
    from worker.tasks import _refresh_youtube_analytics_async

    creator = MagicMock()
    creator.id = uuid.uuid4()
    creator.last_analytics_refreshed_at = None

    creators_result = MagicMock()
    creators_result.scalars.return_value = [creator]
    videos_result = MagicMock()
    videos_result.scalars.return_value = []

    before = datetime.now(UTC)

    async def run():
        with (
            patch("db.AsyncSessionLocal") as mock_ctx,
            patch(
                "youtube.oauth.get_valid_access_token", new_callable=AsyncMock, return_value="tok"
            ),
            patch("youtube.analytics.sync_video_analytics", new_callable=AsyncMock),
            patch("youtube.analytics.sync_audience_data", new_callable=AsyncMock),
            patch("worker.tasks.remaining", new_callable=AsyncMock, return_value=5000),
        ):
            session = AsyncMock()
            session.execute = AsyncMock(side_effect=[creators_result, videos_result])
            session.commit = AsyncMock()
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            await _refresh_youtube_analytics_async()

    asyncio.run(run())

    after = datetime.now(UTC)
    assert creator.last_analytics_refreshed_at is not None
    assert before <= creator.last_analytics_refreshed_at <= after


def test_analytics_refresh_does_not_stamp_on_quota_exhaustion():
    """If the creator's refresh raises QuotaExhaustedError, we rollback and the
    timestamp must NOT advance — otherwise the starved creator would silently
    drop to the back of the queue next run."""
    from worker.tasks import _refresh_youtube_analytics_async

    creator = MagicMock()
    creator.id = uuid.uuid4()
    creator.last_analytics_refreshed_at = None

    creators_result = MagicMock()
    creators_result.scalars.return_value = [creator]
    videos_result = MagicMock()
    videos_result.scalars.return_value = [MagicMock()]

    async def run():
        with (
            patch("db.AsyncSessionLocal") as mock_ctx,
            patch(
                "youtube.oauth.get_valid_access_token", new_callable=AsyncMock, return_value="tok"
            ),
            patch(
                "youtube.analytics.sync_video_analytics",
                new_callable=AsyncMock,
                side_effect=QuotaExhaustedError("daily cap hit"),
            ),
            patch("youtube.analytics.sync_audience_data", new_callable=AsyncMock),
            patch("worker.tasks.remaining", new_callable=AsyncMock, return_value=5000),
        ):
            session = AsyncMock()
            session.execute = AsyncMock(side_effect=[creators_result, videos_result])
            session.commit = AsyncMock()
            session.rollback = AsyncMock()
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            await _refresh_youtube_analytics_async()

            session.rollback.assert_awaited()

    asyncio.run(run())

    assert creator.last_analytics_refreshed_at is None
