"""Issue 446 — archive / restore / permanently-erase a video.

Archive frees the stored media and hides the row from every surface while
PRESERVING clips + clip_feedback (the preference model's training labels) and
the posters/peaks identity artifacts (owner default: they survive archive so a
restored row is recognisable). Erase is the separate, explicitly-worded
permanent path: full media enumeration incl. posters/peaks, then row deletion.

Written RED-first: these tests were authored before the endpoints existed.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from auth import SESSION_COOKIE, create_session_token, get_current_creator
from db import get_session
from main import app
from tests._helpers import override_current_creator


def _cookie(creator) -> dict:
    return {SESSION_COOKIE: create_session_token(creator.id)}


def _make_creator():
    c = MagicMock()
    c.id = uuid.uuid4()
    return c


def _make_video(creator_id, archived_at=None):
    v = MagicMock()
    v.id = uuid.uuid4()
    v.creator_id = creator_id
    v.archived_at = archived_at
    v.source_uri = f"s3://media/source/{creator_id}/tok.mp4"
    v.audio_uri = f"s3://media/audio/{v.id}.wav"
    v.poster_uri = f"s3://media/posters/{creator_id}/{v.id}.jpg"
    v.peaks_uri = f"s3://media/peaks/{creator_id}/{v.id}.json"
    v.ingest_done_at = datetime(2026, 8, 1, tzinfo=UTC)
    v.created_at = datetime(2026, 8, 1, tzinfo=UTC)
    return v


def _make_clip(creator_id, video_id):
    c = MagicMock()
    c.id = uuid.uuid4()
    c.creator_id = creator_id
    c.video_id = video_id
    c.render_uri = f"s3://media/clips/{c.id}.mp4"
    c.cleaned_render_uri = None
    c.poster_uri = f"s3://media/posters/{creator_id}/clip-{c.id}.jpg"
    return c


class _Scalars(list):
    def all(self):
        return list(self)


class _FakeResult:
    def __init__(self, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = rows if rows is not None else []

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return _Scalars(self._rows)


class _FakeSession:
    """Statement-dispatching fake: routes by table name, records everything."""

    def __init__(self, video=None, clips=None, summaries=None):
        self.video = video
        self.clips = clips or []
        self.summaries = summaries or []
        self.statements: list[str] = []
        self.commits = 0
        self.deleted = []
        self.info = {}

    async def execute(self, stmt, *a, **k):
        s = str(stmt)
        self.statements.append(s)
        if s.lstrip().upper().startswith("DELETE"):
            return _FakeResult()
        if "FROM summaries" in s:
            return _FakeResult(rows=self.summaries)
        if "FROM clips" in s:
            return _FakeResult(rows=self.clips)
        if "FROM videos" in s:
            return _FakeResult(scalar=self.video, rows=[])
        return _FakeResult()

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass

    async def refresh(self, obj):
        pass

    async def delete(self, obj):
        self.deleted.append(obj)

    def add(self, obj):
        pass


def _install(creator, session):
    async def _dep():
        yield session

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _dep


# ── Archive ───────────────────────────────────────────────────────────────────


def test_archive_sets_archived_at_and_purges_media_but_not_identity_artifacts(client):
    creator = _make_creator()
    video = _make_video(creator.id)
    clip = _make_clip(creator.id, video.id)
    session = _FakeSession(video=video, clips=[clip])
    _install(creator, session)

    purged: list[str] = []
    try:
        with patch("worker.storage.delete_file", side_effect=purged.append):
            resp = client.delete(f"/videos/{video.id}", cookies=_cookie(creator))
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "archived"
    assert video.archived_at is not None

    # Media freed: source, audio, and every render variant of every clip.
    assert any(u.endswith(f"source/{creator.id}/tok.mp4") for u in purged)
    assert any(u.endswith(f"audio/{video.id}.wav") for u in purged)
    assert any(u.endswith(f"clips/{clip.id}.mp4") for u in purged)
    assert any(u.endswith(f"clips/{clip.id}_clean.mp4") for u in purged)
    assert any(u.endswith(f"clips/{clip.id}_edit.mp4") for u in purged)
    # Identity artifacts SURVIVE archive (owner default — restore recognisability).
    assert not any("posters/" in u for u in purged)
    assert not any("peaks/" in u for u in purged)
    # Purged pointers are nulled; poster/peaks pointers are kept.
    assert video.source_uri is None
    assert video.audio_uri is None
    assert clip.render_uri is None
    assert video.poster_uri is not None
    assert video.peaks_uri is not None
    assert clip.poster_uri is not None


def test_archive_is_idempotent_repeat_is_200_noop(client):
    creator = _make_creator()
    video = _make_video(creator.id, archived_at=datetime(2026, 8, 10, tzinfo=UTC))
    session = _FakeSession(video=video, clips=[])
    _install(creator, session)

    purged: list[str] = []
    try:
        with patch("worker.storage.delete_file", side_effect=purged.append):
            resp = client.delete(f"/videos/{video.id}", cookies=_cookie(creator))
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["status"] == "noop"
    assert purged == []
    assert video.archived_at == datetime(2026, 8, 10, tzinfo=UTC)


def test_archive_preserves_clip_and_feedback_rows(client):
    """Archiving must delete NO rows — clips + clip_feedback are the preference
    model's training labels (models.py archived_at docstring)."""
    creator = _make_creator()
    video = _make_video(creator.id)
    clip = _make_clip(creator.id, video.id)
    session = _FakeSession(video=video, clips=[clip])
    _install(creator, session)

    try:
        with patch("worker.storage.delete_file"):
            resp = client.delete(f"/videos/{video.id}", cookies=_cookie(creator))
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert session.deleted == []
    assert not any(s.lstrip().upper().startswith("DELETE") for s in session.statements)


