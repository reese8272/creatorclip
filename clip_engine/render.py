"""
Render a clip: extract segment with ffmpeg, detect active speaker face,
crop to 9:16 centered on face, upload to storage.

Face detection (default): OpenCV Haar frontal-face cascade on a single keyframe.
Falls back to frame center if no face is found.

Per-frame active-speaker reframe (Issue 189, GATED):
When ``settings.ACTIVE_SPEAKER_REFRAME_ENABLED`` is True, the static single-
keyframe Haar crop is replaced by per-frame MediaPipe BlazeFace tracking via
``clip_engine.reframe.compute_reframe_crop``.  The result is fed to ffmpeg as a
time-varying crop using the ``sendcmd`` filter.  This path is disabled by default
(flag defaults to False) because it requires a render environment (ffmpeg +
MediaPipe + real multi-speaker media) that is NOT available on the dev box.  Flip
the flag ONLY after render-env smoke tests pass.  See ``clip_engine/reframe.py``
and ``docs/DECISIONS.md`` (2026-06-23, Issue 189) for the build-vs-buy rationale.

Animated captions (Issue 133): when ``style_preset["subtitle"]`` names a known
caption style (``bold_pop`` / ``bold_pop_highlight`` / ``gradient_slide`` /
``minimal``), an ASS subtitle
file is generated from the supplied transcript segments and burned in via
libass. See ``clip_engine/captions.py``.

Cleaned render (Issue 134): ``render_cleaned_clip_file`` excises ranges from an
already-rendered clip via a single-pass ``filter_complex`` (``trim`` + ``atrim``
+ ``setpts`` + ``concat``) with a 5ms ``afade`` at every splice for click
prevention. The graph is written to a temp file and passed via
``-filter_complex_script`` to avoid shell-arg-length issues at scale. See
``clip_engine/filler.py`` for the cut-list generator.

Loudness normalization (Issue 181): both render paths normalize to YouTube's
−14 LUFS playback target via a two-pass ffmpeg ``loudnorm`` (measure, then apply
the measured values for linear, pump-free gain). Even loudness across a feed is
Principle 5 (dead-air/credibility — momentum is retention); single-pass was
rejected because it adapts gain in real time and pumps on quiet→loud material.
Near-silent clips are left un-normalized so we never amplify hiss. See
``docs/DECISIONS.md`` (2026-06-22, Issue 181).
"""

import json
import logging
import math
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

from clip_engine import captions

logger = logging.getLogger(__name__)

# Directory libass searches for custom fonts (Dockerfile installs Anton here).
_FONTS_DIR = "/usr/share/fonts/custom"

# Export presets (Issue 182). Single source of truth for output geometry, shared
# with the editorial/recap work. The crop width is derived from the preset ratio
# (`frame_h * out_w/out_h`) so 9:16 stays byte-identical to the pre-182 output
# (`int(frame_h*1080/1920) == int(frame_h*9/16)`). Presets are applied at render
# time via `style_preset["aspect"]`; no stored ClipFormat change.
OUTPUT_PRESETS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),  # default vertical Short
    "1:1": (1080, 1080),  # square
    "16:9": (1920, 1080),  # horizontal
}
_DEFAULT_FORMAT = "9:16"

# Default 9:16 dimensions, kept for callers/tests that reference them directly.
_OUTPUT_W, _OUTPUT_H = OUTPUT_PRESETS[_DEFAULT_FORMAT]


def _run(cmd: list[str], label: str, timeout_s: float = 120.0) -> None:
    from verbose import now_ms, vlog

    vlog("ffmpeg_start", label=label, cmd=cmd, timeout_s=timeout_s)
    _t0 = now_ms()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        vlog("ffmpeg_timeout", label=label, cmd=cmd, timeout_s=timeout_s)
        raise RuntimeError(f"ffmpeg {label} timed out after {timeout_s}s") from exc
    except OSError as exc:
        # FileNotFoundError (binary missing), PermissionError, or generic OS error.
        vlog("ffmpeg_os_error", label=label, cmd=cmd, exc=str(exc))
        raise RuntimeError(f"ffmpeg {label} failed to start [{shlex.join(cmd)}]: {exc}") from exc
    if result.returncode != 0:
        # Full (untruncated) stderr to the verbose sink; the raised error carries the
        # TAIL of stderr (~500 chars) so the meaningful part of long ffmpeg logs is
        # preserved (ffmpeg prints the failure reason at the end, not the start).
        vlog(
            "ffmpeg_failed",
            label=label,
            cmd=cmd,
            returncode=result.returncode,
            stderr=result.stderr,
            duration_ms=int(now_ms() - _t0),
        )
        raise RuntimeError(f"ffmpeg {label} failed [{shlex.join(cmd)}]: {result.stderr[-500:]}")
    vlog("ffmpeg_done", label=label, duration_ms=int(now_ms() - _t0))


# ── Loudness normalization (Issue 181) ────────────────────────────────────────
# YouTube normalizes playback to −14 LUFS; targeting −14 means YouTube leaves the
# clip untouched. TP −1.5 dBTP / LRA 11 are the standard companions for the
# integrated target. Two-pass (measure → apply with measured_* values) gives
# linear, pump-free gain; single-pass loudnorm adapts in real time and pumps on
# quiet→loud clips, so it cannot meet the no-pumping bar. Principle 5.
_LOUDNORM_TARGET = "I=-14:TP=-1.5:LRA=11"
# Integrated loudness at/below this is effectively silence (ffmpeg's loudness
# gate floors near −70 LUFS). Normalizing it only amplifies hiss, so we skip.
_LOUDNORM_SILENCE_FLOOR_LUFS = -50.0

# Opt-in noise reduction (Issue 185, style_preset["denoise"], off by default).
# ffmpeg's FFT denoiser — no model asset to ship (unlike arnndn's .rnnn file).
# Conservative settings (the docs' own example): 10 dB reduction, −40 dB noise
# floor, adaptive noise-floor tracking — cut hiss without speech artifacts.
# Applied BEFORE loudnorm so normalization targets the denoised signal.
_DENOISE_FILTER = "afftdn=nr=10:nf=-40:tn=1"


