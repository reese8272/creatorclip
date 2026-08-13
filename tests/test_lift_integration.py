"""Integration tests for GET /creators/me/insights/lift (Issue 374).

Marker: integration — needs real Postgres (+ RLS), per the two-lane rule.
Covers the honest empty state, the tri-state outcome buckets against real
rows, and per-creator isolation.

Isolation is the load-bearing case here: ``ClipOutcome`` has NO ``creator_id``
column (it is PK'd on ``clip_id``), so the creator filter reaches it only
through the join to ``Clip``. A regression that filtered the outcome table
directly would leak across tenants, which is exactly what this pins.
"""

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from auth import create_session_token
from config import settings
from main import app
from models import (
    Clip,
    ClipFormat,
    ClipOutcome,
    ClipPublication,
    Creator,
    IngestStatus,
    OnboardingState,
    PublishStatus,
    Video,
    VideoKind,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clear_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _auth_cookie(creator_id: uuid.UUID) -> dict[str, str]:
    return {"cc_session": create_session_token(creator_id)}


async def _seed_creator(session: AsyncSession) -> Creator:
    creator = Creator(
        google_sub=f"test_lift_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_lift_{uuid.uuid4().hex[:6]}",
        channel_title="Lift Test",
        onboarding_state=OnboardingState.active,
    )
    session.add(creator)
    await session.commit()
    return creator


async def _seed_published_clip(
    session: AsyncSession,
    creator_id: uuid.UUID,
    *,
    performed_well: bool | None,
    score: float = 0.7,
    setup_start_s: float | None = 10.0,
    start_s: float = 10.0,
    end_s: float = 40.0,
    peak_s: float = 25.0,
) -> Clip:
    """Seed video → clip → publication(done) → outcome, mirroring the real path."""
    video = Video(
        creator_id=creator_id,
        youtube_video_id=f"yt_{uuid.uuid4().hex[:11]}",
        title="src",
        kind=VideoKind.long,
        duration_s=600.0,
        ingest_status=IngestStatus.done,
    )
    session.add(video)
    await session.flush()

    clip = Clip(
        video_id=video.id,
        creator_id=creator_id,
        start_s=start_s,
        end_s=end_s,
        peak_s=peak_s,
        setup_start_s=setup_start_s,
        score=score,
        dna_match=0.6,
        format=ClipFormat.short,
        signals_jsonb={"principle": "clip-the-setup"},
    )
    session.add(clip)
    await session.flush()

    session.add(
        ClipPublication(
            clip_id=clip.id,
            creator_id=creator_id,
            status=PublishStatus.done,
            youtube_video_id=f"pub_{uuid.uuid4().hex[:8]}",
        )
    )
    session.add(
        ClipOutcome(
            clip_id=clip.id,
            published_youtube_id=f"pub_{uuid.uuid4().hex[:8]}",
            performed_well=performed_well,
            final=False,
            fetched_at=datetime.now(UTC),
        )
    )
    await session.commit()
    await session.refresh(clip)
    return clip


async def _cleanup(session: AsyncSession, creator_id: uuid.UUID) -> None:
    await session.execute(delete(Video).where(Video.creator_id == creator_id))
    await session.execute(delete(Creator).where(Creator.id == creator_id))
    await session.commit()


@pytest.mark.asyncio
async def test_lift_empty_creator_returns_honest_zeroes_not_404(client, db_session):
    """The beta reality: a creator with nothing published still gets the shape.

    #374's own risk note says the empty state IS the feature at launch.
    """
    creator = await _seed_creator(db_session)
    try:
        resp = client.get("/creators/me/insights/lift", cookies=_auth_cookie(creator.id))
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["published_count"] == 0
        assert data["judged_count"] == 0
        assert data["rate"] is None
        assert data["contrast"] is None
        assert data["metric"] and data["checkpoint"]
    finally:
        await _cleanup(db_session, creator.id)


@pytest.mark.asyncio
async def test_lift_buckets_deferred_verdicts_separately(client, db_session):
    """A published-but-unjudgeable clip must not be reported as a failure."""
    creator = await _seed_creator(db_session)
    try:
        await _seed_published_clip(db_session, creator.id, performed_well=True)
        await _seed_published_clip(db_session, creator.id, performed_well=False)
        await _seed_published_clip(db_session, creator.id, performed_well=None)

        resp = client.get("/creators/me/insights/lift", cookies=_auth_cookie(creator.id))
        assert resp.status_code == 200, resp.text
        data = resp.json()

        assert data["published_count"] == 3
        assert data["judged_count"] == 2
        assert data["beat_median_count"] == 1
        assert data["below_median_count"] == 1
        assert data["deferred_count"] == 1
        # Below the minimum, so counts only — no rate claim.
        assert data["rate"] is None
    finally:
        await _cleanup(db_session, creator.id)


@pytest.mark.asyncio
async def test_lift_geometry_uses_the_setup_origin(client, db_session):
    """Issue 475(b) — every other surface measures clip-relative time from the
    `setup_start_s ?? start_s` origin (the engine starts the clip at the setup).
    The lift contrast must too: with setup_start_s=5 / start_s=12 / end_s=45 /
    peak_s=30 the delivered clip is 40 s with a 25 s setup lead — not the 33/18
    the raw start_s would claim."""
    creator = await _seed_creator(db_session)
    geometry = {"setup_start_s": 5.0, "start_s": 12.0, "end_s": 45.0, "peak_s": 30.0}
    try:
        # 3 winners + 3 losers so the contrast panel is emitted.
        for verdict in (True, True, True, False, False, False):
            await _seed_published_clip(db_session, creator.id, performed_well=verdict, **geometry)

        resp = client.get("/creators/me/insights/lift", cookies=_auth_cookie(creator.id))
        assert resp.status_code == 200, resp.text
        contrast = resp.json()["contrast"]
        assert contrast is not None

        for group in ("winners", "underperformers"):
            assert contrast[group]["median_duration_s"] == pytest.approx(40.0), (
                "duration must be measured from the setup origin, not start_s"
            )
            assert contrast[group]["median_setup_lead_s"] == pytest.approx(25.0), (
                "setup lead must be measured from the setup origin, not start_s"
            )
    finally:
        await _cleanup(db_session, creator.id)


@pytest.mark.asyncio
async def test_lift_per_creator_isolation(client, db_session):
    """Creator A must never see B's outcomes.

    ClipOutcome has no creator_id, so this pins that the join carries isolation.
    """
    creator_a = await _seed_creator(db_session)
    creator_b = await _seed_creator(db_session)
    try:
        await _seed_published_clip(db_session, creator_b.id, performed_well=True)
        await _seed_published_clip(db_session, creator_b.id, performed_well=True)

        resp = client.get("/creators/me/insights/lift", cookies=_auth_cookie(creator_a.id))
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["published_count"] == 0, "creator A must not see creator B's clips"
        assert data["beat_median_count"] == 0
    finally:
        await _cleanup(db_session, creator_a.id)
        await _cleanup(db_session, creator_b.id)
