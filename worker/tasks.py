"""
Celery pipeline tasks: ingest_video → transcribe_video → build_signals → build_dna.

Design: each task accepts only a UUID string — never large payloads —
so Redis messages stay small. Each task is idempotent and safe to retry.
run_async() dispatches each coroutine onto the worker-process singleton loop
installed by worker_process_init (Issue 39), so the SQLAlchemy async engine
pool stays bound to a single loop across task invocations.
"""

import asyncio
import logging
import tempfile
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import redis.asyncio as aredis
from celery import Task
from celery.exceptions import SoftTimeLimitExceeded

import db
from models import (
    Clip,
    ClipFeedback,
    ClipOutcome,
    Creator,
    CreatorDna,
    IngestStatus,
    OnboardingState,
    PreferenceModel,
    RenderStatus,
    RetentionCurve,
    Signals,
    Transcript,
    Video,
    VideoKind,
    VideoMetrics,
)
from worker.celery_app import celery, run_async
from youtube.errors import YouTubeAuthError
from youtube.quota import QuotaExhaustedError, remaining

logger = logging.getLogger(__name__)

# Module-level async Redis singleton for the thumbnail-patterns cache.
# Mirrors the pattern in worker/progress.py::_async_client().
_THUMB_REDIS: aredis.Redis | None = None


def _thumb_redis() -> aredis.Redis:
    global _THUMB_REDIS
    if _THUMB_REDIS is None:
        from config import settings as _s

        _THUMB_REDIS = aredis.from_url(
            _s.REDIS_URL,
            decode_responses=True,
            socket_timeout=2.0,
            socket_connect_timeout=2.0,
        )
    return _THUMB_REDIS


# ── Public entry points ───────────────────────────────────────────────────────


def start_pipeline(video_id: str) -> None:
    """Kick off the ingest → transcribe → signals chain."""
    (ingest_video.s(video_id) | transcribe_video.s() | build_signals.s()).apply_async()


# ── Refund-on-terminal-failure base class (Issue 57) ──────────────────────────


class RefundOnFailureTask(Task):
    """Celery Task base that auto-refunds deducted minutes on terminal failure.

    `on_failure` fires only when retries are exhausted (Celery does NOT call it
    on intermediate `Retry` exceptions). The refund helper is idempotent on
    `pack_id=refund:<video_id>` so a duplicate on_failure invocation is safe.
    """

    def on_failure(
        self,
        exc: BaseException,
        task_id: str,
        args: Sequence[Any],
        kwargs: dict[str, Any],
        einfo: Any,
    ) -> None:
        from billing.refund import refund_for_video

        video_id_raw = args[0] if args else kwargs.get("video_id")
        if not video_id_raw:
            return
        try:
            video_uuid = uuid.UUID(str(video_id_raw))
        except (ValueError, TypeError):
            return
        try:
            run_async(refund_for_video(video_uuid))
        except Exception as refund_exc:
            # Refund must never crash the failure path — log and let the
            # task's terminal failure stand. Manual recovery is supported via
            # the same refund_for_video helper.
            logger.warning(
                "Auto-refund failed for video %s (task %s): %s",
                video_uuid,
                task_id,
                refund_exc,
            )


# ── Tasks ─────────────────────────────────────────────────────────────────────


@celery.task(
    base=RefundOnFailureTask,
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name="worker.tasks.ingest_video",
)
def ingest_video(self, video_id: str) -> str:
    try:
        run_async(_ingest_async(video_id))
    except SoftTimeLimitExceeded:
        # Soft limit means we've already burned ~50 min. Do NOT retry —
        # the next delivery would time out again, wasting credits. Re-raise
        # so RefundOnFailureTask.on_failure fires immediately on terminal failure.
        raise
    except Exception as exc:
        run_async(_set_status(video_id, IngestStatus.failed))
        raise self.retry(exc=exc) from exc
    return video_id


@celery.task(
    base=RefundOnFailureTask,
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name="worker.tasks.transcribe_video",
)
def transcribe_video(self, video_id: str) -> str:
    try:
        run_async(_transcribe_async(video_id))
    except SoftTimeLimitExceeded:
        # See ingest_video above — re-raise immediately to fire on_failure.
        raise
    except Exception as exc:
        run_async(_set_status(video_id, IngestStatus.failed))
        raise self.retry(exc=exc) from exc
    return video_id


@celery.task(
    base=RefundOnFailureTask,
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name="worker.tasks.build_signals",
)
def build_signals(self, video_id: str) -> str:
    try:
        run_async(_signals_async(video_id))
    except SoftTimeLimitExceeded:
        # See ingest_video above — re-raise immediately to fire on_failure.
        raise
    except Exception as exc:
        run_async(_set_status(video_id, IngestStatus.failed))
        raise self.retry(exc=exc) from exc
    generate_clips.delay(video_id)
    return video_id


@celery.task(
    base=RefundOnFailureTask,
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    name="worker.tasks.generate_clips",
)
def generate_clips(self, video_id: str) -> str:
    """Score and rank clip candidates for a fully-ingested video."""
    try:
        run_async(_generate_clips_async(video_id))
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    return video_id


@celery.task(bind=True, max_retries=3, default_retry_delay=60, name="worker.tasks.render_clip")
def render_clip(self, clip_id: str) -> str:
    """Render a clip to 9:16 and upload to storage."""
    try:
        run_async(_render_clip_async(clip_id))
    except Exception as exc:
        run_async(_set_clip_render_status(clip_id, RenderStatus.failed))
        raise self.retry(exc=exc) from exc
    return clip_id


@celery.task(bind=True, max_retries=2, default_retry_delay=60, name="worker.tasks.clean_clip")
def clean_clip(self, clip_id: str) -> str:
    """Re-render a clip with filler words + long silences removed (Issue 134)."""
    try:
        run_async(_clean_clip_async(clip_id))
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    return clip_id


@celery.task(bind=True, max_retries=2, default_retry_delay=60, name="worker.tasks.edit_clip")
def edit_clip(self, clip_id: str, cut_segments: list[list[float]]) -> str:
    """Re-render a clip with user-supplied transcript-editor cuts (Issue 135).

    ``cut_segments`` is a JSON-serialisable list of ``[start_s, end_s]`` pairs
    in clip-relative seconds. The endpoint pre-validates via
    ``clip_engine.edits.validate_user_cuts``; the worker re-validates as a
    defensive belt-and-suspenders pass (a Celery redelivery from a buggy
    older endpoint version cannot land an invalid graph on ffmpeg).
    """
    try:
        run_async(_edit_clip_async(clip_id, cut_segments))
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    return clip_id


@celery.task(name="worker.tasks.purge_stale_source_media")
def purge_stale_source_media() -> None:
    """
    Celery Beat task: delete source video files older than SOURCE_MEDIA_RETENTION_HOURS
    and null their source_uri, complying with the YouTube API data retention policy.
    """
    run_async(_purge_stale_source_media_async())


@celery.task(name="worker.tasks.purge_stale_youtube_analytics")
def purge_stale_youtube_analytics() -> None:
    """Wave-4 Fix 3 (Issue 75b) — Celery Beat task.

    Delete YouTube analytics rows whose ``fetched_at`` exceeds
    ``YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS`` (default 30) — the hard maximum
    set by YouTube API Services Developer Policies §III.E.4.b + §III.D.2.3.b.
    When a creator's daily refresh stops succeeding (OAuth revoked, quota
    exhausted, etc.) ``fetched_at`` stops advancing; the partial-staleness
    purge deletes rows past the cutoff. Required for OAuth app verification.
    Source: https://developers.google.com/youtube/terms/developer-policies
    """
    run_async(_purge_stale_youtube_analytics_async())


@celery.task(name="worker.tasks.expire_trials")
def expire_trials() -> None:
    """Issue 126 — daily observability sweep for trial expirations.

    Watchdog only: logs creators whose `trial_ends_at` just crossed into the
    past AND whose minutes_balance is zero, so funnel drop-off is visible in
    structured logs without a new dashboard. Does NOT mutate any state — the
    402 paywall in billing/ledger.py reads `trial_ends_at` live, so a flag
    here would be a second source of truth that could disagree.
    """
    run_async(_expire_trials_async())


@celery.task(name="worker.tasks.refresh_youtube_analytics")
def refresh_youtube_analytics() -> None:
    """
    Celery Beat task: re-fetch video_metrics and audience_activity for all creators
    with valid tokens. Keeps analytics fresh per YouTube API ToS (no indefinite caching).
    """
    run_async(_refresh_youtube_analytics_async())


@celery.task(
    bind=True, max_retries=3, default_retry_delay=60, name="worker.tasks.sync_channel_catalog"
)
def sync_channel_catalog(self, creator_id: str) -> str:
    """Pull the creator's uploads playlist into the videos table (Issue 87).

    Idempotent: the underlying sync_video_catalog skips existing
    (creator_id, youtube_video_id) rows. YouTubeAuthError is terminal
    (token revoked — surfaces the row deletion via the existing refresh
    path); transient errors retry.

    Issue 92: passes ``self.request.id`` as the SSE stream key so the
    catalog-sync UI can subscribe to per-video metric progress.
    """
    try:
        run_async(_sync_channel_catalog_async(creator_id, task_id=self.request.id))
    except YouTubeAuthError:
        # Permanent — the token is dead. Don't retry; the next refresh tick
        # will delete the YoutubeToken row via the existing handler.
        raise
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    return creator_id


@celery.task(name="worker.tasks.poll_clip_outcomes")
def poll_clip_outcomes() -> None:
    """
    Celery Beat task: fetch YouTube stats for published clips at 48h and 7d checkpoints.
    Sets performed_well = views >= channel_median_views; used as a 3× weight multiplier
    in preference model retraining.
    """
    run_async(_poll_clip_outcomes_async())


@celery.task(bind=True, max_retries=3, default_retry_delay=60, name="worker.tasks.build_dna")
def build_dna(self, creator_id: str) -> str:
    """
    Build creator DNA patterns, generate brief, store draft profile + embeddings.
    ValueError (data gate failure) is re-raised without retry — it is a permanent error.
    """
    try:
        # self.request.id is the stable idempotency key across at-least-once
        # redelivery of THIS task; a new user-triggered build gets a new id. (Issue 63)
        run_async(_build_dna_async(creator_id, self.request.id))
    except ValueError:
        raise
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    return creator_id


@celery.task(
    bind=True, max_retries=3, default_retry_delay=60, name="worker.tasks.retrain_preference"
)
def retrain_preference(self, creator_id: str) -> str:
    """Retrain the creator's preference model from their clip feedback (Issue 60).

    Idempotent + self-debouncing: a no-op when no new trainable feedback has arrived
    since the latest model version, so repeated feedback clicks collapse to cheap
    no-ops. The version-assignment race is hardened in Issue 71.
    """
    try:
        run_async(_retrain_preference_async(creator_id))
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    return creator_id


# ── Async implementations ─────────────────────────────────────────────────────


async def _retrain_preference_async(creator_id: str) -> None:
    from sqlalchemy import func, select, text
    from sqlalchemy.exc import IntegrityError

    from preference.train import TRAINABLE_ACTIONS, build_and_save

    cid = uuid.UUID(creator_id)
    # Audit fix (Issue-135 audit, scale-checklist D): the retrain reads
    # ClipFeedback rows for a single creator. Use AdminSessionLocal so the
    # RLS `after_begin` listener (which sets `app.creator_id` from
    # session.info) doesn't NULL the predicate and silently fit an empty
    # model. Worker-internal task — admin role is correct here.
    async with db.AdminSessionLocal() as session:
        # Advisory lock (Issue 105 — Fix 4): non-blocking variant so a concurrent
        # or redelivered task returns immediately rather than queueing behind a
        # stuck prior run. Template: _build_dna_async uses the xact variant;
        # here we use the session-scoped non-transactional lock (pg_try_advisory_lock)
        # with an explicit unlock in the finally clause.
        lock_key = f"retrain:{cid}"
        acquired = (
            await session.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:k))"), {"k": lock_key}
            )
        ).scalar_one()
        if not acquired:
            logger.info("advisory lock held — skipping retrain_preference for creator %s", cid)
            return
        try:
            latest = (
                (
                    await session.execute(
                        select(PreferenceModel)
                        .where(PreferenceModel.creator_id == cid)
                        .order_by(PreferenceModel.version.desc())
                    )
                )
                .scalars()
                .first()
            )
            if latest is not None:
                # Self-debounce: only retrain if trainable feedback arrived since the
                # last model was saved. Repeated clicks otherwise collapse to no-ops.
                new_labels = (
                    await session.execute(
                        select(func.count())
                        .select_from(ClipFeedback)
                        .where(
                            ClipFeedback.creator_id == cid,
                            ClipFeedback.action.in_(TRAINABLE_ACTIONS),
                            ClipFeedback.created_at > latest.updated_at,
                        )
                    )
                ).scalar_one()
                if not new_labels:
                    logger.info("retrain_preference: no new feedback for creator %s, skip", cid)
                    return
            try:
                await build_and_save(session, cid)
            except IntegrityError:
                # Concurrent retrain won the version race (hardened in Issue 71).
                await session.rollback()
                logger.info("retrain_preference: version race for creator %s, skip", cid)
        finally:
            await session.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": lock_key})


