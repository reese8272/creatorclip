"""Integration tests for GET /creators/me/insights (Issue 93).

Marker: integration. Covers totals aggregation, DNA snapshot
resolution, performer-list resolution, per-creator isolation,
and the empty-state shape (no DNA, no videos).
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from auth import create_session_token
from config import settings
from models import (
    Creator,
    CreatorDna,
    DnaStatus,
    IngestStatus,
    OnboardingState,
    Video,
    VideoKind,
    VideoMetrics,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed_creator(session: AsyncSession) -> Creator:
    creator = Creator(
        google_sub=f"test_ins_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_ins_{uuid.uuid4().hex[:6]}",
        channel_title="Insights Test",
        onboarding_state=OnboardingState.active,
    )
    session.add(creator)
    await session.commit()
    return creator


async def _seed_video(
    session: AsyncSession,
    creator_id: uuid.UUID,
    *,
    kind: VideoKind,
    status: IngestStatus,
    duration_s: float | None,
    title: str = "v",
    views: int | None = None,
    engagement_rate: float | None = None,
) -> Video:
    yt_id = f"yt_{uuid.uuid4().hex[:11]}"
    video = Video(
        creator_id=creator_id,
        youtube_video_id=yt_id,
        title=title,
        kind=kind,
        duration_s=duration_s,
        ingest_status=status,
    )
    session.add(video)
    await session.flush()
    if views is not None or engagement_rate is not None:
        session.add(
            VideoMetrics(
                video_id=video.id,
                views=views,
                engagement_rate=engagement_rate,
            )
        )
    await session.commit()
    await session.refresh(video)
    return video


async def _cleanup(session: AsyncSession, creator_id: uuid.UUID) -> None:
    await session.execute(delete(CreatorDna).where(CreatorDna.creator_id == creator_id))
    # VideoMetrics cascades via Video FK
    await session.execute(delete(Video).where(Video.creator_id == creator_id))
    await session.execute(delete(Creator).where(Creator.id == creator_id))
    await session.commit()


def _auth_cookie(creator_id: uuid.UUID) -> dict[str, str]:
    return {"cc_session": create_session_token(creator_id)}


# ── Empty state ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_insights_empty_creator_returns_zeros(client, db_session: AsyncSession):
    """A brand-new creator with no videos and no DNA should get the
    full payload shape with zero totals — never a 404."""
    creator = await _seed_creator(db_session)
    try:
        resp = client.get("/creators/me/insights", cookies=_auth_cookie(creator.id))
        assert resp.status_code == 200, resp.text
        data = resp.json()

        assert data["totals"]["videos_analyzed"] == 0
        assert data["totals"]["shorts"] == 0
        assert data["totals"]["longs"] == 0
        assert data["totals"]["ingested_done"] == 0
        assert data["totals"]["total_minutes_processed"] == 0.0

        assert data["dna"]["version"] is None
        assert data["dna"]["status"] is None

        assert data["top_performers"] == []
        assert data["bottom_performers"] == []
    finally:
        await _cleanup(db_session, creator.id)


# ── Totals aggregation ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_insights_totals_aggregate_by_kind_and_status(
    client, db_session: AsyncSession
):
    """Pin the math: 2 longs + 3 shorts, 1 long ingested-done, total
    minutes_processed sums duration_s."""
    creator = await _seed_creator(db_session)
    try:
        await _seed_video(
            db_session, creator.id, kind=VideoKind.long,
            status=IngestStatus.done, duration_s=600.0, title="L1",
        )
        await _seed_video(
            db_session, creator.id, kind=VideoKind.long,
            status=IngestStatus.pending, duration_s=900.0, title="L2",
        )
        for i in range(3):
            await _seed_video(
                db_session, creator.id, kind=VideoKind.short,
                status=IngestStatus.pending, duration_s=60.0, title=f"S{i}",
            )

        resp = client.get("/creators/me/insights", cookies=_auth_cookie(creator.id))
        data = resp.json()
        assert data["totals"]["videos_analyzed"] == 5
        assert data["totals"]["longs"] == 2
        assert data["totals"]["shorts"] == 3
        assert data["totals"]["ingested_done"] == 1
        # 600 + 900 + 3×60 = 1680s = 28.0 min
        assert data["totals"]["total_minutes_processed"] == 28.0
    finally:
        await _cleanup(db_session, creator.id)


# ── DNA snapshot ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_insights_dna_snapshot_uses_latest_confirmed(
    client, db_session: AsyncSession
):
    """When v1 is confirmed and v2 is a draft (rebuild in progress),
    the snapshot should surface v2 — the latest by version, not
    necessarily the latest confirmed."""
    creator = await _seed_creator(db_session)
    try:
        db_session.add(CreatorDna(
            creator_id=creator.id, version=1,
            status=DnaStatus.confirmed,
            brief_text="v1", patterns_jsonb={},
            top_video_ids_jsonb=[], bottom_video_ids_jsonb=[],
            optimal_clip_len_s=12.0, best_source_region="first_third",
            optimal_upload_gap_h=8.0,
        ))
        db_session.add(CreatorDna(
            creator_id=creator.id, version=2,
            status=DnaStatus.draft,
            brief_text="v2", patterns_jsonb={},
            top_video_ids_jsonb=[], bottom_video_ids_jsonb=[],
            optimal_clip_len_s=14.5, best_source_region="middle",
            optimal_upload_gap_h=10.0,
        ))
        await db_session.commit()

        resp = client.get("/creators/me/insights", cookies=_auth_cookie(creator.id))
        data = resp.json()
        assert data["dna"]["version"] == 2
        assert data["dna"]["status"] == "draft"
        assert data["dna"]["optimal_clip_len_s"] == 14.5
        assert data["dna"]["best_source_region"] == "middle"
        assert data["dna"]["optimal_upload_gap_h"] == 10.0
    finally:
        await _cleanup(db_session, creator.id)


# ── Performers ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_insights_top_performers_resolve_to_video_payloads(
    client, db_session: AsyncSession
):
    """DNA's top_video_ids_jsonb is a list of Video UUIDs. The endpoint
    resolves them to the full {title, kind, views, engagement_rate}
    shape, preserving input order."""
    creator = await _seed_creator(db_session)
    try:
        a = await _seed_video(
            db_session, creator.id, kind=VideoKind.long,
            status=IngestStatus.done, duration_s=600.0, title="A title",
            views=10000, engagement_rate=0.12,
        )
        b = await _seed_video(
            db_session, creator.id, kind=VideoKind.short,
            status=IngestStatus.done, duration_s=60.0, title="B title",
            views=500, engagement_rate=0.04,
        )

        db_session.add(CreatorDna(
            creator_id=creator.id, version=1,
            status=DnaStatus.confirmed,
            brief_text="x", patterns_jsonb={},
            top_video_ids_jsonb=[str(a.id), str(b.id)],
            bottom_video_ids_jsonb=[],
        ))
        await db_session.commit()

        resp = client.get("/creators/me/insights", cookies=_auth_cookie(creator.id))
        data = resp.json()
        top = data["top_performers"]
        assert len(top) == 2
        assert top[0]["title"] == "A title"
        assert top[0]["views"] == 10000
        assert top[0]["engagement_rate"] == 0.12
        assert top[0]["kind"] == "long"
        assert top[1]["title"] == "B title"
        assert top[1]["kind"] == "short"
    finally:
        await _cleanup(db_session, creator.id)


@pytest.mark.asyncio
async def test_insights_drops_stale_video_ids_silently(
    client, db_session: AsyncSession
):
    """A DNA row with a top_video_ids reference to a video that's been
    deleted (or never belonged to this creator) drops that entry — does
    NOT 404 the whole endpoint."""
    creator = await _seed_creator(db_session)
    try:
        real = await _seed_video(
            db_session, creator.id, kind=VideoKind.long,
            status=IngestStatus.done, duration_s=600.0, title="real",
            engagement_rate=0.10,
        )
        ghost_id = uuid.uuid4()

        db_session.add(CreatorDna(
            creator_id=creator.id, version=1,
            status=DnaStatus.confirmed,
            brief_text="x", patterns_jsonb={},
            top_video_ids_jsonb=[str(real.id), str(ghost_id)],
            bottom_video_ids_jsonb=[],
        ))
        await db_session.commit()

        resp = client.get("/creators/me/insights", cookies=_auth_cookie(creator.id))
        data = resp.json()
        assert resp.status_code == 200
        assert len(data["top_performers"]) == 1
        assert data["top_performers"][0]["title"] == "real"
    finally:
        await _cleanup(db_session, creator.id)


# ── Per-creator isolation (load-bearing) ───────────────────────────────────


@pytest.mark.asyncio
async def test_insights_per_creator_isolation(client, db_session: AsyncSession):
    """Creator A's payload must contain ONLY A's data — no totals, no
    performers, no DNA from creator B. CLAUDE.md per-creator rule."""
    creator_a = await _seed_creator(db_session)
    creator_b = await _seed_creator(db_session)
    try:
        await _seed_video(
            db_session, creator_a.id, kind=VideoKind.long,
            status=IngestStatus.done, duration_s=600.0, title="A video",
            views=100,
        )
        await _seed_video(
            db_session, creator_b.id, kind=VideoKind.short,
            status=IngestStatus.done, duration_s=60.0, title="B video",
            views=999_999,
        )

        a_resp = client.get("/creators/me/insights", cookies=_auth_cookie(creator_a.id))
        b_resp = client.get("/creators/me/insights", cookies=_auth_cookie(creator_b.id))

        a_data = a_resp.json()
        b_data = b_resp.json()

        # A sees 1 long, 0 shorts
        assert a_data["totals"]["longs"] == 1
        assert a_data["totals"]["shorts"] == 0
        # B sees 0 longs, 1 short
        assert b_data["totals"]["longs"] == 0
        assert b_data["totals"]["shorts"] == 1
        # Total disjoint
        assert a_data["totals"]["videos_analyzed"] == 1
        assert b_data["totals"]["videos_analyzed"] == 1
    finally:
        await _cleanup(db_session, creator_a.id)
        await _cleanup(db_session, creator_b.id)


@pytest.mark.asyncio
async def test_insights_does_not_resolve_other_creators_videos(
    client, db_session: AsyncSession
):
    """If creator A's DNA top_video_ids accidentally references a Video
    belonging to creator B (e.g., from a bug), the resolver must drop
    it. Defends against the Issue 33-shape cross-creator leak."""
    creator_a = await _seed_creator(db_session)
    creator_b = await _seed_creator(db_session)
    try:
        b_video = await _seed_video(
            db_session, creator_b.id, kind=VideoKind.long,
            status=IngestStatus.done, duration_s=600.0, title="B confidential",
            engagement_rate=0.99,
        )
        # Maliciously / accidentally point A's DNA at B's video
        db_session.add(CreatorDna(
            creator_id=creator_a.id, version=1,
            status=DnaStatus.confirmed,
            brief_text="x", patterns_jsonb={},
            top_video_ids_jsonb=[str(b_video.id)],
            bottom_video_ids_jsonb=[],
        ))
        await db_session.commit()

        resp = client.get("/creators/me/insights", cookies=_auth_cookie(creator_a.id))
        data = resp.json()
        assert data["top_performers"] == [], (
            "Cross-creator video ID must NOT resolve — _fetch_performers "
            "filters on Video.creator_id == creator.id."
        )
    finally:
        await _cleanup(db_session, creator_a.id)
        await _cleanup(db_session, creator_b.id)


# ── Auth ───────────────────────────────────────────────────────────────────


def test_insights_requires_auth(client):
    """No session cookie → 401."""
    resp = client.get("/creators/me/insights")
    assert resp.status_code == 401
