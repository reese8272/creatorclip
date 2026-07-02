"""
Celery pipeline tasks: ingest_video → transcribe_video → build_signals → build_dna.

Design: each task accepts only a UUID string — never large payloads —
so Redis messages stay small. Each task is idempotent and safe to retry.
run_async() dispatches each coroutine onto the worker-process singleton loop
installed by worker_process_init (Issue 39), so the SQLAlchemy async engine
pool stays bound to a single loop across task invocations.
"""

import asyncio
import contextlib
import logging
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import redis.asyncio as aredis
from celery import Task
from celery.exceptions import SoftTimeLimitExceeded

import db
from flags import flag_enabled
from models import (
    ChatConversation,
    ChatMessage,
    ChatRole,
    Clip,
    ClipFeedback,
    ClipFormat,
    ClipOutcome,
    ClipPublication,
    Creator,
    CreatorDna,
    DataExport,
    DataExportStatus,
    IngestStatus,
    MinuteDeduction,
    MinutePack,
    OnboardingState,
    PreferenceModel,
    PublishPlatform,
    PublishStatus,
    RenderStatus,
    RetentionCurve,
    Signals,
    Transcript,
    Video,
    VideoKind,
    VideoMetrics,
)
from observability import RENDER_FAILURES_TOTAL, log_event
from worker.celery_app import celery, run_async
from youtube.errors import YouTubeAuthError
from youtube.quota import QuotaExhaustedError, QuotaSubBudgetExhaustedError, remaining

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

        # on_failure fires only on TERMINAL failure (after all retries are
        # exhausted). Celery does NOT call it for intermediate Retry exceptions.
        # We emit the structured event here rather than inside the task body so
        # every task that inherits this base class gets *_failed instrumentation
        # for free. creator_id is intentionally omitted on this hot-path because
        # it would require a DB call on a potentially-degraded connection.
        task_name = self.name or "unknown"
        log_event(
            f"{task_name.rsplit('.', 1)[-1]}_failed",
            task_id=task_id,
            exc_type=type(exc).__name__,
        )

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
            return
        # Fire the refund notification after a successful refund. Runs in a
        # separate async call so a notification failure never affects the
        # already-completed refund. Best-effort: log and continue.
        try:
            run_async(_fire_refund_notification_async(video_uuid))
        except Exception as notify_exc:
            logger.warning(
                "Refund notification failed for video %s (task %s): %s",
                video_uuid,
                task_id,
                notify_exc,
            )


async def _fire_refund_notification_async(video_uuid: uuid.UUID) -> None:
    """Look up creator_id from the MinuteDeduction row and enqueue refund_issued.

    Runs after refund_for_video() succeeds.  Best-effort — callers must catch
    any exception so a notification failure never blocks the failure-handling path.

    entity_id = str(video_uuid): the UNIQUE dedupe_key on this triple means
    exactly one refund notification fires per video per creator, even if
    on_failure is invoked more than once (at-least-once Celery semantics).
    """
    from sqlalchemy import select

    from models import MinuteDeduction

    async with db.AdminSessionLocal() as session:
        row = await session.scalar(
            select(MinuteDeduction.creator_id).where(MinuteDeduction.video_id == video_uuid)
        )
        if row is None:
            logger.info(
                "_fire_refund_notification: no deduction row for video %s — skip",
                video_uuid,
            )
            return

    send_notification.delay(str(row), "refund_issued", str(video_uuid), {})


# ── Tasks ─────────────────────────────────────────────────────────────────────


@celery.task(
    base=RefundOnFailureTask,
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name="worker.tasks.ingest_video",
)
def ingest_video(self, video_id: str) -> str:
    creator_id = run_async(_creator_id_for_video(video_id))
    log_event(
        "ingest_video_started", creator_id=creator_id, task_id=self.request.id, video_id=video_id
    )
    try:
        run_async(_ingest_async(video_id))
    except SoftTimeLimitExceeded:
        # Soft limit means we've already burned ~50 min. Do NOT retry —
        # the next delivery would time out again, wasting credits. Re-raise
        # so RefundOnFailureTask.on_failure fires immediately on terminal failure.
        raise
    except Exception as exc:
        run_async(
            _set_status(video_id, IngestStatus.failed, reason=_humanize_failure(exc, "ingest"))
        )
        raise self.retry(exc=exc) from exc
    log_event(
        "ingest_video_done", creator_id=creator_id, task_id=self.request.id, video_id=video_id
    )
    return video_id


@celery.task(
    base=RefundOnFailureTask,
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name="worker.tasks.transcribe_video",
)
def transcribe_video(self, video_id: str) -> str:
    creator_id = run_async(_creator_id_for_video(video_id))
    log_event(
        "transcribe_video_started",
        creator_id=creator_id,
        task_id=self.request.id,
        video_id=video_id,
    )
    try:
        run_async(_transcribe_async(video_id))
    except SoftTimeLimitExceeded:
        # Terminal — transcription timed out; a retry would time out again.
        # Mark failed so the UI shows an error instead of spinning, then
        # re-raise so RefundOnFailureTask.on_failure fires (no retry).
        logger.warning(
            "transcribe_video soft-timeout for video %s (task %s) — terminal, not retrying",
            video_id,
            self.request.id,
        )
        run_async(
            _set_status(
                video_id,
                IngestStatus.failed,
                reason="Transcription timed out. Please try again.",
            )
        )
        raise
    except Exception as exc:
        run_async(
            _set_status(video_id, IngestStatus.failed, reason=_humanize_failure(exc, "transcribe"))
        )
        raise self.retry(exc=exc) from exc
    log_event(
        "transcribe_video_done", creator_id=creator_id, task_id=self.request.id, video_id=video_id
    )
    return video_id


@celery.task(
    base=RefundOnFailureTask,
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name="worker.tasks.build_signals",
)
def build_signals(self, video_id: str) -> str:
    creator_id = run_async(_creator_id_for_video(video_id))
    log_event(
        "build_signals_started", creator_id=creator_id, task_id=self.request.id, video_id=video_id
    )
    try:
        run_async(_signals_async(video_id))
    except SoftTimeLimitExceeded:
        # See ingest_video above — re-raise immediately to fire on_failure.
        raise
    except Exception as exc:
        run_async(
            _set_status(video_id, IngestStatus.failed, reason=_humanize_failure(exc, "signals"))
        )
        raise self.retry(exc=exc) from exc
    generate_clips.delay(video_id)
    log_event(
        "build_signals_done", creator_id=creator_id, task_id=self.request.id, video_id=video_id
    )
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
    creator_id = run_async(_creator_id_for_video(video_id))
    log_event(
        "generate_clips_started", creator_id=creator_id, task_id=self.request.id, video_id=video_id
    )
    try:
        run_async(_generate_clips_async(video_id))
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    log_event(
        "generate_clips_done", creator_id=creator_id, task_id=self.request.id, video_id=video_id
    )
    return video_id


@celery.task(bind=True, max_retries=3, default_retry_delay=60, name="worker.tasks.render_clip")
def render_clip(self, clip_id: str) -> str:
    """Render a clip to 9:16 and upload to storage.

    Permanent failures (missing clip/source, invalid timing range — raised as
    ``ValueError``/``FileNotFoundError``) are TERMINAL: set the clip to ``failed``
    and re-raise WITHOUT retry. Previously every failure went through
    ``self.retry`` (max_retries=3, 60s apart), so a deterministically-broken clip
    burned ~3 minutes of retries while the UI sat on "Rendering…" — the retry-storm
    the owner reported. Transient failures (R2/network, an ffmpeg blip) still retry
    with backoff. Mirrors ``build_dna``'s ValueError-is-permanent convention.
    """
    creator_id = run_async(_creator_id_for_clip(clip_id))
    log_event(
        "render_clip_started", creator_id=creator_id, task_id=self.request.id, video_id=clip_id
    )
    try:
        run_async(_render_clip_async(clip_id))
    except (ValueError, FileNotFoundError) as exc:
        # Permanent — retrying cannot conjure a missing clip/source or fix a bad range.
        run_async(_set_clip_render_status(clip_id, RenderStatus.failed))
        RENDER_FAILURES_TOTAL.labels(task="render_clip").inc()
        log_event(
            "render_clip_failed_permanent",
            creator_id=creator_id,
            task_id=self.request.id,
            video_id=clip_id,
            exc_type=type(exc).__name__,
        )
        raise
    except SoftTimeLimitExceeded:
        # Terminal — the task ran past its Celery soft time limit. A retry would time
        # out again, compounding the wasted render minutes. Mark failed and re-raise
        # immediately so RefundOnFailureTask.on_failure fires (no retry). (Issue 336)
        logger.warning(
            "render_clip soft-timeout for clip %s (task %s) — terminal, not retrying",
            clip_id,
            self.request.id,
        )
        run_async(_set_clip_render_status(clip_id, RenderStatus.failed))
        RENDER_FAILURES_TOTAL.labels(task="render_clip").inc()
        raise
    except Exception as exc:
        # Transient — set failed (so a UI poll between attempts shows a terminal
        # state, and the final MaxRetriesExceededError leaves it failed) then retry.
        run_async(_set_clip_render_status(clip_id, RenderStatus.failed))
        RENDER_FAILURES_TOTAL.labels(task="render_clip").inc()
        raise self.retry(exc=exc) from exc
    log_event("render_clip_done", creator_id=creator_id, task_id=self.request.id, video_id=clip_id)
    return clip_id


@celery.task(
    bind=True, max_retries=3, default_retry_delay=60, name="worker.tasks.render_video_clips"
)
def render_video_clips(self, video_id: str, clip_ids: list[str]) -> str:
    """Render a batch of clips from one video, downloading the source ONCE.

    The auto-render path enqueues this instead of N ``render_clip`` tasks so a
    multi-clip video fetches its (often large) source from R2 a single time
    rather than once per clip. Per-clip failures are isolated inside
    ``_render_video_clips_async``; only a failure to obtain the shared source
    reaches here. Missing source is permanent (mark the batch failed, no retry);
    a transient R2/network error retries the whole batch with backoff — mirrors
    ``render_clip``'s ValueError-is-permanent convention.
    """
    log_event(
        "render_video_clips_started",
        task_id=self.request.id,
        video_id=video_id,
        count=len(clip_ids),
    )
    try:
        run_async(_render_video_clips_async(video_id, clip_ids))
    except (ValueError, FileNotFoundError) as exc:
        # Permanent shared failure (no source) — every clip is unrenderable.
        for clip_id in clip_ids:
            run_async(_set_clip_render_status(clip_id, RenderStatus.failed))
        RENDER_FAILURES_TOTAL.labels(task="render_video_clips").inc()
        log_event(
            "render_video_clips_failed_permanent",
            task_id=self.request.id,
            video_id=video_id,
            exc_type=type(exc).__name__,
        )
        raise
    except Exception as exc:
        # Transient shared failure (R2/network on the single download) — retry
        # the whole batch; the per-clip done-check skips any already rendered.
        RENDER_FAILURES_TOTAL.labels(task="render_video_clips").inc()
        raise self.retry(exc=exc) from exc
    log_event(
        "render_video_clips_done",
        task_id=self.request.id,
        video_id=video_id,
        count=len(clip_ids),
    )
    return video_id


@celery.task(bind=True, max_retries=2, default_retry_delay=60, name="worker.tasks.clean_clip")
def clean_clip(self, clip_id: str) -> str:
    """Re-render a clip with filler words + long silences removed (Issue 134)."""
    try:
        run_async(_clean_clip_async(clip_id))
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    return clip_id


