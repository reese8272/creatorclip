"""Integration tests (real Postgres, mocked storage) for Issues 446 + 471.

Covers, against the real schema and FK graph:
- the archive endpoint end-to-end: media purged per-URI, pointers nulled,
  clips + clip_feedback + posters/peaks PRESERVED, restore round-trip
- every enumerated read path hiding an archived video (list / archived view /
  catalog / clip counts / analytics aggregate / data export / poster + peaks +
  camera-region backfill sweeps)
- permanent erase: full media enumeration incl. identity artifacts, row
  cascade removing clips + feedback
- Issue 471 acceptance: erase_creator's delete set covers EVERY write site's
  key (renders, extracted audio, posters, peaks, recap render, export bundle)

Run with: .venv/bin/python -m pytest tests/test_video_archive_integration.py -m integration -q

Harness rule (same as tests/test_billing_integration.py): do NOT override
get_session — TestClient runs handlers on its own event loop, and sharing the
pytest-loop AsyncSession across loops raises MissingGreenlet/DetachedInstance.
Seeds COMMIT so the app's own per-request sessions see them; verification
re-reads through db_session after rollback + expunge_all.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from auth import SESSION_COOKIE, create_session_token, get_current_creator
from config import settings
from main import app
from models import (
    Clip,
    ClipFeedback,
    ClipFormat,
    Creator,
    DataExport,
    FeedbackAction,
    IngestStatus,
    OnboardingState,
    RenderStatus,
    Summary,
    Video,
    VideoKind,
    VideoMetrics,
    VideoOrigin,
)
from tests._helpers import override_current_creator

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


def _cookie_for(creator: Creator) -> dict:
    return {SESSION_COOKIE: create_session_token(creator.id)}


async def _seed_creator(session: AsyncSession) -> Creator:
    creator = Creator(
        google_sub=f"integration_446_{uuid.uuid4().hex[:12]}",
        channel_id=f"UC_{uuid.uuid4().hex[:8]}",
        channel_title="Archive Test Channel",
        email="archive-test@example.com",
        onboarding_state=OnboardingState.active,
        minutes_balance=60,
    )
    session.add(creator)
    await session.flush()
    return creator


async def _seed_video(
    session: AsyncSession,
    creator: Creator,
    *,
    archived: bool = False,
    origin: VideoOrigin = VideoOrigin.upload,
    with_clip: bool = True,
    with_metrics: bool = False,
) -> tuple[Video, Clip | None]:
    video = Video(
        creator_id=creator.id,
        youtube_video_id=None,
        title="archived" if archived else "active",
        kind=VideoKind.long,
        origin=origin,
        ingest_status=IngestStatus.done,
        duration_s=600.0,
        ingest_done_at=datetime.now(UTC),
        archived_at=datetime.now(UTC) if archived else None,
    )
    session.add(video)
    await session.flush()
    cid, vid = creator.id, video.id
    video.source_uri = f"s3://media/source/{cid}/{uuid.uuid4().hex}.mp4"
    video.audio_uri = f"s3://media/audio/{vid}.wav"
    video.poster_uri = f"s3://media/posters/{cid}/{vid}.jpg"
    video.peaks_uri = f"s3://media/peaks/{cid}/{vid}.json"

    clip = None
    if with_clip:
        clip = Clip(
            video_id=vid,
            creator_id=cid,
            start_s=10.0,
            end_s=70.0,
            format=ClipFormat.short,
            render_status=RenderStatus.done,
        )
        session.add(clip)
        await session.flush()
        clip.render_uri = f"s3://media/clips/{clip.id}.mp4"
        clip.poster_uri = f"s3://media/posters/{cid}/clip-{clip.id}.jpg"
        session.add(ClipFeedback(clip_id=clip.id, creator_id=cid, action=FeedbackAction.upvote))
    if with_metrics:
        session.add(VideoMetrics(video_id=vid, views=100, fetched_at=datetime.now(UTC)))
        video.published_at = datetime.now(UTC)
    await session.commit()
    return video, clip


@asynccontextmanager
async def _fake_local(uri: str, _calls: list | None = None):
    yield Path("/tmp/fake-media.bin")


# ── Archive endpoint end-to-end ───────────────────────────────────────────────


async def test_archive_end_to_end_preserves_training_data_and_identity_artifacts(
    db_session: AsyncSession,
):
    from fastapi.testclient import TestClient

    creator = await _seed_creator(db_session)
    video, clip = await _seed_video(db_session, creator)
    deleted: list[str] = []

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    try:
        with (
            patch("worker.storage.delete_file", side_effect=deleted.append),
            TestClient(app) as client,
        ):
            resp = client.delete(f"/videos/{video.id}", cookies=_cookie_for(creator))
            assert resp.status_code == 200
            assert resp.json()["status"] == "archived"
            # Idempotent repeat.
            resp2 = client.delete(f"/videos/{video.id}", cookies=_cookie_for(creator))
            assert resp2.status_code == 200
            assert resp2.json()["status"] == "noop"
    finally:
        app.dependency_overrides.clear()

    await db_session.rollback()
    db_session.expunge_all()  # drop the identity map so asserts read the DB, not the cache
    row = await db_session.get(Video, video.id)
    assert row is not None and row.archived_at is not None
    # Purged + nulled: source, audio, clip render. Kept: posters, peaks.
    assert row.source_uri is None
    assert row.audio_uri is None
    assert row.poster_uri is not None
    assert row.peaks_uri is not None
    clip_row = await db_session.get(Clip, clip.id)
    assert clip_row is not None, "archive must PRESERVE clip rows"
    assert clip_row.render_uri is None
    assert clip_row.poster_uri is not None
    fb = await db_session.scalar(
        select(func.count()).select_from(ClipFeedback).where(ClipFeedback.clip_id == clip.id)
    )
    assert fb == 1, "archive must PRESERVE clip_feedback (training labels)"
    assert any(u.endswith(f"clips/{clip.id}.mp4") for u in deleted)
    assert not any("posters/" in u for u in deleted)
    assert not any("peaks/" in u for u in deleted)


async def test_restore_round_trip_returns_video_to_the_library(db_session: AsyncSession):
    from fastapi.testclient import TestClient

    creator = await _seed_creator(db_session)
    video, _ = await _seed_video(db_session, creator, archived=True)

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    try:
        with TestClient(app) as client:
            listed = client.get("/videos", cookies=_cookie_for(creator)).json()
            assert listed["videos"] == []
            archived_view = client.get("/videos?archived=true", cookies=_cookie_for(creator)).json()
            assert [v["id"] for v in archived_view["videos"]] == [str(video.id)]

            resp = client.post(f"/videos/{video.id}/restore", cookies=_cookie_for(creator))
            assert resp.status_code == 200
            assert resp.json()["archived_at"] is None
            # Idempotent repeat.
            assert (
                client.post(f"/videos/{video.id}/restore", cookies=_cookie_for(creator)).status_code
                == 200
            )
            listed = client.get("/videos", cookies=_cookie_for(creator)).json()
            assert [v["id"] for v in listed["videos"]] == [str(video.id)]
    finally:
        app.dependency_overrides.clear()


# ── Read paths: one test per path proving the archived video disappears ───────


async def test_archived_video_disappears_from_list_and_counts_and_analytics(
    db_session: AsyncSession,
):
    from fastapi.testclient import TestClient

    creator = await _seed_creator(db_session)
    active, active_clip = await _seed_video(db_session, creator, with_metrics=True)
    archived, archived_clip = await _seed_video(
        db_session, creator, archived=True, with_metrics=True
    )

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    try:
        with TestClient(app) as client:
            cookies = _cookie_for(creator)
            # GET /videos — default view hides the archived row.
            ids = [v["id"] for v in client.get("/videos", cookies=cookies).json()["videos"]]
            assert ids == [str(active.id)]
            # GET /videos/clips/counts — the archived video's clips leave the counts.
            counts = client.get("/videos/clips/counts", cookies=cookies).json()["counts"]
            assert [c["video_id"] for c in counts] == [str(active.id)]
            # Dashboard/Profile analytics aggregate — archived metrics excluded.
            analytics = client.get(
                "/creators/me/insights/analytics?period=all", cookies=cookies
            ).json()
            assert analytics["videos_in_period"] == 1
            assert analytics["total_views"] == 100
    finally:
        app.dependency_overrides.clear()


async def test_archived_catalog_row_disappears_from_channel_browser(db_session: AsyncSession):
    from fastapi.testclient import TestClient

    creator = await _seed_creator(db_session)
    active, _ = await _seed_video(db_session, creator, origin=VideoOrigin.catalog, with_clip=False)
    await _seed_video(
        db_session, creator, origin=VideoOrigin.catalog, archived=True, with_clip=False
    )

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    try:
        with TestClient(app) as client:
            body = client.get("/videos/catalog", cookies=_cookie_for(creator)).json()
            assert [v["id"] for v in body["videos"]] == [str(active.id)]
            assert body["total"] == 1
    finally:
        app.dependency_overrides.clear()


async def test_data_export_excludes_archived_video_but_keeps_retained_feedback(
    db_session: AsyncSession,
):
    from worker.tasks import _collect_creator_export

    creator = await _seed_creator(db_session)
    active, _ = await _seed_video(db_session, creator)
    archived, archived_clip = await _seed_video(db_session, creator, archived=True)

    export = await _collect_creator_export(db_session, creator)

    exported_video_ids = {str(v["id"]) for v in export["videos"]}
    assert str(active.id) in exported_video_ids
    assert str(archived.id) not in exported_video_ids
    # The clips + feedback retained past archive ARE creator data we still hold,
    # so Art. 15 honesty keeps them in the export.
    exported_clip_ids = {str(c["id"]) for c in export["clips"]}
    assert str(archived_clip.id) in exported_clip_ids


async def test_poster_backfill_skips_archived_videos(db_session: AsyncSession):
    from worker.tasks import _backfill_video_posters_async

    creator = await _seed_creator(db_session)
    active, _ = await _seed_video(db_session, creator, with_clip=False)
    archived, _ = await _seed_video(db_session, creator, archived=True, with_clip=False)
    # Both rows are backfill candidates apart from the archive marker.
    active.poster_uri = None
    archived.poster_uri = None
    await db_session.commit()

    processed: list[str] = []

    async def _fake_poster(src, key, duration_s=None):
        processed.append(key)
        return f"s3://media/{key}"

    with (
        patch("worker.storage.alocal_path", _fake_local),
        patch("worker.tasks._extract_and_upload_poster", side_effect=_fake_poster),
    ):
        await _backfill_video_posters_async()

    assert any(str(active.id) in key for key in processed)
    assert not any(str(archived.id) in key for key in processed), (
        "poster backfill must not touch archived videos"
    )


async def test_peaks_backfill_skips_archived_videos(db_session: AsyncSession):
    from worker.tasks import _backfill_video_peaks_async

    creator = await _seed_creator(db_session)
    active, _ = await _seed_video(db_session, creator, with_clip=False)
    archived, _ = await _seed_video(db_session, creator, archived=True, with_clip=False)
    active.peaks_uri = None
    archived.peaks_uri = None
    await db_session.commit()

    processed: list[str] = []

    async def _fake_peaks(wav, key):
        processed.append(key)
        return f"s3://media/{key}"

    with (
        patch("worker.storage.alocal_path", _fake_local),
        patch("worker.tasks._compute_and_upload_peaks", side_effect=_fake_peaks),
    ):
        await _backfill_video_peaks_async()

    assert any(str(active.id) in key for key in processed)
    assert not any(str(archived.id) in key for key in processed), (
        "peaks backfill must not touch archived videos"
    )


async def test_camera_region_backfill_skips_archived_videos(db_session: AsyncSession, monkeypatch):
    from worker.tasks import _backfill_video_camera_regions_async

    creator = await _seed_creator(db_session)
    active, _ = await _seed_video(db_session, creator, with_clip=False)
    archived, _ = await _seed_video(db_session, creator, archived=True, with_clip=False)
    await db_session.commit()

    monkeypatch.setattr(settings, "CAMERA_REGION_DETECT_ENABLED", True)
    monkeypatch.setattr(settings, "OVERLAY_BAND_DETECT_ENABLED", False)

    touched: list[str] = []

    @asynccontextmanager
    async def _recording_local(uri: str):
        touched.append(uri)
        yield Path("/tmp/fake-media.bin")

    with (
        patch("worker.storage.alocal_path", _recording_local),
        patch("clip_engine.camera_region.detect_video_camera_region", return_value=None),
        patch("clip_engine.render.frame_dimensions", return_value=(1920, 1080)),
    ):
        await _backfill_video_camera_regions_async()

    assert not any(
        str(archived.id) in (uri or "") or uri == archived.source_uri for uri in touched
    ), "camera-region backfill must not download archived videos"
    assert any(uri == active.source_uri for uri in touched)


# ── Permanent erase ───────────────────────────────────────────────────────────


async def test_erase_video_removes_rows_and_all_media(db_session: AsyncSession):
    from fastapi.testclient import TestClient

    creator = await _seed_creator(db_session)
    video, clip = await _seed_video(db_session, creator)
    summary = Summary(
        creator_id=creator.id,
        video_id=video.id,
        target_duration_s=60,
        render_status=RenderStatus.done,
    )
    db_session.add(summary)
    await db_session.flush()
    summary.render_uri = f"s3://media/summaries/{summary.id}.mp4"
    await db_session.commit()

    deleted: list[str] = []
    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    try:
        with (
            patch("worker.storage.delete_file", side_effect=deleted.append),
            TestClient(app) as client,
        ):
            resp = client.post(f"/videos/{video.id}/erase", cookies=_cookie_for(creator))
            assert resp.status_code == 200
    finally:
        app.dependency_overrides.clear()

    await db_session.rollback()
    db_session.expunge_all()  # drop the identity map so asserts read the DB, not the cache
    assert await db_session.get(Video, video.id) is None
    assert await db_session.get(Clip, clip.id) is None
    fb = await db_session.scalar(
        select(func.count()).select_from(ClipFeedback).where(ClipFeedback.clip_id == clip.id)
    )
    assert fb == 0, "permanent erase removes feedback (and the UI must say so)"
    assert await db_session.get(Summary, summary.id) is None
    # Full enumeration this time — identity artifacts included.
    for key in (
        f"posters/{creator.id}/{video.id}.jpg",
        f"peaks/{creator.id}/{video.id}.json",
        f"posters/{creator.id}/clip-{clip.id}.jpg",
        f"clips/{clip.id}.mp4",
        f"summaries/{summary.id}.mp4",
        f"audio/{video.id}.wav",
    ):
        assert any(u.endswith(key) for u in deleted), f"erase missed {key}"


# ── Issue 471 acceptance: erasure covers every write site ─────────────────────


async def test_erase_creator_delete_set_covers_every_write_site(db_session: AsyncSession):
    """Renders, extracted audio, recap artifacts, and GDPR export bundles must
    ALL reach the delete set — the keys the old prefix sweep could never match."""
    from fastapi.testclient import TestClient

    creator = await _seed_creator(db_session)
    video, clip = await _seed_video(db_session, creator)
    # An ARCHIVED video's leftovers must be erased too.
    archived, archived_clip = await _seed_video(db_session, creator, archived=True)
    summary = Summary(
        creator_id=creator.id,
        video_id=video.id,
        target_duration_s=60,
        render_status=RenderStatus.done,
    )
    db_session.add(summary)
    export = DataExport(creator_id=creator.id)
    db_session.add(export)
    await db_session.flush()
    summary.render_uri = f"s3://media/summaries/{summary.id}.mp4"
    export.export_uri = f"s3://media/exports/{creator.id}/{uuid.uuid4().hex}.json"
    export_uri = export.export_uri
    await db_session.commit()

    deleted: list[str] = []
    prefixes: list[str] = []
    # No get_current_creator override here: erase_creator DELETES the Creator
    # row, and injecting the pytest-session-attached instance makes the request
    # session raise "already attached to session '1'". The signed cookie drives
    # the REAL auth dependency, which loads its own instance per request.
    try:
        with (
            patch("worker.storage.delete_file", side_effect=deleted.append),
            patch("worker.storage.delete_prefix", side_effect=lambda p: prefixes.append(p) or 0),
            patch("routers.auth.httpx.AsyncClient"),
            TestClient(app) as client,
        ):
            resp = client.delete("/auth/me", cookies=_cookie_for(creator))
            assert resp.status_code == 204, resp.text
    finally:
        app.dependency_overrides.clear()

    expected_keys = [
        f"audio/{video.id}.wav",  # extracted audio (Issue 471)
        f"audio/{archived.id}.wav",
        f"posters/{creator.id}/{video.id}.jpg",
        f"peaks/{creator.id}/{video.id}.json",
        f"clips/{clip.id}.mp4",  # render — the Issue 446 gap
        f"clips/{clip.id}_clean.mp4",
        f"clips/{clip.id}_edit.mp4",
        f"clips/{archived_clip.id}.mp4",
        f"posters/{creator.id}/clip-{clip.id}.jpg",
        f"summaries/{summary.id}.mp4",  # recap artifact (Issue 471)
    ]
    for key in expected_keys:
        assert any(u.endswith(key) for u in deleted), f"erasure missed write-site key {key}"
    assert export_uri in deleted, "GDPR export bundle not erased (Issue 471)"
    assert f"exports/{creator.id}/" in prefixes, "exports prefix sweep missing"
