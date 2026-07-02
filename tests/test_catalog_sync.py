"""
Issue 87 — catalog sync wiring + Shorts-threshold + link/upload kind resolution.

The bugs being pinned:
  1. sync_video_catalog had zero callers, so a freshly connected creator's
     videos table stayed empty forever and the data-gate reported 0/0.
  2. classify_video_kind used <=60s for Shorts; YouTube raised the official
     max to 180s in Oct 2024 — any 61-180s vertical was mis-bucketed.
  3. /videos/link hardcoded kind=long, ignoring duration entirely.
  4. /videos/upload hardcoded kind=long, ignoring the probed duration.

All four are now tested. No DB; YouTube API + probe are mocked at the
single mockable boundary in each module.
"""

from __future__ import annotations

import io
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from auth import get_current_creator
from db import get_session
from main import app
from models import VideoKind
from tests._helpers import override_current_creator
from youtube.data_api import classify_video_kind

# ── 180s Shorts boundary (load-bearing classification contract) ───────────────


def test_shorts_threshold_at_61_seconds():
    """The exact bug from Issue 87: a 61-second vertical is a Short under the
    2024 YouTube spec. The old <=60 rule would have called it long-form."""
    assert classify_video_kind(61.0) == VideoKind.short


def test_shorts_threshold_at_179_seconds():
    """Anything strictly under 180s remains a Short."""
    assert classify_video_kind(179.0) == VideoKind.short


def test_long_form_at_181_seconds():
    """The first second above 180s flips to long-form."""
    assert classify_video_kind(181.0) == VideoKind.long


# ── sync_channel_catalog Celery task (wrapper contract) ───────────────────────


@pytest.mark.asyncio
async def test_sync_channel_catalog_calls_sync_video_catalog_and_commits():
    """The new task must (a) resolve a token, (b) call sync_video_catalog,
    (c) commit. Without the commit the new Video rows never leave the
    transaction — which is exactly the symptom the user reported."""
    from worker.tasks import _sync_channel_catalog_async

    creator_id = uuid.uuid4()
    fake_creator = MagicMock(id=creator_id)
    fake_session = MagicMock()
    fake_session.get = AsyncMock(return_value=fake_creator)
    fake_session.commit = AsyncMock()
    # Issue 352 Batch F: the finally now rolls back before the advisory unlock.
    fake_session.rollback = AsyncMock()
    # Issue 88: phase 2 queries for unmetered videos. Empty result = no metrics fetched.
    empty_phase2 = MagicMock()
    empty_phase2.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    # Issue 105: the first execute() call is the advisory-lock probe, which must
    # return scalar_one() == True so the body proceeds. Subsequent calls (phase 2
    # metrics query, advisory unlock) use empty_phase2 / a no-op result.
    advisory_lock_result = MagicMock()
    advisory_lock_result.scalar_one = MagicMock(return_value=True)
    # Issue 120: Phase 2 now runs two queries (longs + shorts) instead of one.
    fake_session.execute = AsyncMock(
        side_effect=[advisory_lock_result, empty_phase2, empty_phase2, MagicMock()]
    )

    # AdminSessionLocal() returns an async context manager
    fake_ctx = MagicMock()
    fake_ctx.__aenter__ = AsyncMock(return_value=fake_session)
    fake_ctx.__aexit__ = AsyncMock(return_value=None)

    sync_catalog_mock = AsyncMock()
    with (
        patch("worker.tasks.db.AdminSessionLocal", return_value=fake_ctx),
        patch(
            "youtube.oauth.get_valid_access_token",
            new=AsyncMock(return_value="tok"),
        ),
        patch("youtube.analytics.sync_video_catalog", new=sync_catalog_mock),
    ):
        await _sync_channel_catalog_async(str(creator_id))

    sync_catalog_mock.assert_awaited_once()
    # Two commits: one after phase 1 (catalog upsert), one after phase 2 (metrics chain).
    assert fake_session.commit.await_count == 2