@celery.task(
    bind=True, max_retries=3, default_retry_delay=120, name="worker.tasks.publish_to_youtube"
)
def publish_to_youtube(self, clip_id: str) -> str:
    """Upload a rendered clip to the creator's YouTube channel (Issue 195).

    Idempotent on the Celery task id: a redelivery finds the existing
    ``clip_publications`` row and never double-posts. Pre-audit, uploads are
    forced ``private`` (the creator flips each Short to public manually).
    Transient failures (quota, 5xx, network) retry; permanent failures
    (audit/forbidden, invalid grant) are recorded and surfaced, not retried.
    """
    from youtube.errors import YouTubeAuthError
    from youtube.publish import YouTubeUploadError
    from youtube.quota import QuotaExhaustedError

    try:
        return run_async(_publish_to_youtube_async(self.request.id, clip_id))
    except (YouTubeAuthError, YouTubeUploadError):
        # Permanent — already recorded as failed in the async body. Surface to
        # Celery as terminal; retrying can't fix audit/grant/forbidden.
        raise
    except QuotaExhaustedError as exc:
        # Daily quota drained — retry later (the budget resets at PT midnight).
        raise self.retry(exc=exc) from exc
    except Exception as exc:
        raise self.retry(exc=exc) from exc


async def _publish_to_youtube_async(task_id: str, clip_id: str) -> str:
    from sqlalchemy import select

    from config import settings
    from worker.storage import alocal_path
    from youtube.errors import YouTubeAuthError
    from youtube.oauth import get_valid_access_token
    from youtube.publish import YouTubeUploadError, upload_video
    from youtube.quota import COST_DATA_VIDEOS_INSERT, consume

    cid = uuid.UUID(clip_id)

    # Kill switch (Issue 284): when youtube_publish is off, record a clean
    # FAILED publication row (never a silent drop) and stop before any quota
    # or upload work. YouTubeUploadError is terminal in the task wrapper — the
    # blocked publish is surfaced, not retried into a paused subsystem.
    if not await flag_enabled("youtube_publish", session_factory=db.AdminSessionLocal):
        async with db.AdminSessionLocal() as session:
            existing = (
                await session.execute(
                    select(ClipPublication).where(ClipPublication.task_id == task_id)
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing.status = PublishStatus.failed
                existing.error = "youtube_publish_disabled"
            else:
                clip = await session.get(Clip, cid)
                if clip is not None:
                    session.add(
                        ClipPublication(
                            clip_id=cid,
                            creator_id=clip.creator_id,
                            task_id=task_id,
                            status=PublishStatus.failed,
                            error="youtube_publish_disabled",
                        )
                    )
            await session.commit()
        logger.warning("publish blocked for clip %s — youtube_publish kill switch is off", clip_id)
        raise YouTubeUploadError(503, "youtube_publish_disabled: publishing is paused by operators")

    async with db.AdminSessionLocal() as session:
        # Idempotency: a redelivery of the same task id that already succeeded is
        # a no-op — never a second upload (at-least-once → effectively once).
        existing = (
            await session.execute(select(ClipPublication).where(ClipPublication.task_id == task_id))
        ).scalar_one_or_none()
        if existing is not None and existing.status == PublishStatus.done:
            if existing.youtube_video_id:
                logger.info(
                    "Clip %s already published (%s) — skipping",
                    clip_id,
                    existing.youtube_video_id,
                )
                return existing.youtube_video_id
            # done-but-NULL youtube_video_id: the prior run likely uploaded successfully
            # but crashed before the success-path commit wrote youtube_video_id. The
            # idempotency guard is bypassed so the task re-runs, consuming quota again.
            # Log the asymmetry so operators can investigate if it recurs. (Issue 336)
            logger.warning(
                "Clip %s publication row is done but youtube_video_id is NULL — "
                "idempotency guard bypassed, task will re-run and consume quota",
                clip_id,
            )

        clip = await session.get(Clip, cid)
        if not clip or not clip.render_uri:
            raise ValueError(f"Clip {clip_id} has no rendered media to publish")
        creator_id = clip.creator_id
        render_uri = clip.render_uri
        video = await session.get(Video, clip.video_id)
        base_title = (video.title if video and video.title else "New Short").strip()[:90]

        if existing is None:
            pub = ClipPublication(
                clip_id=cid,
                creator_id=creator_id,
                task_id=task_id,
                status=PublishStatus.running,
            )
            session.add(pub)
        else:
            pub = existing
            pub.status = PublishStatus.running
        await session.flush()
        pub_id = pub.id
        await session.commit()

        # Same open session — get_valid_access_token may refresh + commit.
        access_token = await get_valid_access_token(creator_id, session)

    # Reserve quota before the upload (raises QuotaExhaustedError → task retries).
    await consume(COST_DATA_VIDEOS_INSERT)

    try:
        async with alocal_path(render_uri) as local_file:
            video_id = await upload_video(
                access_token,
                local_file,
                title=base_title,
                description="#Shorts",
                privacy_status=settings.YOUTUBE_PUBLISH_PRIVACY,
            )
    except (YouTubeAuthError, YouTubeUploadError) as exc:
        async with db.AdminSessionLocal() as session:
            row = await session.get(ClipPublication, pub_id)
            if row is not None:
                row.status = PublishStatus.failed
                row.error = str(exc)[:500]
                await session.commit()
        # QUOTA ASYMMETRY: consume() already deducted COST_DATA_VIDEOS_INSERT units
        # from the daily quota before the upload failed. Those units are gone — the
        # YouTube API does not refund on upload error. Log so operators can track
        # partial quota consumption during outages or audit spikes. (Issue 336)
        logger.warning(
            "Publish %s failed permanently (%s) — QUOTA ASYMMETRY: %d quota units"
            " consumed before upload failure; units are non-refundable",
            clip_id,
            type(exc).__name__,
            COST_DATA_VIDEOS_INSERT,
        )
        raise

    # Store the returned video id + done, then wire the clip into the outcome loop
    # (Issue 197). Both writes are committed together so a crash between them cannot
    # leave the publication done but the outcome missing.
    now_utc = datetime.now(UTC)
    async with db.AdminSessionLocal() as session:
        row = await session.get(ClipPublication, pub_id)
        if row is not None:
            row.youtube_video_id = video_id
            row.status = PublishStatus.done

        # Upsert ClipOutcome so poll_clip_outcomes can find this clip at the 48h/7d
        # checkpoints.  Guard: never reset final=True (that would re-open a closed
        # measurement cycle and waste YouTube quota).
        existing_outcome = await session.get(ClipOutcome, cid)
        if existing_outcome is None:
            session.add(
                ClipOutcome(
                    clip_id=cid,
                    published_youtube_id=video_id,
                    final=False,
                    fetched_at=now_utc,
                )
            )
        elif not existing_outcome.final:
            # Re-publish or task redelivery: refresh the youtube_id without
            # disturbing views/retention/performed_well already collected.
            existing_outcome.published_youtube_id = video_id
            # Do NOT reset fetched_at — the existing timestamp governs the
            # 48h/7d polling schedule; resetting it would delay the next check.

        await session.commit()
    logger.info("Published clip %s → youtube %s (%s)", clip_id, video_id, "private")
    return video_id


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


@celery.task(name="worker.tasks.run_lifecycle_scan")
def run_lifecycle_scan() -> None:
    """Issue 246 — daily lifecycle-email sweep (first-clip nudge + re-engagement).

    Reads + enqueues only. The shared 48h cap, per-event dedupe, and the
    email_lifecycle opt-out gate all live downstream in ``_run_lifecycle_scan_async``
    and ``send_notification``, so re-running daily is idempotent.
    """
    run_async(_run_lifecycle_scan_async())


@celery.task(name="worker.tasks.reconcile_stripe_ledger")
def reconcile_stripe_ledger() -> None:
    """Issue 205 — daily Stripe↔ledger reconciliation sweep.

    Catches paid Checkout sessions that the webhook never delivered (Stripe
    outage, endpoint down past the retry window). Grants minutes idempotently
    for any paid session whose stripe_session_id has no matching MinutePack row.
    Persistent mismatches emit a PII-free alert log.

    Idempotency relies on UNIQUE(stripe_session_id) in MinutePack — the same
    guarantee that makes the webhook fulfillment path safe for at-least-once
    Stripe delivery. The grant is therefore a clean no-op on duplicates.
    """
    run_async(_reconcile_stripe_ledger_async())


@celery.task(name="worker.tasks.purge_stale_event_logs")
def purge_stale_event_logs() -> None:
    """Issue 250 — GDPR Art. 5(1)(e) storage-limitation Beat task.

    Deletes event_logs rows older than EVENT_LOG_RETENTION_DAYS (default 90).
    Best-effort: a DB failure is logged and swallowed — it must not break the
    Beat worker loop. The async helper mirrors the purge_creator_events pattern
    in event_log.py (same engine, same error posture).
    """
    run_async(_purge_stale_event_logs_async())


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
    log_event("sync_channel_catalog_started", creator_id=creator_id, task_id=self.request.id)
    try:
        run_async(_sync_channel_catalog_async(creator_id, task_id=self.request.id))
    except YouTubeAuthError:
        # Permanent — the token is dead. Don't retry; the next refresh tick
        # will delete the YoutubeToken row via the existing handler.
        # Trigger 4: re-auth-needed notification (Issue 244). entity_id = creator_id
        # so the dedupe key scopes to one notification per creator per auth error.
        try:
            send_notification.delay(creator_id, "reauth_required", creator_id, {})
        except Exception as notify_exc:
            logger.warning(
                "reauth_required notification failed for creator %s: %s",
                creator_id,
                notify_exc,
            )
        raise
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    log_event("sync_channel_catalog_done", creator_id=creator_id, task_id=self.request.id)
    return creator_id


@celery.task(name="worker.tasks.poll_clip_outcomes")
def poll_clip_outcomes() -> None:
    """
    Celery Beat task: fetch YouTube stats for published clips at 48h and 7d checkpoints.
    Sets performed_well = views >= channel_median_views; used as a 3× weight multiplier
    in preference model retraining.
    """
    run_async(_poll_clip_outcomes_async())


@celery.task(name="worker.tasks.sweep_scheduled_publications")
def sweep_scheduled_publications() -> None:
    """Celery Beat task: enqueue publish_to_youtube for due scheduled publications.

    Runs every 5 minutes. Selects ClipPublication rows whose ``scheduled_at`` has
    passed AND ``status == confirmed``, then enqueues ``publish_to_youtube`` for
    each. An ``pg_try_advisory_lock`` guard ensures only one Beat/worker instance
    runs this sweep concurrently — mirrors the pattern used in ``_poll_clip_outcomes``
    and ``_retrain_preference``.

    Idempotency: the task_id written here becomes the Celery task id for the
    enqueued ``publish_to_youtube`` call. The UNIQUE constraint on
    ``clip_publications.task_id`` ensures a second sweep tick for the same row
    (before the first finishes) produces a no-op in the upload task body.
    """
    run_async(_sweep_scheduled_publications_async())


@celery.task(bind=True, max_retries=3, default_retry_delay=60, name="worker.tasks.build_dna")
def build_dna(self, creator_id: str) -> str:
    """
    Build creator DNA patterns, generate brief, store draft profile + embeddings.
    ValueError (data gate failure) is re-raised without retry — it is a permanent error.
    """
    log_event("build_dna_started", creator_id=creator_id, task_id=self.request.id)
    try:
        # self.request.id is the stable idempotency key across at-least-once
        # redelivery of THIS task; a new user-triggered build gets a new id. (Issue 63)
        run_async(_build_dna_async(creator_id, self.request.id))
    except ValueError:
        raise
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    log_event("build_dna_done", creator_id=creator_id, task_id=self.request.id)
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
                scorer = await build_and_save(session, cid)
            except IntegrityError:
                # Concurrent retrain won the version race (hardened in Issue 71).
                await session.rollback()
                logger.info("retrain_preference: version race for creator %s, skip", cid)
            else:
                if scorer is not None:
                    # Best-effort per-retrain offline eval (Issue 202) — never
                    # fails the retrain; the model version is already committed.
                    await _emit_preference_metrics(session, cid)
        finally:
            await session.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": lock_key})


async def _emit_preference_metrics(session: Any, creator_id: uuid.UUID) -> None:
    """Best-effort offline eval of the just-saved preference-model version (Issue 202).

    Runs the efficacy harness (chronological held-out split) for this creator, stores
    {"ndcg_at_5", "map_at_5", "n_eval", "computed_at"} on the newest PreferenceModel row,
    and warn-ratchets against the previous version: a held-out NDCG@5 drop greater than
    PREFERENCE_NDCG_REGRESSION_THRESHOLD emits a warning-severity event. WARN-only by
    design — an eval failure or a regression NEVER fails or rolls back the retrain.
    """
    from sqlalchemy import select

    from config import settings
    from preference.efficacy import DEFAULT_K, evaluate_creator

    try:
        cm = await evaluate_creator(session, creator_id, k=DEFAULT_K)
        if cm is None:
            logger.info(
                "preference eval skipped for creator %s (insufficient held-out labels)",
                creator_id,
            )
            return
        rows = (
            (
                await session.execute(
                    select(PreferenceModel)
                    .where(PreferenceModel.creator_id == creator_id)
                    .order_by(PreferenceModel.version.desc())
                    .limit(2)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return
        newest = rows[0]
        previous = rows[1] if len(rows) > 1 else None
        ndcg = float(cm.ndcg["dna_preference"])
        newest.metrics_jsonb = {
            "ndcg_at_5": ndcg,
            "map_at_5": float(cm.map["dna_preference"]),
            "n_eval": cm.n_eval,
            "computed_at": datetime.now(UTC).isoformat(),
        }
        await session.commit()
        log_event(
            "preference_metrics_computed",
            creator_id=str(creator_id),
            version=newest.version,
            ndcg_at_5=round(ndcg, 4),
            n_eval=cm.n_eval,
        )
        prev_ndcg = (previous.metrics_jsonb or {}).get("ndcg_at_5") if previous else None
        if (
            prev_ndcg is not None
            and (float(prev_ndcg) - ndcg) > settings.PREFERENCE_NDCG_REGRESSION_THRESHOLD
        ):
            log_event(
                "preference_metrics_regression",
                severity="warning",
                creator_id=str(creator_id),
                version=newest.version,
                ndcg_at_5=round(ndcg, 4),
                previous_ndcg_at_5=round(float(prev_ndcg), 4),
                threshold=settings.PREFERENCE_NDCG_REGRESSION_THRESHOLD,
            )
            logger.warning(
                "preference model v%d for creator %s regressed held-out NDCG@5 "
                "(%.4f -> %.4f, threshold %.2f) — warn-only, retrain kept",
                newest.version,
                creator_id,
                float(prev_ndcg),
                ndcg,
                settings.PREFERENCE_NDCG_REGRESSION_THRESHOLD,
            )
    except Exception:
        with contextlib.suppress(Exception):
            await session.rollback()
        logger.warning(
            "preference metrics emission failed for creator %s — retrain unaffected",
            creator_id,
            exc_info=True,
        )


async def _creator_id_for_video(video_id: str) -> str | None:
    """Return creator_id string for a video, or None on any error.

    Used at task-started time for structured event context. Tolerates missing
    rows (e.g. a race between task dispatch and DB commit) by returning None so
    the log_event still fires — just without the creator dimension.
    """
    try:
        async with db.AdminSessionLocal() as session:
            video = await session.get(Video, uuid.UUID(video_id))
            return str(video.creator_id) if video else None
    except Exception:
        return None


async def _creator_id_for_clip(clip_id: str) -> str | None:
    """Return creator_id string for a clip, or None on any error."""
    try:
        async with db.AdminSessionLocal() as session:
            clip = await session.get(Clip, uuid.UUID(clip_id))
            return str(clip.creator_id) if clip else None
    except Exception:
        return None


async def _set_status(video_id: str, status: IngestStatus, *, reason: str | None = None) -> None:
    async with db.AdminSessionLocal() as session:
        video = await session.get(Video, uuid.UUID(video_id))
        if video:
            video.ingest_status = status
            # Record WHY on failure so the dashboard isn't a black box; clear any
            # stale reason on a transition that isn't a failure (e.g. a retry that
            # reaches running/done), so a recovered video doesn't keep showing it.
            video.failure_reason = reason if status == IngestStatus.failed else None
            await session.commit()


# Map a stage + exception to a short, creator-safe failure reason. Deliberately
# coarse (stage + category, never the raw exception message) so we can never leak
# a presigned URL, file path, token, or other secret into a user-visible field.
def _humanize_failure(exc: Exception, stage: str) -> str:
    name = type(exc).__name__
    if isinstance(exc, FileNotFoundError) or "NoSuchKey" in name or "source_uri" in str(exc):
        return "We couldn't read your uploaded file from storage. Please re-upload and try again."
    if stage == "transcribe":
        return "Transcription failed. This can be a temporary service issue — please try again."
    if stage == "signals":
        return "We couldn't analyse this video's content. Please try again."
    if stage == "ingest":
        return "We couldn't process this video file. Check the file is a valid video and re-upload."
    return "Processing failed. Please try again, or contact support if it persists."


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

            # Idempotency short-circuit (Issue 105 — Fix 2; updated migration 0039):
            # a prior run that finished the audio-extract + upload set `audio_uri`.
            # Returning here is safe — `audio_uri` is the durable progress marker
            # (AWS Lambda idempotent-retry doctrine: make progress persistent and
            # detectable, skip work already persisted). `source_uri` now stays the
            # ORIGINAL VIDEO (the renderer needs it), so we key the probe off
            # `audio_uri`, not a `.wav` suffix on source_uri.
            # Issue 336: probe size first — zero-byte or unprobeable WAV re-extracts.
            if video.audio_uri:
                _wav_ok = False
                try:
                    async with alocal_path(video.audio_uri) as _wav_p:
                        _wav_ok = _wav_p.stat().st_size > 0
                except Exception:
                    pass
                if _wav_ok:
                    logger.info("Video %s audio ingest already done — skipping", video_id)
                    await aemit(
                        video_id,
                        "step",
                        label="ingest_skipped",
                        stage="ingest",
                        reason="already_done",
                    )
                    return
                logger.warning(
                    "Video %s audio WAV zero-byte or unprobeable — re-extracting", video_id
                )

            source_uri = video.source_uri
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
                # Store the audio derivative on its own column; LEAVE source_uri as
                # the original video so the renderer can extract keyframes. The video
                # is retained for SOURCE_MEDIA_RETENTION_HOURS and purged by
                # `purge_stale_source_media` (migration 0039 — fixes the broken render
                # loop where source_uri was overwritten with audio + the mp4 deleted).
                video.audio_uri = audio_uri
                if duration_s and not video.duration_s:
                    video.duration_s = duration_s
                if duration_s:
                    from billing.ledger import deduct_for_video

                    await aemit(video_id, "step", label="deduct_minutes", stage="ingest")
                    await deduct_for_video(video.id, video.creator_id, duration_s, session)
                await session.commit()

        # NB: the original video is deliberately NOT deleted here anymore. The
        # renderer needs it (9:16 active-speaker reframe extracts keyframes), so it
        # is retained under `source_uri` for the render window and purged by
        # `purge_stale_source_media` at SOURCE_MEDIA_RETENTION_HOURS after
        # `ingest_done_at` — the documented COMPLIANCE.md posture. (migration 0039)
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
            if not video or not video.audio_uri:
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

            audio_uri = video.audio_uri

        async with alocal_path(audio_uri) as audio_path:
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
            if not video or not video.audio_uri:
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

            audio_uri = video.audio_uri
            retention_result = await session.execute(
                select(RetentionCurve).where(RetentionCurve.video_id == video.id)
            )
            retention_points = list(retention_result.scalars())

        async with alocal_path(audio_uri) as audio_path:
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


@dataclass(frozen=True)
class _ClipRenderPlan:
    """The fields needed to encode one clip, snapshotted out of the DB session.

    Snapshotting into a plain object means the session can close before the
    multi-second encode/upload, and lets several clips of the same video share a
    single downloaded source (``source_uri`` is identical across them).
    """

    source_uri: str
    setup_start_s: float | None
    start_s: float
    end_s: float
    peak_s: float | None
    clip_duration_s: float
    style_preset: dict | None
    transcript_segments: list[dict] | None


async def _load_clip_render_plan(clip_id: str) -> _ClipRenderPlan | None:
    """Lock the clip row, flip it to ``running``, and snapshot its render plan.

    Returns ``None`` (and emits the terminal ``done`` event) when the clip is
    already rendered — the idempotent skip under at-least-once delivery
    (Issue 62). ``with_for_update`` serializes concurrent redeliveries of the
    same clip at the Postgres row level so two deliveries cannot both pass the
    done-check and double-encode (Issue 76).
    """
    from worker.progress import aemit

    async with db.AdminSessionLocal() as session:
        clip = await session.get(Clip, uuid.UUID(clip_id), with_for_update=True)
        if not clip:
            raise ValueError(f"Clip {clip_id} not found")
        if clip.render_status == RenderStatus.done and clip.render_uri:
            logger.info("Clip %s already rendered — skipping", clip_id)
            await aemit(clip_id, "done", stage="render", message="Clip already rendered.")
            return None
        if clip.render_status == RenderStatus.running and clip.render_uri:
            # Anomalous state: the clip has a render_uri but is not marked done. This
            # can occur if a prior render uploaded the file but crashed before setting
            # the status to done. Re-rendering is safe (idempotent upload key); log
            # the anomaly so an operator can investigate if it recurs. (Issue 336)
            logger.warning(
                "Clip %s is running with existing render_uri=%s — re-rendering (anomalous state)",
                clip_id,
                clip.render_uri,
            )
        video = await session.get(Video, clip.video_id)
        if not video or not video.source_uri:
            raise ValueError(f"Source video not available for clip {clip_id}")
        # Snapshot the timing fields into locals — session closes at the end of
        # this with-block, after which `clip.start_s` would emit an implicit
        # SELECT to refresh the expired attribute (Issue 38 Wave 1).
        setup_start_s = clip.setup_start_s
        start_s = clip.start_s
        end_s = clip.end_s
        peak_s = clip.peak_s  # for the opt-in auto-zoom punch-in (Issue 184)
        clip.render_status = RenderStatus.running
        style_preset = clip.style_preset  # snapshot before session closes
        # Transcript segments only needed when style_preset selects a caption
        # style — skip the load otherwise (Issue 133). Gate off the single
        # source of truth so new styles (e.g. bold_pop_highlight, Issue 183)
        # don't silently render captionless.
        from clip_engine.captions import VALID_STYLES as _CAPTION_STYLES

        transcript_segments: list[dict] | None = None
        if style_preset and style_preset.get("subtitle") in _CAPTION_STYLES:
            transcript = await session.get(Transcript, video.id)
            if transcript and isinstance(transcript.segments_jsonb, dict):
                segments = transcript.segments_jsonb.get("segments")
                if isinstance(segments, list):
                    transcript_segments = segments
        await session.commit()
        return _ClipRenderPlan(
            source_uri=video.source_uri,
            setup_start_s=setup_start_s,
            start_s=start_s,
            end_s=end_s,
            peak_s=peak_s,
            clip_duration_s=end_s - (setup_start_s if setup_start_s is not None else start_s),
            style_preset=style_preset,
            transcript_segments=transcript_segments,
        )


async def _encode_and_upload_clip(clip_id: str, src: Path, plan: _ClipRenderPlan) -> None:
    """Encode one clip from an already-downloaded ``src`` and mark it ``done``.

    Split out of the download so several clips of one video can reuse a single
    source file (auto-render fans a video into N clips — see
    ``_render_video_clips_async``). The caller owns ``src``'s lifetime.
    """
    import tempfile

    from clip_engine.render import render_clip_file
    from worker.progress import aemit
    from worker.storage import aupload_file

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
            clip_duration_s=plan.clip_duration_s,
        )
        await asyncio.to_thread(
            render_clip_file,
            source_path=src,
            start_s=plan.setup_start_s if plan.setup_start_s is not None else plan.start_s,
            end_s=plan.end_s,
            out_path=out_path,
            style_preset=plan.style_preset,
            transcript_segments=plan.transcript_segments,
            peak_s=plan.peak_s,
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


async def _render_clip_async(clip_id: str, src: Path | None = None) -> None:
    """Render a clip. Issue 92 progress wired — uses ``clip_id`` as the
    SSE stream key for the same deterministic-lookup reason as the upload
    chain. Per-frame ffmpeg progress is intentionally NOT parsed here (the
    encode runs as a single ``asyncio.to_thread`` shell-out); we emit
    step-level boundaries instead — start/encode/upload/done.

    ``src`` lets a caller pass an already-downloaded source file so a batch of
    clips from one video shares a single R2 download (see
    ``_render_video_clips_async``). When ``None`` the source is downloaded here
    for the single-clip path (the manual ``POST /clips/{id}/render`` endpoint).
    """
    from worker.progress import aemit
    from worker.storage import alocal_path

    try:
        await aemit(clip_id, "step", label="render_start", stage="render")
        plan = await _load_clip_render_plan(clip_id)
        if plan is None:  # already rendered — skip emitted by the loader
            return
        if src is not None:
            await _encode_and_upload_clip(clip_id, src, plan)
        else:
            await aemit(clip_id, "step", label="download_source", stage="render")
            async with alocal_path(plan.source_uri) as downloaded:
                await _encode_and_upload_clip(clip_id, downloaded, plan)
    except Exception as exc:
        # Don't promise a retry here — whether render_clip retries depends on the
        # error class (permanent ValueError/FileNotFoundError are terminal). Keep
        # the user-facing message neutral; the clip's render_status is the source
        # of truth the UI polls.
        await aemit(
            clip_id,
            "error",
            stage="render",
            message="Render failed.",
            exc_type=type(exc).__name__,
        )
        raise


async def _render_video_clips_async(video_id: str, clip_ids: list[str]) -> None:
    """Render every clip in ``clip_ids`` (all from ``video_id``) downloading the
    source from R2 exactly ONCE.

    Auto-render fans one upload into N clips; rendering them as N independent
    ``render_clip`` tasks re-downloaded the full source N times (the dominant
    cost for multi-clip videos). Here the source is fetched once and reused
    across every clip's encode.

    Per-clip failures are isolated: a permanent error marks that clip ``failed``
    and moves on; a transient error re-enqueues the single-clip ``render_clip``
    so its existing backoff/retry path applies — neither aborts the batch nor
    re-downloads the source for its siblings. A failure to obtain the source
    itself propagates so the task wrapper can classify + retry the whole batch.
    """
    from worker.storage import alocal_path

    if not clip_ids:
        return
    async with db.AdminSessionLocal() as session:
        video = await session.get(Video, uuid.UUID(video_id))
        if not video or not video.source_uri:
            raise ValueError(f"Source video not available for video {video_id}")
        source_uri = video.source_uri

    async with alocal_path(source_uri) as src:
        for clip_id in clip_ids:
            try:
                await _render_clip_async(clip_id, src=src)
            except (ValueError, FileNotFoundError) as exc:
                # Permanent for THIS clip (bad range / missing row) — retrying
                # cannot fix it. Mark failed and keep rendering the siblings.
                await _set_clip_render_status(clip_id, RenderStatus.failed)
                RENDER_FAILURES_TOTAL.labels(task="render_video_clips").inc()
                log_event(
                    "render_video_clip_failed_permanent",
                    video_id=video_id,
                    clip_id=clip_id,
                    exc_type=type(exc).__name__,
                )
            except Exception as exc:  # noqa: BLE001 — isolate per-clip transient errors
                # Transient (ffmpeg/upload blip) — hand THIS clip to the
                # single-clip task so its backoff/retry applies, without
                # re-downloading the shared source for the rest of the batch.
                await _set_clip_render_status(clip_id, RenderStatus.failed)
                RENDER_FAILURES_TOTAL.labels(task="render_video_clips").inc()
                log_event(
                    "render_video_clip_retry_enqueued",
                    video_id=video_id,
                    clip_id=clip_id,
                    exc_type=type(exc).__name__,
                )
                try:
                    render_clip.delay(clip_id)
                except Exception:  # noqa: BLE001 — best-effort re-enqueue
                    logger.warning("render retry enqueue failed clip=%s", clip_id)


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

    from config import settings
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
            brief_text, _brief_usage = await asyncio.to_thread(
                generate_brief,
                patterns,
                channel_title,
                stated_identity,
                job_id if progress_enabled else None,
            )

            from billing.ledger import record_llm_usage

            await record_llm_usage(
                creator_uuid,
                _brief_usage,
                settings.COST_PER_MTOK_IN_SONNET,
                settings.COST_PER_MTOK_OUT_SONNET,
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
            # Trigger 2: DNA-built notification (Issue 244).
            # entity_id = creator_id (one notification per creator per DNA build;
            # the job_id would differ on every build, so use creator_id to bound to
            # one notification per creator per day at most via the daily beat cadence).
            try:
                send_notification.delay(creator_id, "dna_built", creator_id, {})
            except Exception as notify_exc:
                logger.warning(
                    "dna_built notification failed for creator %s: %s",
                    creator_id,
                    notify_exc,
                )
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


async def _sweep_scheduled_publications_async() -> None:
    """Find due confirmed scheduled publications and enqueue publish_to_youtube.

    Selection criteria:
    - ``status == confirmed``  — creator explicitly approved the schedule.
    - ``scheduled_at <= now()`` — the target time has arrived.
    - ``platform == youtube``  — only YouTube is supported in this release.

    For each matching row the function:
    1. Acquires the global session-level advisory lock (non-blocking). A concurrent
       sweep tick or a redelivered Beat message returns immediately.
    2. Re-reads qualifying rows inside the lock so any rows already transitioned
       by a concurrent pick are excluded.
    3. Transitions ``status → pending`` and sets ``task_id`` to the newly
       enqueued Celery task id BEFORE committing — the UNIQUE constraint on
       task_id is the idempotency guard that prevents double-posting if this
       sweep fires twice before the upload task runs.
    4. Commits, then enqueues ``publish_to_youtube``. Commit-before-enqueue
       is intentional: the row persists even if the Celery broker is temporarily
       unavailable.
    """
    from sqlalchemy import select, text

    now = datetime.now(UTC)
    lock_key = "sweep_scheduled_publications"

    async with db.AdminSessionLocal() as session:
        acquired = (
            await session.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:k))"), {"k": lock_key}
            )
        ).scalar_one()
        if not acquired:
            logger.info("advisory lock held — skipping sweep_scheduled_publications")
            return

        try:
            rows = (
                (
                    await session.execute(
                        select(ClipPublication).where(
                            ClipPublication.status == PublishStatus.confirmed,
                            ClipPublication.scheduled_at.isnot(None),
                            ClipPublication.scheduled_at <= now,
                            ClipPublication.platform == PublishPlatform.youtube,
                        )
                    )
                )
                .scalars()
                .all()
            )

            if not rows:
                return

            # Transition rows to pending and assign a task_id before committing.
            # Using Celery's apply_async(task_id=…) lets us set the task_id upfront
            # so the UNIQUE constraint guards against a double-enqueue race.
            to_enqueue: list[tuple[uuid.UUID, str]] = []
            for pub in rows:
                new_task_id = str(uuid.uuid4())
                pub.status = PublishStatus.pending
                pub.task_id = new_task_id
                pub.updated_at = now
                to_enqueue.append((pub.clip_id, new_task_id))
                logger.info(
                    "sweep_scheduled_publications: enqueuing clip=%s task_id=%s",
                    pub.clip_id,
                    new_task_id,
                )

            await session.commit()

            # Enqueue after commit so the UNIQUE task_id row already exists — a
            # race where the task runs before this loop finishes is impossible
            # because Celery picks up the task from the broker after the apply_async
            # call, which happens after the commit.
            for clip_id, task_id in to_enqueue:
                publish_to_youtube.apply_async(
                    args=[str(clip_id)],
                    task_id=task_id,
                )
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": lock_key})


# Minimum number of comparable published Shorts before we'll judge performed_well.
# Below this the format-matched median is too noisy to trust, so we defer the judgment
# (leave performed_well=None) rather than mislabel — honest "not enough data yet". (Issue 201)
_MIN_COMPARABLE_SHORTS = 3


def _shorts_baseline_median(shorts_views: list[int]) -> float | None:
    """Format-matched baseline for `performed_well` (Issue 201): the median of the
    creator's published SHORT view counts (Shorts-vs-Shorts).

    Returns None when fewer than `_MIN_COMPARABLE_SHORTS` comparable Shorts exist — an
    honest "can't judge yet" instead of comparing a Short against a 1–2 sample median or
    (the original bug) the full-video VideoMetrics.views median, which sits on a wildly
    different view scale and marked nearly every Short performed_well=False.
    """
    if len(shorts_views) < _MIN_COMPARABLE_SHORTS:
        return None
    import statistics

    return statistics.median(shorts_views)


async def _poll_clip_outcomes_async() -> None:
    """
    Find published clips past the 48h or 7d checkpoints, fetch their YouTube stats,
    and set performed_well = views >= median of the creator's COMPARABLE published Shorts
    (format-matched — Issue 201; not the full-video view median).
    """
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

                # Whether each row qualified via the 7d (terminal) checkpoint — captured
                # BEFORE we overwrite fetched_at. (Issue 70)
                terminal = {o.clip_id: o.fetched_at < cutoff_7d for o in outcomes}

                # Pass 1 — fetch + record views for this creator's due outcomes. We set
                # views first so the comparable-Shorts baseline below can include this
                # batch (autoflush makes the updates visible to the next SELECT).
                fetched: list[ClipOutcome] = []
                for outcome in outcomes:
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
                    fetched.append(outcome)

                # Comparable-unit baseline (Issue 201): median over the creator's published
                # SHORT outcome views — Shorts-vs-Shorts — NOT the full-video VideoMetrics
                # median (wrong unit). Includes this batch's freshly-set views via autoflush.
                shorts_views_result = await session.execute(
                    select(ClipOutcome.views)
                    .join(Clip, Clip.id == ClipOutcome.clip_id)
                    .where(
                        Clip.creator_id == creator_id,
                        Clip.format == ClipFormat.short,
                        ClipOutcome.views.isnot(None),
                    )
                )
                shorts_views = [r[0] for r in shorts_views_result.all()]
                channel_median = _shorts_baseline_median(shorts_views)

                # Pass 2 — judge + finalize. Below the comparable-Shorts floor we DEFER
                # the verdict (leave performed_well as-is/None) rather than mislabel.
                for outcome in fetched:
                    if outcome.views is not None and channel_median is not None:
                        outcome.performed_well = outcome.views >= channel_median
                    outcome.fetched_at = now
                    if terminal[outcome.clip_id]:
                        # 7d checkpoint recorded — never poll this outcome again.
                        outcome.final = True
                    logger.info(
                        "ClipOutcome clip=%s views=%s performed_well=%s final=%s "
                        "(comparable_shorts=%d, baseline=%s)",
                        outcome.clip_id,
                        outcome.views,
                        outcome.performed_well,
                        outcome.final,
                        len(shorts_views),
                        channel_median,
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


async def _brand_kit_style(session: Any, creator_id: uuid.UUID) -> dict:
    """Return the creator's saved brand-kit render style, or ``{}`` if none.

    Used to seed auto-rendered clips (auto-render) so captions / aspect / background
    match the creator's chosen style without a manual pick. Mirrors the brand-kit
    read in ``routers/clips.py::render_clip``; a copy is returned so the caller can
    assign it onto a clip without aliasing the ORM-tracked ``CreatorStyle.style``.
    """
    from sqlalchemy import select

    from models import CreatorStyle

    row = await session.scalar(select(CreatorStyle).where(CreatorStyle.creator_id == creator_id))
    return dict(row.style) if row and isinstance(row.style, dict) else {}


async def _generate_clips_async(video_id: str) -> None:
    """Generate ranked clip candidates for a fully-ingested video.

    Wave-3 Fix E: emits progress events on the **same** ``task:{video_id}:events``
    stream that the upload chain (`_ingest`/`_transcribe`/`_signals`) uses, so
    the SSE consumer stays subscribed through clip generation. The terminal
    ``done`` event now fires here (not in `_signals_async`) — that's the
    moment the user-visible work is actually complete.
    """
    from sqlalchemy import select

    from clip_engine.ranking import load_existing_clips, persist_ranked_clips, score_and_rank
    from config import settings
    from dna.profile import get_active
    from models import Signals, Transcript
    from worker.progress import aemit

    try:
        await aemit(video_id, "step", label="generate_clips_start", stage="generate_clips")

        video_uuid = uuid.UUID(video_id)

        # Capture creator_id and per-email vars outside the session so they are
        # available for the notification enqueue after the session closes.
        clip_creator_id: uuid.UUID | None = None
        clip_video_title: str | None = None
        clip_creator_name: str | None = None
        # Clip ids to auto-render once generation commits (auto-render). Captured
        # inside the session so post-commit attribute expiry can't trigger a lazy
        # refresh after it closes; the actual enqueue happens after the session.
        auto_render_clip_ids: list[str] = []

        async with db.AdminSessionLocal() as session:
            video = await session.get(Video, video_uuid)
            if not video:
                raise ValueError(f"Video {video_id} not found")
            clip_video_title = video.title

            clip_creator_id = video.creator_id

            # Load creator channel_title for the clips_ready email greeting.
            # Captured here (inside the session) so it is available post-close.
            from models import Creator as _Creator

            _creator_row = await session.get(_Creator, clip_creator_id)
            clip_creator_name = (_creator_row.channel_title if _creator_row else None) or "there"

            # Idempotency guard (Issue 46): a late retry on a video whose clips are
            # already rendered is a no-op. Without this, the score-and-persist path
            # would re-extract candidates and insert duplicate pending rows alongside
            # the already-done clips.
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
            timeline = signals.timeline_jsonb

            transcript = await session.get(Transcript, video_uuid)
            transcript_segments = (
                transcript.segments_jsonb.get("segments", []) if transcript else []
            )

            dna_profile = await get_active(session, video.creator_id)
            dna_brief = dna_profile.brief_text if dna_profile else None

            # Idempotency guard (Issue 61): existing clips mean a redelivered task
            # must not re-score (LLM cost) or re-insert (would cascade-delete
            # feedback/outcomes). Captured here; scoring runs after this session
            # closes.
            existing_clips = await load_existing_clips(session, video_uuid, clip_creator_id)

        # Issue 82b (pool starvation): the per-candidate LLM scoring round-trip
        # (30–120 s) runs with NO admin DB session held — the read session above
        # is closed, and persistence reacquires a fresh one below.
        await aemit(video_id, "step", label="score_and_rank", stage="generate_clips")
        ranked: list[dict] = []
        if not existing_clips:
            ranked = await score_and_rank(
                video_id=video_uuid,
                creator_id=clip_creator_id,
                timeline=timeline,
                dna_brief=dna_brief,
                transcript_segments=transcript_segments,
                max_candidates=settings.CLIPS_PER_VIDEO_DEFAULT,
                ledger_session_factory=db.AdminSessionLocal,
            )

        async with db.AdminSessionLocal() as session:
            # persist_ranked_clips re-runs the existing-clips guard on THIS
            # session, so the already-generated path returns clips attached to
            # the live session (the pre-scoring snapshot above is detached).
            clips = await persist_ranked_clips(session, video_uuid, clip_creator_id, ranked)

            logger.info("Generated %d clips for video %s", len(clips), video_id)

            # Auto-render (auto-render): uploading the video already consented to —
            # and was charged — the minutes (deduct_for_video at ingest), so the
            # Review queue should be watch-ready with zero manual steps. Seed each
            # clip's render style from the creator's saved brand kit, then enqueue a
            # render below. render_clip is idempotent (skips clips already done) and
            # charges no additional minutes. AUTO_RENDER_TOP_N caps how many of the
            # ranked clips render immediately (0 = all generated candidates).
            if settings.AUTO_RENDER_CLIPS and clips:
                kit_style = await _brand_kit_style(session, clip_creator_id)
                top_n = settings.AUTO_RENDER_TOP_N
                # Only sort when we actually cap — the default (render all) path
                # renders every clip regardless of order, so skip the sort.
                if top_n and top_n > 0:
                    ordered = sorted(
                        clips, key=lambda c: c.rank if c.rank is not None else 1_000_000
                    )[:top_n]
                else:
                    ordered = clips
                seeded = False
                for clip in ordered:
                    # Never clobber a style the creator already chose — only seed
                    # when the clip has no preset yet.
                    if kit_style and not clip.style_preset:
                        clip.style_preset = kit_style
                        seeded = True
                    auto_render_clip_ids.append(str(clip.id))
                if seeded:
                    await session.commit()

        # Terminal event — the upload-to-clips pipeline is now done.
        # Sets TTL on the stream so a creator who comes back later can still see it.
        await aemit(
            video_id,
            "done",
            stage="generate_clips",
            message=f"Generated {len(clips)} clip(s).",
            clip_count=len(clips),
        )

        # Enqueue the auto-render batch AFTER the session commits + the terminal
        # `done` fires. Commit-before-enqueue (same posture as
        # sweep_scheduled_publications) so the seeded style writes persist even if
        # the broker is briefly down. One batch task (render_video_clips) renders
        # every clip from a single source download instead of N tasks each
        # re-downloading the full source — the dominant cost for multi-clip videos.
        # An enqueue failure is logged, never raised — it must not fail clip
        # generation or trigger a refund; the creator can still render on demand.
        if auto_render_clip_ids:
            try:
                render_video_clips.delay(video_id, auto_render_clip_ids)
            except Exception as enqueue_exc:  # noqa: BLE001 — best-effort enqueue
                logger.warning(
                    "auto-render enqueue failed video=%s count=%d err=%s",
                    video_id,
                    len(auto_render_clip_ids),
                    type(enqueue_exc).__name__,
                )
        if auto_render_clip_ids:
            log_event(
                "auto_render_enqueued",
                creator_id=str(clip_creator_id) if clip_creator_id else None,
                video_id=video_id,
                count=len(auto_render_clip_ids),
            )

        # Trigger 1: clips-ready notification (Issue 244 / delivers #193).
        # entity_id = video_id so dedupe prevents duplicate notifications on retry.
        if clip_creator_id is not None:
            try:
                send_notification.delay(
                    str(clip_creator_id),
                    "clips_ready",
                    video_id,
                    {
                        "clip_count": len(clips),
                        "creator_name": clip_creator_name or "there",
                        "video_title": clip_video_title or "your video",
                        # review_url is an absolute link built from APP_BASE_URL so the
                        # clips_ready template renders a clickable button in prod.
                        "review_url": f"{settings.APP_BASE_URL}/app/review",
                    },
                )
            except Exception as notify_exc:
                logger.warning(
                    "clips_ready notification failed for video %s creator %s: %s",
                    video_id,
                    clip_creator_id,
                    notify_exc,
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

    from sqlalchemy import and_, or_, select, text, update

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
                select(Video.id, Video.source_uri, Video.audio_uri).where(
                    and_(
                        or_(Video.source_uri.isnot(None), Video.audio_uri.isnot(None)),
                        Video.ingest_done_at.is_not(None),
                        Video.ingest_done_at < cutoff,
                    )
                )
            )
            # `.all()` returns Sequence[Row[...]] which is Row-iterable and unpacks
            # to (uuid, str|None, str|None) per row. Untyped because Row's bracketed
            # type doesn't equal `tuple[...]` in the eyes of mypy. Both the video
            # (source_uri) and its extracted audio (audio_uri) are purged once the
            # video is past its retention window — the audio derivative isn't needed
            # after signals ran (migration 0039). The loop skips defensive None.
            targets = result.all()
        finally:
            await session.execute(
                text("SELECT pg_advisory_unlock(hashtext(:k))"),
                {"k": "purge_stale_source_media"},
            )

    if not targets:
        return

    # Purge each blob independently and only null the column whose file we actually
    # deleted, so a transient R2 error on one leaves the other's pointer intact for
    # the next sweep (retry-safe; the R2 `source/` lifecycle rule sweeps any orphan).
    src_purged: list[uuid.UUID] = []
    audio_purged: list[uuid.UUID] = []
    for video_id, source_uri, audio_uri in targets:
        if source_uri is not None:
            try:
                await adelete_file(source_uri)
                src_purged.append(video_id)
            except Exception as exc:
                logger.warning("Failed to purge source media for video %s: %s", video_id, exc)
        if audio_uri is not None:
            try:
                await adelete_file(audio_uri)
                audio_purged.append(video_id)
            except Exception as exc:
                logger.warning("Failed to purge audio media for video %s: %s", video_id, exc)

    if not src_purged and not audio_purged:
        return

    async with db.AdminSessionLocal() as session:
        if src_purged:
            await session.execute(
                update(Video).where(Video.id.in_(src_purged)).values(source_uri=None)
            )
        if audio_purged:
            await session.execute(
                update(Video).where(Video.id.in_(audio_purged)).values(audio_uri=None)
            )
        await session.commit()
        logger.info(
            "Purged media: %d source video(s), %d audio file(s)",
            len(src_purged),
            len(audio_purged),
        )


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
            # Trigger 5: trial-ending notification (Issue 244).
            # entity_id = ISO date of expiry so one notification fires per creator
            # per trial-ending day regardless of how many beat ticks run.
            entity_id = (
                trial_ends_at.date().isoformat() if trial_ends_at else now.date().isoformat()
            )
            try:
                send_notification.delay(str(cid), "trial_ending", entity_id, {})
            except Exception as notify_exc:
                logger.warning(
                    "trial_ending notification failed for creator %s: %s",
                    cid,
                    notify_exc,
                )


# ── Lifecycle email sequence (Issue 246) ──────────────────────────────────────

# All event types that count against the shared lifecycle frequency budget.
_LIFECYCLE_EVENT_TYPES = ("welcome", "first_clip_nudge", "re_engagement")


async def _lifecycle_capped(session: Any, creator_id: uuid.UUID, now: datetime) -> bool:
    """Return True if a lifecycle email was already delivered to this creator
    within ``LIFECYCLE_FREQUENCY_CAP_HOURS`` (the shared budget across welcome /
    nudge / re-engagement).

    This is the cross-event cap the per-event dedupe_key cannot enforce on its
    own: it stops a welcome and a nudge from landing on the same day.
    """
    from datetime import timedelta

    from sqlalchemy import select

    from config import settings
    from models import NotificationDelivery

    cutoff = now - timedelta(hours=settings.LIFECYCLE_FREQUENCY_CAP_HOURS)
    existing = await session.scalar(
        select(NotificationDelivery.id)
        .where(NotificationDelivery.creator_id == creator_id)
        .where(NotificationDelivery.event_type.in_(_LIFECYCLE_EVENT_TYPES))
        .where(NotificationDelivery.created_at >= cutoff)
        .limit(1)
    )
    return existing is not None


async def _run_lifecycle_scan_async() -> None:
    """Issue 246 — daily lifecycle-sequence sweep.

    Reads + enqueues only (no mutation). Uses AdminSessionLocal (BYPASSRLS) for
    a cross-tenant sweep, mirroring ``_expire_trials_async``. Two cohorts:

      * FIRST-CLIP NUDGE — creators older than ``LIFECYCLE_NUDGE_AFTER_DAYS``
        with no Video rows. entity_id = creator_id ⇒ once ever per creator.
      * RE-ENGAGEMENT — creators with ≥1 Video but no ClipFeedback within
        ``LIFECYCLE_INACTIVITY_DAYS``. entity_id = a stable period bucket so it
        recurs at most once per inactivity window.

    The shared 48h frequency cap (``_lifecycle_capped``) is checked before every
    enqueue so welcome / nudge / re-engagement share one budget. The
    send_notification task additionally gates on prefs.email_lifecycle and
    dedupes via notification_deliveries, so opted-out creators get none and a
    daily re-scan of the same quiet creator does not re-send.
    """
    from datetime import timedelta

    from sqlalchemy import exists, select

    from config import settings
    from models import ClipFeedback, Creator, Video

    # CAN-SPAM safety gate: with no physical postal address configured, no
    # lifecycle email may legally be sent. Short-circuit the whole sweep so we do
    # not enqueue tasks that would each individually skip. (send_notification
    # enforces the same gate defensively for the welcome path.)
    if not settings.MAILING_ADDRESS:
        logger.info(
            "run_lifecycle_scan: MAILING_ADDRESS unset — skipping lifecycle sweep "
            "(CAN-SPAM safety gate)"
        )
        return

    now = datetime.now(UTC)

    async with db.AdminSessionLocal() as session:
        # ── First-clip nudge: signed up ≥ N days ago, never uploaded a video ──
        nudge_cutoff = now - timedelta(days=settings.LIFECYCLE_NUDGE_AFTER_DAYS)
        has_video = exists().where(Video.creator_id == Creator.id)
        nudge_rows = await session.execute(
            select(Creator.id).where(Creator.created_at <= nudge_cutoff).where(~has_video)
        )
        for (cid,) in nudge_rows.all():
            if await _lifecycle_capped(session, cid, now):
                continue
            try:
                send_notification.delay(str(cid), "first_clip_nudge", str(cid), {})
            except Exception as notify_exc:
                logger.warning(
                    "first_clip_nudge notification enqueue failed creator=%s err=%s",
                    cid,
                    type(notify_exc).__name__,
                )

        # ── Re-engagement: has a video but no recent clip review ─────────────
        inactivity_cutoff = now - timedelta(days=settings.LIFECYCLE_INACTIVITY_DAYS)
        # Stable period bucket so the dedupe_key recurs at most once per the
        # documented ~14-day cadence. A DAILY bucket would re-fire roughly every
        # 48h (only blocked by the shared frequency cap); flooring the ordinal day
        # to a LIFECYCLE_INACTIVITY_DAYS-wide window means a dormant creator gets
        # at most one re-engagement email per inactivity window.
        period_window = now.toordinal() // settings.LIFECYCLE_INACTIVITY_DAYS
        period_bucket = f"reengage-{period_window}"
        recent_feedback = exists().where(
            (ClipFeedback.creator_id == Creator.id) & (ClipFeedback.created_at >= inactivity_cutoff)
        )
        reengage_rows = await session.execute(
            select(Creator.id)
            .where(exists().where(Video.creator_id == Creator.id))
            .where(~recent_feedback)
        )
        for (cid,) in reengage_rows.all():
            if await _lifecycle_capped(session, cid, now):
                continue
            try:
                send_notification.delay(str(cid), "re_engagement", period_bucket, {})
            except Exception as notify_exc:
                logger.warning(
                    "re_engagement notification enqueue failed creator=%s err=%s",
                    cid,
                    type(notify_exc).__name__,
                )


async def _reconcile_stripe_ledger_async() -> None:
    """Issue 205 — compare recent paid Stripe sessions against the MinutePack ledger.

    Uses ``AdminSessionLocal`` (BYPASSRLS) — same pattern as ``refund_for_video``
    and ``_retrain_preference_async``. This is a cross-tenant system sweep with no
    per-creator request context.

    For each paid Stripe session that lacks a matching MinutePack row, calls
    ``grant_minutes`` (idempotent via UNIQUE(stripe_session_id) + SAVEPOINT) to
    fulfill it. Metadata validity gaps (missing creator_id / pack_id / unknown
    pack) emit a PII-free alert log for manual follow-up.
    """
    import uuid

    from sqlalchemy import select

    from billing.ledger import grant_minutes
    from billing.packs import PURCHASABLE_PACKS
    from billing.stripe_client import list_recent_paid_sessions
    from config import settings

    paid_sessions = await asyncio.get_event_loop().run_in_executor(
        None,
        list_recent_paid_sessions,
        settings.STRIPE_RECONCILE_LOOKBACK_HOURS,
    )

    granted = 0
    skipped = 0
    errors = 0

    async with db.AdminSessionLocal() as session:
        for cs in paid_sessions:
            stripe_session_id: str = cs["id"]

            # Fast-path: already in the ledger → skip without opening a SAVEPOINT.
            existing = await session.scalar(
                select(MinutePack.id).where(MinutePack.stripe_session_id == stripe_session_id)
            )
            if existing is not None:
                skipped += 1
                continue

            meta = cs.get("metadata") or {}
            creator_id_str = meta.get("creator_id")
            pack_id = meta.get("pack_id")

            if not creator_id_str or not pack_id:
                logger.error(
                    "billing reconcile missing metadata stripe_session=%s",
                    stripe_session_id,
                )
                errors += 1
                continue

            pack = PURCHASABLE_PACKS.get(pack_id)
            if pack is None:
                logger.error(
                    "billing reconcile unknown pack_id=%s stripe_session=%s",
                    pack_id,
                    stripe_session_id,
                )
                errors += 1
                continue

            try:
                creator_id = uuid.UUID(creator_id_str)
            except ValueError:
                logger.error(
                    "billing reconcile malformed creator_id stripe_session=%s",
                    stripe_session_id,
                )
                errors += 1
                continue

            await grant_minutes(
                creator_id=creator_id,
                minutes=pack.minutes,
                reason="reconcile",
                session=session,
                pack_id=pack_id,
                stripe_session_id=stripe_session_id,
                price_cents=pack.price_cents,
            )
            await session.commit()
            granted += 1
            logger.info(
                "billing reconcile granted pack=%s creator=%s stripe_session=%s",
                pack_id,
                creator_id,
                stripe_session_id,
            )

    logger.info(
        "billing reconcile complete granted=%d skipped=%d errors=%d",
        granted,
        skipped,
        errors,
    )
    if errors:
        logger.error(
            "billing reconcile alert: %d sessions could not be fulfilled — check logs above",
            errors,
        )


async def _purge_stale_event_logs_async() -> None:
    """Issue 250 — delete event_logs rows past the rolling retention window.

    Calls event_log.purge_stale_events() with a cutoff derived from
    ``EVENT_LOG_RETENTION_DAYS`` (default 90). The event_log module owns the
    separate-engine access pattern; this wrapper just computes the cutoff and
    logs the result for observability.

    Idempotent: running multiple times in the same window is a no-op once all
    stale rows are gone. Best-effort: a failure is already swallowed inside
    purge_stale_events() — this function only needs to log the outcome.
    """
    from datetime import timedelta

    from config import settings
    from event_log import purge_stale_events

    cutoff = datetime.now(UTC) - timedelta(days=settings.EVENT_LOG_RETENTION_DAYS)
    n = await purge_stale_events(cutoff)
    if n > 0:
        logger.info("purge_stale_event_logs deleted %d row(s) older than %s", n, cutoff.date())
    elif n == -1:
        logger.warning("purge_stale_event_logs encountered an error (swallowed by event_log)")


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
                # This is the INTERACTIVE, user-triggered first sync. It must NOT
                # be charged to the per-creator refresh sub-budget — that budget
                # exists to stop the Beat fan-out from draining the shared pool, and
                # charging it here would let a large channel exhaust its own
                # 300-unit/day allowance mid-onboarding. charge_sub_budget=False keeps
                # only the global daily cap in force on this path (Issue 260).
                await _emit("step", label="fetch_uploads", stage="catalog_sync")
                await sync_video_catalog(session, creator, access_token, charge_sub_budget=False)
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
                        # Interactive path — global cap only, no sub-budget (Issue 260).
                        await sync_video_analytics(
                            session, video, creator, access_token, charge_sub_budget=False
                        )
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
                except QuotaSubBudgetExhaustedError:
                    # One creator hit its per-day refresh sub-budget — skip it but
                    # keep serving the rest of the fan-out (Issue 260). This except
                    # MUST precede the global QuotaExhaustedError arm because the
                    # sub-budget error subclasses it; otherwise the `break` below
                    # would swallow the per-creator case and stop the whole run.
                    logger.warning(
                        "Creator %s hit its daily refresh sub-budget — skipping, others continue",
                        creator.id,
                    )
                    await session.rollback()
                    continue
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
    bind=True, max_retries=3, default_retry_delay=60, name="worker.tasks.generate_data_export"
)
def generate_data_export(self, creator_id: str) -> str:
    """Build a creator's GDPR Art. 15/20 data export off the request path (Issue 249).

    Gathers every data class into a JSON artifact, uploads it to R2, and writes
    the artifact URI + ``ready`` onto the creator's ``data_exports`` row, which the
    GET endpoint polls. Mirrors the improvement-brief 202+poll precedent.
    """
    try:
        run_async(_generate_data_export_async(self.request.id, creator_id))
    except Exception as exc:
        run_async(_set_data_export_failed(creator_id))
        raise self.retry(exc=exc) from exc
    return creator_id


def _row_to_dict(obj) -> dict:
    """Serialize a model instance to a plain column dict (JSON via default=str)."""
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


async def _collect_creator_export(session, creator) -> dict:
    """Gather every data class for one creator into a JSON-serializable dict.

    Strictly single-tenant: every query is scoped to this creator (directly via
    ``creator_id`` or via the creator's own video/clip/conversation ids)."""
    from sqlalchemy import select

    cid = creator.id

    async def _all(stmt) -> list[dict]:
        return [_row_to_dict(r) for r in (await session.execute(stmt)).scalars().all()]

    videos = (await session.execute(select(Video).where(Video.creator_id == cid))).scalars().all()
    video_ids = [v.id for v in videos]
    clips = (await session.execute(select(Clip).where(Clip.creator_id == cid))).scalars().all()
    clip_ids = [c.id for c in clips]
    convos = (
        (await session.execute(select(ChatConversation).where(ChatConversation.creator_id == cid)))
        .scalars()
        .all()
    )
    convo_ids = [c.id for c in convos]

    return {
        "profile": _row_to_dict(creator),
        "dna": await _all(select(CreatorDna).where(CreatorDna.creator_id == cid)),
        "videos": [_row_to_dict(v) for v in videos],
        "video_metrics": (
            await _all(select(VideoMetrics).where(VideoMetrics.video_id.in_(video_ids)))
            if video_ids
            else []
        ),
        # Clips reference their downloadable media via the authed endpoint (durable,
        # isolation-enforced) rather than an expiring presigned link.
        "clips": [{**_row_to_dict(c), "download_path": f"/clips/{c.id}/download"} for c in clips],
        "clip_feedback": await _all(select(ClipFeedback).where(ClipFeedback.creator_id == cid)),
        "clip_outcomes": (
            await _all(select(ClipOutcome).where(ClipOutcome.clip_id.in_(clip_ids)))
            if clip_ids
            else []
        ),
        "chat_conversations": [_row_to_dict(c) for c in convos],
        "chat_messages": (
            await _all(select(ChatMessage).where(ChatMessage.conversation_id.in_(convo_ids)))
            if convo_ids
            else []
        ),
        "billing_packs": await _all(select(MinutePack).where(MinutePack.creator_id == cid)),
        "billing_deductions": (
            await _all(select(MinuteDeduction).where(MinuteDeduction.video_id.in_(video_ids)))
            if video_ids
            else []
        ),
    }


async def _set_data_export_failed(creator_id: str) -> None:
    from sqlalchemy import select

    async with db.AdminSessionLocal() as session:
        row = (
            await session.execute(
                select(DataExport).where(DataExport.creator_id == uuid.UUID(creator_id))
            )
        ).scalar_one_or_none()
        if row is not None:
            row.status = DataExportStatus.failed
            row.error = "Export failed — please try again."
            await session.commit()


async def _generate_data_export_async(job_id: str, creator_id: str) -> None:
    import json
    from datetime import UTC, datetime

    from sqlalchemy import select

    from worker.storage import aupload_file

    cid = uuid.UUID(creator_id)
    async with db.AdminSessionLocal() as session:
        row = (
            await session.execute(select(DataExport).where(DataExport.creator_id == cid))
        ).scalar_one_or_none()
        # Idempotent: a redelivery whose artifact is already built short-circuits.
        if row is not None and row.status == DataExportStatus.ready and row.export_uri:
            return
        creator = await session.get(Creator, cid)
        if not creator:
            raise ValueError(f"Creator {creator_id} not found for export")
        payload = await _collect_creator_export(session, creator)

    payload["exported_at"] = datetime.now(UTC).isoformat()
    payload["format"] = "creatorclip-export-v1"

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        json.dump(payload, tmp, default=str, ensure_ascii=False, indent=2)
        tmp_path = Path(tmp.name)
    try:
        export_uri = await aupload_file(tmp_path, f"exports/{creator_id}/{job_id}.json")
    finally:
        tmp_path.unlink(missing_ok=True)

    async with db.AdminSessionLocal() as session:
        row = (
            await session.execute(select(DataExport).where(DataExport.creator_id == cid))
        ).scalar_one_or_none()
        if row is not None:
            row.export_uri = export_uri
            row.status = DataExportStatus.ready
            row.completed_at = datetime.now(UTC)
            await session.commit()
    logger.info("Data export ready for creator %s → %s", creator_id, export_uri)


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

    from config import settings
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
                brief_text, _improv_usage = await asyncio.to_thread(
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

            from billing.ledger import record_llm_usage

            await record_llm_usage(
                cid,
                _improv_usage,
                settings.COST_PER_MTOK_IN_SONNET,
                settings.COST_PER_MTOK_OUT_SONNET,
            )

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
    from config import settings
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

            _analysis_result, _analysis_usage = await asyncio.to_thread(
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

        from billing.ledger import record_llm_usage

        await record_llm_usage(
            cid,
            _analysis_usage,
            settings.COST_PER_MTOK_IN_SONNET,
            settings.COST_PER_MTOK_OUT_SONNET,
        )

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

    # Post-billing: SSE delivery failure must not re-run the LLM call.
    try:
        await aemit(job_id, "done", stage="video_analysis", message="Analysis complete.")
    except Exception:
        logger.warning(
            "_generate_video_analysis_async: aemit('done') failed post-billing for job %s",
            job_id,
        )


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
    from sqlalchemy import select

    from config import settings
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

        raw_json, _title_usage = await asyncio.to_thread(
            build_suggestions,
            channel_title=creator.channel_title or "Unknown Channel",
            dna_brief=dna_brief,
            stated_identity=stated_identity,
            video_title=video.title,
            transcript_summary=transcript_summary,
            task_id=job_id,
        )

        from billing.ledger import record_llm_usage

        await record_llm_usage(
            cid, _title_usage, settings.COST_PER_MTOK_IN_SONNET, settings.COST_PER_MTOK_OUT_SONNET
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

    # Post-billing: parse + SSE delivery; failure must not re-run the LLM call.
    try:
        suggestions = parse_candidates(raw_json)
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
        logger.warning(
            "_generate_title_suggestions_async: post-billing step failed for job %s: %s",
            job_id,
            exc,
        )
        with contextlib.suppress(Exception):
            await aemit(
                job_id,
                "error",
                stage="title_suggestions",
                message="Title processing failed — please try again.",
            )


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

    from config import settings
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

        raw_json, _thumb_usage = await asyncio.to_thread(
            build_concepts,
            channel_title=creator.channel_title or "Unknown Channel",
            dna_brief=dna_brief,
            patterns=patterns,
            transcript_hook=transcript_hook,
            stated_identity=stated_identity,
            task_id=job_id,
        )

        from billing.ledger import record_llm_usage

        await record_llm_usage(
            cid,
            _thumb_usage,
            settings.COST_PER_MTOK_IN_SONNET,
            settings.COST_PER_MTOK_OUT_SONNET,
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

    # Post-billing: parse + SSE delivery; failure must not re-run the LLM call.
    try:
        concepts = parse_concepts(raw_json)
        await aemit(
            job_id,
            "done",
            stage="thumbnail_concepts",
            message="Concepts ready.",
            concepts=concepts,
        )
    except Exception as exc:
        logger.warning(
            "_generate_thumbnail_concepts_async: post-billing step failed for job %s: %s",
            job_id,
            exc,
        )
        with contextlib.suppress(Exception):
            await aemit(
                job_id,
                "error",
                stage="thumbnail_concepts",
                message="Concept processing failed — please try again.",
            )


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
    from sqlalchemy import select

    from config import settings
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

        raw_json, _hook_usage = await asyncio.to_thread(
            build_hook_report,
            channel_title=creator.channel_title or "Unknown Channel",
            dna_brief=dna_brief,
            retention_drop_at_s=drop_at_s,
            retention_at_drop=retention_at_drop,
            creator_median_at_drop=creator_median_at_drop,
            transcript_excerpt=transcript_excerpt,
            task_id=job_id,
        )

        from billing.ledger import record_llm_usage

        await record_llm_usage(
            cid, _hook_usage, settings.COST_PER_MTOK_IN_HAIKU, settings.COST_PER_MTOK_OUT_HAIKU
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

    # Post-billing: parse + SSE delivery; failure must not re-run the LLM call.
    try:
        report = parse_hook_report(raw_json)
        await aemit(
            job_id,
            "done",
            stage="hook_analysis",
            message="Hook analysis ready.",
            report=report,
        )
    except Exception as exc:
        logger.warning(
            "_analyze_hook_async: post-billing step failed for job %s: %s",
            job_id,
            exc,
        )
        with contextlib.suppress(Exception):
            await aemit(
                job_id,
                "error",
                stage="hook_analysis",
                message="Hook processing failed — please try again.",
            )


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
    from sqlalchemy import select

    from config import settings
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

        raw_json, _chap_usage = await asyncio.to_thread(
            build_chapters,
            boundaries=boundaries,
            segments=segments,
            video_duration_s=video_duration_s,
            task_id=job_id,
        )

        from billing.ledger import record_llm_usage

        await record_llm_usage(
            cid, _chap_usage, settings.COST_PER_MTOK_IN_HAIKU, settings.COST_PER_MTOK_OUT_HAIKU
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

    # Post-billing: parse + SSE delivery; failure must not re-run the LLM call.
    try:
        result = parse_chapters(raw_json)
        await aemit(
            job_id,
            "done",
            stage="chapters",
            message="Chapters ready.",
            chapters=result["chapters"],
            description_block=result["description_block"],
        )
    except Exception as exc:
        logger.warning(
            "_generate_chapters_async: post-billing step failed for job %s: %s",
            job_id,
            exc,
        )
        with contextlib.suppress(Exception):
            await aemit(
                job_id,
                "error",
                stage="chapters",
                message="Chapter processing failed — please try again.",
            )


# ── Pro chatbot (Issue 152) ───────────────────────────────────────────────────


@celery.task(
    bind=True,
    max_retries=0,  # a chat reply is not worth re-charging tokens on retry
    name="worker.tasks.chat_respond",
)
def chat_respond(self: Task, creator_id: str, conversation_id: str) -> str:
    """Generate one streamed assistant reply for a conversation (Issue 152).

    The user message is already persisted by the router; this task loads the
    recent history, runs the agentic tool loop (streaming tokens to the SSE
    channel keyed on ``self.request.id``), and persists the assistant reply.
    Not retried — a partial token stream already reached the user, and a retry
    would double-spend tokens.
    """
    run_async(_chat_respond_async(self.request.id, creator_id, conversation_id))
    return creator_id


async def _chat_respond_async(job_id: str, creator_id: str, conversation_id: str) -> None:
    from sqlalchemy import select

    from chat.runner import run_chat_turn
    from config import settings
    from worker.progress import aemit

    cid = uuid.UUID(creator_id)
    conv_uuid = uuid.UUID(conversation_id)
    history_turns = settings.CHAT_HISTORY_TURNS

    try:
        async with db.AsyncSessionLocal() as session:
            # Set the per-creator GUC so RLS policies on chat_conversations and
            # chat_messages (via conversation subquery) apply to this session.
            session.info["creator_id"] = str(cid)
            conv = await session.get(ChatConversation, conv_uuid)
            if conv is None or conv.creator_id != cid:
                # Ownership re-check in the worker (defense in depth — the router
                # already gated). Don't leak whether the id exists.
                await aemit(job_id, "error", stage="chat", message="Conversation not found.")
                return

            creator = await session.get(Creator, cid)
            channel_title = creator.channel_title if creator else None

            # Load the tail of the conversation, oldest-first, capped at
            # history_turns*2 messages, then drop any leading assistant rows so
            # the first message sent to Anthropic is a user turn.
            rows = list(
                (
                    await session.execute(
                        select(ChatMessage)
                        .where(ChatMessage.conversation_id == conv_uuid)
                        .order_by(ChatMessage.created_at.desc())
                        .limit(history_turns * 2)
                    )
                ).scalars()
            )
            rows.reverse()
            while rows and rows[0].role is not ChatRole.user:
                rows.pop(0)
            if not rows:
                await aemit(job_id, "error", stage="chat", message="Nothing to respond to.")
                return

            history = [{"role": m.role.value, "content": m.content} for m in rows]

            final_text, usage = await run_chat_turn(job_id, cid, channel_title, history, session)

            if not final_text:
                await aemit(job_id, "error", stage="chat", message="No reply generated.")
                return

            session.add(
                ChatMessage(
                    conversation_id=conv_uuid,
                    role=ChatRole.assistant,
                    content=final_text,
                    tokens_in=usage["input_tokens"] + usage["cache_read"],
                    tokens_out=usage["output_tokens"],
                    cache_read=usage["cache_read"],
                )
            )
            conv.updated_at = datetime.now(UTC)
            await session.commit()

        await aemit(job_id, "done", stage="chat", message="Reply complete.")

    except Exception as exc:
        logger.error(
            "_chat_respond_async failed creator=%s conv=%s: %s", creator_id, conversation_id, exc
        )
        await aemit(
            job_id,
            "error",
            stage="chat",
            message="The assistant hit an error — please try again.",
            exc_type=type(exc).__name__,
        )
        raise


# ── Notification send task (Issue 243) ───────────────────────────────────────


@celery.task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name="worker.tasks.send_notification",
)
def send_notification(
    self: Task,
    creator_id: str,
    event_type: str,
    entity_id: str,
    payload: dict,
) -> str:
    """Send a transactional or in-app notification to a creator (Issue 243).

    Implements the Inbox/idempotent-consumer pattern:

    1. Load creator and check ``notification_preferences`` (skip if opted out or
       the relevant channel is disabled).
    2. Compute ``dedupe_key = sha256(creator_id:event_type:entity_id)`` — stable
       across retries, unique per notification event.
    3. INSERT ``notification_deliveries`` row with the dedupe_key.  If Postgres
       raises ``IntegrityError``, the delivery already succeeded on a prior
       attempt — return immediately without sending again.
    4. Render and send the email via ``notify.mailer.send()`` (which uses Resend's
       own ``Idempotency-Key`` header as a second deduplication layer).
    5. INSERT a ``notifications`` row (in-app center).

    Idempotency guarantee: a Celery at-least-once redelivery or a duplicate
    ``send_notification.delay(...)`` call for the same (creator, event, entity)
    triple is safe — the UNIQUE dedupe_key constraint ensures exactly-one delivery.

    Args:
        creator_id: UUID string identifying the recipient creator.
        event_type: Stable event classifier, e.g. ``"clips_ready"``,
                    ``"dna_built"``, ``"trial_ending"``.
        entity_id: Primary entity driving this notification (e.g. video_id string
                   for ``clips_ready``). Must be stable across retries.
        payload: Extra rendering context for the Jinja2 template (e.g.
                 ``{"clip_count": 5, "video_title": "..."}``). Never put PII
                 or tokens here — this dict is logged at DEBUG level.

    Returns:
        The creator_id string on success or skip; raises on unrecoverable failure
        (Celery retries on transient errors).
    """
    try:
        run_async(_send_notification_async(creator_id, event_type, entity_id, payload))
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    return creator_id


async def _send_notification_async(
    creator_id: str,
    event_type: str,
    entity_id: str,
    payload: dict,
) -> None:
    """Async implementation for the send_notification Celery task (Issue 243).

    Uses AdminSessionLocal (BYPASSRLS) because this task operates on multiple
    tables that span the RLS boundary: it reads notification_preferences
    (no RLS) and inserts notification_deliveries (no RLS) and notifications
    (has RLS but the admin role bypasses it).  The admin role is correct here —
    the task is a trusted worker process, not a creator-facing request path.

    Per-creator isolation is enforced by the WHERE creator_id = cid predicate on
    every query, which is equivalent to what the RLS policy would enforce.
    """
    from sqlalchemy.exc import IntegrityError

    from models import (
        Creator,
        NotificationChannel,
        NotificationDelivery,
        NotificationDeliveryStatus,
        NotificationPreference,
    )
    from notify.dedupe import make_dedupe_key
    from notify.mailer import send as mailer_send

    cid = uuid.UUID(creator_id)

    async with db.AdminSessionLocal() as session:
        # ── 1. Load creator (needed for the email address) ──────────────────
        creator = await session.get(Creator, cid)
        if creator is None:
            logger.warning("send_notification: creator %s not found — skipping", creator_id)
            return

        # ── 2. Load preferences (lazy-create with defaults if absent) ────────
        prefs = await session.get(NotificationPreference, cid)
        if prefs is None:
            # First notification for this creator: write the default row so
            # subsequent tasks find it. Uses RETURNING to avoid a second read.
            prefs = NotificationPreference(creator_id=cid)
            session.add(prefs)
            try:
                await session.flush()
            except IntegrityError:
                # Concurrent task already created the row — reload.
                await session.rollback()
                prefs = await session.get(NotificationPreference, cid)
                if prefs is None:
                    logger.error("send_notification: cannot load prefs for creator %s", creator_id)
                    return

        # ── 3. Preference gate ────────────────────────────────────────────────
        # Transactional events (e.g. clips_ready, dna_built, refund) are
        # always-on per CAN-SPAM / GDPR Art. 6(1)(b); the UI locks the toggle.
        # Lifecycle events (welcome, nudge, re-engagement) respect opt-out.
        _LIFECYCLE_EVENTS = frozenset({"welcome", "first_clip_nudge", "re_engagement"})
        is_lifecycle = event_type in _LIFECYCLE_EVENTS

        # CAN-SPAM safety gate: lifecycle (commercial-leaning) email may not be
        # sent without a physical postal address in the body. With MAILING_ADDRESS
        # unset we SKIP the send entirely (no delivery row, no in-app fallback) and
        # log the skip — so merging/deploying this branch cannot blast users until
        # a real address is configured.
        from config import settings as _settings

        if is_lifecycle and not _settings.MAILING_ADDRESS:
            logger.info(
                "send_notification: MAILING_ADDRESS unset — skipping lifecycle email %s "
                "for creator %s (CAN-SPAM safety gate)",
                event_type,
                creator_id,
            )
            return

        if is_lifecycle and not prefs.email_lifecycle:
            logger.info(
                "send_notification: creator %s opted out of lifecycle email — skip %s",
                creator_id,
                event_type,
            )
            return

        if not prefs.inapp_enabled and not prefs.email_transactional:
            # Both channels off (unusual, but respect the preference).
            logger.info(
                "send_notification: all channels disabled for creator %s — skip %s",
                creator_id,
                event_type,
            )
            return

        # ── 4. Compute dedupe key (stable across retries) ─────────────────────
        dedupe_key = make_dedupe_key(cid, event_type, entity_id)

        # ── 5. Idempotency INSERT into notification_deliveries ────────────────
        delivery = NotificationDelivery(
            creator_id=cid,
            event_type=event_type,
            entity_id=entity_id,
            channel=NotificationChannel.email,
            dedupe_key=dedupe_key,
            status=NotificationDeliveryStatus.sent,
        )
        session.add(delivery)
        try:
            await session.flush()
        except IntegrityError:
            # UNIQUE dedupe_key violation → already delivered on a prior attempt.
            await session.rollback()
            logger.info(
                "send_notification: dedupe_key=%s already delivered — skipping %s for creator %s",
                dedupe_key,
                event_type,
                creator_id,
            )
            return

        # ── 6. Build email send context while the session is still open ─────────
        # Capture everything needed for the mailer call BEFORE committing, so
        # ORM attributes on creator/prefs are accessible. The actual send happens
        # AFTER the session closes (step 8) to avoid holding the DB connection
        # open while the Resend API call blocks. (Issue 349)
        delivery_id = delivery.id  # populated by flush() above; needed for failure update
        send_to: str | None = creator.email if prefs.email_transactional else None
        email_context: dict | None = None
        email_headers: dict[str, str] | None = None
        if send_to:
            # RFC 8058 one-click unsubscribe headers on LIFECYCLE sends only.
            unsub_url: str | None = None
            if is_lifecycle:
                unsub_url = f"{_settings.APP_BASE_URL}/unsubscribe/{prefs.unsubscribe_token}"
                email_headers = {
                    "List-Unsubscribe": f"<{unsub_url}>",
                    "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
                }
            email_context = (
                {"creator": creator, "unsubscribe_url": unsub_url, **payload}
                if is_lifecycle
                else {"creator": creator, **payload}
            )
        else:
            # No email address or opted out — in-app delivery only.
            delivery.channel = NotificationChannel.inapp

        # ── 7. Insert in-app notification row ────────────────────────────────
        if prefs.inapp_enabled:
            notification = _build_inapp_notification(cid, event_type, payload)
            session.add(notification)

        # Commit now — before the blocking mailer call — to free the DB connection.
        await session.commit()

    # ── 8. Send email outside the session ────────────────────────────────────
    # asyncio.wait_for + asyncio.to_thread give a Python-level timeout around
    # the blocking sync mailer call. On timeout or any send error we open a
    # fresh session to mark the delivery row failed (the commit in step 7 already
    # persisted it as NotificationDeliveryStatus.sent). (Issue 349)
    if send_to and email_context is not None:
        import asyncio

        try:
            await asyncio.wait_for(
                asyncio.to_thread(
                    mailer_send,
                    to=send_to,
                    template=event_type,
                    context=email_context,
                    idempotency_key=dedupe_key,
                    headers=email_headers,
                ),
                timeout=_settings.RESEND_TIMEOUT_S,
            )
            logger.info(
                "send_notification: email sent event_type=%s creator=%s dedupe_key=%s",
                event_type,
                creator_id,
                dedupe_key,
            )
        except Exception as exc:
            logger.error(
                "send_notification: email send failed event_type=%s creator=%s: %s",
                event_type,
                creator_id,
                exc,
            )
            async with db.AdminSessionLocal() as fail_session:
                fail_delivery = await fail_session.get(NotificationDelivery, delivery_id)
                if fail_delivery is not None:
                    fail_delivery.status = NotificationDeliveryStatus.failed
                await fail_session.commit()


def _build_inapp_notification(
    creator_id: uuid.UUID,
    event_type: str,
    payload: dict,
) -> Any:
    """Build a Notification model instance from the event type and payload.

    Returns a ``Notification`` ORM instance.  Return type is annotated ``Any``
    because the local import pattern used throughout this module (deferred to
    avoid circular imports) means the class is not visible at the function
    signature level without a TYPE_CHECKING guard.  Callers (both in the task
    and in tests) receive the concrete model instance regardless.

    Centralises the copy strings for in-app notifications so they can be
    asserted by tests without needing a live database.  Copy never promises
    virality (Honesty Constraint, CLAUDE.md).
    """
    from models import Notification

    # Map event_type to (title, body, link_url) copy.  New event types must be
    # added here; unknown types fall back to a generic notification.
    _COPY: dict[str, tuple[str, str, str | None]] = {
        "clips_ready": (
            "Your clips are ready to review.",
            payload.get("body", "We found candidate clips from your video. Tap to review them."),
            "/app/review",
        ),
        "dna_built": (
            "Your channel DNA is ready.",
            "We've built your channel DNA profile — review and confirm it.",
            "/app/profile",
        ),
        "trial_ending": (
            "Your free trial is ending soon.",
            payload.get("body", "Your trial is ending soon. Top up your minutes to keep clipping."),
            "/app/dashboard",
        ),
        "balance_low": (
            "Your minutes balance is running low.",
            payload.get("body", "You have a few minutes left. Add more to keep processing."),
            "/app/dashboard",
        ),
        "refund_issued": (
            "Your minutes were refunded.",
            payload.get(
                "body",
                "We couldn't process your video — your minutes have been refunded.",
            ),
            "/app/dashboard",
        ),
        "reauth_required": (
            "Reconnect your YouTube account.",
            "Your YouTube connection needs to be refreshed to keep analytics up to date.",
            "/app/profile",
        ),
        "catalog_sync_done": (
            "Your video catalog is up to date.",
            "Your YouTube catalog has been synced successfully.",
            "/app/dashboard",
        ),
        "welcome": (
            "Welcome to AutoClip.",
            (
                "AutoClip predicts fit with your style and audience — it does not promise virality. "
                "Every recommendation is an estimate grounded in your own data."
            ),
            "/app/dashboard",
        ),
        "first_clip_nudge": (
            "Ready to make your first clip?",
            (
                "Upload a video and AutoClip will surface candidate clips scored against your "
                "own channel's style and audience data."
            ),
            "/app/dashboard",
        ),
        "re_engagement": (
            "Your channel data is waiting.",
            (
                "It's been a while since you reviewed a clip. The more you review, the better "
                "AutoClip's fit estimates get for your channel."
            ),
            "/app/review",
        ),
    }

    title, body, link_url = _COPY.get(
        event_type,
        (
            f"Notification: {event_type}",
            payload.get("body", "You have a new notification."),
            None,
        ),
    )

    return Notification(
        creator_id=creator_id,
        kind=event_type,
        title=title,
        body=body,
        link_url=link_url,
    )
