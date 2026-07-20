"""Tests for Issue 192 — stream-VOD recap summaries API (routers/clips.py).

Unit lane: DB/Redis mocked at the session boundary; selection
(clip_engine.summary_select) runs for real — it is pure. Covers:
  - 202 enqueue happy path + idempotent re-POST (no duplicate render job)
  - concurrent double-POST loser (uq_summaries_active IntegrityError → winner)
  - origin != upload → honest 400
  - purged source (72h retention) → 409
  - cross-creator video/summary → 404 (never 403)
  - no scored material → honest 422
  - download 404 until render_uri lands; presigned 302 once rendered
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.exc import IntegrityError

from auth import get_current_creator
from db import get_session
from main import app
from models import (
    Clip,
    Creator,
    IngestStatus,
    RenderStatus,
    Signals,
    Summary,
    SummaryStatus,
    Video,
    VideoOrigin,
)
from tests._helpers import override_current_creator, owned_lookup_result, where_criteria

# ── Fixture helpers ────────────────────────────────────────────────────────────


def _creator() -> MagicMock:
    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    return c


def _video(
    creator_id: uuid.UUID,
    *,
    origin: VideoOrigin = VideoOrigin.upload,
    ingest_status: IngestStatus = IngestStatus.done,
    source_uri: str | None = "s3://bucket/source/v.mp4",
) -> MagicMock:
    v = MagicMock(spec=Video)
    v.id = uuid.uuid4()
    v.creator_id = creator_id
    v.origin = origin
    v.ingest_status = ingest_status
    v.source_uri = source_uri
    v.duration_s = 1800.0
    return v


def _clip(*, start_s: float, end_s: float, score: float) -> MagicMock:
    c = MagicMock(spec=Clip)
    c.setup_start_s = start_s
    c.start_s = start_s + 2.0
    c.end_s = end_s
    c.score = score
    c.signals_jsonb = {"principle": "HOOK_SETUP", "reasoning": "Clear setup into a payoff."}
    return c


def _summary(
    creator_id: uuid.UUID,
    *,
    render_status: RenderStatus = RenderStatus.pending,
    render_uri: str | None = None,
) -> MagicMock:
    s = MagicMock(spec=Summary)
    s.id = uuid.uuid4()
    s.creator_id = creator_id
    s.video_id = uuid.uuid4()
    s.status = SummaryStatus.ready
    s.render_status = render_status
    s.render_uri = render_uri
    s.target_duration_s = 600
    s.segments = [
        {
            "start_s": 10.0,
            "end_s": 70.0,
            "score": 0.8,
            "principle": "HOOK_SETUP",
            "rationale": "Clear setup into a payoff.",
        }
    ]
    return s


def _fake_session(
    *,
    video: MagicMock | None = None,
    clips: list | None = None,
    existing_summary: MagicMock | None = None,
    summary: MagicMock | None = None,
    signals: MagicMock | None = None,
    commit_exc: Exception | None = None,
    reselect_summary: MagicMock | None = None,
):
    """Async get_session override. Ownership fetches (Video/Summary) go through
    the get_owned single-shot select (Issue 109e) — emulated with
    owned_lookup_result so foreign rows genuinely miss; other execute calls
    return the clip list (POST) or summary list (GET); session.get still serves
    the non-ownership Signals fetch; scalar returns the idempotency probe.
    ``commit_exc`` makes commit raise (the uq_summaries_active loser path,
    Issue 361); the post-rollback re-select then serves ``reselect_summary``."""

    async def _session():
        session = AsyncMock()

        async def _get(model, pk, **kwargs):
            if model is Signals:
                return signals
            return None

        session.get = AsyncMock(side_effect=_get)
        if commit_exc is not None:
            session.commit = AsyncMock(side_effect=commit_exc)
            session.scalar = AsyncMock(side_effect=[existing_summary, reselect_summary])
        else:
            session.scalar = AsyncMock(return_value=existing_summary)

        async def _execute(stmt, *a, **kw):
            entity = stmt.column_descriptions[0]["type"]
            if "id" in where_criteria(stmt):  # get_owned ownership select
                return owned_lookup_result(stmt, video if entity is Video else summary)
            result = MagicMock()
            result.scalars.return_value = iter(clips or [])
            return result

        session.execute = AsyncMock(side_effect=_execute)

        async def _refresh(obj, *a, **kw):
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

        session.refresh = AsyncMock(side_effect=_refresh)
        yield session

    return _session


def _set_overrides(creator: MagicMock, session_factory) -> None:
    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = session_factory


def _post(client, video_id):
    return client.post(f"/videos/{video_id}/summaries", cookies={"session": "x"})


# ── POST /videos/{id}/summaries ────────────────────────────────────────────────


def test_create_summary_enqueues_render_202(client):
    creator = _creator()
    video = _video(creator.id)
    clips = [_clip(start_s=10, end_s=70, score=0.9), _clip(start_s=200, end_s=260, score=0.7)]
    _set_overrides(creator, _fake_session(video=video, clips=clips))

    fake_task = MagicMock()
    fake_task.id = "task-1"
    with (
        patch("routers.clips.check_positive_balance", new=AsyncMock()),
        patch("dna.profile.get_active", new=AsyncMock(return_value=None)),
        patch("worker.tasks.render_summary") as task_mock,
        patch("worker.progress.aset_owner", new=AsyncMock()),
    ):
        task_mock.delay.return_value = fake_task
        resp = _post(client, video.id)

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "queued"
    assert body["stream_url"] == f"/tasks/{body['summary_id']}/events"
    task_mock.delay.assert_called_once_with(body["summary_id"])


def test_create_summary_idempotent_repost_returns_existing(client):
    """A pending/running Summary for the video is returned — no duplicate job."""
    creator = _creator()
    video = _video(creator.id)
    existing = _summary(creator.id, render_status=RenderStatus.running)
    _set_overrides(creator, _fake_session(video=video, existing_summary=existing))

    with (
        patch("routers.clips.check_positive_balance", new=AsyncMock()),
        patch("worker.tasks.render_summary") as task_mock,
    ):
        resp = _post(client, video.id)

    assert resp.status_code == 202, resp.text
    assert resp.json()["summary_id"] == str(existing.id)
    task_mock.delay.assert_not_called()


def test_create_summary_commit_race_returns_winner(client):
    """Concurrent double-POST (Issue 361): the loser's commit violates the
    uq_summaries_active partial index → rollback, return the winner's summary,
    and enqueue NO duplicate render_summary job."""
    creator = _creator()
    video = _video(creator.id)
    clips = [_clip(start_s=10, end_s=70, score=0.9), _clip(start_s=200, end_s=260, score=0.7)]
    winner = _summary(creator.id, render_status=RenderStatus.pending)
    _set_overrides(
        creator,
        _fake_session(
            video=video,
            clips=clips,
            commit_exc=IntegrityError("stmt", {}, Exception("uq_summaries_active")),
            reselect_summary=winner,
        ),
    )

    with (
        patch("routers.clips.check_positive_balance", new=AsyncMock()),
        patch("dna.profile.get_active", new=AsyncMock(return_value=None)),
        patch("worker.tasks.render_summary") as task_mock,
    ):
        resp = _post(client, video.id)

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["summary_id"] == str(winner.id)
    assert body["stream_url"] == f"/tasks/{winner.id}/events"
    task_mock.delay.assert_not_called()


def test_create_summary_commit_race_winner_gone_409(client):
    """Loser's commit conflicts but the winner already left pending/running —
    a clean 409, never a duplicate enqueue or a raw 500."""
    creator = _creator()
    video = _video(creator.id)
    clips = [_clip(start_s=10, end_s=70, score=0.9)]
    _set_overrides(
        creator,
        _fake_session(
            video=video,
            clips=clips,
            commit_exc=IntegrityError("stmt", {}, Exception("uq_summaries_active")),
        ),
    )

    with (
        patch("routers.clips.check_positive_balance", new=AsyncMock()),
        patch("dna.profile.get_active", new=AsyncMock(return_value=None)),
        patch("worker.tasks.render_summary") as task_mock,
    ):
        resp = _post(client, video.id)

    assert resp.status_code == 409
    task_mock.delay.assert_not_called()


def test_create_summary_non_upload_origin_400(client):
    creator = _creator()
    video = _video(creator.id, origin=VideoOrigin.link)
    _set_overrides(creator, _fake_session(video=video))

    with patch("routers.clips.check_positive_balance", new=AsyncMock()):
        resp = _post(client, video.id)

    assert resp.status_code == 400
    assert "Terms of Service" in resp.json()["detail"]


def test_create_summary_purged_source_409(client):
    creator = _creator()
    video = _video(creator.id, source_uri=None)
    _set_overrides(creator, _fake_session(video=video))

    with patch("routers.clips.check_positive_balance", new=AsyncMock()):
        resp = _post(client, video.id)

    assert resp.status_code == 409
    assert "re-upload" in resp.json()["detail"]


def test_create_summary_cross_creator_404(client):
    creator = _creator()
    video = _video(uuid.uuid4())  # owned by someone else
    _set_overrides(creator, _fake_session(video=video))

    with patch("routers.clips.check_positive_balance", new=AsyncMock()):
        resp = _post(client, video.id)

    assert resp.status_code == 404


def test_create_summary_no_scored_material_422(client):
    """Zero scored clips → empty selection → honest 422, nothing persisted."""
    creator = _creator()
    video = _video(creator.id)
    _set_overrides(creator, _fake_session(video=video, clips=[]))

    with (
        patch("routers.clips.check_positive_balance", new=AsyncMock()),
        patch("worker.tasks.render_summary") as task_mock,
    ):
        resp = _post(client, video.id)

    assert resp.status_code == 422
    assert "Not enough scored material" in resp.json()["detail"]
    task_mock.delay.assert_not_called()


# ── GET list / single ──────────────────────────────────────────────────────────


def test_list_summaries_creator_scoped(client):
    creator = _creator()
    video = _video(creator.id)
    s = _summary(creator.id, render_status=RenderStatus.done, render_uri="s3://b/r.mp4")
    s.created_at = None
    _set_overrides(creator, _fake_session(video=video, clips=[s]))

    resp = client.get(f"/videos/{video.id}/summaries", cookies={"session": "x"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["summaries"]) == 1
    row = body["summaries"][0]
    assert row["render_status"] == "done"
    assert row["segments"][0]["principle"] == "HOOK_SETUP"


def test_get_summary_foreign_creator_404(client):
    creator = _creator()
    foreign = _summary(uuid.uuid4())
    _set_overrides(creator, _fake_session(summary=foreign))

    resp = client.get(f"/summaries/{foreign.id}", cookies={"session": "x"})
    assert resp.status_code == 404


# ── Download ───────────────────────────────────────────────────────────────────


def test_download_summary_404_until_rendered(client):
    creator = _creator()
    s = _summary(creator.id, render_uri=None)
    _set_overrides(creator, _fake_session(summary=s))

    resp = client.get(f"/summaries/{s.id}/download", cookies={"session": "x"})
    assert resp.status_code == 404


def test_download_summary_redirects_to_presigned(client):
    creator = _creator()
    s = _summary(creator.id, render_status=RenderStatus.done, render_uri="s3://b/summaries/x.mp4")
    _set_overrides(creator, _fake_session(summary=s))

    with patch("routers.clips.presigned_download_url", return_value="https://signed/recap"):
        resp = client.get(
            f"/summaries/{s.id}/download?disposition=inline",
            cookies={"session": "x"},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://signed/recap"
