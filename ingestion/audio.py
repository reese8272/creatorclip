"""
Audio signal extraction using librosa.

Extracts energy spikes, silence, and a laughter/applause heuristic from a WAV file.
All thresholds are tunable constants; no ML required at this fidelity.
"""

import logging
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
    y, sr = librosa.load(str(audio_path), sr=None, mono=True)
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
