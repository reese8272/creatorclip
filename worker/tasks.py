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

from celery import Task

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
    VideoMetrics,
)
from worker.celery_app import celery, run_async
from youtube.errors import YouTubeAuthError
from youtube.quota import QuotaExhaustedError, remaining

logger = logging.getLogger(__name__)


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

    def on_failure(self, exc, task_id, args, kwargs, einfo):
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
    except Exception as exc:
        run_async(_set_status(video_id, IngestStatus.failed))
        raise self.retry(exc=exc) from exc
    generate_clips.delay(video_id)
    return video_id


@celery.task(bind=True, max_retries=2, default_retry_delay=60, name="worker.tasks.generate_clips")
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


@celery.task(name="worker.tasks.purge_stale_source_media")
def purge_stale_source_media() -> None:
    """
    Celery Beat task: delete source video files older than SOURCE_MEDIA_RETENTION_HOURS
    and null their source_uri, complying with the YouTube API data retention policy.
    """
    run_async(_purge_stale_source_media_async())


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
    """
    try:
        run_async(_sync_channel_catalog_async(creator_id))
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
    from sqlalchemy import func, select
    from sqlalchemy.exc import IntegrityError

    from preference.train import TRAINABLE_ACTIONS, build_and_save

    cid = uuid.UUID(creator_id)
    async with db.AsyncSessionLocal() as session:
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


async def _set_status(video_id: str, status: IngestStatus) -> None:
    async with db.AdminSessionLocal() as session:
        video = await session.get(Video, uuid.UUID(video_id))
        if video:
            video.ingest_status = status
            await session.commit()


async def _ingest_async(video_id: str) -> None:
    from worker.storage import alocal_path, aupload_file
    from youtube.ingest import extract_audio_wav

    async with db.AdminSessionLocal() as session:
        video = await session.get(Video, uuid.UUID(video_id))
        if not video:
            raise ValueError(f"Video {video_id} not found")
        if not video.source_uri:
            raise ValueError(f"Video {video_id} has no source_uri — upload the file first")
        source_uri = video.source_uri
        video.ingest_status = IngestStatus.running
        await session.commit()

    duration_s: float | None = None
    async with alocal_path(source_uri) as src:
        from youtube.ingest import probe_duration_s

        # Offload sync subprocess + ffmpeg + boto3 work to a worker thread so the
        # event loop is not blocked for the duration of the call (Issue 38 Wave 1).
        duration_s = await asyncio.to_thread(probe_duration_s, src)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = Path(tmp.name)
        try:
            await asyncio.to_thread(extract_audio_wav, src, wav_path)
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

                await deduct_for_video(video.id, video.creator_id, duration_s, session)
            await session.commit()


async def _transcribe_async(video_id: str) -> None:
    from config import settings
    from ingestion.transcribe import transcribe_audio
    from worker.storage import alocal_path

    async with db.AdminSessionLocal() as session:
        video = await session.get(Video, uuid.UUID(video_id))
        if not video or not video.source_uri:
            raise ValueError(f"Video {video_id} not ready for transcription")
        source_uri = video.source_uri

    async with alocal_path(source_uri) as audio_path:
        # transcribe_audio dispatches to sync Deepgram / AssemblyAI / WhisperX
        # SDKs — offload to a thread so the event loop is free during the
        # multi-second transcription round-trip (Issue 38 Wave 1). Bounded by
        # TRANSCRIPTION_TIMEOUT_S so a hung provider fails (→ retry) instead
        # of stalling forever (Issue 68).
        result = await asyncio.wait_for(
            asyncio.to_thread(transcribe_audio, str(audio_path)),
            timeout=settings.TRANSCRIPTION_TIMEOUT_S,
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


async def _signals_async(video_id: str) -> None:
    from sqlalchemy import select

    from ingestion.audio import extract_audio_events
    from ingestion.signals import build_signal_timeline
    from worker.storage import alocal_path

    async with db.AdminSessionLocal() as session:
        video = await session.get(Video, uuid.UUID(video_id))
        if not video or not video.source_uri:
            raise ValueError(f"Video {video_id} not ready for signal extraction")
        source_uri = video.source_uri
        retention_result = await session.execute(
            select(RetentionCurve).where(RetentionCurve.video_id == video.id)
        )
        retention_points = list(retention_result.scalars())

    async with alocal_path(source_uri) as audio_path:
        # extract_audio_events is librosa-backed (sync, CPU + IO heavy).
        # Offload so the event loop stays responsive (Issue 38 Wave 1).
        audio_events = await asyncio.to_thread(extract_audio_events, str(audio_path))

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
    from clip_engine.render import render_clip_file
    from worker.storage import alocal_path, aupload_file

    async with db.AdminSessionLocal() as session:
        clip = await session.get(Clip, uuid.UUID(clip_id))
        if not clip:
            raise ValueError(f"Clip {clip_id} not found")
        # Idempotent under at-least-once delivery (Issue 62): a redelivered render
        # must not re-encode and last-writer-win the URI. Skip if already done.
        if clip.render_status == RenderStatus.done and clip.render_uri:
            logger.info("Clip %s already rendered — skipping", clip_id)
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
        clip.render_status = RenderStatus.running
        await session.commit()

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
            await asyncio.to_thread(
                render_clip_file,
                source_path=src,
                start_s=setup_start_s if setup_start_s is not None else start_s,
                end_s=end_s,
                out_path=out_path,
            )
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

    from sqlalchemy import and_, or_, select

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
        result = await session.execute(
            select(ClipOutcome, Clip)
            .join(Clip, Clip.id == ClipOutcome.clip_id)
            .where(
                ClipOutcome.published_youtube_id.isnot(None),
                ClipOutcome.final.is_(False),  # never re-poll a finalized outcome
                Clip.created_at >= cutoff_created,
                or_(
                    and_(ClipOutcome.performed_well.is_(None), ClipOutcome.fetched_at < cutoff_48h),
                    ClipOutcome.fetched_at < cutoff_7d,
                ),
            )
        )
        rows = result.all()

        if not rows:
            return

        by_creator: dict = defaultdict(list)
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


async def _generate_clips_async(video_id: str) -> None:

    from sqlalchemy import select

    from clip_engine.ranking import generate_and_rank_clips
    from config import settings
    from dna.profile import get_active
    from models import Signals, Transcript

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
            return

        signals = await session.get(Signals, video_uuid)
        if not signals:
            raise ValueError(f"Signals not available for video {video_id}")

        transcript = await session.get(Transcript, video_uuid)
        transcript_segments = transcript.segments_jsonb.get("segments", []) if transcript else []

        dna_profile = await get_active(session, video.creator_id)
        dna_brief = dna_profile.brief_text if dna_profile else None

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


async def _purge_stale_source_media_async() -> None:
    from datetime import timedelta

    from sqlalchemy import and_, select, update

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


async def _sync_channel_catalog_async(creator_id: str) -> None:
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

    YouTubeAuthError mid-loop is surfaced (terminal — the refresh tick will
    delete the YoutubeToken row); other per-video errors are logged and
    skipped so one bad video can't strand the whole catalog. (Issues 87, 88)
    """
    from sqlalchemy import select

    from youtube.analytics import sync_video_analytics, sync_video_catalog
    from youtube.oauth import get_valid_access_token

    cid = uuid.UUID(creator_id)
    async with db.AdminSessionLocal() as session:
        creator = await session.get(Creator, cid)
        if creator is None:
            logger.warning("sync_channel_catalog: creator %s not found, skip", cid)
            return
        try:
            access_token = await get_valid_access_token(creator.id, session)
        except Exception as exc:
            logger.warning("sync_channel_catalog: no valid token for %s: %s", cid, exc)
            return

        # Phase 1 — catalog upsert.
        await sync_video_catalog(session, creator, access_token)
        await session.commit()

        # Phase 2 — fetch metrics for any video that doesn't have them yet.
        # Filtered query, not a global iterate-and-skip, so quota cost is bounded
        # to truly-missing rows (re-runs are cheap).
        unmeasured = (
            (
                await session.execute(
                    select(Video)
                    .outerjoin(VideoMetrics, VideoMetrics.video_id == Video.id)
                    .where(
                        Video.creator_id == creator.id,
                        (VideoMetrics.video_id.is_(None))
                        | (VideoMetrics.engagement_rate.is_(None)),
                    )
                )
            )
            .scalars()
            .all()
        )

        fetched = 0
        for video in unmeasured:
            try:
                await sync_video_analytics(session, video, creator, access_token)
                fetched += 1
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
        await session.commit()
        logger.info(
            "sync_channel_catalog: creator %s synced (metrics fetched for %d new video(s))",
            cid,
            fetched,
        )


