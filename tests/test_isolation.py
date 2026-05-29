"""
Integration tests for per-creator isolation — Issue 48.

Verifies that every protected route returns 404 when creator A tries to access
creator B's resources (never 200, never 403). Each route is tested with:
  - Cross-creator access → 404
  - Own-resource access → 200 (sanity check)

Routes with no path param (GET /videos, GET /billing/balance, etc.) are
scoped implicitly by the session user — these tests verify that the response
only reflects the requesting creator's own data, not an empty or cross-creator
payload.

Requires a running Postgres + Alembic schema (see docker-compose.yml).
Excluded from the default pytest run by `addopts = -m "not integration"`.
"""

import uuid
from datetime import UTC

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from auth import SESSION_COOKIE, create_session_token
from config import settings
from models import (
    Clip,
    Creator,
    IngestStatus,
    OnboardingState,
    RenderStatus,
    Video,
    VideoKind,
)

# ── Shared DB fixture ──────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as session:
        yield session
    await engine.dispose()


# ── Seed helpers ───────────────────────────────────────────────────────────────


async def _make_creator(session: AsyncSession, *, suffix: str) -> Creator:
    creator = Creator(
        google_sub=f"iso48_{suffix}_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_iso48_{suffix}",
        channel_title=f"Channel {suffix}",
        onboarding_state=OnboardingState.active,
        minutes_balance=100,
    )
    session.add(creator)
    await session.flush()
    return creator


async def _make_video(session: AsyncSession, creator: Creator) -> Video:
    video = Video(
        creator_id=creator.id,
        youtube_video_id=f"vid_{uuid.uuid4().hex[:10]}",
        kind=VideoKind.long,
        ingest_status=IngestStatus.done,
        duration_s=600.0,
    )
    session.add(video)
    await session.flush()
    return video


async def _make_clip(session: AsyncSession, creator: Creator, video: Video) -> Clip:
    clip = Clip(
        video_id=video.id,
        creator_id=creator.id,
        start_s=10.0,
        end_s=70.0,
        rank=1,
        render_status=RenderStatus.pending,
    )
    session.add(clip)
    await session.flush()
    return clip


def _cookie(creator: Creator) -> dict:
    return {SESSION_COOKIE: create_session_token(creator.id)}


# ── Tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_get_videos_scoped_to_creator(db_session: AsyncSession, client):
    """GET /videos returns only the requesting creator's videos."""
    creator_a = await _make_creator(db_session, suffix="a_gv")
    creator_b = await _make_creator(db_session, suffix="b_gv")
    vid_a = await _make_video(db_session, creator_a)
    await _make_video(db_session, creator_b)
    await db_session.commit()

    try:
        resp = client.get("/videos", cookies=_cookie(creator_a))
        assert resp.status_code == 200
        ids = [v["id"] for v in resp.json()]
        assert str(vid_a.id) in ids
        assert all(v["id"] != str(creator_b.id) for v in resp.json())
    finally:
        await db_session.execute(
            delete(Creator).where(Creator.id.in_([creator_a.id, creator_b.id]))
        )
        await db_session.commit()


@pytest.mark.integration
async def test_post_videos_link_scoped_to_creator(db_session: AsyncSession, client):
    """POST /videos/link creates the video under the authenticated creator only."""
    creator_a = await _make_creator(db_session, suffix="a_lv")
    creator_b = await _make_creator(db_session, suffix="b_lv")
    await db_session.commit()

    yt_id = f"yt_{uuid.uuid4().hex[:8]}"
    try:
        resp = client.post(
            "/videos/link", data={"youtube_video_id": yt_id}, cookies=_cookie(creator_a)
        )
        assert resp.status_code == 200
        # Verify the video is owned by A, not B
        video_id = resp.json()["video_id"]
        from sqlalchemy import select as _sel

        from models import Video as _V

        row = await db_session.scalar(_sel(_V).where(_V.id == uuid.UUID(video_id)))
        assert row is not None
        assert row.creator_id == creator_a.id
        assert row.creator_id != creator_b.id
    finally:
        await db_session.execute(
            delete(Creator).where(Creator.id.in_([creator_a.id, creator_b.id]))
        )
        await db_session.commit()


