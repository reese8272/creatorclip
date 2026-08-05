"""Issue 421 — crop-track persistence + API.

Covers: GET /clips/{clip_id}/crop-track (auth + per-creator isolation + the
404 `no_crop_track` body), `has_crop_track` on ClipOut, the done-marking
transaction persisting/replacing the track, `_load_clip_render_plan` loading
the transcript whenever the reframe flag is on, and the 0055 migration file's
structure (revision chain + nullable JSONB column).

Session-boundary mocks (unit lane); the RLS proof lives in the integration
lane per the project's two-lane testing rule.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from auth import get_current_creator
from db import get_session
from main import app
from models import Clip, Creator
from tests._helpers import owned_lookup_result

_REPO = Path(__file__).resolve().parents[1]

_TRACK = {
    "version": 1,
    "mode": "speaker_cut",
    "source": {"width": 1920, "height": 1080},
    "crop": {"width": 607, "height": 1080},
    "origin_s": 10.0,
    "duration_s": 10.0,
    "keyframes": [{"t": 0.0, "x": 100}, {"t": 0.2, "x": 1196}],
    "cuts": [{"t": 0.2, "from_x": 100, "to_x": 1196, "speaker": 1}],
    "shots": [],
    "speakers": {"count": 2, "mapping_confidence": 0.9},
    "meta": {"sample_fps": 5.0, "fallback": False},
}


def _creator() -> MagicMock:
    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    return c


def _clip(creator_id, track) -> MagicMock:
    clip = MagicMock(spec=Clip)
    clip.id = uuid.uuid4()
    clip.creator_id = creator_id
    clip.reframe_track_jsonb = track
    return clip


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


# ── GET /clips/{clip_id}/crop-track ──────────────────────────────────────────


class TestCropTrackEndpoint:
    def test_serves_the_persisted_track(self):
        creator = _creator()
        clip = _clip(creator.id, _TRACK)
        _overrides(creator, clip)
        try:
            resp = TestClient(app).get(f"/clips/{clip.id}/crop-track")
            assert resp.status_code == 200
            body = resp.json()
            assert body == _TRACK
            # The wire values ARE the sendcmd values — one geometry definition.
            assert body["keyframes"][1]["x"] == body["cuts"][0]["to_x"]
        finally:
            _clear()

    def test_404_no_crop_track_body(self):
        """Pre-pipeline / flag-off clips 404 with the structured code the
        frontend keys on (honest absence — the overlay renders nothing)."""
        creator = _creator()
        clip = _clip(creator.id, None)
        _overrides(creator, clip)
        try:
            resp = TestClient(app).get(f"/clips/{clip.id}/crop-track")
            assert resp.status_code == 404
            assert resp.json()["detail"]["code"] == "no_crop_track"
        finally:
            _clear()

    def test_404_for_another_creators_clip(self):
        """Per-creator isolation: a different clip id misses the ownership
        predicate — 404, not 403, so ownership is not enumerable."""
        creator = _creator()
        clip = _clip(creator.id, _TRACK)
        _overrides(creator, clip)
        try:
            resp = TestClient(app).get(f"/clips/{uuid.uuid4()}/crop-track")
            assert resp.status_code == 404
        finally:
            _clear()

    def test_requires_auth(self):
        _clear()
        resp = TestClient(app).get(f"/clips/{uuid.uuid4()}/crop-track")
        assert resp.status_code == 401


# ── ClipOut surface ──────────────────────────────────────────────────────────


def test_clipout_carries_boolean_not_track():
    """The list surface only carries `has_crop_track` — a track is ~15–20 KB
    per clip, too heavy for a list; the JSON comes from the endpoint."""
    from routers.clips import ClipOut

    assert "has_crop_track" in ClipOut.model_fields
    assert ClipOut.model_fields["has_crop_track"].default is False
    assert "reframe_track_jsonb" not in ClipOut.model_fields


def test_clip_response_maps_track_presence():
    from routers.clips import _clip_response

    clip = MagicMock(spec=Clip)
    clip.id = uuid.uuid4()
    clip.video_id = uuid.uuid4()
    clip.setup_start_s = None
    clip.start_s = 0.0
    clip.end_s = 10.0
    clip.peak_s = None
    clip.score = None
    clip.rank = None
    clip.signals_jsonb = {}
    clip.render_status = MagicMock(value="pending")
    clip.render_uri = None
    clip.cleaned_render_uri = None
    clip.applied_title = None
    clip.applied_description = None
    clip.style_preset = None
    clip.poster_uri = None

    clip.reframe_track_jsonb = None
    assert _clip_response(clip)["has_crop_track"] is False
    clip.reframe_track_jsonb = _TRACK
    assert _clip_response(clip)["has_crop_track"] is True


# ── Worker: done-marking transaction persists / replaces the track ───────────


def _render_plan():
    from worker.tasks import _ClipRenderPlan

    return _ClipRenderPlan(
        source_uri="s3://bucket/videos/v.mp4",
        setup_start_s=None,
        start_s=10.0,
        end_s=20.0,
        peak_s=None,
        clip_duration_s=10.0,
        style_preset=None,
        transcript_segments=None,
    )


class _FakeTenantSession:
    def __init__(self, clip):
        self._clip = clip
        self.committed = False

    def __call__(self, creator_id):
        return self

    async def __aenter__(self):
        session = AsyncMock()
        session.get = AsyncMock(return_value=self._clip)

        async def _commit():
            self.committed = True

        session.commit = AsyncMock(side_effect=_commit)
        return session

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_encode_persists_track_in_done_transaction(tmp_path):
    """The track rides the SAME transaction that marks the clip done."""
    from worker.tasks import _encode_and_upload_clip

    clip = MagicMock(spec=Clip)
    clip.reframe_track_jsonb = None
    fake_sessions = _FakeTenantSession(clip)

    with (
        patch("clip_engine.render.render_clip_file", return_value=_TRACK),
        patch("worker.storage.aupload_file", AsyncMock(return_value="s3://clips/x.mp4")),
        patch("worker.tasks._extract_and_upload_poster", AsyncMock(return_value=None)),
        patch("worker.progress.aemit", AsyncMock()),
        patch("worker.tasks.db.tenant_session", fake_sessions),
    ):
        await _encode_and_upload_clip(
            str(uuid.uuid4()), tmp_path / "src.mp4", _render_plan(), str(uuid.uuid4())
        )

    assert clip.reframe_track_jsonb == _TRACK
    assert clip.render_uri == "s3://clips/x.mp4"
    assert fake_sessions.committed


@pytest.mark.asyncio
async def test_encode_nulls_stale_track_when_reframe_off(tmp_path):
    """Flag-off re-render REPLACES (nulls) a previously persisted track —
    the stored track must never describe a render the creator isn't seeing."""
    from worker.tasks import _encode_and_upload_clip

    clip = MagicMock(spec=Clip)
    clip.reframe_track_jsonb = _TRACK  # stale, from an earlier flag-on render
    fake_sessions = _FakeTenantSession(clip)

    with (
        patch("clip_engine.render.render_clip_file", return_value=None),
        patch("worker.storage.aupload_file", AsyncMock(return_value="s3://clips/x.mp4")),
        patch("worker.tasks._extract_and_upload_poster", AsyncMock(return_value=None)),
        patch("worker.progress.aemit", AsyncMock()),
        patch("worker.tasks.db.tenant_session", fake_sessions),
    ):
        await _encode_and_upload_clip(
            str(uuid.uuid4()), tmp_path / "src.mp4", _render_plan(), str(uuid.uuid4())
        )

    assert clip.reframe_track_jsonb is None