async def _set_status(video_id: str, status: IngestStatus) -> None:
    async with db.AdminSessionLocal() as session:
        video = await session.get(Video, uuid.UUID(video_id))
        if video:
            video.ingest_status = status
            await session.commit()


async def _ingest_async(video_id: str) -> None:
    """Ingest stage of the upload chain.

    Progress (Issue 92): emits step events to ``task:{video_id}:events``.
    The frontend subscribes via ``/tasks/{video_id}/events`` — using the
    video_id as the SSE stream key keeps the client lookup deterministic
    across the chain (no need to pipe a Celery chain id through every
    stage; the client already knows the video_id from the upload response).
    """
    from worker.progress import aemit
    from worker.storage import alocal_path, aupload_file
    from youtube.ingest import extract_audio_wav

    try:
        await aemit(video_id, "step", label="ingest_start", stage="ingest")

        async with db.AdminSessionLocal() as session:
            video = await session.get(Video, uuid.UUID(video_id))
            if not video:
                raise ValueError(f"Video {video_id} not found")
            if not video.source_uri:
                raise ValueError(f"Video {video_id} has no source_uri — upload the file first")

            # Idempotency short-circuit (Issue 105 — Fix 2): if source_uri ends in
            # ".wav" the prior run already completed the audio-extract + upload step
            # and committed the WAV key. Returning here is safe because the final
            # DB commit (source_uri = audio_uri) is the durable progress marker per
            # AWS Lambda's idempotent-retry doctrine — make progress persistent and
            # detectable, skip work that is already persisted.
            if video.source_uri.endswith(".wav"):
                logger.info(
                    "Video %s source_uri already points to WAV — ingest already completed",
                    video_id,
                )
                await aemit(
                    video_id,
                    "step",
                    label="ingest_skipped",
                    stage="ingest",
                    reason="already_done",
                )
                return

            source_uri = video.source_uri
            # Capture the original blob key BEFORE we overwrite source_uri with
            # the audio key on the final commit. After commit, the original mp4
            # has no SQL row pointing at it — `_purge_stale_source_media_async`
            # iterates `Video.source_uri` to find purgeables and would never
            # see the orphan. Delete it explicitly post-commit. Guard with the
            # `source/` prefix + `.mp4` suffix because the Issue-105 `.wav`
            # short-circuit at function entry should already prevent a retry
            # from re-entering with audio_uri here, but the prefix check is
            # the canonical retry-safe shape (AWS Lambda idempotent-retry
            # doctrine) in case a future ingest path skips the short-circuit.
            # (Issue 110 — closes the Issue-105 misread on first-run orphan;
            # R2 bucket lifecycle on `source/` is the documented
            # belt-and-suspenders, see DECISIONS.)
            prior_source_uri = source_uri
            video.ingest_status = IngestStatus.running
            await session.commit()

        duration_s: float | None = None
        async with alocal_path(source_uri) as src:
            from youtube.ingest import probe_duration_s

            # Offload sync subprocess + ffmpeg + boto3 work to a worker thread so the
            # event loop is not blocked for the duration of the call (Issue 38 Wave 1).
            await aemit(video_id, "step", label="probe_duration", stage="ingest")
            duration_s = await asyncio.to_thread(probe_duration_s, src)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                wav_path = Path(tmp.name)
            try:
                await aemit(
                    video_id,
                    "step",
                    label="extract_audio",
                    stage="ingest",
                    duration_s=duration_s,
                )
                await asyncio.to_thread(extract_audio_wav, src, wav_path)
                await aemit(video_id, "step", label="upload_audio", stage="ingest")
                audio_uri = await aupload_file(wav_path, f"audio/{video_id}.wav")
            finally:
                wav_path.unlink(missing_ok=True)

        async with db.AdminSessionLocal() as session:
            video = await session.get(Video, uuid.UUID(video_id))
            if video:
                video.source_uri = audio_uri
                if duration_s and not video.duration_s:
                    video.duration_s = duration_s
                if duration_s:
                    from billing.ledger import deduct_for_video

                    await aemit(video_id, "step", label="deduct_minutes", stage="ingest")
                    await deduct_for_video(video.id, video.creator_id, duration_s, session)
                await session.commit()

        # Post-commit orphan cleanup: the original mp4 is now unreferenced.
        # Prefix guard makes this safe under Celery redelivery — only fires
        # when prior_source_uri is the expected `source/...mp4` shape.
        # adelete_file is idempotent (no-op on missing key), so a crash
        # between commit and this call leaks an orphan that the R2
        # `source/` lifecycle rule eventually sweeps (belt-and-suspenders).
        # (Issue 110)
        if prior_source_uri.startswith("source/") and prior_source_uri.endswith(".mp4"):
            from worker.storage import adelete_file

            try:
                await adelete_file(prior_source_uri)
            except Exception as exc:  # noqa: BLE001 — best-effort cleanup
                logger.warning(
                    "_ingest_async: prior-mp4 cleanup failed video=%s uri=%s err=%s",
                    video_id,
                    prior_source_uri,
                    type(exc).__name__,
                )
    except Exception as exc:
        # Safe message — exception args may carry internal detail. The
        # generic shape matches the data-gate emit policy in _build_dna_async.
        await aemit(
            video_id,
            "error",
            stage="ingest",
            message="Ingest failed; retrying.",
            exc_type=type(exc).__name__,
        )
        raise


async def _transcribe_async(video_id: str) -> None:
    """Transcribe stage of the upload chain. (Issue 92 progress wired.)"""
    from config import settings
    from ingestion.transcribe import transcribe_audio
    from worker.progress import aemit
    from worker.storage import alocal_path

    try:
        await aemit(video_id, "step", label="transcribe_start", stage="transcribe")

        async with db.AdminSessionLocal() as session:
            video = await session.get(Video, uuid.UUID(video_id))
            if not video or not video.source_uri:
                raise ValueError(f"Video {video_id} not ready for transcription")

            # Idempotency probe (Issue 105 — Fix 1): if a Transcript row already
            # exists AND the video status is past the transcription stage, a
            # redelivered task is a no-op. Mirrors the render_clip pattern
            # (worker/tasks.py:562-570). Sidekiq + Celery + Stripe canonical pattern:
            # status-column check-then-skip at task entry.
            existing_transcript = await session.get(Transcript, uuid.UUID(video_id))
            if existing_transcript is not None and video.ingest_status not in (
                IngestStatus.pending,
                IngestStatus.running,
            ):
                logger.info(
                    "Transcript already exists for video %s (status=%s) — skipping",
                    video_id,
                    video.ingest_status,
                )
                await aemit(
                    video_id,
                    "step",
                    label="transcribe_skipped",
                    stage="transcribe",
                    reason="already_done",
                )
                return

            source_uri = video.source_uri

        async with alocal_path(source_uri) as audio_path:
            # transcribe_audio dispatches to sync Deepgram / AssemblyAI / WhisperX
            # SDKs — offload to a thread so the event loop is free during the
            # multi-second transcription round-trip (Issue 38 Wave 1). Bounded by
            # TRANSCRIPTION_TIMEOUT_S so a hung provider fails (→ retry) instead
            # of stalling forever (Issue 68).
            await aemit(
                video_id,
                "step",
                label="transcribe_audio",
                stage="transcribe",
                backend=settings.TRANSCRIPTION_BACKEND,
            )
            result = await asyncio.wait_for(
                asyncio.to_thread(transcribe_audio, str(audio_path)),
                timeout=settings.TRANSCRIPTION_TIMEOUT_S,
            )

        await aemit(
            video_id,
            "step",
            label="store_transcript",
            stage="transcribe",
            segment_count=len(result.get("segments", [])) if isinstance(result, dict) else 0,
        )
        async with db.AdminSessionLocal() as session:
            existing = await session.get(Transcript, uuid.UUID(video_id))
            if existing:
                existing.source = result["source"]
                existing.segments_jsonb = result
            else:
                session.add(
                    Transcript(
                        video_id=uuid.UUID(video_id),
                        source=result["source"],
                        segments_jsonb=result,
                    )
                )
            await session.commit()
    except Exception as exc:
        await aemit(
            video_id,
            "error",
            stage="transcribe",
            message="Transcription failed; retrying.",
            exc_type=type(exc).__name__,
        )
        raise


async def _signals_async(video_id: str) -> None:
    """Final stage of the upload chain. Emits the terminal ``done`` event."""
    from sqlalchemy import select

    from ingestion.audio import extract_audio_events
    from ingestion.signals import build_signal_timeline
    from worker.progress import aemit
    from worker.storage import alocal_path

    try:
        await aemit(video_id, "step", label="signals_start", stage="signals")

        async with db.AdminSessionLocal() as session:
            video = await session.get(Video, uuid.UUID(video_id))
            if not video or not video.source_uri:
                raise ValueError(f"Video {video_id} not ready for signal extraction")

            # Idempotency probe (Issue 105 — Fix 1): if a Signals row already exists
            # AND the video is marked done, a redelivered task is a no-op. Mirrors
            # the render_clip and _transcribe_async patterns.
            existing_signals = await session.get(Signals, uuid.UUID(video_id))
            if existing_signals is not None and video.ingest_status == IngestStatus.done:
                logger.info(
                    "Signals already exist for video %s (status=done) — skipping",
                    video_id,
                )
                await aemit(
                    video_id,
                    "step",
                    label="signals_skipped",
                    stage="signals",
                    reason="already_done",
                )
                return

            source_uri = video.source_uri
            retention_result = await session.execute(
                select(RetentionCurve).where(RetentionCurve.video_id == video.id)
            )
            retention_points = list(retention_result.scalars())

        async with alocal_path(source_uri) as audio_path:
            # extract_audio_events is librosa-backed (sync, CPU + IO heavy).
            # Offload so the event loop stays responsive (Issue 38 Wave 1).
            await aemit(video_id, "step", label="extract_audio_events", stage="signals")
            audio_events = await asyncio.to_thread(extract_audio_events, str(audio_path))

        await aemit(video_id, "step", label="build_timeline", stage="signals")
        timeline = build_signal_timeline(audio_events, retention_points)

        async with db.AdminSessionLocal() as session:
            existing = await session.get(Signals, uuid.UUID(video_id))
            if existing:
                existing.timeline_jsonb = timeline
            else:
                session.add(Signals(video_id=uuid.UUID(video_id), timeline_jsonb=timeline))
            video = await session.get(Video, uuid.UUID(video_id))
            if video:
                video.ingest_status = IngestStatus.done
                if video.ingest_done_at is None:
                    video.ingest_done_at = datetime.now(UTC)
            await session.commit()

        # Wave-3 Fix E: NON-terminal — the upload pipeline isn't done yet,
        # `generate_clips.delay(video_id)` is enqueued by the sync wrapper
        # AFTER this function returns. Previously this emitted a terminal
        # `done` event here, so the UI closed the SSE while clips were
        # still being prepared. `_generate_clips_async` now emits its own
        # terminal `done` on the same stream key so the consumer stays
        # subscribed through clip generation.
        await aemit(video_id, "step", label="ingest_complete", stage="signals")
    except Exception as exc:
        await aemit(
            video_id,
            "error",
            stage="signals",
            message="Signal extraction failed; retrying.",
            exc_type=type(exc).__name__,
        )
        raise


async def _set_clip_render_status(clip_id: str, status: RenderStatus) -> None:
    async with db.AdminSessionLocal() as session:
        clip = await session.get(Clip, uuid.UUID(clip_id))
        if clip:
            clip.render_status = status
            await session.commit()