async def _refresh_youtube_analytics_async() -> None:
    from sqlalchemy import delete, select

    from youtube.analytics import sync_audience_data, sync_video_analytics, sync_video_catalog
    from youtube.oauth import get_valid_access_token

    async with db.AdminSessionLocal() as session:
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
    """
    from sqlalchemy import select

    from dna.profile import get_active
    from improvement.brief import generate_improvement_brief as build_brief
    from models import ImprovementBrief, ImprovementBriefStatus

    cid = uuid.UUID(creator_id)
    async with db.AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(ImprovementBrief).where(ImprovementBrief.creator_id == cid)
            )
        ).scalar_one_or_none()
        if row is None:
            logger.warning("generate_improvement_brief: no row for %s; nothing to do", creator_id)
            return

        # Idempotency: this exact task already produced the brief (redelivery).
        if row.job_id == job_id and row.status == ImprovementBriefStatus.ready:
            logger.info("generate_improvement_brief: redelivery for %s — already built", creator_id)
            return

        creator = await session.get(Creator, cid)
        if creator is None:
            row.status = ImprovementBriefStatus.failed
            row.error = "Creator not found."
            row.completed_at = datetime.now(UTC)
            await session.commit()
            return

        try:
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

            brief_text = await asyncio.to_thread(
                build_brief,
                channel_title=creator.channel_title or "Unknown Channel",
                analytics=analytics,
                dna_brief=dna_brief,
            )
        except Exception as exc:
            row.status = ImprovementBriefStatus.failed
            row.error = "Brief generation failed — try again."
            row.completed_at = datetime.now(UTC)
            await session.commit()
            logger.error("generate_improvement_brief failed for %s: %s", creator_id, exc)
            raise

        row.status = ImprovementBriefStatus.ready
        row.brief_text = brief_text
        row.error = None
        row.completed_at = datetime.now(UTC)
        await session.commit()
        logger.info("Improvement brief ready for creator %s", creator_id)
