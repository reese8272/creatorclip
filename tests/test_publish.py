"""Tests for YouTube publish (Issue 195 / 197): resumable upload client,
task idempotency, and ClipOutcome upsert on publish success.

ffmpeg/Google are never touched — httpx and the DB session are mocked. The full
happy-path task flow (real upload + DB) is covered by the integration suite on
staging (real Postgres); here we lock the load-bearing logic: the resumable
protocol's success/error branches, the at-least-once idempotency skip, and the
ClipOutcome upsert that wires the published clip into the outcome loop.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models import ClipOutcome, PublishStatus
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


# ── Issue 197: ClipOutcome upsert on publish success ──────────────────────────


def _make_full_session(
    *,
    existing_pub: MagicMock | None = None,
    existing_outcome: MagicMock | None = None,
    clip_id: uuid.UUID | None = None,
) -> MagicMock:
    """Build a mock async DB session for the success-path session block.

    The success block calls:
      1. session.get(ClipPublication, pub_id)    → existing_pub (or a fresh MagicMock)
      2. session.get(ClipOutcome, cid)           → existing_outcome
      3. session.add(...)                        → only when no existing_outcome
      4. session.commit()
    """
    session = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.add = MagicMock()

    pub_row = existing_pub if existing_pub is not None else MagicMock()
    # session.get is called twice: once for ClipPublication, once for ClipOutcome.
    session.get = AsyncMock(side_effect=[pub_row, existing_outcome])
    return session


def test_publish_success_creates_clip_outcome_when_absent():
    """Happy path (Issue 197 AC1): a successful first publish creates a ClipOutcome
    row with published_youtube_id set and final=False."""
    from worker.tasks import _publish_to_youtube_async

    cid = uuid.uuid4()
    clip_id_str = str(cid)
    vid = "YT_VID_NEWONE"

    # First session block (setup): returns no existing pub, a clip, and a video.
    clip_mock = MagicMock()
    clip_mock.render_uri = "/tmp/clip.mp4"
    clip_mock.creator_id = uuid.uuid4()
    clip_mock.video_id = uuid.uuid4()

    video_mock = MagicMock()
    video_mock.title = "My Channel Clip"

    setup_session = MagicMock()
    setup_session.flush = AsyncMock()
    setup_session.commit = AsyncMock()
    setup_session.add = MagicMock()
    # execute → scalar_one_or_none returns None (no existing pub)
    no_existing_result = MagicMock()
    no_existing_result.scalar_one_or_none = MagicMock(return_value=None)
    setup_session.execute = AsyncMock(return_value=no_existing_result)
    # get calls: Clip → clip_mock, Video → video_mock
    pub_row = MagicMock()
    pub_row.id = uuid.uuid4()
    setup_session.get = AsyncMock(side_effect=[clip_mock, video_mock])

    # add() stores the pub row so pub.id is accessible
    def _capture_add(obj: object) -> None:
        if isinstance(obj, MagicMock) or hasattr(obj, "id"):
            pass

    setup_session.add.side_effect = lambda obj: setattr(obj, "id", pub_row.id)

    # Second session block (success): no existing outcome → should add one.
    success_session = MagicMock()
    success_session.flush = AsyncMock()
    success_session.commit = AsyncMock()
    added_objects: list[object] = []
    success_session.add = MagicMock(side_effect=added_objects.append)
    # get calls: ClipPublication → a done pub_row, ClipOutcome → None
    success_pub = MagicMock()
    success_session.get = AsyncMock(side_effect=[success_pub, None])

    sessions = iter([setup_session, success_session])

    def _session_factory() -> _SessionCM:
        return _SessionCM(next(sessions))

    # Fake alocal_path context manager
    local_path_cm = MagicMock()
    local_path_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    local_path_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("worker.tasks.db.AdminSessionLocal", _session_factory),
        patch("youtube.oauth.get_valid_access_token", AsyncMock(return_value="tok")),
        patch("worker.storage.alocal_path", return_value=local_path_cm),
        patch("youtube.publish.upload_video", AsyncMock(return_value=vid)),
        patch("youtube.quota.consume", AsyncMock()),
        patch("config.settings") as mock_settings,
    ):
        mock_settings.YOUTUBE_PUBLISH_PRIVACY = "private"
        out = asyncio.run(_publish_to_youtube_async("task-new", clip_id_str))

    assert out == vid
    assert len(added_objects) == 1
    outcome_added = added_objects[0]
    assert isinstance(outcome_added, ClipOutcome)
    assert outcome_added.published_youtube_id == vid
    assert outcome_added.final is False
    assert isinstance(outcome_added.fetched_at, datetime)


def test_publish_outcome_upsert_skips_when_final_true():
    """Issue 197 AC idempotency guard: a ClipOutcome with final=True must not be
    reset — a re-publish of a finalized clip must leave the outcome unchanged."""
    from worker.tasks import _publish_to_youtube_async

    cid = uuid.uuid4()
    vid = "YT_VID_REPUB"

    # Build a finalized existing outcome.
    final_outcome = MagicMock(spec=ClipOutcome)
    final_outcome.final = True
    final_outcome.published_youtube_id = "ORIGINAL_VID"

    # Setup session: clip + video present.
    clip_mock = MagicMock()
    clip_mock.render_uri = "/tmp/r.mp4"
    clip_mock.creator_id = uuid.uuid4()
    clip_mock.video_id = uuid.uuid4()

    video_mock = MagicMock()
    video_mock.title = "Clip"

    setup_session = MagicMock()
    setup_session.flush = AsyncMock()
    setup_session.commit = AsyncMock()
    setup_session.add = MagicMock()
    no_existing_result = MagicMock()
    no_existing_result.scalar_one_or_none = MagicMock(return_value=None)
    setup_session.execute = AsyncMock(return_value=no_existing_result)
    setup_session.get = AsyncMock(side_effect=[clip_mock, video_mock])

    # Success session: ClipOutcome exists with final=True.
    success_session = MagicMock()
    success_session.flush = AsyncMock()
    success_session.commit = AsyncMock()
    success_session.add = MagicMock()
    success_session.get = AsyncMock(side_effect=[MagicMock(), final_outcome])

    sessions = iter([setup_session, success_session])

    def _session_factory() -> _SessionCM:
        return _SessionCM(next(sessions))

    local_path_cm = MagicMock()
    local_path_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    local_path_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("worker.tasks.db.AdminSessionLocal", _session_factory),
        patch("youtube.oauth.get_valid_access_token", AsyncMock(return_value="tok")),
        patch("worker.storage.alocal_path", return_value=local_path_cm),
        patch("youtube.publish.upload_video", AsyncMock(return_value=vid)),
        patch("youtube.quota.consume", AsyncMock()),
        patch("config.settings") as mock_settings,
    ):
        mock_settings.YOUTUBE_PUBLISH_PRIVACY = "private"
        asyncio.run(_publish_to_youtube_async("task-repub", str(cid)))

    # The finalized outcome must NOT have been touched.
    success_session.add.assert_not_called()
    assert final_outcome.published_youtube_id == "ORIGINAL_VID"


def test_publish_outcome_updates_youtube_id_when_not_final():
    """Issue 197: task redelivery with an existing non-final outcome updates
    published_youtube_id without resetting fetched_at or views/retention."""
    from worker.tasks import _publish_to_youtube_async

    cid = uuid.uuid4()
    vid = "YT_VID_RETRY"
    original_fetched = datetime(2026, 1, 1, tzinfo=UTC)

    existing_outcome = MagicMock(spec=ClipOutcome)
    existing_outcome.final = False
    existing_outcome.published_youtube_id = "OLD_VID_ID"
    existing_outcome.fetched_at = original_fetched

    clip_mock = MagicMock()
    clip_mock.render_uri = "/tmp/r.mp4"
    clip_mock.creator_id = uuid.uuid4()
    clip_mock.video_id = uuid.uuid4()

    video_mock = MagicMock()
    video_mock.title = "Clip"

    setup_session = MagicMock()
    setup_session.flush = AsyncMock()
    setup_session.commit = AsyncMock()
    setup_session.add = MagicMock()
    no_existing_result = MagicMock()
    no_existing_result.scalar_one_or_none = MagicMock(return_value=None)
    setup_session.execute = AsyncMock(return_value=no_existing_result)
    setup_session.get = AsyncMock(side_effect=[clip_mock, video_mock])

    success_session = MagicMock()
    success_session.flush = AsyncMock()
    success_session.commit = AsyncMock()
    success_session.add = MagicMock()
    success_session.get = AsyncMock(side_effect=[MagicMock(), existing_outcome])

    sessions = iter([setup_session, success_session])

    def _session_factory() -> _SessionCM:
        return _SessionCM(next(sessions))

    local_path_cm = MagicMock()
    local_path_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    local_path_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("worker.tasks.db.AdminSessionLocal", _session_factory),
        patch("youtube.oauth.get_valid_access_token", AsyncMock(return_value="tok")),
        patch("worker.storage.alocal_path", return_value=local_path_cm),
        patch("youtube.publish.upload_video", AsyncMock(return_value=vid)),
        patch("youtube.quota.consume", AsyncMock()),
        patch("config.settings") as mock_settings,
    ):
        mock_settings.YOUTUBE_PUBLISH_PRIVACY = "private"
        asyncio.run(_publish_to_youtube_async("task-retry", str(cid)))

    # youtube_id updated; fetched_at deliberately NOT reset (preserves 48h/7d schedule).
    assert existing_outcome.published_youtube_id == vid
    assert existing_outcome.fetched_at == original_fetched
    success_session.add.assert_not_called()
