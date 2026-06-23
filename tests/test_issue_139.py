"""Issue 139 — linked videos are visible, with honest clip provenance.

The SEV1: ``link_video`` created a Video with no ``source_uri``, and
``list_videos`` filtered ``source_uri IS NOT NULL``, so every linked video
silently vanished from the dashboard. Fix: a ``VideoOrigin`` discriminator
(``catalog | link | upload``). Catalog rows stay hidden; linked rows show with
``clippable=false`` — we never download from YouTube (their ToS), so clipping a
linked video requires the creator to upload the source file (e.g. via Google
Takeout). The doomed "queue for analysis" path on source-less rows is rejected.
"""

from __future__ import annotations

import pathlib
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from auth import get_current_creator
from db import get_session
from main import app
from models import IngestStatus, OnboardingState, Video, VideoKind, VideoOrigin
from tests._helpers import override_current_creator

_REPO = pathlib.Path(__file__).resolve().parent.parent


def _creator():
    c = MagicMock()
    c.id = uuid.uuid4()
    c.onboarding_state = OnboardingState.active
    return c


# ── provenance is stamped on creation ────────────────────────────────────────


def test_link_video_sets_origin_link():
    creator = _creator()
    fake_session = AsyncMock()
    fake_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
    fake_session.add = MagicMock()
    fake_session.commit = AsyncMock()
    fake_session.refresh = AsyncMock()

    async def _sess():
        yield fake_session

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _sess
    try:
        with (
            patch("routers.videos.get_valid_access_token", new=AsyncMock(return_value="tok")),
            patch(
                "routers.videos.get_videos_metadata",
                new=AsyncMock(
                    return_value=[
                        {"video_id": "abcdefghijk", "duration_s": 30.0, "kind": VideoKind.short}
                    ]
                ),
            ),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/videos/link", data={"youtube_video_id": "abcdefghijk"})
        assert resp.status_code == 200, resp.text
        added = fake_session.add.call_args[0][0]
        assert added.origin == VideoOrigin.link
        # Never downloaded — a linked video has no stored source until uploaded.
        assert added.source_uri is None
    finally:
        app.dependency_overrides.clear()


def test_model_default_origin_is_upload():
    """A directly-constructed Video defaults to origin=upload (the clip-track
    path), so any insert that forgets to set origin is safe — never a hidden
    catalog row."""
    assert Video.__table__.c.origin.default.arg == VideoOrigin.upload


# ── the SEV1 regression: linked videos now appear in the dashboard ───────────


def test_linked_video_appears_in_list_with_clippable_false():
    creator = _creator()
    link_video = MagicMock()
    link_video.id = uuid.uuid4()
    link_video.creator_id = creator.id
    link_video.youtube_video_id = "abcdefghijk"
    link_video.title = "Linked"
    link_video.kind = VideoKind.long
    link_video.ingest_status = IngestStatus.pending
    link_video.duration_s = 120.0
    link_video.created_at = datetime.now(UTC)
    link_video.origin = VideoOrigin.link
    link_video.source_uri = None

    async def _sess():
        s = AsyncMock()
        result = MagicMock()
        result.scalars = MagicMock(return_value=[link_video])
        s.execute = AsyncMock(return_value=result)
        yield s

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _sess
    try:
        client = TestClient(app)
        resp = client.get("/videos")
        assert resp.status_code == 200
        items = resp.json()["videos"]
        assert len(items) == 1
        assert items[0]["origin"] == "link"
        # The whole point: it's visible (SEV1 closed) but flagged non-clippable
        # so the UI offers an upload affordance, not a doomed clip CTA.
        assert items[0]["clippable"] is False
    finally:
        app.dependency_overrides.clear()


# ── the doomed ingest path is gated, not silently fired ──────────────────────


def test_queue_rejects_source_less_video_with_409():
    creator = _creator()
    video_id = uuid.uuid4()
    video = MagicMock()
    video.id = video_id
    video.creator_id = creator.id
    video.ingest_status = IngestStatus.pending
    video.source_uri = None  # linked/catalog row — nothing to ingest

    async def _sess():
        s = AsyncMock()
        s.get = AsyncMock(return_value=video)
        yield s

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _sess
    with patch("routers.videos.start_pipeline") as mock_start:
        try:
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(f"/videos/{video_id}/queue")
        finally:
            app.dependency_overrides.clear()
    assert resp.status_code == 409
    assert "upload" in resp.json()["detail"].lower()
    mock_start.assert_not_called()


# ── migration + frontend honesty ─────────────────────────────────────────────


def test_migration_0024_backfills_from_source_uri():
    src = (_REPO / "alembic/versions/0024_video_origin_enum.py").read_text()
    assert 'revision = "0024"' in src
    assert 'down_revision = "0023"' in src
    assert "video_origin_enum" in src
    # Backfill must derive from source_uri, NOT a blanket server_default, or it
    # would resurface every catalog-only row Issue 90 deliberately hid.
    assert "source_uri IS NOT NULL" in src
    assert "WHERE source_uri IS NULL" in src


@pytest.mark.skip("Issue 226: legacy static pages retired — index.html deleted")
def test_dashboard_offers_upload_affordance_for_linked_videos():
    html = (_REPO / "static/index.html").read_text()
    assert "Upload source file to clip" in html
    # Honest, ToS-compliant guidance toward the sanctioned export path.
    assert "Google Takeout" in html
    # Non-clippable rows must be gated out of the clip CTA, the in-flight
    # ingest tracker, and the status poller.
    assert "v.clippable" in html
    assert "data-clippable" in html
