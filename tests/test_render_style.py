"""Tests for styled render endpoint (Issue 119)."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from auth import get_current_creator
from billing.ledger import check_positive_balance
from clip_engine.render import render_clip_file
from db import get_session
from main import app
from models import Clip, Creator, RenderStatus
from tests._helpers import stub_get_owned


def _mock_creator() -> MagicMock:
    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    c.minutes_balance = 100
    return c


def _mock_clip(creator_id: uuid.UUID, style: dict | None = None) -> MagicMock:
    clip = MagicMock(spec=Clip)
    clip.id = uuid.uuid4()
    clip.creator_id = creator_id
    clip.render_status = RenderStatus.pending
    clip.style_preset = style
    return clip


def _fake_session(clip: MagicMock):
    async def _session():
        session = AsyncMock()
        stub_get_owned(session, clip)
        session.commit = AsyncMock()
        yield session

    return _session


def test_render_endpoint_accepts_style_body(client):
    """POST /clips/{id}/render with style body returns 202 and persists style."""
    creator = _mock_creator()
    clip = _mock_clip(creator.id)
    captured_style = {}

    async def _fake_commit():
        captured_style.update(clip.style_preset or {})

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session(clip)
    app.dependency_overrides[check_positive_balance] = AsyncMock(return_value=None)

    with (
        patch("routers.clips.check_positive_balance", AsyncMock(return_value=None)),
        patch("worker.tasks.render_clip") as mock_task,
        patch("worker.progress.aset_owner", AsyncMock()),
    ):
        mock_task.delay.return_value = MagicMock(id="task-xyz")
        try:
            resp = client.post(
                f"/clips/{clip.id}/render",
                json={
                    "subtitle": "white_large",
                    "background": "blur",
                    "captions_enabled": False,
                    "zoom_on_peak": True,
                },
                cookies={"session": "x"},
            )
        finally:
            app.dependency_overrides.clear()

    assert resp.status_code == 202
    # The opt-in punch-in flag (Issue 184) must persist onto the clip's style_preset
    # (the endpoint merges the body into clip.style_preset before enqueuing).
    assert (clip.style_preset or {}).get("zoom_on_peak") is True


def test_render_endpoint_no_style_body_still_works(client):
    """POST /clips/{id}/render without a body still returns 202."""
    creator = _mock_creator()
    clip = _mock_clip(creator.id)

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session(clip)

    with (
        patch("routers.clips.check_positive_balance", AsyncMock(return_value=None)),
        patch("worker.tasks.render_clip") as mock_task,
        patch("worker.progress.aset_owner", AsyncMock()),
    ):
        mock_task.delay.return_value = MagicMock(id="task-abc")
        try:
            resp = client.post(
                f"/clips/{clip.id}/render",
                cookies={"session": "x"},
            )
        finally:
            app.dependency_overrides.clear()

    assert resp.status_code == 202


def test_render_endpoint_resets_done_clip_for_rerender(client):
    """Issue 353: a styled render on a `done` clip resets render state so the
    worker's redelivery guard doesn't no-op the re-render."""
    creator = _mock_creator()
    clip = _mock_clip(creator.id, style={"subtitle": "bold_pop"})
    clip.render_status = RenderStatus.done
    clip.render_uri = f"s3://test/clips/{clip.id}.mp4"

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session(clip)

    with (
        patch("routers.clips.check_positive_balance", AsyncMock(return_value=None)),
        patch("worker.tasks.render_clip") as mock_task,
        patch("worker.progress.aset_owner", AsyncMock()),
    ):
        mock_task.delay.return_value = MagicMock(id="task-rerender")
        try:
            resp = client.post(
                f"/clips/{clip.id}/render",
                json={"background": "blur"},
                cookies={"session": "x"},
            )
        finally:
            app.dependency_overrides.clear()

    assert resp.status_code == 202
    # Endpoint owns the re-render intent: state reset in the same transaction
    # as the merged style, then the task is enqueued.
    assert clip.render_status == RenderStatus.pending
    assert clip.render_uri is None
    assert clip.style_preset == {"subtitle": "bold_pop", "background": "blur"}
    mock_task.delay.assert_called_once_with(str(clip.id))


def test_render_endpoint_running_clip_still_409s(client):
    """Issue 353 keeps the existing guard: a render on a FRESH `running` clip is 409.

    Issue 359 scopes the guard: `running` only 409s while the render-start
    marker shows a live render (ais_render_stale → False here).
    """
    creator = _mock_creator()
    clip = _mock_clip(creator.id)
    clip.render_status = RenderStatus.running

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session(clip)

    with (
        patch("routers.clips.check_positive_balance", AsyncMock(return_value=None)),
        patch("worker.tasks.render_clip") as mock_task,
        patch("worker.tasks.ais_render_stale", AsyncMock(return_value=False)),
        patch("worker.progress.aset_owner", AsyncMock()),
    ):
        try:
            resp = client.post(
                f"/clips/{clip.id}/render",
                json={"subtitle": "white_large"},
                cookies={"session": "x"},
            )
        finally:
            app.dependency_overrides.clear()

    assert resp.status_code == 409
    mock_task.delay.assert_not_called()


def test_render_endpoint_stale_running_clip_allows_rerender(client):
    """Issue 359b: `running` past the hard time limit (orphaned by a worker
    SIGKILL) must NOT 409 forever — the stale override re-enqueues the render."""
    creator = _mock_creator()
    clip = _mock_clip(creator.id)
    clip.render_status = RenderStatus.running
    clip.render_uri = None

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session(clip)

    with (
        patch("routers.clips.check_positive_balance", AsyncMock(return_value=None)),
        patch("worker.tasks.render_clip") as mock_task,
        patch("worker.tasks.ais_render_stale", AsyncMock(return_value=True)),
        patch("worker.progress.aset_owner", AsyncMock()),
    ):
        mock_task.delay.return_value = MagicMock(id="task-stale-override")
        try:
            resp = client.post(
                f"/clips/{clip.id}/render",
                cookies={"session": "x"},
            )
        finally:
            app.dependency_overrides.clear()

    assert resp.status_code == 202
    mock_task.delay.assert_called_once_with(str(clip.id))


def test_render_endpoint_enqueue_failure_restores_done_state(client):
    """Issue 359c: a broker throw on `.delay()` must not destroy a watchable
    clip — the Issue-353 reset (pending + render_uri=None) is rolled back."""
    creator = _mock_creator()
    clip = _mock_clip(creator.id)
    clip.render_status = RenderStatus.done
    prior_uri = f"s3://test/clips/{clip.id}.mp4"
    clip.render_uri = prior_uri

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session(clip)

    with (
        patch("routers.clips.check_positive_balance", AsyncMock(return_value=None)),
        patch("worker.tasks.render_clip") as mock_task,
        patch("worker.progress.aset_owner", AsyncMock()),
    ):
        mock_task.delay.side_effect = RuntimeError("broker down")
        try:
            resp = client.post(
                f"/clips/{clip.id}/render",
                cookies={"session": "x"},
            )
        finally:
            app.dependency_overrides.clear()

    assert resp.status_code == 503
    # The previous render survives the failed enqueue.
    assert clip.render_status == RenderStatus.done
    assert clip.render_uri == prior_uri


def test_render_clip_file_passes_style_to_vf(tmp_path):
    """render_clip_file with bold_pop + transcript appends a subtitles= filter
    pointing at a generated ASS file (Issue 133)."""
    from pathlib import Path
    from unittest.mock import patch

    called_args = {}

    def _mock_run(cmd, label, timeout_s=120.0):
        called_args["cmd"] = cmd

    segments = [
        {
            "start": 10.0,
            "end": 12.0,
            "text": "hello world",
            "words": [
                {"word": "hello", "start": 10.0, "end": 10.5},
                {"word": "world", "start": 10.6, "end": 11.2},
            ],
        }
    ]
    out_path = tmp_path / "out.mp4"

    with (
        patch("clip_engine.render._run", _mock_run),
        patch("clip_engine.render._frame_dimensions", return_value=(1920, 1080)),
        patch("clip_engine.render._extract_keyframe"),
        patch("clip_engine.render._detect_face_center_x", return_value=960),
        patch("tempfile.NamedTemporaryFile") as mock_tmp,
    ):
        mock_tmp.return_value.__enter__ = lambda s: s
        mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
        mock_tmp.return_value.name = str(tmp_path / "kf.jpg")

        render_clip_file(
            source_path=Path("/fake/source.mp4"),
            start_s=10.0,
            end_s=40.0,
            out_path=out_path,
            style_preset={"subtitle": "bold_pop"},
            transcript_segments=segments,
        )

    vf_arg_index = called_args["cmd"].index("-vf")
    vf = called_args["cmd"][vf_arg_index + 1]
    assert "crop=" in vf
    assert "scale=" in vf
    assert "subtitles=" in vf
    assert ".bold_pop.ass" in vf
    assert "fontsdir=/usr/share/fonts/custom" in vf


def test_render_clip_file_unknown_style_skips_subtitles_filter(tmp_path):
    """A legacy/unknown subtitle key (e.g. the removed Issue-119 'white_large')
    leaves the vf string with just crop+scale — no subtitles= filter."""
    from pathlib import Path
    from unittest.mock import patch

    called_args = {}

    def _mock_run(cmd, label, timeout_s=120.0):
        called_args["cmd"] = cmd

    with (
        patch("clip_engine.render._run", _mock_run),
        patch("clip_engine.render._frame_dimensions", return_value=(1920, 1080)),
        patch("clip_engine.render._extract_keyframe"),
        patch("clip_engine.render._detect_face_center_x", return_value=960),
        patch("tempfile.NamedTemporaryFile") as mock_tmp,
    ):
        mock_tmp.return_value.__enter__ = lambda s: s
        mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
        mock_tmp.return_value.name = str(tmp_path / "kf.jpg")

        render_clip_file(
            source_path=Path("/fake/source.mp4"),
            start_s=10.0,
            end_s=40.0,
            out_path=tmp_path / "out.mp4",
            style_preset={"subtitle": "white_large"},
        )

    vf_arg_index = called_args["cmd"].index("-vf")
    vf = called_args["cmd"][vf_arg_index + 1]
    assert "subtitles=" not in vf
    assert "drawtext" not in vf


def test_render_clip_file_no_style_unchanged(tmp_path):
    """render_clip_file with style_preset=None produces vf without subtitles=."""
    from pathlib import Path
    from unittest.mock import patch

    called_args = {}

    def _mock_run(cmd, label, timeout_s=120.0):
        called_args["cmd"] = cmd

    with (
        patch("clip_engine.render._run", _mock_run),
        patch("clip_engine.render._frame_dimensions", return_value=(1920, 1080)),
        patch("clip_engine.render._extract_keyframe"),
        patch("clip_engine.render._detect_face_center_x", return_value=960),
        patch("tempfile.NamedTemporaryFile") as mock_tmp,
    ):
        mock_tmp.return_value.__enter__ = lambda s: s
        mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
        mock_tmp.return_value.name = str(tmp_path / "kf.jpg")

        render_clip_file(
            source_path=Path("/fake/source.mp4"),
            start_s=0.0,
            end_s=30.0,
            out_path=tmp_path / "out.mp4",
            style_preset=None,
        )

    vf_arg_index = called_args["cmd"].index("-vf")
    vf = called_args["cmd"][vf_arg_index + 1]
    assert "subtitles=" not in vf
    assert "drawtext" not in vf


def test_render_endpoint_409_when_source_expired(client):
    """Issue 362: a render request for a clip whose source video was purged by
    the retention sweep must 409 with the re-upload message BEFORE enqueuing —
    not enqueue a render that can only fail permanently."""
    creator = _mock_creator()
    clip = _mock_clip(creator.id)
    clip.video_id = uuid.uuid4()

    expired_video = MagicMock()
    expired_video.source_uri = None

    async def _session():
        session = AsyncMock()
        stub_get_owned(session, clip)
        session.get = AsyncMock(return_value=expired_video)
        yield session

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session

    with (
        patch("routers.clips.check_positive_balance", AsyncMock(return_value=None)),
        patch("worker.tasks.render_clip") as mock_task,
    ):
        try:
            resp = client.post(f"/clips/{clip.id}/render", cookies={"session": "x"})
        finally:
            app.dependency_overrides.clear()

    assert resp.status_code == 409
    assert "re-upload" in resp.json()["detail"]
    mock_task.delay.assert_not_called()
