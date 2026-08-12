"""
Per-frame active-speaker reframe (Issue 189, extended by Issue 420).

Replaces the single-keyframe Haar crop in render.py with a time-varying
9:16 (or any aspect) crop path driven by per-frame face tracking, and — when
diarization + shot detection + speaker→face mapping agree — hard CUTS between
speaker framings on turn changes (Lane L26, Issue 420).

Architecture:
  1. Sample frames from the clip at ``sample_fps`` (default 5 fps) using ONE
     ``cv2.VideoCapture`` with sequential ``grab()``/``retrieve()`` — the old
     per-sample open+seek pattern was the dominant reframe latency.
  2. For each frame run MediaPipe BlazeFace face detection (lazy import so
     the app and test suite remain importable without mediapipe installed),
     keeping full observations: box + 6 keypoints + a mouth patch.
  3. Detect source shot changes (``clip_engine/shots.py``), extract diarized
     speaker turns and map speakers onto per-shot face tracks
     (``clip_engine/speaker_map.py``), then choose the reframe mode via the
     fallback ladder ``speaker_cut → face_pan → static``.
  4. Plan crop directives: cut on speaker change (J-cut 150 ms lead, snapped
     to shot boundaries, spaced ≥ REFRAME_MIN_SHOT_S), ALWAYS cut at source
     shot changes, hold framing for off-screen speakers, suppress cuts inside
     the punch-in pulse.
  5. Build the VIRTUAL-TRIPOD crop path (Issue 436): one constant hold per
     speaker segment (the owning track's windowed median); the pan rung holds
     a median position and re-frames only after a sustained deadband breach,
     via one explicit linear glide. No per-sample following anywhere — the
     measured live failure was detector wobble re-emitted 5×/s.
  6. Emit an ffmpeg ``sendcmd`` script with one line per keyframe (holds and
     glide ramps — typically tens of lines, not hundreds). A cut is just a
     value jump between consecutive sendcmd lines — no new filter machinery.

The heavy path is intentionally designed to be:
  - LAZY: mediapipe and cv2 are imported inside functions, not at module level,
    so the app starts and tests run without these packages installed.
  - GATED: ``render.py`` only calls this module when
    ``settings.ACTIVE_SPEAKER_REFRAME_ENABLED`` is True (default False).
  - FALLBACK-SAFE: every rung of the ladder degrades toward today's behavior,
    never past it; total failure returns a single center keyframe
    (byte-compatible with the legacy path).

Verify scope:
  Unit-testable: geometry, tracks, turns, mapping, ladder, planner, hold/glide
  planning, sendcmd text, track JSON — all verified with synthetic inputs.
  render-env/staging-pending: actual ffmpeg crop output on real multi-speaker
  media (Issue 422 staging checklist / Issue 189 unlock criteria).

Cites Principle 4 (Pattern interrupt — the crop follows the conversation) and
Principle 11 (Audience-fit — per-creator quality, not a generic virality
signal).
"""

from __future__ import annotations

import logging
import math
import statistics
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from clip_engine import shots as _shots
from clip_engine import speaker_map as _sm

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Frames-per-second to sample for detection. 5 fps is enough for talking-head
# content (a face position changes slowly between turns) and keeps compute
# proportional to clip duration without decoding every frame.
_DEFAULT_SAMPLE_FPS: float = 5.0

# A pan-hold retarget glide shorter than about a quarter second reads as a
# jump, not a camera move (Issue 436 virtual tripod). Module constant, not
# config: it is a perceptual floor, not a tuning knob.
_GLIDE_MIN_S: float = 0.25

# Minimum detection confidence for MediaPipe BlazeFace to be counted as a
# real face detection rather than noise.
_MP_MIN_CONFIDENCE: float = 0.5

# ffmpeg sendcmd timing precision (decimal places for seconds).
_SENDCMD_TIME_DECIMALS: int = 3

# Cut planner (Issue 420). The J-cut lead makes the framing change LEAD the
# incoming speaker's first word by 150 ms — the editorial convention that a
# reaction shot pre-empts the line. Cuts within ±300 ms of a detected shot
# boundary snap onto it (one visual event, not two), and speaker cuts inside
# ±0.6 s of the punch-in peak are suppressed so the zoom pulse and a framing
# jump never stack (0.6 s = render.py's _PUNCH_IN_RAMP_S half-width).
_J_CUT_LEAD_S: float = 0.150
_SHOT_SNAP_S: float = 0.300
_PUNCH_IN_SUPPRESS_S: float = 0.6

# BlazeFace keypoint order: right eye, left eye, nose tip, MOUTH CENTER,
# right tragion, left tragion.
_MOUTH_KEYPOINT_INDEX: int = 3
# Half-size (px) of the grayscale mouth patch cut around the mouth keypoint.
_MOUTH_PATCH_HALF_PX: int = 8

# Downscale target for the histogram-fallback samples handed to shots.py —
# tiny on purpose: ~450 samples × 32×18×3 bytes ≈ 0.8 MB per 90 s clip.
_HIST_SAMPLE_W: int = 32
_HIST_SAMPLE_H: int = 18


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


class CropCenterPoint:
    """A single (timestamp, center_x) measurement in the crop track.

    ``center_x`` is in source-video pixels, 0-indexed from the left edge of
    the frame.  ``is_fallback`` is True when no face was detected and the
    frame-center default was used instead.
    """

    __slots__ = ("timestamp_s", "center_x", "is_fallback")

    def __init__(self, timestamp_s: float, center_x: int, *, is_fallback: bool = False) -> None:
        self.timestamp_s = timestamp_s
        self.center_x = center_x
        self.is_fallback = is_fallback

    def __repr__(self) -> str:
        tag = " [fallback]" if self.is_fallback else ""
        return f"CropCenterPoint(t={self.timestamp_s:.3f}s, cx={self.center_x}px{tag})"


@dataclass(frozen=True)
class CropCut:
    """A planned hard cut in the crop track (source-relative seconds).

    ``speaker`` is the diarized speaker whose turn triggered the cut, or
    ``None`` for a mandatory source shot-change cut. Track ids refer to
    :class:`clip_engine.speaker_map.FaceTrack`.
    """

    t: float
    speaker: int | None
    from_track: int | None
    to_track: int | None


@dataclass(frozen=True)
class CropSegment:
    """One inter-cut span of the clip and the face track that owns framing.

    ``track_id`` None = no mapped on-screen face — framing holds (previous
    value, or frame center at clip start).
    """

    start: float
    end: float
    track_id: int | None


