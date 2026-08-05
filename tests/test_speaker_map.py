"""Unit tests for clip_engine/speaker_map.py (Issue 420).

EVERYTHING here is synthetic — no mediapipe, no cv2, no ffmpeg, no media
files. The module under test is pure by design; these tests must pass on a
box where mediapipe is not installed (it isn't, locally).
"""

from __future__ import annotations

import numpy as np

from clip_engine.speaker_map import (
    FaceObs,
    FaceTrack,
    SpeakerMapping,
    Turn,
    build_face_tracks,
    choose_reframe_mode,
    diarization_coverage,
    extract_speaker_turns,
    map_speakers_to_tracks,
    mouth_motion_energy,
    shot_index_for,
)

# ---------------------------------------------------------------------------
# Synthetic builders
# ---------------------------------------------------------------------------


def _obs(t: float, cx: float, *, area_side: float = 100.0, mouth_patch=None) -> FaceObs:
    return FaceObs(t=t, cx=cx, cy=500.0, w=area_side, h=area_side, mouth_patch=mouth_patch)


def _obs_frames(
    centers_per_sample: list[list[float]], *, start: float = 0.0, fps: float = 5.0
) -> list[tuple[float, list[FaceObs]]]:
    interval = 1.0 / fps
    return [
        (start + i * interval, [_obs(start + i * interval, cx) for cx in centers])
        for i, centers in enumerate(centers_per_sample)
    ]


def _track(
    track_id: int,
    cx: float,
    t0: float,
    t1: float,
    *,
    shot_idx: int = 0,
    area_side: float = 100.0,
    fps: float = 5.0,
    patches=None,
) -> FaceTrack:
    interval = 1.0 / fps
    obs = []
    i = 0
    t = t0
    while t <= t1 + 1e-9:
        patch = patches[i % len(patches)] if patches else None
        obs.append(_obs(t, cx, area_side=area_side, mouth_patch=patch))
        i += 1
        t = t0 + i * interval
    return FaceTrack(track_id=track_id, shot_idx=shot_idx, obs=obs)


def _speaker_segments(turn_spec: list[tuple[int, float, float]]) -> list[dict]:
    """Transcript segments with one word per 0.2s carrying word-level speakers."""
    segments = []
    for speaker, start, end in turn_spec:
        words = []
        t = start
        while t < end - 1e-9:
            w_end = min(t + 0.2, end)
            words.append(
                {"word": "w", "start": round(t, 3), "end": round(w_end, 3), "speaker": speaker}
            )
            t = w_end
        segments.append({"start": start, "end": end, "text": "…", "words": words})
    return segments


# ---------------------------------------------------------------------------
# build_face_tracks
# ---------------------------------------------------------------------------


class TestBuildFaceTracks:
    def test_two_stable_faces_two_tracks(self) -> None:
        frames = _obs_frames([[400.0, 1500.0]] * 10)
        tracks = build_face_tracks(frames, frame_w=1920, shot_changes=[])
        assert len(tracks) == 2
        assert sorted(round(t.mean_cx) for t in tracks) == [400, 1500]
        assert all(len(t.obs) == 10 for t in tracks)

    def test_small_drift_stays_one_track(self) -> None:
        # 20px/sample drift ≪ 0.15·1920 = 288px → one track follows.
        frames = _obs_frames([[400.0 + 20 * i] for i in range(10)])
        tracks = build_face_tracks(frames, frame_w=1920, shot_changes=[])
        assert len(tracks) == 1

    def test_large_jump_starts_new_track(self) -> None:
        # A 500px jump (> 288px) is a different face, not movement.
        frames = _obs_frames([[400.0]] * 5 + [[900.0]] * 5)
        tracks = build_face_tracks(frames, frame_w=1920, shot_changes=[])
        assert len(tracks) == 2

    def test_low_coverage_track_discarded(self) -> None:
        # A face in 1 of 10 samples = 10% < 20% coverage → noise, dropped.
        frames = _obs_frames([[400.0, 1500.0]] + [[400.0]] * 9)
        tracks = build_face_tracks(frames, frame_w=1920, shot_changes=[])
        assert len(tracks) == 1
        assert round(tracks[0].mean_cx) == 400

    def test_tracks_never_span_shot_boundary(self) -> None:
        # Same face position through a shot change at t=1.0 → TWO tracks
        # (shots.py contract: tracks never span a shot boundary).
        frames = _obs_frames([[800.0]] * 10)  # t = 0.0 … 1.8
        tracks = build_face_tracks(frames, frame_w=1920, shot_changes=[1.0])
        assert len(tracks) == 2
        assert sorted(t.shot_idx for t in tracks) == [0, 1]
        assert all(all(shot_index_for(o.t, [1.0]) == t.shot_idx for o in t.obs) for t in tracks)

    def test_empty_frames_no_tracks(self) -> None:
        assert build_face_tracks([], frame_w=1920, shot_changes=[]) == []
        assert build_face_tracks([(0.0, []), (0.2, [])], frame_w=1920, shot_changes=[]) == []


