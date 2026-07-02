"""
Unit tests for clip_engine/render.py.

ffmpeg / ffprobe / cv2 calls are patched — no video files needed.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("cv2")  # skip whole file if libGL is absent on this host

from clip_engine.render import (
    _detect_face_center_x,
    _extract_keyframe,
    _frame_dimensions,
    _measure_loudnorm_filter,
    _parse_loudnorm_stats,
    _run,
    render_cleaned_clip_file,
    render_clip_file,
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


# ── auto-zoom punch-in at peak (Issue 184) ────────────────────────────────────

# The crop expression's escaped comma inside max() is the unique punch-in marker.
_PUNCH_MARKER = "max(0\\,1-abs(t-"


def _render_vf(tmp_path, *, style_preset=None, peak_s=None) -> str:
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
            src, start_s=10.0, end_s=70.0, out_path=out, style_preset=style_preset, peak_s=peak_s
        )
    render_cmd = next((c for c in captured if "-vf" in c), None)
    assert render_cmd is not None
    return render_cmd[render_cmd.index("-vf") + 1]


def test_punch_in_applied_when_enabled_and_peak_in_window(tmp_path):
    vf = _render_vf(tmp_path, style_preset={"zoom_on_peak": True}, peak_s=40.0)
    assert _PUNCH_MARKER in vf
    assert "abs(t-30.000)" in vf  # peak centered at peak_s - start_s = 30s


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

    # Patch compute_reframe_crop to return a deterministic synthetic track.
    from clip_engine.reframe import CropCenterPoint

    fake_track = [
        CropCenterPoint(10.0, 700),
        CropCenterPoint(10.2, 720),
    ]
    fake_script = "0.000 [enter] crop x 396;\n0.200 [enter] crop x 416;"

    with (
        patch("subprocess.run", side_effect=_fake),
        patch("cv2.imread", return_value=fake_img),
        patch("cv2.CascadeClassifier") as mock_cc,
        patch.object(_config_mod.settings, "ACTIVE_SPEAKER_REFRAME_ENABLED", flag_enabled),
        patch.object(_config_mod.settings, "REFRAME_SAMPLE_FPS", 5.0),
        # compute_reframe_crop is imported locally inside render_clip_file via
        # `from clip_engine.reframe import compute_reframe_crop` — patch at source.
        patch(
            "clip_engine.reframe.compute_reframe_crop",
            return_value=(fake_track, fake_script),
        ),
    ):
        mock_cc.return_value.detectMultiScale.return_value = []
        render_clip_file(src, start_s=10.0, end_s=70.0, out_path=out)

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
    assert "crop=" in vf


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
