"""Tests for GET /videos/{video_id}/stream — full-source playback (Issue 372)."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from auth import get_current_creator
from db import get_session
from main import app
from models import Creator, Video
from tests._helpers import owned_lookup_result


def _creator() -> MagicMock:
    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    return c


def _video(creator_id, *, source_uri="s3://bucket/source/x.mp4") -> MagicMock:
    video = MagicMock(spec=Video)
    video.id = uuid.uuid4()
    video.creator_id = creator_id
    video.source_uri = source_uri
    return video


def _overrides(creator, video):
    async def _session():
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=lambda stmt, *a, **kw: owned_lookup_result(stmt, video)
        )
        yield session

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session


def test_stream_cross_creator_returns_404(client):
    creator = _creator()
    video = _video(uuid.uuid4())  # owned by someone else
    _overrides(creator, video)
    try:
        resp = client.get(f"/videos/{video.id}/stream", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 404


def test_stream_purged_source_returns_structured_409(client):
    creator = _creator()
    video = _video(creator.id, source_uri=None)
    _overrides(creator, video)
    try:
        resp = client.get(f"/videos/{video.id}/stream", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "source_expired"
    assert "re-upload" in detail["message"]


def test_stream_s3_redirects_to_presigned_inline(client):
    creator = _creator()
    video = _video(creator.id, source_uri="s3://bucket/source/x.mov")
    _overrides(creator, video)
    captured = {}

    def _fake_presign(uri, **kwargs):
        captured.update(kwargs, uri=uri)
        return "https://signed/source"

    try:
        with patch("routers.videos.presigned_download_url", side_effect=_fake_presign):
            resp = client.get(
                f"/videos/{video.id}/stream", cookies={"session": "x"}, follow_redirects=False
            )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://signed/source"
    # Inline by default (backs the <video>); original container suffix kept.
    assert captured["disposition"] == "inline"
    assert captured["filename"].endswith(".mov")


def test_stream_local_file_serves_inline(client, tmp_path):
    creator = _creator()
    media = tmp_path / "source.mp4"
    media.write_bytes(b"\x00\x00fake-source-bytes")
    video = _video(creator.id, source_uri=str(media))
    _overrides(creator, video)
    try:
        resp = client.get(f"/videos/{video.id}/stream", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert "inline" in resp.headers["content-disposition"]
    # FileResponse advertises Range support — the seek contract for <video>.
    assert resp.headers.get("accept-ranges") == "bytes"
    assert resp.content == b"\x00\x00fake-source-bytes"
