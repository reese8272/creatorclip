"""
Unit tests for clip_engine/render.py.

ffmpeg / ffprobe / cv2 calls are patched — no video files needed.
"""

import logging
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("cv2")  # skip whole file if libGL is absent on this host

from clip_engine import camera_region as _camera_region
from clip_engine.overlay_bands import OVERLAY_SPANS_VERSION
from clip_engine.render import (
    _detect_face_center_x,
    _extract_keyframe,
    _frame_dimensions,
    _measure_loudnorm_filter,
    _parse_loudnorm_stats,
    _run,
    build_summary_filtergraph,
    build_summary_render_cmd,
    render_cleaned_clip_file,
    render_clip_file,
    render_summary_file,
)

# A representative loudnorm JSON block as printed to stderr by
# `loudnorm=...:print_format=json`. input_i is well above the silence floor.
_LOUDNORM_JSON = """[Parsed_loudnorm_0 @ 0x55]
{
\t"input_i" : "-27.85",
\t"input_tp" : "-12.34",
\t"input_lra" : "5.20",
\t"input_thresh" : "-38.12",
\t"output_i" : "-14.00",
\t"output_tp" : "-1.50",
\t"output_lra" : "5.00",
\t"output_thresh" : "-24.61",
\t"normalization_type" : "dynamic",
\t"target_offset" : "0.30"
}
"""

# ── _run: filter-configuration errors are terminal (Issue 467) ────────────────


@pytest.mark.parametrize(
    "signature",
    [
        "Error when evaluating the expression 'ih/(1+0.08*max(0,1-abs(t-1.000)/0.6))'",
        "Error reinitializing filters!",
        "Error initializing filter 'crop' with args 'w=iw/nan'",
        "No such filter: 'zoompan'",
    ],
)
def test_run_filter_config_error_raises_valueerror(signature):
    """A broken filtergraph is deterministic — the same argv fails identically on
    every attempt — so it must map to ValueError, which render_clip treats as
    permanent. Before Issue 467 it was RuntimeError → 3 pointless retries."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=234, stdout="", stderr=f"frame=0\n{signature}\nConversion failed!\n"
        )
        with pytest.raises(ValueError, match="filter-configuration"):
            _run(["ffmpeg", "-i", "in.mp4", "out.mp4"], "render")


def test_run_plain_failure_stays_runtimeerror():
    """Failures WITHOUT a filter-config signature keep RuntimeError (transient →
    retried): an I/O blip must not become a permanent clip failure."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="av_interleaved_write_frame(): I/O error\n"
        )
        with pytest.raises(RuntimeError, match="ffmpeg render failed"):
            _run(["ffmpeg", "-i", "in.mp4", "out.mp4"], "render")


# ── _frame_dimensions ─────────────────────────────────────────────────────────


def test_frame_dimensions_parses_output(tmp_path):
    fake = tmp_path / "v.mp4"
    fake.touch()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="1920,1080\n", stderr="")
        w, h = _frame_dimensions(fake)
    assert w == 1920 and h == 1080


def test_frame_dimensions_defaults_on_bad_output(tmp_path):
    fake = tmp_path / "v.mp4"
    fake.touch()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="bad", stderr="")
        w, h = _frame_dimensions(fake)
    assert w == 1920 and h == 1080


# ── _extract_keyframe ─────────────────────────────────────────────────────────


def test_extract_keyframe_calls_ffmpeg(tmp_path):
    src = tmp_path / "v.mp4"
    src.touch()
    kf = tmp_path / "kf.jpg"
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        _extract_keyframe(src, 30.0, kf)
    cmd = mock_run.call_args[0][0]
    assert "ffmpeg" in cmd
    assert "30.0" in cmd
    assert str(kf) in cmd


def test_extract_keyframe_raises_on_failure(tmp_path):
    src = tmp_path / "v.mp4"
    src.touch()
    kf = tmp_path / "kf.jpg"
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="Error")
        with pytest.raises(RuntimeError, match="ffmpeg"):
            _extract_keyframe(src, 30.0, kf)


# ── _detect_face_center_x ─────────────────────────────────────────────────────


def test_detect_face_center_x_with_face(tmp_path):
    kf = tmp_path / "kf.jpg"
    kf.touch()
    import numpy as np

    fake_img = np.zeros((1080, 1920, 3), dtype="uint8")

    with (
        patch("cv2.imread", return_value=fake_img),
        patch("cv2.CascadeClassifier") as mock_cascade_cls,
    ):
        mock_cascade = MagicMock()
        # Detected face at x=800, y=200, w=200, h=200
        mock_cascade.detectMultiScale.return_value = [(800, 200, 200, 200)]
        mock_cascade_cls.return_value = mock_cascade
        cx = _detect_face_center_x(kf, 1920)

    assert cx == 800 + 100  # x + w//2


def test_detect_face_center_x_no_face_returns_center(tmp_path):
    kf = tmp_path / "kf.jpg"
    kf.touch()
    import numpy as np

    fake_img = np.zeros((1080, 1920, 3), dtype="uint8")

    with (
        patch("cv2.imread", return_value=fake_img),
        patch("cv2.CascadeClassifier") as mock_cascade_cls,
    ):
        mock_cascade = MagicMock()
        mock_cascade.detectMultiScale.return_value = []
        mock_cascade_cls.return_value = mock_cascade
        cx = _detect_face_center_x(kf, 1920)

    assert cx == 960  # 1920 // 2


def test_detect_face_center_x_fallback_on_exception(tmp_path):
    kf = tmp_path / "kf.jpg"
    kf.touch()
    with patch("cv2.imread", side_effect=Exception("cv2 broken")):
        cx = _detect_face_center_x(kf, 1920)
    assert cx == 960


# ── render_clip_file ──────────────────────────────────────────────────────────


def test_render_clip_file_raises_on_invalid_range(tmp_path):
    src = tmp_path / "v.mp4"
    src.touch()
    out = tmp_path / "out.mp4"
    with pytest.raises(ValueError, match="Invalid clip range"):
        render_clip_file(src, start_s=50.0, end_s=30.0, out_path=out)