def _parse_loudnorm_stats(stderr: str) -> dict[str, str] | None:
    """Extract the JSON stats object printed by ``loudnorm=...:print_format=json``.

    loudnorm writes a single brace-delimited JSON block (no nesting) to stderr at
    the end of the pass. Returns the parsed dict, or ``None`` if absent/unparseable.
    """
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except (ValueError, TypeError):
        return None


def _measure_loudnorm_filter(measure_cmd: list[str], label: str, timeout_s: float) -> str | None:
    """Run the loudnorm analysis pass (``measure_cmd``) and build the second-pass
    ``loudnorm`` filter string with the measured values baked in.

    Returns the filter string, or ``None`` when the input is effectively silent
    (skip normalization to avoid amplifying hiss) or measurement fails for any
    reason — in which case the render proceeds un-normalized rather than aborting
    (loudness is a quality nicety, not a correctness requirement).
    """
    try:
        result = subprocess.run(measure_cmd, capture_output=True, text=True, timeout=timeout_s)
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("Loudness measurement failed for %s (%s) — rendering flat", label, exc)
        return None
    stats = _parse_loudnorm_stats(result.stderr)
    if stats is None:
        logger.warning("Loudness stats unparseable for %s — rendering flat", label)
        return None
    try:
        measured_i = float(stats["input_i"])
        measured_tp = float(stats["input_tp"])
        measured_lra = float(stats["input_lra"])
        measured_thresh = float(stats["input_thresh"])
        offset = float(stats["target_offset"])
    except (KeyError, ValueError, TypeError):
        logger.warning("Loudness stats incomplete for %s — rendering flat", label)
        return None
    if measured_i <= _LOUDNORM_SILENCE_FLOOR_LUFS:
        logger.info("Input near-silent (measured_I=%.1f LUFS) — skipping loudnorm", measured_i)
        return None
    return (
        f"loudnorm={_LOUDNORM_TARGET}"
        f":measured_I={measured_i}:measured_TP={measured_tp}"
        f":measured_LRA={measured_lra}:measured_thresh={measured_thresh}"
        f":offset={offset}:linear=true:print_format=summary"
    )


def _extract_keyframe(
    source_path: Path, seek_s: float, out_path: Path, timeout_s: float = 120.0
) -> None:
    """Pull one frame at seek_s from source into out_path (JPEG)."""
    _run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            str(seek_s),
            "-i",
            str(source_path),
            "-vframes",
            "1",
            "-q:v",
            "2",
            str(out_path),
        ],
        "keyframe extraction",
        timeout_s=timeout_s,
    )


_POSTER_WIDTH = 640
_POSTER_SEEK_FRACTION = 0.10
_POSTER_SEEK_MIN_S = 3.0
_POSTER_SEEK_MAX_S = 60.0
_POSTER_JPEG_Q = 4
_POSTER_THUMBNAIL_BATCH = 40


def poster_seek_s(duration_s: float | None) -> float:
    """Where to sample a representative still from a source of ``duration_s``.

    10% in, floored at 3s and capped at 60s. A fixed 3s — the obvious choice —
    lands inside the fade-in, cold open or channel bumper on a large fraction of
    real uploads; the cap stops a three-hour podcast seeking eighteen minutes
    deep for no extra representativeness. Unknown duration falls back to the
    floor.
    """
    if duration_s is None or not math.isfinite(duration_s) or duration_s <= 0:
        return _POSTER_SEEK_MIN_S
    seek = min(max(duration_s * _POSTER_SEEK_FRACTION, _POSTER_SEEK_MIN_S), _POSTER_SEEK_MAX_S)
    # Never seek past the end of a very short source.
    return max(0.0, min(seek, duration_s - 1.0))


def _poster_cmd(
    source_path: Path, out_path: Path, seek_s: float, width: int, *, pick: bool
) -> list[str]:
    """Build the ffmpeg argv for a poster frame.

    FILTER ORDER IS LOAD-BEARING. ``scale`` MUST precede ``thumbnail``: the
    thumbnail filter buffers ``_POSTER_THUMBNAIL_BATCH`` frames to pick the most
    histogram-representative one, which at 4K is ~1 GB of decoded frames inside a
    Celery worker and ~27 MB at 640px. ``fps=2`` first widens that batch from
    ~1.6s of source to ~20s for no extra decode cost, since decoding stops as
    soon as a frame is emitted.

    ``-map 0:V:0`` (capital V) excludes attached cover-art streams — ffmpeg's
    "best video stream" heuristic will otherwise happily pick album art over
    actual footage.
    """
    vf = f"fps=2,scale='min({width},iw)':-2"
    if pick:
        vf += f",thumbnail=n={_POSTER_THUMBNAIL_BATCH}"
    return [
        "ffmpeg",
        "-y",
        "-ss",
        f"{seek_s:.3f}",
        "-i",
        str(source_path),
        "-map",
        "0:V:0",
        "-an",
        "-sn",
        "-dn",
        "-vf",
        vf,
        "-frames:v",
        "1",
        "-q:v",
        str(_POSTER_JPEG_Q),
        "-f",
        "image2",
        str(out_path),
    ]


def extract_poster_frame(
    source_path: Path,
    out_path: Path,
    *,
    duration_s: float | None = None,
    width: int = _POSTER_WIDTH,
    timeout_s: float = 60.0,
) -> bool:
    """Write a representative JPEG still from ``source_path`` to ``out_path``.

    Returns True when a non-empty file was produced, False otherwise. NEVER
    raises: a poster frame is an index entry, and no caller should fail a video
    ingest or a clip render because one could not be made (Issue 387).

    Deliberately a sibling of ``_extract_keyframe`` rather than a parameter on
    it. That function's only caller feeds its output to ``_detect_face_center_x``
    and clamps the result against ``_frame_dimensions``, which is in SOURCE
    pixels — adding a scale filter there would return face coordinates in scaled
    space against a source-width clamp and silently off-centre every legacy-path
    9:16 render, with no test to catch it.
    """
    seek = poster_seek_s(duration_s if duration_s is not None else _source_duration_s(source_path))

    # `-ss` before `-i` is a fast seek and can land past the end on a VFR or
    # broken-index source, exiting 0 with no output — hence the explicit
    # non-empty check and the seek-0 retry rather than trusting the return code.
    for attempt_seek, pick in ((seek, True), (0.0, False)):
        try:
            _run(
                _poster_cmd(source_path, out_path, attempt_seek, width, pick=pick),
                "poster frame",
                timeout_s=timeout_s,
            )
        except (RuntimeError, OSError) as exc:
            logger.warning("poster_frame_attempt_failed seek=%.3f err=%s", attempt_seek, exc)
            continue
        if out_path.exists() and out_path.stat().st_size > 0:
            return True

    logger.warning("poster_frame_unavailable source=%s", source_path.name)
    return False


