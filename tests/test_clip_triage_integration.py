"""
Integration tests for Issues 444 + 472 — one training label per clip, and the
pile ⇒ training invariant, against a real Postgres.

The dedup lives in SQL (a window function), so a mocked session cannot see it.
This is the behavioural proof: before the 444 fix, an upvote followed by a
downvote of the SAME clip produced two contradictory training samples — a
positive and a negative with identical features. Issue 472 is the sequel: the
shipped Trim → Skip UI flow inserted a `skip` feedback row that WON the verdict
partition and silently retracted the keep label while the pile stayed `kept`.
The API-level tests here drive the real endpoints (TestClient + cookie auth,
production `get_session` — never the pytest-loop session) and assert the
invariant end to end.

Marked `integration` (excluded from the default run — see pytest.ini).
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from auth import create_session_token
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
from preference.train import build_and_save, latest_verdict_subquery

pytestmark = pytest.mark.integration


def _auth_cookie(creator_id: uuid.UUID) -> dict[str, str]:
    return {"cc_session": create_session_token(creator_id)}


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


# ── Issue 472 — feedback `skip` must not retract the creator's latest label ───


async def _triage_of(session: AsyncSession, clip_id: uuid.UUID) -> ClipTriage:
    return (await session.execute(select(Clip.triage).where(Clip.id == clip_id))).scalar_one()


async def _skip_row_count(session: AsyncSession, clip_id: uuid.UUID) -> int:
    return (
        await session.execute(
            select(func.count())
            .select_from(ClipFeedback)
            .where(ClipFeedback.clip_id == clip_id, ClipFeedback.action == FeedbackAction.skip)
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_trim_then_skip_keeps_the_trim_label_trained(client, db_session: AsyncSession):
    """The Issue-472 SEV1 flow: "Save trim" (a keep label, deliberately does not
    advance) followed by the always-visible Skip button. Skip is queue
    navigation, not a verdict — it must not erase the trim label, must not
    write a ClipFeedback row, and must leave the pile `kept`."""
    creator_id, video = await _seed(db_session)
    trimmed = await _clip(db_session, creator_id, video, 0.8)
    downvoted = await _clip(db_session, creator_id, video, 0.2)
    await db_session.commit()
    cookies = _auth_cookie(creator_id)

    resp = client.post(
        f"/clips/{trimmed.id}/feedback",
        json={"action": "trim", "trim_start_s": 5.0, "trim_end_s": 50.0},
        cookies=cookies,
    )
    assert resp.status_code == 201, resp.text
    skip_resp = client.post(
        f"/clips/{trimmed.id}/feedback", json={"action": "skip"}, cookies=cookies
    )
    assert skip_resp.status_code == 201, skip_resp.text
    resp = client.post(
        f"/clips/{downvoted.id}/feedback", json={"action": "downvote"}, cookies=cookies
    )
    assert resp.status_code == 201, resp.text

    # The training partition still yields the keep label: trim + downvote = 2.
    scorer = await build_and_save(db_session, creator_id)
    assert scorer is not None, "skip after trim must not erase the only positive label"
    assert scorer.label_count == 2

    # Triage stays kept, and skip persisted nothing.
    assert await _triage_of(db_session, trimmed.id) == ClipTriage.kept
    assert await _skip_row_count(db_session, trimmed.id) == 0
    assert skip_resp.json() == {"id": None, "action": "skip"}


@pytest.mark.asyncio
async def test_pile_state_implies_training_state(client, db_session: AsyncSession):
    """The 444 invariant, end to end: kept/dropped ⇒ exactly one surviving
    matching-polarity label in the verdict partition; pending ⇒ none. A PUT
    /triage retraction (→ pending) still records `skip`; a feedback-surface
    skip records nothing."""
    creator_id, video = await _seed(db_session)
    kept = await _clip(db_session, creator_id, video, 0.8)
    dropped = await _clip(db_session, creator_id, video, 0.2)
    retracted = await _clip(db_session, creator_id, video, 0.5)
    trimmed = await _clip(db_session, creator_id, video, 0.6)
    await db_session.commit()
    cookies = _auth_cookie(creator_id)

    for clip, action in ((kept, "upvote"), (dropped, "downvote"), (retracted, "upvote")):
        r = client.post(f"/clips/{clip.id}/feedback", json={"action": action}, cookies=cookies)
        assert r.status_code == 201, r.text
    # Genuine retraction: back to the review queue via PUT /triage.
    r = client.put(f"/clips/{retracted.id}/triage", json={"triage": "pending"}, cookies=cookies)
    assert r.status_code == 200, r.text
    # The 472 flow: trim, then skip past it.
    r = client.post(
        f"/clips/{trimmed.id}/feedback",
        json={"action": "trim", "trim_start_s": 5.0, "trim_end_s": 50.0},
        cookies=cookies,
    )
    assert r.status_code == 201, r.text
    r = client.post(f"/clips/{trimmed.id}/feedback", json={"action": "skip"}, cookies=cookies)
    assert r.status_code == 201, r.text

    latest = latest_verdict_subquery(creator_id)
    rows = (
        await db_session.execute(
            select(Clip.id, Clip.triage, ClipFeedback.action)
            .join(ClipFeedback, ClipFeedback.clip_id == Clip.id)
            .join(latest, latest.c.feedback_id == ClipFeedback.id)
            .where(latest.c.rn == 1, Clip.creator_id == creator_id)
        )
    ).all()
    verdict_by_clip = {r.id: r.action for r in rows}
    triage_by_clip = {
        r.id: r.triage
        for r in (
            await db_session.execute(
                select(Clip.id, Clip.triage).where(Clip.creator_id == creator_id)
            )
        ).all()
    }

    _POSITIVE = {FeedbackAction.upvote, FeedbackAction.trim}
    for clip_id, triage in triage_by_clip.items():
        verdict = verdict_by_clip.get(clip_id)
        if triage == ClipTriage.kept:
            assert verdict in _POSITIVE, f"kept clip {clip_id} has surviving verdict {verdict}"
        elif triage == ClipTriage.dropped:
            assert verdict == FeedbackAction.downvote
        else:  # pending — no trainable label may survive the partition
            assert verdict in (None, FeedbackAction.skip)

    assert triage_by_clip[kept.id] == ClipTriage.kept
    assert triage_by_clip[dropped.id] == ClipTriage.dropped
    assert triage_by_clip[retracted.id] == ClipTriage.pending
    assert triage_by_clip[trimmed.id] == ClipTriage.kept