def test_render_clip_file_calls_ffmpeg_with_crop(tmp_path):
    src = tmp_path / "v.mp4"
    src.touch()
    out = tmp_path / "out.mp4"

    import numpy as np

    fake_img = np.zeros((1080, 1920, 3), dtype="uint8")

    def fake_subprocess_run(cmd, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stdout = "1920,1080\n"
        m.stderr = ""
        return m

    with (
        patch("subprocess.run", side_effect=fake_subprocess_run),
        patch("cv2.imread", return_value=fake_img),
        patch("cv2.CascadeClassifier") as mock_cc,
    ):
        mock_cc.return_value.detectMultiScale.return_value = []
        render_clip_file(src, start_s=10.0, end_s=70.0, out_path=out)

    # subprocess.run was called (ffprobe, keyframe extract, render)
    # Just verify it ran without error (no assertion on exact call count needed)


def test_render_clip_file_crop_centers_on_face(tmp_path):
    """Face at x=1400 should push the crop right, not stay at center."""
    src = tmp_path / "v.mp4"
    src.touch()
    out = tmp_path / "out.mp4"

    import numpy as np

    fake_img = np.zeros((1080, 1920, 3), dtype="uint8")
    captured_cmds: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        m = MagicMock()
        m.returncode = 0
        m.stdout = "1920,1080\n"
        m.stderr = ""
        return m

    with (
        patch("subprocess.run", side_effect=fake_run),
        patch("cv2.imread", return_value=fake_img),
        patch("cv2.CascadeClassifier") as mock_cc,
    ):
        # Face centered at x=1400+100=1500
        mock_cc.return_value.detectMultiScale.return_value = [(1400, 300, 200, 200)]
        render_clip_file(src, start_s=10.0, end_s=70.0, out_path=out)

    # Find the render ffmpeg call (has -vf crop=...)
    render_cmd = next((c for c in captured_cmds if "-vf" in c), None)
    assert render_cmd is not None
    vf_idx = render_cmd.index("-vf")
    vf_arg = render_cmd[vf_idx + 1]
    # x_offset should not be the default center (960-304=656); face at 1500 pushes it right
    assert "crop=" in vf_arg


def test_render_clip_file_uses_accurate_seek_before_input(tmp_path):
    """The cut must be frame-accurate: `-accurate_seek` after `-ss`, before `-i` (Issue 59)."""
    src = tmp_path / "v.mp4"
    src.touch()
    out = tmp_path / "out.mp4"

    import numpy as np

    fake_img = np.zeros((1080, 1920, 3), dtype="uint8")
    captured_cmds: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        m = MagicMock()
        m.returncode = 0
        m.stdout = "1920,1080\n"
        m.stderr = ""
        return m

    with (
        patch("subprocess.run", side_effect=fake_run),
        patch("cv2.imread", return_value=fake_img),
        patch("cv2.CascadeClassifier") as mock_cc,
    ):
        mock_cc.return_value.detectMultiScale.return_value = []
        render_clip_file(src, start_s=10.0, end_s=70.0, out_path=out)

    render_cmd = next((c for c in captured_cmds if "-vf" in c), None)
    assert render_cmd is not None
    # -accurate_seek must come after -ss and before -i (it is an input option).
    assert "-accurate_seek" in render_cmd
    assert render_cmd.index("-accurate_seek") < render_cmd.index("-i")
    assert render_cmd.index("-ss") < render_cmd.index("-accurate_seek")


# ── _render_start_for: render from setup_start_s, not the start_s clamp (Issue 59) ──


def test_render_start_uses_setup_start_when_present():
    from models import Clip
    from worker.tasks import _render_start_for

    clip = Clip(setup_start_s=12.0, start_s=40.0, end_s=72.0)
    # Must render from the computed setup boundary, NOT the peak−window fallback.
    assert _render_start_for(clip) == 12.0


def test_render_start_falls_back_to_start_s_when_setup_missing():
    from models import Clip
    from worker.tasks import _render_start_for

    clip = Clip(setup_start_s=None, start_s=40.0, end_s=72.0)
    assert _render_start_for(clip) == 40.0


# ── timeout tests ─────────────────────────────────────────────────────────────


def test_run_raises_runtime_error_on_timeout():
    """`_run` converts `subprocess.TimeoutExpired` into a `RuntimeError` with message."""
    with (
        patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=120),
        ),
        pytest.raises(RuntimeError, match="timed out after 120"),
    ):
        _run(["ffmpeg", "-version"], "test label", timeout_s=120)


def test_frame_dimensions_raises_on_timeout(tmp_path):
    """`_frame_dimensions` raises `RuntimeError` when ffprobe hangs."""
    fake = tmp_path / "v.mp4"
    fake.touch()
    with (
        patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["ffprobe"], timeout=30),
        ),
        pytest.raises(RuntimeError, match="ffprobe timed out after 30s"),
    ):
        _frame_dimensions(fake)


# ── loudness normalization (Issue 181) ────────────────────────────────────────


def test_parse_loudnorm_stats_extracts_json():
    stats = _parse_loudnorm_stats(_LOUDNORM_JSON)
    assert stats is not None
    assert stats["input_i"] == "-27.85"
    assert stats["target_offset"] == "0.30"


def test_parse_loudnorm_stats_returns_none_on_garbage():
    assert _parse_loudnorm_stats("ffmpeg error: no such file") is None
    assert _parse_loudnorm_stats("") is None


def test_measure_loudnorm_filter_bakes_in_measured_values():
    """A non-silent measurement yields a second-pass filter with measured_* + linear."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr=_LOUDNORM_JSON)
        flt = _measure_loudnorm_filter(["ffmpeg", "..."], "render", 120.0)
    assert flt is not None
    assert "loudnorm=I=-14:TP=-1.5:LRA=11" in flt
    assert "measured_I=-27.85" in flt
    assert "linear=true" in flt


def test_measure_loudnorm_filter_skips_near_silent():
    """Integrated loudness at/below the floor → None (don't amplify hiss)."""
    silent = _LOUDNORM_JSON.replace('"input_i" : "-27.85"', '"input_i" : "-65.00"')
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr=silent)
        flt = _measure_loudnorm_filter(["ffmpeg", "..."], "render", 120.0)
    assert flt is None


def test_measure_loudnorm_filter_degrades_on_subprocess_failure():
    """A measurement that can't run (e.g. ffmpeg missing) → None, never raises."""
    with patch("subprocess.run", side_effect=OSError("ffmpeg not found")):
        assert _measure_loudnorm_filter(["ffmpeg", "..."], "render", 120.0) is None


def _fake_run_with_loudnorm(input_i: str = "-27.85"):
    """subprocess.run double: ffprobe dims via stdout, loudnorm stats via stderr."""
    stderr = _LOUDNORM_JSON.replace('"input_i" : "-27.85"', f'"input_i" : "{input_i}"')

    def _fake(cmd, **kwargs):
        return MagicMock(returncode=0, stdout="1920,1080\n", stderr=stderr)

    return _fake


def test_render_clip_file_applies_loudnorm_when_measured(tmp_path):
    src = tmp_path / "v.mp4"
    src.touch()
    out = tmp_path / "out.mp4"

    import numpy as np

    fake_img = np.zeros((1080, 1920, 3), dtype="uint8")
    captured: list[list[str]] = []

    def _fake(cmd, **kwargs):
        captured.append(cmd)
        return MagicMock(returncode=0, stdout="1920,1080\n", stderr=_LOUDNORM_JSON)

    with (
        patch("subprocess.run", side_effect=_fake),
        patch("cv2.imread", return_value=fake_img),
        patch("cv2.CascadeClassifier") as mock_cc,
    ):
        mock_cc.return_value.detectMultiScale.return_value = []
        render_clip_file(src, start_s=10.0, end_s=70.0, out_path=out)

    render_cmd = next((c for c in captured if "-vf" in c), None)
    assert render_cmd is not None
    assert "-af" in render_cmd
    af_arg = render_cmd[render_cmd.index("-af") + 1]
    assert "loudnorm=" in af_arg and "measured_I=-27.85" in af_arg


def test_render_clip_file_skips_loudnorm_when_silent(tmp_path):
    src = tmp_path / "v.mp4"
    src.touch()
    out = tmp_path / "out.mp4"

    import numpy as np

    fake_img = np.zeros((1080, 1920, 3), dtype="uint8")
    captured: list[list[str]] = []

    def _fake(cmd, **kwargs):
        captured.append(cmd)
        stderr = _LOUDNORM_JSON.replace('"input_i" : "-27.85"', '"input_i" : "-70.00"')
        return MagicMock(returncode=0, stdout="1920,1080\n", stderr=stderr)

    with (
        patch("subprocess.run", side_effect=_fake),
        patch("cv2.imread", return_value=fake_img),
        patch("cv2.CascadeClassifier") as mock_cc,
    ):
        mock_cc.return_value.detectMultiScale.return_value = []
        render_clip_file(src, start_s=10.0, end_s=70.0, out_path=out)

    render_cmd = next((c for c in captured if "-vf" in c), None)
    assert render_cmd is not None
    assert "-af" not in render_cmd  # near-silent → no normalization