def _frame_dimensions(source_path: Path) -> tuple[int, int]:
    """Return (width, height) of the video stream using ffprobe."""
    try:
        result = subprocess.run(
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
                str(source_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("ffmpeg ffprobe timed out after 30s") from exc
    try:
        parts = result.stdout.strip().split(",")
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        logger.warning(
            "_frame_dimensions: unparseable ffprobe output %r — using default 1920×1080",
            result.stdout[:100],
        )
        return 1920, 1080  # safe default


def _source_duration_s(source_path: Path) -> float:
    """Return the total duration of ``source_path`` in seconds via ffprobe.

    Returns ``float('inf')`` on any failure so callers can treat unknown
    duration as "allow through" rather than aborting the render.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(source_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return float(result.stdout.strip())
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return float("inf")  # unknown → caller allows through


def _detect_face_box(keyframe_path: Path) -> tuple[int, int, int, int] | None:
    """Largest detected face as ``(x, y, w, h)`` in SOURCE pixels, or ``None``
    when OpenCV is unavailable, the frame is unreadable, or no face is found.

    The full box (not just center-x) feeds both the crop centering and the
    caption face-avoidance rule (Issue 427).
    """
    try:
        import cv2

        img = cv2.imread(str(keyframe_path))
        if img is None:
            logger.info(
                "_detect_face_box: corrupt frame — %s could not be decoded",
                keyframe_path.name,
            )
            return None
        cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"  # type: ignore[attr-defined]
        )
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
        if len(faces) == 0:
            logger.info("_detect_face_box: no face detected in %s", keyframe_path.name)
            return None
        largest = max(faces, key=lambda f: int(f[2]) * int(f[3]))
        return int(largest[0]), int(largest[1]), int(largest[2]), int(largest[3])
    except Exception as exc:
        logger.warning("Face detection failed (%s)", exc)
        return None


def _detect_face_center_x(keyframe_path: Path, frame_width: int) -> int:
    """
    Return x-coordinate of the center of the largest detected face.
    Falls back to frame_width // 2 if OpenCV is unavailable or no face found.
    """
    box = _detect_face_box(keyframe_path)
    if box is None:
        return frame_width // 2
    x, _, w, _ = box
    return x + w // 2


# Animated caption styles (Issue 133). Style identifiers are validated against
# the captions module's VALID_STYLES set; the actual filter string is built per
# render from a generated ASS file (see render_clip_file below). The legacy
# Issue-119 drawtext placeholders (white_large / yellow_impact / captions_sm)
# only ever drew empty text and have been removed — clips that persisted those
# values silently produce no captions, which matches their prior behaviour.
_ANIMATED_CAPTION_STYLES = captions.VALID_STYLES


def frame_dimensions(source_path: Path) -> tuple[int, int]:
    """Public alias for the source frame-dimension probe.

    Ingest needs the source dims to resolve the video-level camera region
    (Issue 439) without reaching for a private name across package boundaries.
    """
    return _frame_dimensions(source_path)


def _face_inside(
    face_box: tuple[int, int, int, int] | None, region: tuple[int, int, int, int]
) -> bool:
    """Whether a detected face's center lies inside ``region``.

    No face detected → True: absence of evidence is not evidence the region is
    wrong, and the module's contract is to fail open (Issue 439).
    """
    if face_box is None:
        return True
    fx, fy, fw, fh = face_box
    rx, ry, rw, rh = region
    return rx <= fx + fw / 2 <= rx + rw and ry <= fy + fh / 2 <= ry + rh


# Auto-zoom punch-in at peak (Issue 184, opt-in via style_preset["zoom_on_peak"]).
# Principle 4 (pattern interrupt). A triangular zoom pulse centered on the clip's
# peak ramps to (1 + _PUNCH_IN_SCALE)× over ±_PUNCH_IN_RAMP_S seconds, then back to
# 100%. Implemented with crop's per-frame `t` variable + scale — NOT zoompan, which
# is built for stills and resamples the stream.
_PUNCH_IN_SCALE = 0.08
_PUNCH_IN_RAMP_S = 0.6


def _punch_in_filter(peak_offset_s: float, out_w: int, out_h: int) -> str:
    """ffmpeg crop+scale chain for a brief punch-in centered at ``peak_offset_s``
    (clip-relative seconds). Zoom ``z(t)=1+A·max(0,1−|t−p|/W)``; the centered crop
    shrinks by ``z`` then scales back to the output resolution. Outside the pulse
    ``z=1`` → crop is the full frame → a no-op."""
    # `\,` escapes the comma inside max() so the filtergraph parser doesn't read
    # it as a filter separator.
    z = f"(1+{_PUNCH_IN_SCALE}*max(0\\,1-abs(t-{peak_offset_s:.3f})/{_PUNCH_IN_RAMP_S}))"
    return f"crop=w=iw/{z}:h=ih/{z}:x=(iw-iw/{z})/2:y=(ih-ih/{z})/2,scale={out_w}:{out_h}"


def render_clip_file(
    source_path: Path,
    start_s: float,
    end_s: float,
    out_path: Path,
    style_preset: dict | None = None,
    transcript_segments: list[dict] | None = None,
    peak_s: float | None = None,
    video_camera_region: dict | None = None,
    video_overlay_spans: dict | None = None,
) -> dict | None:
    """
    Cut [start_s, end_s] from source, crop to 9:16 centered on detected face,
    scale to OUTPUT_W×OUTPUT_H, write to out_path (mp4).

    Returns the computed crop-track dict (the UNIFIED wire contract from
    ``clip_engine.reframe.compute_dynamic_crop`` — Issue 421) when the
    speaker-aware reframe ran, else ``None`` (flag off → the caller nulls any
    previously persisted track so it can never go stale).

    ``style_preset`` is a dict with optional keys:
      - ``subtitle``: one of "bold_pop" | "bold_pop_highlight" | "gradient_slide"
        | "minimal" | None
      - ``caption_position``: "top" | "middle" | "bottom" | None (Issue 427) —
        the creator's caption band. None → per-style default (karaoke at the
        ~70% bottom band with face avoidance; minimal/gradient lower-third).
      - ``captions_enabled``: bool (currently informational — the subtitle key
        is the load-bearing switch)
      - ``zoom_on_peak``: bool (Issue 184) — when set and ``peak_s`` is inside the
        clip window, apply a brief punch-in centered on the peak (Principle 4).
        Off by default.
      - ``denoise``: bool (Issue 185) — when set, run an ``afftdn`` noise-reduction
        pass before loudnorm. Off by default.
      - ``aspect``: str (Issue 182) — export preset, one of ``OUTPUT_PRESETS``
        ("9:16" | "1:1" | "16:9"). Defaults to "9:16" (byte-identical to pre-182).

    Unknown/legacy keys in ``style_preset`` (e.g. the Issue-442-removed
    ``background``, still present on old rows) are ignored — the filter chain
    is crop→scale, full-bleed at every supported aspect, so there is no
    letterbox to fill and nothing to composite behind.

    ``peak_s`` is the clip's absolute peak time (source-relative seconds, from
    ``Clip.peak_s``); the punch-in is centered at ``peak_s - start_s``. Ignored
    when ``None`` or outside ``[start_s, end_s]``.

    ``transcript_segments`` is the full ``Transcript.segments_jsonb["segments"]``
    list (WhisperX shape — see ``ingestion/transcribe.py``). When a known
    caption style is selected the caller passes these; the renderer slices to
    the clip window and generates an ASS subtitle file via
    ``clip_engine.captions``. Missing transcript → caption style is silently
    skipped (the render still succeeds without captions).
    """
    if start_s < 0:
        raise ValueError(f"start_s must be non-negative, got {start_s}")
    src_dur = _source_duration_s(source_path)
    if src_dur < float("inf") and end_s > src_dur:
        raise ValueError(
            f"end_s {end_s}s exceeds source duration {src_dur:.3f}s for {source_path.name}"
        )
    duration = end_s - start_s
    if duration <= 0:
        raise ValueError(f"Invalid clip range: {start_s}s–{end_s}s")

    # Give ffmpeg generous headroom: libx264 fast preset encodes near real-time on
    # 1080p; 4× the clip duration is a safe ceiling for any reasonable hardware.
    # Floor at 120s so short clips don't get an absurdly tight budget.
    render_timeout_s = max(120.0, duration * 4)

    frame_w, frame_h = _frame_dimensions(source_path)

    # Export preset (Issue 182): output geometry from the shared registry. Default
    # 9:16; `style_preset["aspect"]` selects 1:1 / 16:9. Crop keeps full height and
    # derives width from the target ratio — 9:16 is byte-identical to the old path.
    aspect = (style_preset or {}).get("aspect") or _DEFAULT_FORMAT
    out_w, out_h = OUTPUT_PRESETS.get(aspect, OUTPUT_PRESETS[_DEFAULT_FORMAT])
    crop_w = int(frame_h * out_w / out_h)
    crop_w = min(crop_w, frame_w)

    # ── Reframe: per-frame (gated) or single-keyframe Haar (default) ─────────
    # Issue 189: ACTIVE_SPEAKER_REFRAME_ENABLED defaults False; the per-frame
    # path is NEVER verified on this dev box (no ffmpeg/real media). Only flip
    # the flag after render-env smoke tests pass. See clip_engine/reframe.py.
    from config import settings as _settings  # local import avoids circular dep at module init

    sendcmd_path: Path | None = None  # set when the per-frame path is active
    reframe_track: dict | None = None  # the wire-contract dict returned to the caller

    # Punch-in activity is needed BEFORE the reframe call: the planner
    # suppresses speaker cuts inside the zoom pulse (±0.6s of the peak).
    punch_in_active = bool(
        style_preset
        and style_preset.get("zoom_on_peak")
        and peak_s is not None
        and 0.0 <= (peak_s - start_s) <= duration
    )

    # ── Face geometry (unconditional, Issue 433) ─────────────────────────────
    # One midpoint keyframe + Haar box feeds BOTH caption face-avoidance
    # (Issue 427 — previously dead under the reframe flag) and the camera
    # region's face sanity gate.
    face_box: tuple[int, int, int, int] | None = None  # source px
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        kf_path = Path(tmp.name)
    try:
        mid_s = start_s + duration / 2
        _extract_keyframe(source_path, mid_s, kf_path, timeout_s=render_timeout_s)
        face_box = _detect_face_box(kf_path)
    finally:
        kf_path.unlink(missing_ok=True)

    # ── Camera-region pre-crop (Issue 430, composed with the reframe in 433) ─
    # Produced podcast layouts carry their own chrome (logo cards, name chips,
    # social banners); the full-height crop slices through it. Detect the
    # active camera via temporal variance and crop INTO it before the 9:16
    # composition. The reframe (below) then runs entirely in REGION space:
    # sampled frames are sliced to the region, so sendcmd x-commands and the
    # persisted crop track are region-relative by construction — the region
    # pre-crop filter translates the stream, and no offset arithmetic exists.
    region_x, region_y = 0, 0
    region_w, region_h = frame_w, frame_h
    if _settings.CAMERA_REGION_DETECT_ENABLED:
        from clip_engine import camera_region as _camera_region

        # Prefer the video-level region resolved once at ingest (Issue 439) so
        # clips of one source cannot disagree. It carries no face-sanity check of
        # its own — there is no single face for a whole video — so validate it
        # against THIS clip's face box and fall back to per-clip detection if the
        # speaker is outside it.
        detected = _camera_region.region_from_video_json(video_camera_region, frame_w, frame_h)
        if detected is not None and not _face_inside(face_box, detected):
            logger.info(
                "camera_region: video-level region %s does not contain this clip's face "
                "box %s — re-detecting for this clip",
                detected,
                face_box,
            )
            detected = None
        if detected is None:
            detected = _camera_region.detect_camera_region(
                source_path,
                start_s,
                end_s,
                frame_w,
                frame_h,
                sample_frames=_settings.CAMERA_REGION_SAMPLE_FRAMES,
                motion_thresh=_settings.CAMERA_REGION_MOTION_THRESH,
                min_area_frac=_settings.CAMERA_REGION_MIN_AREA_FRAC,
                min_height_frac=_settings.CAMERA_REGION_MIN_HEIGHT_FRAC,
                full_frame_frac=_settings.CAMERA_REGION_FULL_FRAME_FRAC,
                pad_frac=_settings.CAMERA_REGION_PAD_FRAC,
                face_box=face_box,
                timeout_s=render_timeout_s,
            )
        if detected is not None:
            region_x, region_y, region_w, region_h = detected
            # All downstream geometry switches to region space.
            crop_w = min(int(region_h * out_w / out_h), region_w)
    has_camera_region = (region_x, region_y, region_w, region_h) != (0, 0, frame_w, frame_h)

    if _settings.ACTIVE_SPEAKER_REFRAME_ENABLED:
        # Speaker-aware dynamic crop (Issue 189 + Issue 420/421), region-space
        # since Issue 433: the region dims are passed AS the frame dims and
        # sampled frames are sliced inside compute_dynamic_crop.
        # Import the module (not the function) so `clip_engine.reframe.compute_dynamic_crop`
        # is the canonical patch target in tests (patching a `from … import` binding
        # would only patch the local reference, not the source).
        import clip_engine.reframe as _reframe_mod  # noqa: PLC0415

        reframe_result = _reframe_mod.compute_dynamic_crop(
            source_path=source_path,
            start_s=start_s,
            end_s=end_s,
            frame_width=region_w,
            frame_height=region_h,
            crop_w=crop_w,
            sample_fps=_settings.REFRAME_SAMPLE_FPS,
            transcript_segments=transcript_segments,
            punch_in_peak_s=peak_s if punch_in_active else None,
            region=(region_x, region_y, region_w, region_h) if has_camera_region else None,
        )
        smoothed_track = reframe_result.track_points
        sendcmd_script = reframe_result.sendcmd_text
        reframe_track = reframe_result.track_json
        if smoothed_track:
            # Use the first smoothed center as the static fallback; sendcmd
            # will override x dynamically if there are multiple points.
            x_offset = max(0, min(smoothed_track[0].center_x - crop_w // 2, region_w - crop_w))
        else:
            x_offset = (region_w - crop_w) // 2  # center fallback

        if sendcmd_script:
            # Write the sendcmd script to a sibling temp file.
            sendcmd_path = out_path.with_suffix(".sendcmd")
            sendcmd_path.parent.mkdir(parents=True, exist_ok=True)
            sendcmd_path.write_text(sendcmd_script)
            logger.info(
                "Per-frame reframe enabled for %s: mode=%s, sendcmd written to %s",
                source_path.name,
                reframe_result.mode,
                sendcmd_path,
            )
        else:
            # Single-sample or empty track: fall through to static crop.
            logger.info(
                "Per-frame reframe: static/single-sample track for %s — using static x_offset=%d",
                source_path.name,
                x_offset,
            )
    else:
        # Legacy path: static crop centered on the Haar face box (region-space
        # when a region is active; identical to the pre-433 math otherwise).
        face_x_rel = face_box[0] + face_box[2] // 2 - region_x if face_box else region_w // 2
        x_offset = max(0, min(face_x_rel - crop_w // 2, region_w - crop_w))

    # Build vf chain: [sendcmd→]crop → scale, with optional style additions.
    # `-ss` before `-i` is fast (seeks to the nearest keyframe first); `-accurate_seek`
    # then decodes to the exact start frame. With re-encoding (libx264) accurate_seek is
    # already the ffmpeg default, but we set it explicitly so the cut stays frame-accurate
    # even if anyone introduces `-c copy` later — the clip MUST start exactly at the setup.
    #
    # When the per-frame reframe path is active and produced a multi-point track,
    # a ``sendcmd=f=<file>`` filter is prepended.  It has no video output pad —
    # it only injects commands into the filtergraph — so the crop filter
    # follows immediately after in the same comma-separated vf chain.
    # The initial crop x is set to x_offset; sendcmd overrides it at each
    # sample timestamp. Outside the first sample window ffmpeg holds the last
    # set value, so there is no "uncovered" period.
    # A detected camera region prepends its own (unlabeled, unaddressed) crop;
    # without one, region_h == frame_h and the chain is byte-identical to
    # before. In sendcmd chains the 9:16 crop carries the `@spk` instance
    # label — sendcmd commands target `crop@spk x`, because a bare `crop`
    # target addresses EVERY crop instance in the graph (empirically verified
    # 2026-08-05), including the region pre-crop and the punch-in crop.
    # Static chains keep the unlabeled `crop=` spelling (byte-identical).
    # Transient overlay mask (Issue 448) goes FIRST, in absolute source
    # coordinates, so the region pre-crop and the 9:16 crop carry the masked
    # pixels through by construction — no offset arithmetic, and it works
    # whether or not a camera region was detected. Empty list when no span
    # overlaps this clip, which keeps an unaffected clip's chain byte-identical.
    overlay_parts: list[str] = []
    if video_overlay_spans:
        from clip_engine.overlay_bands import overlay_blur_filters, spans_from_video_json

        unpacked = spans_from_video_json(video_overlay_spans, frame_w, frame_h)
        if unpacked is not None:
            band_y, band_h, spans = unpacked
            overlay_parts = overlay_blur_filters(band_y, band_h, spans, start_s, end_s, frame_w)
            if overlay_parts:
                logger.info(
                    "overlay_bands: masking y=%d h=%d over %d span(s) for clip [%.2f–%.2f]",
                    band_y,
                    band_h,
                    len(spans),
                    start_s,
                    end_s,
                )

    if sendcmd_path is not None:
        vf_parts = [f"sendcmd=f={sendcmd_path}", *overlay_parts]
        if has_camera_region:
            vf_parts.append(f"crop={region_w}:{region_h}:{region_x}:{region_y}")
        vf_parts.extend([f"crop@spk={crop_w}:{region_h}:{x_offset}:0", f"scale={out_w}:{out_h}"])
    else:
        vf_parts = [*overlay_parts]
        if has_camera_region:
            vf_parts.append(f"crop={region_w}:{region_h}:{region_x}:{region_y}")
        vf_parts.extend([f"crop={crop_w}:{region_h}:{x_offset}:0", f"scale={out_w}:{out_h}"])

    # Auto-zoom punch-in (Issue 184): applied to the framed video BEFORE subtitles
    # so the captions stay steady while the content zooms. Off unless the creator
    # opted in and the peak actually falls inside this clip window.
    if punch_in_active and peak_s is not None:
        vf_parts.append(_punch_in_filter(peak_s - start_s, out_w, out_h))

    ass_path: Path | None = None
    if style_preset:
        subtitle_key = style_preset.get("subtitle")
        if subtitle_key in _ANIMATED_CAPTION_STYLES:
            # Sibling temp file to out_path so we co-cleanup in the finally block.
            # Suffix is required for the {clip_id}_{style}.ass naming hint Issue 133
            # asked for — out_path is already clip-unique (the worker creates a
            # NamedTemporaryFile per render call), so the suffix keeps concurrent
            # re-renders from stomping each other.
            ass_path = out_path.with_suffix(f".{subtitle_key}.ass")
            # Face bottom in OUTPUT-canvas pixels (Issue 427): the (region) crop
            # keeps region height and scales region_h → out_h, so the vertical
            # factor applies after subtracting the region's y origin. The Haar
            # box is unconditional since Issue 433, so avoidance is live on the
            # reframe path too. None when no face was detected.
            face_bottom_px = (
                round((face_box[1] + face_box[3] - region_y) * out_h / region_h)
                if face_box
                else None
            )
            captions.build_ass_subtitles(
                segments=transcript_segments,
                style=subtitle_key,
                clip_start_s=start_s,
                clip_duration_s=duration,
                out_path=ass_path,
                play_res_x=out_w,
                play_res_y=out_h,
                caption_position=style_preset.get("caption_position"),
                face_bottom_px=face_bottom_px,
                words_per_group=_settings.CAPTION_WORDS_PER_GROUP,
                group_max_gap_s=_settings.CAPTION_GROUP_MAX_GAP_S,
                baseline_frac=_settings.CAPTION_BASELINE_FRAC,
                min_bottom_margin_px=_settings.CAPTION_MIN_BOTTOM_MARGIN_PX,
                top_margin_px=_settings.CAPTION_TOP_MARGIN_PX,
                face_avoid_pad_px=_settings.CAPTION_FACE_AVOID_PAD_PX,
            )
            if ass_path.exists():
                # The subtitles= filter uses `:` as arg separator; the ass path lives
                # in /tmp/ so colons in the path are not a real concern here.
                vf_parts.append(f"subtitles={ass_path}:fontsdir={_FONTS_DIR}")
            else:
                # Issue 438: the render still succeeds — a captionless clip beats
                # no clip — but a requested caption track that produced nothing
                # must not vanish without a trace. Usual causes: no word-level
                # timings overlapping the window, or an empty transcript slice.
                logger.warning(
                    "captions requested (style=%s) but no caption track was produced "
                    "for %s [%.1f-%.1f] — rendering WITHOUT captions",
                    subtitle_key,
                    out_path.name,
                    start_s,
                    end_s,
                )
                ass_path = None
        elif subtitle_key is not None:
            # A style key that is not in VALID_STYLES falls through the branch
            # above and silently disables captions (Issue 438).
            logger.warning(
                "captions requested with unknown style %r for %s — rendering WITHOUT "
                "captions (valid: %s)",
                subtitle_key,
                out_path.name,
                sorted(_ANIMATED_CAPTION_STYLES),
            )

    vf = ",".join(vf_parts)

    # Opt-in denoise (Issue 185): prepend afftdn to the audio chain, before
    # loudnorm — so loudnorm measures and targets the denoised signal.
    denoise_on = bool(style_preset and style_preset.get("denoise"))
    measure_af = f"loudnorm={_LOUDNORM_TARGET}:print_format=json"
    if denoise_on:
        measure_af = f"{_DENOISE_FILTER},{measure_af}"

    # Two-pass loudness normalization (Issue 181): measure the clip-window audio,
    # then apply the measured values so the gain is linear (no pumping). The
    # measurement pass decodes audio only (`-vn`) for speed and degrades to a flat
    # render if it fails or the clip is near-silent.
    loudnorm_filter = _measure_loudnorm_filter(
        [
            "ffmpeg",
            "-y",
            "-ss",
            str(start_s),
            "-accurate_seek",
            "-i",
            str(source_path),
            "-t",
            str(duration),
            "-vn",
            "-af",
            measure_af,
            "-f",
            "null",
            "-",
        ],
        "render",
        render_timeout_s,
    )

    render_cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(start_s),
        "-accurate_seek",
        "-i",
        str(source_path),
        "-t",
        str(duration),
        "-vf",
        vf,
    ]
    # Audio filter chain: denoise (opt-in) → loudnorm (when measured). Order
    # matters — denoise first so normalization doesn't re-lift the noise floor.
    audio_filters = []
    if denoise_on:
        audio_filters.append(_DENOISE_FILTER)
    if loudnorm_filter:
        audio_filters.append(loudnorm_filter)
    if audio_filters:
        render_cmd += ["-af", ",".join(audio_filters)]
    render_cmd += [
        "-c:v",
        "libx264",
        "-crf",
        "23",
        "-preset",
        "fast",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    try:
        _run(render_cmd, "render", timeout_s=render_timeout_s)
    finally:
        if ass_path is not None:
            ass_path.unlink(missing_ok=True)
        # Clean up the per-frame reframe sendcmd temp file if it was written.
        if sendcmd_path is not None:
            sendcmd_path.unlink(missing_ok=True)
    logger.info(
        "Rendered clip %s→%s style=%s (%s)", source_path.name, out_path.name, style_preset, vf
    )
    return reframe_track


# Per-segment audio fade applied at every splice point in render_cleaned_clip_file.
# 5ms is the documented production figure: well below the ~20ms human fade-
# perception threshold (~220 samples at 44.1kHz) yet large enough to bring the
# waveform to zero on both sides of every cut. See docs/DECISIONS.md.
_CLEAN_AFADE_S = 0.005


def _audio_segment_filter(idx: int, start: float, end: float) -> str:
    """Build the ``atrim``+``afade`` audio-filter line for one kept segment.

    A 5ms ``afade`` in/out brackets every splice for click prevention; the fade
    is halved for any segment shorter than ``2 × _CLEAN_AFADE_S`` so it never
    exceeds half the segment duration (which ffmpeg rejects). Shared by the
    measurement and apply passes so both see byte-identical audio (Issue 135/181).
    """
    seg_dur = end - start
    afade_s = min(_CLEAN_AFADE_S, seg_dur / 2.0)
    fade_out_st = max(0.0, seg_dur - afade_s)
    return (
        f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS,"
        f"afade=t=in:st=0:d={afade_s},"
        f"afade=t=out:st={fade_out_st:.3f}:d={afade_s}[a{idx}];"
    )


def _measure_concat_loudnorm(
    source_path: Path,
    segments: list[tuple[float, float]],
    out_path: Path,
    label: str,
    timeout_s: float,
) -> str | None:
    """Measure loudness of the CONCATENATED segment audio and return the
    second-pass ``loudnorm`` filter string (Issue 181 contract: the loudnorm
    target is the final stitched output, not each segment).

    The measurement graph is audio-only (``concat … v=0:a=1``) and runs to
    ``null``; it degrades to ``None`` (flat render) on failure or near-silence.
    Segment afades come from ``_audio_segment_filter`` so measurement and apply
    passes see byte-identical audio. Shared by ``render_cleaned_clip_file`` and
    ``render_summary_file``.
    """
    measure_lines = [_audio_segment_filter(idx, s, e) for idx, (s, e) in enumerate(segments)]
    a_inputs = "".join(f"[a{idx}]" for idx in range(len(segments)))
    measure_lines.append(
        f"{a_inputs}concat=n={len(segments)}:v=0:a=1[outa];"
        f"[outa]loudnorm={_LOUDNORM_TARGET}:print_format=json[outm]"
    )
    measure_script_path = out_path.with_suffix(".measure.filter")
    measure_script_path.parent.mkdir(parents=True, exist_ok=True)
    measure_script_path.write_text("\n".join(measure_lines))
    try:
        return _measure_loudnorm_filter(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(source_path),
                "-filter_complex_script",
                str(measure_script_path),
                "-map",
                "[outm]",
                "-f",
                "null",
                "-",
            ],
            label,
            timeout_s,
        )
    finally:
        measure_script_path.unlink(missing_ok=True)


def render_cleaned_clip_file(
    source_path: Path,
    keep_ranges: list[tuple[float, float]],
    out_path: Path,
    timeout_s: float = 120.0,
) -> None:
    """Excise everything OUTSIDE ``keep_ranges`` from ``source_path`` and write
    the concatenated result to ``out_path`` (Issue 134).

    ``keep_ranges`` is a list of ``(start_s, end_s)`` pairs in
    source-relative seconds — the inverse of the cut-list from
    ``clip_engine.filler.detect_cut_segments`` (call
    ``invert_to_keep_ranges`` after ``merge_adjacent_cuts``).

    The filter graph is written to a sibling ``.filter`` file and passed via
    ``-filter_complex_script``; both temp file and script are cleaned in a
    ``finally`` block. Each kept segment carries a 5ms ``afade=in`` + 5ms
    ``afade=out`` for click prevention at the splice points.

    Raises ``ValueError`` when ``keep_ranges`` is empty or contains invalid
    pairs; raises ``RuntimeError`` on ffmpeg failure.
    """
    if not keep_ranges:
        raise ValueError("render_cleaned_clip_file: empty keep_ranges")
    for s, e in keep_ranges:
        if e <= s:
            raise ValueError(f"render_cleaned_clip_file: invalid range ({s}, {e})")

    # Normalize: sort by start time, then merge overlapping/adjacent ranges.
    # Unsorted or overlapping inputs are common when the cut-list generator returns
    # ranges out of order or with touching boundaries; normalizing here is more robust
    # than rejecting (DECISIONS.md — Issue 329).
    _original = list(keep_ranges)
    _sorted = sorted(keep_ranges, key=lambda r: r[0])
    _merged: list[tuple[float, float]] = []
    for _s, _e in _sorted:
        if _merged and _s <= _merged[-1][1]:
            _merged[-1] = (_merged[-1][0], max(_merged[-1][1], _e))
        else:
            _merged.append((_s, _e))
    if _merged != _original:
        logger.info(
            "render_cleaned_clip_file: keep_ranges normalized (sorted/merged) %s → %s for %s",
            _original,
            _merged,
            source_path.name,
        )
    keep_ranges = _merged

    # Two-pass loudness normalization (Issue 181): measure the *concatenated* kept
    # audio (the loudnorm target is the final clip, not each segment), then apply
    # the measured values in the real render.
    loudnorm_filter = _measure_concat_loudnorm(
        source_path, keep_ranges, out_path, "clean render", timeout_s
    )

    script_lines: list[str] = []
    concat_inputs: list[str] = []
    for idx, (start, end) in enumerate(keep_ranges):
        # afade=out start time is segment-relative because setpts/asetpts reset
        # PTS to 0 at the start of each trimmed segment. The afade guard (halving
        # the fade for sub-10ms segments) lives in ``_audio_segment_filter``.
        script_lines.append(
            f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[v{idx}];"
        )
        script_lines.append(_audio_segment_filter(idx, start, end))
        concat_inputs.append(f"[v{idx}][a{idx}]")
    # Chain loudnorm onto the concatenated audio when measurement succeeded; the
    # concat substring stays intact either way so the cut shape is unchanged.
    concat_line = f"{''.join(concat_inputs)}concat=n={len(keep_ranges)}:v=1:a=1[outv][outa]"
    audio_out = "[outa]"
    if loudnorm_filter:
        concat_line += f";[outa]{loudnorm_filter}[outaln]"
        audio_out = "[outaln]"
    script_lines.append(concat_line)
    script_text = "\n".join(script_lines)

    # Sibling temp script — same cleanup pattern as the ASS subtitle path.
    script_path = out_path.with_suffix(".filter")
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script_text)

    try:
        _run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(source_path),
                "-filter_complex_script",
                str(script_path),
                "-map",
                "[outv]",
                "-map",
                audio_out,
                "-c:v",
                "libx264",
                "-crf",
                "23",
                "-preset",
                "fast",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                str(out_path),
            ],
            "clean render",
            timeout_s=timeout_s,
        )
    finally:
        script_path.unlink(missing_ok=True)
    logger.info(
        "Cleaned clip %s→%s segments=%d",
        source_path.name,
        out_path.name,
        len(keep_ranges),
    )


# ── Stream-VOD recap render (Issue 191) ───────────────────────────────────────

# Light per-segment VIDEO fade at every splice, mirroring the audio afades'
# placement (in at 0, out at dur−d, halved for very short segments). The audio
# fade is 5ms (click prevention); a 5ms video fade is under one frame, so the
# video constant is longer — 0.1s (~3 frames at 30fps) softens the jump cut
# without eating content. Hard cuts + fades were chosen over xfade (cumulative-
# offset fragility; overlap eats content) — docs/DECISIONS.md (2026-07-02).
_SUMMARY_VIDEO_FADE_S = 0.1
_SUMMARY_FORMAT = "16:9"


def _video_segment_filter(idx: int, start: float, end: float, out_w: int, out_h: int) -> str:
    """Build the ``trim``+``scale``+``fade`` video-filter line for one recap segment.

    ``setsar=1`` normalizes the sample aspect ratio so ``concat`` never rejects
    segments whose scaled SAR differs. fade ``st`` values are segment-relative
    because ``setpts=PTS-STARTPTS`` resets timestamps to 0 per segment.
    """
    seg_dur = end - start
    fade_s = min(_SUMMARY_VIDEO_FADE_S, seg_dur / 2.0)
    fade_out_st = max(0.0, seg_dur - fade_s)
    return (
        f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS,"
        f"scale={out_w}:{out_h},setsar=1,"
        f"fade=t=in:st=0:d={fade_s:.3f},fade=t=out:st={fade_out_st:.3f}:d={fade_s:.3f}[v{idx}];"
    )


def build_summary_filtergraph(
    segments: list[tuple[float, float]],
    loudnorm_filter: str | None = None,
) -> tuple[str, str]:
    """Build the ``filter_complex`` script for a 16:9 multi-segment recap.

    PURE — no subprocess, no filesystem — so the graph shape is unit-testable
    without ffmpeg. Per segment: ``trim``/``atrim`` + ``setpts``/``asetpts=
    PTS-STARTPTS``, per-segment ``scale`` to the 16:9 preset, a light video
    fade in/out and a 5ms ``afade`` at every splice; then
    ``concat=n=N:v=1:a=1``. When ``loudnorm_filter`` is provided (the measured
    second-pass string) it is chained onto the concatenated audio.

    Returns ``(script_text, audio_out_label)`` where ``audio_out_label`` is the
    ``-map`` target for audio (``[outa]``, or ``[outaln]`` when normalized).
    """
    out_w, out_h = OUTPUT_PRESETS[_SUMMARY_FORMAT]
    lines: list[str] = []
    concat_inputs: list[str] = []
    for idx, (start, end) in enumerate(segments):
        lines.append(_video_segment_filter(idx, start, end, out_w, out_h))
        lines.append(_audio_segment_filter(idx, start, end))
        concat_inputs.append(f"[v{idx}][a{idx}]")
    concat_line = f"{''.join(concat_inputs)}concat=n={len(segments)}:v=1:a=1[outv][outa]"
    audio_out = "[outa]"
    if loudnorm_filter:
        concat_line += f";[outa]{loudnorm_filter}[outaln]"
        audio_out = "[outaln]"
    lines.append(concat_line)
    return "\n".join(lines), audio_out


def build_summary_render_cmd(
    source_path: Path, script_path: Path, out_path: Path, audio_out: str
) -> list[str]:
    """PURE argv builder for the recap render: single ``-i`` source, graph via
    ``-filter_complex_script`` (never inline — OS arg-length limits at scale)."""
    return [
        "ffmpeg",
        "-y",
        "-i",
        str(source_path),
        "-filter_complex_script",
        str(script_path),
        "-map",
        "[outv]",
        "-map",
        audio_out,
        "-c:v",
        "libx264",
        "-crf",
        "23",
        "-preset",
        "fast",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(out_path),
    ]


def render_summary_file(
    source_path: Path,
    segments: list[tuple[float, float]],
    out_path: Path,
    timeout_s: float | None = None,
) -> None:
    """Stitch the ordered recap ``segments`` from ``source_path`` into one
    16:9 (1920×1080) mp4 at ``out_path`` (Issue 191).

    ``segments`` is a list of ``(start_s, end_s)`` pairs in source-relative
    seconds — the ``summaries.segments`` timing contract from Issue 190. They
    are sorted chronologically here so the stitched recap always plays in
    narrative order even if a caller passes them shuffled.

    Decode approach: a single-input trim graph — ffmpeg decodes the whole VOD
    once and drops frames outside the trims. Simple and frame-accurate;
    the extra decode cost on multi-hour VODs is accepted for the beta (a
    seek-per-segment multi-input graph is the optimization if recap renders
    become a bottleneck). Two-pass loudnorm is measured on the CONCATENATED
    audio (Issue 181 contract) and degrades to a flat render on failure.

    When ``timeout_s`` is ``None`` a budget is derived from the source
    duration (the whole-VOD decode dominates on long sources).

    Raises ``ValueError`` for empty/invalid ``segments``; ``RuntimeError`` on
    ffmpeg failure.
    """
    if not segments:
        raise ValueError("render_summary_file: empty segments")
    for s, e in segments:
        if s < 0 or e <= s:
            raise ValueError(f"render_summary_file: invalid segment ({s}, {e})")
    segments = sorted(segments, key=lambda r: r[0])

    if timeout_s is None:
        total_dur = sum(e - s for s, e in segments)
        src_dur = _source_duration_s(source_path)
        # 4× the recap duration covers the encode; 1× the source duration covers
        # the whole-VOD decode (ffmpeg decodes well above real time); floor 300s.
        timeout_s = max(300.0, total_dur * 4, src_dur if src_dur < float("inf") else 0.0)

    loudnorm_filter = _measure_concat_loudnorm(
        source_path, segments, out_path, "summary render", timeout_s
    )
    script_text, audio_out = build_summary_filtergraph(segments, loudnorm_filter)

    # Sibling temp script — same cleanup pattern as render_cleaned_clip_file.
    script_path = out_path.with_suffix(".filter")
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script_text)
    try:
        _run(
            build_summary_render_cmd(source_path, script_path, out_path, audio_out),
            "summary render",
            timeout_s=timeout_s,
        )
    finally:
        script_path.unlink(missing_ok=True)
    logger.info(
        "Recap %s→%s segments=%d",
        source_path.name,
        out_path.name,
        len(segments),
    )
