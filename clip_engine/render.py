"""
Render a clip: extract segment with ffmpeg, detect active speaker face,
crop to 9:16 centered on face, upload to storage.

Face detection: OpenCV Haar frontal-face cascade on a single keyframe.
Falls back to frame center if no face is found.

Animated captions (Issue 133): when ``style_preset["subtitle"]`` names a known
caption style (``bold_pop`` / ``gradient_slide`` / ``minimal``), an ASS subtitle
file is generated from the supplied transcript segments and burned in via
libass. See ``clip_engine/captions.py``.

Cleaned render (Issue 134): ``render_cleaned_clip_file`` excises ranges from an
already-rendered clip via a single-pass ``filter_complex`` (``trim`` + ``atrim``
+ ``setpts`` + ``concat``) with a 5ms ``afade`` at every splice for click
prevention. The graph is written to a temp file and passed via
``-filter_complex_script`` to avoid shell-arg-length issues at scale. See
``clip_engine/filler.py`` for the cut-list generator.
"""

import logging
import subprocess
import tempfile
from pathlib import Path

from clip_engine import captions

logger = logging.getLogger(__name__)

# Directory libass searches for custom fonts (Dockerfile installs Anton here).
_FONTS_DIR = "/usr/share/fonts/custom"

# Output resolution for 9:16 Shorts
_OUTPUT_W = 1080
_OUTPUT_H = 1920


def _run(cmd: list[str], label: str, timeout_s: float = 120.0) -> None:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ffmpeg {label} timed out after {timeout_s}s") from exc
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg {label} failed: {result.stderr[:500]}")


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


def render_clip_file(
    source_path: Path,
    start_s: float,
    end_s: float,
    out_path: Path,
    style_preset: dict | None = None,
    transcript_segments: list[dict] | None = None,
) -> None:
    """
    Cut [start_s, end_s] from source, crop to 9:16 centered on detected face,
    scale to OUTPUT_W×OUTPUT_H, write to out_path (mp4).

    ``style_preset`` is a dict with optional keys:
      - ``subtitle``: one of "bold_pop" | "gradient_slide" | "minimal" | None
      - ``background``: "blur" | "black" | None  (None → default black letterbox)
      - ``captions_enabled``: bool (currently informational — the subtitle key
        is the load-bearing switch)

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

    # Crop width for 9:16: keep full height, compute matching width
    crop_w = int(frame_h * 9 / 16)
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
    vf_parts = [f"crop={crop_w}:{frame_h}:{x_offset}:0", f"scale={_OUTPUT_W}:{_OUTPUT_H}"]

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
            )
            if ass_path.exists():
                # The subtitles= filter uses `:` as arg separator; the ass path lives
                # in /tmp/ so colons in the path are not a real concern here.
                vf_parts.append(f"subtitles={ass_path}:fontsdir={_FONTS_DIR}")
            else:
                ass_path = None

    vf = ",".join(vf_parts)
    try:
        _run(
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
                "-vf",
                vf,
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
            "render",
            timeout_s=render_timeout_s,
        )
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

    script_lines: list[str] = []
    concat_inputs: list[str] = []
    for idx, (start, end) in enumerate(keep_ranges):
        seg_dur = end - start
        # afade=out start time is segment-relative because setpts/asetpts reset
        # PTS to 0 at the start of each trimmed segment.
        # Guard: a kept segment shorter than 2 × _CLEAN_AFADE_S would request
        # a fade that exceeds half the segment's duration — ffmpeg errors.
        # Halving the fade keeps the click-prevention character intact for
        # any segment ≥ 0.04 s (one frame at 25 fps); ``edits.MIN_KEEP_SEGMENT_S``
        # is the upstream floor. Found while building Issue 135.
        afade_s = min(_CLEAN_AFADE_S, seg_dur / 2.0)
        fade_out_st = max(0.0, seg_dur - afade_s)
        script_lines.append(
            f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[v{idx}];"
        )
        script_lines.append(
            f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:st=0:d={afade_s},"
            f"afade=t=out:st={fade_out_st:.3f}:d={afade_s}[a{idx}];"
        )
        concat_inputs.append(f"[v{idx}][a{idx}]")
    script_lines.append(f"{''.join(concat_inputs)}concat=n={len(keep_ranges)}:v=1:a=1[outv][outa]")
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
                "[outa]",
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