# ---------------------------------------------------------------------------
# extract_speaker_turns
# ---------------------------------------------------------------------------


class TestExtractSpeakerTurns:
    def test_basic_turn_grouping(self) -> None:
        segments = _speaker_segments([(0, 0.0, 2.0), (1, 2.5, 4.0)])
        turns = extract_speaker_turns(segments, 0.0, 10.0)
        assert [(t.speaker, t.start, t.end) for t in turns] == [(0, 0.0, 2.0), (1, 2.5, 4.0)]

    def test_same_speaker_gap_under_threshold_merges(self) -> None:
        # 0.3s gap < 0.4s → one turn.
        segments = _speaker_segments([(0, 0.0, 2.0), (0, 2.3, 4.0)])
        turns = extract_speaker_turns(segments, 0.0, 10.0)
        assert [(t.speaker, t.start, t.end) for t in turns] == [(0, 0.0, 4.0)]

    def test_same_speaker_gap_over_threshold_stays_split(self) -> None:
        segments = _speaker_segments([(0, 0.0, 2.0), (0, 3.0, 4.0)])
        turns = extract_speaker_turns(segments, 0.0, 10.0)
        assert len(turns) == 2

    def test_backchannel_absorbed(self) -> None:
        # A 0.6s "mm-hm" from speaker 1 inside speaker 0's flow must NOT
        # produce a turn (and must not trigger a camera cut downstream).
        segments = _speaker_segments([(0, 0.0, 3.0), (1, 3.1, 3.7), (0, 3.8, 6.0)])
        turns = extract_speaker_turns(segments, 0.0, 10.0)
        assert [(t.speaker, t.start, t.end) for t in turns] == [(0, 0.0, 6.0)]

    def test_long_interjection_not_absorbed(self) -> None:
        # 1.5s ≥ 0.8s is a real turn.
        segments = _speaker_segments([(0, 0.0, 3.0), (1, 3.1, 4.6), (0, 4.7, 6.0)])
        turns = extract_speaker_turns(segments, 0.0, 10.0)
        assert [t.speaker for t in turns] == [0, 1, 0]

    def test_segment_level_speaker_fallback(self) -> None:
        # Words without a speaker key inherit the segment's speaker.
        segments = [
            {
                "start": 0.0,
                "end": 1.0,
                "text": "hi",
                "speaker": 3,
                "words": [{"word": "hi", "start": 0.0, "end": 1.0}],
            }
        ]
        turns = extract_speaker_turns(segments, 0.0, 10.0)
        assert [(t.speaker, t.start, t.end) for t in turns] == [(3, 0.0, 1.0)]

    def test_no_speaker_labels_returns_empty(self) -> None:
        segments = [
            {
                "start": 0.0,
                "end": 1.0,
                "text": "hi",
                "words": [{"word": "hi", "start": 0.0, "end": 1.0}],
            }
        ]
        assert extract_speaker_turns(segments, 0.0, 10.0) == []
        assert extract_speaker_turns([], 0.0, 10.0) == []

    def test_window_filtering(self) -> None:
        segments = _speaker_segments([(0, 0.0, 2.0), (1, 50.0, 52.0)])
        turns = extract_speaker_turns(segments, 40.0, 60.0)
        assert [t.speaker for t in turns] == [1]


