"""
Audio signal extraction using librosa.

Extracts energy spikes, silence, and a laughter/applause heuristic from a WAV file.
All thresholds are tunable constants; no ML required at this fidelity.

Issue 188: also provides ``generate_waveform_image`` — an ffmpeg showwavespic wrapper
that produces a waveform PNG alongside the existing RMS extraction.  Callers must have
the ffmpeg CLI available; the function raises RuntimeError if it is absent so that the
ingestion pipeline can degrade gracefully (skip waveform, log warning) without crashing.
"""

import logging
import subprocess
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_ENERGY_THRESHOLD = 0.6  # normalized RMS ≥ this → energy spike
_SILENCE_THRESHOLD = 0.03  # normalized RMS < this → silence
_LAUGHTER_ENERGY_MIN = 0.3  # minimum energy for laughter classification
_LAUGHTER_ZCR_THRESHOLD = 0.5  # normalized ZCR ≥ this (combined with energy) → laughter
_MIN_ENERGY_DURATION_S = 0.5
_MIN_SILENCE_DURATION_S = 0.3
_MIN_LAUGHTER_DURATION_S = 0.5


def extract_audio_events(audio_path: str | Path) -> dict:
    """
    Returns:
        {
          "duration_s": float,
          "energy_spikes": [{"start_s", "end_s", "value"}],
          "silences":      [{"start_s", "end_s"}],
          "laughter":      [{"start_s", "end_s", "value"}],
        }
    """
    import librosa

    hop = 512
    # Resample to 16 kHz on load (Issue 74): the RMS/ZCR energy/silence/laughter
    # heuristics need no more fidelity, and sr=None decoded at the native rate
    # (e.g. 48 kHz) held ~3× the samples in memory — an OOM vector for long videos
    # across concurrent workers.
    y, sr = librosa.load(str(audio_path), sr=16000, mono=True)
    duration_s = float(len(y) / sr)
    frame_dur = hop / sr

    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    rms_norm = rms / (rms.max() + 1e-8)

    zcr = librosa.feature.zero_crossing_rate(y=y, hop_length=hop)[0]
    zcr_norm = zcr / (zcr.max() + 1e-8)

    times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)

    energy_spikes = _merge_runs(
        times, rms_norm >= _ENERGY_THRESHOLD, rms_norm, frame_dur, _MIN_ENERGY_DURATION_S
    )
    silences = _merge_runs(
        times, rms_norm < _SILENCE_THRESHOLD, None, frame_dur, _MIN_SILENCE_DURATION_S
    )
    laughter_mask = (rms_norm >= _LAUGHTER_ENERGY_MIN) & (zcr_norm >= _LAUGHTER_ZCR_THRESHOLD)
    laughter = _merge_runs(times, laughter_mask, zcr_norm, frame_dur, _MIN_LAUGHTER_DURATION_S)

    return {
        "duration_s": duration_s,
        "energy_spikes": energy_spikes,
        "silences": silences,
        "laughter": laughter,
    }


def generate_waveform_image(
    audio_path: str | Path,
    output_path: str | Path,
    *,
    width: int = 1200,
    height: int = 200,
    fg_color: str = "0x6699cc",
    bg_color: str = "0x1a1a1a",
) -> Path:
    """Generate a waveform PNG via ffmpeg ``showwavespic`` (Issue 188).

    Uses ffmpeg's ``showwavespic`` filter which draws a static waveform image
    — the industry-standard approach for server-side waveform generation
    (Descript, Riverside, Opus Clip all use a server-rendered waveform image
    served alongside the media).  The image is written to ``output_path`` and
    its resolved Path is returned.

    Args:
        audio_path: Path to the source audio or video file.
        output_path: Destination PNG path.  Parent directory must already exist.
        width: Output image width in pixels.
        height: Output image height in pixels.
        fg_color: ffmpeg color string for the waveform (hex ``0xRRGGBB``).
        bg_color: ffmpeg color string for the background.

    Raises:
        RuntimeError: When ffmpeg is not found on PATH or the showwavespic
            command exits non-zero.  Caller should catch and log, then skip
            the waveform asset — the rest of the pipeline continues.
    """
    import shutil

    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH — waveform image skipped")

    output_path = Path(output_path)
    audio_path = Path(audio_path)

    cmd = [
        "ffmpeg",
        "-y",  # overwrite without prompting
        "-i", str(audio_path),
        "-filter_complex",
        (
            f"showwavespic=s={width}x{height}"
            f":colors={fg_color}:bg_color={bg_color}"
        ),
        "-frames:v", "1",
        str(output_path),
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg showwavespic failed (exit {result.returncode}): {result.stderr[:400]}"
        )

    logger.info("waveform image written to %s", output_path)
    return output_path


def _merge_runs(
    times: np.ndarray,
    mask: np.ndarray,
    values: "np.ndarray | None",
    frame_duration: float,
    min_duration_s: float,
) -> list[dict]:
    """Convert a boolean mask over frames to a list of event dicts."""
    events: list[dict] = []
    in_run = False
    run_start = 0

    for i, active in enumerate(mask):
        if active and not in_run:
            in_run = True
            run_start = i
        elif not active and in_run:
            in_run = False
            _emit(events, times, values, run_start, i, frame_duration, min_duration_s)

    if in_run:
        _emit(events, times, values, run_start, len(mask), frame_duration, min_duration_s)

    return events


def _emit(
    events: list[dict],
    times: np.ndarray,
    values: "np.ndarray | None",
    start_idx: int,
    end_idx: int,
    frame_duration: float,
    min_duration_s: float,
) -> None:
    start_s = float(times[start_idx])
    end_s = float(times[min(end_idx - 1, len(times) - 1)]) + frame_duration
    if end_s - start_s < min_duration_s:
        return
    event: dict = {"start_s": start_s, "end_s": end_s}
    if values is not None:
        event["value"] = float(values[start_idx:end_idx].mean())
    events.append(event)