def _render_start_for(clip: Clip) -> float:
    """The timestamp the clip is rendered from.

    Render from the computed setup boundary (CLIPPING_PRINCIPLE #2 — "clip the
    setup, not the aftermath"), NOT the fixed peak−window `start_s` fallback;
    scoring, the API, and the eval all key on setup_start_s, so the rendered bytes
    must match. setup_start_s is nullable — fall back to the start_s clamp only if
    it was never computed, so a legacy/edge clip still renders a valid range. (Issue 59)
    """
    return clip.setup_start_s if clip.setup_start_s is not None else clip.start_s


async def _render_clip_async(clip_id: str) -> None:
    """Render a clip. Issue 92 progress wired — uses ``clip_id`` as the
    SSE stream key for the same deterministic-lookup reason as the upload
    chain. Per-frame ffmpeg progress is intentionally NOT parsed here (the
    encode runs as a single ``asyncio.to_thread`` shell-out); we emit
    step-level boundaries instead — start/encode/upload/done.
    """
    from clip_engine.render import render_clip_file
    from worker.progress import aemit
    from worker.storage import alocal_path, aupload_file

    try:
        await aemit(clip_id, "step", label="render_start", stage="render")

        async with db.AdminSessionLocal() as session:
            clip = await session.get(Clip, uuid.UUID(clip_id))
            if not clip:
                raise ValueError(f"Clip {clip_id} not found")
            # Idempotent under at-least-once delivery (Issue 62): a redelivered render
            # must not re-encode and last-writer-win the URI. Skip if already done.
            if clip.render_status == RenderStatus.done and clip.render_uri:
                logger.info("Clip %s already rendered — skipping", clip_id)
                await aemit(
                    clip_id,
                    "done",
                    stage="render",
                    message="Clip already rendered.",
                )
                return
            video = await session.get(Video, clip.video_id)
            if not video or not video.source_uri:
                raise ValueError(f"Source video not available for clip {clip_id}")
            source_uri = video.source_uri
            # Snapshot the timing fields into locals — session closes at the end of
            # this with-block, after which `clip.start_s` would emit an implicit
            # SELECT to refresh the expired attribute (Issue 38 Wave 1).
            setup_start_s = clip.setup_start_s
            start_s = clip.start_s
            end_s = clip.end_s
            clip_duration_s = end_s - (setup_start_s if setup_start_s is not None else start_s)
            clip.render_status = RenderStatus.running
            style_preset = clip.style_preset  # snapshot before session closes
            # Transcript segments only needed when style_preset selects an
            # animated caption style — skip the load otherwise (Issue 133).
            transcript_segments: list[dict] | None = None
            if style_preset and style_preset.get("subtitle") in {
                "bold_pop",
                "gradient_slide",
                "minimal",
            }:
                transcript = await session.get(Transcript, video.id)
                if transcript and isinstance(transcript.segments_jsonb, dict):
                    segments = transcript.segments_jsonb.get("segments")
                    if isinstance(segments, list):
                        transcript_segments = segments
            await session.commit()

        await aemit(clip_id, "step", label="download_source", stage="render")
        async with alocal_path(source_uri) as src:
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                out_path = Path(tmp.name)
            try:
                # render_clip_file shells out to ffmpeg via subprocess.run; upload_file
                # is sync boto3. Both go to a worker thread so the event loop stays
                # free during the multi-second render + upload (Issue 38 Wave 1).
                # Render from the computed setup boundary (CLIPPING_PRINCIPLE #2 —
                # "clip the setup, not the aftermath"), NOT the fixed peak−window
                # `start_s` fallback. setup_start_s is nullable; fall back to start_s
                # only if it was never computed so a legacy clip still renders a
                # valid range. (Issue 59)
                await aemit(
                    clip_id,
                    "step",
                    label="ffmpeg_encode",
                    stage="render",
                    clip_duration_s=clip_duration_s,
                )
                await asyncio.to_thread(
                    render_clip_file,
                    source_path=src,
                    start_s=setup_start_s if setup_start_s is not None else start_s,
                    end_s=end_s,
                    out_path=out_path,
                    style_preset=style_preset,
                    transcript_segments=transcript_segments,
                )
                await aemit(clip_id, "step", label="upload_r2", stage="render")
                render_uri = await aupload_file(out_path, f"clips/{clip_id}.mp4")
            finally:
                out_path.unlink(missing_ok=True)

        async with db.AdminSessionLocal() as session:
            clip = await session.get(Clip, uuid.UUID(clip_id))
            if clip:
                clip.render_uri = render_uri
                clip.render_status = RenderStatus.done
                await session.commit()

        logger.info("Clip %s rendered → %s", clip_id, render_uri)
        await aemit(clip_id, "done", stage="render", message="Clip ready.")
    except Exception as exc:
        await aemit(
            clip_id,
            "error",
            stage="render",
            message="Render failed; retrying.",
            exc_type=type(exc).__name__,
        )
        raise


async def _clean_clip_async(clip_id: str) -> None:
    """Re-render a clip with filler words + long silences excised (Issue 134).

    Source is the existing ``render_uri`` (the burned-in, captioned clip), not
    the original video — keeps animated captions intact and reuses the
    already-paid encode of the speaker reframe. Output goes to a sibling R2
    key (``clips/{id}_clean.mp4``); ``Clip.cleaned_render_uri`` is set so the
    UI can offer the cleaned version side-by-side with the original until the
    creator hits ``POST /clean/confirm``.

    Idempotent under at-least-once delivery: a redelivery whose
    ``cleaned_render_uri`` is already populated short-circuits without
    re-encoding.
    """
    from clip_engine.filler import (
        detect_cut_segments,
        invert_to_keep_ranges,
        merge_adjacent_cuts,
    )
    from clip_engine.render import render_cleaned_clip_file
    from worker.progress import aemit
    from worker.storage import alocal_path, aupload_file

    try:
        await aemit(clip_id, "step", label="clean_start", stage="clean")

        async with db.AdminSessionLocal() as session:
            clip = await session.get(Clip, uuid.UUID(clip_id))
            if not clip:
                raise ValueError(f"Clip {clip_id} not found")
            if clip.cleaned_render_uri:
                logger.info("Clip %s already cleaned — skipping", clip_id)
                await aemit(clip_id, "done", stage="clean", message="Already cleaned.")
                return
            if not clip.render_uri:
                raise ValueError(f"Clip {clip_id} has no render_uri — render before cleaning")
            source_uri = clip.render_uri
            video_id = clip.video_id
            setup_start_s = clip.setup_start_s
            start_s = clip.start_s
            end_s = clip.end_s
            clip_origin_s = setup_start_s if setup_start_s is not None else start_s
            clip_duration_s = end_s - clip_origin_s

        async with db.AdminSessionLocal() as session:
            transcript = await session.get(Transcript, video_id)
            if not transcript or not isinstance(transcript.segments_jsonb, dict):
                raise ValueError(f"Clip {clip_id}: transcript missing")
            segments = transcript.segments_jsonb.get("segments") or []

        # Flatten segment-level words and shift to clip-relative timebase. The
        # rendered clip starts at t=0 — but our transcript stores video-absolute
        # word times, so we subtract clip_origin_s.
        words_clip_relative: list[dict] = []
        for seg in segments:
            for w in seg.get("words") or []:
                w_start = float(w.get("start", 0.0))
                w_end = float(w.get("end", 0.0))
                if w_end <= clip_origin_s or w_start >= end_s:
                    continue
                words_clip_relative.append(
                    {
                        "word": w.get("word", ""),
                        "start": w_start - clip_origin_s,
                        "end": w_end - clip_origin_s,
                    }
                )

        from config import settings as _s

        cuts = detect_cut_segments(
            words_clip_relative,
            clip_start_s=0.0,
            clip_end_s=clip_duration_s,
            silence_threshold_ms=_s.SILENCE_REMOVAL_THRESHOLD_MS,
            silence_tail_ms=_s.SILENCE_TAIL_MS,
            flank_gap_ms=_s.FILLER_TIER2_FLANK_GAP_MS,
            tier2_max_duration_ms=_s.FILLER_TIER2_MAX_DURATION_MS,
        )
        merged = merge_adjacent_cuts(cuts)
        keep_ranges = invert_to_keep_ranges(merged, 0.0, clip_duration_s)
        if not keep_ranges or (len(keep_ranges) == 1 and keep_ranges[0] == (0.0, clip_duration_s)):
            # Nothing to cut — surface as a no-op done event rather than failing.
            logger.info("Clip %s: no cuts detected — skipping clean render", clip_id)
            await aemit(
                clip_id,
                "done",
                stage="clean",
                message="No filler words or long silences detected.",
            )
            return

        await aemit(
            clip_id,
            "step",
            label="download_source",
            stage="clean",
        )
        async with alocal_path(source_uri) as src:
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                out_path = Path(tmp.name)
            try:
                await aemit(
                    clip_id,
                    "step",
                    label="ffmpeg_clean",
                    stage="clean",
                    segments=len(keep_ranges),
                )
                await asyncio.to_thread(
                    render_cleaned_clip_file,
                    source_path=src,
                    keep_ranges=keep_ranges,
                    out_path=out_path,
                )
                await aemit(clip_id, "step", label="upload_r2", stage="clean")
                cleaned_uri = await aupload_file(out_path, f"clips/{clip_id}_clean.mp4")
            finally:
                out_path.unlink(missing_ok=True)

        async with db.AdminSessionLocal() as session:
            clip = await session.get(Clip, uuid.UUID(clip_id))
            if clip:
                clip.cleaned_render_uri = cleaned_uri
                await session.commit()

        logger.info("Clip %s cleaned → %s", clip_id, cleaned_uri)
        await aemit(clip_id, "done", stage="clean", message="Clean ready.")
    except Exception as exc:
        await aemit(
            clip_id,
            "error",
            stage="clean",
            message="Clean failed.",
            exc_type=type(exc).__name__,
        )
        raise


async def _edit_clip_async(clip_id: str, cut_segments: list[list[float]]) -> None:
    """Re-render a clip with user-selected transcript cuts (Issue 135).

    Mirrors ``_clean_clip_async`` but takes its cut list from the caller
    instead of running filler detection. Reuses ``cleaned_render_uri`` so
    the same ``POST /clips/{id}/clean/confirm`` swap path applies.
    Idempotent: a redelivered task whose ``cleaned_render_uri`` is already
    populated short-circuits without re-encoding.
    """
    from clip_engine.edits import validate_user_cuts
    from clip_engine.render import render_cleaned_clip_file
    from worker.progress import aemit
    from worker.storage import alocal_path, aupload_file

    try:
        await aemit(clip_id, "step", label="edit_start", stage="edit")

        async with db.AdminSessionLocal() as session:
            clip = await session.get(Clip, uuid.UUID(clip_id))
            if not clip:
                raise ValueError(f"Clip {clip_id} not found")
            if clip.cleaned_render_uri:
                logger.info(
                    "Clip %s already has a pending cleaned/edited render — skipping", clip_id
                )
                await aemit(clip_id, "done", stage="edit", message="Already edited.")
                return
            if not clip.render_uri:
                raise ValueError(f"Clip {clip_id} has no render_uri — render before editing")
            source_uri = clip.render_uri
            setup_start_s = clip.setup_start_s
            start_s = clip.start_s
            end_s = clip.end_s
            clip_origin_s = setup_start_s if setup_start_s is not None else start_s
            clip_duration_s = end_s - clip_origin_s

        # Defensive re-validation. The endpoint already validated; this guards
        # against a redelivery from a buggy older endpoint version.
        edit = validate_user_cuts(
            [(float(s[0]), float(s[1])) for s in cut_segments],
            clip_duration_s=clip_duration_s,
        )

        await aemit(clip_id, "step", label="download_source", stage="edit")
        async with alocal_path(source_uri) as src:
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                out_path = Path(tmp.name)
            try:
                await aemit(
                    clip_id,
                    "step",
                    label="ffmpeg_edit",
                    stage="edit",
                    keep_segments=len(edit.keep_ranges),
                )
                await asyncio.to_thread(
                    render_cleaned_clip_file,
                    source_path=src,
                    keep_ranges=edit.keep_ranges,
                    out_path=out_path,
                )
                await aemit(clip_id, "step", label="upload_r2", stage="edit")
                edited_uri = await aupload_file(out_path, f"clips/{clip_id}_edit.mp4")
            finally:
                out_path.unlink(missing_ok=True)

        async with db.AdminSessionLocal() as session:
            clip = await session.get(Clip, uuid.UUID(clip_id))
            if clip:
                clip.cleaned_render_uri = edited_uri
                await session.commit()

        logger.info("Clip %s edited → %s", clip_id, edited_uri)
        await aemit(clip_id, "done", stage="edit", message="Edit ready.")
    except Exception as exc:
        await aemit(
            clip_id,
            "error",
            stage="edit",
            message="Edit failed.",
            exc_type=type(exc).__name__,
        )
        raise


