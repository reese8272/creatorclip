"""
Source acquisition helpers.

Compliant paths:
  1. Creator uploads a file directly via POST /videos/upload.
  2. yt-dlp of own content — YTDLP_ENABLED=true required; caller must verify ownership.

yt-dlp is off by default. It must never be used on third-party channels.
See docs/COMPLIANCE.md.
"""

import logging
import subprocess
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)

_AUDIO_SAMPLE_RATE = 16000


def extract_audio_wav(source_path: str | Path, dest_path: str | Path) -> None:
    """Extract 16 kHz mono WAV from any video/audio source using ffmpeg."""
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(source_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        str(_AUDIO_SAMPLE_RATE),
        "-ac",
        "1",
        str(dest_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extraction failed: {result.stderr[:500]}")


def download_via_ytdlp(youtube_video_id: str, dest_dir: Path) -> Path:
    """
    Download audio for own-channel content via yt-dlp.

    Guards: YTDLP_ENABLED must be true; caller must enforce creator ownership
    (the data model ensures video.creator_id == authenticated creator).
    Returns the downloaded WAV path.
    """
    if not settings.YTDLP_ENABLED:
        raise ValueError(
            "yt-dlp is disabled. Set YTDLP_ENABLED=true only for your own channel content."
        )
    try:
        import yt_dlp
    except ImportError as exc:
        raise ImportError("yt-dlp is not installed. Run: pip install yt-dlp==2024.11.4") from exc

    url = f"https://www.youtube.com/watch?v={youtube_video_id}"
    out_template = str(dest_dir / f"{youtube_video_id}.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_template,
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
            }
        ],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    wav_path = dest_dir / f"{youtube_video_id}.wav"
    if not wav_path.exists():
        raise FileNotFoundError(f"yt-dlp output not found at {wav_path}")
    return wav_path
