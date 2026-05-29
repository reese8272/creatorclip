"""
Tests for Issue 17 — source media purge + YouTube analytics refresh.
Covers: Beat schedule entries, purge task clears source_uri past retention window,
analytics refresh iterates creators, no-op when nothing to purge.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

# ── Beat schedule ─────────────────────────────────────────────────────────────


def test_purge_beat_schedule_registered():
    import worker.schedule  # noqa: F401 — registers beat_schedule as side-effect
    from worker.celery_app import celery

    assert "purge-stale-source-media-hourly" in celery.conf.beat_schedule


def test_analytics_beat_schedule_registered():
    import worker.schedule  # noqa: F401
    from worker.celery_app import celery

    assert "refresh-youtube-analytics-daily" in celery.conf.beat_schedule


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
    """Videos older than retention window should have source_uri nulled."""
    from worker.tasks import _purge_stale_source_media_async

    vid = MagicMock()
    vid.id = uuid.uuid4()
    vid.source_uri = "s3://bucket/source/vid.mp4"
    vid.created_at = datetime.now(UTC) - timedelta(hours=100)

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
    vid1.created_at = datetime.now(UTC) - timedelta(hours=200)

    vid2 = MagicMock()
    vid2.id = uuid.uuid4()
    vid2.source_uri = "s3://bucket/good.mp4"
    vid2.created_at = datetime.now(UTC) - timedelta(hours=200)

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