async def _build_dna_async(creator_id: str, job_id: str | None = None) -> None:
    """Build creator DNA patterns, brief, draft profile, and embeddings atomically.

    All DB writes — draft INSERT, onboarding state update, and embedding INSERTs —
    occur inside a single transaction committed at the end. This prevents orphan draft
    rows on Celery retry: if the Voyage embedding call or any subsequent write fails,
    the session is rolled back (via the context manager exit) and no draft row is
    persisted. The next retry therefore computes max(version) without an orphan row and
    assigns the same version number again.

    Idempotent across at-least-once redelivery (Issue 63 + Issue 76): ``job_id`` is the
    Celery task id, stamped onto the draft (build_job_id). A per-creator
    ``pg_advisory_xact_lock`` serializes concurrent builds, and the idempotency key is
    re-checked UNDER that lock — so a redelivery of the same task id (serial *or*
    concurrent) short-circuits BEFORE the paid Anthropic brief and Voyage embedding
    calls, and costs nothing. The partial UNIQUE on ``build_job_id`` (migration 0008)
    is the structural backstop.
    """
    from sqlalchemy import select, text
    from sqlalchemy.exc import IntegrityError

    from dna.brief import generate_brief
    from dna.builder import build_patterns
    from dna.embeddings import embed_brief, embed_patterns
    from dna.identity import format_for_prompt, get_current
    from dna.profile import create_draft
    from worker.progress import aemit

    creator_uuid = uuid.UUID(creator_id)

    # Emit progress events ONLY when we have a job_id to scope them to. The
    # job_id is the Celery task id — the SSE endpoint at /tasks/{id}/events
    # tails the stream at task:{job_id}:events. With no job_id (direct unit-
    # test invocations of _build_dna_async) there's no subscriber and emitting
    # would just litter Redis with orphan streams.
    progress_enabled = job_id is not None

    async def _emit(event_type: str, **fields: object) -> None:
        if progress_enabled:
            await aemit(job_id, event_type, **fields)  # type: ignore[arg-type]

    try:
        async with db.AdminSessionLocal() as session:
            await _emit("step", label="acquire_lock")
            # Serialize concurrent builds for this creator. The xact-scoped advisory lock
            # is held until commit/rollback, so a concurrent same-job redelivery blocks
            # here, then re-reads the committed draft below and short-circuits before any
            # paid Anthropic/Voyage call — closing the double-spend race the bare
            # check-then-act left open (Issue 76). Mirrors preference/train.py.
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                {"k": str(creator_uuid)},
            )

            # Re-check the idempotency key UNDER the lock: same-task redelivery (serial
            # or concurrent) is now a no-op that costs nothing.
            if job_id is not None:
                already = await session.scalar(
                    select(CreatorDna.id).where(CreatorDna.build_job_id == job_id)
                )
                if already is not None:
                    logger.info(
                        "DNA build for job %s already completed — skipping (idempotent)",
                        job_id,
                    )
                    await _emit("done", reason="idempotent_skip")
                    return

            creator = await session.get(Creator, creator_uuid)
            if not creator:
                raise ValueError(f"Creator {creator_id} not found")
            channel_title = creator.channel_title or "Unknown Channel"

            await _emit("step", label="analyze_patterns")
            (
                patterns,
                top_ids,
                bottom_ids,
                clip_len_s,
                source_region,
                upload_gap_h,
            ) = await build_patterns(session, creator_uuid)
            await _emit(
                "step",
                label="analyzed_patterns",
                long_videos=patterns.get("long_videos_analyzed", 0),
                shorts=patterns.get("shorts_analyzed", 0),
                top_count=len(top_ids),
                bottom_count=len(bottom_ids),
            )

            # Fetch the creator's stated identity (Issue 83) and render it as a
            # stable system block to inject ahead of the volatile performance corpus.
            # Returns None if the creator hasn't filled the intake yet — brief.py
            # then skips the block entirely (cleaner prompt + better cache hit-rate
            # than passing "(no identity)").
            identity_row = await get_current(session, creator_uuid)
            stated_identity = format_for_prompt(identity_row)

            await _emit("step", label="call_claude")
            # generate_brief switches to the streaming path internally when a
            # task_id is provided (Issue 86) — emits `cache` + `token` (+ future
            # `thinking`) events as the LLM call progresses. Passing task_id=None
            # keeps the legacy .create() path for unit-test callers that mock
            # this function and any internal invocation without a job_id.
            brief_text = await asyncio.to_thread(
                generate_brief,
                patterns,
                channel_title,
                stated_identity,
                job_id if progress_enabled else None,
            )

            # Stage the draft row without committing.  commit=False keeps the INSERT
            # pending in this transaction so all writes land atomically below.
            dna = await create_draft(
                session,
                creator_id=creator_uuid,
                patterns=patterns,
                top_video_ids=top_ids,
                bottom_video_ids=bottom_ids,
                brief_text=brief_text,
                optimal_clip_len_s=clip_len_s,
                best_source_region=source_region,
                optimal_upload_gap_h=upload_gap_h,
                build_job_id=job_id,
                commit=False,
            )

            # Stage onboarding state update in the same transaction.
            if creator.onboarding_state == OnboardingState.awaiting_data:
                creator.onboarding_state = OnboardingState.dna_pending

            await _emit("step", label="embed")
            # Stage embedding rows without committing — all writes flush in the single
            # commit below.  Both helpers accept commit=False for exactly this purpose.
            await embed_patterns(session, creator_uuid, patterns, commit=False)
            await embed_brief(session, creator_uuid, brief_text, commit=False)

            # Single atomic commit: draft row + onboarding state + all embeddings.
            # Under the advisory lock above a build_job_id collision cannot occur, but
            # if the lock path is ever bypassed the partial UNIQUE (migration 0008)
            # raises here — treat that lost race as the idempotent no-op, not a
            # spurious retry.
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                logger.info(
                    "DNA build for job %s collided on commit — already built (idempotent)",
                    job_id,
                )
                await _emit("done", reason="idempotency_collision")
                return
            await session.refresh(dna)
            logger.info(
                "DNA draft v%d built for creator %s (%s)",
                dna.version,
                creator_id,
                channel_title,
            )
            await _emit("done", version=dna.version, brief_chars=len(brief_text or ""))
    except ValueError as exc:
        # Data-gate failures (e.g. "0 long videos") are permanent — emit a
        # human-safe error and re-raise so the caller's data-gate handling
        # remains intact (ValueError bypasses retry by design).
        await _emit("error", message=str(exc))
        raise
    except Exception as exc:
        # Anything else: emit a generic error so the UI stops spinning, then
        # re-raise so Celery's retry logic kicks in. Never leak exc args into
        # the UI — they may carry stack traces or token-shaped data.
        await _emit("error", message="DNA build failed; retrying")
        logger.error("DNA build for job %s failed: %s", job_id, exc)
        raise


async def _poll_clip_outcomes_async() -> None:
    """
    Find published clips past the 48h or 7d checkpoints, fetch their YouTube stats,
    and set performed_well = views >= channel_median_views for that creator.
    """
    import statistics
    from collections import defaultdict
    from datetime import datetime, timedelta

    from sqlalchemy import and_, or_, select, text

    from youtube.data_api import get_video_stats
    from youtube.oauth import get_valid_access_token

    now = datetime.now(UTC)
    cutoff_48h = now - timedelta(hours=48)
    cutoff_7d = now - timedelta(days=7)
    # Bound the candidate set: a clip's measurement lifecycle is the 48h + 7d
    # checkpoints, so nothing created >10 days ago should still be polled. Combined
    # with `final`, this stops the unbounded quota drain. (Issue 70 — supersedes
    # the looser 30-day floor explored in local Issue 46 before the timelines met.)
    cutoff_created = now - timedelta(days=10)

    async with db.AdminSessionLocal() as session:
        # Advisory lock (Issue 105 — Fix 4): global Beat task — only one instance
        # should run at a time. Non-blocking so a slow prior run doesn't queue.
        acquired = (
            await session.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:k))"),
                {"k": "poll_clip_outcomes"},
            )
        ).scalar_one()
        if not acquired:
            logger.info("advisory lock held — skipping poll_clip_outcomes")
            return
        try:
            result = await session.execute(
                select(ClipOutcome, Clip)
                .join(Clip, Clip.id == ClipOutcome.clip_id)
                .where(
                    ClipOutcome.published_youtube_id.isnot(None),
                    ClipOutcome.final.is_(False),  # never re-poll a finalized outcome
                    Clip.created_at >= cutoff_created,
                    or_(
                        and_(
                            ClipOutcome.performed_well.is_(None),
                            ClipOutcome.fetched_at < cutoff_48h,
                        ),
                        ClipOutcome.fetched_at < cutoff_7d,
                    ),
                )
            )
            rows = result.all()

            if not rows:
                return

            by_creator: dict[uuid.UUID, list[ClipOutcome]] = defaultdict(list)
            for outcome, clip in rows:
                by_creator[clip.creator_id].append(outcome)

            for creator_id, outcomes in by_creator.items():
                try:
                    access_token = await get_valid_access_token(creator_id, session)
                except Exception as exc:
                    logger.warning("Cannot get token for creator %s: %s", creator_id, exc)
                    continue

                views_result = await session.execute(
                    select(VideoMetrics.views)
                    .join(Video, Video.id == VideoMetrics.video_id)
                    .where(Video.creator_id == creator_id, VideoMetrics.views.isnot(None))
                )
                all_views = [r[0] for r in views_result.all()]
                channel_median = statistics.median(all_views) if all_views else 0

                for outcome in outcomes:
                    # Whether this row qualified via the 7d (terminal) checkpoint —
                    # captured BEFORE we overwrite fetched_at. (Issue 70)
                    is_terminal_poll = outcome.fetched_at < cutoff_7d
                    try:
                        stats = await get_video_stats(access_token, outcome.published_youtube_id)
                    except Exception as exc:
                        logger.warning(
                            "Stats fetch failed for clip %s (yt=%s): %s",
                            outcome.clip_id,
                            outcome.published_youtube_id,
                            exc,
                        )
                        continue
                    views = stats.get("views")
                    if views is not None:
                        outcome.views = views
                        outcome.performed_well = views >= channel_median
                    outcome.fetched_at = now
                    if is_terminal_poll:
                        # 7d checkpoint recorded — never poll this outcome again.
                        outcome.final = True
                    logger.info(
                        "ClipOutcome clip=%s views=%s performed_well=%s final=%s",
                        outcome.clip_id,
                        views,
                        outcome.performed_well,
                        outcome.final,
                    )

                # Commit per creator so a slow YouTube call can't hold one transaction
                # across the whole batch, and partial progress survives a mid-batch failure.
                await session.commit()
        finally:
            # Roll back first: if the body aborted (e.g. a per-creator commit raised),
            # the session is in a failed-transaction state and the unlock execute would
            # itself raise — silently *leaking* the session-level advisory lock and
            # blocking every future poll on this pooled backend. Issue 143.
            await session.rollback()
            await session.execute(
                text("SELECT pg_advisory_unlock(hashtext(:k))"),
                {"k": "poll_clip_outcomes"},
            )


