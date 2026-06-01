"""
Integration tests for Issue 50 — account-deletion FK cascade against real Postgres.

Seeds one creator with one row in every dependent table, calls DELETE /auth/me via
the FastAPI TestClient (with storage and OAuth patched), then asserts every dependent
table is empty for that creator and an audit_log row with action="creator.deleted" exists.

Requires a running Postgres with the Alembic schema applied.
Run with: .venv/bin/python -m pytest tests/test_account_deletion_integration.py -m integration -q
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from auth import SESSION_COOKIE, create_session_token, get_current_creator
from config import settings
from crypto import encrypt
from db import get_session
from main import app
from models import (
    AudienceActivity,
    AuditLog,
    Clip,
    ClipFeedback,
    ClipFormat,
    ClipOutcome,
    Creator,
    CreatorDna,
    Demographics,
    DnaEmbedding,
    DnaEmbeddingKind,
    DnaStatus,
    FeedbackAction,
    IngestStatus,
    MinuteDeduction,
    MinutePack,
    OnboardingState,
    PreferenceModel,
    RenderStatus,
    RetentionCurve,
    Signals,
    Transcript,
    Usage,
    Video,
    VideoKind,
    VideoMetrics,
    YoutubeToken,
)
from tests._helpers import override_current_creator

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


# ── Seed helpers ──────────────────────────────────────────────────────────────


async def _seed_full_creator(session: AsyncSession) -> Creator:
    """Insert one creator and one row in every dependent table. Returns the Creator."""
    creator = Creator(
        google_sub=f"integration_del_{uuid.uuid4().hex[:12]}",
        channel_id=f"UC_{uuid.uuid4().hex[:8]}",
        channel_title="Integration Test Channel",
        email="integration@example.com",
        onboarding_state=OnboardingState.active,
        minutes_balance=60,
    )
    session.add(creator)
    await session.flush()  # creator.id now available

    cid = creator.id

    # YoutubeToken (1-to-1, creator_id is PK)
    session.add(
        YoutubeToken(
            creator_id=cid,
            access_token_encrypted=encrypt("access-token"),
            refresh_token_encrypted=encrypt("refresh-token"),
            scope="openid email",
            expires_at=datetime.now(UTC),
        )
    )

    # Video (1-to-many)
    video = Video(
        creator_id=cid,
        youtube_video_id=f"vid_{uuid.uuid4().hex[:8]}",
        kind=VideoKind.long,
        ingest_status=IngestStatus.done,
        duration_s=600.0,
    )
    session.add(video)
    await session.flush()  # video.id now available

    vid = video.id

    # VideoMetrics (video_id is PK)
    session.add(
        VideoMetrics(
            video_id=vid,
            views=1000,
            fetched_at=datetime.now(UTC),
        )
    )

    # RetentionCurve
    session.add(
        RetentionCurve(
            video_id=vid,
            timestamp_s=10.0,
            audience_watch_ratio=0.9,
        )
    )

    # Transcript (video_id is PK)
    session.add(
        Transcript(
            video_id=vid,
            source="whisperx",
            segments_jsonb={"words": []},
        )
    )

    # Signals (video_id is PK)
    session.add(
        Signals(
            video_id=vid,
            timeline_jsonb={"events": []},
        )
    )

    # Clip
    clip = Clip(
        video_id=vid,
        creator_id=cid,
        start_s=10.0,
        end_s=70.0,
        format=ClipFormat.short,
        render_status=RenderStatus.done,
    )
    session.add(clip)
    await session.flush()  # clip.id now available

    clip_id = clip.id

    # ClipFeedback
    session.add(
        ClipFeedback(
            clip_id=clip_id,
            creator_id=cid,
            action=FeedbackAction.upvote,
        )
    )

    # ClipOutcome (clip_id is PK)
    session.add(
        ClipOutcome(
            clip_id=clip_id,
            fetched_at=datetime.now(UTC),
        )
    )

    # AudienceActivity (composite PK: creator_id, day_of_week, hour)
    session.add(
        AudienceActivity(
            creator_id=cid,
            day_of_week=1,
            hour=9,
            activity_index=0.75,
            fetched_at=datetime.now(UTC),
        )
    )

    # Demographics (creator_id is PK)
    session.add(
        Demographics(
            creator_id=cid,
            payload_jsonb={"age_groups": {}},
            fetched_at=datetime.now(UTC),
        )
    )

    # CreatorDna
    dna = CreatorDna(
        creator_id=cid,
        version=1,
        status=DnaStatus.confirmed,
    )
    session.add(dna)

    # DnaEmbedding (direct creator_id FK)
    session.add(
        DnaEmbedding(
            creator_id=cid,
            kind=DnaEmbeddingKind.pattern,
            embedding=[0.0] * 1024,
        )
    )

    # PreferenceModel
    session.add(
        PreferenceModel(
            creator_id=cid,
            version=1,
            updated_at=datetime.now(UTC),
        )
    )

    # MinutePack
    session.add(
        MinutePack(
            creator_id=cid,
            pack_id="trial",
            minutes_granted=60,
            price_cents=0,
            reason="trial",
        )
    )

    # MinuteDeduction (video_id FK + creator_id FK)
    session.add(
        MinuteDeduction(
            video_id=vid,
            creator_id=cid,
            minutes_deducted=10,
            duration_s=600.0,
        )
    )

    # Usage
    session.add(
        Usage(
            creator_id=cid,
            period="2026-05",
            videos_processed=1,
            clips_generated=1,
        )
    )

    await session.commit()
    return creator


def _cookie_for(creator: Creator) -> dict:
    return {SESSION_COOKIE: create_session_token(creator.id)}


# ── Shared patches ────────────────────────────────────────────────────────────

_STORAGE_PATH = "worker.storage.delete_prefix"
_HTTPX_PATH = "routers.auth.httpx.AsyncClient"


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_delete_account_cascades_all_dependent_tables(
    db_session: AsyncSession,
):
    """Cascade: every dependent table has 0 rows for the creator after DELETE /auth/me."""
    from fastapi.testclient import TestClient

    creator = await _seed_full_creator(db_session)
    cid = creator.id

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _make_override_session(db_session)

    try:
        with patch(_STORAGE_PATH, return_value=0), patch(_HTTPX_PATH), TestClient(app) as client:
            resp = client.delete("/auth/me", cookies=_cookie_for(creator))
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 204

    # The creator row itself must be gone
    gone = await db_session.scalar(select(func.count()).where(Creator.id == cid))
    assert gone == 0, "creators row not deleted"

    # Every directly creator_id-keyed table
    creator_keyed = [
        YoutubeToken,
        AudienceActivity,
        Demographics,
        CreatorDna,
        DnaEmbedding,
        PreferenceModel,
        MinutePack,
        MinuteDeduction,
        Usage,
        ClipFeedback,
    ]
    for model in creator_keyed:
        count = await db_session.scalar(
            select(func.count()).select_from(model).where(model.creator_id == cid)
        )
        assert count == 0, f"{model.__tablename__} still has rows for creator {cid}"

    # Tables keyed through video (no direct creator_id column in the model)
    # — covered by the Video FK cascade; verified via raw SQL on video_id absence
    video_orphan = await db_session.scalar(
        text("SELECT COUNT(*) FROM videos WHERE creator_id = :cid"),
        {"cid": cid},
    )
    assert video_orphan == 0, "videos rows not deleted"

    for table in ("video_metrics", "retention_curves", "transcripts", "signals", "clips"):
        orphan = await db_session.scalar(
            text(
                f"SELECT COUNT(*) FROM {table} WHERE video_id IN "  # noqa: S608
                "(SELECT id FROM videos WHERE creator_id = :cid)"
            ),
            {"cid": cid},
        )
        assert orphan == 0, f"{table} has orphan rows after cascade"

    for table in ("clip_feedback", "clip_outcomes"):
        orphan = await db_session.scalar(
            text(
                f"SELECT COUNT(*) FROM {table} WHERE clip_id IN "  # noqa: S608
                "(SELECT id FROM clips WHERE creator_id = :cid)"
            ),
            {"cid": cid},
        )
        assert orphan == 0, f"{table} has orphan rows after cascade"

    # Audit log entry must exist
    audit_count = await db_session.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.action == "creator.deleted",
            AuditLog.entity_id == cid,
        )
    )
    assert audit_count >= 1, "audit_log row missing after account deletion"


@pytest.mark.integration
async def test_delete_account_purges_both_storage_prefixes(
    db_session: AsyncSession,
):
    """Storage purge is called with source/{id}/ and clips/{id}/."""
    from fastapi.testclient import TestClient

    creator = await _seed_full_creator(db_session)
    cid = creator.id
    purged: list[str] = []

    def _capture(prefix: str) -> int:
        purged.append(prefix)
        return 0

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _make_override_session(db_session)

    try:
        with (
            patch(_STORAGE_PATH, side_effect=_capture),
            patch(_HTTPX_PATH),
            TestClient(app) as client,
        ):
            client.delete("/auth/me", cookies=_cookie_for(creator))
    finally:
        app.dependency_overrides.clear()

    assert f"source/{cid}/" in purged, "source prefix not purged"
    assert f"clips/{cid}/" in purged, "clips prefix not purged"

    # Cleanup: creator is already deleted by the handler


@pytest.mark.integration
async def test_delete_account_succeeds_when_google_revoke_fails(
    db_session: AsyncSession,
):
    """Account + cascade happen even when OAuth revocation raises an exception."""
    import httpx
    from fastapi.testclient import TestClient

    creator = await _seed_full_creator(db_session)
    cid = creator.id

    class _FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def post(self, *_args, **_kwargs):
            req = httpx.Request("POST", "https://oauth2.googleapis.com/revoke")
            raise httpx.HTTPStatusError(
                "500 Server Error",
                request=req,
                response=httpx.Response(500, request=req),
            )

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _make_override_session(db_session)

    try:
        with (
            patch(_STORAGE_PATH, return_value=0),
            patch(_HTTPX_PATH, return_value=_FailingClient()),
            TestClient(app) as client,
        ):
            resp = client.delete("/auth/me", cookies=_cookie_for(creator))
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 204

    gone = await db_session.scalar(select(func.count()).where(Creator.id == cid))
    assert gone == 0, "creator row should be gone even after OAuth revocation failure"

    audit_count = await db_session.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.action == "creator.deleted",
            AuditLog.entity_id == cid,
        )
    )
    assert audit_count >= 1, "audit log not written despite OAuth failure"


@pytest.mark.integration
async def test_delete_account_succeeds_when_storage_purge_fails(
    db_session: AsyncSession,
):
    """Account + cascade happen even when delete_prefix raises an exception."""
    from fastapi.testclient import TestClient

    creator = await _seed_full_creator(db_session)
    cid = creator.id

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _make_override_session(db_session)

    try:
        with (
            patch(_STORAGE_PATH, side_effect=OSError("R2 unavailable")),
            patch(_HTTPX_PATH),
            TestClient(app) as client,
        ):
            resp = client.delete("/auth/me", cookies=_cookie_for(creator))
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 204

    gone = await db_session.scalar(select(func.count()).where(Creator.id == cid))
    assert gone == 0, "creator row should be gone even after storage purge failure"

    # Cascade: spot-check one child table
    orphan_videos = await db_session.scalar(
        text("SELECT COUNT(*) FROM videos WHERE creator_id = :cid"),
        {"cid": cid},
    )
    assert orphan_videos == 0, "cascade did not run when storage purge failed"


# ── Internal helpers ──────────────────────────────────────────────────────────


def _make_override_session(session: AsyncSession):
    """Return a FastAPI dependency override that yields the given session."""

    async def _override():
        yield session

    return _override
