"""
Celery pipeline tasks: ingest_video → transcribe_video → build_signals → build_dna.

Design: each task accepts only a UUID string — never large payloads —
so Redis messages stay small. Each task is idempotent and safe to retry.
asyncio.run() bridges Celery's sync execution model and the async SQLAlchemy session.
"""

import asyncio
import logging
import tempfile
import uuid
from datetime import UTC
from pathlib import Path

from db import AsyncSessionLocal
from models import (
    Clip,
    ClipOutcome,
    Creator,
    IngestStatus,
    OnboardingState,
    RenderStatus,
    RetentionCurve,
    Signals,
    Transcript,
    Video,
    VideoMetrics,
)
from worker.celery_app import celery
from youtube.quota import QuotaExhaustedError, remaining

logger = logging.getLogger(__name__)


# ── Public entry points ───────────────────────────────────────────────────────


def start_pipeline(video_id: str) -> None:
    """Kick off the ingest → transcribe → signals chain."""
    (ingest_video.s(video_id) | transcribe_video.s() | build_signals.s()).apply_async()


# ── Tasks ─────────────────────────────────────────────────────────────────────


@celery.task(bind=True, max_retries=3, default_retry_delay=30, name="worker.tasks.ingest_video")
def ingest_video(self, video_id: str) -> str:
    try:
        asyncio.run(_ingest_async(video_id))
    except Exception as exc:
        asyncio.run(_set_status(video_id, IngestStatus.failed))
        raise self.retry(exc=exc) from exc
    return video_id


@celery.task(bind=True, max_retries=3, default_retry_delay=30, name="worker.tasks.transcribe_video")
def transcribe_video(self, video_id: str) -> str:
    try:
        asyncio.run(_transcribe_async(video_id))
    except Exception as exc:
        asyncio.run(_set_status(video_id, IngestStatus.failed))
        raise self.retry(exc=exc) from exc
    return video_id


@celery.task(bind=True, max_retries=3, default_retry_delay=30, name="worker.tasks.build_signals")
def build_signals(self, video_id: str) -> str:
    try:
        asyncio.run(_signals_async(video_id))
    except Exception as exc:
        asyncio.run(_set_status(video_id, IngestStatus.failed))
        raise self.retry(exc=exc) from exc
    generate_clips.delay(video_id)
    return video_id


@celery.task(bind=True, max_retries=2, default_retry_delay=60, name="worker.tasks.generate_clips")
def generate_clips(self, video_id: str) -> str:
    """Score and rank clip candidates for a fully-ingested video."""
    try:
        asyncio.run(_generate_clips_async(video_id))
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    return video_id


@celery.task(bind=True, max_retries=3, default_retry_delay=60, name="worker.tasks.render_clip")
def render_clip(self, clip_id: str) -> str:
    """Render a clip to 9:16 and upload to storage."""
    try:
        asyncio.run(_render_clip_async(clip_id))
    except Exception as exc:
        asyncio.run(_set_clip_render_status(clip_id, RenderStatus.failed))
        raise self.retry(exc=exc) from exc
    return clip_id


@celery.task(name="worker.tasks.purge_stale_source_media")
def purge_stale_source_media() -> None:
    """
    Celery Beat task: delete source video files older than SOURCE_MEDIA_RETENTION_HOURS
    and null their source_uri, complying with the YouTube API data retention policy.
    """
    asyncio.run(_purge_stale_source_media_async())


@celery.task(name="worker.tasks.refresh_youtube_analytics")
def refresh_youtube_analytics() -> None:
    """
    Celery Beat task: re-fetch video_metrics and audience_activity for all creators
    with valid tokens. Keeps analytics fresh per YouTube API ToS (no indefinite caching).
    """
    asyncio.run(_refresh_youtube_analytics_async())


@celery.task(name="worker.tasks.poll_clip_outcomes")
def poll_clip_outcomes() -> None:
    """
    Celery Beat task: fetch YouTube stats for published clips at 48h and 7d checkpoints.
    Sets performed_well = views >= channel_median_views; used as a 3× weight multiplier
    in preference model retraining.
    """
    asyncio.run(_poll_clip_outcomes_async())