async def _generate_clips_async(video_id: str) -> None:
    """Generate ranked clip candidates for a fully-ingested video.

    Wave-3 Fix E: emits progress events on the **same** ``task:{video_id}:events``
    stream that the upload chain (`_ingest`/`_transcribe`/`_signals`) uses, so
    the SSE consumer stays subscribed through clip generation. The terminal
    ``done`` event now fires here (not in `_signals_async`) — that's the
    moment the user-visible work is actually complete.
    """
    from sqlalchemy import select

    from clip_engine.ranking import generate_and_rank_clips
    from config import settings
    from dna.profile import get_active
    from models import Signals, Transcript
    from worker.progress import aemit

    try:
        await aemit(video_id, "step", label="generate_clips_start", stage="generate_clips")

        video_uuid = uuid.UUID(video_id)

        async with db.AdminSessionLocal() as session:
            video = await session.get(Video, video_uuid)
            if not video:
                raise ValueError(f"Video {video_id} not found")

            # Idempotency guard (Issue 46): a late retry on a video whose clips are
            # already rendered is a no-op. Without this, generate_and_rank_clips would
            # re-extract candidates and insert duplicate pending rows alongside the
            # already-done clips.
            existing_done = await session.scalar(
                select(Clip.id)
                .where(Clip.video_id == video_uuid, Clip.render_status == RenderStatus.done)
                .limit(1)
            )
            if existing_done is not None:
                logger.info(
                    "Skipping generate_clips for video %s — rendered clips already exist",
                    video_id,
                )
                # Terminal `done` still fires so the SSE consumer auto-closes.
                await aemit(
                    video_id,
                    "done",
                    stage="generate_clips",
                    message="Clips already generated.",
                )
                return

            signals = await session.get(Signals, video_uuid)
            if not signals:
                raise ValueError(f"Signals not available for video {video_id}")

            transcript = await session.get(Transcript, video_uuid)
            transcript_segments = (
                transcript.segments_jsonb.get("segments", []) if transcript else []
            )

            dna_profile = await get_active(session, video.creator_id)
            dna_brief = dna_profile.brief_text if dna_profile else None

            await aemit(video_id, "step", label="score_and_rank", stage="generate_clips")
            clips = await generate_and_rank_clips(
                session=session,
                video_id=video_uuid,
                creator_id=video.creator_id,
                timeline=signals.timeline_jsonb,
                dna_brief=dna_brief,
                transcript_segments=transcript_segments,
                max_candidates=settings.CLIPS_PER_VIDEO_DEFAULT,
            )

            logger.info("Generated %d clips for video %s", len(clips), video_id)

        # Terminal event — the upload-to-clips pipeline is now done.
        # Sets TTL on the stream so a creator who comes back later can still see it.
        await aemit(
            video_id,
            "done",
            stage="generate_clips",
            message=f"Generated {len(clips)} clip(s).",
            clip_count=len(clips),
        )
    except Exception as exc:
        await aemit(
            video_id,
            "error",
            stage="generate_clips",
            message="Clip generation failed; retrying.",
            exc_type=type(exc).__name__,
        )
        raise


async def _purge_stale_source_media_async() -> None:
    from datetime import timedelta

    from sqlalchemy import and_, select, text, update

    from config import settings
    from worker.storage import adelete_file

    # Retention clock starts at ingest completion, not upload time (Issue 43).
    # A long-running or stuck ingest of an old upload must not have its source
    # purged mid-pipeline — gate on ingest_done_at instead of created_at.
    cutoff = datetime.now(UTC) - timedelta(hours=settings.SOURCE_MEDIA_RETENTION_HOURS)

    # Issue 38 Wave 1: collect URIs in a short read transaction, release the
    # session during the sync boto3 delete loop (now offloaded via to_thread),
    # then reopen a short write transaction to null source_uri. Previously the
    # session was held across every delete_file call — N round-trips to R2
    # pinned a DB connection for the entire sweep.
    async with db.AdminSessionLocal() as session:
        # Advisory lock (Issue 105 — Fix 4): global Beat task.
        acquired = (
            await session.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:k))"),
                {"k": "purge_stale_source_media"},
            )
        ).scalar_one()
        if not acquired:
            logger.info("advisory lock held — skipping purge_stale_source_media")
            return
        try:
            result = await session.execute(
                select(Video.id, Video.source_uri).where(
                    and_(
                        Video.source_uri.isnot(None),
                        Video.ingest_done_at.is_not(None),
                        Video.ingest_done_at < cutoff,
                    )
                )
            )
            # `.all()` returns Sequence[Row[...]] which is Row-iterable and
            # unpacks to (uuid, str|None) per row. Untyped because Row's
            # bracketed type doesn't equal `tuple[...]` in the eyes of mypy.
            # The WHERE clause filters `source_uri.isnot(None)`; the loop below
            # still skips defensive None so type-narrowing stays trivial.
            targets = result.all()
        finally:
            await session.execute(
                text("SELECT pg_advisory_unlock(hashtext(:k))"),
                {"k": "purge_stale_source_media"},
            )

    if not targets:
        return

    purged_ids: list[uuid.UUID] = []
    for video_id, source_uri in targets:
        if source_uri is None:
            continue
        try:
            await adelete_file(source_uri)
            purged_ids.append(video_id)
        except Exception as exc:
            logger.warning("Failed to purge source media for video %s: %s", video_id, exc)

    if not purged_ids:
        return

    async with db.AdminSessionLocal() as session:
        await session.execute(update(Video).where(Video.id.in_(purged_ids)).values(source_uri=None))
        await session.commit()
        logger.info("Purged source media for %d video(s)", len(purged_ids))


async def _purge_stale_youtube_analytics_async() -> None:
    """Delete YouTube analytics rows past the ToS retention cutoff.

    Per YouTube API Services Developer Policies §III.E.4.b: API clients must
    re-verify authorization every 30 calendar days OR delete the stored
    data. ``fetched_at`` is the natural proxy — if the daily Beat refresh
    fails for any reason (token revoked, quota exhausted, transient outage
    >30d) the row's fetched_at stops advancing and falls past the cutoff.

    Purges in four tables in one short transaction:
      - ``video_metrics`` (per-video metrics)
      - ``retention_curves`` (per-video; written in lock-step with
        VideoMetrics by ``youtube/analytics.py::sync_video_analytics``, so
        we delete RetentionCurve rows for videos whose VideoMetrics is
        being purged)
      - ``audience_activity`` (per-creator, per day-of-week × hour)
      - ``demographics`` (per-creator aggregate)

    Cascades: ``retention_curves.video_id`` has ON DELETE CASCADE on
    ``videos`` but we do NOT delete Video rows here (the Video record itself
    is the creator's data, not YouTube API data). We explicitly delete
    RetentionCurve rows for the affected video_ids.

    Idempotent: runs to no-op when there are no stale rows. Safe to call
    repeatedly; safe to call concurrently (DELETE WHERE fetched_at < cutoff
    is the same query each time).
    """
    from datetime import timedelta

    from sqlalchemy import delete, select, text

    from config import settings

    cutoff = datetime.now(UTC) - timedelta(days=settings.YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS)

    async with db.AdminSessionLocal() as session:
        # Advisory lock (Issue 105 — Fix 4): global Beat task.
        acquired = (
            await session.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:k))"),
                {"k": "purge_stale_youtube_analytics"},
            )
        ).scalar_one()
        if not acquired:
            logger.info("advisory lock held — skipping purge_stale_youtube_analytics")
            return
        try:
            # VideoMetrics — collect video_ids first so we can cascade
            # RetentionCurve deletion in the same transaction.
            stale_video_ids = (
                (
                    await session.execute(
                        select(VideoMetrics.video_id).where(VideoMetrics.fetched_at < cutoff)
                    )
                )
                .scalars()
                .all()
            )

            deleted_metrics = 0
            deleted_curves = 0
            if stale_video_ids:
                # RetentionCurve is written in lock-step with VideoMetrics so its
                # staleness equals its parent VideoMetrics's staleness. The FK
                # cascade only fires on Video deletion (which we do NOT do here).
                r = await session.execute(
                    delete(RetentionCurve).where(RetentionCurve.video_id.in_(stale_video_ids))
                )
                deleted_curves = r.rowcount or 0
                r = await session.execute(
                    delete(VideoMetrics).where(VideoMetrics.video_id.in_(stale_video_ids))
                )
                deleted_metrics = r.rowcount or 0

            # AudienceActivity — per-creator, per (day_of_week, hour).
            from models import AudienceActivity, Demographics

            r = await session.execute(
                delete(AudienceActivity).where(AudienceActivity.fetched_at < cutoff)
            )
            deleted_activity = r.rowcount or 0

            # Demographics — per-creator aggregate.
            r = await session.execute(delete(Demographics).where(Demographics.fetched_at < cutoff))
            deleted_demographics = r.rowcount or 0

            await session.commit()
        finally:
            await session.execute(
                text("SELECT pg_advisory_unlock(hashtext(:k))"),
                {"k": "purge_stale_youtube_analytics"},
            )

    total = deleted_metrics + deleted_curves + deleted_activity + deleted_demographics
    if total:
        logger.info(
            "Purged stale YouTube analytics — metrics=%d curves=%d activity=%d demographics=%d "
            "(cutoff: rows fetched before %s)",
            deleted_metrics,
            deleted_curves,
            deleted_activity,
            deleted_demographics,
            cutoff.isoformat(),
        )


async def _expire_trials_async() -> None:
    """Issue 126 — log creators whose trial just expired with zero balance.

    Reads only; does not mutate. The recent-window narrows to creators whose
    `trial_ends_at` fell into the past in the last 25 hours, so a daily Beat
    cadence (24h) reliably catches every expiration once without re-logging
    the same row forever after.
    """
    from datetime import timedelta

    from sqlalchemy import select

    from models import Creator

    async with db.AdminSessionLocal() as session:
        now = datetime.now(UTC)
        window_start = now - timedelta(hours=25)
        rows = await session.execute(
            select(Creator.id, Creator.trial_ends_at, Creator.minutes_balance)
            .where(Creator.trial_ends_at.is_not(None))
            .where(Creator.trial_ends_at > window_start)
            .where(Creator.trial_ends_at <= now)
        )
        # Never log creator email — the creator id is sufficient to correlate
        # and keeps this line PII-free (no-PII-in-logs invariant, CLAUDE.md).
        for cid, trial_ends_at, balance in rows.all():
            if balance <= 0:
                logger.info(
                    "trial_expired_zero_balance creator=%s trial_ends_at=%s",
                    cid,
                    trial_ends_at.isoformat() if trial_ends_at else None,
                )


