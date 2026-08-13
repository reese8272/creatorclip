"""
Integration tests for Issue 475(a) — the efficacy harness loads EXACTLY the
dataset production trains on, against a real Postgres.

`load_labeled_clips` shares `latest_verdict_subquery` with `build_and_save`, but
until Issue 475 it lacked the TRAINABLE SQL filter: a clip whose latest verdict
was a `skip` retraction still flowed into the harness, and an attached
`performed_well=True` outcome graded it 2.0 via the outcome-first relevance rule
— the offline NDCG then measured labels production had discarded, and the
per-retrain warn-ratchet could fire on phantom shifts. These tests pin
harness-set == training-set for identical inputs, via the shared query.

Marked `integration` (excluded from the default run — see pytest.ini).
"""

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from models import (
    Clip,
    ClipFeedback,
    ClipOutcome,
    ClipTriage,
    Creator,
    FeedbackAction,
    OnboardingState,
    RenderStatus,
    Video,
    VideoKind,
)
from preference.efficacy import load_labeled_clips

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
        google_sub=f"test_eff_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_eff_{uuid.uuid4().hex[:6]}",
        channel_title="Efficacy Loader Test",
        onboarding_state=OnboardingState.active,
    )
    session.add(creator)
    await session.flush()
    video = Video(
        creator_id=creator.id,
        youtube_video_id=f"yt_{uuid.uuid4().hex[:8]}",
        title="Efficacy fixture",
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
    session.add(ClipFeedback(clip_id=clip.id, creator_id=clip.creator_id, action=action))
    await session.flush()


@pytest.mark.asyncio
async def test_skip_latest_verdict_with_good_outcome_is_excluded(db_session: AsyncSession):
    """A retracted clip (latest verdict `skip`) carrying performed_well=True must
    NOT appear in the harness set — production training discarded it."""
    creator_id, video = await _seed(db_session)
    kept = await _clip(db_session, creator_id, video, 0.8)
    dropped = await _clip(db_session, creator_id, video, 0.2)
    retracted = await _clip(db_session, creator_id, video, 0.5)

    await _feedback(db_session, kept, FeedbackAction.upvote)
    await _feedback(db_session, dropped, FeedbackAction.downvote)
    await _feedback(db_session, retracted, FeedbackAction.upvote)
    await _feedback(db_session, retracted, FeedbackAction.skip)  # retraction
    db_session.add(
        ClipOutcome(
            clip_id=retracted.id,
            published_youtube_id=f"yt_{uuid.uuid4().hex[:8]}",
            performed_well=True,  # must not resurrect the discarded label
            fetched_at=datetime.now(UTC),
            final=True,
        )
    )
    await db_session.commit()

    labeled = await load_labeled_clips(db_session, creator_id)

    ids = {lc.clip_id for lc in labeled}
    assert retracted.id not in ids, (
        "a skip-retracted clip must not enter the eval set via its outcome"
    )
    assert ids == {kept.id, dropped.id}


@pytest.mark.asyncio
async def test_efficacy_set_equals_training_set_for_identical_inputs(db_session: AsyncSession):
    """The acceptance pin: for the same creator, `load_labeled_clips` yields the
    same (clip, action) rows the shared training-rows select feeds
    `build_and_save` — measured against the shared query itself."""
    from preference.train import training_rows_select

    creator_id, video = await _seed(db_session)
    a = await _clip(db_session, creator_id, video, 0.9)
    b = await _clip(db_session, creator_id, video, 0.4)
    c = await _clip(db_session, creator_id, video, 0.6)
    d = await _clip(db_session, creator_id, video, 0.3)

    await _feedback(db_session, a, FeedbackAction.upvote)
    await _feedback(db_session, b, FeedbackAction.downvote)
    await _feedback(db_session, c, FeedbackAction.trim)
    await _feedback(db_session, c, FeedbackAction.format)  # must not supersede trim
    await _feedback(db_session, d, FeedbackAction.upvote)
    await _feedback(db_session, d, FeedbackAction.skip)  # retracted → in neither set
    await db_session.commit()

    train_rows = (await db_session.execute(training_rows_select(creator_id))).all()
    train_pairs = {(fb.clip_id, fb.action.value) for fb, _clip_row, _outcome in train_rows}

    labeled = await load_labeled_clips(db_session, creator_id)
    harness_pairs = {(lc.clip_id, lc.action) for lc in labeled}

    assert harness_pairs == train_pairs
    assert harness_pairs == {(a.id, "upvote"), (b.id, "downvote"), (c.id, "trim")}