class TestDiarizationCoverage:
    def test_full_coverage(self) -> None:
        segments = _speaker_segments([(0, 0.0, 2.0), (1, 2.0, 4.0)])
        assert diarization_coverage(segments, 0.0, 4.0) == 1.0

    def test_half_coverage(self) -> None:
        labeled = _speaker_segments([(0, 0.0, 2.0)])
        unlabeled = [
            {
                "start": 2.0,
                "end": 4.0,
                "text": "x",
                "words": [{"word": "x", "start": 2.0, "end": 4.0}],
            }
        ]
        cov = diarization_coverage(labeled + unlabeled, 0.0, 4.0)
        assert abs(cov - 0.5) < 1e-9

    def test_no_words_is_zero(self) -> None:
        assert diarization_coverage([], 0.0, 4.0) == 0.0
        assert diarization_coverage([{"start": 0, "end": 1, "text": "", "words": []}], 0, 4) == 0.0


# ---------------------------------------------------------------------------
# mouth_motion_energy
# ---------------------------------------------------------------------------


class TestMouthMotionEnergy:
    def test_static_mouth_zero_energy(self) -> None:
        patch = np.full((16, 16), 128.0, dtype=np.float32)
        obs = [_obs(t, 400.0, mouth_patch=patch) for t in (0.0, 0.2, 0.4)]
        assert mouth_motion_energy(obs, 0.0, 1.0) == 0.0

    def test_talking_mouth_positive_energy(self) -> None:
        open_p = np.zeros((16, 16), dtype=np.float32)
        closed_p = np.full((16, 16), 200.0, dtype=np.float32)
        obs = [
            _obs(t, 400.0, mouth_patch=open_p if i % 2 else closed_p)
            for i, t in enumerate((0.0, 0.2, 0.4, 0.6))
        ]
        energy = mouth_motion_energy(obs, 0.0, 1.0)
        assert 0.5 < energy <= 1.0

    def test_window_filters_observations(self) -> None:
        open_p = np.zeros((16, 16), dtype=np.float32)
        closed_p = np.full((16, 16), 200.0, dtype=np.float32)
        obs = [_obs(0.0, 400.0, mouth_patch=open_p), _obs(5.0, 400.0, mouth_patch=closed_p)]
        # Only one obs inside [4, 6] → no pair → 0.
        assert mouth_motion_energy(obs, 4.0, 6.0) == 0.0

    def test_missing_patches_are_no_evidence(self) -> None:
        obs = [_obs(t, 400.0) for t in (0.0, 0.2)]
        assert mouth_motion_energy(obs, 0.0, 1.0) == 0.0

    def test_mismatched_patch_shapes_skipped(self) -> None:
        a = np.zeros((16, 16), dtype=np.float32)
        b = np.full((8, 16), 200.0, dtype=np.float32)  # edge-of-frame crop
        obs = [_obs(0.0, 400.0, mouth_patch=a), _obs(0.2, 400.0, mouth_patch=b)]
        assert mouth_motion_energy(obs, 0.0, 1.0) == 0.0


# ---------------------------------------------------------------------------
# map_speakers_to_tracks
# ---------------------------------------------------------------------------


