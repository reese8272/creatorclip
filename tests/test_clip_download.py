"""Tests for the clip download/export endpoint + presign helper (Issue 182)."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from auth import get_current_creator
from db import get_session
from main import app
from models import Clip, Creator
from tests._helpers import owned_lookup_result
from worker.storage import presigned_download_url


def _creator() -> MagicMock:
    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    return c


def _clip(
    creator_id, *, render_uri="s3://bucket/clips/x.mp4", cleaned_render_uri=None
) -> MagicMock:
    clip = MagicMock(spec=Clip)
    clip.id = uuid.uuid4()
    clip.creator_id = creator_id
    clip.render_uri = render_uri
    clip.cleaned_render_uri = cleaned_render_uri
    return clip


def _fake_session(clip):
    async def _session():
        session = AsyncMock()
        # get_owned ownership select (Issue 109e) — emulates the DB predicate,
        # so the cross-creator clip genuinely misses (404).
        session.execute = AsyncMock(
            side_effect=lambda stmt, *a, **kw: owned_lookup_result(stmt, clip)
        )
        yield session

    return _session


def _overrides(creator, clip):
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session(clip)


# ── presign helper ────────────────────────────────────────────────────────────


def test_presigned_download_url_none_for_local_uri():
    assert presigned_download_url("/var/media/clips/x.mp4", filename="x.mp4") is None


def test_presigned_download_url_signs_s3_with_disposition():
    fake = MagicMock()
    fake.generate_presigned_url.return_value = "https://signed.example/clip"
    with patch("worker.storage._r2", return_value=fake):
        url = presigned_download_url(
            "s3://bucket/clips/x.mp4", filename="clip-1.mp4", disposition="inline", expires_s=300
        )
    assert url == "https://signed.example/clip"
    params = fake.generate_presigned_url.call_args.kwargs["Params"]
    assert params["Bucket"] == "bucket" and params["Key"] == "clips/x.mp4"
    assert params["ResponseContentDisposition"] == 'inline; filename="clip-1.mp4"'


# ── download endpoint ─────────────────────────────────────────────────────────


def test_download_cross_creator_returns_404(client):
    creator = _creator()
    clip = _clip(uuid.uuid4())  # owned by someone else
    _overrides(creator, clip)
    try:
        resp = client.get(f"/clips/{clip.id}/download", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 404


def test_download_unrendered_returns_404(client):
    creator = _creator()
    clip = _clip(creator.id, render_uri=None)
    _overrides(creator, clip)
    try:
        resp = client.get(f"/clips/{clip.id}/download", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 404


def test_download_s3_redirects_to_presigned(client):
    creator = _creator()
    clip = _clip(creator.id, render_uri="s3://bucket/clips/x.mp4")
    _overrides(creator, clip)
    try:
        with patch("routers.clips.presigned_download_url", return_value="https://signed/clip"):
            resp = client.get(
                f"/clips/{clip.id}/download", cookies={"session": "x"}, follow_redirects=False
            )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://signed/clip"


def test_download_cleaned_variant_uses_cleaned_uri(client):
    creator = _creator()
    clip = _clip(creator.id, render_uri="s3://b/orig.mp4", cleaned_render_uri="s3://b/clean.mp4")
    _overrides(creator, clip)
    captured = {}

    def _fake_presign(uri, **kwargs):
        captured["uri"] = uri
        return "https://signed/clean"

    try:
        with patch("routers.clips.presigned_download_url", side_effect=_fake_presign):
            resp = client.get(
                f"/clips/{clip.id}/download?variant=cleaned",
                cookies={"session": "x"},
                follow_redirects=False,
            )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 302
    assert captured["uri"] == "s3://b/clean.mp4"


def test_download_local_file_streams_as_attachment(client, tmp_path):
    creator = _creator()
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"\x00\x00fake-mp4-bytes")
    clip = _clip(creator.id, render_uri=str(media))
    _overrides(creator, clip)
    try:
        resp = client.get(f"/clips/{clip.id}/download", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.content == b"\x00\x00fake-mp4-bytes"
