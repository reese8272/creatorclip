"""Poster-frame serving endpoints (Issue 387).

Isolation here is asserted against a MOCKED session that emulates the get_owned
predicate. The authoritative proof lives in the integration lane
(tests/test_isolation.py, `-m integration`), which runs under the real RLS role —
see that file's route matrix. Docker is unavailable on the dev box, so those
cases must be executed in CI or on staging before the issue closes.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from auth import get_current_creator
from db import get_session
from main import app
from models import Clip, Creator, Video
from tests._helpers import owned_lookup_result


def _creator() -> MagicMock:
    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    return c


def _row(model, creator_id, poster_uri):
    row = MagicMock(spec=model)
    row.id = uuid.uuid4()
    row.creator_id = creator_id
    row.poster_uri = poster_uri
    return row


def _overrides(creator, row):
    async def _session():
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=lambda stmt, *a, **kw: owned_lookup_result(stmt, row)
        )
        yield session

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session


def _clear():
    app.dependency_overrides.clear()


class TestVideoPoster:
    def test_serves_jpeg_bytes_with_a_private_cache_header(self):
        creator = _creator()
        video = _row(Video, creator.id, "s3://bucket/posters/c/v.jpg")
        _overrides(creator, video)
        try:
            with patch(
                "routers.videos.aread_bytes", AsyncMock(return_value=b"\xff\xd8jpeg")
            ):
                resp = TestClient(app).get(f"/videos/{video.id}/poster")
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "image/jpeg"
            # `private` is load-bearing: the bytes are per-creator and must never
            # reach a shared or CDN cache.
            assert "private" in resp.headers["cache-control"]
            assert resp.content == b"\xff\xd8jpeg"
        finally:
            _clear()

    def test_404_when_no_poster_has_been_extracted(self):
        creator = _creator()
        video = _row(Video, creator.id, None)
        _overrides(creator, video)
        try:
            resp = TestClient(app).get(f"/videos/{video.id}/poster")
            assert resp.status_code == 404
        finally:
            _clear()

    def test_404_when_the_blob_is_gone(self):
        creator = _creator()
        video = _row(Video, creator.id, "s3://bucket/posters/c/v.jpg")
        _overrides(creator, video)
        try:
            with patch("routers.videos.aread_bytes", AsyncMock(return_value=None)):
                resp = TestClient(app).get(f"/videos/{video.id}/poster")
            assert resp.status_code == 404
        finally:
            _clear()

    def test_404_for_another_creators_video(self):
        creator = _creator()
        video = _row(Video, creator.id, "s3://bucket/posters/c/v.jpg")
        _overrides(creator, video)
        try:
            # A different id misses the ownership predicate, exactly as it would
            # in the DB — and 404, not 403, so ownership is not enumerable.
            resp = TestClient(app).get(f"/videos/{uuid.uuid4()}/poster")
            assert resp.status_code == 404
        finally:
            _clear()


class TestClipPoster:
    def test_serves_jpeg_bytes(self):
        creator = _creator()
        clip = _row(Clip, creator.id, "s3://bucket/posters/c/clip-x.jpg")
        _overrides(creator, clip)
        try:
            with patch("routers.clips.aread_bytes", AsyncMock(return_value=b"\xff\xd8jpeg")):
                resp = TestClient(app).get(f"/clips/{clip.id}/poster")
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "image/jpeg"
        finally:
            _clear()

    def test_404_for_another_creators_clip(self):
        creator = _creator()
        clip = _row(Clip, creator.id, "s3://bucket/posters/c/clip-x.jpg")
        _overrides(creator, clip)
        try:
            resp = TestClient(app).get(f"/clips/{uuid.uuid4()}/poster")
            assert resp.status_code == 404
        finally:
            _clear()


def test_poster_uri_is_never_returned_in_json():
    """The storage key is internal. Only the boolean crosses the wire."""
    from routers.clips import ClipOut
    from routers.videos import VideoListItemOut

    assert "poster_uri" not in VideoListItemOut.model_fields
    assert "has_poster" in VideoListItemOut.model_fields
    assert "poster_uri" not in ClipOut.model_fields
    assert "has_poster" in ClipOut.model_fields
