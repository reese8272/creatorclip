"""
Integration tests for Issues 60 + 473 — the retrain task trains a preference
model from real feedback, self-debounces, and its watermark is NOT blind to
retractions or outcome arrivals, against a real Postgres.

Marked `integration` (excluded from the default run — see pytest.ini). The blend
math + rerank gating are unit-tested in tests/test_preference_rerank.py; this
proves the end-to-end training loop and the debounce predicate.
"""

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from models import (
    Clip,
    ClipFeedback,
    ClipOutcome,
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

_DEFAULT_FEEDBACK = ((0.8, FeedbackAction.upvote), (0.2, FeedbackAction.downvote))


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed_creator_with_feedback(
    session: AsyncSession,
    feedback: tuple[tuple[float, FeedbackAction], ...] = _DEFAULT_FEEDBACK,
) -> tuple[uuid.UUID, list[Clip]]:
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

    # Clips with both feedback classes so build_and_save has a trainable set.
    clips: list[Clip] = []
    for score, action in feedback:
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
        clips.append(clip)
    await session.commit()
    return creator.id, clips


async def _model_versions(session: AsyncSession, creator_id: uuid.UUID) -> list[int]:
    rows = await session.execute(
        select(PreferenceModel.version).where(PreferenceModel.creator_id == creator_id)
    )
    return sorted(rows.scalars().all())


async def _cleanup(session: AsyncSession, creator_id: uuid.UUID) -> None:
    await session.execute(delete(PreferenceModel).where(PreferenceModel.creator_id == creator_id))
    await session.execute(delete(Clip).where(Clip.creator_id == creator_id))
    await session.commit()


@pytest.mark.asyncio
async def test_retrain_trains_then_debounces(db_session: AsyncSession):
    """The coalescing pin: with nothing new — no verdict, no outcome — a repeat
    run is a no-op. Issue 473 widens the watermark; this stays true."""
    creator_id, _clips = await _seed_creator_with_feedback(db_session)
    try:
        # First run: no model yet → trains version 1.
        await _retrain_preference_async(str(creator_id))
        assert await _model_versions(db_session, creator_id) == [1]

        # Second run with no new feedback → self-debounce, still just version 1.
        await _retrain_preference_async(str(creator_id))
        assert await _model_versions(db_session, creator_id) == [1]
    finally:
        await _cleanup(db_session, creator_id)


# ── Issue 473 — the watermark must see retractions and outcome arrivals ───────


@pytest.mark.asyncio
async def test_skip_only_retraction_triggers_a_retrain(db_session: AsyncSession):
    """A `skip` row (PUT /triage → pending) REMOVES a label from the effective
    training set. It is not trainable, so the old watermark ignored it and the
    model kept serving the retracted label indefinitely."""
    creator_id, clips = await _seed_creator_with_feedback(
        db_session,
        feedback=(
            (0.8, FeedbackAction.upvote),
            (0.6, FeedbackAction.upvote),
            (0.2, FeedbackAction.downvote),
        ),
    )
    try:
        await _retrain_preference_async(str(creator_id))
        assert await _model_versions(db_session, creator_id) == [1]

        # Retract the second upvote — exactly what PUT /triage → pending writes.
        db_session.add(
            ClipFeedback(clip_id=clips[1].id, creator_id=creator_id, action=FeedbackAction.skip)
        )
        await db_session.commit()

        await _retrain_preference_async(str(creator_id))
        assert await _model_versions(db_session, creator_id) == [1, 2], (
            "a retraction changes the effective training set — the debounce must retrain"
        )
    finally:
        await _cleanup(db_session, creator_id)


@pytest.mark.asyncio
async def test_outcome_arrival_triggers_a_retrain(db_session: AsyncSession):
    """A `performed_well` write multiplies the clip's sample weight 3× — the
    model must be refit even though no feedback row changed."""
    creator_id, clips = await _seed_creator_with_feedback(db_session)
    try:
        await _retrain_preference_async(str(creator_id))
        assert await _model_versions(db_session, creator_id) == [1]

        # poll_clip_outcomes lands a judged outcome after the model was saved.
        db_session.add(
            ClipOutcome(
                clip_id=clips[0].id,
                published_youtube_id=f"yt_{uuid.uuid4().hex[:8]}",
                performed_well=True,
                fetched_at=datetime.now(UTC),
                final=True,
            )
        )
        await db_session.commit()

        await _retrain_preference_async(str(creator_id))
        assert await _model_versions(db_session, creator_id) == [1, 2], (
            "an outcome arrival reweights the training set — the debounce must retrain"
        )
    finally:
        await _cleanup(db_session, creator_id)
