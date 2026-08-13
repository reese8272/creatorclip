"""Tests for POST /videos/{video_id}/clips — creator-selected clips (Issue 373)."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from auth import get_current_creator
from db import get_session
from main import app
from models import Clip, ClipTriage, Creator, IngestStatus, RenderStatus, Video
from routers.clips import _clip_response
from tests._helpers import owned_lookup_result


def _creator() -> MagicMock:
    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    return c


def _video(
    creator_id, *, ingest_status=IngestStatus.done, source_uri="s3://b/src.mp4", duration_s=300.0
) -> MagicMock:
    video = MagicMock(spec=Video)
    video.id = uuid.uuid4()
    video.creator_id = creator_id
    video.ingest_status = ingest_status
    video.source_uri = source_uri
    video.duration_s = duration_s
    return video


def _scalars_result(rows):
    result = MagicMock()
    result.scalars.return_value.first.return_value = rows[0] if rows else None
    result.scalar_one_or_none.return_value = rows[0] if rows else None
    return result


def _overrides(creator, video, *, existing_clip=None):
    """Session mock: get_owned select → video; dedupe select → existing_clip;
    brand-kit select → none. Distinguished by inspecting the statement."""
    added = []

    async def _session():
        session = AsyncMock()

        def _execute(stmt, *a, **kw):
            desc = str(stmt)
            if "videos" in desc and "clips" not in desc:
                return owned_lookup_result(stmt, video)
            if "clips" in desc:
                return _scalars_result([existing_clip] if existing_clip else [])
            return _scalars_result([])  # creator_styles — no kit row

        session.execute = AsyncMock(side_effect=_execute)
        session.add = MagicMock(side_effect=added.append)
        session.added = added
        yield session

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session
    return added


@pytest.fixture(autouse=True)
def _positive_balance():
    with patch("routers.clips.check_positive_balance", new=AsyncMock()):
        yield


def _post(client, video_id, body):
    return client.post(f"/videos/{video_id}/clips", json=body, cookies={"session": "x"})


def test_create_clip_foreign_video_404(client):
    creator = _creator()
    video = _video(uuid.uuid4())
    _overrides(creator, video)
    try:
        resp = _post(client, video.id, {"start_s": 10, "end_s": 40})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 404


def test_create_clip_not_ingested_400(client):
    creator = _creator()
    video = _video(creator.id, ingest_status=IngestStatus.running)
    _overrides(creator, video)
    try:
        resp = _post(client, video.id, {"start_s": 10, "end_s": 40})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 400


def test_create_clip_purged_source_structured_409(client):
    creator = _creator()
    video = _video(creator.id, source_uri=None)
    _overrides(creator, video)
    try:
        resp = _post(client, video.id, {"start_s": 10, "end_s": 40})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "source_expired"


@pytest.mark.parametrize(
    "body",
    [
        {"start_s": 40, "end_s": 10},  # inverted
        {"start_s": 10, "end_s": 10.5},  # < 2s floor
        {"start_s": 0, "end_s": 700},  # > 600s cap (and past 300s duration)
        {"start_s": 100, "end_s": 320},  # ends past 300s source + epsilon
        {"start_s": -5, "end_s": 40},  # negative start (schema ge=0)
    ],
)
def test_create_clip_bounds_rejected_422(client, body):
    creator = _creator()
    video = _video(creator.id)
    _overrides(creator, video)
    try:
        resp = _post(client, video.id, body)
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 422


def test_create_clip_happy_path_persists_creator_provenance_and_enqueues(client):
    creator = _creator()
    video = _video(creator.id)
    added = _overrides(creator, video)
    try:
        with patch("worker.tasks.render_clip") as render_task:
            resp = _post(client, video.id, {"start_s": 10.0, "end_s": 40.0})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 201
    body = resp.json()
    assert body["origin"] == "creator"
    assert body["score"] is None and body["rank"] is None and body["peak_s"] is None
    assert body["principle"] == ""

    assert len(added) == 1
    clip = added[0]
    assert clip.signals_jsonb == {"origin": "creator", "principle": "", "reasoning": ""}
    assert clip.render_status == RenderStatus.pending
    assert clip.setup_start_s is None
    render_task.delay.assert_called_once()


def test_create_clip_duplicate_selection_returns_existing_200(client):
    creator = _creator()
    video = _video(creator.id)
    existing = MagicMock(spec=Clip)
    existing.triage = ClipTriage.pending
    existing.id = uuid.uuid4()
    existing.video_id = video.id
    existing.creator_id = creator.id
    existing.setup_start_s = None
    existing.start_s = 10.0
    existing.end_s = 40.0
    existing.peak_s = None
    existing.score = None
    existing.rank = None
    existing.signals_jsonb = {"origin": "creator", "principle": "", "reasoning": ""}
    existing.render_status = RenderStatus.pending
    existing.render_uri = None
    existing.cleaned_render_uri = None
    existing.applied_title = None
    existing.suggested_title = None
    existing.suggested_description = None
    existing.suggested_hook = None
    existing.applied_description = None
    existing.style_preset = None
    added = _overrides(creator, video, existing_clip=existing)
    try:
        with patch("worker.tasks.render_clip") as render_task:
            resp = _post(client, video.id, {"start_s": 10.0, "end_s": 40.0})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json()["id"] == str(existing.id)
    assert added == []  # no duplicate insert
    render_task.delay.assert_not_called()  # no second enqueue


def test_create_clip_broker_failure_still_201(client):
    creator = _creator()
    video = _video(creator.id)
    _overrides(creator, video)
    try:
        with patch("worker.tasks.render_clip") as render_task:
            render_task.delay.side_effect = RuntimeError("broker down")
            resp = _post(client, video.id, {"start_s": 10.0, "end_s": 40.0})
    finally:
        app.dependency_overrides.clear()
    # The row exists as pending; the player's manual render button is recovery.
    assert resp.status_code == 201
    assert resp.json()["render_status"] == "pending"


def test_list_clips_impressions_exclude_creator_selections(client):
    """Issue 373: creator-made clips (rank NULL) must not be logged at rank 0 —
    that would poison the IPS/counterfactual top slot."""
    creator = _creator()
    video = _video(creator.id)

    def _mock_clip(rank, origin):
        cl = MagicMock(spec=Clip)
        cl.triage = ClipTriage.pending
        cl.id = uuid.uuid4()
        cl.video_id = video.id
        cl.creator_id = creator.id
        cl.setup_start_s = None
        cl.start_s = 0.0
        cl.end_s = 30.0
        cl.peak_s = None
        cl.score = None
        cl.rank = rank
        cl.signals_jsonb = {"origin": origin} if origin else {}
        cl.render_status = RenderStatus.pending
        cl.render_uri = None
        cl.cleaned_render_uri = None
        cl.applied_title = None
        cl.suggested_title = None
        cl.suggested_description = None
        cl.suggested_hook = None
        cl.applied_description = None
        cl.style_preset = None
        cl.downloaded_at = None  # Issue 447
        return cl

    engine_clip = _mock_clip(1, None)
    creator_clip = _mock_clip(None, "creator")
    clips = [engine_clip, creator_clip]

    captured = {}

    async def _session():
        s = AsyncMock()
        result = MagicMock()
        result.scalars.side_effect = lambda: iter(clips)  # fresh iter per call

        async def _execute(stmt, *a, **kw):
            from models import ClipPublication

            # Issue 447: the latest-publication aggregate returns no rows here.
            if stmt.column_descriptions[0]["type"] is ClipPublication:
                r = MagicMock()
                r.scalars.return_value = []
                return r
            return result

        s.execute = AsyncMock(side_effect=_execute)
        s.add_all = MagicMock(side_effect=lambda rows: captured.update(rows=rows))
        yield s

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session
    try:
        with patch("preference.train.load_latest", new=AsyncMock(return_value=None)):
            resp = client.get(f"/videos/{video.id}/clips", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    logged_clip_ids = [imp.clip_id for imp in captured["rows"]]
    assert engine_clip.id in logged_clip_ids
    assert creator_clip.id not in logged_clip_ids


def test_clip_response_engine_backcompat_origin_and_aspect():
    clip = MagicMock(spec=Clip)
    clip.triage = ClipTriage.pending
    clip.id = uuid.uuid4()
    clip.video_id = uuid.uuid4()
    clip.setup_start_s = 1.0
    clip.start_s = 0.0
    clip.end_s = 20.0
    clip.peak_s = 10.0
    clip.score = 0.8
    clip.rank = 1
    clip.signals_jsonb = {"principle": "Curiosity gap", "reasoning": "hook"}  # no origin key
    clip.render_status = RenderStatus.done
    clip.render_uri = "s3://b/c.mp4"
    clip.cleaned_render_uri = None
    clip.applied_title = None
    clip.suggested_title = None
    clip.suggested_description = None
    clip.suggested_hook = None
    clip.applied_description = None
    clip.style_preset = {"aspect": "1:1"}
    body = _clip_response(clip)
    assert body["origin"] == "engine"
    assert body["aspect"] == "1:1"