def test_archive_keeps_pointer_when_blob_delete_fails(client):
    """Object-Lock / transient-refusal posture: log-and-continue, and DO NOT
    null the DB pointer whose blob still exists — a later sweep retries it."""
    creator = _make_creator()
    video = _make_video(creator.id)
    session = _FakeSession(video=video, clips=[])
    _install(creator, session)

    def _fail_source(uri: str) -> None:
        if "source/" in uri:
            raise RuntimeError("AccessDenied: Object Lock")

    try:
        with patch("worker.storage.delete_file", side_effect=_fail_source):
            resp = client.delete(f"/videos/{video.id}", cookies=_cookie(creator))
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200  # the archive itself still succeeds
    assert video.archived_at is not None
    assert video.source_uri is not None  # pointer kept for retry
    assert video.audio_uri is None  # the delete that succeeded IS nulled


# ── Restore ───────────────────────────────────────────────────────────────────


def test_restore_clears_archived_at_and_is_idempotent(client):
    creator = _make_creator()
    video = _make_video(creator.id, archived_at=datetime(2026, 8, 10, tzinfo=UTC))
    video.title = "t"
    video.kind = MagicMock(value="long")
    video.ingest_status = MagicMock(value="done")
    video.failure_reason = None
    video.duration_s = 10.0
    video.origin = MagicMock(value="upload")
    video.youtube_video_id = None
    session = _FakeSession(video=video, clips=[])
    _install(creator, session)

    try:
        resp = client.post(f"/videos/{video.id}/restore", cookies=_cookie(creator))
        assert resp.status_code == 200
        assert video.archived_at is None
        # Repeat is a 200 no-op.
        resp2 = client.post(f"/videos/{video.id}/restore", cookies=_cookie(creator))
        assert resp2.status_code == 200
    finally:
        app.dependency_overrides.clear()


# ── Permanent erase ───────────────────────────────────────────────────────────


