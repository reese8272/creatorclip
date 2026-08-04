"""Issue 392 — waveform peak extraction."""

from __future__ import annotations

import json
import math
import wave
from pathlib import Path

import numpy as np
import pytest

from ingestion.peaks import (
    DEFAULT_SAMPLES_PER_PIXEL,
    MAX_PAIRS,
    compute_peaks,
    samples_per_pixel_for,
    write_peaks_json,
)

SAMPLE_RATE = 16_000


def _write_wav(
    path: Path, samples: np.ndarray, *, rate: int = SAMPLE_RATE, channels: int = 1
) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.astype("<i2").tobytes())
    return path


def test_full_scale_tone_reaches_the_8bit_rails(tmp_path: Path) -> None:
    """A full-scale signal must quantise to the format's extremes, not near them.

    This is the load-bearing correctness check: it pins the normalisation
    (int16 full scale -> -1..1 -> -128..127). An off-by-one-scale bug here makes
    every waveform in the product quietly too short or clipped.
    """
    n = DEFAULT_SAMPLES_PER_PIXEL * 10
    # Alternating +full/-full so every bucket contains both extremes.
    samples = np.empty(n, dtype=np.int16)
    samples[0::2] = 32767
    samples[1::2] = -32768
    peaks = compute_peaks(_write_wav(tmp_path / "loud.wav", samples))

    assert peaks is not None
    assert peaks["bits"] == 8
    assert peaks["version"] == 1
    assert peaks["sample_rate"] == SAMPLE_RATE
    assert peaks["samples_per_pixel"] == DEFAULT_SAMPLES_PER_PIXEL
    assert peaks["length"] == 10
    assert len(peaks["data"]) == 20
    # data is min,max interleaved — mins on even indices, maxes on odd.
    assert set(peaks["data"][0::2]) == {-128}
    assert set(peaks["data"][1::2]) == {127}


def test_silence_is_flat_zero(tmp_path: Path) -> None:
    n = DEFAULT_SAMPLES_PER_PIXEL * 4
    peaks = compute_peaks(_write_wav(tmp_path / "silent.wav", np.zeros(n, dtype=np.int16)))
    assert peaks is not None
    assert peaks["data"] == [0] * 8


def test_envelope_tracks_a_loud_section(tmp_path: Path) -> None:
    """Quiet-loud-quiet must come back as quiet-loud-quiet, in the right bucket."""
    spp = DEFAULT_SAMPLES_PER_PIXEL
    samples = np.zeros(spp * 3, dtype=np.int16)
    samples[spp : spp * 2] = 16384  # half scale, middle bucket only
    peaks = compute_peaks(_write_wav(tmp_path / "burst.wav", samples))
    assert peaks is not None
    maxes = peaks["data"][1::2]
    assert maxes[0] == 0
    assert maxes[2] == 0
    assert maxes[1] == pytest.approx(64, abs=1)  # half of 127


def test_resolution_coarsens_instead_of_growing_without_bound() -> None:
    """A long source must cap its artifact size, not emit millions of pairs."""
    # Comfortably under the cap: keep full resolution.
    short = DEFAULT_SAMPLES_PER_PIXEL * 1000
    assert samples_per_pixel_for(short) == DEFAULT_SAMPLES_PER_PIXEL

    # Three hours at 16 kHz.
    long_samples = 3 * 3600 * SAMPLE_RATE
    spp = samples_per_pixel_for(long_samples)
    assert spp > DEFAULT_SAMPLES_PER_PIXEL
    assert long_samples // spp <= MAX_PAIRS
    # Must stay an integer multiple so a client can down-bucket exactly.
    assert spp % DEFAULT_SAMPLES_PER_PIXEL == 0


def test_zero_length_source_falls_back_to_the_default_resolution() -> None:
    assert samples_per_pixel_for(0) == DEFAULT_SAMPLES_PER_PIXEL
    assert samples_per_pixel_for(-1) == DEFAULT_SAMPLES_PER_PIXEL


