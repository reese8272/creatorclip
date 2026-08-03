"""Poster-frame extraction (Issue 387).

These tests are NOT optional: `extract_poster_frame` plus its fallback ladder is
~35 statements landing in `clip_engine`, whose per-module coverage floor is 91.0
against a measured 92.5 — untested, it would consume nearly the whole margin.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clip_engine import render


def _argv(mock_run) -> list[str]:
    """The ffmpeg argv from the most recent _run call."""
    return mock_run.call_args_list[-1].args[0]


class TestPosterSeek:
    @pytest.mark.parametrize(
        ("duration", "expected"),
        [
            (None, 3.0),  # unknown → the floor
            (float("inf"), 3.0),  # ffprobe failure sentinel → the floor
            (0.0, 3.0),
            (30.0, 3.0),  # 10% is under the floor
            (100.0, 10.0),  # 10%
            (600.0, 60.0),  # 10% is over the cap
            (10_800.0, 60.0),  # a 3h podcast still seeks 60s, not 18min
        ],
    )
    def test_seek_point(self, duration: float | None, expected: float) -> None:
        assert render.poster_seek_s(duration) == pytest.approx(expected)

    def test_never_seeks_past_the_end_of_a_short_source(self) -> None:
        # A fixed 3s floor would land past the end of a 2s clip.
        assert render.poster_seek_s(2.0) == pytest.approx(1.0)
        assert render.poster_seek_s(0.5) >= 0.0


class TestPosterCommand:
    def test_scale_precedes_thumbnail(self, tmp_path: Path, monkeypatch) -> None:
        """LOAD-BEARING ORDERING, not style.

        `thumbnail=n=40` buffers 40 decoded frames to pick the most
        representative one: ~1 GB at 4K, ~27 MB at 640px. Reversing these two
        filters OOMs the Celery worker on a large upload, and it presents as a
        mysterious ingest failure rather than as a memory error. This test is the
        only thing standing between a future "simplify the filter chain" refactor
        and that outage.
        """
        calls: list[list[str]] = []

        def fake_run(cmd, label, timeout_s=120.0):  # noqa: ANN001, ANN202
            calls.append(cmd)
            (tmp_path / "p.jpg").write_bytes(b"jpegbytes")

        monkeypatch.setattr(render, "_run", fake_run)
        assert render.extract_poster_frame(tmp_path / "src.mp4", tmp_path / "p.jpg", duration_s=60)

        vf = calls[0][calls[0].index("-vf") + 1]
        assert "scale=" in vf and "thumbnail=" in vf
        assert vf.index("scale=") < vf.index("thumbnail="), (
            f"scale must precede thumbnail to bound the frame buffer; got {vf!r}"
        )

    def test_excludes_attached_cover_art(self, tmp_path: Path, monkeypatch) -> None:
        # `-map 0:V:0` with a CAPITAL V. ffmpeg's "best video stream" heuristic
        # will otherwise pick an attached picture (album art) over real footage.
        calls: list[list[str]] = []
        monkeypatch.setattr(
            render,
            "_run",
            lambda cmd, label, timeout_s=120.0: (
                calls.append(cmd),
                (tmp_path / "p.jpg").write_bytes(b"x"),
            )[0],
        )
        render.extract_poster_frame(tmp_path / "src.mp4", tmp_path / "p.jpg", duration_s=60)
        assert "0:V:0" in calls[0]

    def test_never_upscales_a_narrow_source(self, tmp_path: Path, monkeypatch) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(
            render,
            "_run",
            lambda cmd, label, timeout_s=120.0: (
                calls.append(cmd),
                (tmp_path / "p.jpg").write_bytes(b"x"),
            )[0],
        )
        render.extract_poster_frame(tmp_path / "src.mp4", tmp_path / "p.jpg", duration_s=60)
        vf = calls[0][calls[0].index("-vf") + 1]
        assert "min(640,iw)" in vf


class TestPosterFallback:
    def test_retries_at_zero_when_the_seek_produces_nothing(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """`-ss` before `-i` is a fast seek and can land past the end on a VFR or
        broken-index source, exiting 0 with NO output. Hence the explicit
        non-empty check rather than trusting the return code."""
        out = tmp_path / "p.jpg"
        calls: list[list[str]] = []

        def fake_run(cmd, label, timeout_s=120.0):  # noqa: ANN001, ANN202
            calls.append(cmd)
            if len(calls) == 2:  # only the retry writes
                out.write_bytes(b"jpegbytes")

        monkeypatch.setattr(render, "_run", fake_run)
        assert render.extract_poster_frame(tmp_path / "src.mp4", out, duration_s=600)

        assert len(calls) == 2
        assert calls[0][calls[0].index("-ss") + 1] == "60.000"
        assert calls[1][calls[1].index("-ss") + 1] == "0.000"
        # The retry drops the thumbnail picker — if frame selection is what failed,
        # repeating it is not a fallback.
        assert "thumbnail=" not in calls[1][calls[1].index("-vf") + 1]

    def test_retries_when_ffmpeg_raises(self, tmp_path: Path, monkeypatch) -> None:
        out = tmp_path / "p.jpg"
        calls: list[list[str]] = []

        def fake_run(cmd, label, timeout_s=120.0):  # noqa: ANN001, ANN202
            calls.append(cmd)
            if len(calls) == 1:
                raise RuntimeError("ffmpeg poster frame failed")
            out.write_bytes(b"jpegbytes")

        monkeypatch.setattr(render, "_run", fake_run)
        assert render.extract_poster_frame(tmp_path / "src.mp4", out, duration_s=60)
        assert len(calls) == 2

    def test_returns_false_and_never_raises_when_both_attempts_fail(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        def boom(cmd, label, timeout_s=120.0):  # noqa: ANN001, ANN202
            raise RuntimeError("no video stream")

        monkeypatch.setattr(render, "_run", boom)
        # A poster is an index entry. Nothing that produced media should fail
        # because one could not be made.
        assert render.extract_poster_frame(tmp_path / "src.mp4", tmp_path / "p.jpg") is False

    def test_returns_false_on_a_zero_byte_output(self, tmp_path: Path, monkeypatch) -> None:
        out = tmp_path / "p.jpg"
        monkeypatch.setattr(
            render, "_run", lambda cmd, label, timeout_s=120.0: out.write_bytes(b"")
        )
        assert render.extract_poster_frame(tmp_path / "src.mp4", out) is False

    def test_survives_an_os_error(self, tmp_path: Path, monkeypatch) -> None:
        def boom(cmd, label, timeout_s=120.0):  # noqa: ANN001, ANN202
            raise OSError("ffmpeg binary missing")

        monkeypatch.setattr(render, "_run", boom)
        assert render.extract_poster_frame(tmp_path / "src.mp4", tmp_path / "p.jpg") is False


def test_extract_keyframe_still_takes_no_video_filter(tmp_path: Path, monkeypatch) -> None:
    """GUARD. `_extract_keyframe`'s only caller feeds its output to
    `_detect_face_center_x` and clamps the result against `_frame_dimensions`,
    which is in SOURCE pixels. Adding a scale filter there would return face
    coordinates in scaled space against a source-width clamp and silently
    off-centre every legacy-path 9:16 render — with no existing test asserting on
    those pixels, so it would ship green. That is why poster extraction is a
    SIBLING function and not a parameter on this one.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(render, "_run", lambda cmd, label, timeout_s=120.0: calls.append(cmd))
    render._extract_keyframe(tmp_path / "src.mp4", 5.0, tmp_path / "kf.jpg")
    assert "-vf" not in calls[0], "scaling _extract_keyframe breaks the face-crop coordinate space"
