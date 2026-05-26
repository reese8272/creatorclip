"""
Render a clip: extract segment with ffmpeg, detect active speaker face,
crop to 9:16 centered on face, upload to storage.

Face detection: OpenCV Haar frontal-face cascade on a single keyframe.
Falls back to frame center if no face is found.
"""

import logging
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Output resolution for 9:16 Shorts
_OUTPUT_W = 1080
_OUTPUT_H = 1920


def _run(cmd: list[str], label: str) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg {label} failed: {result.stderr[:500]}")


def _extract_keyframe(source_path: Path, seek_s: float, out_path: Path) -> None:
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
    )


def _frame_dimensions(source_path: Path) -> tuple[int, int]:
    """Return (width, height) of the video stream using ffprobe."""
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
    )
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
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
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


def render_clip_file(
    source_path: Path,
    start_s: float,
    end_s: float,
    out_path: Path,
) -> None:
    """
    Cut [start_s, end_s] from source, crop to 9:16 centered on detected face,
    scale to OUTPUT_W×OUTPUT_H, write to out_path (mp4).
    """
    duration = end_s - start_s
    if duration <= 0:
        raise ValueError(f"Invalid clip range: {start_s}s–{end_s}s")

    frame_w, frame_h = _frame_dimensions(source_path)

    # Crop width for 9:16: keep full height, compute matching width
    crop_w = int(frame_h * 9 / 16)
    crop_w = min(crop_w, frame_w)

    # Find face center in a keyframe at the midpoint of the clip
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        kf_path = Path(tmp.name)
    try:
        mid_s = start_s + duration / 2
        _extract_keyframe(source_path, mid_s, kf_path)
        face_x = _detect_face_center_x(kf_path, frame_w)
    finally:
        kf_path.unlink(missing_ok=True)

    # Clamp crop x-offset
    x_offset = max(0, min(face_x - crop_w // 2, frame_w - crop_w))

    # ffmpeg: cut segment → crop → scale
    vf = f"crop={crop_w}:{frame_h}:{x_offset}:0,scale={_OUTPUT_W}:{_OUTPUT_H}"
    _run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            str(start_s),
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
    )
    logger.info("Rendered clip %s→%s (%s)", source_path.name, out_path.name, vf)