async def _sync_channel_catalog_async(creator_id: str, task_id: str | None = None) -> None:
    """Fetch the creator's uploads playlist and upsert Video rows + their metrics.

    Two-phase: (1) `sync_video_catalog` upserts Video rows from the uploads
    playlist (classifies Shorts vs long-form by duration); (2) for each video
    that does NOT yet have a VideoMetrics row with engagement_rate, call
    `sync_video_analytics` to populate it. Phase 2 closes the Issue 88 gap:
    the user clicks "Refresh data status" and the data-gate / DNA build
    immediately see ready rows instead of waiting up to an hour for the
    Beat `refresh_youtube_analytics` to catch up.

    Idempotent end-to-end:
      - `sync_video_catalog` skips known (creator_id, youtube_video_id) pairs.
      - Phase 2 filters to videos missing engagement_rate, so a re-run is a
        no-op once metrics are in place.

    Advisory lock (Issue 105 — Fix 4): per-creator key so concurrent syncs for
    different creators run in parallel; only double-deliveries for the SAME
    creator are serialised. Non-blocking so a slow prior run does not queue.

    YouTubeAuthError mid-loop is surfaced (terminal — the refresh tick will
    delete the YoutubeToken row); other per-video errors are logged and
    skipped so one bad video can't strand the whole catalog. (Issues 87, 88)

    Issue 92: when ``task_id`` is provided, emits step events to
    ``task:{task_id}:events`` so the catalog-sync UI can show per-video
    metric progress. When None (Beat-task callers + tests), emits short-
    circuit silently — no observer.
    """
    from sqlalchemy import select, text

    from config import settings
    from worker.progress import aemit
    from youtube.analytics import sync_video_analytics, sync_video_catalog
    from youtube.oauth import get_valid_access_token

    async def _emit(event_type: str, **fields: object) -> None:
        if task_id is not None:
            await aemit(task_id, event_type, **fields)

    cid = uuid.UUID(creator_id)
    lock_key = f"catalog-sync:{cid}"
    try:
        async with db.AdminSessionLocal() as session:
            acquired = (
                await session.execute(
                    text("SELECT pg_try_advisory_lock(hashtext(:k))"), {"k": lock_key}
                )
            ).scalar_one()
            if not acquired:
                logger.info(
                    "advisory lock held — skipping sync_channel_catalog for creator %s", cid
                )
                await _emit(
                    "step",
                    label="catalog_sync_skipped",
                    stage="catalog_sync",
                    reason="lock_held",
                )
                return
            try:
                creator = await session.get(Creator, cid)
                if creator is None:
                    logger.warning("sync_channel_catalog: creator %s not found, skip", cid)
                    await _emit("error", stage="catalog_sync", message="Creator not found.")
                    return
                try:
                    access_token = await get_valid_access_token(creator.id, session)
                except Exception as exc:
                    logger.warning("sync_channel_catalog: no valid token for %s: %s", cid, exc)
                    await _emit(
                        "error",
                        stage="catalog_sync",
                        message="YouTube auth unavailable; reconnect.",
                        exc_type=type(exc).__name__,
                    )
                    return

                # Phase 1 — catalog upsert.
                await _emit("step", label="fetch_uploads", stage="catalog_sync")
                await sync_video_catalog(session, creator, access_token)
                await session.commit()

                # Phase 2 — fetch metrics for any video that doesn't have them yet.
                # Capped to DNA_LONGS_CAP most-recent longs + DNA_SHORTS_CAP most-recent
                # shorts (same caps as rank_videos). This bounds the first-sync to ≤125
                # YouTube Analytics API calls (~4 min) regardless of catalog size. Older
                # or excess videos are picked up gradually by the hourly Beat task
                # (refresh_youtube_analytics). (Issue 120)
                def _unmeasured_query(kind: VideoKind, cap: int):
                    return (
                        select(Video)
                        .outerjoin(VideoMetrics, VideoMetrics.video_id == Video.id)
                        .where(
                            Video.creator_id == creator.id,
                            Video.kind == kind,
                            (VideoMetrics.video_id.is_(None))
                            | (VideoMetrics.engagement_rate.is_(None)),
                        )
                        .order_by(Video.published_at.desc().nullslast())
                        .limit(cap)
                    )

                longs_unmeasured = (
                    (
                        await session.execute(
                            _unmeasured_query(VideoKind.long, settings.DNA_LONGS_CAP)
                        )
                    )
                    .scalars()
                    .all()
                )
                shorts_unmeasured = (
                    (
                        await session.execute(
                            _unmeasured_query(VideoKind.short, settings.DNA_SHORTS_CAP)
                        )
                    )
                    .scalars()
                    .all()
                )
                unmeasured = longs_unmeasured + shorts_unmeasured

                # Re-fetch the access token before the Phase 2 loop. Phase 1 (catalog
                # upsert) can take several minutes on large channels; if the token was
                # close to expiry when first fetched it will have expired by the time
                # Phase 2 starts. A fresh token here ensures the metrics loop has a
                # full 60-minute window regardless of how long Phase 1 took.
                access_token = await get_valid_access_token(creator.id, session)

                total = len(unmeasured)
                await _emit("step", label="sync_metrics_start", stage="catalog_sync", total=total)

                fetched = 0
                for i, video in enumerate(unmeasured, 1):
                    try:
                        await sync_video_analytics(session, video, creator, access_token)
                        fetched += 1
                        await _emit(
                            "step",
                            label="sync_metrics",
                            stage="catalog_sync",
                            i=i,
                            total=total,
                        )
                    except YouTubeAuthError:
                        # Surface immediately — token is dead.
                        raise
                    except Exception as exc:
                        # repr() because some exceptions (httpx.ReadTimeout) have an
                        # empty str(); exc_info=True so the traceback is in the log
                        # rather than requiring an ssh + Python repro. (Issue 88 lesson)
                        logger.warning(
                            "sync_channel_catalog: metrics fetch failed for video %s: %r",
                            video.id,
                            exc,
                            exc_info=True,
                        )
                        # Wave-3 Fix F: emit a step event for the skipped video so
                        # the SSE consumer's `i/total` math stays contiguous. Class
                        # name only — never the exception message — to keep the
                        # progress wire-shape's no-PII / no-internal-detail
                        # invariant the worker module's structural-trust SEV2s
                        # depend on.
                        await _emit(
                            "step",
                            label="sync_metrics_skipped",
                            stage="catalog_sync",
                            i=i,
                            total=total,
                            reason=type(exc).__name__,
                        )
                await session.commit()
                logger.info(
                    "sync_channel_catalog: creator %s synced (metrics fetched for %d new video(s))",
                    cid,
                    fetched,
                )
                await _emit(
                    "done",
                    stage="catalog_sync",
                    message=f"Synced {fetched} new video(s).",
                    fetched=fetched,
                )
            finally:
                await session.execute(
                    text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": lock_key}
                )
    except YouTubeAuthError:
        await _emit("error", stage="catalog_sync", message="YouTube token revoked; reconnect.")
        raise
    except Exception as exc:
        await _emit(
            "error",
            stage="catalog_sync",
            message="Catalog sync failed; retrying.",
            exc_type=type(exc).__name__,
        )
        raise


async def _refresh_youtube_analytics_async() -> None:
    from sqlalchemy import delete, select, text

    from youtube.analytics import sync_audience_data, sync_video_analytics, sync_video_catalog
    from youtube.oauth import get_valid_access_token

    async with db.AdminSessionLocal() as session:
        # Advisory lock (Issue 105 — Fix 4): global Beat task — only one instance
        # should iterate all creators at a time.
        acquired = (
            await session.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:k))"),
                {"k": "refresh_youtube_analytics"},
            )
        ).scalar_one()
        if not acquired:
            logger.info("advisory lock held — skipping refresh_youtube_analytics")
            return
        try:
            # Issue 47: ORDER BY last_analytics_refreshed_at NULLS FIRST, id so
            # creators that starved past quota in earlier runs go first next time.
            # New creators (NULL) jump the queue, matching user expectation that a
            # just-connected creator sees data fast.
            result = await session.execute(
                select(Creator).order_by(
                    Creator.last_analytics_refreshed_at.asc().nulls_first(),
                    Creator.id,
                )
            )
            creators = list(result.scalars())

            quota_left = await remaining()
            logger.info(
                "Starting analytics refresh for %d creator(s); quota remaining: %d units",
                len(creators),
                quota_left,
            )

            for creator in creators:
                try:
                    access_token = await get_valid_access_token(creator.id, session)
                except Exception as exc:
                    logger.warning("Skipping analytics refresh for creator %s: %s", creator.id, exc)
                    continue

                try:
                    # Pull any new uploads into the videos table BEFORE iterating
                    # per-video analytics — otherwise newly published videos stay
                    # invisible to the pipeline until the next deploy. (Issue 87)
                    await sync_video_catalog(session, creator, access_token)

                    videos_result = await session.execute(
                        select(Video).where(Video.creator_id == creator.id)
                    )
                    for video in list(videos_result.scalars()):
                        await sync_video_analytics(session, video, creator, access_token)

                    await sync_audience_data(session, creator, access_token)
                    creator.last_analytics_refreshed_at = datetime.now(UTC)
                    await session.commit()
                    logger.info("Refreshed analytics for creator %s", creator.id)
                except QuotaExhaustedError:
                    logger.warning(
                        "YouTube quota exhausted during analytics refresh — stopping early. "
                        "Remaining creators will be refreshed in tomorrow's run."
                    )
                    await session.rollback()
                    break
                except YouTubeAuthError as exc:
                    # Grant is dead (revoked / suspended / forbidden). Drop the token row
                    # so subsequent beat ticks skip this creator via the existing
                    # get_valid_access_token "no tokens" path, instead of looping on 403s.
                    logger.warning(
                        "YouTube auth error for creator %s (reason=%s, status=%s) — "
                        "deleting YoutubeToken row",
                        creator.id,
                        exc.reason,
                        exc.status_code,
                    )
                    await session.rollback()
                    from models import YoutubeToken

                    await session.execute(
                        delete(YoutubeToken).where(YoutubeToken.creator_id == creator.id)
                    )
                    await session.commit()
                except Exception as exc:
                    logger.warning("Analytics refresh failed for creator %s: %s", creator.id, exc)
                    await session.rollback()
        finally:
            await session.execute(
                text("SELECT pg_advisory_unlock(hashtext(:k))"),
                {"k": "refresh_youtube_analytics"},
            )


@celery.task(
    bind=True, max_retries=3, default_retry_delay=60, name="worker.tasks.generate_improvement_brief"
)
def generate_improvement_brief(self, creator_id: str) -> str:
    """Generate a creator's content-improvement brief off the request path (Issue 78d).

    The ~120s Claude + web_search call previously ran inline on the API event loop;
    here it runs in the worker and the result is polled via GET /me/improvement-brief.
    """
    try:
        run_async(_generate_improvement_brief_async(self.request.id, creator_id))
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    return creator_id


async def _generate_improvement_brief_async(job_id: str, creator_id: str) -> None:
    """Build the creator-scoped analytics summary + Claude brief and store it.

    Idempotent + retry-safe (Celery is at-least-once): a redelivery whose row is
    already ``ready`` for this job short-circuits before the paid LLM call. On
    failure the row is marked ``failed`` with a SAFE message (no stack trace /
    token / PII) and the task retries. Per-creator isolation on every query (Issue 33).

    Issue 92: ``job_id`` is the Celery task id, which doubles as the SSE
    stream key — the router stamps ownership for the same id at enqueue
    time. Step + token + cache events flow to ``task:{job_id}:events``.
    The brief itself streams via the ``task_id`` kwarg on
    ``generate_improvement_brief`` (Issue 92 added this mirroring the DNA
    brief's Issue-86 pattern).
    """
    from sqlalchemy import select

    from dna.profile import get_active
    from improvement.brief import generate_improvement_brief as build_brief
    from models import ImprovementBrief, ImprovementBriefStatus
    from worker.progress import aemit

    try:
        await aemit(job_id, "step", label="improvement_brief_start", stage="improvement_brief")

        cid = uuid.UUID(creator_id)
        async with db.AsyncSessionLocal() as session:
            # Audit fix (Issue-135 audit, scale-checklist D): stamp creator_id so
            # the RLS `after_begin` listener sets `app.creator_id` before any
            # query. Without this the brief query + the VideoMetrics join return
            # empty under the production role split, and the brief silently
            # writes a `ready` row with no analytics.
            session.info["creator_id"] = str(cid)
            row = (
                await session.execute(
                    select(ImprovementBrief).where(ImprovementBrief.creator_id == cid)
                )
            ).scalar_one_or_none()
            if row is None:
                logger.warning(
                    "generate_improvement_brief: no row for %s; nothing to do", creator_id
                )
                await aemit(
                    job_id,
                    "error",
                    stage="improvement_brief",
                    message="No brief request found.",
                )
                return

            # Idempotency: this exact task already produced the brief (redelivery).
            if row.job_id == job_id and row.status == ImprovementBriefStatus.ready:
                logger.info(
                    "generate_improvement_brief: redelivery for %s — already built",
                    creator_id,
                )
                await aemit(
                    job_id,
                    "done",
                    stage="improvement_brief",
                    message="Brief already ready.",
                )
                return

            creator = await session.get(Creator, cid)
            if creator is None:
                row.status = ImprovementBriefStatus.failed
                row.error = "Creator not found."
                row.completed_at = datetime.now(UTC)
                await session.commit()
                await aemit(
                    job_id,
                    "error",
                    stage="improvement_brief",
                    message="Creator not found.",
                )
                return

            try:
                await aemit(
                    job_id,
                    "step",
                    label="load_analytics",
                    stage="improvement_brief",
                )
                metrics_result = await session.execute(
                    select(VideoMetrics)
                    .join(Video, VideoMetrics.video_id == Video.id)
                    .where(Video.creator_id == creator.id)
                    .order_by(VideoMetrics.fetched_at.desc())
                    .limit(50)
                )
                all_metrics = list(metrics_result.scalars())
                views_list = [m.views for m in all_metrics if m.views]
                eng_list = [m.engagement_rate for m in all_metrics if m.engagement_rate]
                dur_list = [m.avg_view_duration_s for m in all_metrics if m.avg_view_duration_s]

                def _avg(lst: Sequence[float]) -> float | None:
                    return sum(lst) / len(lst) if lst else None

                analytics = {
                    "channel_title": creator.channel_title,
                    "videos_in_db": len(all_metrics),
                    "avg_views": _avg(views_list),
                    "avg_engagement_rate": _avg(eng_list),
                    "avg_view_duration_s": _avg(dur_list),
                }

                dna_profile = await get_active(session, creator.id)
                dna_brief = dna_profile.brief_text if dna_profile else None

                await aemit(
                    job_id,
                    "step",
                    label="call_claude",
                    stage="improvement_brief",
                )
                # task_id propagates into improvement.brief.stream_and_emit,
                # which forwards cache/token deltas on the same Redis stream.
                brief_text = await asyncio.to_thread(
                    build_brief,
                    channel_title=creator.channel_title or "Unknown Channel",
                    analytics=analytics,
                    dna_brief=dna_brief,
                    task_id=job_id,
                )
            except Exception as exc:
                row.status = ImprovementBriefStatus.failed
                row.error = "Brief generation failed — try again."
                row.completed_at = datetime.now(UTC)
                await session.commit()
                logger.error("generate_improvement_brief failed for %s: %s", creator_id, exc)
                await aemit(
                    job_id,
                    "error",
                    stage="improvement_brief",
                    message="Brief generation failed; retrying.",
                    exc_type=type(exc).__name__,
                )
                raise

            row.status = ImprovementBriefStatus.ready
            row.brief_text = brief_text
            row.error = None
            row.completed_at = datetime.now(UTC)
            await session.commit()
            logger.info("Improvement brief ready for creator %s", creator_id)
            await aemit(
                job_id,
                "done",
                stage="improvement_brief",
                message="Brief ready.",
            )
    except Exception:
        # The inner try/except above already emitted the error + persisted the
        # row.failed state. This outer guard exists so a Redis emit failure
        # at line entry does not silently swallow the original exception.
        raise