@celery.task(bind=True, max_retries=3, default_retry_delay=60, name="worker.tasks.build_dna")
def build_dna(self, creator_id: str) -> str:
    """
    Build creator DNA patterns, generate brief, store draft profile + embeddings.
    ValueError (data gate failure) is re-raised without retry — it is a permanent error.
    """
    try:
        asyncio.run(_build_dna_async(creator_id))
    except ValueError:
        raise
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    return creator_id


# ── Async implementations ─────────────────────────────────────────────────────


async def _set_status(video_id: str, status: IngestStatus) -> None:
    async with AsyncSessionLocal() as session:
        video = await session.get(Video, uuid.UUID(video_id))
        if video:
            video.ingest_status = status
            await session.commit()


async def _ingest_async(video_id: str) -> None:
    from worker.storage import local_path, upload_file
    from youtube.ingest import extract_audio_wav

    async with AsyncSessionLocal() as session:
        video = await session.get(Video, uuid.UUID(video_id))
        if not video:
            raise ValueError(f"Video {video_id} not found")
        if not video.source_uri:
            raise ValueError(f"Video {video_id} has no source_uri — upload the file first")
        source_uri = video.source_uri
        video.ingest_status = IngestStatus.running
        await session.commit()

    duration_s: float | None = None
    with local_path(source_uri) as src:
        from youtube.ingest import probe_duration_s

        duration_s = probe_duration_s(src)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = Path(tmp.name)
        try:
            extract_audio_wav(src, wav_path)
            audio_uri = upload_file(wav_path, f"audio/{video_id}.wav")
        finally:
            wav_path.unlink(missing_ok=True)

    async with AsyncSessionLocal() as session:
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
    from ingestion.transcribe import transcribe_audio
    from worker.storage import local_path

    async with AsyncSessionLocal() as session:
        video = await session.get(Video, uuid.UUID(video_id))
        if not video or not video.source_uri:
            raise ValueError(f"Video {video_id} not ready for transcription")
        source_uri = video.source_uri

    with local_path(source_uri) as audio_path:
        result = transcribe_audio(str(audio_path))

    async with AsyncSessionLocal() as session:
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
    from worker.storage import local_path

    async with AsyncSessionLocal() as session:
        video = await session.get(Video, uuid.UUID(video_id))
        if not video or not video.source_uri:
            raise ValueError(f"Video {video_id} not ready for signal extraction")
        source_uri = video.source_uri
        retention_result = await session.execute(
            select(RetentionCurve).where(RetentionCurve.video_id == video.id)
        )
        retention_points = list(retention_result.scalars())

    with local_path(source_uri) as audio_path:
        audio_events = extract_audio_events(str(audio_path))

    timeline = build_signal_timeline(audio_events, retention_points)

    async with AsyncSessionLocal() as session:
        existing = await session.get(Signals, uuid.UUID(video_id))
        if existing:
            existing.timeline_jsonb = timeline
        else:
            session.add(Signals(video_id=uuid.UUID(video_id), timeline_jsonb=timeline))
        video = await session.get(Video, uuid.UUID(video_id))
        if video:
            video.ingest_status = IngestStatus.done
        await session.commit()


async def _set_clip_render_status(clip_id: str, status: RenderStatus) -> None:
    async with AsyncSessionLocal() as session:
        clip = await session.get(Clip, uuid.UUID(clip_id))
        if clip:
            clip.render_status = status
            await session.commit()


async def _render_clip_async(clip_id: str) -> None:
    from clip_engine.render import render_clip_file
    from worker.storage import local_path, upload_file

    async with AsyncSessionLocal() as session:
        clip = await session.get(Clip, uuid.UUID(clip_id))
        if not clip:
            raise ValueError(f"Clip {clip_id} not found")
        video = await session.get(Video, clip.video_id)
        if not video or not video.source_uri:
            raise ValueError(f"Source video not available for clip {clip_id}")
        source_uri = video.source_uri
        clip.render_status = RenderStatus.running
        await session.commit()

    with local_path(source_uri) as src:
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            out_path = Path(tmp.name)
        try:
            render_clip_file(
                source_path=src,
                start_s=clip.start_s,
                end_s=clip.end_s,
                out_path=out_path,
            )
            render_uri = upload_file(out_path, f"clips/{clip_id}.mp4")
        finally:
            out_path.unlink(missing_ok=True)

    async with AsyncSessionLocal() as session:
        clip = await session.get(Clip, uuid.UUID(clip_id))
        if clip:
            clip.render_uri = render_uri
            clip.render_status = RenderStatus.done
            await session.commit()

    logger.info("Clip %s rendered → %s", clip_id, render_uri)