@pytest.mark.integration
async def test_get_video_status_cross_creator_returns_404(db_session: AsyncSession, client):
    """GET /videos/{id}/status → 404 when A requests B's video."""
    creator_a = await _make_creator(db_session, suffix="a_vs")
    creator_b = await _make_creator(db_session, suffix="b_vs")
    vid_b = await _make_video(db_session, creator_b)
    vid_a = await _make_video(db_session, creator_a)
    await db_session.commit()

    try:
        # Cross-creator: A tries to read B's video
        resp = client.get(f"/videos/{vid_b.id}/status", cookies=_cookie(creator_a))
        assert resp.status_code == 404

        # Sanity: A can read their own video
        resp = client.get(f"/videos/{vid_a.id}/status", cookies=_cookie(creator_a))
        assert resp.status_code == 200
    finally:
        await db_session.execute(
            delete(Creator).where(Creator.id.in_([creator_a.id, creator_b.id]))
        )
        await db_session.commit()


@pytest.mark.integration
async def test_generate_clips_cross_creator_returns_404(db_session: AsyncSession, client):
    """POST /videos/{id}/clips/generate → 404 when A requests B's video."""
    creator_a = await _make_creator(db_session, suffix="a_gc")
    creator_b = await _make_creator(db_session, suffix="b_gc")
    vid_b = await _make_video(db_session, creator_b)
    vid_a = await _make_video(db_session, creator_a)
    await db_session.commit()

    try:
        # Cross-creator: A tries to generate clips on B's video → 404
        resp = client.post(f"/videos/{vid_b.id}/clips/generate", cookies=_cookie(creator_a))
        assert resp.status_code == 404

        # Sanity: A's own video exists and auth passes, though it will 400 (not done/no signals)
        resp = client.post(f"/videos/{vid_a.id}/clips/generate", cookies=_cookie(creator_a))
        assert resp.status_code in (200, 400)  # 400 = not done or no signals, not a 404
    finally:
        await db_session.execute(
            delete(Creator).where(Creator.id.in_([creator_a.id, creator_b.id]))
        )
        await db_session.commit()


@pytest.mark.integration
async def test_list_clips_cross_creator_returns_404(db_session: AsyncSession, client):
    """GET /videos/{id}/clips → 404 when A requests B's video."""
    creator_a = await _make_creator(db_session, suffix="a_lc")
    creator_b = await _make_creator(db_session, suffix="b_lc")
    vid_b = await _make_video(db_session, creator_b)
    vid_a = await _make_video(db_session, creator_a)
    await db_session.commit()

    try:
        resp = client.get(f"/videos/{vid_b.id}/clips", cookies=_cookie(creator_a))
        assert resp.status_code == 404

        resp = client.get(f"/videos/{vid_a.id}/clips", cookies=_cookie(creator_a))
        assert resp.status_code == 200
    finally:
        await db_session.execute(
            delete(Creator).where(Creator.id.in_([creator_a.id, creator_b.id]))
        )
        await db_session.commit()


@pytest.mark.integration
async def test_get_clip_cross_creator_returns_404(db_session: AsyncSession, client):
    """GET /clips/{id} → 404 when A requests B's clip."""
    creator_a = await _make_creator(db_session, suffix="a_getc")
    creator_b = await _make_creator(db_session, suffix="b_getc")
    vid_a = await _make_video(db_session, creator_a)
    vid_b = await _make_video(db_session, creator_b)
    clip_a = await _make_clip(db_session, creator_a, vid_a)
    clip_b = await _make_clip(db_session, creator_b, vid_b)
    await db_session.commit()

    try:
        resp = client.get(f"/clips/{clip_b.id}", cookies=_cookie(creator_a))
        assert resp.status_code == 404

        resp = client.get(f"/clips/{clip_a.id}", cookies=_cookie(creator_a))
        assert resp.status_code == 200
    finally:
        await db_session.execute(
            delete(Creator).where(Creator.id.in_([creator_a.id, creator_b.id]))
        )
        await db_session.commit()


@pytest.mark.integration
async def test_render_clip_cross_creator_returns_404(db_session: AsyncSession, client):
    """POST /clips/{id}/render → 404 when A requests B's clip."""
    creator_a = await _make_creator(db_session, suffix="a_rc")
    creator_b = await _make_creator(db_session, suffix="b_rc")
    vid_a = await _make_video(db_session, creator_a)
    vid_b = await _make_video(db_session, creator_b)
    clip_a = await _make_clip(db_session, creator_a, vid_a)
    clip_b = await _make_clip(db_session, creator_b, vid_b)
    await db_session.commit()

    try:
        resp = client.post(f"/clips/{clip_b.id}/render", cookies=_cookie(creator_a))
        assert resp.status_code == 404

        # Sanity: A's own clip — passes isolation, may 202 or 503/500 (no Celery in test)
        resp = client.post(f"/clips/{clip_a.id}/render", cookies=_cookie(creator_a))
        assert resp.status_code in (202, 500, 503)
    finally:
        await db_session.execute(
            delete(Creator).where(Creator.id.in_([creator_a.id, creator_b.id]))
        )
        await db_session.commit()


