"""Tests for YouTube publish (Issue 195): resumable upload client + task idempotency.

ffmpeg/Google are never touched — httpx and the DB session are mocked. The full
happy-path task flow (real upload + DB) is covered by the integration suite on
staging (real Postgres); here we lock the load-bearing logic: the resumable
protocol's success/error branches and the at-least-once idempotency skip.
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models import PublishStatus
from youtube.errors import YouTubeAuthError
from youtube.publish import YouTubeUploadError, _offset_from_range, upload_video


def _resp(status, *, headers=None, json_body=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.headers = headers or {}
    r.json = MagicMock(return_value=json_body or {})
    r.text = text
    return r


def _fake_client(*, post=None, put=None):
    c = MagicMock()
    c.post = AsyncMock(return_value=post)
    c.put = AsyncMock(return_value=put)
    return c


# ── _offset_from_range ─────────────────────────────────────────────────────────


def test_offset_from_range_parses_byte_end():
    assert _offset_from_range("bytes=0-262143", 0) == 262144  # resume at next byte
    assert _offset_from_range(None, 99) == 99  # fallback when header absent


# ── upload_video ───────────────────────────────────────────────────────────────


def test_upload_video_happy_path(tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"x" * 100)  # single chunk
    client = _fake_client(
        post=_resp(200, headers={"Location": "https://upload.session/abc"}),
        put=_resp(200, json_body={"id": "VID12345"}),
    )
    with patch("youtube.publish._http.client", return_value=client):
        vid = asyncio.run(
            upload_video("tok", media, title="T", description="#Shorts", privacy_status="private")
        )
    assert vid == "VID12345"
    # The session was opened and the bytes were PUT with a Content-Range.
    assert "bytes 0-99/100" in client.put.call_args.kwargs["headers"]["Content-Range"]


def test_upload_video_init_403_is_permanent(tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"x" * 10)
    client = _fake_client(post=_resp(403, text="youtubeSignupRequired / audit"))
    with (
        patch("youtube.publish._http.client", return_value=client),
        pytest.raises(YouTubeUploadError) as ei,
    ):
        asyncio.run(upload_video("tok", media, title="T", description="d"))
    assert ei.value.status_code == 403


def test_upload_video_init_401_is_auth_error(tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"x" * 10)
    client = _fake_client(post=_resp(401))
    with (
        patch("youtube.publish._http.client", return_value=client),
        pytest.raises(YouTubeAuthError),
    ):
        asyncio.run(upload_video("tok", media, title="T", description="d"))


# ── task idempotency ───────────────────────────────────────────────────────────


class _SessionCM:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def test_publish_idempotent_skips_when_already_done():
    """A redelivery whose row is already `done` returns the stored id and never
    re-uploads (the core 'never double-posts' guarantee)."""
    from worker.tasks import _publish_to_youtube_async

    done = MagicMock()
    done.status = PublishStatus.done
    done.youtube_video_id = "ALREADY_UP"

    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=done)
    session.execute = AsyncMock(return_value=result)

    with (
        patch("worker.tasks.db.AdminSessionLocal", lambda: _SessionCM(session)),
        patch("youtube.publish.upload_video", AsyncMock()) as upload,
        patch("youtube.quota.consume", AsyncMock()) as consume,
    ):
        out = asyncio.run(_publish_to_youtube_async("task-1", str(uuid.uuid4())))

    assert out == "ALREADY_UP"
    upload.assert_not_called()  # no second post
    consume.assert_not_called()  # no quota spent on a no-op
