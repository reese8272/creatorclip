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
# Absolute silence floor in dBFS. Silence must be gated absolutely, not against the
# per-file peak: with the old rms/rms.max() ratio a near-silent file "spiked" at 0.6
# of its own noise floor, and audible speech next to one loud transient was flagged
# as silence. Industry silence gates are absolute — ffmpeg silencedetect defaults to
# -60 dB noise tolerance; EBU R128 gates at -70 LUFS. (Issue 352 Batch E)
_SILENCE_DBFS = -60.0  # absolute RMS below this → silence
_LAUGHTER_ENERGY_MIN = 0.3  # minimum energy for laughter classification
_LAUGHTER_ZCR_THRESHOLD = 0.5  # normalized ZCR ≥ this (combined with energy) → laughter
_MIN_ENERGY_DURATION_S = 0.5
_MIN_SILENCE_DURATION_S = 0.3
_MIN_LAUGHTER_DURATION_S = 0.5


_EMPTY_EVENTS: dict = {
    "duration_s": 0.0,
    "energy_spikes": [],
    "silences": [],
    "laughter": [],
}


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

    from config import settings

    hop = 512
    cap_s: int = settings.AUDIO_ANALYSIS_MAX_DURATION_S

    # Check the file's duration before loading (reads the container header only —
    # no audio data decoded) so we can truncate pathologically long files instead of
    # loading the entire WAV array into RAM (OOM vector for multi-hour videos under
    # concurrent workers). (Issue 334)
    try:
        file_duration_s = librosa.get_duration(path=str(audio_path))
    except Exception:
        file_duration_s = None

    if file_duration_s is not None and cap_s > 0 and file_duration_s > cap_s:
        logger.warning(
            "Audio %.0fs exceeds AUDIO_ANALYSIS_MAX_DURATION_S=%d; "
            "truncating analysis to first %ds. (Issue 334)",
            file_duration_s,
            cap_s,
            cap_s,
        )
        # Resample to 16 kHz on load (Issue 74): the RMS/ZCR energy/silence/laughter
        # heuristics need no more fidelity, and sr=None decoded at the native rate
        # (e.g. 48 kHz) held ~3× the samples in memory — an OOM vector for long videos
        # across concurrent workers.
        y, sr = librosa.load(str(audio_path), sr=16000, mono=True, duration=float(cap_s))
    else:
        y, sr = librosa.load(str(audio_path), sr=16000, mono=True)

    # Guard: pathological WAV with too few samples for any frame-level analysis
    # (zero-sample or single-sample). librosa.feature.rms on a 1-sample array
    # returns a degenerate 1-frame result that breaks downstream math. Return
    # empty events cleanly instead of propagating an error. (Issue 334)
    if len(y) < 2:
        logger.warning(
            "Audio file has only %d sample(s) — returning empty events. (Issue 334)",
            len(y),
        )
        return {
            "duration_s": float(len(y) / sr) if sr else 0.0,
            "energy_spikes": [],
            "silences": [],
            "laughter": [],
        }

    duration_s = float(len(y) / sr)
    frame_dur = hop / sr

    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    rms_norm = rms / (rms.max() + 1e-8)
    # Absolute loudness in dBFS — librosa loads float audio in [-1, 1], so ref=1.0
    # is full scale. top_db=None disables the relative-to-max clipping so truly
    # silent frames keep their real (very low) level. (Issue 352 Batch E)
    rms_db = librosa.amplitude_to_db(rms, ref=1.0, top_db=None)

    zcr = librosa.feature.zero_crossing_rate(y=y, hop_length=hop)[0]
    zcr_norm = zcr / (zcr.max() + 1e-8)

    times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)

    silence_mask = rms_db < _SILENCE_DBFS
    if bool(silence_mask.all()):
        logger.warning(
            "Audio peak RMS %.1f dBFS is below the %.0f dBFS silence floor — file is "
            "effectively silent; per-file-relative energy/laughter signals suppressed.",
            float(rms_db.max()),
            _SILENCE_DBFS,
        )
    # Energy spikes and laughter stay relative to the creator's own baseline, but a
    # frame below the absolute silence floor can never be a spike — that was the
    # near-silent-file false-positive vector. (Issue 352 Batch E)
    energy_mask = (rms_norm >= _ENERGY_THRESHOLD) & ~silence_mask
    energy_spikes = _merge_runs(times, energy_mask, rms_norm, frame_dur, _MIN_ENERGY_DURATION_S)
    silences = _merge_runs(times, silence_mask, None, frame_dur, _MIN_SILENCE_DURATION_S)
    laughter_mask = (
        (rms_norm >= _LAUGHTER_ENERGY_MIN) & (zcr_norm >= _LAUGHTER_ZCR_THRESHOLD) & ~silence_mask
    )
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
    duration_s: float | None = None,
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
        duration_s: Known audio duration in seconds.  When provided the
            subprocess timeout is scaled up proportionally — the base
            ``WAVEFORM_TIMEOUT_S`` config value is used when omitted. (Issue 334)

    Raises:
        RuntimeError: When ffmpeg is not found on PATH or the showwavespic
            command exits non-zero.  Caller should catch and log, then skip
            the waveform asset — the rest of the pipeline continues.
    """
    import shutil

    from config import settings

    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH — waveform image skipped")

    output_path = Path(output_path)
    audio_path = Path(audio_path)

    # Scale the timeout with the known audio duration so that long-form videos
    # (e.g. 4-hour podcasts) don't time-out ffmpeg showwavespic on slow hardware.
    # Formula: base + 5 s per minute of audio.  Without duration_s the caller
    # falls back to the WAVEFORM_TIMEOUT_S config default (Issue 334).
    base_timeout: int = settings.WAVEFORM_TIMEOUT_S
    if duration_s is not None and duration_s > 0:
        timeout = max(base_timeout, int(base_timeout + duration_s / 60 * 5))
    else:
        timeout = base_timeout

    # showwavespic has NO background option (`ffmpeg -h filter=showwavespic` lists
    # only size/split_channels/colors/scale/draw/filter) — the previous
    # `:bg_color=...` token made ffmpeg exit non-zero on every call. The standard
    # way to get an opaque background is compositing the (transparent-background)
    # waveform over a `color` source via `overlay`. (Issue 352 Batch E)
    filter_complex = (
        f"color=c={bg_color}:s={width}x{height}[bg];"
        f"[0:a]showwavespic=s={width}x{height}:colors={fg_color}[fg];"
        f"[bg][fg]overlay=format=auto"
    )
    cmd = [
        "ffmpeg",
        "-y",  # overwrite without prompting
        "-i",
        str(audio_path),
        "-filter_complex",
        filter_complex,
        "-frames:v",
        "1",
        str(output_path),
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
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