@pytest.mark.integration
async def test_feedback_cross_creator_returns_404(db_session: AsyncSession, client):
    """POST /clips/{id}/feedback → 404 when A submits feedback on B's clip."""
    creator_a = await _make_creator(db_session, suffix="a_fb")
    creator_b = await _make_creator(db_session, suffix="b_fb")
    vid_a = await _make_video(db_session, creator_a)
    vid_b = await _make_video(db_session, creator_b)
    clip_a = await _make_clip(db_session, creator_a, vid_a)
    clip_b = await _make_clip(db_session, creator_b, vid_b)
    await db_session.commit()

    payload = {"action": "upvote"}

    try:
        resp = client.post(f"/clips/{clip_b.id}/feedback", json=payload, cookies=_cookie(creator_a))
        assert resp.status_code == 404

        resp = client.post(f"/clips/{clip_a.id}/feedback", json=payload, cookies=_cookie(creator_a))
        assert resp.status_code == 201
    finally:
        await db_session.execute(
            delete(Creator).where(Creator.id.in_([creator_a.id, creator_b.id]))
        )
        await db_session.commit()


@pytest.mark.integration
async def test_get_dna_scoped_to_creator(db_session: AsyncSession, client):
    """GET /creators/me/dna returns only the requesting creator's profile."""
    from models import CreatorDna, DnaStatus

    creator_a = await _make_creator(db_session, suffix="a_dna")
    creator_b = await _make_creator(db_session, suffix="b_dna")
    dna_a = CreatorDna(
        creator_id=creator_a.id,
        version=1,
        brief_text="A's DNA",
        status=DnaStatus.confirmed,
    )
    dna_b = CreatorDna(
        creator_id=creator_b.id,
        version=1,
        brief_text="B's DNA",
        status=DnaStatus.confirmed,
    )
    db_session.add_all([dna_a, dna_b])
    await db_session.commit()

    try:
        resp_a = client.get("/creators/me/dna", cookies=_cookie(creator_a))
        assert resp_a.status_code == 200
        profile_a = resp_a.json().get("profile")
        assert profile_a is not None
        assert profile_a["brief_text"] == "A's DNA"

        resp_b = client.get("/creators/me/dna", cookies=_cookie(creator_b))
        assert resp_b.status_code == 200
        profile_b = resp_b.json().get("profile")
        assert profile_b is not None
        assert profile_b["brief_text"] == "B's DNA"
    finally:
        await db_session.execute(
            delete(Creator).where(Creator.id.in_([creator_a.id, creator_b.id]))
        )
        await db_session.commit()


@pytest.mark.integration
async def test_confirm_dna_scoped_to_creator(db_session: AsyncSession, client):
    """POST /creators/me/dna/confirm only promotes the authenticated creator's draft."""

    from models import CreatorDna, DnaStatus

    creator_a = await _make_creator(db_session, suffix="a_cdna")
    creator_b = await _make_creator(db_session, suffix="b_cdna")
    draft_a = CreatorDna(
        creator_id=creator_a.id,
        version=1,
        brief_text="Draft A",
        status=DnaStatus.draft,
    )
    draft_b = CreatorDna(
        creator_id=creator_b.id,
        version=1,
        brief_text="Draft B",
        status=DnaStatus.draft,
    )
    db_session.add_all([draft_a, draft_b])
    await db_session.commit()

    try:
        # A confirms their own draft
        resp = client.post("/creators/me/dna/confirm", cookies=_cookie(creator_a))
        assert resp.status_code == 200

        # B's draft must remain draft — not touched by A's confirm
        await db_session.refresh(draft_b)
        assert draft_b.status == DnaStatus.draft
    finally:
        await db_session.execute(
            delete(Creator).where(Creator.id.in_([creator_a.id, creator_b.id]))
        )
        await db_session.commit()


@pytest.mark.integration
async def test_upload_intel_scoped_to_creator(db_session: AsyncSession, client):
    """GET /creators/me/upload-intel only returns the requesting creator's data."""
    from datetime import datetime as _dt

    from models import AudienceActivity

    creator_a = await _make_creator(db_session, suffix="a_ui")
    creator_b = await _make_creator(db_session, suffix="b_ui")
    now = _dt.now(UTC)
    # Seed activity only for B
    db_session.add(
        AudienceActivity(
            creator_id=creator_b.id,
            day_of_week=1,
            hour=10,
            activity_index=0.9,
            fetched_at=now,
        )
    )
    await db_session.commit()

    try:
        # A has no activity rows → data_available=False
        resp_a = client.get("/creators/me/upload-intel", cookies=_cookie(creator_a))
        assert resp_a.status_code == 200
        assert resp_a.json()["data_available"] is False

        # B has rows → data_available=True
        resp_b = client.get("/creators/me/upload-intel", cookies=_cookie(creator_b))
        assert resp_b.status_code == 200
        assert resp_b.json()["data_available"] is True
    finally:
        await db_session.execute(
            delete(Creator).where(Creator.id.in_([creator_a.id, creator_b.id]))
        )
        await db_session.commit()