@dataclass
class ReframeResult:
    """Everything one dynamic-crop computation produces (Issue 420/421).

    ``track_points`` + ``sendcmd_text`` drive the ffmpeg render;
    ``track_json`` is the UNIFIED wire-contract dict persisted to
    ``clips.reframe_track_jsonb`` and served at ``GET /clips/{id}/crop-track``
    — its keyframe ``x`` values ARE the sendcmd values (one geometry
    definition). ``mode`` is the ladder outcome:
    ``speaker_cut`` | ``face_pan`` | ``static``.
    """

    track_points: list[CropCenterPoint] = field(default_factory=list)
    sendcmd_text: str = ""
    track_json: dict = field(default_factory=dict)
    mode: str = "static"


# ---------------------------------------------------------------------------
# MediaPipe face detection (lazy import)
# ---------------------------------------------------------------------------


def _create_face_detector() -> object | None:
    """Build ONE MediaPipe Tasks FaceDetector for reuse across sampled frames.

    Hoisted out of the per-frame path (Issue 352 Batch H): constructing the
    detector parses the model asset, so building it per frame cost 300–450
    constructions per 60–90s clip at 5 fps. BlazeFace IMAGE-mode ``detect()``
    is safe to reuse serially within one thread, which is exactly the Celery
    render worker's execution model.

    Returns None when mediapipe is unavailable or the model asset is missing —
    callers then fall back to frame-center for every frame.
    """
    try:
        # Lazy import: mediapipe is an optional ~200 MB native package.
        # The Tasks API (mediapipe >= 0.10) is the current 2026 standard;
        # the legacy `solutions.face_detection` is deprecated.
        import mediapipe as mp  # noqa: PLC0415

        model_path = _mediapipe_model_path()
        if not model_path:
            return None

        BaseOptions = mp.tasks.BaseOptions
        FaceDetector = mp.tasks.vision.FaceDetector
        FaceDetectorOptions = mp.tasks.vision.FaceDetectorOptions
        VisionRunningMode = mp.tasks.vision.RunningMode

        options = FaceDetectorOptions(
            base_options=BaseOptions(model_asset_path=model_path),
            running_mode=VisionRunningMode.IMAGE,
            min_detection_confidence=_MP_MIN_CONFIDENCE,
        )
        return FaceDetector.create_from_options(options)
    except Exception as exc:
        logger.debug("MediaPipe face detector unavailable: %s", exc)
        return None


def _close_face_detector(detector: object | None) -> None:
    """Release a detector created by :func:`_create_face_detector` (best effort)."""
    close = getattr(detector, "close", None)
    if callable(close):
        try:
            close()
        except Exception as exc:
            logger.debug("MediaPipe face detector close failed: %s", exc)


def _extract_mouth_patch(frame_bgr: object, mouth_xy: tuple[float, float]) -> object | None:
    """Small grayscale patch around the mouth keypoint (mouth-motion signal).

    Fixed ±``_MOUTH_PATCH_HALF_PX`` window; may be smaller at frame edges —
    ``speaker_map.mouth_motion_energy`` skips shape-mismatched pairs.
    """
    try:
        import numpy as np

        arr = np.asarray(frame_bgr)
        h, w = arr.shape[:2]
        x, y = int(round(mouth_xy[0])), int(round(mouth_xy[1]))
        x0, x1 = max(0, x - _MOUTH_PATCH_HALF_PX), min(w, x + _MOUTH_PATCH_HALF_PX)
        y0, y1 = max(0, y - _MOUTH_PATCH_HALF_PX), min(h, y + _MOUTH_PATCH_HALF_PX)
        patch = arr[y0:y1, x0:x1]
        if patch.size == 0:
            return None
        return patch.mean(axis=2).astype("float32")
    except Exception:
        return None


def _detect_face_obs(
    frame_bgr: object,  # numpy ndarray — typed as object to avoid numpy import at module level
    detector: object | None,
    timestamp_s: float,
) -> list[_sm.FaceObs]:
    """Detect full face observations (box + 6 BlazeFace keypoints + mouth patch).

    Returns observations sorted largest-face-first; ``[]`` when no detector is
    available, nothing is found, or anything fails (fallback point upstream).
    """
    if detector is None:
        return []
    try:
        import mediapipe as mp  # noqa: PLC0415
        import numpy as np

        # MediaPipe Tasks API expects RGB; cv2 produces BGR.
        frame_rgb = np.ascontiguousarray(np.asarray(frame_bgr)[:, :, ::-1])
        h, w = frame_rgb.shape[:2]
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
        result = detector.detect(mp_image)  # type: ignore[attr-defined]

        obs_list: list[_sm.FaceObs] = []
        for det in result.detections or []:
            bb = det.bounding_box
            keypoints = tuple((float(kp.x) * w, float(kp.y) * h) for kp in (det.keypoints or []))
            mouth_patch = None
            if len(keypoints) > _MOUTH_KEYPOINT_INDEX:
                mouth_patch = _extract_mouth_patch(frame_bgr, keypoints[_MOUTH_KEYPOINT_INDEX])
            obs_list.append(
                _sm.FaceObs(
                    t=timestamp_s,
                    cx=bb.origin_x + bb.width / 2.0,
                    cy=bb.origin_y + bb.height / 2.0,
                    w=float(bb.width),
                    h=float(bb.height),
                    keypoints=keypoints,
                    mouth_patch=mouth_patch,
                )
            )
        obs_list.sort(key=lambda o: o.area, reverse=True)
        return obs_list
    except Exception as exc:
        logger.debug("MediaPipe face detection unavailable: %s", exc)
        return []


def _detect_faces_mediapipe(
    frame_bgr: object,
    frame_width: int,
    detector: object | None = None,
) -> list[int]:
    """Legacy wrapper kept for the pan path + existing tests (Issue 420).

    Returns face center-x values in descending bounding-box-area order
    (largest face first — the active-speaker proxy of the pan rung), by
    delegating to :func:`_detect_face_obs`.
    """
    _ = frame_width  # kept for signature compatibility
    return [int(round(o.cx)) for o in _detect_face_obs(frame_bgr, detector, 0.0)]


