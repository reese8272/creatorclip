"""Audio waveform peak extraction (Issue 392).

Produces a min/max amplitude envelope for a source's audio, in the BBC
``audiowaveform`` JSON format, so the editor timelines can draw the creator's
ACTUAL audio instead of decoration.

WHY THIS EXISTS
---------------
The long-form "Source timeline" previously drew 48 bars at ``20 + ((i*37) % 60)%``
— a deterministic arithmetic pattern with no relationship to the audio — under a
label telling the creator it was their source. That asserts something false about
the user's own content on the surface whose entire job is helping them find
moments, which the honesty constraint in CLAUDE.md does not permit.

WHY NOT THE BBC BINARY
----------------------
We emit BBC ``audiowaveform``'s data format (the de-facto interchange standard,
readable by waveform-data.js / peaks.js) but do NOT install BBC's C++ tool: it is
not in the Debian repositories, so it would mean fetching a third-party ``.deb``
at image-build time — supply-chain surface and image weight — to produce bytes
that numpy already produces from a WAV we already have on disk.
See docs/DECISIONS.md 2026-08-03.

WHY NOT ffmpeg showwavespic
---------------------------
``ingestion.audio.generate_waveform_image`` renders a PNG. A raster can only be
stretched or squeezed; it cannot be re-bucketed, so it blurs when zoomed in and
aliases when zoomed out. Issue 390's timeline needs DATA at adaptive density.
"""

from __future__ import annotations

import json
import logging
import math
import wave
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# One min/max pair per this many source samples. At the 16 kHz mono WAV that
# `youtube.ingest.extract_audio_wav` always produces, 512 gives 31.25 pairs per
# second.
#
# The floor is set by zoom, not by looks: BBC's format cannot be zoomed in PAST
# the resolution it was generated at, and Issue 390 zooms a ~40s clip across a
# ~1100px timeline — 27.5 px/s. Anything coarser than ~28 pairs/s would render
# visibly blocky at full zoom with no way to recover detail client-side.
DEFAULT_SAMPLES_PER_PIXEL = 512

# Hard cap on the number of pairs in one artifact, so a three-hour podcast cannot
# produce a multi-megabyte download. Past this the resolution coarsens instead —
# which is safe precisely because `samples_per_pixel` is recorded IN the file;
# that is what the field is for. At the default resolution the cap first binds at
# ~32 minutes of audio.
MAX_PAIRS = 60_000

# 8-bit, per BBC's own recommendation: smaller, and peaks.js does not read 16-bit.
BITS = 8
_INT8_MIN, _INT8_MAX = -128, 127

# int16 PCM full scale. Dividing by this normalises to -1.0..1.0 before the 8-bit
# quantisation, so the envelope is independent of the source's bit depth.
_INT16_FULL_SCALE = 32768.0

# Samples per read. 1M int16 samples is ~2 MB resident, so even a three-hour
# source never materialises more than that — reading a 3h 16 kHz WAV whole would
# be ~345 MB inside a Celery worker.
_READ_BLOCK_SAMPLES = 1 << 20


def samples_per_pixel_for(total_samples: int, *, default: int = DEFAULT_SAMPLES_PER_PIXEL) -> int:
    """Resolution to use for a source of ``total_samples``.

    Returns ``default`` unless that would exceed ``MAX_PAIRS``, in which case it
    returns the smallest multiple of ``default`` that fits. Keeping it a multiple
    means a client can always down-bucket by an integer factor.
    """
    if total_samples <= 0:
        return default
    needed = math.ceil(total_samples / MAX_PAIRS)
    if needed <= default:
        return default
    return default * math.ceil(needed / default)


def _quantise(block: np.ndarray, samples_per_pixel: int) -> tuple[np.ndarray, np.ndarray]:
    """Per-bucket (min, max) of ``block``, as 8-bit values.

    ``block`` must be a whole number of buckets long; the caller carries the
    remainder across reads.
    """
    buckets = block.reshape(-1, samples_per_pixel)
    # float32, not float64: half the memory for a precision that 8-bit output
    # cannot represent anyway.
    scaled = buckets.astype(np.float32) / _INT16_FULL_SCALE
    lo = np.clip(np.round(scaled.min(axis=1) * -_INT8_MIN), _INT8_MIN, _INT8_MAX)
    hi = np.clip(np.round(scaled.max(axis=1) * _INT8_MAX), _INT8_MIN, _INT8_MAX)
    return lo.astype(np.int16), hi.astype(np.int16)


def compute_peaks(wav_path: str | Path) -> dict[str, Any] | None:
    """Build a BBC-format waveform-data object for a 16-bit PCM WAV.

    Returns ``None`` on any failure — an unreadable file, an unexpected codec, a
    zero-length track. NEVER raises: a waveform is a navigation aid, and no
    caller should fail a video ingest because one could not be made. This mirrors
    ``clip_engine.render.extract_poster_frame`` (Issue 387).
    """
    path = Path(wav_path)
    try:
        with wave.open(str(path), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            sample_rate = wav.getframerate()
            total_frames = wav.getnframes()

            if sample_width != 2:
                logger.warning(
                    "peaks_unsupported_sample_width path=%s width=%s", path.name, sample_width
                )
                return None
            if sample_rate <= 0 or total_frames <= 0 or channels <= 0:
                logger.warning("peaks_empty_audio path=%s frames=%s", path.name, total_frames)
                return None

            spp = samples_per_pixel_for(total_frames)

            data: list[int] = []
            carry = np.empty(0, dtype=np.int16)
            while True:
                raw = wav.readframes(_READ_BLOCK_SAMPLES)
                if not raw:
                    break
                block = np.frombuffer(raw, dtype="<i2")
                if channels > 1:
                    # Down-mix by averaging in int32 so the sum cannot overflow.
                    usable = (block.size // channels) * channels
                    block = (
                        block[:usable]
                        .reshape(-1, channels)
                        .astype(np.int32)
                        .mean(axis=1)
                        .astype(np.int16)
                    )
                block = np.concatenate((carry, block)) if carry.size else block
                whole = (block.size // spp) * spp
                if whole:
                    lo, hi = _quantise(block[:whole], spp)
                    # Interleave min,max per index — the format's layout.
                    pairs = np.empty(lo.size * 2, dtype=np.int16)
                    pairs[0::2] = lo
                    pairs[1::2] = hi
                    data.extend(pairs.tolist())
                carry = block[whole:].copy()

            # Deliberately drop a trailing partial bucket rather than emit one
            # computed over fewer samples: a short final bucket reads as a
            # spurious amplitude change at the very end of every waveform.
            if not data:
                logger.warning(
                    "peaks_too_short path=%s frames=%s spp=%s", path.name, total_frames, spp
                )
                return None

            return {
                "version": 1,
                "sample_rate": sample_rate,
                "samples_per_pixel": spp,
                "bits": BITS,
                "length": len(data) // 2,
                "data": data,
            }
    except (OSError, wave.Error, ValueError, MemoryError) as exc:
        logger.warning("peaks_failed path=%s err=%s", path.name, exc)
        return None


def write_peaks_json(peaks: dict[str, Any], out_path: str | Path) -> bool:
    """Serialise ``peaks`` to ``out_path``. Returns False instead of raising."""
    try:
        # separators: the default ", " / ": " adds ~1 byte per value, which on a
        # 60k-pair artifact is ~120 KB of pure whitespace.
        Path(out_path).write_text(json.dumps(peaks, separators=(",", ":")), encoding="utf-8")
        return True
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("peaks_write_failed path=%s err=%s", out_path, exc)
        return False