@pytest.mark.integration
async def test_improvement_brief_scoped_to_creator(db_session: AsyncSession, mocker):
    """The brief (now a Celery job — Issue 75) feeds only the requesting creator's metrics."""
    from datetime import datetime as _dt

    from improvement import jobs
    from models import VideoMetrics
    from worker.tasks import _improvement_brief_async

    creator_a = await _make_creator(db_session, suffix="a_ib")
    creator_b = await _make_creator(db_session, suffix="b_ib")
    now = _dt.now(UTC)

    vid_a = await _make_video(db_session, creator_a)
    vid_b = await _make_video(db_session, creator_b)
    db_session.add(
        VideoMetrics(
            video_id=vid_a.id,
            views=1_000,
            watch_time_s=100_000,
            avg_view_duration_s=120.0,
            engagement_rate=0.05,
            fetched_at=now,
        )
    )
    db_session.add(
        VideoMetrics(
            video_id=vid_b.id,
            views=999_999,
            watch_time_s=999_999_00,
            avg_view_duration_s=300.0,
            engagement_rate=0.9,
            fetched_at=now,
        )
    )
    await db_session.commit()

    captured: dict = {}

    def _stub(*, channel_title, analytics, dna_brief):
        captured["channel_title"] = channel_title
        captured["analytics"] = analytics
        return "stubbed brief"

    mocker.patch("improvement.brief.generate_improvement_brief", side_effect=_stub)

    try:
        await _improvement_brief_async(str(creator_a.id))
        assert captured["analytics"]["avg_views"] == pytest.approx(1_000.0)
        assert captured["analytics"]["videos_in_db"] == 1
    finally:
        await jobs.get_redis_client().delete(f"improvement_brief:{creator_a.id}")
        await db_session.execute(
            delete(Creator).where(Creator.id.in_([creator_a.id, creator_b.id]))
        )
        await db_session.commit()


@pytest.mark.integration
async def test_billing_balance_scoped_to_creator(db_session: AsyncSession, client):
    """GET /billing/balance returns the authenticated creator's own balance."""
    creator_a = await _make_creator(db_session, suffix="a_bal")
    creator_b = await _make_creator(db_session, suffix="b_bal")
    # Seed different balances
    creator_a.minutes_balance = 42
    creator_b.minutes_balance = 999
    await db_session.commit()

    try:
        resp_a = client.get("/billing/balance", cookies=_cookie(creator_a))
        assert resp_a.status_code == 200
        assert resp_a.json()["minutes_balance"] == 42

        resp_b = client.get("/billing/balance", cookies=_cookie(creator_b))
        assert resp_b.status_code == 200
        assert resp_b.json()["minutes_balance"] == 999
    finally:
        await db_session.execute(
            delete(Creator).where(Creator.id.in_([creator_a.id, creator_b.id]))
        )
        await db_session.commit()


@pytest.mark.integration
async def test_videos_upload_is_creator_scoped(db_session: AsyncSession, client, mocker):
    """POST /videos/upload creates the video under the authenticated creator only."""
    import io

    from sqlalchemy import select as _sel

    from models import Video as _V

    creator_a = await _make_creator(db_session, suffix="a_up")
    creator_b = await _make_creator(db_session, suffix="b_up")
    await db_session.commit()

    # Patch storage + Celery so the test doesn't need R2 or a worker
    mocker.patch("routers.videos.upload_file", return_value="r2://fake/key.mp4")
    mocker.patch("routers.videos.start_pipeline")

    yt_id = f"yt_{uuid.uuid4().hex[:8]}"
    fake_file = io.BytesIO(b"fake video content")

    try:
        resp = client.post(
            "/videos/upload",
            data={"youtube_video_id": yt_id},
            files={"file": ("video.mp4", fake_file, "video/mp4")},
            cookies=_cookie(creator_a),
        )
        assert resp.status_code == 200
        video_id = resp.json()["video_id"]
        row = await db_session.scalar(_sel(_V).where(_V.id == uuid.UUID(video_id)))
        assert row is not None
        assert row.creator_id == creator_a.id
        assert row.creator_id != creator_b.id
    finally:
        await db_session.execute(
            delete(Creator).where(Creator.id.in_([creator_a.id, creator_b.id]))
        )
        await db_session.commit()