# ── Video analysis (Issue 121) ────────────────────────────────────────────────


@celery.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="worker.tasks.generate_video_analysis",
)
def generate_video_analysis(
    self,
    creator_id: str,
    youtube_video_id: str,
    query: str,
    video_id: str | None = None,
) -> str:
    """Analyze a YouTube video's performance off the request path (Issue 121).

    Accepts a youtube_video_id (always) plus an optional video_id (our DB PK)
    for videos already in the creator's catalog — richer context when present.
    """
    try:
        run_async(
            _generate_video_analysis_async(
                self.request.id, creator_id, youtube_video_id, query, video_id
            )
        )
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    return creator_id


async def _generate_video_analysis_async(
    job_id: str,
    creator_id: str,
    youtube_video_id: str,
    query: str,
    video_id: str | None = None,
) -> None:
    """Fetch available data for the video + creator and call Claude (streaming).

    Uses AdminSessionLocal (cross-tenant worker context) but enforces
    per-creator isolation on every query via creator.id filters. The
    analysis result is ephemeral — no row is persisted; the stream IS
    the response.
    """
    from collections.abc import Sequence
    from typing import Any

    from sqlalchemy import select

    from analysis.brief import generate_video_analysis as build_analysis
    from dna.profile import get_active
    from models import RetentionCurve, Video, VideoMetrics
    from worker.progress import aemit

    try:
        await aemit(job_id, "step", label="loading_data", stage="video_analysis")

        cid = uuid.UUID(creator_id)
        async with db.AdminSessionLocal() as session:
            creator = await session.get(Creator, cid)
            if creator is None:
                await aemit(job_id, "error", stage="video_analysis", message="Creator not found.")
                return

            video_metrics: dict[str, Any] | None = None
            retention_summary: dict[str, Any] | None = None
            video_title: str | None = None

            # Pull metrics + retention only when we have the video in our DB.
            # Videos outside the catalog get a metadata-only analysis (still
            # useful because DNA + channel averages provide comparison context).
            if video_id is not None:
                vid = await session.get(Video, uuid.UUID(video_id))
                if vid is not None and vid.creator_id == cid:
                    video_title = vid.title
                    metrics = (
                        await session.execute(
                            select(VideoMetrics)
                            .where(VideoMetrics.video_id == vid.id)
                            .order_by(VideoMetrics.fetched_at.desc())
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                    if metrics:
                        video_metrics = {
                            "views": metrics.views,
                            "watch_time_s": metrics.watch_time_s,
                            "avg_view_duration_s": metrics.avg_view_duration_s,
                            "engagement_rate": metrics.engagement_rate,
                        }

                    if vid.duration_s:
                        curves: Sequence[RetentionCurve] = list(
                            (
                                await session.execute(
                                    select(RetentionCurve)
                                    .where(RetentionCurve.video_id == vid.id)
                                    .order_by(RetentionCurve.timestamp_s)
                                )
                            ).scalars()
                        )
                        if curves:
                            total = vid.duration_s
                            checkpoints: dict[str, float | None] = {
                                "at_25pct": None,
                                "at_50pct": None,
                                "at_75pct": None,
                                "at_end": None,
                            }
                            for c in curves:
                                pct = c.timestamp_s / total
                                if checkpoints["at_25pct"] is None and pct >= 0.25:
                                    checkpoints["at_25pct"] = round(c.audience_watch_ratio or 0, 3)
                                if checkpoints["at_50pct"] is None and pct >= 0.50:
                                    checkpoints["at_50pct"] = round(c.audience_watch_ratio or 0, 3)
                                if checkpoints["at_75pct"] is None and pct >= 0.75:
                                    checkpoints["at_75pct"] = round(c.audience_watch_ratio or 0, 3)
                                checkpoints["at_end"] = round(c.audience_watch_ratio or 0, 3)
                            retention_summary = checkpoints

            # Channel averages for comparison — scoped to this creator.
            def _avg(lst: Sequence[float]) -> float | None:
                return round(sum(lst) / len(lst), 2) if lst else None

            all_metrics = list(
                (
                    await session.execute(
                        select(VideoMetrics)
                        .join(Video, VideoMetrics.video_id == Video.id)
                        .where(Video.creator_id == cid)
                        .order_by(VideoMetrics.fetched_at.desc())
                        .limit(50)
                    )
                ).scalars()
            )
            channel_avg: dict[str, Any] | None = None
            if all_metrics:
                views_list = [m.views for m in all_metrics if m.views]
                eng_list = [m.engagement_rate for m in all_metrics if m.engagement_rate]
                dur_list = [m.avg_view_duration_s for m in all_metrics if m.avg_view_duration_s]
                channel_avg = {
                    "avg_views": _avg(views_list),
                    "avg_engagement_rate": _avg(eng_list),
                    "avg_view_duration_s": _avg(dur_list),
                    "sample_size": len(all_metrics),
                }

            dna_profile = await get_active(session, cid)
            dna_brief = dna_profile.brief_text if dna_profile else None

            await aemit(job_id, "step", label="analyzing", stage="video_analysis")

            await asyncio.to_thread(
                build_analysis,
                channel_title=creator.channel_title or "Unknown Channel",
                youtube_video_id=youtube_video_id,
                video_title=video_title,
                query=query,
                video_metrics=video_metrics,
                retention_summary=retention_summary,
                channel_avg=channel_avg,
                dna_brief=dna_brief,
                task_id=job_id,
            )

        await aemit(job_id, "done", stage="video_analysis", message="Analysis complete.")

    except Exception as exc:
        logger.error(
            "_generate_video_analysis_async failed creator=%s video=%s: %s",
            creator_id,
            youtube_video_id,
            exc,
        )
        await aemit(
            job_id,
            "error",
            stage="video_analysis",
            message="Analysis failed — please try again.",
            exc_type=type(exc).__name__,
        )
        raise


# ── Title suggestions (Issue 128) ─────────────────────────────────────────────


@celery.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="worker.tasks.generate_title_suggestions",
)
def generate_title_suggestions(self, creator_id: str, video_id: str) -> str:
    """Generate title suggestions for a video off the request path (Issue 128)."""
    try:
        run_async(_generate_title_suggestions_async(self.request.id, creator_id, video_id))
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    return creator_id


async def _generate_title_suggestions_async(
    job_id: str,
    creator_id: str,
    video_id: str,
) -> None:
    """Fetch transcript + DNA + identity; call Claude; emit result + done events.

    Results are ephemeral — no DB row is persisted. The SSE consumer captures
    the ``result`` event containing the top-5 TitleSuggestion objects.
    Per-creator isolation enforced on every query via creator.id filters.
    """
    import json as _json

    from sqlalchemy import select

    from dna.identity import format_for_prompt
    from dna.identity import get_current as get_identity
    from dna.profile import get_active
    from knowledge.titles import (
        _extract_transcript_summary,
        parse_candidates,
    )
    from knowledge.titles import (
        generate_title_suggestions as build_suggestions,
    )
    from models import Transcript, Video
    from worker.progress import aemit

    try:
        await aemit(job_id, "step", label="loading_data", stage="title_suggestions")

        cid = uuid.UUID(creator_id)
        vid = uuid.UUID(video_id)

        async with db.AdminSessionLocal() as session:
            creator = await session.get(Creator, cid)
            if creator is None:
                await aemit(
                    job_id, "error", stage="title_suggestions", message="Creator not found."
                )
                return

            video = await session.get(Video, vid)
            if video is None or video.creator_id != cid:
                await aemit(job_id, "error", stage="title_suggestions", message="Video not found.")
                return

            transcript_row = await session.scalar(
                select(Transcript).where(Transcript.video_id == vid)
            )
            transcript_summary = _extract_transcript_summary(
                transcript_row.segments_jsonb if transcript_row else None
            )

            dna_profile = await get_active(session, cid)
            dna_brief = dna_profile.brief_text if dna_profile else None

            identity = await get_identity(session, cid)
            stated_identity = format_for_prompt(identity)

        await aemit(job_id, "step", label="generating_titles", stage="title_suggestions")

        raw_json = await asyncio.to_thread(
            build_suggestions,
            channel_title=creator.channel_title or "Unknown Channel",
            dna_brief=dna_brief,
            stated_identity=stated_identity,
            video_title=video.title,
            transcript_summary=transcript_summary,
            task_id=job_id,
        )

        try:
            suggestions = parse_candidates(raw_json)
        except (ValueError, _json.JSONDecodeError) as exc:
            logger.error(
                "_generate_title_suggestions_async parse failed creator=%s video=%s: %s",
                creator_id,
                video_id,
                exc,
            )
            await aemit(
                job_id,
                "error",
                stage="title_suggestions",
                message="Title parsing failed — please try again.",
            )
            raise

        # Pass suggestions in the done payload so the SSE consumer's onDone
        # callback receives them without a separate result event (done already
        # fires handlers.onDone(data) in progressStream.js, and data is the
        # full parsed JSON — no progressStream.js changes required).
        await aemit(
            job_id,
            "done",
            stage="title_suggestions",
            message="Titles ready.",
            suggestions=suggestions,
        )

    except Exception as exc:
        logger.error(
            "_generate_title_suggestions_async failed creator=%s video=%s: %s",
            creator_id,
            video_id,
            exc,
        )
        await aemit(
            job_id,
            "error",
            stage="title_suggestions",
            message="Title generation failed — please try again.",
            exc_type=type(exc).__name__,
        )
        raise


# ── Thumbnail concept generator (Issue 129) ───────────────────────────────────


@celery.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="worker.tasks.generate_thumbnail_concepts",
)
def generate_thumbnail_concepts(self, creator_id: str, video_id: str) -> str:
    """Generate thumbnail concepts for a video off the request path (Issue 129)."""
    try:
        run_async(_generate_thumbnail_concepts_async(self.request.id, creator_id, video_id))
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    return creator_id