def test_render_cleaned_clip_file_chains_loudnorm_into_graph(tmp_path):
    """When measurement succeeds the loudnorm filter is chained after concat and
    the audio map points at the normalized output label."""
    captured = {}

    def _fake_run(cmd, label, timeout_s=120.0):
        captured["cmd"] = cmd
        captured["script"] = Path(cmd[cmd.index("-filter_complex_script") + 1]).read_text()

    measured = "loudnorm=I=-14:TP=-1.5:LRA=11:measured_I=-20.0:linear=true"
    with (
        patch("clip_engine.render._run", _fake_run),
        patch("clip_engine.render._measure_loudnorm_filter", return_value=measured),
    ):
        render_cleaned_clip_file(
            source_path=Path("/fake/src.mp4"),
            keep_ranges=[(0.0, 2.5), (3.0, 8.0)],
            out_path=tmp_path / "out.mp4",
        )

    script = captured["script"]
    assert f";[outa]{measured}[outaln]" in script
    # The render must map the normalized audio, not the raw concat output.
    cmd = captured["cmd"]
    assert "[outaln]" in cmd


# ── auto-zoom punch-in at peak (Issue 184, rebuilt Issue 467) ─────────────────

# The punch-in is an animated scale (eval=frame) + constant trailing crop;
# ":eval=frame,crop=" appears nowhere else in any chain, so it is the unique
# punch-in marker.
_PUNCH_MARKER = ":eval=frame,crop="


def _render_vf(
    tmp_path,
    *,
    style_preset=None,
    peak_s=None,
    video_camera_region=None,
    video_overlay_spans=None,
) -> str:
    """Render with mocked ffmpeg/cv2 and return the -vf filter string."""
    src = tmp_path / "v.mp4"
    src.touch()
    out = tmp_path / "out.mp4"
    import numpy as np

    fake_img = np.zeros((1080, 1920, 3), dtype="uint8")
    captured: list[list[str]] = []

    def _fake(cmd, **kwargs):
        captured.append(cmd)
        return MagicMock(returncode=0, stdout="1920,1080\n", stderr="")

    with (
        patch("subprocess.run", side_effect=_fake),
        patch("cv2.imread", return_value=fake_img),
        patch("cv2.CascadeClassifier") as mock_cc,
    ):
        mock_cc.return_value.detectMultiScale.return_value = []
        render_clip_file(
            src,
            start_s=10.0,
            end_s=70.0,
            out_path=out,
            style_preset=style_preset,
            peak_s=peak_s,
            video_camera_region=video_camera_region,
            video_overlay_spans=video_overlay_spans,
        )
    render_cmd = next((c for c in captured if "-vf" in c), None)
    assert render_cmd is not None
    return render_cmd[render_cmd.index("-vf") + 1]


def test_punch_in_applied_when_enabled_and_peak_in_window(tmp_path):
    vf = _render_vf(tmp_path, style_preset={"zoom_on_peak": True}, peak_s=40.0)
    assert _PUNCH_MARKER in vf
    assert "abs(t-30.000)" in vf  # peak centered at peak_s - start_s = 30s
    # The trailing crop is CONSTANT (config-time-legal) — crop w/h expressions
    # are evaluated once at filter configuration, where `t` is NaN (Issue 467).
    assert vf.endswith(",crop=1080:1920")
    punch = vf[vf.index("scale=w='trunc(") :]
    assert "crop=w=" not in punch and "crop=h=" not in punch


def test_no_punch_in_when_disabled(tmp_path):
    vf = _render_vf(tmp_path, style_preset={"zoom_on_peak": False}, peak_s=40.0)
    assert _PUNCH_MARKER not in vf


def test_no_punch_in_when_peak_missing(tmp_path):
    vf = _render_vf(tmp_path, style_preset={"zoom_on_peak": True}, peak_s=None)
    assert _PUNCH_MARKER not in vf


def test_no_punch_in_when_peak_outside_window(tmp_path):
    # peak_s=200 → offset 190s > 60s clip duration → skipped.
    vf = _render_vf(tmp_path, style_preset={"zoom_on_peak": True}, peak_s=200.0)
    assert _PUNCH_MARKER not in vf


# ── Issue 469: ONE duration authority (ingest probe → candidates → render) ────
# These tests live in the render lane's file for W4: the natural homes
# (tests/test_worker_pipeline.py, tests/test_sentence_snap.py) are owned by
# sibling lanes this wave.


def test_probed_duration_always_overwrites_youtube_seed():
    """link_video seeds duration_s from YouTube's ISO-8601 INTEGER seconds
    (routers/videos.py) and nothing ever corrected it. The ffprobe container
    duration is the ONE authority the render enforces (Issue 469), so the
    ingest probe must overwrite a non-empty row, not only fill an empty one."""
    from types import SimpleNamespace

    from models import VideoKind
    from worker.tasks import _apply_probed_duration

    video = SimpleNamespace(duration_s=180.0, kind=VideoKind.short)
    _apply_probed_duration(video, 200.6)  # type: ignore[arg-type]
    assert video.duration_s == 200.6
    assert video.kind == VideoKind.long  # reclassified alongside (probe > 180s cap)


def test_vfr_container_shorter_than_audio_clamps_candidates():
    """VFR-shaped source (Issue 469): the librosa audio timeline runs to 120 s
    but the ffprobe container — the duration the render enforces — is 100 s.
    Candidate end_s must be clamped to the container, and a clamp that strands
    the peak outside the window must fall through W2's repair-or-drop (456)."""
    from clip_engine.sentence_snap import snap_candidates_to_sentences

    def _seg(start, end, specs):
        return {
            "start": start,
            "end": end,
            "text": " ".join(w for w, _, _ in specs),
            "words": [{"word": w, "start": s, "end": e} for w, s, e in specs],
        }

    segments = [
        _seg(0.0, 29.5, [("Intro", 0.0, 0.5), ("beat.", 0.6, 29.5)]),
        _seg(30.0, 110.0, [("Main", 30.0, 30.5), ("beat.", 30.6, 110.0)]),
    ]
    candidates = [
        # Peak inside the container: survives with end clamped to 100.
        {"setup_start_s": 30.0, "start_s": 20.0, "peak_s": 80.0, "end_s": 108.0},
        # Peak past the container: no window inside 100 s can hold the 456
        # invariant → repair cannot restore it → dropped, never persisted.
        {"setup_start_s": 30.0, "start_s": 20.0, "peak_s": 105.0, "end_s": 108.0},
    ]

    out = snap_candidates_to_sentences(
        [dict(c) for c in candidates], segments, duration_s=120.0, container_duration_s=100.0
    )
    assert len(out) == 1
    assert out[0]["peak_s"] == 80.0
    assert out[0]["end_s"] <= 100.0  # never past what the render will accept
    # Issue 456 invariant holds on the survivor.
    assert out[0]["setup_start_s"] + 0.1 <= out[0]["peak_s"] <= out[0]["end_s"] - 0.1

    # Without the container the audio timeline alone would let end_s run to
    # 110 s — the geometry the render used to hard-reject (the 469 defect).
    unthreaded = snap_candidates_to_sentences([dict(candidates[0])], segments, duration_s=120.0)
    assert unthreaded[0]["end_s"] == 110.0


def test_probed_duration_boundary_and_failure_behavior():
    """classify_video_kind is `<= SHORTS_MAX_DURATION_S → short` (boundary
    pinned); a failed probe (None/0) must never null out an existing value."""
    from types import SimpleNamespace

    from models import VideoKind
    from worker.tasks import _apply_probed_duration

    video = SimpleNamespace(duration_s=200.0, kind=VideoKind.long)
    _apply_probed_duration(video, 180.0)  # type: ignore[arg-type]
    assert video.duration_s == 180.0
    assert video.kind == VideoKind.short  # exactly at the cap → short (<=)

    _apply_probed_duration(video, None)  # type: ignore[arg-type]
    assert video.duration_s == 180.0  # probe failure → keep the last good value
    assert video.kind == VideoKind.short