@pytest.mark.asyncio
async def test_sync_channel_catalog_no_token_is_a_clean_no_op():
    """A creator whose YoutubeToken row was deleted (revoked grant) must
    not crash the task — the next refresh tick will handle it."""
    from worker.tasks import _sync_channel_catalog_async

    creator_id = uuid.uuid4()
    fake_creator = MagicMock(id=creator_id)
    fake_session = MagicMock()
    fake_session.get = AsyncMock(return_value=fake_creator)
    fake_session.commit = AsyncMock()
    # Issue 352 Batch F: the finally now rolls back before the advisory unlock.
    fake_session.rollback = AsyncMock()
    # Issue 105: advisory-lock probe is the first execute call — must return True
    # so the body proceeds (then the token lookup raises, which is what we test).
    advisory_lock_result = MagicMock()
    advisory_lock_result.scalar_one = MagicMock(return_value=True)
    unlock_result = MagicMock()
    fake_session.execute = AsyncMock(side_effect=[advisory_lock_result, unlock_result])

    fake_ctx = MagicMock()
    fake_ctx.__aenter__ = AsyncMock(return_value=fake_session)
    fake_ctx.__aexit__ = AsyncMock(return_value=None)

    sync_catalog_mock = AsyncMock()
    with (
        patch("worker.tasks.db.AdminSessionLocal", return_value=fake_ctx),
        patch(
            "youtube.oauth.get_valid_access_token",
            new=AsyncMock(side_effect=RuntimeError("no token")),
        ),
        patch("youtube.analytics.sync_video_catalog", new=sync_catalog_mock),
    ):
        # Must NOT raise.
        await _sync_channel_catalog_async(str(creator_id))

    sync_catalog_mock.assert_not_awaited()


# ── Issue 260: interactive sync stays OFF the per-creator refresh sub-budget ───
#
# The per-creator/day refresh sub-budget exists so the Beat fan-out cannot drain
# the shared interactive pool. It must NOT be charged on the user-triggered
# onboarding sync, or a large channel's first sync could exhaust its own
# 300-unit/day allowance mid-onboarding. The load-bearing boundary is the
# creator_id threaded into each YouTube fetch: creator.id charges the sub-budget,
# None falls back to the global cap only (see youtube/quota.py::consume).


@pytest.mark.asyncio
async def test_sync_video_catalog_interactive_does_not_charge_sub_budget():
    """charge_sub_budget=False ⇒ inner fetchers receive creator_id=None, so the
    interactive first-sync is bounded only by the global daily cap (Issue 260)."""
    from youtube import analytics

    creator = MagicMock(id=uuid.uuid4())
    fake_session = MagicMock()
    fake_session.execute = AsyncMock(return_value=MagicMock(__iter__=lambda self: iter([])))

    list_videos = AsyncMock(return_value=[{"video_id": "v1", "title": "t"}])
    get_meta = AsyncMock(return_value=[])
    with (
        patch.object(analytics, "list_channel_videos", new=list_videos),
        patch.object(analytics, "get_videos_metadata", new=get_meta),
    ):
        await analytics.sync_video_catalog(fake_session, creator, "tok", charge_sub_budget=False)

    assert list_videos.await_args.kwargs["creator_id"] is None
    assert get_meta.await_args.kwargs["creator_id"] is None


@pytest.mark.asyncio
async def test_sync_video_catalog_beat_default_charges_sub_budget():
    """Default (Beat fan-out) ⇒ inner fetchers receive creator.id, charging the
    per-creator sub-budget so one channel cannot drain the shared pool."""
    from youtube import analytics

    creator = MagicMock(id=uuid.uuid4())
    fake_session = MagicMock()
    fake_session.execute = AsyncMock(return_value=MagicMock(__iter__=lambda self: iter([])))

    list_videos = AsyncMock(return_value=[{"video_id": "v1", "title": "t"}])
    get_meta = AsyncMock(return_value=[])
    with (
        patch.object(analytics, "list_channel_videos", new=list_videos),
        patch.object(analytics, "get_videos_metadata", new=get_meta),
    ):
        await analytics.sync_video_catalog(fake_session, creator, "tok")

    assert list_videos.await_args.kwargs["creator_id"] == creator.id
    assert get_meta.await_args.kwargs["creator_id"] == creator.id


