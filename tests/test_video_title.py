"""Issue 435 — video titles: filename seeding + PATCH /videos/{id} rename.

Unit lane (DB mocked at the session boundary). 80/20: the stem helper's edge
cases, the tri-state rename (set / clear / untouched), and per-creator
isolation on the new endpoint.
"""

from __future__ import annotations

import datetime
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from auth import get_current_creator
from db import get_session
from main import app
from models import Creator, IngestStatus, Video, VideoKind, VideoOrigin
from routers.videos import _title_from_filename
from tests._helpers import override_current_creator, owned_lookup_result

# ── _title_from_filename ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("My Podcast Ep 12.mp4", "My Podcast Ep 12"),
        ("clip.final.v2.MOV", "clip.final.v2"),
        ("   .mp4", None),  # whitespace stem clears to None
        ("", None),
        (None, None),
        ("x" * 300 + ".mp4", "x" * 200),  # clamped
    ],
)
def test_title_from_filename(filename, expected) -> None:
    assert _title_from_filename(filename) == expected


# ── PATCH /videos/{id} ───────────────────────────────────────────────────────


def _creator() -> MagicMock:
    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    return c


def _video(creator_id: uuid.UUID) -> MagicMock:
    v = MagicMock(spec=Video)
    v.id = uuid.uuid4()
    v.creator_id = creator_id
    v.youtube_video_id = None
    v.title = "Old name"
    v.kind = VideoKind.long
    v.ingest_status = IngestStatus.done
    v.failure_reason = None
    v.duration_s = 300.0
    v.created_at = datetime.datetime(2026, 8, 5, tzinfo=datetime.UTC)
    v.origin = VideoOrigin.upload
    v.source_uri = "s3://b/source/x.mp4"
    v.poster_uri = None
    v.peaks_uri = None
    return v


def _override(creator: MagicMock, video: MagicMock | None) -> AsyncMock:
    session = AsyncMock()
    session.info = {}

    async def _execute(stmt, *a, **kw):
        return owned_lookup_result(stmt, video)

    session.execute = AsyncMock(side_effect=_execute)
    app.dependency_overrides[get_current_creator] = override_current_creator(creator)

    async def _gen():
        yield session

    app.dependency_overrides[get_session] = _gen
    return session


def test_patch_title_sets_and_returns_item(client) -> None:
    creator = _creator()
    video = _video(creator.id)
    session = _override(creator, video)

    resp = client.patch(f"/videos/{video.id}", json={"title": "  Episode 12 — draft  "})
    assert resp.status_code == 200
    assert video.title == "Episode 12 — draft"  # trimmed
    assert resp.json()["title"] == "Episode 12 — draft"
    session.commit.assert_awaited()
    app.dependency_overrides.clear()


def test_patch_title_null_clears(client) -> None:
    creator = _creator()
    video = _video(creator.id)
    _override(creator, video)

    resp = client.patch(f"/videos/{video.id}", json={"title": None})
    assert resp.status_code == 200
    assert video.title is None
    assert resp.json()["title"] is None
    app.dependency_overrides.clear()


def test_patch_empty_body_leaves_title_untouched(client) -> None:
    creator = _creator()
    video = _video(creator.id)
    _override(creator, video)

    resp = client.patch(f"/videos/{video.id}", json={})
    assert resp.status_code == 200
    assert video.title == "Old name"
    app.dependency_overrides.clear()


def test_patch_404_for_non_owner(client) -> None:
    creator = _creator()
    # get_owned's ownership select finds nothing for this creator.
    _override(creator, None)

    resp = client.patch(f"/videos/{uuid.uuid4()}", json={"title": "hijack"})
    assert resp.status_code == 404
    app.dependency_overrides.clear()
