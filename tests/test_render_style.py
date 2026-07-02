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