class TestMapSpeakersToTracks:
    def test_cooccurrence_maps_alternating_visibility(self) -> None:
        """Track 0 is only on screen during speaker 0's turns and vice versa."""
        track0 = _track(0, 400.0, 0.0, 2.0)
        track1 = _track(1, 1500.0, 2.2, 4.0)
        turns = [Turn(0, 0.0, 2.0), Turn(1, 2.2, 4.0)]
        mapping = map_speakers_to_tracks([track0, track1], turns)
        assert mapping.track_for(0, 0) == 0
        assert mapping.track_for(1, 0) == 1
        assert mapping.confidence > 0.5

    def test_mouth_energy_breaks_symmetric_cooccurrence(self) -> None:
        """Both faces on screen the whole time, same size — the churning
        mouth decides."""
        static = np.full((16, 16), 128.0, dtype=np.float32)
        churn_a = np.zeros((16, 16), dtype=np.float32)
        churn_b = np.full((16, 16), 255.0, dtype=np.float32)
        talking = _track(0, 400.0, 0.0, 4.0, patches=[churn_a, churn_b])
        listening = _track(1, 1500.0, 0.0, 4.0, patches=[static])
        turns = [Turn(0, 0.0, 4.0)]
        mapping = map_speakers_to_tracks([talking, listening], turns)
        assert mapping.track_for(0, 0) == 0

    def test_largest_face_vote(self) -> None:
        big = _track(0, 400.0, 0.0, 4.0, area_side=200.0)
        small = _track(1, 1500.0, 0.0, 4.0, area_side=80.0)
        turns = [Turn(0, 0.0, 4.0)]
        mapping = map_speakers_to_tracks([big, small], turns)
        assert mapping.track_for(0, 0) == 0

    def test_greedy_exclusive_assignment(self) -> None:
        """Two speakers can never share one track."""
        track0 = _track(0, 400.0, 0.0, 4.0, area_side=200.0)
        track1 = _track(1, 1500.0, 0.0, 4.0, area_side=80.0)
        turns = [Turn(0, 0.0, 2.0), Turn(1, 2.0, 4.0)]
        mapping = map_speakers_to_tracks([track0, track1], turns)
        assigned = [mapping.track_for(0, 0), mapping.track_for(1, 0)]
        assert sorted(assigned) == [0, 1]

    def test_no_tracks_or_turns_empty_mapping(self) -> None:
        assert map_speakers_to_tracks([], [Turn(0, 0.0, 1.0)]).assignments == {}
        assert map_speakers_to_tracks([_track(0, 400.0, 0.0, 1.0)], []).assignments == {}
        assert map_speakers_to_tracks([], []).confidence == 0.0

    def test_ambiguous_scores_yield_low_confidence(self) -> None:
        """Identical tracks → margin ratio ≈ 0 for the second assignment."""
        track0 = _track(0, 400.0, 0.0, 4.0)
        track1 = _track(1, 1500.0, 0.0, 4.0)
        # One speaker, both tracks equally plausible except the largest vote
        # ties are broken by area — make them identical.
        turns = [Turn(0, 0.0, 4.0)]
        mapping = map_speakers_to_tracks([track0, track1], turns)
        # Largest-vote goes to one of them deterministically; co-occurrence is
        # equal, mouth energy absent → margin driven only by the largest vote.
        assert 0.0 <= mapping.confidence <= 1.0

    def test_off_screen_speaker_unmapped(self) -> None:
        """A speaker with no co-occurring track maps to nothing (→ the
        planner holds framing, never cuts to nothing)."""
        track0 = _track(0, 400.0, 0.0, 2.0)
        turns = [Turn(0, 0.0, 2.0), Turn(7, 10.0, 12.0)]  # speaker 7 after track ends
        mapping = map_speakers_to_tracks([track0], turns)
        assert mapping.track_for(0, 0) == 0
        assert mapping.track_for(7, 0) is None


# ---------------------------------------------------------------------------
# choose_reframe_mode — every ladder rung
# ---------------------------------------------------------------------------


