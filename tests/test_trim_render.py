"""Tests for POST /clips/{clip_id}/trim-render (Wave-1 ready pass).

The endpoint turns the Review screen's CLIP-RELATIVE trim window into cut
segments for the existing ``edit_clip`` worker. Covers:
  - happy path 202 + exact trim→cuts inversion payload
  - trim_start_s=0 boundary → single right-edge cut
  - setup_start_s origin (clip_duration_s = end_s - setup_start_s)
  - 422 trim_noop on a full-clip trim
  - 422 kept_too_short / removed_too_much / out_of_bounds pass-through
  - 409 pending_clean_or_edit when cleaned_render_uri already set
  - 400 when the clip has no render_uri
  - per-creator isolation → 404
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clip_engine.edits import MIN_KEEP_SEGMENT_S
from tests._helpers import stub_get_owned


def _mock_creator():
    from models import Creator

    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    c.minutes_balance = 100
    return c


def _mock_clip(creator_id, duration_s=60.0):
    from models import Clip, RenderStatus

    clip = MagicMock(spec=Clip)
    clip.id = uuid.uuid4()
    clip.creator_id = creator_id
    clip.video_id = uuid.uuid4()
    clip.setup_start_s = 0.0
    clip.start_s = 0.0
    clip.end_s = duration_s
    clip.render_status = RenderStatus.done
    clip.render_uri = "clips/x.mp4"
    clip.cleaned_render_uri = None
    return clip


def _post_trim(client, clip, creator, body):
    """Shared request plumbing: overrides + worker patches, returns
    (response, mock edit_clip task)."""
    from auth import get_current_creator
    from db import get_session
    from main import app

    async def _session():
        s = AsyncMock()
        stub_get_owned(s, clip)
        yield s

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session
    with (
        patch("routers.review.check_positive_balance", AsyncMock(return_value=None)),
        patch("worker.tasks.edit_clip") as mock_task,
        patch("worker.progress.aset_owner", AsyncMock()),
    ):
        mock_task.delay.return_value = MagicMock(id="task-trim-1")
        try:
            resp = client.post(f"/clips/{clip.id}/trim-render", json=body)
        finally:
            app.dependency_overrides.clear()
    return resp, mock_task


def test_trim_render_happy_path_returns_202_with_inverted_cuts(client):
    creator = _mock_creator()
    clip = _mock_clip(creator.id, duration_s=60.0)

    resp, mock_task = _post_trim(client, clip, creator, {"trim_start_s": 5.0, "trim_end_s": 50.0})

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["task_id"] == "task-trim-1"
    assert body["status"] == "queued"
    assert body["stream_url"] == f"/tasks/{clip.id}/events"
    mock_task.delay.assert_called_once_with(str(clip.id), [[0.0, 5.0], [50.0, 60.0]])


def test_trim_render_start_zero_yields_single_right_cut(client):
    creator = _mock_creator()
    clip = _mock_clip(creator.id, duration_s=60.0)

    resp, mock_task = _post_trim(client, clip, creator, {"trim_start_s": 0.0, "trim_end_s": 50.0})

    assert resp.status_code == 202, resp.text
    mock_task.delay.assert_called_once_with(str(clip.id), [[50.0, 60.0]])


def test_trim_render_origin_is_setup_start_when_set(client):
    """Clip-relative timebase: origin = setup_start_s ?? start_s. With
    setup_start_s=10 and end_s=70, the rendered mp4 is 60s long — a trim to
    [5, 55] must cut [0,5] + [55,60], NOT use start_s as origin."""
    creator = _mock_creator()
    clip = _mock_clip(creator.id, duration_s=70.0)
    clip.setup_start_s = 10.0
    clip.start_s = 12.0
    clip.end_s = 70.0

    resp, mock_task = _post_trim(client, clip, creator, {"trim_start_s": 5.0, "trim_end_s": 55.0})

    assert resp.status_code == 202, resp.text
    mock_task.delay.assert_called_once_with(str(clip.id), [[0.0, 5.0], [55.0, 60.0]])


def test_trim_render_full_clip_trim_returns_422_trim_noop(client):
    creator = _mock_creator()
    clip = _mock_clip(creator.id, duration_s=60.0)

    resp, mock_task = _post_trim(
        client,
        clip,
        creator,
        {"trim_start_s": MIN_KEEP_SEGMENT_S / 2, "trim_end_s": 60.0},
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "trim_noop"
    mock_task.delay.assert_not_called()


@pytest.mark.parametrize(
    "body, expected_code, duration_s",
    [
        # 10s clip trimmed to 1s kept → removed_too_much binds first (90% > 85%).
        ({"trim_start_s": 8.0, "trim_end_s": 9.0}, "removed_too_much", 10.0),
        # 30s clip trimmed to 4.5s kept (85% cap not hit) → kept_too_short.
        ({"trim_start_s": 20.0, "trim_end_s": 24.5}, "kept_too_short", 30.0),
        # trim_end_s past the clip duration + one-frame tolerance.
        ({"trim_start_s": 5.0, "trim_end_s": 65.0}, "out_of_bounds", 60.0),
    ],
)
def test_trim_render_validation_returns_422_with_code(client, body, expected_code, duration_s):
    creator = _mock_creator()
    clip = _mock_clip(creator.id, duration_s=duration_s)

    resp, mock_task = _post_trim(client, clip, creator, body)

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == expected_code
    mock_task.delay.assert_not_called()


def test_trim_render_rejects_inverted_window_via_model_validator(client):
    """_validate_trim_pair (shared with FeedbackRequest) rejects start >= end
    at the pydantic layer — before any DB access."""
    creator = _mock_creator()
    clip = _mock_clip(creator.id, duration_s=60.0)

    resp, mock_task = _post_trim(client, clip, creator, {"trim_start_s": 50.0, "trim_end_s": 5.0})

    assert resp.status_code == 422, resp.text
    mock_task.delay.assert_not_called()


def test_trim_render_409_when_cleaned_render_uri_pending(client):
    creator = _mock_creator()
    clip = _mock_clip(creator.id, duration_s=60.0)
    clip.cleaned_render_uri = "clips/x_clean.mp4"

    resp, mock_task = _post_trim(client, clip, creator, {"trim_start_s": 5.0, "trim_end_s": 50.0})

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "pending_clean_or_edit"
    mock_task.delay.assert_not_called()


def test_trim_render_400_when_not_rendered(client):
    creator = _mock_creator()
    clip = _mock_clip(creator.id, duration_s=60.0)
    clip.render_uri = None

    resp, mock_task = _post_trim(client, clip, creator, {"trim_start_s": 5.0, "trim_end_s": 50.0})

    assert resp.status_code == 400, resp.text
    mock_task.delay.assert_not_called()


def test_trim_render_rejects_other_creators_clip(client):
    """Per-creator isolation: foreign clip → ownership-scoped select finds
    no row → 404 (not 403, to avoid leaking clip existence)."""
    creator = _mock_creator()
    other = _mock_creator()
    clip = _mock_clip(other.id, duration_s=60.0)

    from auth import get_current_creator
    from db import get_session
    from main import app

    async def _session():
        s = AsyncMock()
        stub_get_owned(s, None)
        yield s

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session
    try:
        with patch("routers.review.check_positive_balance", AsyncMock(return_value=None)):
            resp = client.post(
                f"/clips/{clip.id}/trim-render",
                json={"trim_start_s": 5.0, "trim_end_s": 50.0},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 404