async def _generate_thumbnail_concepts_async(
    job_id: str,
    creator_id: str,
    video_id: str,
) -> None:
    """Fetch patterns + transcript + DNA; call Claude; emit result + done events.

    Results are ephemeral — no DB row persisted. The SSE done payload carries
    the concept objects. Per-creator isolation enforced on every query.
    """
    import json

    from sqlalchemy import select

    from dna.identity import format_for_prompt
    from dna.identity import get_current as get_identity
    from dna.profile import get_active
    from knowledge.thumbnails import (
        PATTERNS_CACHE_KEY_PREFIX,
        PATTERNS_CACHE_TTL,
        _extract_transcript_hook,
        analyze_thumbnail_patterns,
        parse_concepts,
    )
    from knowledge.thumbnails import (
        generate_thumbnail_concepts as build_concepts,
    )
    from models import Transcript, Video
    from worker.progress import aemit

    try:
        await aemit(job_id, "step", label="loading_data", stage="thumbnail_concepts")

        cid = uuid.UUID(creator_id)
        vid = uuid.UUID(video_id)

        async with db.AdminSessionLocal() as session:
            creator = await session.get(Creator, cid)
            if creator is None:
                await aemit(
                    job_id, "error", stage="thumbnail_concepts", message="Creator not found."
                )
                return

            video = await session.get(Video, vid)
            if video is None or video.creator_id != cid:
                await aemit(job_id, "error", stage="thumbnail_concepts", message="Video not found.")
                return

            transcript_row = await session.scalar(
                select(Transcript).where(Transcript.video_id == vid)
            )
            transcript_hook = _extract_transcript_hook(
                transcript_row.segments_jsonb if transcript_row else None
            )

            dna_profile = await get_active(session, cid)
            dna_brief = dna_profile.brief_text if dna_profile else None

            identity = await get_identity(session, cid)
            stated_identity = format_for_prompt(identity)

            top_ids: list[str] = (dna_profile.top_video_ids_jsonb or []) if dna_profile else []
            youtube_ids: list[str] = []
            if top_ids:
                try:
                    uuid_list = [uuid.UUID(vid_id) for vid_id in top_ids[:10]]
                    rows = (
                        (
                            await session.execute(
                                select(Video.youtube_video_id).where(
                                    Video.id.in_(uuid_list),
                                    Video.creator_id == cid,
                                    Video.youtube_video_id.isnot(None),
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    youtube_ids = [r for r in rows if r]
                except (ValueError, TypeError) as exc:
                    logger.warning(
                        "_thumbnail_concepts_async: failed to resolve video IDs: %s", exc
                    )

        # Check Redis cache for patterns (same key as GET endpoint — avoids
        # a second Claude multimodal call if the creator already viewed patterns).
        # Uses the module-level singleton to avoid creating a new connection pool
        # per-task invocation (SEV1 fix: was creating per-task via _aredis.from_url).
        patterns: dict | None = None
        _rc = _thumb_redis()
        try:
            cached_raw = await _rc.get(f"{PATTERNS_CACHE_KEY_PREFIX}{creator_id}")
            if cached_raw:
                patterns = json.loads(cached_raw)
        except Exception as exc:
            logger.warning("_thumbnail_concepts_async: Redis cache read failed: %s", exc)

        if patterns is None and youtube_ids:
            await aemit(job_id, "step", label="analyzing_patterns", stage="thumbnail_concepts")
            patterns = await asyncio.to_thread(
                analyze_thumbnail_patterns,
                youtube_ids,
                creator.channel_title or "Unknown Channel",
            )
            try:
                await _rc.setex(
                    f"{PATTERNS_CACHE_KEY_PREFIX}{creator_id}",
                    PATTERNS_CACHE_TTL,
                    json.dumps(patterns),
                )
            except Exception as exc:
                logger.warning("_thumbnail_concepts_async: Redis cache write failed: %s", exc)

        if patterns is None:
            patterns = {
                "face_present": "unknown",
                "dominant_emotions": [],
                "text_overlay_style": "unknown",
                "typical_colors": "unknown",
                "composition_pattern": "unknown",
                "channel_thumbnail_signature": "Insufficient data.",
            }

        await aemit(job_id, "step", label="generating_concepts", stage="thumbnail_concepts")

        raw_json = await asyncio.to_thread(
            build_concepts,
            channel_title=creator.channel_title or "Unknown Channel",
            dna_brief=dna_brief,
            patterns=patterns,
            transcript_hook=transcript_hook,
            stated_identity=stated_identity,
            task_id=job_id,
        )

        try:
            concepts = parse_concepts(raw_json)
        except (ValueError, json.JSONDecodeError) as exc:
            logger.error(
                "_generate_thumbnail_concepts_async parse failed creator=%s video=%s: %s",
                creator_id,
                video_id,
                exc,
            )
            await aemit(
                job_id,
                "error",
                stage="thumbnail_concepts",
                message="Concept parsing failed — please try again.",
            )
            raise

        await aemit(
            job_id,
            "done",
            stage="thumbnail_concepts",
            message="Concepts ready.",
            concepts=concepts,
        )

    except Exception as exc:
        logger.error(
            "_generate_thumbnail_concepts_async failed creator=%s video=%s: %s",
            creator_id,
            video_id,
            exc,
        )
        await aemit(
            job_id,
            "error",
            stage="thumbnail_concepts",
            message="Thumbnail concept generation failed — please try again.",
            exc_type=type(exc).__name__,
        )
        raise


# ── Hook analyzer (Issue 130) ─────────────────────────────────────────────────


@celery.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="worker.tasks.analyze_hook",
)
def analyze_hook(self, creator_id: str, video_id: str) -> str:
    """Analyze the first-30s hook against the creator's retention curves (Issue 130)."""
    try:
        run_async(_analyze_hook_async(self.request.id, creator_id, video_id))
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    return creator_id


async def _analyze_hook_async(job_id: str, creator_id: str, video_id: str) -> None:
    """Fetch retention curves + transcript + DNA; compute drop; call Claude; emit done.

    Per-creator isolation enforced on every query.
    """
    import json as _json

    from sqlalchemy import select

    from dna.profile import get_active
    from knowledge.hooks import analyze_hook as build_hook_report
    from knowledge.hooks import compute_retention_drop, parse_hook_report
    from knowledge.util import extract_transcript_excerpt
    from models import RetentionCurve, Transcript, Video
    from worker.progress import aemit

    try:
        await aemit(job_id, "step", label="loading_data", stage="hook_analysis")

        cid = uuid.UUID(creator_id)
        vid = uuid.UUID(video_id)

        async with db.AdminSessionLocal() as session:
            creator = await session.get(Creator, cid)
            if creator is None:
                await aemit(job_id, "error", stage="hook_analysis", message="Creator not found.")
                return

            video = await session.get(Video, vid)
            if video is None or video.creator_id != cid:
                await aemit(job_id, "error", stage="hook_analysis", message="Video not found.")
                return

            # Fetch the target video's retention curve
            video_curve_rows = (
                await session.execute(
                    select(RetentionCurve.timestamp_s, RetentionCurve.audience_watch_ratio)
                    .where(RetentionCurve.video_id == vid)
                    .order_by(RetentionCurve.timestamp_s)
                )
            ).all()
            video_curves: list[tuple[float, float]] = [(r[0], r[1]) for r in video_curve_rows]

            # Fetch other creator videos' retention curves for the median baseline
            other_video_ids = (
                (
                    await session.execute(
                        select(Video.id).where(
                            Video.creator_id == cid,
                            Video.id != vid,
                        )
                    )
                )
                .scalars()
                .all()
            )

            creator_curves: list[list[tuple[float, float]]] = []
            for other_vid in other_video_ids[:20]:  # cap to 20 for performance
                rows = (
                    await session.execute(
                        select(RetentionCurve.timestamp_s, RetentionCurve.audience_watch_ratio)
                        .where(RetentionCurve.video_id == other_vid)
                        .order_by(RetentionCurve.timestamp_s)
                    )
                ).all()
                if rows:
                    creator_curves.append([(r[0], r[1]) for r in rows])

            # Transcript for the first 60s
            transcript_row = await session.scalar(
                select(Transcript).where(Transcript.video_id == vid)
            )
            transcript_excerpt = extract_transcript_excerpt(
                transcript_row.segments_jsonb if transcript_row else None,
                max_s=60.0,
            )

            dna_profile = await get_active(session, cid)
            dna_brief = dna_profile.brief_text if dna_profile else None

        await aemit(job_id, "step", label="analyzing_hook", stage="hook_analysis")

        # Compute retention drop (pure Python)
        drop_at_s, retention_at_drop = compute_retention_drop(video_curves, creator_curves)

        # Compute creator median at the drop point for the prompt
        creator_median_at_drop: float | None = None
        if drop_at_s is not None and creator_curves:
            import numpy as np

            grid_point = np.array([drop_at_s])
            medians = []
            for c in creator_curves:
                sorted_c = sorted(c)
                ts = [p[0] for p in sorted_c]
                rs = [p[1] for p in sorted_c]
                if ts[0] > 0:
                    ts, rs = [0.0] + ts, [1.0] + rs
                medians.append(float(np.interp(grid_point, ts, rs)[0]))
            creator_median_at_drop = float(np.median(medians)) if medians else None

        raw_json = await asyncio.to_thread(
            build_hook_report,
            channel_title=creator.channel_title or "Unknown Channel",
            dna_brief=dna_brief,
            retention_drop_at_s=drop_at_s,
            retention_at_drop=retention_at_drop,
            creator_median_at_drop=creator_median_at_drop,
            transcript_excerpt=transcript_excerpt,
            task_id=job_id,
        )

        try:
            report = parse_hook_report(raw_json)
        except (ValueError, _json.JSONDecodeError) as exc:
            logger.error(
                "_analyze_hook_async parse failed creator=%s video=%s: %s",
                creator_id,
                video_id,
                exc,
            )
            await aemit(
                job_id,
                "error",
                stage="hook_analysis",
                message="Hook report parsing failed — please try again.",
            )
            raise

        await aemit(
            job_id,
            "done",
            stage="hook_analysis",
            message="Hook analysis ready.",
            report=report,
        )

    except Exception as exc:
        logger.error(
            "_analyze_hook_async failed creator=%s video=%s: %s",
            creator_id,
            video_id,
            exc,
        )
        await aemit(
            job_id,
            "error",
            stage="hook_analysis",
            message="Hook analysis failed — please try again.",
            exc_type=type(exc).__name__,
        )
        raise


# ── Auto chapter markers (Issue 131) ─────────────────────────────────────────


@celery.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="worker.tasks.generate_chapters",
)
def generate_chapters(self, creator_id: str, video_id: str) -> str:
    """Generate YouTube chapter markers from transcript + signal timeline (Issue 131)."""
    try:
        run_async(_generate_chapters_async(self.request.id, creator_id, video_id))
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    return creator_id


async def _generate_chapters_async(job_id: str, creator_id: str, video_id: str) -> None:
    """Fetch transcript + signals; detect boundaries; call Claude; emit done.

    Per-creator isolation enforced on every query.
    """
    import json as _json

    from sqlalchemy import select

    from knowledge.chapters import (
        find_chapter_boundaries,
        parse_chapters,
    )
    from knowledge.chapters import (
        generate_chapters as build_chapters,
    )
    from knowledge.util import get_transcript_segments
    from models import Signals, Transcript, Video
    from worker.progress import aemit

    try:
        await aemit(job_id, "step", label="loading_data", stage="chapters")

        cid = uuid.UUID(creator_id)
        vid = uuid.UUID(video_id)

        async with db.AdminSessionLocal() as session:
            video = await session.get(Video, vid)
            if video is None or video.creator_id != cid:
                await aemit(job_id, "error", stage="chapters", message="Video not found.")
                return

            transcript_row = await session.scalar(
                select(Transcript).where(Transcript.video_id == vid)
            )
            if transcript_row is None:
                await aemit(
                    job_id,
                    "error",
                    stage="chapters",
                    message="Transcript not available — wait for ingestion to complete.",
                )
                return

            signals_row = await session.scalar(select(Signals).where(Signals.video_id == vid))

        await aemit(job_id, "step", label="generating_chapters", stage="chapters")

        video_duration_s = float(video.duration_s or 0)
        segments = get_transcript_segments(transcript_row.segments_jsonb)
        timeline = signals_row.timeline_jsonb if signals_row else None

        boundaries = find_chapter_boundaries(timeline, video_duration_s)

        raw_json = await asyncio.to_thread(
            build_chapters,
            boundaries=boundaries,
            segments=segments,
            video_duration_s=video_duration_s,
            task_id=job_id,
        )

        try:
            result = parse_chapters(raw_json)
        except (ValueError, _json.JSONDecodeError) as exc:
            logger.error(
                "_generate_chapters_async parse failed creator=%s video=%s: %s",
                creator_id,
                video_id,
                exc,
            )
            await aemit(
                job_id,
                "error",
                stage="chapters",
                message="Chapter parsing failed — please try again.",
            )
            raise

        await aemit(
            job_id,
            "done",
            stage="chapters",
            message="Chapters ready.",
            chapters=result["chapters"],
            description_block=result["description_block"],
        )

    except Exception as exc:
        logger.error(
            "_generate_chapters_async failed creator=%s video=%s: %s",
            creator_id,
            video_id,
            exc,
        )
        await aemit(
            job_id,
            "error",
            stage="chapters",
            message="Chapter generation failed — please try again.",
            exc_type=type(exc).__name__,
        )
        raise