def _mediapipe_model_path() -> str:
    """Return the filesystem path to the BlazeFace model asset.

    The MediaPipe Tasks FaceDetector requires a metadata-bearing model bundle
    from the model hub (``blaze_face_short_range.tflite`` / a ``.task`` bundle)
    — NOT the legacy Solutions graph asset
    ``modules/face_detection/face_detection_short_range.tflite``, which fails
    at ``create_from_options`` (Issue 352 Batch H).

    Resolution order:
      1. ``settings.MEDIAPIPE_FACE_MODEL_PATH`` — the pinned hub asset shipped
         in the render image (Dockerfile fetches it alongside the fonts).
      2. A ``.task`` bundle inside the installed mediapipe package, if present.
      3. "" — callers fall back to frame-center for the whole track.
    """
    from config import settings  # noqa: PLC0415

    configured = (settings.MEDIAPIPE_FACE_MODEL_PATH or "").strip()
    if configured:
        if Path(configured).exists():
            return configured
        logger.warning(
            "MEDIAPIPE_FACE_MODEL_PATH=%s does not exist; "
            "face detection will fall back to frame center.",
            configured,
        )
        return ""

    try:
        import mediapipe as mp  # noqa: PLC0415

        pkg_root = Path(mp.__file__).parent
        tasks_bundle = pkg_root / "tasks" / "vision" / "face_detector.task"
        if tasks_bundle.exists():
            return str(tasks_bundle)
        logger.warning(
            "No Tasks-compatible BlazeFace model found (MEDIAPIPE_FACE_MODEL_PATH unset, "
            "no bundle under %s); face detection will fall back to frame center.",
            pkg_root,
        )
        return ""
    except ImportError:
        return ""


# ---------------------------------------------------------------------------
# OpenCV frame extraction (lazy import) — ONE capture per clip (Issue 420)
# ---------------------------------------------------------------------------


def _iter_sampled_frames(
    video_path: Path, timestamps: list[float]
) -> Iterator[tuple[float, object | None]]:
    """Yield ``(timestamp_s, BGR frame | None)`` for each requested timestamp
    using ONE ``cv2.VideoCapture``.

    MANDATORY refactor (Issue 420): the previous per-sample
    ``open → seek → read → release`` pattern spawned 300–450 captures per
    60–90 s clip at 5 fps — each open re-parses the container and each seek
    walks the index, making detection the render path's dominant latency.
    Here the capture opens once, seeks ONCE to the first sample's frame
    index, then advances with sequential ``grab()`` (decode-skip) and reads
    only the sampled frames.

    NaN/zero FPS guards preserved from the old ``_read_frame_cv2``: NaN is
    truthy, so a bare ``fps or 25.0`` would keep NaN and ``int(ts*NaN)``
    raises — checked explicitly with ``math.isfinite``.

    Never raises; any failure yields ``None`` frames (fallback upstream).
    """
    if not timestamps:
        return
    try:
        import cv2  # noqa: PLC0415
    except ImportError:
        for ts in timestamps:
            yield (ts, None)
        return

    cap = None
    try:
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            logger.warning("cv2 could not open %s", video_path)
            for ts in timestamps:
                yield (ts, None)
            return
        raw_fps = cap.get(cv2.CAP_PROP_FPS)
        fps = raw_fps if math.isfinite(raw_fps) and raw_fps > 0 else 25.0

        current: int | None = None
        eof = False
        for ts in timestamps:
            target = max(0, int(ts * fps))
            if eof:
                yield (ts, None)
                continue
            try:
                if current is None:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, float(target))
                    current = target
                while current < target:
                    if not cap.grab():
                        eof = True
                        break
                    current += 1
                if eof:
                    yield (ts, None)
                    continue
                ok, frame = cap.read()
                current += 1
                if not ok:
                    eof = True
                    yield (ts, None)
                    continue
                yield (ts, frame)
            except Exception as exc:
                logger.debug("cv2 frame read failed at %.3fs from %s: %s", ts, video_path, exc)
                yield (ts, None)
    finally:
        if cap is not None:
            cap.release()


def _sample_timestamps(start_s: float, end_s: float, sample_fps: float) -> list[float]:
    """The clip's detection sample times — shared by both detection passes."""
    duration = end_s - start_s
    interval_s = 1.0 / sample_fps
    sample_count = max(1, int(duration * sample_fps))
    return [ts for i in range(sample_count) if (ts := start_s + i * interval_s) < end_s]


