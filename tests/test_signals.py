"""
Unit tests for ingestion/audio.py and ingestion/signals.py.

Audio tests use a synthetic WAV generated in-process — no binary fixtures committed.
Signal timeline tests use simple mock objects for RetentionCurve rows.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from ingestion.audio import _merge_runs, extract_audio_events, generate_waveform_image
from ingestion.signals import _RETENTION_SPIKE_THRESHOLD, build_signal_timeline

# ── Synthetic WAV fixture ─────────────────────────────────────────────────────


@pytest.fixture
def wav_quiet(tmp_path):
    """3-second near-silence WAV (energy well below spike threshold)."""
    import scipy.io.wavfile as wf

    sr = 16000
    audio = (np.ones(sr * 3) * 50).astype(np.int16)  # very low amplitude
    path = tmp_path / "quiet.wav"
    wf.write(str(path), sr, audio)
    return path


@pytest.fixture
def wav_with_burst(tmp_path):
    """3-second WAV: silent first second, loud burst middle, silent last second."""
    import scipy.io.wavfile as wf

    sr = 16000
    quiet = np.zeros(sr, dtype=np.float32)
    t = np.linspace(0, 1, sr)
    loud = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    audio = np.concatenate([quiet, loud, quiet])
    audio_int16 = (audio / (np.abs(audio).max() + 1e-8) * 32767).astype(np.int16)
    path = tmp_path / "burst.wav"
    wf.write(str(path), sr, audio_int16)
    return path


# ── _merge_runs ───────────────────────────────────────────────────────────────


def test_merge_runs_single_run():
    times = np.array([0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
    mask = np.array([False, True, True, True, True, False, False])
    events = _merge_runs(times, mask, None, frame_duration=0.1, min_duration_s=0.2)
    assert len(events) == 1
    assert events[0]["start_s"] == pytest.approx(0.1)


def test_merge_runs_below_min_duration_excluded():
    times = np.array([0.0, 0.1, 0.2, 0.3])
    mask = np.array([False, True, False, False])  # only 1 frame → 0.1s
    events = _merge_runs(times, mask, None, frame_duration=0.1, min_duration_s=0.3)
    assert events == []


def test_merge_runs_with_values():
    times = np.array([0.0, 0.1, 0.2, 0.3, 0.4, 0.5])
    mask = np.array([False, True, True, True, False, False])
    values = np.array([0.0, 0.8, 0.9, 0.7, 0.0, 0.0])
    events = _merge_runs(times, mask, values, frame_duration=0.1, min_duration_s=0.2)
    assert len(events) == 1
    assert "value" in events[0]
    assert events[0]["value"] == pytest.approx((0.8 + 0.9 + 0.7) / 3)


def test_merge_runs_run_at_end():
    times = np.array([0.0, 0.1, 0.2, 0.3, 0.4, 0.5])
    mask = np.array([False, False, True, True, True, True])
    events = _merge_runs(times, mask, None, frame_duration=0.1, min_duration_s=0.2)
    assert len(events) == 1


# ── extract_audio_events ──────────────────────────────────────────────────────


def test_extract_audio_events_returns_expected_keys(wav_quiet):
    result = extract_audio_events(wav_quiet)
    assert "duration_s" in result
    assert "energy_spikes" in result
    assert "silences" in result
    assert "laughter" in result


def test_extract_audio_events_duration_accurate(wav_quiet):
    result = extract_audio_events(wav_quiet)
    assert result["duration_s"] == pytest.approx(3.0, abs=0.1)


def test_extract_audio_events_energy_spike_detected(wav_with_burst):
    result = extract_audio_events(wav_with_burst)
    assert len(result["energy_spikes"]) >= 1
    spike = result["energy_spikes"][0]
    # The loud burst is in the middle second, so start should be around 1s
    assert spike["start_s"] >= 0.8
    assert spike["start_s"] <= 1.5


def test_extract_audio_events_silence_detected(wav_with_burst):
    result = extract_audio_events(wav_with_burst)
    # The first and last seconds are near-silence
    assert len(result["silences"]) >= 1


def test_extract_audio_events_returns_lists(wav_quiet):
    result = extract_audio_events(wav_quiet)
    assert isinstance(result["energy_spikes"], list)
    assert isinstance(result["silences"], list)
    assert isinstance(result["laughter"], list)


# ── build_signal_timeline ─────────────────────────────────────────────────────


def _make_retention(timestamp_s, ratio, rrp):
    return SimpleNamespace(
        timestamp_s=timestamp_s,
        audience_watch_ratio=ratio,
        relative_retention_performance=rrp,
    )


def test_build_signal_timeline_structure():
    audio = {"duration_s": 10.0, "energy_spikes": [], "silences": [], "laughter": []}
    result = build_signal_timeline(audio, [])
    assert result["version"] == 1
    assert result["duration_s"] == 10.0
    assert isinstance(result["events"], list)


def test_build_signal_timeline_includes_audio_events():
    audio = {
        "duration_s": 10.0,
        "energy_spikes": [{"start_s": 5.0, "end_s": 6.0, "value": 0.8}],
        "silences": [{"start_s": 1.0, "end_s": 2.0}],
        "laughter": [],
    }
    result = build_signal_timeline(audio, [])
    types = {e["type"] for e in result["events"]}
    assert "energy_spike" in types
    assert "silence" in types


def test_build_signal_timeline_retention_spike_above_threshold():
    audio = {"duration_s": 10.0, "energy_spikes": [], "silences": [], "laughter": []}
    pt = _make_retention(timestamp_s=4.5, ratio=0.9, rrp=_RETENTION_SPIKE_THRESHOLD + 0.01)
    result = build_signal_timeline(audio, [pt])
    assert any(e["type"] == "retention_spike" for e in result["events"])


def test_build_signal_timeline_retention_spike_at_threshold_excluded():
    audio = {"duration_s": 10.0, "energy_spikes": [], "silences": [], "laughter": []}
    pt = _make_retention(timestamp_s=4.5, ratio=0.9, rrp=_RETENTION_SPIKE_THRESHOLD)
    result = build_signal_timeline(audio, [pt])
    assert not any(e["type"] == "retention_spike" for e in result["events"])


def test_build_signal_timeline_events_sorted():
    audio = {
        "duration_s": 20.0,
        "energy_spikes": [{"start_s": 10.0, "end_s": 11.0, "value": 0.9}],
        "silences": [{"start_s": 2.0, "end_s": 3.0}],
        "laughter": [{"start_s": 6.0, "end_s": 7.0, "value": 0.5}],
    }
    pt = _make_retention(timestamp_s=15.0, ratio=0.85, rrp=1.5)
    result = build_signal_timeline(audio, [pt])
    starts = [e["start_s"] for e in result["events"]]
    assert starts == sorted(starts)


# ── extract_audio_events edge cases (Issue 334) ───────────────────────────────


@pytest.fixture
def wav_all_zeros(tmp_path):
    """True digital-silence WAV: every sample is exactly zero."""
    import scipy.io.wavfile as wf

    sr = 16000
    audio = np.zeros(sr * 3, dtype=np.int16)  # 3s of pure zeros
    path = tmp_path / "zeros.wav"
    wf.write(str(path), sr, audio)
    return path


@pytest.fixture
def wav_single_sample(tmp_path):
    """Pathological WAV with exactly one sample — exercises short-array guard."""
    import scipy.io.wavfile as wf

    sr = 16000
    audio = np.array([1000], dtype=np.int16)
    path = tmp_path / "single.wav"
    wf.write(str(path), sr, audio)
    return path


def test_extract_audio_events_all_zero_wav_no_energy_no_laughter(wav_all_zeros):
    """All-zero WAV exercises the rms.max()+1e-8 divide-guard.

    Expectation: no energy_spikes and no laughter events, since every normalized
    RMS frame is 0.0 (below both _ENERGY_THRESHOLD and _LAUGHTER_ENERGY_MIN).
    """
    result = extract_audio_events(wav_all_zeros)
    assert result["energy_spikes"] == [], "spurious energy spike from all-zero audio"
    assert result["laughter"] == [], "spurious laughter event from all-zero audio"
    # Structural keys must still be present.
    assert "duration_s" in result
    assert "silences" in result


def test_extract_audio_events_single_sample_no_crash(wav_single_sample):
    """Single-sample WAV must not raise — returns empty event dict cleanly."""
    result = extract_audio_events(wav_single_sample)
    assert isinstance(result, dict)
    assert result["energy_spikes"] == []
    assert result["laughter"] == []
    assert result["silences"] == []


def test_extract_audio_events_over_cap_truncates_and_warns(wav_with_burst, monkeypatch, caplog):
    """Audio longer than AUDIO_ANALYSIS_MAX_DURATION_S is truncated, not rejected.

    Use the existing 3-second burst WAV; set the cap to 1 second so the 3s file
    exceeds it.  The returned duration must be ≤ the cap.
    """
    import logging

    monkeypatch.setattr("config.settings.AUDIO_ANALYSIS_MAX_DURATION_S", 1)
    with caplog.at_level(logging.WARNING, logger="ingestion.audio"):
        result = extract_audio_events(wav_with_burst)
    assert result["duration_s"] <= 1.1  # small tolerance for librosa rounding
    assert any("truncat" in r.message.lower() for r in caplog.records), (
        "expected a truncation WARNING log when audio exceeds the cap"
    )


# ── generate_waveform_image: timeout scales with duration (Issue 334) ──────────


def test_generate_waveform_image_timeout_scales_with_duration(tmp_path, monkeypatch):
    """Timeout passed to subprocess.run must be larger for long-duration audio."""
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr("config.settings.WAVEFORM_TIMEOUT_S", 60)

    audio_path = tmp_path / "audio.wav"
    audio_path.touch()
    out = tmp_path / "wave.png"

    captured: dict = {}

    def fake_run(cmd, **kwargs):  # type: ignore[override]
        captured["timeout"] = kwargs.get("timeout")
        return MagicMock(returncode=0, stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        # Short audio (no duration_s) — should use base timeout.
        generate_waveform_image(audio_path, out)
        base_timeout = captured["timeout"]

        # Long audio — should produce a larger timeout.
        generate_waveform_image(audio_path, out, duration_s=7200.0)
        long_timeout = captured["timeout"]

    assert long_timeout > base_timeout, (
        f"timeout did not scale with duration: base={base_timeout}, long={long_timeout}"
    )


def test_generate_waveform_image_no_ffmpeg_raises(tmp_path, monkeypatch):
    """RuntimeError when ffmpeg is absent (existing contract, keep green)."""
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="ffmpeg not found"):
        generate_waveform_image(tmp_path / "a.wav", tmp_path / "out.png")
