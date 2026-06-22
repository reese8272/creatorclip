"""
Render a clip: extract segment with ffmpeg, detect active speaker face,
crop to 9:16 centered on face, upload to storage.

Face detection: OpenCV Haar frontal-face cascade on a single keyframe.
Falls back to frame center if no face is found.

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
import re
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
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ffmpeg {label} timed out after {timeout_s}s") from exc
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg {label} failed: {result.stderr[:500]}")


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
        return 1920, 1080  # safe default


def _detect_face_center_x(keyframe_path: Path, frame_width: int) -> int:
    """
    Return x-coordinate of the center of the largest detected face.
    Falls back to frame_width // 2 if OpenCV is unavailable or no face found.
    """
    try:
        import cv2

        img = cv2.imread(str(keyframe_path))
        if img is None:
            return frame_width // 2
        cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"  # type: ignore[attr-defined]
        )
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
        if len(faces) == 0:
            return frame_width // 2
        largest = max(faces, key=lambda f: int(f[2]) * int(f[3]))
        x, w = int(largest[0]), int(largest[2])
        return x + w // 2
    except Exception as exc:
        logger.warning("Face detection failed (%s) — using frame center", exc)
        return frame_width // 2


# Animated caption styles (Issue 133). Style identifiers are validated against
# the captions module's VALID_STYLES set; the actual filter string is built per
# render from a generated ASS file (see render_clip_file below). The legacy
# Issue-119 drawtext placeholders (white_large / yellow_impact / captions_sm)
# only ever drew empty text and have been removed — clips that persisted those
# values silently produce no captions, which matches their prior behaviour.
_ANIMATED_CAPTION_STYLES = captions.VALID_STYLES

_BACKGROUND_STYLES: dict[str, str] = {
    "blur": "split[v][blur];[blur]scale={ow}:{oh},boxblur=luma_radius=20:luma_power=2[blurred];[blurred][v]overlay=(W-w)/2:(H-h)/2",
    "black": "",  # default — letterbox fills with black (ffmpeg default pad colour)
}


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
) -> None:
    """
    Cut [start_s, end_s] from source, crop to 9:16 centered on detected face,
    scale to OUTPUT_W×OUTPUT_H, write to out_path (mp4).

    ``style_preset`` is a dict with optional keys:
      - ``subtitle``: one of "bold_pop" | "bold_pop_highlight" | "gradient_slide"
        | "minimal" | None
      - ``background``: "blur" | "black" | None  (None → default black letterbox)
      - ``captions_enabled``: bool (currently informational — the subtitle key
        is the load-bearing switch)
      - ``zoom_on_peak``: bool (Issue 184) — when set and ``peak_s`` is inside the
        clip window, apply a brief punch-in centered on the peak (Principle 4).
        Off by default.
      - ``denoise``: bool (Issue 185) — when set, run an ``afftdn`` noise-reduction
        pass before loudnorm. Off by default.
      - ``aspect``: str (Issue 182) — export preset, one of ``OUTPUT_PRESETS``
        ("9:16" | "1:1" | "16:9"). Defaults to "9:16" (byte-identical to pre-182).

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

    # Find face center in a keyframe at the midpoint of the clip
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        kf_path = Path(tmp.name)
    try:
        mid_s = start_s + duration / 2
        _extract_keyframe(source_path, mid_s, kf_path, timeout_s=render_timeout_s)
        face_x = _detect_face_center_x(kf_path, frame_w)
    finally:
        kf_path.unlink(missing_ok=True)

    # Clamp crop x-offset
    x_offset = max(0, min(face_x - crop_w // 2, frame_w - crop_w))

    # Build vf chain: crop → scale, with optional style additions.
    # `-ss` before `-i` is fast (seeks to the nearest keyframe first); `-accurate_seek`
    # then decodes to the exact start frame. With re-encoding (libx264) accurate_seek is
    # already the ffmpeg default, but we set it explicitly so the cut stays frame-accurate
    # even if anyone introduces `-c copy` later — the clip MUST start exactly at the setup.
    vf_parts = [f"crop={crop_w}:{frame_h}:{x_offset}:0", f"scale={out_w}:{out_h}"]

    # Auto-zoom punch-in (Issue 184): applied to the framed video BEFORE subtitles
    # so the captions stay steady while the content zooms. Off unless the creator
    # opted in and the peak actually falls inside this clip window.
    if style_preset and style_preset.get("zoom_on_peak") and peak_s is not None:
        peak_offset_s = peak_s - start_s
        if 0.0 <= peak_offset_s <= duration:
            vf_parts.append(_punch_in_filter(peak_offset_s, out_w, out_h))

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
            captions.build_ass_subtitles(
                segments=transcript_segments,
                style=subtitle_key,
                clip_start_s=start_s,
                clip_duration_s=duration,
                out_path=ass_path,
                play_res_x=out_w,
                play_res_y=out_h,
            )
            if ass_path.exists():
                # The subtitles= filter uses `:` as arg separator; the ass path lives
                # in /tmp/ so colons in the path are not a real concern here.
                vf_parts.append(f"subtitles={ass_path}:fontsdir={_FONTS_DIR}")
            else:
                ass_path = None

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
    logger.info(
        "Rendered clip %s→%s style=%s (%s)", source_path.name, out_path.name, style_preset, vf
    )


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

    # Two-pass loudness normalization (Issue 181): measure the *concatenated* kept
    # audio (the loudnorm target is the final clip, not each segment), then apply
    # the measured values in the real render. The measurement graph is audio-only
    # (`concat ... v=0:a=1`) and runs to `null`; it degrades to a flat render on
    # failure or near-silence. Segment afades are shared via ``_audio_segment_filter``.
    measure_lines = [_audio_segment_filter(idx, s, e) for idx, (s, e) in enumerate(keep_ranges)]
    a_inputs = "".join(f"[a{idx}]" for idx in range(len(keep_ranges)))
    measure_lines.append(
        f"{a_inputs}concat=n={len(keep_ranges)}:v=0:a=1[outa];"
        f"[outa]loudnorm={_LOUDNORM_TARGET}:print_format=json[outm]"
    )
    measure_script_path = out_path.with_suffix(".measure.filter")
    measure_script_path.parent.mkdir(parents=True, exist_ok=True)
    measure_script_path.write_text("\n".join(measure_lines))
    try:
        loudnorm_filter = _measure_loudnorm_filter(
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
            "clean render",
            timeout_s,
        )
    finally:
        measure_script_path.unlink(missing_ok=True)

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
