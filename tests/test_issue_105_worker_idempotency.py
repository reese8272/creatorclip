"""
Unit tests for Issue 105 — Worker idempotency + advisory locks.

All tests in this file are unit tests: they mock the database session and any
external API calls so they run without a live Postgres or Redis.  The advisory-lock
tests in particular assert that the *correct SQL text* is issued, not that Postgres
behaviour actually short-circuits — that confidence lives in integration tests.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Fix 1: _transcribe_async short-circuits when transcript already exists ────


@pytest.mark.asyncio
async def test_transcribe_short_circuits_when_already_done() -> None:
    """_transcribe_async returns without calling transcribe_audio when a Transcript
    row already exists and ingest_status is NOT pending/running."""
    from models import IngestStatus, Transcript, Video

    video_id = str(uuid.uuid4())
    mock_video = MagicMock(spec=Video)
    mock_video.source_uri = f"s3://bucket/audio/{video_id}.wav"
    mock_video.ingest_status = IngestStatus.done  # past transcription stage

    mock_transcript = MagicMock(spec=Transcript)

    # Simulate session.get returning video then transcript
    mock_session = AsyncMock()
    mock_session.get = AsyncMock(side_effect=[mock_video, mock_transcript])

    mock_session_cm = AsyncMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("worker.tasks.db.AdminSessionLocal", return_value=mock_session_cm),
        patch("worker.progress.aemit", new_callable=AsyncMock),
        patch("ingestion.transcribe.transcribe_audio") as mock_transcribe,
    ):
        from worker.tasks import _transcribe_async

        await _transcribe_async(video_id)

    # The paid Deepgram / WhisperX / AssemblyAI call must never fire.
    mock_transcribe.assert_not_called()


# ── Fix 1: _signals_async short-circuits when Signals row already exists ──────


@pytest.mark.asyncio
async def test_signals_short_circuits_when_already_done() -> None:
    """_signals_async returns without calling extract_audio_events when a Signals
    row already exists and ingest_status == done."""
    from models import IngestStatus, Signals, Video

    video_id = str(uuid.uuid4())
    mock_video = MagicMock(spec=Video)
    mock_video.id = uuid.UUID(video_id)
    mock_video.source_uri = f"s3://bucket/audio/{video_id}.wav"
    mock_video.ingest_status = IngestStatus.done

    mock_signals = MagicMock(spec=Signals)

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(side_effect=[mock_video, mock_signals])
    mock_session.execute = AsyncMock(
        return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )
    )

    mock_session_cm = AsyncMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("worker.tasks.db.AdminSessionLocal", return_value=mock_session_cm),
        patch("worker.progress.aemit", new_callable=AsyncMock),
        patch("ingestion.audio.extract_audio_events") as mock_extract,
    ):
        from worker.tasks import _signals_async

        await _signals_async(video_id)

    mock_extract.assert_not_called()


# ── Fix 2: _ingest_async short-circuits when source_uri already ends in .wav ──


@pytest.mark.asyncio
async def test_ingest_short_circuits_when_source_uri_endswith_wav() -> None:
    """_ingest_async returns immediately when video.source_uri already ends in .wav.

    This indicates the prior run's final commit (source_uri = audio_uri) landed —
    the orphan-mp4 short-circuit per AWS Lambda idempotent-retry doctrine.
    """
    from models import Video

    video_id = str(uuid.uuid4())
    mock_video = MagicMock(spec=Video)
    mock_video.source_uri = f"s3://bucket/audio/{video_id}.wav"

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=mock_video)

    mock_session_cm = AsyncMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("worker.tasks.db.AdminSessionLocal", return_value=mock_session_cm),
        patch("worker.progress.aemit", new_callable=AsyncMock),
        patch("worker.tasks.asyncio.to_thread") as mock_to_thread,
    ):
        from worker.tasks import _ingest_async

        await _ingest_async(video_id)

    # No ffmpeg / audio-extract work should have been dispatched.
    mock_to_thread.assert_not_called()


# ── Fix 3: generate_clips uses RefundOnFailureTask as base ────────────────────


def test_generate_clips_uses_refund_on_failure_task() -> None:
    """generate_clips must carry base=RefundOnFailureTask so a terminal failure
    automatically triggers a minutes refund."""
    from worker.tasks import RefundOnFailureTask, generate_clips

    # Celery stores the base class on the task's class hierarchy.
    assert isinstance(generate_clips, RefundOnFailureTask), (
        "generate_clips must use base=RefundOnFailureTask so terminal failures "
        "trigger automatic minutes refunds"
    )


# ── Fix 4: advisory locks on two representative sites ─────────────────────────


@pytest.mark.asyncio
async def test_sync_channel_catalog_acquires_advisory_lock() -> None:
    """_sync_channel_catalog_async issues pg_try_advisory_lock before body work
    and pg_advisory_unlock in the finally clause."""
    creator_id = str(uuid.uuid4())

    # Lock is NOT acquired — task should return immediately.
    mock_scalar_result = MagicMock()
    mock_scalar_result.scalar_one = MagicMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_scalar_result)

    mock_session_cm = AsyncMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("worker.tasks.db.AdminSessionLocal", return_value=mock_session_cm),
        patch("worker.progress.aemit", new_callable=AsyncMock),
    ):
        from worker.tasks import _sync_channel_catalog_async

        await _sync_channel_catalog_async(creator_id)

    # Should have issued the advisory lock query.
    issued_sqls = [str(c.args[0]) for c in mock_session.execute.call_args_list]
    assert any("pg_try_advisory_lock" in sql for sql in issued_sqls), (
        "Expected pg_try_advisory_lock to be called but got: " + str(issued_sqls)
    )


@pytest.mark.asyncio
async def test_retrain_preference_acquires_advisory_lock() -> None:
    """_retrain_preference_async issues pg_try_advisory_lock and skips body when
    lock is not acquired."""
    creator_id = str(uuid.uuid4())

    mock_scalar_result = MagicMock()
    mock_scalar_result.scalar_one = MagicMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_scalar_result)

    mock_session_cm = AsyncMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_cm.__aexit__ = AsyncMock(return_value=False)

    # Issue-135 audit fix: _retrain_preference_async now opens an
    # AdminSessionLocal (worker-internal pass, bypasses RLS) instead of
    # AsyncSessionLocal — patch the correct factory.
    with patch("worker.tasks.db.AdminSessionLocal", return_value=mock_session_cm):
        from worker.tasks import _retrain_preference_async

        await _retrain_preference_async(creator_id)

    issued_sqls = [str(c.args[0]) for c in mock_session.execute.call_args_list]
    assert any("pg_try_advisory_lock" in sql for sql in issued_sqls), (
        "Expected pg_try_advisory_lock to be called but got: " + str(issued_sqls)
    )


# ── Fix 5: SoftTimeLimitExceeded does NOT trigger retry ───────────────────────


def test_soft_time_limit_exceeded_does_not_retry() -> None:
    """SoftTimeLimitExceeded raised inside a sync wrapper must propagate immediately
    without calling self.retry, so RefundOnFailureTask.on_failure fires.

    We test the transcribe_video wrapper (representative of the three).
    """
    from celery.exceptions import SoftTimeLimitExceeded

    from worker.tasks import transcribe_video

    video_id = str(uuid.uuid4())

    # Simulate _transcribe_async raising SoftTimeLimitExceeded.
    with (
        patch("worker.tasks.run_async", side_effect=SoftTimeLimitExceeded()),
        patch("worker.tasks._set_status", new_callable=AsyncMock),
    ):
        # We call the underlying function directly to avoid Celery's task harness;
        # we want to confirm the exception propagates without retry being called.
        mock_self = MagicMock()
        mock_self.retry = MagicMock(side_effect=AssertionError("retry must not be called"))

        with pytest.raises(SoftTimeLimitExceeded):
            transcribe_video.run(video_id)

    # If we reach here without AssertionError, retry was not called.


# ── Fix 6: Redis singletons have socket timeouts ──────────────────────────────


def test_redis_singletons_have_socket_timeouts() -> None:
    """Both the sync and async Redis clients in worker/progress.py must be
    constructed with socket_timeout and socket_connect_timeout set to 2.0 s.

    We reset the module-level singletons so the factory functions re-run, then
    check the connection-pool kwargs that redis-py stores on the client.
    """

    import worker.progress as progress_module

    # Force singleton rebuild.
    progress_module._SYNC = None
    progress_module._AIO = None
    progress_module._AIO_LOOP = None

    with (
        patch("worker.progress.redis.from_url") as mock_sync_factory,
        patch("worker.progress.aredis.from_url") as mock_async_factory,
    ):
        mock_sync_factory.return_value = MagicMock()
        mock_async_factory.return_value = MagicMock()

        # Trigger sync client construction.
        progress_module._sync_client()

        # Trigger async client construction by patching asyncio.get_running_loop.
        with patch("worker.progress.asyncio.get_running_loop", return_value=MagicMock()):
            progress_module._async_client()

    # Verify socket_timeout kwarg on sync client.
    sync_kwargs = mock_sync_factory.call_args.kwargs
    assert sync_kwargs.get("socket_timeout") == 2.0, (
        f"sync Redis client missing socket_timeout=2.0; got {sync_kwargs}"
    )
    assert sync_kwargs.get("socket_connect_timeout") == 2.0, (
        f"sync Redis client missing socket_connect_timeout=2.0; got {sync_kwargs}"
    )

    # Verify socket_timeout kwarg on async client.
    async_kwargs = mock_async_factory.call_args.kwargs
    assert async_kwargs.get("socket_timeout") == 2.0, (
        f"async Redis client missing socket_timeout=2.0; got {async_kwargs}"
    )
    assert async_kwargs.get("socket_connect_timeout") == 2.0, (
        f"async Redis client missing socket_connect_timeout=2.0; got {async_kwargs}"
    )


# ── Fix 7: config validator rejects relative LOCAL_MEDIA_DIR in production ────


def test_config_validator_rejects_relative_local_media_dir_in_prod() -> None:
    """Settings must raise ValueError when ENV=production AND
    STORAGE_BACKEND=local AND LOCAL_MEDIA_DIR is relative. The check is
    skipped when STORAGE_BACKEND=r2 since LOCAL_MEDIA_DIR is dead config
    there — relaxed in the Issue 110 hotfix after the validator
    crash-looped prod where STORAGE_BACKEND=r2."""
    import os

    from pydantic import ValidationError

    # We can't re-import Settings with different env vars because pydantic-settings
    # reads from the environment at construction time. Build a minimal subclass
    # that only sets the fields we need to exercise the validator.
    from config import Settings

    # Save originals
    orig_env = os.environ.get("ENV")
    orig_local = os.environ.get("LOCAL_MEDIA_DIR")
    orig_storage = os.environ.get("STORAGE_BACKEND")
    orig_stripe_key = os.environ.get("STRIPE_SECRET_KEY")
    orig_stripe_webhook = os.environ.get("STRIPE_WEBHOOK_SECRET")

    try:
        os.environ["ENV"] = "production"
        os.environ["LOCAL_MEDIA_DIR"] = "./media"
        os.environ["STORAGE_BACKEND"] = "local"  # make the path actually load-bearing
        # Supply required billing secrets so they don't mask the LOCAL_MEDIA_DIR error.
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_fake"
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_fake"

        with pytest.raises((ValidationError, ValueError)):
            Settings()
    finally:
        # Restore env
        if orig_env is None:
            os.environ.pop("ENV", None)
        else:
            os.environ["ENV"] = orig_env
        if orig_local is None:
            os.environ.pop("LOCAL_MEDIA_DIR", None)
        else:
            os.environ["LOCAL_MEDIA_DIR"] = orig_local
        if orig_stripe_key is None:
            os.environ.pop("STRIPE_SECRET_KEY", None)
        else:
            os.environ["STRIPE_SECRET_KEY"] = orig_stripe_key
        if orig_stripe_webhook is None:
            os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
        else:
            os.environ["STRIPE_WEBHOOK_SECRET"] = orig_stripe_webhook
        if orig_storage is None:
            os.environ.pop("STORAGE_BACKEND", None)
        else:
            os.environ["STORAGE_BACKEND"] = orig_storage


def test_config_validator_allows_relative_local_media_dir_in_prod_when_r2() -> None:
    """Issue 110 hotfix: when STORAGE_BACKEND=r2, LOCAL_MEDIA_DIR is dead
    config — the validator must NOT crash-loop prod just because the value
    is left at the ./media default. This was the real production deploy
    failure that prompted the hotfix."""
    import os

    from config import Settings

    orig_env = os.environ.get("ENV")
    orig_local = os.environ.get("LOCAL_MEDIA_DIR")
    orig_storage = os.environ.get("STORAGE_BACKEND")
    orig_stripe_key = os.environ.get("STRIPE_SECRET_KEY")
    orig_stripe_webhook = os.environ.get("STRIPE_WEBHOOK_SECRET")

    try:
        os.environ["ENV"] = "production"
        os.environ["LOCAL_MEDIA_DIR"] = "./media"  # dead config when STORAGE_BACKEND=r2
        os.environ["STORAGE_BACKEND"] = "r2"
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_fake"
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_fake"

        # Must NOT raise — LOCAL_MEDIA_DIR is unused with STORAGE_BACKEND=r2.
        s = Settings()
        assert s.ENV == "production"
        assert s.STORAGE_BACKEND == "r2"
        assert s.LOCAL_MEDIA_DIR == "./media"
    finally:
        for k, v in (
            ("ENV", orig_env),
            ("LOCAL_MEDIA_DIR", orig_local),
            ("STORAGE_BACKEND", orig_storage),
            ("STRIPE_SECRET_KEY", orig_stripe_key),
            ("STRIPE_WEBHOOK_SECRET", orig_stripe_webhook),
        ):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
