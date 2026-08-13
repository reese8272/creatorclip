"""Issue 481 — flag-gated LIVE Deepgram timing-fidelity test.

Marked ``transcription_live`` and excluded from the default lane (pytest.ini
addopts). Runs ONLY with ``RUN_TRANSCRIPTION_LIVE=1`` + a real
``DEEPGRAM_API_KEY`` — executed nightly by
``.github/workflows/llm-e2e-nightly.yml`` (dedicated ASR step, one ~5 s
transcription per night).

This is the drift alarm the offline fixture cannot be: if Deepgram's nova-3
timing behavior shifts (model rev, API change), the recorded fixture stays
green while every real upload silently moves — this leg re-transcribes the
SAME WAV live and holds it to the same ±0.25 s ground truth.

Gating pattern mirrors tests/test_llm_live.py (Issue 319).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "transcription"

_LIVE = os.environ.get("RUN_TRANSCRIPTION_LIVE") == "1" and bool(
    os.environ.get("DEEPGRAM_API_KEY")
)

_skip_unless_live = pytest.mark.skipif(
    not _LIVE,
    reason="Set RUN_TRANSCRIPTION_LIVE=1 and DEEPGRAM_API_KEY to run live ASR tests.",
)

# ── Always-running guard (no mark — MUST run in the default lane) ────────────


def test_transcription_live_mark_excluded_from_default_lane() -> None:
    """If someone drops 'not transcription_live' from pytest.ini addopts, the
    default unit lane starts making a real Deepgram call on every run — this
    always-running guard catches that immediately."""
    import configparser

    parser = configparser.ConfigParser()
    parser.read(Path(__file__).resolve().parents[2] / "pytest.ini")
    addopts = parser.get("pytest", "addopts", fallback="")
    assert "not transcription_live" in addopts, (
        "pytest.ini addopts must deselect transcription_live tests — the "
        "nightly workflow is the only place they run"
    )


# ── Live test ────────────────────────────────────────────────────────────────


@pytest.mark.transcription_live
@_skip_unless_live
def test_live_deepgram_word_timings_within_tolerance() -> None:
    """Re-transcribe the checked-in LibriSpeech WAV against LIVE Deepgram via
    the production request path and hold every word to expected_words.json
    (±0.25 s). Word sequence must equal the LibriSpeech reference transcript."""
    from ingestion.transcribe import _transcribe_deepgram

    expected = json.loads((_FIXTURES / "expected_words.json").read_text())
    tol = expected["tolerance_s"]
    wav = _FIXTURES / expected["audio"]
    assert wav.is_file(), f"fixture WAV missing: {wav}"

    result = _transcribe_deepgram(str(wav))
    words = [w for seg in result["segments"] for w in seg["words"]]

    got = [re.sub(r"[^a-z']", "", w["word"].lower()) for w in words]
    want = [w["word"] for w in expected["words"]]
    assert got == want, f"live word sequence diverged from reference:\n{got}\n{want}"

    for norm, exp in zip(words, expected["words"], strict=True):
        assert abs(norm["start"] - exp["start"]) <= tol, (
            f"live start drifted beyond ±{tol}s: {norm} vs {exp}"
        )
        assert abs(norm["end"] - exp["end"]) <= tol, (
            f"live end drifted beyond ±{tol}s: {norm} vs {exp}"
        )