def _render_against_container(tmp_path, *, src_dur: float, end_s: float) -> list[list[str]]:
    """Run render_clip_file with the container probe pinned to ``src_dur``.

    Returns every captured subprocess argv (the render cmd carries ``-t``).
    """
    import numpy as np

    src = tmp_path / "v.mp4"
    src.touch()
    out = tmp_path / "out.mp4"
    fake_img = np.zeros((1080, 1920, 3), dtype="uint8")
    captured: list[list[str]] = []

    def _fake(cmd, **kwargs):
        captured.append(cmd)
        return MagicMock(returncode=0, stdout="1920,1080\n", stderr="")

    with (
        patch("clip_engine.render._source_duration_s", return_value=src_dur),
        patch("subprocess.run", side_effect=_fake),
        patch("cv2.imread", return_value=fake_img),
        patch("cv2.CascadeClassifier") as mock_cc,
    ):
        mock_cc.return_value.detectMultiScale.return_value = []
        render_clip_file(src, start_s=0.0, end_s=end_s, out_path=out)
    return captured


def test_subsecond_container_overshoot_clamps_and_renders(tmp_path, caplog):
    """end_s 10.4 vs a 10.0 s container: a rounding-hair overshoot must clamp
    to the container and render — not become a permanent ValueError failure
    (Issue 469). The clamped duration reaches ffmpeg's -t."""
    with caplog.at_level(logging.WARNING, logger="clip_engine.render"):
        captured = _render_against_container(tmp_path, src_dur=10.0, end_s=10.4)
    render_cmd = next(c for c in captured if "-vf" in c)
    assert render_cmd[render_cmd.index("-t") + 1] == "10.0"
    assert "duration_overshoot_clamped" in caplog.text


def test_full_second_container_overshoot_still_raises(tmp_path):
    """end_s 11.5 vs a 10.0 s container (≥ 1.0 s overshoot) means the persisted
    geometry is genuinely wrong — still terminal."""
    with pytest.raises(ValueError, match="exceeds source duration"):
        _render_against_container(tmp_path, src_dur=10.0, end_s=11.5)


# ── Camera-region pre-crop (Issue 430) ───────────────────────────────────────


def _render_vf_with_region(tmp_path, *, region, flag=True, reframe=False) -> tuple[str, dict]:
    """Render with the camera-region flag toggled and detection patched.

    Returns ``(vf, compute_kwargs)`` — ``compute_kwargs`` captures the call to
    ``compute_dynamic_crop`` under the reframe flag (empty dict otherwise).
    """
    from config import settings as _settings

    compute_kwargs: dict = {}
    with (
        patch.object(_settings, "CAMERA_REGION_DETECT_ENABLED", flag),
        patch.object(_settings, "ACTIVE_SPEAKER_REFRAME_ENABLED", reframe),
        patch("clip_engine.camera_region.detect_camera_region", return_value=region),
    ):
        if reframe:
            from clip_engine.reframe import CropCenterPoint, ReframeResult

            def _fake_compute(**kwargs):
                compute_kwargs.update(kwargs)
                # Multi-point track → sendcmd chain (the composed path).
                return ReframeResult(
                    track_points=[
                        CropCenterPoint(10.0, 400),
                        CropCenterPoint(10.2, 420),
                    ],
                    sendcmd_text="0.000 [enter] crop@spk x 147;\n0.200 [enter] crop@spk x 167;",
                    mode="face_pan",
                    track_json={"version": 1},
                )

            with patch("clip_engine.reframe.compute_dynamic_crop", side_effect=_fake_compute):
                vf = _render_vf(tmp_path)
        else:
            vf = _render_vf(tmp_path)
    return vf, compute_kwargs


def _render_vf_with_video_region(tmp_path, *, stored, per_clip=None, face=None):
    """Render with a stored video-level region; returns ``(vf, detect_calls)``."""
    from config import settings as _settings

    calls: list = []

    def _detect(*args, **kwargs):
        calls.append(kwargs)
        return per_clip

    with (
        patch.object(_settings, "CAMERA_REGION_DETECT_ENABLED", True),
        patch.object(_settings, "ACTIVE_SPEAKER_REFRAME_ENABLED", False),
        patch("clip_engine.camera_region.detect_camera_region", side_effect=_detect),
        patch("clip_engine.render._detect_face_box", return_value=face),
    ):
        vf = _render_vf(tmp_path, video_camera_region=stored)
    return vf, calls


_STORED_REGION = {
    # Read from the constant, not pinned to a literal: with a stale version the
    # render falls back to per-clip detection, so a version bump would leave
    # test_video_level_region_falls_back_when_the_face_is_outside_it passing for
    # entirely the wrong reason (calls == 1 either way).
    "version": _camera_region.VIDEO_REGION_VERSION,
    "x": 200,
    "y": 100,
    "width": 1400,
    "height": 900,
    "frame": {"width": 1920, "height": 1080},
    "sample_frames": 10,
}


def test_video_level_region_is_preferred_over_per_clip_detection(tmp_path):
    """Issue 439 Stage 2: the stored rect wins and per-clip detection never runs,
    which is what makes clips of one source agree by construction."""
    vf, calls = _render_vf_with_video_region(
        tmp_path, stored=_STORED_REGION, face=(800, 400, 200, 200)
    )
    assert "crop=1400:900:200:100" in vf
    assert calls == [], "per-clip detection must not run when a video region is stored"


def test_video_level_region_falls_back_when_the_face_is_outside_it(tmp_path):
    """The video-level rect carries no face-sanity check of its own, so a clip
    whose speaker sits outside it re-detects rather than cropping them away."""
    _vf, calls = _render_vf_with_video_region(
        tmp_path, stored=_STORED_REGION, per_clip=None, face=(1850, 1000, 60, 60)
    )
    assert len(calls) == 1, "a face outside the stored region must trigger per-clip detection"


def test_absent_video_region_still_detects_per_clip(tmp_path):
    """Videos ingested before migration 0056 keep the pre-439 behaviour."""
    _vf, calls = _render_vf_with_video_region(tmp_path, stored=None, per_clip=None)
    assert len(calls) == 1