def _downscale_for_hist(frame: object) -> object | None:
    """Tiny copy of a frame for the shots.py histogram fallback (≈2 KB each)."""
    try:
        import cv2  # noqa: PLC0415
        import numpy as np

        return cv2.resize(
            np.asarray(frame), (_HIST_SAMPLE_W, _HIST_SAMPLE_H), interpolation=cv2.INTER_AREA
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Core tracking logic
# ---------------------------------------------------------------------------


def build_crop_center_track(
    source_path: Path,
    start_s: float,
    end_s: float,
    frame_width: int,
    *,
    sample_fps: float = _DEFAULT_SAMPLE_FPS,
) -> list[CropCenterPoint]:
    """Build a per-frame crop-center timeline for the clip [start_s, end_s].

    Samples frames at ``sample_fps`` through ONE ``cv2.VideoCapture``
    (:func:`_iter_sampled_frames`), runs MediaPipe BlazeFace on each frame,
    and returns a chronologically ordered list of :class:`CropCenterPoint`
    objects describing where the crop should be centred at each timestamp.

    On total detection failure (mediapipe unavailable or every frame returns
    no detection) the function returns frame-center fallback points — byte-
    compatible with the legacy single-keyframe Haar path.
    """
    duration = end_s - start_s
    if duration <= 0:
        raise ValueError(f"build_crop_center_track: invalid range [{start_s}, {end_s}]")

    center_fallback = frame_width // 2
    timestamps = _sample_timestamps(start_s, end_s, sample_fps)

    # One detector for the whole track (Issue 352 Batch H) — building it per
    # frame parsed the model asset 300–450 times per clip at 5 fps.
    detector = _create_face_detector()
    raw_points: list[CropCenterPoint] = []
    try:
        for ts, frame in _iter_sampled_frames(source_path, timestamps):
            if frame is None:
                raw_points.append(CropCenterPoint(ts, center_fallback, is_fallback=True))
                continue
            try:
                centers = _detect_faces_mediapipe(frame, frame_width, detector)
            except Exception as exc:
                logger.debug("Face detection raised at t=%.3f: %s — using center fallback", ts, exc)
                centers = []
            if centers:
                # Use the largest face (index 0) as the active speaker proxy.
                raw_points.append(CropCenterPoint(ts, centers[0]))
            else:
                raw_points.append(CropCenterPoint(ts, center_fallback, is_fallback=True))
    finally:
        _close_face_detector(detector)

    if not raw_points:
        # Should not happen (sample_count >= 1) but guard defensively.
        mid = start_s + duration / 2.0
        return [CropCenterPoint(mid, center_fallback, is_fallback=True)]

    return raw_points


def _split_at_boundaries(
    raw_track: list[CropCenterPoint],
    cut_times: list[float],
) -> list[tuple[float, list[CropCenterPoint]]]:
    """Split a raw track into ``(span_start_time, points)`` spans at cut times.

    ``span_start_time`` is the raw first point's time for the opening span and
    the BOUNDARY time itself for every later span, so each span's hold keyframe
    lands exactly on the cut (cut entries and the frontend snap key off it).
    """
    if not raw_track:
        return []
    boundaries = sorted(t for t in cut_times)
    spans: list[tuple[float, list[CropCenterPoint]]] = []
    current: list[CropCenterPoint] = []
    current_start = raw_track[0].timestamp_s
    bi = 0
    for point in raw_track:
        while bi < len(boundaries) and point.timestamp_s >= boundaries[bi]:
            if current:
                spans.append((current_start, current))
                current = []
            current_start = boundaries[bi]
            bi += 1
        current.append(point)
    if current:
        spans.append((current_start, current))
    return spans


def plan_pan_holds(
    raw: list[CropCenterPoint],
    shot_cut_times: list[float],
    *,
    sample_fps: float,
    deadband_px: float,
    retarget_s: float,
    glide_px_per_s: float,
    glide_sample_fps: float,
) -> list[CropCenterPoint]:
    """Virtual-tripod planner for the pan rung (Issue 436).

    Replaces continuous face-following (the measured live failure: 25–60 px
    see-saw steps 5×/s) with hold-and-retarget: each shot-bounded span opens
    on the MEDIAN of its first ``retarget_s`` of detections and HOLDS it.
    A retarget fires only when EVERY real detection in the last ``retarget_s``
    sits beyond ``deadband_px`` from the hold (a spike shorter than the window
    can never flip it — sustained means continuously sustained) — then the
    crop glides ONCE (an explicit linear keyframe ramp at ``glide_sample_fps``;
    lerp-exact, so the frontend preview and the sendcmd render agree) to the
    window's median and holds again. Fallback (no-detection) samples never
    trigger anything — the tripod just holds. Glides never cross a shot cut
    (spans are split at the boundaries).
    """
    out: list[CropCenterPoint] = []
    window_n = max(1, math.ceil(retarget_s * sample_fps))

    for span_start, points in _split_at_boundaries(raw, shot_cut_times):
        first_window = [p for p in points if p.timestamp_s < points[0].timestamp_s + retarget_s]
        real_first = [p for p in first_window if not p.is_fallback]
        hold = float(statistics.median([p.center_x for p in (real_first or first_window)]))
        out.append(CropCenterPoint(span_start, int(round(hold)), is_fallback=not real_first))

        span_end = points[-1].timestamp_s
        window: deque[CropCenterPoint] = deque(maxlen=window_n)
        glide_end_t = -math.inf
        for p in points:
            if p.timestamp_s < glide_end_t or p.is_fallback:
                continue  # mid-glide samples are consumed; fallbacks never vote
            window.append(p)
            if len(window) < window_n:
                continue
            if any(abs(w.center_x - hold) <= deadband_px for w in window):
                continue  # not continuously sustained — the tripod holds
            target = float(statistics.median([w.center_x for w in window]))
            t0 = p.timestamp_s
            d = min(max(abs(target - hold) / glide_px_per_s, _GLIDE_MIN_S), span_end - t0)
            if d <= 0:
                continue  # no room left in the span for a visible move
            steps = max(1, math.ceil(d * glide_sample_fps))
            for i in range(steps + 1):
                f = i / steps
                out.append(CropCenterPoint(t0 + f * d, int(round(hold + f * (target - hold)))))
            hold = target
            glide_end_t = t0 + d
            window.clear()
    return out


def clamp_crop_x(center_x: int, crop_w: int, frame_w: int) -> int:
    """Clamp a crop x-offset so the crop window stays fully within the frame.

    Args:
        center_x: Desired crop-center x in source pixels.
        crop_w: Width of the crop window (derived from target aspect ratio).
        frame_w: Width of the full source frame.

    Returns:
        Clamped left-edge x-offset (``crop_x`` in ffmpeg crop filter).
    """
    x_offset = center_x - crop_w // 2
    return max(0, min(x_offset, frame_w - crop_w))


def build_sendcmd_script(
    track: list[CropCenterPoint],
    crop_w: int,
    frame_w: int,
    start_s: float,
) -> str:
    """Produce an ffmpeg ``sendcmd`` script string for a time-varying crop.

    Each line in the script sets the crop filter's ``x`` parameter at the
    corresponding clip-relative timestamp. ffmpeg's ``sendcmd`` filter
    reads this file and injects the commands into the filtergraph at the
    specified times.

    Format per ffmpeg sendcmd docs:
        ``<timestamp_s> [enter] <filter> <param> <value>;``

    The target is the INSTANCE-LABELED crop ``crop@spk`` (Issue 433): a bare
    ``crop`` target addresses EVERY crop instance in the filtergraph
    (empirically verified 2026-08-05) — with the camera-region pre-crop and/or
    the punch-in zoom present, unlabeled commands would corrupt those filters'
    geometry. render.py labels the 9:16 speaker crop ``crop@spk=`` whenever a
    sendcmd script is in the chain.

    The ``enter`` directive fires once when the frame's PTS reaches the
    timestamp — suitable for our once-per-sample update rate.

    Args:
        track: Ordered crop-center timeline (the planned hold/glide points).
        crop_w: Crop window width in source pixels.
        frame_w: Source frame width in pixels.
        start_s: Clip start time (source-relative seconds); subtracted to
            convert timestamps to clip-relative seconds for ffmpeg.

    Returns:
        Multi-line string suitable for writing to a ``.sendcmd`` temp file.
    """
    lines: list[str] = []
    for point in track:
        clip_t = round(point.timestamp_s - start_s, _SENDCMD_TIME_DECIMALS)
        clip_t = max(0.0, clip_t)
        x = clamp_crop_x(point.center_x, crop_w, frame_w)
        lines.append(f"{clip_t:.{_SENDCMD_TIME_DECIMALS}f} [enter] crop@spk x {x};")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Cut/pan planner (Issue 420)
# ---------------------------------------------------------------------------


def plan_crop_directives(
    turns: list[_sm.Turn],
    mapping: _sm.SpeakerMapping,
    tracks: list[_sm.FaceTrack],
    shot_changes: list[float],
    start_s: float,
    end_s: float,
    frame_w: int,
    *,
    min_turn_s: float,
    min_distance_frac: float,
    min_shot_s: float,
    j_cut_lead_s: float = _J_CUT_LEAD_S,
    shot_snap_s: float = _SHOT_SNAP_S,
    punch_in_peak_s: float | None = None,
    punch_in_suppress_s: float = _PUNCH_IN_SUPPRESS_S,
) -> tuple[list[CropCut], list[CropSegment]]:
    """Plan the hard cuts and per-segment framing owners for ``speaker_cut``.

    THE CUT POLICY (Issue 420, exact):
      - a SPEAKER cut fires only when the mapped speaker CHANGES track AND
        the incoming turn lasts ≥ ``min_turn_s`` AND the framing move is
        ≥ ``min_distance_frac·frame_w`` AND ≥ ``min_shot_s`` has passed since
        the last cut;
      - the cut lands ``j_cut_lead_s`` (150 ms) BEFORE the turn's first word
        (J-cut) and SNAPS onto a source shot boundary within ±``shot_snap_s``;
      - a source shot change is ALWAYS a cut (never pan across a source cut —
        shots.py contract), regardless of spacing;
      - speaker cuts inside ±``punch_in_suppress_s`` of the punch-in peak are
        SUPPRESSED (the zoom pulse and a framing jump never stack);
      - an off-screen speaker (no mapped track in the current shot) HOLDS the
        current framing — never cut to nothing.

    Returns ``(cuts, segments)`` — segments tile ``[start_s, end_s]`` exactly,
    each owning the track that drives framing between consecutive cuts.
    All times are source-relative.
    """
    window_shots = [t for t in shot_changes if start_s < t < end_s]
    tracks_by_id = {t.track_id: t for t in tracks}

    def _cx_of(track_id: int | None) -> float:
        track = tracks_by_id.get(track_id) if track_id is not None else None
        return track.mean_cx if track is not None else frame_w / 2.0

    # Initial framing owner: the mapped track of whichever turn is active (or
    # first) at clip start; else the largest track in the opening shot.
    shot0 = _sm.shot_index_for(start_s, shot_changes)
    current_speaker: int | None = None
    current_track: int | None = None
    for first_turn in turns:
        if first_turn.end > start_s:
            current_speaker = first_turn.speaker
            current_track = mapping.track_for(first_turn.speaker, shot0)
            break
    if current_track is None:
        shot0_tracks = [t for t in tracks if t.shot_idx == shot0]
        if shot0_tracks:
            current_track = max(shot0_tracks, key=lambda t: t.mean_area).track_id

    cuts: list[CropCut] = []
    segment_tracks: list[int | None] = [current_track]
    last_cut_t = start_s

    # Merge the two event streams chronologically.
    events: list[tuple[float, str, object]] = [(t, "shot", t) for t in window_shots]
    events += [(turn.start, "turn", turn) for turn in turns if start_s < turn.start < end_s]
    events.sort(key=lambda e: (e[0], e[1]))  # shot before turn on exact ties

    def _emit(t: float, speaker: int | None, to_track: int | None) -> None:
        nonlocal current_track, last_cut_t
        cuts.append(CropCut(t=t, speaker=speaker, from_track=current_track, to_track=to_track))
        segment_tracks.append(to_track)
        current_track = to_track
        last_cut_t = t

    consumed_shots: set[float] = set()

    for _, kind, payload in events:
        if kind == "shot":
            boundary = float(payload)  # type: ignore[arg-type]
            if boundary in consumed_shots:
                continue
            # Mandatory: tracks are per-shot, so re-resolve the active
            # speaker's track in the NEW shot (None → hold/center framing).
            new_shot = _sm.shot_index_for(boundary, shot_changes)
            to_track = (
                mapping.track_for(current_speaker, new_shot)
                if current_speaker is not None
                else None
            )
            if to_track is None:
                new_shot_tracks = [t for t in tracks if t.shot_idx == new_shot]
                if new_shot_tracks:
                    to_track = max(new_shot_tracks, key=lambda t: t.mean_area).track_id
            consumed_shots.add(boundary)
            _emit(boundary, None, to_track)
            continue

        turn: _sm.Turn = payload  # type: ignore[assignment]
        prev_speaker = current_speaker
        current_speaker = turn.speaker
        t_cut = turn.start - j_cut_lead_s
        # Snap target first: a snapped cut lands ON the boundary, so the
        # incoming speaker's track must be resolved in the POST-boundary shot.
        snapped = next((b for b in window_shots if abs(b - t_cut) <= shot_snap_s), None)
        effective_t = snapped if snapped is not None else max(t_cut, start_s)
        shot_at = _sm.shot_index_for(effective_t, shot_changes)
        target = mapping.track_for(turn.speaker, shot_at)
        if target is None:
            continue  # off-screen speaker holds framing — never cut to nothing

        # Snapped-boundary coalescing (one visual event, not two): when a
        # mandatory shot cut already landed within snap range, fold the
        # speaker change into it — retarget its destination and annotate the
        # speaker — instead of stacking a second cut 150 ms away.
        if snapped is not None and snapped in consumed_shots and cuts and cuts[-1].t == snapped:
            if turn.speaker != prev_speaker:
                cuts[-1] = CropCut(
                    t=snapped,
                    speaker=turn.speaker,
                    from_track=cuts[-1].from_track,
                    to_track=target,
                )
                segment_tracks[-1] = target
                current_track = target
            continue

        if target == current_track:
            continue  # already framed on this speaker
        if turn.duration < min_turn_s:
            continue
        if abs(_cx_of(target) - _cx_of(current_track)) < min_distance_frac * frame_w:
            continue
        if punch_in_peak_s is not None and abs(t_cut - punch_in_peak_s) <= punch_in_suppress_s:
            continue  # never stack a framing jump on the zoom pulse

        if snapped is not None:
            # Snap onto the (not yet reached) source shot boundary.
            consumed_shots.add(snapped)
            _emit(snapped, turn.speaker, target)
            continue

        if t_cut <= start_s:
            # The turn effectively starts with the clip — frame it, no cut.
            current_track = target
            segment_tracks[-1] = target if not cuts else segment_tracks[-1]
            if not cuts:
                segment_tracks[0] = target
            continue
        if t_cut - last_cut_t < min_shot_s:
            continue
        _emit(t_cut, turn.speaker, target)

    boundaries = [start_s] + [c.t for c in cuts] + [end_s]
    segments = [
        CropSegment(start=boundaries[i], end=boundaries[i + 1], track_id=segment_tracks[i])
        for i in range(len(boundaries) - 1)
    ]
    return cuts, segments


def _hold_points_from_segments(
    segments: list[CropSegment],
    tracks: list[_sm.FaceTrack],
    frame_w: int,
) -> list[CropCenterPoint]:
    """Virtual tripod for ``speaker_cut`` (Issue 436): ONE constant crop center
    per segment — the owning track's median position over the segment window.

    The per-sample predecessor followed the face continuously WITHIN a turn,
    so detector wobble became ~4 crop micro-moves/second (measured live).
    A speaker turn is framed like a locked-off camera: position changes happen
    ONLY at cuts, which is where the next segment's point lands. Off-screen
    segments (``track_id`` None) HOLD the previous position — deliberate
    framing, not a fallback; only a total absence of signal yields the
    frame-center fallback point.
    """
    tracks_by_id = {t.track_id: t for t in tracks}
    out: list[CropCenterPoint] = []
    prev_cx: float | None = None
    for seg in segments:
        track = tracks_by_id.get(seg.track_id) if seg.track_id is not None else None
        if track is not None:
            cx = track.median_cx(seg.start, seg.end)
            out.append(CropCenterPoint(seg.start, int(round(cx))))
            prev_cx = cx
        elif prev_cx is not None:
            out.append(CropCenterPoint(seg.start, int(round(prev_cx))))
        else:
            out.append(CropCenterPoint(seg.start, frame_w // 2, is_fallback=True))
            prev_cx = float(frame_w // 2)
    return out


def _seat_hold_plan(
    obs_frames: list[tuple[float, list[_sm.FaceObs]]],
    tracks: list[_sm.FaceTrack],
    shot_changes: list[float],
    start_s: float,
    end_s: float,
    crop_w: int,
    *,
    mapping: _sm.SpeakerMapping | None = None,
    turns: list[_sm.Turn] | None = None,
) -> tuple[list[CropCut], list[CropCenterPoint]] | None:
    """Cut between discrete face "seats" instead of panning between them (Issue 440).

    ``face_pan`` used to follow the single largest detected face. On a wide
    two-shot "largest" flips as speakers lean and turn, so the planner glided the
    crop across the entire pan space and back — measured live at 343 keyframes in
    7 monotonic runs of ±900 px over 42.6 s, with intermediate frames resting on
    the empty background *between* the two speakers.

    Seats are the stable face tracks already built for the speaker-cut rung, so
    no new clustering is needed. Two seats closer together than the crop width
    are one framing, not two — those fall through to the pan planner.

    On a real multi-seat layout the framing HOLDS THE DOMINANT SEAT for each
    shot and changes only at a source shot boundary. It deliberately does not
    try to follow the conversation: this rung is reached precisely when speaker
    mapping was too weak to trust (``REFRAME_MIN_MAPPING_CONFIDENCE``), and the
    only signal left — which face is largest — flips as speakers lean and turn.
    Cutting on that noise trades a sweeping camera for a twitching one. When the
    diarization IS trustworthy the ``speaker_cut`` rung already cuts on real
    turns; when it is not, a still frame on the wrong person is far cheaper than
    a cut to the wrong person, and stillness is what the creator asked for.

    Returns ``(cuts, hold_points)``, or ``None`` when this is not a multi-seat
    layout and the caller should fall back to ``plan_pan_holds``.
    """
    from collections import Counter

    if len(tracks) < 2:
        return None

    # Collapse seats that fit inside one crop — they are a single framing.
    seats: list[float] = []
    for cx in sorted(t.median_cx() for t in tracks):
        if not seats or cx - seats[-1] > crop_w:
            seats.append(cx)
    if len(seats) < 2:
        return None

    def _nearest_seat(cx: float) -> int:
        return min(range(len(seats)), key=lambda i: abs(seats[i] - cx))

    # Seats must be occupied SIMULTANEOUSLY to be a two-shot. Two well-separated
    # tracks that never co-occur are one subject who relocated — that is a real
    # camera move and belongs to the pan planner, which glides to the new
    # position. Holding a "dominant" seat there would frame empty space for the
    # part of the clip before the move.
    if not any(
        len({_nearest_seat(o.cx) for o in obs}) >= 2 for _ts, obs in obs_frames if len(obs) >= 2
    ):
        return None

    # One hold per shot. A source shot change is the only thing that may move
    # the framing.
    boundaries = sorted({start_s, *[t for t in shot_changes if start_s < t < end_s], end_s})
    points: list[CropCenterPoint] = []
    cuts: list[CropCut] = []
    for span_start, span_end in zip(boundaries, boundaries[1:], strict=False):
        # Issue 450 — prefer the seat of whoever is SPEAKING in this span. The
        # size vote below is uncorrelated with speech: on rank 1 of video
        # `7e988321` it held the silent participant for the whole 34.55 s clip
        # while the speaker mapping had already assigned the speaker to the
        # other seat correctly. Whoever sits closer to their camera used to win.
        seat_idx: int | None = None
        if mapping is not None and turns:
            speaking = _sm.speaking_track_for_span(
                mapping, turns, tracks, shot_changes, span_start, span_end
            )
            if speaking is not None:
                seat_idx = _nearest_seat(speaking.median_cx())

        if seat_idx is None:
            # Fallback: the seat that owns the most samples as the LARGEST face.
            # Reached for a no-transcript video, an unmapped span, or a pruned
            # track — i.e. exactly the cases with no speech signal to use.
            counts = Counter(
                _nearest_seat(obs[0].cx)
                for ts, obs in obs_frames
                if obs and span_start <= ts < span_end
            )
            if not counts:
                continue
            seat_idx = counts.most_common(1)[0][0]

        x = int(round(seats[seat_idx]))
        if not points:
            points.append(CropCenterPoint(start_s, x))
        elif x != points[-1].center_x:
            points.append(CropCenterPoint(span_start, x))
            cuts.append(CropCut(t=span_start, speaker=None, from_track=None, to_track=None))
    if not points:
        return None
    return cuts, points


def _raw_track_largest_face(
    obs_frames: list[tuple[float, list[_sm.FaceObs]]],
    frame_w: int,
) -> list[CropCenterPoint]:
    """Pan-rung raw track: largest detected face per sample, center fallback."""
    center = frame_w // 2
    out: list[CropCenterPoint] = []
    for ts, obs in obs_frames:
        if obs:
            out.append(CropCenterPoint(ts, int(round(obs[0].cx))))
        else:
            out.append(CropCenterPoint(ts, center, is_fallback=True))
    return out


# ---------------------------------------------------------------------------
# Track JSON (unified wire contract — Issue 421 / Cross-Track Reconciliation)
# ---------------------------------------------------------------------------

TRACK_JSON_VERSION = 1


def _build_track_json(
    smoothed: list[CropCenterPoint],
    cuts: list[CropCut],
    shot_changes: list[float],
    mode: str,
    *,
    start_s: float,
    end_s: float,
    frame_w: int,
    frame_h: int,
    crop_w: int,
    sample_fps: float,
    speaker_count: int,
    mapping_confidence: float,
    region: tuple[int, int, int, int] | None = None,
) -> dict:
    """Serialize the computed track to the UNIFIED crop-track wire contract.

    ``keyframes[].x`` is the clamped LEFT edge — the EXACT value each sendcmd
    line carries (one geometry definition shared by render and preview;
    conversion from center via :func:`clamp_crop_x`). All ``t`` are
    clip-relative. Interpolation contract for consumers: lerp between
    keyframes; SNAP at cuts.

    Region composition (Issue 433): when a camera region is active,
    ``frame_w``/``frame_h`` ARE the region dims — ``source`` is always the
    pan-space rect the crop moves within, so consumer math is unchanged. The
    additive ``region`` key (absolute source rect, provenance only) is emitted
    ONLY when a region was detected — omitted, never null, keeping the pinned
    wire-contract fixtures exact.
    """
    keyframes = [
        {
            "t": max(0.0, round(p.timestamp_s - start_s, _SENDCMD_TIME_DECIMALS)),
            "x": clamp_crop_x(p.center_x, crop_w, frame_w),
        }
        for p in smoothed
    ]

    cut_entries: list[dict] = []
    for cut in cuts:
        cut_clip_t = cut.t - start_s
        idx = next((i for i, kf in enumerate(keyframes) if kf["t"] >= round(cut_clip_t, 3)), None)
        if idx is None or idx == 0:
            continue
        entry: dict = {
            "t": keyframes[idx]["t"],
            "from_x": keyframes[idx - 1]["x"],
            "to_x": keyframes[idx]["x"],
        }
        if cut.speaker is not None:
            entry["speaker"] = cut.speaker
        cut_entries.append(entry)

    fallback = mode == "static" or all(p.is_fallback for p in smoothed)
    track = {
        "version": TRACK_JSON_VERSION,
        "mode": mode,
        "source": {"width": frame_w, "height": frame_h},
        "crop": {"width": crop_w, "height": frame_h},
        "origin_s": round(start_s, 3),
        "duration_s": round(end_s - start_s, 3),
        "keyframes": keyframes,
        "cuts": cut_entries,
        "shots": [{"t": round(t - start_s, 3)} for t in shot_changes if start_s < t < end_s],
        "speakers": {
            "count": speaker_count,
            "mapping_confidence": round(mapping_confidence, 3),
        },
        "meta": {"sample_fps": sample_fps, "fallback": fallback},
    }
    if region is not None:
        rx, ry, rw, rh = region
        track["region"] = {"x": rx, "y": ry, "width": rw, "height": rh}
    return track


# ---------------------------------------------------------------------------
# Entry points (called from render.py when the flag is enabled)
# ---------------------------------------------------------------------------


def compute_dynamic_crop(
    source_path: Path,
    start_s: float,
    end_s: float,
    frame_width: int,
    frame_height: int,
    crop_w: int,
    *,
    sample_fps: float = _DEFAULT_SAMPLE_FPS,
    transcript_segments: list[dict] | None = None,
    punch_in_peak_s: float | None = None,
    region: tuple[int, int, int, int] | None = None,
) -> ReframeResult:
    """Compute the full dynamic crop: detection → shots → mapping → plan →
    virtual-tripod hold track → sendcmd + wire-contract track JSON.

    The ladder (``speaker_cut → face_pan → static``) is applied via
    :func:`clip_engine.speaker_map.choose_reframe_mode`; each stage degrades
    toward today's behavior and the whole function NEVER raises — total
    failure returns a static single-center result (mode ``static``,
    empty sendcmd → render.py's static-crop path).

    ``transcript_segments`` is the full ``Transcript.segments_jsonb["segments"]``
    list (speaker fields optional — Issue 418). ``punch_in_peak_s`` is the
    SOURCE-relative punch-in peak time when the zoom pulse will be applied,
    used only to suppress speaker cuts inside the pulse.

    ``region`` (Issue 433): the detected camera rect ``(x, y, w, h)`` in
    ABSOLUTE source pixels. Contract: when a region is passed, the caller
    passes the REGION dims as ``frame_width``/``frame_height`` and a
    region-derived ``crop_w``; this function slices every sampled frame to the
    region before detection, so all downstream coordinates (tracks, clamps,
    sendcmd x, track keyframes) are region-relative BY CONSTRUCTION — the
    region pre-crop filter in the vf chain translates the stream, and no
    offset arithmetic exists anywhere. ``region`` itself is recorded in the
    track JSON for provenance only.
    """
    from config import settings  # noqa: PLC0415 — avoid config import at module init
    from verbose import now_ms, vlog  # noqa: PLC0415

    try:
        duration = end_s - start_s
        if duration <= 0:
            raise ValueError(f"compute_dynamic_crop: invalid range [{start_s}, {end_s}]")

        timestamps = _sample_timestamps(start_s, end_s, sample_fps)

        # ── Detection pass: ONE capture, full observations ────────────────
        _t_detect = now_ms()
        detector = _create_face_detector()
        obs_frames: list[tuple[float, list[_sm.FaceObs]]] = []
        hist_samples: list[tuple[float, object]] = []
        try:
            for ts, frame in _iter_sampled_frames(source_path, timestamps):
                if frame is None:
                    obs_frames.append((ts, []))
                    continue
                if region is not None:
                    # Slice to the camera region BEFORE detection (Issue 433):
                    # every coordinate downstream becomes region-relative.
                    # (frame is an ndarray at runtime; the iterator's `object`
                    # annotation keeps numpy out of the module's type surface.)
                    _rx, _ry, _rw, _rh = region
                    frame = frame[_ry : _ry + _rh, _rx : _rx + _rw]  # type: ignore[index]
                obs_frames.append((ts, _detect_face_obs(frame, detector, ts)))
                small = _downscale_for_hist(frame)
                if small is not None:
                    hist_samples.append((ts, small))
        finally:
            _close_face_detector(detector)
        reframe_detect_ms = int(now_ms() - _t_detect)

        # ── Shots / turns / tracks / mapping ──────────────────────────────
        _t_shots = now_ms()
        try:
            shot_changes = _shots.detect_shot_changes(
                source_path, start_s, end_s, fallback_samples=hist_samples
            )
        except Exception as exc:
            logger.warning("shot detection failed: %s — treating clip as one shot", exc)
            shot_changes = []
        reframe_shots_ms = int(now_ms() - _t_shots)
        _t_plan = now_ms()

        segments_in = transcript_segments or []
        turns = _sm.extract_speaker_turns(segments_in, start_s, end_s)
        coverage = _sm.diarization_coverage(segments_in, start_s, end_s)
        tracks = _sm.build_face_tracks(obs_frames, frame_width, shot_changes)
        mapping = _sm.map_speakers_to_tracks(tracks, turns)
        speaker_count = len({t.speaker for t in turns})

        mode = _sm.choose_reframe_mode(
            n_tracks=len(tracks),
            n_speakers=speaker_count,
            coverage=coverage,
            mapping_confidence=mapping.confidence,
            cuts_enabled=settings.REFRAME_CUT_ENABLED,
            frame_w=frame_width,
            crop_w=crop_w,
            min_mapping_confidence=settings.REFRAME_MIN_MAPPING_CONFIDENCE,
        )

        # ── Plan the virtual-tripod hold track per mode (Issue 436) ───────
        cuts: list[CropCut] = []
        if mode == "speaker_cut":
            cuts, plan_segments = plan_crop_directives(
                turns,
                mapping,
                tracks,
                shot_changes,
                start_s,
                end_s,
                frame_width,
                min_turn_s=settings.REFRAME_CUT_MIN_TURN_S,
                min_distance_frac=settings.REFRAME_CUT_MIN_DISTANCE_FRAC,
                min_shot_s=settings.REFRAME_MIN_SHOT_S,
                punch_in_peak_s=punch_in_peak_s,
            )
            planned = _hold_points_from_segments(plan_segments, tracks, frame_width)
        elif mode == "face_pan":
            # Multi-seat layouts CUT between seats (Issue 440) — panning across a
            # wide two-shot whips the crop over the empty background between the
            # speakers. Falls through to the pan planner when every face fits in
            # one framing, i.e. a genuine single subject drifting.
            seat_plan = _seat_hold_plan(
                obs_frames,
                tracks,
                shot_changes,
                start_s,
                end_s,
                crop_w,
                # Issue 450: the mapping already knows which seat is talking.
                # Passing it here is the whole fix — it was computed above and
                # then ignored by the planner.
                mapping=mapping,
                turns=turns,
            )
            if seat_plan is not None:
                cuts, planned = seat_plan
            else:
                # Pan rung — largest face, held on a virtual tripod: deadband +
                # sustained-deviation retarget glides; mandatory cuts at source
                # shot changes (never pan across a source cut).
                raw = _raw_track_largest_face(obs_frames, frame_width)
                cuts = [
                    CropCut(t=t, speaker=None, from_track=None, to_track=None)
                    for t in shot_changes
                    if start_s < t < end_s
                ]
                planned = plan_pan_holds(
                    raw,
                    [c.t for c in cuts],
                    sample_fps=sample_fps,
                    # Issue 440: the deadband scales to the PAN SPACE, not the crop
                    # width. At crop_w it was ~46px against ~940px of travel — a
                    # 20x mismatch that let any attention change commit to a
                    # full-width glide.
                    deadband_px=(settings.REFRAME_PAN_DEADBAND_FRAC * max(1, frame_width - crop_w)),
                    retarget_s=settings.REFRAME_PAN_RETARGET_S,
                    glide_px_per_s=settings.REFRAME_PAN_GLIDE_PX_PER_S,
                    glide_sample_fps=settings.REFRAME_GLIDE_SAMPLE_FPS,
                )
        else:  # static
            mid = start_s + duration / 2.0
            planned = [CropCenterPoint(mid, frame_width // 2, is_fallback=True)]

        # A single point (zero-cut clip, or a hold with no retargets) emits an
        # EMPTY script → render.py's static-crop branch: a true locked-off
        # crop with no sendcmd file at all.
        script = (
            build_sendcmd_script(planned, crop_w, frame_width, start_s) if len(planned) > 1 else ""
        )
        track_json = _build_track_json(
            planned,
            cuts,
            shot_changes,
            mode,
            start_s=start_s,
            end_s=end_s,
            frame_w=frame_width,
            frame_h=frame_height,
            crop_w=crop_w,
            sample_fps=sample_fps,
            speaker_count=speaker_count,
            mapping_confidence=mapping.confidence,
            region=region,
        )
        # Per-stage timing budget (Issue 422): detection is the dominant cost;
        # the staging checklist in docs/DEPLOYMENT.md records these at
        # 30/60/90 s clip lengths against the 240 s render timeout.
        vlog(
            "reframe_stages",
            mode=mode,
            reframe_detect_ms=reframe_detect_ms,
            reframe_shots_ms=reframe_shots_ms,
            reframe_plan_ms=int(now_ms() - _t_plan),
            keyframes=len(planned),
            cuts=len(cuts),
            shots=len(shot_changes),
            clip_duration_s=round(duration, 3),
        )
        logger.info(
            "Dynamic crop: mode=%s keyframes=%d cuts=%d shots=%d speakers=%d "
            "confidence=%.2f coverage=%.2f fallback_pct=%.0f%% for %s [%.2f–%.2f]",
            mode,
            len(planned),
            len(cuts),
            len(shot_changes),
            speaker_count,
            mapping.confidence,
            coverage,
            100.0 * sum(1 for p in planned if p.is_fallback) / max(1, len(planned)),
            source_path.name,
            start_s,
            end_s,
        )
        return ReframeResult(
            track_points=planned, sendcmd_text=script, track_json=track_json, mode=mode
        )
    except Exception as exc:
        logger.warning(
            "Dynamic crop failed for %s [%.2f–%.2f]: %s — falling back to static center",
            source_path.name,
            start_s,
            end_s,
            exc,
        )
        mid = start_s + max(0.0, end_s - start_s) / 2.0
        fallback_point = CropCenterPoint(mid, frame_width // 2, is_fallback=True)
        return ReframeResult(
            track_points=[fallback_point],
            sendcmd_text="",
            track_json=_build_track_json(
                [fallback_point],
                [],
                [],
                "static",
                start_s=start_s,
                end_s=end_s,
                frame_w=frame_width,
                frame_h=frame_height,
                crop_w=crop_w,
                sample_fps=sample_fps,
                speaker_count=0,
                mapping_confidence=0.0,
                region=region,
            ),
            mode="static",
        )


def compute_reframe_crop(
    source_path: Path,
    start_s: float,
    end_s: float,
    frame_width: int,
    frame_height: int,
    crop_w: int,
    *,
    sample_fps: float = _DEFAULT_SAMPLE_FPS,
) -> tuple[list[CropCenterPoint], str]:
    """Legacy entry point — DELEGATES to :func:`compute_dynamic_crop`.

    Kept for callers that predate the speaker-aware planner (no transcript in
    hand): without transcript segments the ladder lands on the pan or static
    rung, which is the original Issue-189 behavior improved by per-shot
    smoothing resets. Returns ``(smoothed_track, sendcmd_script_text)``.
    """
    result = compute_dynamic_crop(
        source_path,
        start_s,
        end_s,
        frame_width,
        frame_height,
        crop_w,
        sample_fps=sample_fps,
        transcript_segments=None,
    )
    return result.track_points, result.sendcmd_text