async def _build_dna_async(creator_id: str) -> None:
    from dna.brief import generate_brief
    from dna.builder import build_patterns
    from dna.embeddings import embed_brief, embed_patterns
    from dna.profile import create_draft

    creator_uuid = uuid.UUID(creator_id)

    async with AsyncSessionLocal() as session:
        creator = await session.get(Creator, creator_uuid)
        if not creator:
            raise ValueError(f"Creator {creator_id} not found")
        channel_title = creator.channel_title or "Unknown Channel"

        (
            patterns,
            top_ids,
            bottom_ids,
            clip_len_s,
            source_region,
            upload_gap_h,
        ) = await build_patterns(session, creator_uuid)

        brief_text = generate_brief(patterns, channel_title)

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
        )

        if creator.onboarding_state == OnboardingState.awaiting_data:
            creator.onboarding_state = OnboardingState.dna_pending
            await session.commit()

        await embed_patterns(session, creator_uuid, patterns)
        await embed_brief(session, creator_uuid, brief_text)

        logger.info(
            "DNA draft v%d built for creator %s (%s)", dna.version, creator_id, channel_title
        )


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

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ClipOutcome, Clip)
            .join(Clip, Clip.id == ClipOutcome.clip_id)
            .where(
                ClipOutcome.published_youtube_id.isnot(None),
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
                logger.info(
                    "ClipOutcome clip=%s views=%s performed_well=%s",
                    outcome.clip_id,
                    views,
                    outcome.performed_well,
                )

        await session.commit()


async def _generate_clips_async(video_id: str) -> None:

    from clip_engine.ranking import generate_and_rank_clips
    from config import settings
    from dna.profile import get_active
    from models import Signals, Transcript

    video_uuid = uuid.UUID(video_id)

    async with AsyncSessionLocal() as session:
        video = await session.get(Video, video_uuid)
        if not video:
            raise ValueError(f"Video {video_id} not found")

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
    from datetime import datetime, timedelta

    from sqlalchemy import and_, select

    from config import settings
    from worker.storage import delete_file

    cutoff = datetime.now(UTC) - timedelta(hours=settings.SOURCE_MEDIA_RETENTION_HOURS)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Video).where(
                and_(
                    Video.source_uri.isnot(None),
                    Video.created_at < cutoff,
                )
            )
        )
        videos = list(result.scalars())

        purged = 0
        for video in videos:
            try:
                delete_file(video.source_uri)
                video.source_uri = None
                purged += 1
            except Exception as exc:
                logger.warning("Failed to purge source media for video %s: %s", video.id, exc)

        if purged:
            await session.commit()
            logger.info("Purged source media for %d video(s)", purged)


async def _refresh_youtube_analytics_async() -> None:
    from sqlalchemy import select

    from youtube.analytics import sync_audience_data, sync_video_analytics
    from youtube.oauth import get_valid_access_token

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Creator))
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
                videos_result = await session.execute(
                    select(Video).where(Video.creator_id == creator.id)
                )
                for video in list(videos_result.scalars()):
                    await sync_video_analytics(session, video, creator, access_token)

                await sync_audience_data(session, creator, access_token)
                await session.commit()
                logger.info("Refreshed analytics for creator %s", creator.id)
            except QuotaExhaustedError:
                logger.warning(
                    "YouTube quota exhausted during analytics refresh — stopping early. "
                    "Remaining creators will be refreshed in tomorrow's run."
                )
                await session.rollback()
                break
            except Exception as exc:
                logger.warning("Analytics refresh failed for creator %s: %s", creator.id, exc)
                await session.rollback()