# ── Worker: _load_clip_render_plan loads transcript when the flag is on ──────


def _plan_session(clip, video, transcript):
    from models import Transcript, Video

    class _Session:
        def __init__(self):
            self.transcript_requested = False

        async def get(self, model, key, **kwargs):
            if model is Clip:
                return clip
            if model is Video:
                return video
            if model is Transcript:
                self.transcript_requested = True
                return transcript
            return None

        async def commit(self):
            return None

    class _Ctx:
        def __init__(self):
            self.session = _Session()

        def __call__(self, creator_id):
            return self

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, *exc):
            return False

    return _Ctx()


def _pending_clip():
    from models import RenderStatus

    clip = MagicMock(spec=Clip)
    clip.render_status = RenderStatus.pending
    clip.render_uri = None
    clip.setup_start_s = None
    clip.start_s = 10.0
    clip.end_s = 20.0
    clip.peak_s = None
    clip.style_preset = None  # NO caption style — the old gate would skip
    clip.video_id = uuid.uuid4()
    return clip


@pytest.mark.asyncio
async def test_render_plan_loads_transcript_when_reframe_enabled():
    import config as _config_mod
    from worker.tasks import _load_clip_render_plan

    video = MagicMock()
    video.source_uri = "s3://bucket/videos/v.mp4"
    video.id = uuid.uuid4()
    transcript = MagicMock()
    transcript.segments_jsonb = {"segments": [{"start": 0.0, "end": 1.0, "words": []}]}
    ctx = _plan_session(_pending_clip(), video, transcript)

    with (
        patch("worker.tasks.db.tenant_session", ctx),
        patch("worker.tasks._amark_render_started", AsyncMock()),
        patch("worker.progress.aemit", AsyncMock()),
        patch.object(_config_mod.settings, "ACTIVE_SPEAKER_REFRAME_ENABLED", True),
    ):
        plan = await _load_clip_render_plan(str(uuid.uuid4()), str(uuid.uuid4()))

    assert plan is not None
    assert ctx.session.transcript_requested, "reframe-on must load the transcript"
    assert plan.transcript_segments == [{"start": 0.0, "end": 1.0, "words": []}]


