"""
Integration test for Issue 60 — the retrain task trains a preference model from
real feedback and self-debounces, against a real Postgres.

Marked `integration` (excluded from the default run — see pytest.ini). The blend
math + rerank gating are unit-tested in tests/test_preference_rerank.py; this
proves the end-to-end training loop and the no-new-feedback debounce.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from models import (
    Clip,
    ClipFeedback,
    Creator,
    FeedbackAction,
    OnboardingState,
    PreferenceModel,
    RenderStatus,
    Video,
    VideoKind,
)
from worker.tasks import _retrain_preference_async

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed_creator_with_feedback(session: AsyncSession) -> uuid.UUID:
    creator = Creator(
        google_sub=f"test_retrain_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_retrain_{uuid.uuid4().hex[:6]}",
        channel_title="Retrain Test Channel",
        onboarding_state=OnboardingState.active,
    )
    session.add(creator)
    await session.flush()

    video = Video(
        creator_id=creator.id,
        youtube_video_id=f"yt_{uuid.uuid4().hex[:8]}",
        title="Retrain fixture",
        kind=VideoKind.long,
    )
    session.add(video)
    await session.flush()

    # Two clips with both feedback classes so build_and_save has a trainable set.
    for score, action in ((0.8, FeedbackAction.upvote), (0.2, FeedbackAction.downvote)):
        clip = Clip(
            video_id=video.id,
            creator_id=creator.id,
            setup_start_s=10.0,
            start_s=10.0,
            end_s=70.0,
            score=score,
            dna_match=score,
            signals_jsonb={"features": {"hook_energy": score}},
            render_status=RenderStatus.pending,
        )
        session.add(clip)
        await session.flush()
        session.add(ClipFeedback(clip_id=clip.id, creator_id=creator.id, action=action))
    await session.commit()
    return creator.id


async def _model_versions(session: AsyncSession, creator_id: uuid.UUID) -> list[int]:
    rows = await session.execute(
        select(PreferenceModel.version).where(PreferenceModel.creator_id == creator_id)
    )
    return sorted(rows.scalars().all())


@pytest.mark.asyncio
async def test_retrain_trains_then_debounces(db_session: AsyncSession):
    creator_id = await _seed_creator_with_feedback(db_session)
    try:
        # First run: no model yet → trains version 1.
        await _retrain_preference_async(str(creator_id))
        assert await _model_versions(db_session, creator_id) == [1]

        # Second run with no new feedback → self-debounce, still just version 1.
        await _retrain_preference_async(str(creator_id))
        assert await _model_versions(db_session, creator_id) == [1]
    finally:
        await db_session.execute(
            delete(PreferenceModel).where(PreferenceModel.creator_id == creator_id)
        )
        await db_session.execute(delete(Clip).where(Clip.creator_id == creator_id))
        await db_session.commit()