def test_camera_region_prepends_pre_crop_and_rescopes_geometry(tmp_path):
    # Region 1400×900 at (200, 100): pre-crop first, then the 9:16 crop uses
    # region height (900) and region-space x, then the scale.
    vf, _ = _render_vf_with_region(tmp_path, region=(200, 100, 1400, 900))
    parts = vf.split(",")
    assert parts[0] == "crop=1400:900:200:100"
    crop_w = int(900 * 1080 / 1920)  # 506
    x_offset = max(0, min(1400 // 2 - crop_w // 2, 1400 - crop_w))
    assert parts[1] == f"crop={crop_w}:900:{x_offset}:0"
    assert parts[2] == "scale=1080:1920"


def test_camera_region_none_is_byte_identical(tmp_path):
    with_flag, _ = _render_vf_with_region(tmp_path, region=None)
    baseline = _render_vf(tmp_path)
    assert with_flag == baseline


def test_camera_region_composes_with_reframe(tmp_path):
    """Issue 433: region + reframe compose — pre-crop first, then the LABELED
    9:16 crop driven by sendcmd, and the reframe runs in REGION space."""
    vf, kwargs = _render_vf_with_region(tmp_path, region=(200, 100, 1400, 900), reframe=True)
    parts = vf.split(",")
    crop_w = int(900 * 1080 / 1920)  # 506
    assert parts[0].startswith("sendcmd=f=")
    assert parts[1] == "crop=1400:900:200:100"  # unlabeled, unaddressed pre-crop
    x0 = max(0, min(400 - crop_w // 2, 1400 - crop_w))  # first track point, region clamp
    assert parts[2] == f"crop@spk={crop_w}:900:{x0}:0"
    assert parts[3] == "scale=1080:1920"
    # The reframe received REGION dims as its frame dims + the region rect.
    assert kwargs["frame_width"] == 1400
    assert kwargs["frame_height"] == 900
    assert kwargs["crop_w"] == crop_w
    assert kwargs["region"] == (200, 100, 1400, 900)


def test_overlay_mask_precedes_the_region_pre_crop(tmp_path):
    """Issue 448: the mask runs FIRST, in absolute source coordinates.

    Everything downstream (region pre-crop, 9:16 crop, scale) then carries the
    masked pixels through by construction, so no offset arithmetic exists and
    the mask works whether or not a camera region was detected.
    """
    spans = {
        "version": OVERLAY_SPANS_VERSION,
        "y": 785,
        "height": 202,
        "frame": {"width": 1920, "height": 1080},
        "spans": [{"start_s": 20.0, "end_s": 40.0}],
    }
    vf = _render_vf(tmp_path, video_overlay_spans=spans)  # clip window is 10-70s
    # NB: do NOT split on "," — `between(t,10.000,30.000)` contains commas.
    assert vf.startswith(
        "split[ob_bg][ob_fg];"
        "[ob_fg]crop=1920:202:0:785,boxblur=12:2[ob_bl];"
        # Clip-relative: span 20-40s against a clip starting at 10s -> 10-30s.
        "[ob_bg][ob_bl]overlay=0:785:enable='between(t,10.000,30.000)',"
    )
    # ...and the ordinary chain still follows, unchanged.
    assert vf.endswith("crop=607:1080:657:0,scale=1080:1920")


def test_no_overlay_mask_is_byte_identical(tmp_path):
    """A clip that misses every span must render exactly what it renders today."""
    spans = {
        "version": OVERLAY_SPANS_VERSION,
        "y": 785,
        "height": 202,
        "frame": {"width": 1920, "height": 1080},
        "spans": [{"start_s": 900.0, "end_s": 950.0}],  # nowhere near the 10-70s clip
    }
    assert _render_vf(tmp_path, video_overlay_spans=spans) == _render_vf(tmp_path)
    assert _render_vf(tmp_path, video_overlay_spans=None) == _render_vf(tmp_path)


def test_overlay_mask_ignored_when_measured_against_other_dimensions(tmp_path):
    """Masking the wrong rows is worse than not masking at all."""
    spans = {
        "version": OVERLAY_SPANS_VERSION,
        "y": 785,
        "height": 202,
        "frame": {"width": 1280, "height": 720},  # source here is 1920x1080
        "spans": [{"start_s": 20.0, "end_s": 40.0}],
    }
    assert _render_vf(tmp_path, video_overlay_spans=spans) == _render_vf(tmp_path)


def test_reframe_without_region_calls_compute_with_full_frame(tmp_path):
    """Flag-ON, region None: compute gets full-frame dims and region=None —
    the vf chain is today's sendcmd chain with only the @spk label added."""
    vf, kwargs = _render_vf_with_region(tmp_path, region=None, reframe=True)
    parts = vf.split(",")
    assert parts[0].startswith("sendcmd=f=")
    assert parts[1].startswith("crop@spk=607:1080:")
    assert parts[2] == "scale=1080:1920"
    assert kwargs["frame_width"] == 1920
    assert kwargs["frame_height"] == 1080
    assert kwargs["region"] is None


def test_reframe_region_punch_in_stays_after_scale(tmp_path):
    """zoom_on_peak + region + reframe: the punch-in chunk appends AFTER the
    scale and carries no @spk label (sendcmd can no longer address it)."""
    from clip_engine.reframe import CropCenterPoint, ReframeResult
    from config import settings as _settings

    with (
        patch.object(_settings, "CAMERA_REGION_DETECT_ENABLED", True),
        patch.object(_settings, "ACTIVE_SPEAKER_REFRAME_ENABLED", True),
        patch(
            "clip_engine.camera_region.detect_camera_region",
            return_value=(200, 100, 1400, 900),
        ),
        patch(
            "clip_engine.reframe.compute_dynamic_crop",
            return_value=ReframeResult(
                track_points=[CropCenterPoint(10.0, 400), CropCenterPoint(10.2, 420)],
                sendcmd_text="0.000 [enter] crop@spk x 147;",
                mode="face_pan",
                track_json={"version": 1},
            ),
        ),
    ):
        vf = _render_vf(tmp_path, style_preset={"zoom_on_peak": True}, peak_s=40.0)
    scale_idx = vf.index("scale=1080:1920")
    punch_idx = vf.index("scale=w='trunc(")
    assert punch_idx > scale_idx
    assert "@spk" not in vf[punch_idx:]


def test_reframe_flag_on_passes_face_bottom_to_captions(tmp_path):
    """Issue 433: the Haar pass is unconditional — caption face avoidance is
    live under the reframe flag (previously dead), and region-aware."""
    import numpy as np

    from clip_engine.reframe import CropCenterPoint, ReframeResult
    from config import settings as _settings

    src = tmp_path / "v.mp4"
    src.touch()
    out = tmp_path / "out.mp4"
    fake_img = np.zeros((1080, 1920, 3), dtype="uint8")
    captured: dict = {}

    def _fake_run(cmd, **kwargs):
        return MagicMock(returncode=0, stdout="1920,1080\n", stderr=_LOUDNORM_JSON)

    def _fake_captions(**kwargs):
        captured.update(kwargs)
        return None

    with (
        patch("subprocess.run", side_effect=_fake_run),
        patch("cv2.imread", return_value=fake_img),
        patch("cv2.CascadeClassifier") as mock_cc,
        patch.object(_settings, "ACTIVE_SPEAKER_REFRAME_ENABLED", True),
        patch.object(_settings, "CAMERA_REGION_DETECT_ENABLED", True),
        patch(
            "clip_engine.camera_region.detect_camera_region",
            return_value=(200, 100, 1400, 900),
        ),
        patch(
            "clip_engine.reframe.compute_dynamic_crop",
            return_value=ReframeResult(
                track_points=[CropCenterPoint(10.0, 400)],
                sendcmd_text="",
                mode="static",
                track_json={"version": 1},
            ),
        ),
        patch("clip_engine.render.captions.build_ass_subtitles", side_effect=_fake_captions),
    ):
        # Face box at y=300, h=200 → bottom 500 source px; region y=100 h=900.
        mock_cc.return_value.detectMultiScale.return_value = [(800, 300, 200, 200)]
        render_clip_file(
            src,
            start_s=10.0,
            end_s=70.0,
            out_path=out,
            style_preset={"subtitle": "bold_pop"},
            transcript_segments=[{"start": 10, "end": 20, "text": "hi"}],
        )
    assert captured["face_bottom_px"] == round((300 + 200 - 100) * 1920 / 900)


def test_requested_captions_that_resolve_to_nothing_are_logged(tmp_path, caplog):
    """Issue 438: a requested caption track that yields no file must not vanish silently.

    `render_clip_file` drops the `subtitles=` filter when `build_ass_subtitles`
    produces nothing, and still returns a successful render. That is how a
    captionless clip reached a creator with `render_status = done` and no signal
    anywhere. The render must still succeed — a captionless clip beats no clip —
    but it has to say so.
    """
    import logging

    with caplog.at_level(logging.WARNING, logger="clip_engine.render"):
        vf = _render_vf(tmp_path, style_preset={"subtitle": "bold_pop"})

    assert "subtitles=" not in vf, "precondition: no caption track was produced"
    assert any(
        "caption" in r.message.lower() for r in caplog.records if r.levelno >= logging.WARNING
    ), "a requested-but-missing caption track must log a warning"


# ── opt-in noise reduction (Issue 185) ────────────────────────────────────────


def _render_capture_afs(tmp_path, *, style_preset=None, input_i="-27.85") -> list[str]:
    """Render with mocked ffmpeg/cv2 and return every -af filter string used
    (measurement pass, then render pass)."""
    src = tmp_path / "v.mp4"
    src.touch()
    out = tmp_path / "out.mp4"
    import numpy as np

    fake_img = np.zeros((1080, 1920, 3), dtype="uint8")
    stderr = _LOUDNORM_JSON.replace('"input_i" : "-27.85"', f'"input_i" : "{input_i}"')
    captured: list[list[str]] = []

    def _fake(cmd, **kwargs):
        captured.append(cmd)
        return MagicMock(returncode=0, stdout="1920,1080\n", stderr=stderr)

    with (
        patch("subprocess.run", side_effect=_fake),
        patch("cv2.imread", return_value=fake_img),
        patch("cv2.CascadeClassifier") as mock_cc,
    ):
        mock_cc.return_value.detectMultiScale.return_value = []
        render_clip_file(src, start_s=10.0, end_s=70.0, out_path=out, style_preset=style_preset)
    return [c[c.index("-af") + 1] for c in captured if "-af" in c]


def test_denoise_prepends_afftdn_before_loudnorm(tmp_path):
    afs = _render_capture_afs(tmp_path, style_preset={"denoise": True})
    assert afs  # both measurement and render carry an audio chain
    for af in afs:
        assert af.startswith("afftdn=nr=10")  # denoise leads every pass
    render_af = afs[-1]
    assert render_af.index("afftdn") < render_af.index("loudnorm")  # denoise before normalize


def test_no_denoise_when_disabled(tmp_path):
    afs = _render_capture_afs(tmp_path, style_preset={"denoise": False})
    assert afs and all("afftdn" not in af for af in afs)


def test_denoise_applied_even_when_clip_is_silent(tmp_path):
    # Near-silent → loudnorm skipped, but the opt-in denoise still runs.
    afs = _render_capture_afs(tmp_path, style_preset={"denoise": True}, input_i="-70.00")
    render_af = afs[-1]
    assert "afftdn=nr=10" in render_af
    assert "loudnorm" not in render_af


# ── export presets (Issue 182) ────────────────────────────────────────────────


def test_default_preset_is_byte_identical_9x16(tmp_path):
    vf = _render_vf(tmp_path)  # no aspect → 9:16
    assert "scale=1080:1920" in vf
    assert "crop=607:1080" in vf  # int(1080*1080/1920)=607, unchanged from pre-182


def test_square_preset_renders_1080x1080(tmp_path):
    vf = _render_vf(tmp_path, style_preset={"aspect": "1:1"})
    assert "scale=1080:1080" in vf
    assert "crop=1080:1080" in vf  # full-height square crop, centered on face


def test_horizontal_preset_renders_1920x1080(tmp_path):
    vf = _render_vf(tmp_path, style_preset={"aspect": "16:9"})
    assert "scale=1920:1080" in vf
    assert "crop=1920:1080" in vf


def test_unknown_aspect_falls_back_to_default(tmp_path):
    vf = _render_vf(tmp_path, style_preset={"aspect": "bogus"})
    assert "scale=1080:1920" in vf


def test_render_clip_file_raises_on_ffmpeg_timeout(tmp_path):
    """`render_clip_file` surfaces a `RuntimeError` when the render ffmpeg call stalls."""
    src = tmp_path / "v.mp4"
    src.touch()
    out = tmp_path / "out.mp4"

    import numpy as np

    fake_img = np.zeros((1080, 1920, 3), dtype="uint8")
    call_count = 0

    def fake_subprocess_run(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        # First call: ffprobe returns dimensions normally.
        # Second call: keyframe extraction returns normally.
        # Third call: the render ffmpeg stalls → TimeoutExpired.
        if call_count <= 2:
            m = MagicMock()
            m.returncode = 0
            m.stdout = "1920,1080\n"
            m.stderr = ""
            return m
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 120))

    with (
        patch("subprocess.run", side_effect=fake_subprocess_run),
        patch("cv2.imread", return_value=fake_img),
        patch("cv2.CascadeClassifier") as mock_cc,
    ):
        mock_cc.return_value.detectMultiScale.return_value = []
        with pytest.raises(RuntimeError, match="timed out after"):
            render_clip_file(src, start_s=10.0, end_s=70.0, out_path=out)


# ── per-frame active-speaker reframe flag (Issue 189) ─────────────────────────


def _render_vf_with_reframe_flag(tmp_path, *, flag_enabled: bool) -> str:
    """Render with ACTIVE_SPEAKER_REFRAME_ENABLED toggled; return the -vf string.

    Patches ``config.settings`` (the global Settings instance) so the local
    import inside render_clip_file picks up the patched value.
    """
    import config as _config_mod

    src = tmp_path / "v.mp4"
    src.touch()
    out = tmp_path / "out.mp4"

    import numpy as np

    fake_img = np.zeros((1080, 1920, 3), dtype="uint8")
    captured: list[list[str]] = []

    def _fake(cmd, **kwargs):
        captured.append(cmd)
        return MagicMock(returncode=0, stdout="1920,1080\n", stderr="")

    # Patch compute_dynamic_crop (Issue 421 — render_clip_file's reframe entry
    # point) to return a deterministic synthetic result.
    from clip_engine.reframe import CropCenterPoint, ReframeResult

    fake_track = [
        CropCenterPoint(10.0, 700),
        CropCenterPoint(10.2, 720),
    ]
    fake_script = "0.000 [enter] crop x 396;\n0.200 [enter] crop x 416;"
    fake_result = ReframeResult(
        track_points=fake_track,
        sendcmd_text=fake_script,
        track_json={"version": 1, "mode": "face_pan"},
        mode="face_pan",
    )

    with (
        patch("subprocess.run", side_effect=_fake),
        patch("cv2.imread", return_value=fake_img),
        patch("cv2.CascadeClassifier") as mock_cc,
        patch.object(_config_mod.settings, "ACTIVE_SPEAKER_REFRAME_ENABLED", flag_enabled),
        patch.object(_config_mod.settings, "REFRAME_SAMPLE_FPS", 5.0),
        # compute_dynamic_crop is resolved through the module inside
        # render_clip_file — patch at source.
        patch(
            "clip_engine.reframe.compute_dynamic_crop",
            return_value=fake_result,
        ),
    ):
        mock_cc.return_value.detectMultiScale.return_value = []
        returned = render_clip_file(src, start_s=10.0, end_s=70.0, out_path=out)

    # Flag off → None (the worker nulls any stale persisted track);
    # flag on → the wire-contract dict from compute_dynamic_crop (Issue 421).
    if flag_enabled:
        assert returned == {"version": 1, "mode": "face_pan"}
    else:
        assert returned is None

    render_cmd = next((c for c in captured if "-vf" in c), None)
    assert render_cmd is not None
    return render_cmd[render_cmd.index("-vf") + 1]


def test_reframe_flag_disabled_uses_legacy_haar_path(tmp_path):
    """When ACTIVE_SPEAKER_REFRAME_ENABLED is False, the legacy Haar path runs
    and the vf string must NOT contain sendcmd."""
    vf = _render_vf_with_reframe_flag(tmp_path, flag_enabled=False)
    assert "sendcmd" not in vf
    assert "crop=" in vf  # static crop still present


def test_reframe_flag_enabled_includes_sendcmd_in_vf(tmp_path):
    """When ACTIVE_SPEAKER_REFRAME_ENABLED is True and compute_reframe_crop
    returns a multi-point script, the vf string must start with sendcmd."""
    vf = _render_vf_with_reframe_flag(tmp_path, flag_enabled=True)
    assert vf.startswith("sendcmd=")
    assert "crop@spk=" in vf  # instance-labeled 9:16 crop (Issue 433)


# ── Issue 329: new edge-suite tests ───────────────────────────────────────────


# ─── 1. _run: stderr tail (not head) in RuntimeError ─────────────────────────


def test_run_stderr_tail_in_error_message():
    """Non-zero exit: error message must contain the END of stderr, not the head.

    Structure: <HEAD_ONLY_MARKER> + 2000 padding chars + <TAIL_ONLY_MARKER>
    Last 500 chars contains TAIL_ONLY_MARKER but not HEAD_ONLY_MARKER.
    """
    head_marker = "HEAD_ONLY_MARKER"
    tail_marker = "TAIL_ONLY_MARKER"
    long_stderr = head_marker + "X" * 2000 + tail_marker

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr=long_stderr)
        with pytest.raises(RuntimeError) as exc_info:
            _run(["ffmpeg", "-version"], "test")

    err = str(exc_info.value)
    assert tail_marker in err, "tail marker must appear — error must use stderr[-500:]"
    assert head_marker not in err, "head marker must NOT appear — head is beyond the 500-char tail"


# ─── 2. _run: OSError / FileNotFoundError / PermissionError → RuntimeError ───


@pytest.mark.parametrize(
    "exc_cls",
    [OSError, FileNotFoundError, PermissionError],
)
def test_run_wraps_subprocess_os_errors(exc_cls):
    """subprocess.run raising OS-level errors must be wrapped in RuntimeError."""
    with (
        patch("subprocess.run", side_effect=exc_cls("ffmpeg not found")),
        pytest.raises(RuntimeError, match="ffmpeg"),
    ):
        _run(["ffmpeg", "-version"], "test label")


# ─── 3. render_clip_file: start_s<0 and end_s>source_duration guards ─────────


def test_render_clip_file_raises_on_negative_start(tmp_path):
    """start_s < 0 must raise ValueError before any ffmpeg call."""
    src = tmp_path / "v.mp4"
    src.touch()
    out = tmp_path / "out.mp4"
    with pytest.raises(ValueError, match="start_s"):
        render_clip_file(src, start_s=-1.0, end_s=10.0, out_path=out)


def test_render_clip_file_raises_when_end_past_source_duration(tmp_path):
    """end_s > source duration must raise ValueError before ffmpeg shell-out."""
    import clip_engine.render as render_mod

    src = tmp_path / "v.mp4"
    src.touch()
    out = tmp_path / "out.mp4"
    with (
        patch.object(render_mod, "_source_duration_s", return_value=30.0),
        pytest.raises(ValueError, match="end_s"),
    ):
        render_clip_file(src, start_s=0.0, end_s=60.0, out_path=out)


# ─── 4. _detect_face_center_x: distinct INFO log lines ───────────────────────


def test_detect_face_center_x_logs_corrupt_frame(tmp_path, caplog):
    """cv2.imread→None logs an INFO 'corrupt frame' message."""
    import logging

    kf = tmp_path / "kf.jpg"
    kf.touch()
    with (
        caplog.at_level(logging.INFO, logger="clip_engine.render"),
        patch("cv2.imread", return_value=None),
    ):
        cx = _detect_face_center_x(kf, 1920)
    assert cx == 960
    assert any("corrupt" in r.message.lower() for r in caplog.records)


def test_detect_face_center_x_logs_no_face(tmp_path, caplog):
    """detectMultiScale→[] logs an INFO 'no face' message."""
    import logging

    import numpy as np

    kf = tmp_path / "kf.jpg"
    kf.touch()
    fake_img = np.zeros((1080, 1920, 3), dtype="uint8")
    with (
        caplog.at_level(logging.INFO, logger="clip_engine.render"),
        patch("cv2.imread", return_value=fake_img),
        patch("cv2.CascadeClassifier") as mock_cc,
    ):
        mock_cc.return_value.detectMultiScale.return_value = []
        cx = _detect_face_center_x(kf, 1920)
    assert cx == 960
    assert any("no face" in r.message.lower() for r in caplog.records)


# ─── 5. _frame_dimensions: WARNING log on parse failure ──────────────────────


def test_frame_dimensions_logs_warning_on_bad_output(tmp_path, caplog):
    """Unparseable ffprobe output must emit a WARNING before returning the default."""
    import logging

    fake = tmp_path / "v.mp4"
    fake.touch()
    with (
        caplog.at_level(logging.WARNING, logger="clip_engine.render"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="garbage", stderr="")
        w, h = _frame_dimensions(fake)
    assert w == 1920 and h == 1080
    assert any(r.levelno == logging.WARNING for r in caplog.records)


# ─── 6. render_cleaned_clip_file: normalize unsorted / overlapping ranges ─────


def test_render_cleaned_clip_file_accepts_unsorted_ranges(tmp_path):
    """Unsorted keep_ranges are normalized (sorted) rather than rejected."""
    captured: dict = {}

    def _fake_run(cmd, label, timeout_s=120.0):
        captured["cmd"] = cmd

    with (
        patch("clip_engine.render._run", _fake_run),
        patch("clip_engine.render._measure_loudnorm_filter", return_value=None),
    ):
        # ranges in wrong order — should succeed, not raise
        render_cleaned_clip_file(
            source_path=Path("/fake/src.mp4"),
            keep_ranges=[(5.0, 8.0), (0.0, 3.0)],
            out_path=tmp_path / "out.mp4",
        )
    assert "cmd" in captured  # ffmpeg was called → succeeded


def test_render_cleaned_clip_file_merges_overlapping_ranges(tmp_path):
    """Overlapping keep_ranges are merged into one segment and ffmpeg is called."""
    captured: dict = {}

    def _fake_run(cmd, label, timeout_s=120.0):
        script = Path(cmd[cmd.index("-filter_complex_script") + 1]).read_text()
        captured["script"] = script

    with (
        patch("clip_engine.render._run", _fake_run),
        patch("clip_engine.render._measure_loudnorm_filter", return_value=None),
    ):
        render_cleaned_clip_file(
            source_path=Path("/fake/src.mp4"),
            keep_ranges=[(0.0, 5.0), (3.0, 8.0)],  # overlap at 3–5
            out_path=tmp_path / "out.mp4",
        )

    script = captured["script"]
    # Merged to (0.0, 8.0) → only one video trim segment
    assert script.count("[0:v]trim=") == 1
    assert "start=0.000" in script
    assert "end=8.000" in script


def test_render_cleaned_clip_file_logs_when_normalization_moves_edge(tmp_path, caplog):
    """When ranges are reordered or merged, an INFO log must be emitted."""
    import logging

    def _fake_run(cmd, label, timeout_s=120.0):
        pass

    with (
        caplog.at_level(logging.INFO, logger="clip_engine.render"),
        patch("clip_engine.render._run", _fake_run),
        patch("clip_engine.render._measure_loudnorm_filter", return_value=None),
    ):
        render_cleaned_clip_file(
            source_path=Path("/fake/src.mp4"),
            keep_ranges=[(5.0, 8.0), (0.0, 3.0)],  # unsorted → normalised
            out_path=tmp_path / "out.mp4",
        )

    assert any("normaliz" in r.message.lower() for r in caplog.records)


def test_render_cleaned_clip_file_warns_on_loudnorm_unparseable(tmp_path, caplog):
    """When loudnorm stats are unparseable the render proceeds flat with a WARNING."""
    import logging

    def _fake_run(cmd, label, timeout_s=120.0):
        pass

    # _measure_loudnorm_filter calls subprocess.run internally; return garbage stderr.
    with (
        caplog.at_level(logging.WARNING, logger="clip_engine.render"),
        patch("clip_engine.render._run", _fake_run),
        patch(
            "subprocess.run",
            return_value=MagicMock(returncode=0, stdout="", stderr="not json"),
        ),
    ):
        render_cleaned_clip_file(
            source_path=Path("/fake/src.mp4"),
            keep_ranges=[(0.0, 5.0)],
            out_path=tmp_path / "out.mp4",
        )

    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_reframe_flag_enabled_sendcmd_file_cleaned_up(tmp_path):
    """The sendcmd temp file must be removed after render, even on success."""
    import config as _config_mod

    src = tmp_path / "v.mp4"
    src.touch()
    out = tmp_path / "out.mp4"

    import numpy as np

    fake_img = np.zeros((1080, 1920, 3), dtype="uint8")

    from clip_engine.reframe import CropCenterPoint

    fake_track = [CropCenterPoint(10.0, 700), CropCenterPoint(10.2, 720)]
    fake_script = "0.000 [enter] crop x 396;\n0.200 [enter] crop x 416;"

    def _fake(cmd, **kwargs):
        return MagicMock(returncode=0, stdout="1920,1080\n", stderr="")

    written_paths: list[Path] = []

    original_write_text = Path.write_text

    def _capture_write(self, text, *args, **kwargs):
        if str(self).endswith(".sendcmd"):
            written_paths.append(self)
        return original_write_text(self, text, *args, **kwargs)

    with (
        patch("subprocess.run", side_effect=_fake),
        patch("cv2.imread", return_value=fake_img),
        patch("cv2.CascadeClassifier") as mock_cc,
        patch.object(_config_mod.settings, "ACTIVE_SPEAKER_REFRAME_ENABLED", True),
        patch.object(_config_mod.settings, "REFRAME_SAMPLE_FPS", 5.0),
        patch(
            "clip_engine.reframe.compute_reframe_crop",
            return_value=(fake_track, fake_script),
        ),
        patch.object(Path, "write_text", _capture_write),
    ):
        mock_cc.return_value.detectMultiScale.return_value = []
        render_clip_file(src, start_s=10.0, end_s=70.0, out_path=out)

    # Every .sendcmd file written must have been cleaned up.
    for p in written_paths:
        assert not p.exists(), f"sendcmd temp file not cleaned up: {p}"


# ── stream-VOD recap render (Issue 191) ───────────────────────────────────────

_RECAP_SEGMENTS = [(10.0, 25.0), (100.0, 130.0), (500.0, 520.0)]


def test_build_summary_filtergraph_shape():
    """Three segments → 3 trim/atrim pairs, per-segment 16:9 scale + video fades
    + splice afades, concatenated with concat=n=3:v=1:a=1."""
    script, audio_out = build_summary_filtergraph(_RECAP_SEGMENTS)

    assert script.count("[0:v]trim=") == 3
    assert script.count("[0:a]atrim=") == 3
    assert script.count(",setpts=PTS-STARTPTS") == 3
    assert script.count(",asetpts=PTS-STARTPTS") == 3
    assert script.count("scale=1920:1080") == 3  # per-segment 16:9 preset
    assert script.count("afade=t=in") == 3 and script.count("afade=t=out") == 3
    assert script.count("fade=t=in") == 6  # 3 video + 3 audio (afade contains 'fade')
    assert "[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[outv][outa]" in script
    assert audio_out == "[outa]"  # no loudnorm → raw concat audio is mapped


def test_build_summary_filtergraph_chains_loudnorm_after_concat():
    measured = "loudnorm=I=-14:TP=-1.5:LRA=11:measured_I=-20.0:linear=true"
    script, audio_out = build_summary_filtergraph(_RECAP_SEGMENTS, measured)

    concat_at = script.index("concat=n=3:v=1:a=1")
    loudnorm_at = script.index(f";[outa]{measured}[outaln]")
    assert loudnorm_at > concat_at, "loudnorm must chain AFTER the concat"
    assert audio_out == "[outaln]"


def test_build_summary_render_cmd_single_input_script_graph(tmp_path):
    cmd = build_summary_render_cmd(
        Path("/abs/src.mp4"), tmp_path / "g.filter", tmp_path / "out.mp4", "[outaln]"
    )
    assert cmd.count("-i") == 1  # single-input trim graph (whole-VOD decode)
    assert "-filter_complex_script" in cmd  # never inline args (OS arg limits)
    assert "-filter_complex" not in cmd
    assert cmd[cmd.index("-map") + 1] == "[outv]"
    assert "[outaln]" in cmd


def test_render_summary_file_writes_script_and_maps_normalized_audio(tmp_path):
    """House mocked-subprocess pattern: capture the -filter_complex_script content
    and assert the render maps the loudnorm-normalized audio label."""
    captured = {}

    def _fake_run(cmd, label, timeout_s=120.0):
        captured["cmd"] = cmd
        captured["script"] = Path(cmd[cmd.index("-filter_complex_script") + 1]).read_text()

    measured = "loudnorm=I=-14:TP=-1.5:LRA=11:measured_I=-20.0:linear=true"
    with (
        patch("clip_engine.render._run", _fake_run),
        patch("clip_engine.render._measure_loudnorm_filter", return_value=measured),
    ):
        render_summary_file(
            source_path=Path("/fake/vod.mp4"),
            segments=[(5.0, 15.0), (60.0, 90.0)],
            out_path=tmp_path / "recap.mp4",
            timeout_s=120.0,
        )

    script = captured["script"]
    assert "concat=n=2:v=1:a=1[outv][outa]" in script
    assert script.count("scale=1920:1080") == 2
    assert f";[outa]{measured}[outaln]" in script
    assert "[outaln]" in captured["cmd"]
    assert not (tmp_path / "recap.filter").exists(), "filter script must be cleaned up"


def test_render_summary_file_sorts_segments_chronologically(tmp_path):
    captured = {}

    def _fake_run(cmd, label, timeout_s=120.0):
        captured["script"] = Path(cmd[cmd.index("-filter_complex_script") + 1]).read_text()

    with (
        patch("clip_engine.render._run", _fake_run),
        patch("clip_engine.render._measure_loudnorm_filter", return_value=None),
    ):
        render_summary_file(
            source_path=Path("/fake/vod.mp4"),
            segments=[(60.0, 90.0), (5.0, 15.0)],  # shuffled on purpose
            out_path=tmp_path / "recap.mp4",
            timeout_s=120.0,
        )

    assert "[0:v]trim=start=5.000" in captured["script"].splitlines()[0]


@pytest.mark.parametrize("segments", [[], [(10.0, 10.0)], [(-1.0, 5.0)]])
def test_render_summary_file_rejects_bad_segments(tmp_path, segments):
    with pytest.raises(ValueError):
        render_summary_file(Path("/fake/vod.mp4"), segments, tmp_path / "recap.mp4", 120.0)


# Real-ffmpeg smoke (render-env bonus): only when ffmpeg + ffprobe are installed.
_FFMPEG_AVAILABLE = bool(shutil.which("ffmpeg")) and bool(shutil.which("ffprobe"))


@pytest.mark.skipif(not _FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not installed")
def test_render_summary_file_real_ffmpeg_smoke(tmp_path):
    """Tiny synthetic 2-segment source → stitched recap exists and is 1920x1080."""
    src = tmp_path / "vod.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=4:size=320x240:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=4",
            "-shortest",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(src),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )

    out = tmp_path / "recap.mp4"
    render_summary_file(src, [(0.5, 1.5), (2.0, 3.0)], out, timeout_s=120.0)

    assert out.exists() and out.stat().st_size > 0
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert probe.stdout.strip() == "1920,1080"