def test_non_16bit_audio_is_refused_rather_than_misread(tmp_path: Path) -> None:
    """An 8-bit WAV read as int16 would produce garbage that LOOKS like a waveform.

    `extract_audio_wav` always writes pcm_s16le today, but this is the guard that
    keeps a future change to those ffmpeg flags from silently rendering noise over
    the creator's audio instead of failing to a flat track.
    """
    path = tmp_path / "eight_bit.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)  # 8-bit
        w.setframerate(SAMPLE_RATE)
        w.writeframes(bytes(DEFAULT_SAMPLES_PER_PIXEL * 4))
    assert compute_peaks(path) is None


def test_default_resolution_supports_issue_390_zoom() -> None:
    """>=28 pairs/s, because BBC data cannot be zoomed in past its resolution.

    Issue 390 zooms a ~40s clip across a ~1100px timeline (27.5 px/s); coarser
    data would render blocky at full zoom with no client-side recovery.
    """
    pairs_per_second = SAMPLE_RATE / DEFAULT_SAMPLES_PER_PIXEL
    assert pairs_per_second >= 28


def test_stereo_is_downmixed_to_one_channel(tmp_path: Path) -> None:
    spp = DEFAULT_SAMPLES_PER_PIXEL
    interleaved = np.zeros(spp * 2 * 2, dtype=np.int16)
    interleaved[0::2] = 20000  # left loud
    interleaved[1::2] = -20000  # right equally loud, opposite phase
    peaks = compute_peaks(_write_wav(tmp_path / "stereo.wav", interleaved, channels=2))
    assert peaks is not None
    assert peaks["length"] == 2
    # Averaging opposite phases cancels to ~0 — proves a real down-mix ran rather
    # than the raw interleaved buffer being bucketed as if it were mono.
    assert all(abs(v) <= 1 for v in peaks["data"])


@pytest.mark.parametrize(
    "make",
    [
        pytest.param(lambda p: p / "missing.wav", id="missing_file"),
        pytest.param(
            lambda p: _write_text(p / "notawav.wav", "definitely not a wav"), id="not_a_wav"
        ),
        pytest.param(
            lambda p: _write_wav(p / "empty.wav", np.zeros(0, dtype=np.int16)), id="zero_length"
        ),
        pytest.param(
            lambda p: _write_wav(p / "tiny.wav", np.zeros(16, dtype=np.int16)),
            id="shorter_than_one_bucket",
        ),
    ],
)
def test_never_raises_returns_none(tmp_path: Path, make) -> None:
    """A waveform is a navigation aid; it must never fail an ingest."""
    assert compute_peaks(make(tmp_path)) is None


def _write_text(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_written_json_is_compact_and_round_trips(tmp_path: Path) -> None:
    n = DEFAULT_SAMPLES_PER_PIXEL * 50
    peaks = compute_peaks(_write_wav(tmp_path / "a.wav", np.full(n, 1000, dtype=np.int16)))
    assert peaks is not None
    out = tmp_path / "a.json"
    assert write_peaks_json(peaks, out) is True

    raw = out.read_text(encoding="utf-8")
    assert " " not in raw  # compact separators — whitespace is ~1 byte per value
    assert json.loads(raw) == peaks


def test_write_returns_false_instead_of_raising(tmp_path: Path) -> None:
    assert write_peaks_json({"data": [1]}, tmp_path / "no" / "such" / "dir" / "x.json") is False


def test_a_22_minute_source_stays_a_reasonable_download(tmp_path: Path) -> None:
    """Size guard on the artifact the browser actually fetches."""
    spp = samples_per_pixel_for(22 * 60 * SAMPLE_RATE)
    pairs = (22 * 60 * SAMPLE_RATE) // spp
    # Worst case ~4 chars per value including the comma.
    approx_bytes = pairs * 2 * 4
    assert approx_bytes < 600_000
    assert pairs <= MAX_PAIRS
    assert math.isclose(SAMPLE_RATE / spp, 31.25)
