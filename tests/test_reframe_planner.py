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
    CropSegment,
    _hold_points_from_segments,
    build_sendcmd_script,
    compute_dynamic_crop,
    plan_crop_directives,
    plan_pan_holds,
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
# Virtual tripod (Issue 436) — speaker_cut holds
# ---------------------------------------------------------------------------


def _wobble_track(track_id: int, base: float, t0: float, t1: float, *, shot_idx: int = 0):
    """A face track wobbling ±10px around ``base`` — real detector behavior."""
    obs = []
    i = 0
    t = t0
    while t <= t1 + 1e-9:
        obs.append(FaceObs(t=t, cx=base + (10.0 if i % 2 else -10.0), cy=500.0, w=100.0, h=100.0))
        i += 1
        t = t0 + i * 0.2
    return FaceTrack(track_id=track_id, shot_idx=shot_idx, obs=obs)


class TestHoldPointsFromSegments:
    def test_wobble_collapses_to_one_constant_per_segment(self) -> None:
        """The pre-436 live failure: detector wobble became ~4 crop moves/s.
        Now: sendcmd line count == segment count, and the only inter-line
        delta is the full cut jump."""
        tracks = [_wobble_track(1, 400.0, 0.0, 2.0), _wobble_track(2, 1500.0, 2.0, 4.0)]
        segments = [CropSegment(0.0, 2.0, 1), CropSegment(2.0, 4.0, 2)]
        pts = _hold_points_from_segments(segments, tracks, 1920)

        assert [(p.timestamp_s) for p in pts] == [0.0, 2.0]
        assert pts[0].center_x == 400  # median of ±10px wobble = the base
        assert pts[1].center_x == 1500
        script = build_sendcmd_script(pts, crop_w=607, frame_w=1920, start_s=0.0)
        xs = [int(line.rstrip(";").split()[-1]) for line in script.splitlines()]
        assert len(xs) == len(segments)
        assert abs(xs[1] - xs[0]) == abs((1500 - 607 // 2) - (400 - 607 // 2)), (
            "the cut must be a full-distance jump between consecutive lines"
        )

    def test_median_ignores_detector_outlier(self) -> None:
        """One 900px outlier among 400px obs: the hold stays at 400 (a mean
        would drift and shift the emitted x)."""
        obs = [FaceObs(t=0.2 * i, cx=400.0, cy=500.0, w=100.0, h=100.0) for i in range(5)]
        obs[2] = FaceObs(t=0.4, cx=900.0, cy=500.0, w=100.0, h=100.0)
        track = FaceTrack(track_id=1, shot_idx=0, obs=obs)
        pts = _hold_points_from_segments([CropSegment(0.0, 1.0, 1)], [track], 1920)
        assert pts[0].center_x == 400

    def test_offscreen_segment_holds_previous(self) -> None:
        tracks = [_wobble_track(1, 400.0, 0.0, 2.0)]
        segments = [CropSegment(0.0, 2.0, 1), CropSegment(2.0, 4.0, None)]
        pts = _hold_points_from_segments(segments, tracks, 1920)
        assert pts[1].center_x == pts[0].center_x
        assert pts[1].is_fallback is False  # deliberate framing, not a fallback

    def test_no_signal_at_all_is_center_fallback(self) -> None:
        pts = _hold_points_from_segments([CropSegment(0.0, 2.0, None)], [], 1920)
        assert len(pts) == 1
        assert pts[0].center_x == 960
        assert pts[0].is_fallback is True


# ---------------------------------------------------------------------------
# Virtual tripod (Issue 436) — pan-rung deadband holds
# ---------------------------------------------------------------------------


def _plan(raw, cuts=()):
    return plan_pan_holds(
        list(raw),
        list(cuts),
        sample_fps=5.0,
        deadband_px=91.0,  # 0.15 × 607 — the shipped default at 1080p
        retarget_s=1.0,
        glide_px_per_s=600.0,
        glide_sample_fps=30.0,
    )


class TestPlanPanHolds:
    def test_oscillation_inside_deadband_holds_forever(self) -> None:
        """The measured live failure: ±40px see-saw 5×/s. Inside the deadband
        the whole 4s span collapses to ONE hold keyframe (→ static render)."""
        raw = _make_track(*([760, 840] * 10))
        out = _plan(raw)
        assert len(out) == 1
        assert abs(out[0].center_x - 800) <= 40

    def test_transient_spike_never_retargets(self) -> None:
        """A 0.6s excursion (3 samples < the 1.0s window) returns before the
        deviation is continuously sustained — the tripod holds."""
        centers = [800] * 10 + [1200] * 3 + [800] * 10
        out = _plan(_make_track(*centers))
        assert len(out) == 1

    def test_sustained_move_earns_exactly_one_glide(self) -> None:
        raw = _make_track(*([800] * 10 + [1400] * 20))
        out = _plan(raw)
        assert out[0].center_x == 800
        xs = [p.center_x for p in out]
        assert xs == sorted(xs)  # monotonic — one glide, no see-saw
        assert out[-1].center_x == 1400
        # Glide duration ≈ 600px / 600px/s = 1s at 30fps ≈ 31 ramp points.
        glide = out[1:]
        assert 25 <= len(glide) <= 35
        # After the glide the hold emits NOTHING (sendcmd holds the last value).
        assert out[-1].timestamp_s < raw[-1].timestamp_s

    def test_fallback_samples_never_trigger(self) -> None:
        """Detection loss must not move the tripod — fallbacks don't vote."""
        raw = _make_track(*([800] * 5))
        raw += [CropCenterPoint(1.0 + 0.2 * i, 960, is_fallback=True) for i in range(15)]
        out = _plan(raw)
        assert len(out) == 1
        assert out[0].center_x == 800

    def test_shot_cut_splits_spans_and_lands_a_boundary_keyframe(self) -> None:
        """Holds re-derive per shot; the cut jump lands exactly ON the boundary
        time (cut entries + the frontend snap key off that alignment)."""
        raw = _make_track(*([400] * 10 + [1400] * 10))
        cut_t = raw[10].timestamp_s
        out = _plan(raw, cuts=[cut_t])
        assert [p.timestamp_s for p in out] == [0.0, cut_t]
        assert [p.center_x for p in out] == [400, 1400]
        # No glide may cross the boundary — everything is a hold here.
        script = build_sendcmd_script(out, crop_w=607, frame_w=1920, start_s=0.0)
        assert len(script.splitlines()) == 2

    def test_empty_track(self) -> None:
        assert _plan([]) == []


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
    def _run(
        self,
        tmp_path,
        *,
        cuts_enabled=True,
        transcript=None,
        obs=_two_face_obs,
        shots=None,
        region=None,
        frame_width=1920,
        frame_height=1080,
        crop_w=607,
        seen_shapes=None,
    ):
        import config as _config_mod

        fake = tmp_path / "v.mp4"
        fake.touch()
        fake_frame = np.zeros((1080, 1920, 3), dtype="uint8")

        def _obs_capture(frame, detector, ts):
            if seen_shapes is not None:
                seen_shapes.append(frame.shape)
            return obs(frame, detector, ts)

        with (
            patch("clip_engine.reframe._iter_sampled_frames", _frames_iter(fake_frame)),
            patch("clip_engine.reframe._detect_face_obs", side_effect=_obs_capture),
            patch("clip_engine.reframe._shots.detect_shot_changes", return_value=shots or []),
            patch("clip_engine.reframe._downscale_for_hist", return_value=None),
            patch.object(_config_mod.settings, "REFRAME_CUT_ENABLED", cuts_enabled),
        ):
            return compute_dynamic_crop(
                source_path=fake,
                start_s=10.0,
                end_s=20.0,
                frame_width=frame_width,
                frame_height=frame_height,
                crop_w=crop_w,
                sample_fps=5.0,
                transcript_segments=transcript,
                region=region,
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
        # Issue 436 virtual tripod: one hold keyframe per segment — a 1-cut
        # clip is exactly 2 keyframes, not one per 5 Hz sample.
        assert len(result.track_json["keyframes"]) == 2

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

    # ── Region composition (Issue 433) ───────────────────────────────────────

    def test_region_slices_frames_before_detection(self, tmp_path) -> None:
        """The detector must see REGION-shaped frames — every downstream
        coordinate becomes region-relative by construction."""
        shapes: list = []
        region = (200, 100, 1400, 900)
        crop_w = int(900 * 1080 / 1920)  # 506
        self._run(
            tmp_path,
            region=region,
            frame_width=1400,
            frame_height=900,
            crop_w=crop_w,
            seen_shapes=shapes,
        )
        assert shapes and all(s == (900, 1400, 3) for s in shapes)

    def test_region_recorded_in_track_json(self, tmp_path) -> None:
        region = (200, 100, 1400, 900)
        crop_w = int(900 * 1080 / 1920)
        result = self._run(
            tmp_path, region=region, frame_width=1400, frame_height=900, crop_w=crop_w
        )
        tj = result.track_json
        # `source` IS the pan-space (region) rect — frontend math unchanged.
        assert tj["source"] == {"width": 1400, "height": 900}
        assert tj["region"] == {"x": 200, "y": 100, "width": 1400, "height": 900}
        assert all(0 <= kf["x"] <= 1400 - crop_w for kf in tj["keyframes"])

    def test_no_region_omits_the_key(self, tmp_path) -> None:
        result = self._run(tmp_path)
        assert "region" not in result.track_json

    def test_exception_fallback_carries_region_and_region_center(self, tmp_path) -> None:
        """Total detection failure with a region active: the static fallback
        centers in REGION space and the track still records the region."""
        region = (200, 100, 1400, 900)
        crop_w = int(900 * 1080 / 1920)

        def _boom(frame, detector, ts):
            raise RuntimeError("detector exploded")

        result = self._run(
            tmp_path,
            obs=_boom,
            region=region,
            frame_width=1400,
            frame_height=900,
            crop_w=crop_w,
        )
        assert result.mode == "static"
        assert result.track_points[0].center_x == 1400 // 2
        assert result.track_json["region"] == {"x": 200, "y": 100, "width": 1400, "height": 900}
        assert result.track_json["source"] == {"width": 1400, "height": 900}

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