@pytest.mark.asyncio
async def test_sync_video_analytics_interactive_does_not_charge_sub_budget():
    """charge_sub_budget=False on per-video metrics ⇒ creator_id=None to the
    metrics + retention fetchers (global cap only). (Issue 260)"""
    from youtube import analytics

    creator = MagicMock(id=uuid.uuid4(), channel_id="UC123")
    video = MagicMock(id=uuid.uuid4(), youtube_video_id="v1", duration_s=120.0)
    fake_session = MagicMock()
    fake_session.get = AsyncMock(return_value=None)
    fake_session.execute = AsyncMock(return_value=MagicMock())

    metrics = AsyncMock(return_value=None)
    retention = AsyncMock(return_value=[])
    with (
        patch.object(analytics, "fetch_video_metrics", new=metrics),
        patch.object(analytics, "fetch_retention_curve", new=retention),
    ):
        await analytics.sync_video_analytics(
            fake_session, video, creator, "tok", charge_sub_budget=False
        )

    assert metrics.await_args.kwargs["creator_id"] is None
    assert retention.await_args.kwargs["creator_id"] is None


# ── /videos/link resolves kind from YouTube metadata ──────────────────────────


def _override_auth_and_session():
    creator = MagicMock(id=uuid.uuid4())
    fake_session = AsyncMock()
    fake_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
    fake_session.add = MagicMock()
    fake_session.commit = AsyncMock()

    async def refresh(obj):
        # Mimic SQLAlchemy refresh: the new row already has a UUID + status set.
        obj.id = obj.id or uuid.uuid4()

    fake_session.refresh = AsyncMock(side_effect=refresh)

    async def _gen():
        yield fake_session

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _gen
    return creator, fake_session


def _cleanup_overrides():
    app.dependency_overrides.pop(get_current_creator, None)
    app.dependency_overrides.pop(get_session, None)


def test_link_video_resolves_short_via_youtube_metadata():
    """A linked 30s video must enter the DB as kind=short (was hardcoded
    long in the pre-Issue-87 code path)."""
    creator, fake_session = _override_auth_and_session()
    try:
        with (
            patch(
                "routers.videos.get_valid_access_token",
                new=AsyncMock(return_value="tok"),
            ),
            patch(
                "routers.videos.get_videos_metadata",
                new=AsyncMock(
                    return_value=[
                        {
                            "video_id": "abcdefghijk",
                            "duration_s": 30.0,
                            "kind": VideoKind.short,
                        }
                    ]
                ),
            ),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/videos/link", data={"youtube_video_id": "abcdefghijk"})
        assert resp.status_code == 200
        added = fake_session.add.call_args[0][0]
        assert added.kind == VideoKind.short
        assert added.duration_s == 30.0
    finally:
        _cleanup_overrides()


def test_link_video_falls_back_to_long_when_youtube_call_fails():
    """If YT API is down at link time, register the video as long-form (the
    safe default) — the catalog sync will repair the row later."""
    creator, fake_session = _override_auth_and_session()
    try:
        with (
            patch(
                "routers.videos.get_valid_access_token",
                new=AsyncMock(side_effect=RuntimeError("network down")),
            ),
            patch("routers.videos.get_videos_metadata", new=AsyncMock()) as gvm,
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/videos/link", data={"youtube_video_id": "abcdefghijk"})
        assert resp.status_code == 200
        added = fake_session.add.call_args[0][0]
        assert added.kind == VideoKind.long
        assert added.duration_s is None
        gvm.assert_not_awaited()
    finally:
        _cleanup_overrides()


# ── /videos/upload resolves kind from local probe ─────────────────────────────


def test_upload_video_resolves_short_from_probe(monkeypatch):
    """A 45s upload must enter the DB as kind=short — that's the whole
    reason we probe duration before persisting the row."""
    monkeypatch.setattr("config.settings.UPLOAD_MAX_MB", 2)
    creator, fake_session = _override_auth_and_session()
    try:
        with (
            patch("routers.videos.probe_duration_s", return_value=45.0),
            patch("routers.videos.upload_file", return_value="local://x"),
            patch("routers.videos.check_positive_balance", new=AsyncMock()),
            # Issue 89: post-probe pre-check uses video_minutes(duration_s).
            # This test isn't about billing — just the kind-resolution shape.
            patch("routers.videos.check_balance_for_minutes", new=AsyncMock()),
            patch("routers.videos.start_pipeline"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/videos/upload",
                data={"youtube_video_id": "abcdefghijk"},
                files={"file": ("v.mp4", io.BytesIO(b"x" * 1024), "video/mp4")},
            )
        assert resp.status_code == 200
        added = fake_session.add.call_args[0][0]
        assert added.kind == VideoKind.short
        assert added.duration_s == 45.0
    finally:
        _cleanup_overrides()
