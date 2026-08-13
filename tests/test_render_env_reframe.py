"""Real-media reframe lane (Issue 478 leg 3) — marker ``render_env``.

Consumes the ``tests/fixtures/reframe_seats/`` frames (the only surviving
real frames of the Issue-450 defect after the 2026-08-13 source purge)
through the REAL pipeline: MediaPipe BlazeFace detection → per-shot face
tracks → mouth-motion energy → speaker→track mapping. The Issue-450
regression test hand-supplies the ``SpeakerMapping``; this test COMPUTES one
from the frames, so a detector/track/energy regression that would silently
break the mapping in production fails here.

SCOPE, measured 2026-08-13 (owner-escalation item — see the module test
docstrings): the fixture PNGs are 480 px wide (region space was 1704 px).
At the production detector confidence (0.5) the LEFT participant — head
down, cap brim occluding the face — is detected in ZERO of the 12 frames at
1x/2x/3x/4x upscale; he only appears at min_detection_confidence=0.1, where
phantom detections spawn three spurious left-region tracks and the mapping
lands on a phantom (cx≈189) — unusable as ground truth. The full-resolution
source is purged, so the two-seat hold and the right>left energy ORDERING
are not provable from this fixture through the real detector; they stay
covered by the synthetic Issue-450 tests. What IS frozen here, at the
production configuration, unmocked: the speaker's seat geometry, a live
mouth-motion signal, and the mapping landing the diarized speaker on that
seat — plus the honest single-track seat-plan gate.

Assertion values frozen from a 2026-08-13 measurement (mediapipe 1.0.0,
pinned hub BlazeFace model, min_detection_confidence=0.5, 480 px frames):
1 track, 9/12 obs, median_cx 350.0 (region ground truth 1257.5 × 480/1704 =
354.2), mouth energy 0.1515, mapping {(0,0): track} at confidence 1.0.

Deliberately NO skipif: a missing mediapipe/model/cv2 must FAIL this lane —
the CI render-env step provisions all three (Issue 478).
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.render_env

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "reframe_seats"
_WINDOW_S = 34.55  # rank 1's window length, per the fixture README
_FRAME_W = 480
_REGION_W = 1704
# Region-space ground truth from the fixture README, scaled to fixture space.
_RIGHT_SEAT_CX = 1257.5 * _FRAME_W / _REGION_W  # ≈ 354.2
_SEAT_TOL_PX = 15.0


def _real_obs_frames():
    """Detect faces on every fixture frame through the REAL production path."""
    import cv2

    from clip_engine.reframe import (
        _close_face_detector,
        _create_face_detector,
        _detect_face_obs,
    )

    detector = _create_face_detector()
    assert detector is not None, (
        "MediaPipe FaceDetector unavailable — the render-env lane must provision "
        "mediapipe + the pinned BlazeFace model (MEDIAPIPE_FACE_MODEL_PATH); a "
        "missing detector FAILS this lane, never skips it (Issue 478)"
    )
    pngs = sorted(_FIXTURES.glob("rank01_two_shot_*.png"))
    assert len(pngs) == 12, f"fixture must hold 12 frames, found {len(pngs)}"

    obs_frames = []
    try:
        for i, png in enumerate(pngs):
            frame = cv2.imread(str(png))
            assert frame is not None and frame.shape[1] == _FRAME_W, png
            ts = i * _WINDOW_S / (len(pngs) - 1)
            obs_frames.append((ts, _detect_face_obs(frame, detector, ts)))
    finally:
        _close_face_detector(detector)
    return obs_frames


@pytest.fixture(scope="module")
def obs_frames():
    return _real_obs_frames()


@pytest.fixture(scope="module")
def tracks(obs_frames):
    from clip_engine import speaker_map as sm

    return sm.build_face_tracks(obs_frames, _FRAME_W, shot_changes=[])


def test_detection_finds_the_speakers_seat(obs_frames, tracks):
    """The stable track sits on the RIGHT seat — the speaker's camera — at
    the region ground truth position, and covers most of the window."""
    assert len(tracks) == 1, (
        f"expected the one stable 480px-detectable track, got {len(tracks)}: "
        f"{[(t.track_id, round(t.median_cx(), 1)) for t in tracks]}"
    )
    track = tracks[0]
    assert len(track.obs) >= 8  # measured 9/12 sampled frames
    assert abs(track.median_cx() - _RIGHT_SEAT_CX) <= _SEAT_TOL_PX, (
        f"track median_cx {track.median_cx():.1f} not at the right seat "
        f"({_RIGHT_SEAT_CX:.1f} ± {_SEAT_TOL_PX})"
    )
    # And it spans the window, not a corner of it.
    assert track.start_t == 0.0 and track.end_t == pytest.approx(_WINDOW_S)


def test_mouth_motion_energy_is_live_signal(tracks):
    """The real mouth patches must carry actual talking energy (measured
    0.1515) — zero here means the patch extraction or diff pipeline broke,
    which would silently gut the map_speakers_to_tracks mouth vote."""
    from clip_engine import speaker_map as sm

    energy = sm.mouth_motion_energy(tracks[0].obs, 0.0, _WINDOW_S)
    assert 0.05 <= energy <= 0.5, f"mouth energy {energy:.4f} outside frozen band"


def test_mapping_lands_diarized_speaker_on_the_speaking_seat(tracks):
    """The fixture window's real diarization was ONE speaker with full
    coverage (README). Through the real vote, that speaker must map onto the
    right-seat track and speaking_track_for_span must return it — the exact
    lookup chain Issue 450 wired into the seat planner."""
    from clip_engine import speaker_map as sm

    turns = [sm.Turn(speaker=0, start=0.0, end=_WINDOW_S)]
    mapping = sm.map_speakers_to_tracks(tracks, turns)
    assert mapping.assignments == {(0, 0): tracks[0].track_id}
    assert mapping.confidence == 1.0  # single unambiguous candidate

    speaking = sm.speaking_track_for_span(mapping, turns, tracks, [], 0.0, _WINDOW_S)
    assert speaking is tracks[0]
    assert abs(speaking.median_cx() - _RIGHT_SEAT_CX) <= _SEAT_TOL_PX


def test_seat_hold_plan_declines_single_track(obs_frames, tracks):
    """With only one 480px-detectable track the seat planner must return None
    (its documented <2-tracks gate) and hand off to the pan planner — frozen
    honestly rather than pretending this fixture proves the two-seat hold."""
    from clip_engine import speaker_map as sm
    from clip_engine.reframe import _seat_hold_plan

    turns = [sm.Turn(speaker=0, start=0.0, end=_WINDOW_S)]
    mapping = sm.map_speakers_to_tracks(tracks, turns)
    crop_w = int(round(309 * _FRAME_W / _REGION_W))  # region crop_w scaled ≈ 87
    plan = _seat_hold_plan(
        obs_frames, tracks, [], 0.0, _WINDOW_S, crop_w, mapping=mapping, turns=turns
    )
    assert plan is None