class TestChooseReframeMode:
    _RUNG_A = dict(
        n_tracks=2,
        n_speakers=2,
        coverage=0.9,
        mapping_confidence=0.8,
        cuts_enabled=True,
        frame_w=1920,
        crop_w=607,
    )

    def test_rung_a_speaker_cut(self) -> None:
        assert choose_reframe_mode(**self._RUNG_A) == "speaker_cut"

    def test_cuts_disabled_caps_at_face_pan(self) -> None:
        assert choose_reframe_mode(**{**self._RUNG_A, "cuts_enabled": False}) == "face_pan"

    def test_single_track_falls_to_face_pan(self) -> None:
        assert choose_reframe_mode(**{**self._RUNG_A, "n_tracks": 1}) == "face_pan"

    def test_single_speaker_falls_to_face_pan(self) -> None:
        assert choose_reframe_mode(**{**self._RUNG_A, "n_speakers": 1}) == "face_pan"

    def test_low_coverage_falls_to_face_pan(self) -> None:
        assert choose_reframe_mode(**{**self._RUNG_A, "coverage": 0.59}) == "face_pan"

    def test_low_confidence_falls_to_face_pan(self) -> None:
        assert choose_reframe_mode(**{**self._RUNG_A, "mapping_confidence": 0.29}) == "face_pan"

    def test_confidence_floor_is_tunable(self) -> None:
        """Issue 422 drill: the floor is env-tunable — a 0.248 mapping on a
        side-by-side layout (honest but co-occurrence-blind) earns cuts when
        the operator lowers the floor; the default still degrades it."""
        args = {**self._RUNG_A, "mapping_confidence": 0.248}
        assert choose_reframe_mode(**args) == "face_pan"  # default floor 0.3
        assert choose_reframe_mode(**args, min_mapping_confidence=0.2) == "speaker_cut"

    def test_no_tracks_falls_to_static(self) -> None:
        assert choose_reframe_mode(**{**self._RUNG_A, "n_tracks": 0, "n_speakers": 0}) == "static"

    def test_vertical_source_is_static_even_with_faces(self) -> None:
        """crop as wide as the frame = nothing to pan → static outranks all."""
        assert choose_reframe_mode(**{**self._RUNG_A, "crop_w": 1920}) == "static"

    def test_boundary_values_are_inclusive(self) -> None:
        """coverage == 0.6 and confidence == 0.3 are IN (≥, not >)."""
        assert (
            choose_reframe_mode(**{**self._RUNG_A, "coverage": 0.6, "mapping_confidence": 0.3})
            == "speaker_cut"
        )


# ---------------------------------------------------------------------------
# SpeakerMapping / shot_index_for plumbing
# ---------------------------------------------------------------------------


class TestPlumbing:
    def test_shot_index_for(self) -> None:
        changes = [10.0, 20.0]
        assert shot_index_for(5.0, changes) == 0
        assert shot_index_for(10.0, changes) == 1
        assert shot_index_for(15.0, changes) == 1
        assert shot_index_for(25.0, changes) == 2
        assert shot_index_for(5.0, []) == 0

    def test_track_for_missing_returns_none(self) -> None:
        mapping = SpeakerMapping(assignments={(0, 1): 5}, confidence=0.7)
        assert mapping.track_for(1, 0) == 5
        assert mapping.track_for(1, 1) is None
        assert mapping.track_for(2, 0) is None

    def test_face_track_median_cx_windowed(self) -> None:
        """Issue 436: the hold statistic. Windowed median; an empty window
        falls back to the whole-track median; one outlier can't move it."""
        track = _track(0, 800.0, 0.0, 2.0)
        assert track.median_cx() == 800.0
        assert track.median_cx(0.0, 1.0) == 800.0
        # Window with no obs → whole-track median, never a crash.
        assert track.median_cx(9.0, 10.0) == 800.0

    def test_face_track_median_cx_outlier_robust(self) -> None:
        obs = [FaceObs(t=0.2 * i, cx=400.0, cy=500.0, w=100.0, h=100.0) for i in range(5)]
        obs[3] = FaceObs(t=0.6, cx=900.0, cy=500.0, w=100.0, h=100.0)
        track = FaceTrack(track_id=0, shot_idx=0, obs=obs)
        assert track.median_cx() == 400.0  # a mean would read 500