def test_erase_purges_all_media_including_identity_artifacts_and_deletes_rows(client):
    creator = _make_creator()
    video = _make_video(creator.id)
    clip = _make_clip(creator.id, video.id)
    summary = MagicMock()
    summary.id = uuid.uuid4()
    summary.creator_id = creator.id
    summary.video_id = video.id
    summary.render_uri = f"s3://media/summaries/{summary.id}.mp4"
    session = _FakeSession(video=video, clips=[clip], summaries=[summary])
    _install(creator, session)

    purged: list[str] = []
    try:
        with patch("worker.storage.delete_file", side_effect=purged.append):
            resp = client.post(f"/videos/{video.id}/erase", cookies=_cookie(creator))
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["status"] == "erased"
    # FULL enumeration — identity artifacts included this time.
    assert any(u.endswith(f"posters/{creator.id}/{video.id}.jpg") for u in purged)
    assert any(u.endswith(f"peaks/{creator.id}/{video.id}.json") for u in purged)
    assert any(u.endswith(f"posters/{creator.id}/clip-{clip.id}.jpg") for u in purged)
    assert any(u.endswith(f"clips/{clip.id}.mp4") for u in purged)
    assert any(u.endswith(f"summaries/{summary.id}.mp4") for u in purged)
    # Row deletion happened (DB FK cascades take clips/feedback/publications).
    assert any(s.lstrip().upper().startswith("DELETE FROM VIDEOS") for s in session.statements)


# ── Read-path filters (archived videos disappear) ─────────────────────────────


def _capture_session():
    session = AsyncMock()
    captured: list[str] = []

    async def execute(stmt, *a, **k):
        captured.append(str(stmt))
        result = MagicMock()
        result.scalars.return_value = _Scalars([])
        result.scalar_one.return_value = 0
        result.one.return_value = (0, 0, 0, None, None)
        result.all.return_value = []
        return result

    session.execute = execute
    session.commit = AsyncMock()
    session.info = {}
    return session, captured


def test_list_videos_excludes_archived_by_default(client):
    creator = _make_creator()
    session, captured = _capture_session()
    _install(creator, session)
    try:
        resp = client.get("/videos", cookies=_cookie(creator))
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert any("archived_at IS NULL" in s for s in captured)


def test_list_videos_archived_true_returns_only_archived(client):
    creator = _make_creator()
    session, captured = _capture_session()
    _install(creator, session)
    try:
        resp = client.get("/videos?archived=true", cookies=_cookie(creator))
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert any("archived_at IS NOT NULL" in s for s in captured)
    # The archived view is a filter, not a first-run state — never show the
    # guided onboarding empty-hero copy there.
    assert resp.json()["state"] == "empty_filtered"


def test_catalog_excludes_archived(client):
    creator = _make_creator()
    session, captured = _capture_session()
    _install(creator, session)
    try:
        resp = client.get("/videos/catalog", cookies=_cookie(creator))
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert any("archived_at IS NULL" in s for s in captured)


def test_clip_counts_excludes_archived(client):
    creator = _make_creator()
    session, captured = _capture_session()
    _install(creator, session)
    try:
        resp = client.get("/videos/clips/counts", cookies=_cookie(creator))
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert any("archived_at IS NULL" in s for s in captured)


def test_analytics_summary_excludes_archived(client):
    creator = _make_creator()
    session, captured = _capture_session()
    _install(creator, session)
    try:
        resp = client.get("/creators/me/insights/analytics", cookies=_cookie(creator))
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert any("archived_at IS NULL" in s for s in captured)


# ── Expiry surface (Issue 446 — the 72h window made visible) ─────────────────


def test_video_item_carries_source_expiry_fields():
    from config import settings
    from routers.videos import _video_item

    creator_id = uuid.uuid4()
    video = _make_video(creator_id)
    video.title = "t"
    video.kind = MagicMock(value="long")
    video.ingest_status = MagicMock(value="done")
    video.failure_reason = None
    video.duration_s = 10.0
    video.origin = MagicMock(value="upload")
    video.youtube_video_id = None

    item = _video_item(video)
    assert item["source_expired"] is False
    assert item["archived_at"] is None
    from datetime import timedelta

    expected = video.ingest_done_at + timedelta(hours=settings.SOURCE_MEDIA_RETENTION_HOURS)
    assert item["source_expires_at"] == expected.isoformat()

    # Once the purge nulls source_uri, the row is explicitly not-re-renderable.
    video.source_uri = None
    item = _video_item(video)
    assert item["source_expired"] is True
    assert item["source_expires_at"] is None
