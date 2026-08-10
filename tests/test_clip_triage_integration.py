"""
Integration test for Issue 444 — one training label per clip, against a real
Postgres.

The dedup lives in SQL (a window function), so a mocked session cannot see it.
This is the behavioural proof: before the fix, an upvote followed by a downvote
of the SAME clip produced two contradictory training samples — a positive and a
negative with identical features. Reversible triage piles would have made that
routine rather than rare.

Marked `integration` (excluded from the default run — see pytest.ini).
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from models import (
    Clip,
    ClipFeedback,
    ClipTriage,
    Creator,
    FeedbackAction,
    OnboardingState,
    RenderStatus,
    Video,
    VideoKind,
)
from preference.train import build_and_save

pytestmark = pytest.mark.integration

_FEATURES = {
    "signal_density": 0.5,
    "hook_energy": 0.3,
    "silence_ratio": 0.1,
    "clip_duration_s": 45.0,
    "setup_length_s": 10.0,
    "has_retention_spike": False,
    "has_laughter": False,
}


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(session: AsyncSession) -> tuple[uuid.UUID, Video]:
    creator = Creator(
        google_sub=f"test_triage_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_triage_{uuid.uuid4().hex[:6]}",
        channel_title="Triage Test Channel",
        onboarding_state=OnboardingState.active,
    )
    session.add(creator)
    await session.flush()
    video = Video(
        creator_id=creator.id,
        youtube_video_id=f"yt_{uuid.uuid4().hex[:8]}",
        title="Triage fixture",
        kind=VideoKind.long,
    )
    session.add(video)
    await session.flush()
    return creator.id, video


async def _clip(session: AsyncSession, creator_id: uuid.UUID, video: Video, score: float) -> Clip:
    clip = Clip(
        video_id=video.id,
        creator_id=creator_id,
        setup_start_s=10.0,
        start_s=10.0,
        end_s=70.0,
        peak_s=40.0,
        score=score,
        signals_jsonb={"features": _FEATURES},
        render_status=RenderStatus.pending,
        triage=ClipTriage.pending,
    )
    session.add(clip)
    await session.flush()
    return clip


async def _feedback(session: AsyncSession, clip: Clip, action: FeedbackAction) -> None:
    """Rows are appended in call order; created_at is set per-row by the model
    default, and the dedup breaks any tie on id DESC, so the LAST call wins."""
    session.add(ClipFeedback(clip_id=clip.id, creator_id=clip.creator_id, action=action))
    await session.flush()


@pytest.mark.asyncio
async def test_contradictory_feedback_yields_one_training_label(db_session: AsyncSession):
    """The headline defect. Two verdicts on one clip must collapse to the
    newest, not stack as a positive AND a negative with identical features."""
    creator_id, video = await _seed(db_session)
    flip = await _clip(db_session, creator_id, video, 0.8)
    steady = await _clip(db_session, creator_id, video, 0.2)

    await _feedback(db_session, flip, FeedbackAction.upvote)
    await _feedback(db_session, flip, FeedbackAction.downvote)  # changed their mind
    await _feedback(db_session, steady, FeedbackAction.upvote)
    await db_session.commit()

    scorer = await build_and_save(db_session, creator_id)

    assert scorer is not None
    # Two clips judged → two labels. Before the fix this was three.
    assert scorer.label_count == 2


@pytest.mark.asyncio
async def test_returning_a_clip_to_pending_retracts_its_label(db_session: AsyncSession):
    """`skip` wins the verdict partition and is then filtered out, so a clip
    sent back to the review queue contributes nothing — rather than leaving its
    superseded verdict training forever."""
    creator_id, video = await _seed(db_session)
    retracted = await _clip(db_session, creator_id, video, 0.8)
    kept = await _clip(db_session, creator_id, video, 0.6)
    dropped = await _clip(db_session, creator_id, video, 0.2)

    await _feedback(db_session, retracted, FeedbackAction.upvote)
    await _feedback(db_session, retracted, FeedbackAction.skip)  # back to the queue
    await _feedback(db_session, kept, FeedbackAction.upvote)
    await _feedback(db_session, dropped, FeedbackAction.downvote)
    await db_session.commit()

    scorer = await build_and_save(db_session, creator_id)

    assert scorer is not None
    assert scorer.label_count == 2, "the retracted clip must not contribute a label"


@pytest.mark.asyncio
async def test_choosing_a_format_does_not_supersede_a_verdict(db_session: AsyncSession):
    """`format` is render mechanics, not a judgement, so it is excluded from the
    verdict partition — picking an aspect ratio must not erase an upvote."""
    creator_id, video = await _seed(db_session)
    kept = await _clip(db_session, creator_id, video, 0.8)
    dropped = await _clip(db_session, creator_id, video, 0.2)

    await _feedback(db_session, kept, FeedbackAction.upvote)
    await _feedback(db_session, kept, FeedbackAction.format)
    await _feedback(db_session, dropped, FeedbackAction.downvote)
    await db_session.commit()

    scorer = await build_and_save(db_session, creator_id)

    assert scorer is not None
    assert scorer.label_count == 2, "format must leave the upvote standing"