@pytest.mark.asyncio
async def test_render_plan_skips_transcript_when_flag_off_and_no_captions():
    """Byte-compat with today: flag off + no caption style = no transcript
    query (Issue 133's skip preserved)."""
    import config as _config_mod
    from worker.tasks import _load_clip_render_plan

    video = MagicMock()
    video.source_uri = "s3://bucket/videos/v.mp4"
    video.id = uuid.uuid4()
    ctx = _plan_session(_pending_clip(), video, MagicMock())

    with (
        patch("worker.tasks.db.tenant_session", ctx),
        patch("worker.tasks._amark_render_started", AsyncMock()),
        patch("worker.progress.aemit", AsyncMock()),
        patch.object(_config_mod.settings, "ACTIVE_SPEAKER_REFRAME_ENABLED", False),
    ):
        plan = await _load_clip_render_plan(str(uuid.uuid4()), str(uuid.uuid4()))

    assert plan is not None
    assert not ctx.session.transcript_requested
    assert plan.transcript_segments is None


# ── Migration 0055 structure ─────────────────────────────────────────────────


def test_migration_0055_structure():
    src = (_REPO / "alembic/versions/0055_clip_reframe_track.py").read_text()
    assert 'revision = "0055"' in src
    # Re-parented onto 0054 at the L26 merge (binding cross-track contract:
    # Track A lands 0053/0054 first; branch-time parent was 0052).
    assert 'down_revision = "0054"' in src
    assert '"reframe_track_jsonb"' in src
    assert "JSONB(), nullable=True" in src
    assert 'op.drop_column("clips", "reframe_track_jsonb")' in src


def test_clip_model_has_nullable_track_column():
    from sqlalchemy.dialects.postgresql import JSONB as PGJSONB

    col = Clip.__table__.columns["reframe_track_jsonb"]
    assert col.nullable is True
    assert isinstance(col.type, PGJSONB)
