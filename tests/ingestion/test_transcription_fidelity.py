"""Issue 481 — transcription timing fidelity against recorded real speech.

Two layers, both offline (fixtures under ``tests/fixtures/transcription/``,
provenance + derivation in its README):

PARSE FIDELITY — each normalizer's output timings must EXACTLY equal the
values in the provider response it consumed (float equality — normalization
must never touch a timestamp except AssemblyAI's documented ms→s division,
whose unit-scale class is pinned here).

PROVIDER FIDELITY — the default backend's normalized words must land within
±0.25 s of ``expected_words.json`` (ground truth: the LibriSpeech reference
transcript + a one-time recorded Deepgram nova-3 response, sanity-gated).

The live re-recording leg lives in ``test_transcription_live.py``
(``transcription_live`` marker, nightly only).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "transcription"


def _load(name: str) -> Any:
    return json.loads((_FIXTURES / name).read_text())


def _normalize_word_text(word: str) -> str:
    return re.sub(r"[^a-z']", "", word.lower())


def _flat_words(normalized: dict) -> list[dict]:
    return [w for seg in normalized["segments"] for w in seg["words"]]


# ── parse fidelity: Deepgram ─────────────────────────────────────────────────


def test_deepgram_normalizer_preserves_recorded_timings_exactly():
    """Every word start/end in the normalized output must be float-EQUAL to
    the recorded raw response's utterance words — the normalizer forwards
    provider timings untouched (a scale/offset bug here shifts every clip
    boundary while the shape tests stay green)."""
    from ingestion.transcribe import _normalize_deepgram

    raw = _load("deepgram_nova3_response.json")
    out = _normalize_deepgram(raw)

    raw_words = [w for u in raw["results"]["utterances"] for w in u["words"]]
    norm_words = _flat_words(out)
    assert len(norm_words) == len(raw_words) == 17
    for norm, rec in zip(norm_words, raw_words, strict=True):
        assert norm["start"] == rec["start"], (norm, rec)
        assert norm["end"] == rec["end"], (norm, rec)
        assert norm["word"] == rec["punctuated_word"]

    # Segment bounds mirror the recorded utterance bounds exactly.
    raw_utts = raw["results"]["utterances"]
    assert [(s["start"], s["end"]) for s in out["segments"]] == [
        (u["start"], u["end"]) for u in raw_utts
    ]
    assert "degraded" not in out  # utterances present → healthy path


# ── parse fidelity: AssemblyAI (ms-int → seconds unit-scale pin) ─────────────


def test_assemblyai_normalizer_divides_ms_ints_exactly():
    """The AssemblyAI SDK hands the normalizer attr objects with MILLISECOND
    INTEGER timings; the normalizer's ÷1000.0 is the only permitted transform.
    Float equality against ms/1000 pins the unit-scale class (a s-vs-ms mixup
    is a 1000× boundary shift). Fixture is schema-derived, not recorded — see
    the fixtures README."""
    from ingestion.transcribe import _normalize_assemblyai

    raw = _load("assemblyai_schema_derived_response.json")
    transcript = SimpleNamespace(
        text=raw["text"],
        words=[
            SimpleNamespace(
                text=w["text"], start=w["start"], end=w["end"], speaker=w["speaker"]
            )
            for w in raw["words"]
        ],
    )
    out = _normalize_assemblyai(transcript)
    norm_words = _flat_words(out)
    assert len(norm_words) == len(raw["words"]) == 17
    for norm, rec in zip(norm_words, raw["words"], strict=True):
        assert isinstance(rec["start"], int) and isinstance(rec["end"], int)
        assert norm["start"] == rec["start"] / 1000.0
        assert norm["end"] == rec["end"] / 1000.0


# ── provider fidelity: default backend vs ground truth ───────────────────────


def test_default_backend_words_match_ground_truth_within_tolerance():
    """Normalized words from the DEFAULT backend's recorded response must sit
    within ±0.25 s of expected_words.json — word-for-word against the
    LibriSpeech reference transcript, monotonic, inside the file duration."""
    from config import settings
    from ingestion.transcribe import _normalize_deepgram

    assert settings.TRANSCRIPTION_BACKEND == "deepgram", (
        "default backend changed — record a response fixture for the new "
        "backend and extend this test before flipping the default"
    )

    expected = _load("expected_words.json")
    tol = expected["tolerance_s"]
    out = _normalize_deepgram(_load("deepgram_nova3_response.json"))
    norm_words = _flat_words(out)

    assert len(norm_words) == len(expected["words"])
    prev_start = -1.0
    for norm, exp in zip(norm_words, expected["words"], strict=True):
        assert _normalize_word_text(norm["word"]) == exp["word"], (norm, exp)
        assert abs(norm["start"] - exp["start"]) <= tol, (norm, exp)
        assert abs(norm["end"] - exp["end"]) <= tol, (norm, exp)
        assert norm["start"] >= prev_start, "word starts must be monotonic"
        assert 0.0 <= norm["start"] < norm["end"] <= expected["duration_s"] + tol
        prev_start = norm["start"]


def test_fixture_wav_exists_and_matches_manifest():
    """The WAV the live nightly re-transcribes must stay checked in and match
    the manifest name — a renamed/dropped fixture should fail HERE, offline,
    not at 03:00 UTC in the nightly."""
    expected = _load("expected_words.json")
    wav = _FIXTURES / expected["audio"]
    assert wav.is_file() and wav.stat().st_size > 100_000
    assert expected["reference_transcript"].split()[0] == "FOR"
    assert len(expected["words"]) == len(expected["reference_transcript"].split())
