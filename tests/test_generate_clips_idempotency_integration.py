"""
Integration tests for Batch 1 (Issues 61 + 62) against a real Postgres.

61: a re-run of clip generation must NOT delete+reinsert clips, because
    Clip.feedback / Clip.outcome cascade-delete — that would wipe the creator's
    feedback labels on a Celery redelivery.
62: a redelivered render must skip when the clip is already done.

Marked `integration` (excluded from the default run — see pytest.ini).
"""

import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from clip_engine.ranking import generate_and_rank_clips
from config import settings
from models import (
    Clip,
    ClipFeedback,
    Creator,
    FeedbackAction,
    OnboardingState,
    RenderStatus,
    Video,
    VideoKind,
)
from worker.tasks import _render_clip_async

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(session: AsyncSession, *, render_status=RenderStatus.pending) -> tuple:
    creator = Creator(
        google_sub=f"test_idem_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_idem_{uuid.uuid4().hex[:6]}",
        channel_title="Idempotency Test",
        onboarding_state=OnboardingState.active,
    )
    session.add(creator)
    await session.flush()
    video = Video(
        creator_id=creator.id,
        youtube_video_id=f"yt_{uuid.uuid4().hex[:8]}",
        title="Idem fixture",
        kind=VideoKind.long,
        source_uri=f"source/{creator.id}/x.mp4",
    )
    session.add(video)
    await session.flush()
    clip = Clip(
        video_id=video.id,
        creator_id=creator.id,
        setup_start_s=10.0,
        start_s=10.0,
        end_s=70.0,
        score=0.9,
        rank=1,
        render_status=render_status,
        render_uri="clips/existing.mp4" if render_status == RenderStatus.done else None,
    )
    session.add(clip)
    await session.flush()
    session.add(ClipFeedback(clip_id=clip.id, creator_id=creator.id, action=FeedbackAction.upvote))
    await session.commit()
    return creator, video, clip


@pytest.mark.asyncio
async def test_regeneration_preserves_existing_clips_and_feedback(db_session: AsyncSession):
    creator, video, clip = await _seed(db_session)
    try:
        # Re-run generation for a video that already has clips → must no-op.
        result = await generate_and_rank_clips(
            session=db_session,
            video_id=video.id,
            creator_id=creator.id,
            timeline={},  # unused: the existing-clips guard short-circuits first
        )
        assert [c.id for c in result] == [clip.id]  # returned the existing clip
        feedback_count = await db_session.scalar(
            select(ClipFeedback).where(ClipFeedback.clip_id == clip.id)
        )
        assert feedback_count is not None  # feedback row survived (not cascade-deleted)
    finally:
        await db_session.execute(delete(Clip).where(Clip.creator_id == creator.id))
        await db_session.commit()


@pytest.mark.asyncio
async def test_render_skips_when_already_done(db_session: AsyncSession):
    creator, video, clip = await _seed(db_session, render_status=RenderStatus.done)
    try:
        with (
            patch("clip_engine.render.render_clip_file") as mock_render,
            patch("worker.storage.local_path") as mock_local,
            patch("worker.storage.upload_file") as mock_upload,
        ):
            await _render_clip_async(str(clip.id))
        # Already done → no re-encode, no storage I/O.
        mock_render.assert_not_called()
        mock_local.assert_not_called()
        mock_upload.assert_not_called()
    finally:
        await db_session.execute(delete(Clip).where(Clip.creator_id == creator.id))
        await db_session.commit()
