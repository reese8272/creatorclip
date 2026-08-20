"""Issue 524 — a render must prove its output before reporting success.

Reproduced on the real production function: ffmpeg exits **0** after writing a
truncated file (0.400 s / 28 KB against a 0.6 s request), printing "partial
file / Decoding error" to a stderr the success path discarded. The clip was
uploaded, marked ``render_status=done`` and announced "Clip ready."

``st_size > 0`` does not catch it — 28 KB is not zero. Only the duration does.

These tests deliberately do NOT use the ``stub_render_verification`` fixture
(``tests/conftest.py``) that the command-construction suites opt into: this file
is what keeps that stub honest.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clip_engine.render import (
    _OUTPUT_SHORTFALL_RATIO,
    _probe_output_duration_s,
    _verify_rendered_output,
)


def _fake_output(tmp_path: Path, *, size: int = 28_000) -> Path:
    """A file that passes every size-based check and still may be truncated."""
    out = tmp_path / "out.mp4"
    out.write_bytes(b"\0" * size)
    return out


# ── The duration check ────────────────────────────────────────────────────────


def test_accepts_an_output_of_the_requested_duration(tmp_path: Path) -> None:
    out = _fake_output(tmp_path)
    with patch("clip_engine.render._probe_output_duration_s", return_value=60.0):
        _verify_rendered_output(out, 60.0, "render")  # must not raise


def test_rejects_the_measured_truncation_case(tmp_path: Path) -> None:
    """THE regression test: 0.400s delivered against 0.600s requested.

    Before Issue 524 this was uploaded and announced as ready.
    """
    out = _fake_output(tmp_path)
    with (
        patch("clip_engine.render._probe_output_duration_s", return_value=0.400),
        pytest.raises(RuntimeError, match="short output"),
    ):
        _verify_rendered_output(out, 0.600, "render")


def test_tolerates_a_legitimate_shortfall_within_the_floor(tmp_path: Path) -> None:
    """Keyframe alignment costs a fraction of a second on a long clip.

    Without this, the guard would red-wall every normal render — the failure
    mode that matters more than the one being fixed, because it would break
    the product rather than a claim about it.
    """
    out = _fake_output(tmp_path)
    with patch("clip_engine.render._probe_output_duration_s", return_value=74.6):
        _verify_rendered_output(out, 75.0, "render")


def test_tolerates_an_overshoot(tmp_path: Path) -> None:
    """The check is one-sided: encoder padding may run slightly long."""
    out = _fake_output(tmp_path)
    with patch("clip_engine.render._probe_output_duration_s", return_value=75.4):
        _verify_rendered_output(out, 75.0, "render")


def test_short_clips_are_governed_by_the_ratio_not_the_absolute_floor() -> None:
    """Pins why BOTH floors exist.

    The absolute floor alone (1.0 s) would pass a 0.400 s output for a 0.600 s
    request, because 0.2 s of shortfall is under 1 s. The ratio catches it.
    """
    assert 0.600 * _OUTPUT_SHORTFALL_RATIO > 0.400


# ── Fail-closed on an unverifiable output ─────────────────────────────────────


def test_unprobeable_output_fails_rather_than_passing(tmp_path: Path) -> None:
    """The posture that separates this from ``_source_duration_s``.

    That sibling returns ``inf`` on probe failure so an unknown SOURCE duration
    lets the render proceed. Applying the same leniency to an OUTPUT would
    reinstate exactly the defect this guard exists to catch: no evidence is not
    the same as evidence of success.
    """
    out = _fake_output(tmp_path)
    with (
        patch("clip_engine.render._probe_output_duration_s", return_value=None),
        pytest.raises(RuntimeError, match="unverifiable"),
    ):
        _verify_rendered_output(out, 60.0, "render")


def test_missing_output_fails(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="wrote no file"):
        _verify_rendered_output(tmp_path / "never-written.mp4", 60.0, "render")


def test_empty_output_fails(tmp_path: Path) -> None:
    out = _fake_output(tmp_path, size=0)
    with pytest.raises(RuntimeError, match="empty file"):
        _verify_rendered_output(out, 60.0, "render")


# ── The probe returns None, never a sentinel that reads as success ────────────


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [(1, ""), (0, "not-a-number"), (0, "")],
)
def test_probe_returns_none_on_any_failure(tmp_path: Path, returncode, stdout) -> None:
    with patch("subprocess.run", return_value=MagicMock(returncode=returncode, stdout=stdout)):
        assert _probe_output_duration_s(tmp_path / "x.mp4") is None


def test_probe_returns_none_when_ffprobe_is_missing(tmp_path: Path) -> None:
    with patch("subprocess.run", side_effect=OSError("no ffprobe")):
        assert _probe_output_duration_s(tmp_path / "x.mp4") is None


def test_probe_returns_none_on_timeout(tmp_path: Path) -> None:
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ffprobe", 30)):
        assert _probe_output_duration_s(tmp_path / "x.mp4") is None


# ── Every entry point must call the verifier ─────────────────────────────────
#
# The construction suites stub `_verify_rendered_output` wholesale via the
# `stub_render_verification` fixture, so without these a call site could be
# deleted and 187 tests would stay green. These are the assertions that make
# that stub safe to use.


def _stub_ffmpeg(monkeypatch, tmp_path: Path) -> None:
    """Make the ffmpeg layer a no-op so only the verifier call is under test."""
    import clip_engine.render as render

    monkeypatch.setattr(render, "_run", lambda *a, **k: None)
    monkeypatch.setattr(render, "_source_duration_s", lambda *a, **k: float("inf"))
    monkeypatch.setattr(render, "_frame_dimensions", lambda *a, **k: (1920, 1080))
    monkeypatch.setattr(render, "_detect_face_box", lambda *a, **k: None)
    monkeypatch.setattr(render, "_extract_keyframe", lambda *a, **k: None)


def test_render_clip_file_verifies_its_output(monkeypatch, tmp_path: Path) -> None:
    """Expected duration is end_s - start_s."""
    import clip_engine.render as render

    _stub_ffmpeg(monkeypatch, tmp_path)
    seen: list[tuple] = []
    monkeypatch.setattr(
        render, "_verify_rendered_output", lambda p, exp, label: seen.append((exp, label))
    )
    src = tmp_path / "src.mp4"
    src.write_bytes(b"\0" * 1024)

    render.render_clip_file(src, 10.0, 70.0, tmp_path / "out.mp4")

    assert seen, "render_clip_file did not verify its output (Issue 524)"
    expected, label = seen[-1]
    assert expected == pytest.approx(60.0), "expected duration must be end_s - start_s"
    assert label == "render"


def test_render_cleaned_clip_file_verifies_its_output(monkeypatch, tmp_path: Path) -> None:
    """Expected duration is the sum of the kept ranges."""
    import clip_engine.render as render

    _stub_ffmpeg(monkeypatch, tmp_path)
    seen: list[tuple] = []
    monkeypatch.setattr(
        render, "_verify_rendered_output", lambda p, exp, label: seen.append((exp, label))
    )
    src = tmp_path / "src.mp4"
    src.write_bytes(b"\0" * 1024)

    render.render_cleaned_clip_file(src, [(0.0, 5.0), (10.0, 20.0)], tmp_path / "out.mp4")

    assert seen, "render_cleaned_clip_file did not verify its output (Issue 524)"
    expected, label = seen[-1]
    assert expected == pytest.approx(15.0), "expected duration must be sum(keep_ranges)"
    assert label == "clean render"


def test_render_summary_file_verifies_its_output(monkeypatch, tmp_path: Path) -> None:
    """Expected duration is the sum of the recap segments."""
    import clip_engine.render as render

    _stub_ffmpeg(monkeypatch, tmp_path)
    seen: list[tuple] = []
    monkeypatch.setattr(
        render, "_verify_rendered_output", lambda p, exp, label: seen.append((exp, label))
    )
    src = tmp_path / "src.mp4"
    src.write_bytes(b"\0" * 1024)

    render.render_summary_file(src, [(0.0, 12.0), (30.0, 38.0)], tmp_path / "out.mp4")

    assert seen, "render_summary_file did not verify its output (Issue 524)"
    expected, label = seen[-1]
    assert expected == pytest.approx(20.0), "expected duration must be sum(segments)"
    assert label == "summary render"
