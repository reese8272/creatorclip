"""Unit tests for the Issue-420 cut/pan planner in clip_engine/reframe.py.

Covers plan_crop_directives (the exact cut policy), segmented smoothing
(a cut = an unsmoothed sendcmd value jump), and compute_dynamic_crop
(fallback ladder + the unified crop-track wire contract). All synthetic —
no mediapipe, no ffmpeg, no media files.
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

from clip_engine.reframe import (
    CropCenterPoint,
    build_sendcmd_script,
    compute_dynamic_crop,
    plan_crop_directives,
    smooth_crop_track,
    smooth_crop_track_segmented,
)
from clip_engine.speaker_map import FaceObs, FaceTrack, SpeakerMapping, Turn

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_track(*centers: int, start_s: float = 0.0, fps: float = 5.0) -> list[CropCenterPoint]:
    interval = 1.0 / fps
    return [CropCenterPoint(start_s + i * interval, cx) for i, cx in enumerate(centers)]


def _frames_iter(frame: object):
    def _gen(video_path, timestamps):
        for ts in timestamps:
            yield (ts, frame)

    return _gen


def _mk_track(track_id: int, cx: float, t0: float, t1: float, *, shot_idx: int = 0) -> FaceTrack:
    obs = []
    t = t0
    i = 0
    while t <= t1 + 1e-9:
        obs.append(FaceObs(t=t, cx=cx, cy=500.0, w=100.0, h=100.0))
        i += 1
        t = t0 + i * 0.2
    return FaceTrack(track_id=track_id, shot_idx=shot_idx, obs=obs)


_PLANNER_DEFAULTS = dict(min_turn_s=0.8, min_distance_frac=0.25, min_shot_s=1.2)


# ---------------------------------------------------------------------------
# plan_crop_directives — the exact cut policy
# ---------------------------------------------------------------------------


class TestPlanCropDirectives:
    """The exact Issue-420 cut policy, pinned threshold by threshold."""

    def _two_speaker_setup(self):
        tracks = [_mk_track(0, 400.0, 10.0, 20.0), _mk_track(1, 1500.0, 10.0, 20.0)]
        mapping = SpeakerMapping(assignments={(0, 0): 0, (0, 1): 1}, confidence=0.9)
        return tracks, mapping

    def test_speaker_change_cuts_with_j_cut_lead(self) -> None:
        tracks, mapping = self._two_speaker_setup()
        turns = [Turn(0, 10.0, 14.0), Turn(1, 14.5, 18.0)]
        cuts, segments = plan_crop_directives(
            turns, mapping, tracks, [], 10.0, 20.0, 1920, **_PLANNER_DEFAULTS
        )
        assert len(cuts) == 1
        cut = cuts[0]
        # J-cut: framing leads the first word by 150ms.
        assert cut.t == pytest.approx(14.35)
        assert cut.speaker == 1
        assert cut.from_track == 0 and cut.to_track == 1
        # Segments tile the window and hand framing across the cut.
        assert len(segments) == 2
        assert segments[0].start == 10.0 and segments[0].track_id == 0
        assert segments[0].end == pytest.approx(14.35)
        assert segments[1].start == pytest.approx(14.35)
        assert segments[1].end == 20.0 and segments[1].track_id == 1

    def test_turn_below_min_turn_s_holds_framing(self) -> None:
        tracks, mapping = self._two_speaker_setup()
        turns = [Turn(0, 10.0, 14.0), Turn(1, 14.5, 15.1)]  # 0.6s < 0.8s
        cuts, segments = plan_crop_directives(
            turns, mapping, tracks, [], 10.0, 20.0, 1920, **_PLANNER_DEFAULTS
        )
        assert cuts == []
        # Structural compare (not dataclass equality): TestLazyImportGuard
        # reloads clip_engine.reframe, which rebinds the CropSegment class.
        assert [(s.start, s.end, s.track_id) for s in segments] == [(10.0, 20.0, 0)]

    def test_small_move_below_distance_frac_holds(self) -> None:
        # Tracks 300px apart < 0.25·1920 = 480px → the EMA pan covers it.
        tracks = [_mk_track(0, 700.0, 10.0, 20.0), _mk_track(1, 1000.0, 10.0, 20.0)]
        mapping = SpeakerMapping(assignments={(0, 0): 0, (0, 1): 1}, confidence=0.9)
        turns = [Turn(0, 10.0, 14.0), Turn(1, 14.5, 18.0)]
        cuts, _ = plan_crop_directives(
            turns, mapping, tracks, [], 10.0, 20.0, 1920, **_PLANNER_DEFAULTS
        )
        assert cuts == []

    def test_min_shot_spacing_suppresses_rapid_second_cut(self) -> None:
        tracks, mapping = self._two_speaker_setup()
        # Cut at 14.35, then a change back at 15.2 → cut would land at 15.05:
        # 15.05 − 14.35 = 0.7 < 1.2 → second cut suppressed.
        turns = [Turn(0, 10.0, 14.0), Turn(1, 14.5, 15.4), Turn(0, 15.2, 18.0)]
        cuts, _ = plan_crop_directives(
            turns, mapping, tracks, [], 10.0, 20.0, 1920, **_PLANNER_DEFAULTS
        )
        assert [c.t for c in cuts] == [pytest.approx(14.35)]

    def test_source_shot_change_always_cuts(self) -> None:
        """Never pan across a source cut — even with no speaker change."""
        tracks, mapping = self._two_speaker_setup()
        turns = [Turn(0, 10.0, 18.0)]
        cuts, _ = plan_crop_directives(
            turns, mapping, tracks, [15.0], 10.0, 20.0, 1920, **_PLANNER_DEFAULTS
        )
        assert [c.t for c in cuts] == [15.0]
        assert cuts[0].speaker is None  # a source cut, not a speaker cut

    def test_speaker_cut_snaps_to_nearby_shot_boundary(self) -> None:
        # A shot change at 14.2 (tracks are per-shot: track 0 lives in shot 0,
        # track 1 in shot 1) with the turn change at 14.5: the J-cut would
        # land at 14.35, within ±0.3 of the boundary → ONE cut at 14.2,
        # coalesced with the speaker change (annotated + retargeted).
        tracks = [
            _mk_track(0, 400.0, 10.0, 14.0, shot_idx=0),
            _mk_track(1, 1500.0, 14.2, 20.0, shot_idx=1),
        ]
        mapping = SpeakerMapping(assignments={(0, 0): 0, (1, 1): 1}, confidence=0.9)
        turns = [Turn(0, 10.0, 14.0), Turn(1, 14.5, 18.0)]
        cuts, segments = plan_crop_directives(
            turns, mapping, tracks, [14.2], 10.0, 20.0, 1920, **_PLANNER_DEFAULTS
        )
        assert [c.t for c in cuts] == [pytest.approx(14.2)]
        assert cuts[0].speaker == 1
        assert cuts[0].to_track == 1
        assert segments[-1].track_id == 1

    def test_future_shot_boundary_snap(self) -> None:
        """J-cut at 14.35 with a boundary at 14.5 (not yet reached) → the
        speaker cut snaps FORWARD onto it and the shot event double-cut is
        consumed."""
        tracks = [
            _mk_track(0, 400.0, 10.0, 14.4, shot_idx=0),
            _mk_track(1, 1500.0, 14.5, 20.0, shot_idx=1),
        ]
        mapping = SpeakerMapping(assignments={(0, 0): 0, (1, 1): 1}, confidence=0.9)
        turns = [Turn(0, 10.0, 14.0), Turn(1, 14.5, 18.0)]
        cuts, _ = plan_crop_directives(
            turns, mapping, tracks, [14.5], 10.0, 20.0, 1920, **_PLANNER_DEFAULTS
        )
        assert [c.t for c in cuts] == [pytest.approx(14.5)]
        assert cuts[0].speaker == 1

    def test_punch_in_pulse_suppresses_speaker_cut(self) -> None:
        tracks, mapping = self._two_speaker_setup()
        turns = [Turn(0, 10.0, 14.0), Turn(1, 14.5, 18.0)]
        cuts, _ = plan_crop_directives(
            turns,
            mapping,
            tracks,
            [],
            10.0,
            20.0,
            1920,
            punch_in_peak_s=14.5,  # cut at 14.35 is inside ±0.6s of the peak
            **_PLANNER_DEFAULTS,
        )
        assert cuts == []

    def test_punch_in_does_not_suppress_source_cut(self) -> None:
        tracks, mapping = self._two_speaker_setup()
        cuts, _ = plan_crop_directives(
            [Turn(0, 10.0, 18.0)],
            mapping,
            tracks,
            [14.5],
            10.0,
            20.0,
            1920,
            punch_in_peak_s=14.5,
            **_PLANNER_DEFAULTS,
        )
        assert [c.t for c in cuts] == [14.5]  # source cuts are mandatory

    def test_off_screen_speaker_holds_framing(self) -> None:
        """A speaker with no mapped track NEVER causes a cut (hold, not center)."""
        tracks, mapping = self._two_speaker_setup()
        turns = [Turn(0, 10.0, 14.0), Turn(9, 14.5, 18.0)]  # speaker 9 unmapped
        cuts, segments = plan_crop_directives(
            turns, mapping, tracks, [], 10.0, 20.0, 1920, **_PLANNER_DEFAULTS
        )
        assert cuts == []
        # Structural compare (not dataclass equality): TestLazyImportGuard
        # reloads clip_engine.reframe, which rebinds the CropSegment class.
        assert [(s.start, s.end, s.track_id) for s in segments] == [(10.0, 20.0, 0)]

    def test_turn_started_before_clip_frames_without_cut(self) -> None:
        tracks, mapping = self._two_speaker_setup()
        turns = [Turn(1, 5.0, 14.0)]  # already talking at clip start
        cuts, segments = plan_crop_directives(
            turns, mapping, tracks, [], 10.0, 20.0, 1920, **_PLANNER_DEFAULTS
        )
        assert cuts == []
        assert segments[0].track_id == 1


# ---------------------------------------------------------------------------
# Segmented smoothing — a cut is an unsmoothed value jump
# ---------------------------------------------------------------------------


class TestSmoothCropTrackSegmented:
    def test_no_cuts_equals_plain_smoothing(self) -> None:
        raw = _make_track(400, 420, 440, 460, 480)
        plain = smooth_crop_track(raw)
        segmented = smooth_crop_track_segmented(raw, [])
        assert [(p.timestamp_s, p.center_x) for p in plain] == [
            (p.timestamp_s, p.center_x) for p in segmented
        ]

    def test_ema_state_resets_at_cut(self) -> None:
        """The first sample at/after a cut takes the NEW segment's raw value —
        no EMA lag, no pan clamp across the cut: an unsmoothed jump."""
        raw = _make_track(*([400] * 5 + [1500] * 5))
        cut_t = raw[5].timestamp_s
        segmented = smooth_crop_track_segmented(raw, [cut_t])
        assert segmented[5].center_x == 1500  # raw value, not a lagged EMA
        # Without the cut the EMA + pan clamp would lag far behind.
        plain = smooth_crop_track(raw)
        assert plain[5].center_x < 1500

    def test_jump_lands_in_sendcmd_values(self) -> None:
        """The cut shows up as a hard value jump between consecutive sendcmd
        lines (Issue 420: cuts are sendcmd value jumps, no new filters)."""
        raw = _make_track(*([400] * 5 + [1500] * 5))
        cut_t = raw[5].timestamp_s
        segmented = smooth_crop_track_segmented(raw, [cut_t])
        script = build_sendcmd_script(segmented, crop_w=607, frame_w=1920, start_s=0.0)
        xs = [int(line.rstrip(";").split()[-1]) for line in script.splitlines()]
        deltas = [abs(b - a) for a, b in zip(xs, xs[1:], strict=False)]
        assert max(deltas) == abs((1500 - 607 // 2) - (400 - 607 // 2)), (
            "the cut must be a full-distance jump between consecutive lines"
        )

    def test_multiple_cuts_multiple_resets(self) -> None:
        raw = _make_track(*([400] * 4 + [1500] * 4 + [800] * 4))
        cuts = [raw[4].timestamp_s, raw[8].timestamp_s]
        segmented = smooth_crop_track_segmented(raw, cuts)
        assert segmented[4].center_x == 1500
        assert segmented[8].center_x == 800

    def test_empty_track(self) -> None:
        assert smooth_crop_track_segmented([], [1.0]) == []


# ---------------------------------------------------------------------------
# compute_dynamic_crop — ladder + wire-contract JSON
# ---------------------------------------------------------------------------


def _two_face_obs(frame, detector, ts):
    return [
        FaceObs(t=ts, cx=400.0, cy=500.0, w=120.0, h=120.0),
        FaceObs(t=ts, cx=1500.0, cy=500.0, w=100.0, h=100.0),
    ]


def _dialogue_segments() -> list[dict]:
    """Speaker 0 talks 10–14s, speaker 1 talks 14.5–19.5s (word-level labels)."""

    def _words(speaker, start, end):
        words = []
        t = start
        while t < end - 1e-9:
            words.append(
                {
                    "word": "w",
                    "start": round(t, 2),
                    "end": round(min(t + 0.25, end), 2),
                    "speaker": speaker,
                }
            )
            t += 0.25
        return words

    return [
        {"start": 10.0, "end": 14.0, "text": "a", "speaker": 0, "words": _words(0, 10.0, 14.0)},
        {"start": 14.5, "end": 19.5, "text": "b", "speaker": 1, "words": _words(1, 14.5, 19.5)},
    ]


class TestComputeDynamicCrop:
    def _run(self, tmp_path, *, cuts_enabled=True, transcript=None, obs=_two_face_obs, shots=None):
        import config as _config_mod

        fake = tmp_path / "v.mp4"
        fake.touch()
        fake_frame = np.zeros((1080, 1920, 3), dtype="uint8")
        with (
            patch("clip_engine.reframe._iter_sampled_frames", _frames_iter(fake_frame)),
            patch("clip_engine.reframe._detect_face_obs", side_effect=obs),
            patch("clip_engine.reframe._shots.detect_shot_changes", return_value=shots or []),
            patch("clip_engine.reframe._downscale_for_hist", return_value=None),
            patch.object(_config_mod.settings, "REFRAME_CUT_ENABLED", cuts_enabled),
        ):
            return compute_dynamic_crop(
                source_path=fake,
                start_s=10.0,
                end_s=20.0,
                frame_width=1920,
                frame_height=1080,
                crop_w=607,
                sample_fps=5.0,
                transcript_segments=transcript,
            )

    def test_speaker_cut_mode_end_to_end(self, tmp_path) -> None:
        result = self._run(tmp_path, transcript=_dialogue_segments())
        assert result.mode == "speaker_cut"
        assert result.track_json["mode"] == "speaker_cut"
        assert len(result.track_json["cuts"]) == 1
        cut = result.track_json["cuts"][0]
        assert cut["speaker"] == 1
        assert cut["from_x"] != cut["to_x"]
        assert result.sendcmd_text  # multi-point script present

    def test_cuts_flag_off_caps_at_face_pan(self, tmp_path) -> None:
        result = self._run(tmp_path, transcript=_dialogue_segments(), cuts_enabled=False)
        assert result.mode == "face_pan"
        assert result.track_json["cuts"] == []

    def test_no_transcript_is_face_pan(self, tmp_path) -> None:
        result = self._run(tmp_path, transcript=None)
        assert result.mode == "face_pan"

    def test_no_faces_is_static(self, tmp_path) -> None:
        result = self._run(tmp_path, transcript=_dialogue_segments(), obs=lambda f, d, ts: [])
        assert result.mode == "static"
        assert result.sendcmd_text == ""
        assert result.track_json["meta"]["fallback"] is True

    def test_track_json_matches_wire_contract(self, tmp_path) -> None:
        """The UNIFIED crop-track contract (Cross-Track Reconciliation §1)."""
        result = self._run(tmp_path, transcript=_dialogue_segments(), shots=[17.0])
        tj = result.track_json
        assert set(tj.keys()) == {
            "version",
            "mode",
            "source",
            "crop",
            "origin_s",
            "duration_s",
            "keyframes",
            "cuts",
            "shots",
            "speakers",
            "meta",
        }
        assert tj["version"] == 1
        assert tj["source"] == {"width": 1920, "height": 1080}
        assert tj["crop"] == {"width": 607, "height": 1080}
        assert tj["origin_s"] == 10.0
        assert tj["duration_s"] == 10.0
        assert tj["speakers"]["count"] == 2
        assert 0.0 <= tj["speakers"]["mapping_confidence"] <= 1.0
        assert tj["meta"]["sample_fps"] == 5.0
        assert tj["shots"] == [{"t": 7.0}]  # clip-relative
        # t clip-relative and monotonic; x within crop bounds.
        ts = [kf["t"] for kf in tj["keyframes"]]
        assert ts == sorted(ts) and ts[0] == 0.0
        assert all(0 <= kf["x"] <= 1920 - 607 for kf in tj["keyframes"])

    def test_keyframe_x_are_the_exact_sendcmd_values(self, tmp_path) -> None:
        """ONE geometry definition: endpoint x == sendcmd x, line for line."""
        result = self._run(tmp_path, transcript=_dialogue_segments())
        script_xs = [int(line.rstrip(";").split()[-1]) for line in result.sendcmd_text.splitlines()]
        json_xs = [kf["x"] for kf in result.track_json["keyframes"]]
        assert script_xs == json_xs
        script_ts = [float(line.split()[0]) for line in result.sendcmd_text.splitlines()]
        json_ts = [kf["t"] for kf in result.track_json["keyframes"]]
        assert script_ts == json_ts

    def test_cut_entry_references_existing_keyframes(self, tmp_path) -> None:
        result = self._run(tmp_path, transcript=_dialogue_segments())
        kf_by_t = {kf["t"]: kf["x"] for kf in result.track_json["keyframes"]}
        for cut in result.track_json["cuts"]:
            assert cut["t"] in kf_by_t
            assert cut["to_x"] == kf_by_t[cut["t"]]

    def test_total_failure_returns_static_result(self, tmp_path) -> None:
        fake = tmp_path / "v.mp4"
        fake.touch()
        with patch("clip_engine.reframe._iter_sampled_frames", side_effect=RuntimeError("boom")):
            result = compute_dynamic_crop(
                source_path=fake,
                start_s=0.0,
                end_s=5.0,
                frame_width=1920,
                frame_height=1080,
                crop_w=607,
            )
        assert result.mode == "static"
        assert result.sendcmd_text == ""
        assert result.track_json["meta"]["fallback"] is True
        assert result.track_json["version"] == 1

    def test_source_shot_change_recorded_in_pan_mode(self, tmp_path) -> None:
        """face_pan still hard-cuts at source shot changes (never pan across)."""
        result = self._run(tmp_path, transcript=None, shots=[15.0])
        assert result.mode == "face_pan"
        assert result.track_json["shots"] == [{"t": 5.0}]
