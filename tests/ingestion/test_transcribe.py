"""Unit tests for ingestion/transcribe.py.

Issue 251 — GDPR / Deepgram MIP opt-out:
  Creator audio must NOT be enrolled in the Deepgram Model Improvement Partnership
  Program. The code passes mip_opt_out=True via the addons dict to transcribe_file()
  because deepgram-sdk v3 does not accept it as a PrerecordedOptions kwarg
  (SDK issue #474 — TypeError).

Also tests: docs/SUBPROCESSORS.md existence (Art. 30 record presence gate).
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch  # noqa: E402

import pytest


# ── regression guard: the DEFAULT backend's SDK must actually be installed ─────
# Root cause of a prod outage: deepgram-sdk was commented out of requirements.txt
# while TRANSCRIPTION_BACKEND defaulted to "deepgram". Ingest succeeded, then
# `from deepgram import …` raised ImportError on EVERY upload — invisible to the
# mocked unit tests. This guard imports the configured default backend's SDK for
# real, so a missing hard dependency fails CI instead of prod.
def test_default_transcription_backend_sdk_is_installed():
    from config import settings

    sdk_module = {"deepgram": "deepgram", "assemblyai": "assemblyai"}.get(
        settings.TRANSCRIPTION_BACKEND
    )
    if sdk_module is None:
        pytest.skip(
            f"backend {settings.TRANSCRIPTION_BACKEND!r} ships no pip SDK "
            "(e.g. whisperx self-host) — nothing to import-check"
        )
    importlib.import_module(sdk_module)  # raises ImportError if the SDK isn't installed


def test_deepgram_prerecorded_options_use_valid_sdk_kwargs():
    """Build the REAL PrerecordedOptions to catch an invalid option kwarg.

    Regression: `words=True` is not a valid Deepgram request param and raised
    TypeError('unexpected keyword argument words') on every prod upload. The
    mocked transcribe tests below can't see it — they replace PrerecordedOptions
    with a MagicMock that swallows any kwargs. This builds it for real.

    Extended for Issue 418: `diarize` must ALSO be a real SDK 3.7.7 field —
    the same outage class would otherwise recur on every diarized upload."""
    pytest.importorskip("deepgram")
    from config import settings
    from ingestion.transcribe import _deepgram_prerecorded_options

    opts = _deepgram_prerecorded_options()  # TypeErrors here if a kwarg is invalid
    assert opts.model == "nova-3"
    assert opts.smart_format is True
    assert opts.utterances is True
    assert opts.diarize is settings.TRANSCRIPTION_DIARIZE_ENABLED


def test_deepgram_diarize_respects_kill_switch(monkeypatch):
    """TRANSCRIPTION_DIARIZE_ENABLED=False must be forwarded as diarize=False."""
    pytest.importorskip("deepgram")
    from config import settings
    from ingestion.transcribe import _deepgram_prerecorded_options

    monkeypatch.setattr(settings, "TRANSCRIPTION_DIARIZE_ENABLED", False)
    assert _deepgram_prerecorded_options().diarize is False


# ── mip_opt_out enforcement ───────────────────────────────────────────────────


def _make_deepgram_mocks() -> tuple[MagicMock, MagicMock, MagicMock]:
    """Build the fake Deepgram client + PrerecordedOptions + file stat mocks.

    We mock the `deepgram` module and the client singleton so these MIP-opt-out
    assertions don't make a network call. (deepgram-sdk IS a real installed
    dependency now — see test_default_transcription_backend_sdk_is_installed —
    but mocking keeps these unit tests hermetic and offline.)
    """
    # Minimal transcribe_file response structure that _normalize_deepgram accepts.
    fake_response = MagicMock()
    fake_response.to_dict.return_value = {
        "results": {"utterances": [], "channels": [{"alternatives": [{"words": []}]}]}
    }

    fake_rest_v1 = MagicMock()
    fake_rest_v1.transcribe_file.return_value = fake_response

    fake_client = MagicMock()
    fake_client.listen.rest.v.return_value = fake_rest_v1

    fake_stat = MagicMock()
    fake_stat.st_size = 1024 * 1024  # 1 MB — under any sane cap

    return fake_client, fake_rest_v1, fake_stat


def test_deepgram_passes_mip_opt_out_in_addons():
    """transcribe_file must be called with addons dict containing mip_opt_out=True
    as its third positional argument (deepgram-sdk v3 pattern per SDK issue #474)."""
    fake_client, fake_rest_v1, fake_stat = _make_deepgram_mocks()
    captured: dict = {}

    original_side_effect = fake_rest_v1.transcribe_file.return_value

    def fake_transcribe_file(source, opts, addons, **kwargs):
        captured["addons"] = addons
        return original_side_effect

    fake_rest_v1.transcribe_file.side_effect = fake_transcribe_file

    # deepgram-sdk is not installed in dev venv — mock the module import.
    fake_deepgram_module = MagicMock()
    fake_deepgram_module.PrerecordedOptions = MagicMock(return_value=MagicMock())

    with (
        patch("ingestion.transcribe.settings") as mock_settings,
        patch("ingestion.transcribe._deepgram_client", return_value=fake_client),
        patch("pathlib.Path.stat", return_value=fake_stat),
        patch("builtins.open", MagicMock()),
        patch.dict("sys.modules", {"deepgram": fake_deepgram_module}),
    ):
        mock_settings.TRANSCRIPTION_BACKEND = "deepgram"
        mock_settings.TRANSCRIPTION_MAX_MB = 500
        mock_settings.TRANSCRIPTION_HTTP_TIMEOUT_S = 30

        from ingestion.transcribe import _transcribe_deepgram

        _transcribe_deepgram("/fake/audio.wav")

    assert "addons" in captured, "addons dict was not captured — transcribe_file not called"
    assert captured["addons"].get("mip_opt_out") is True, (
        f"mip_opt_out must be True in addons; got: {captured['addons']}"
    )


def test_deepgram_mip_opt_out_is_positional_not_kwarg():
    """addons must be passed as the third positional argument, not a keyword argument.

    deepgram-sdk v3 requires this exact call shape:
        transcribe_file(source, opts, addons, timeout=...)
    Passing addons as a keyword would silently be ignored by some SDK versions.
    """
    fake_client, fake_rest_v1, fake_stat = _make_deepgram_mocks()
    captured_args: list = []

    original_return = fake_rest_v1.transcribe_file.return_value

    def fake_transcribe_file(*args, **kwargs):
        captured_args.extend(args)
        return original_return

    fake_rest_v1.transcribe_file.side_effect = fake_transcribe_file

    fake_deepgram_module = MagicMock()
    fake_deepgram_module.PrerecordedOptions = MagicMock(return_value=MagicMock())

    with (
        patch("ingestion.transcribe.settings") as mock_settings,
        patch("ingestion.transcribe._deepgram_client", return_value=fake_client),
        patch("pathlib.Path.stat", return_value=fake_stat),
        patch("builtins.open", MagicMock()),
        patch.dict("sys.modules", {"deepgram": fake_deepgram_module}),
    ):
        mock_settings.TRANSCRIPTION_BACKEND = "deepgram"
        mock_settings.TRANSCRIPTION_MAX_MB = 500
        mock_settings.TRANSCRIPTION_HTTP_TIMEOUT_S = 30

        from ingestion.transcribe import _transcribe_deepgram

        _transcribe_deepgram("/fake/audio.wav")

    # Three positional args expected: source, opts, addons
    assert len(captured_args) == 3, (
        f"Expected 3 positional args (source, opts, addons); got {len(captured_args)}"
    )
    addons_positional = captured_args[2]
    assert isinstance(addons_positional, dict), (
        f"Third positional arg must be the addons dict; got {type(addons_positional)}"
    )
    assert addons_positional.get("mip_opt_out") is True


# ── AssemblyAI error-status → raise (Issue 352 Batch E) ──────────────────────


def _make_fake_aai_module(transcript: MagicMock) -> MagicMock:
    """Fake `assemblyai` module: the SDK is a non-default backend and is not
    installed in the unit lane, so mock it at the sys.modules boundary."""
    fake_aai = MagicMock()
    fake_aai.TranscriptStatus.error = "error"
    fake_aai.Transcriber.return_value.transcribe.return_value = transcript
    return fake_aai


def test_assemblyai_error_status_raises_for_refund():
    """An error-status transcript must RAISE, not normalize to empty segments.

    The AssemblyAI SDK does NOT raise on a failed job — `.transcribe()` returns a
    transcript with status == TranscriptStatus.error and words=None. Before the
    fix that normalized to a charged empty-segments "success" and the worker's
    terminal-failure refund never fired. (Issue 352 Batch E)
    """
    transcript = MagicMock()
    transcript.status = "error"
    transcript.error = "Download error: unable to fetch audio"
    transcript.words = None
    transcript.text = None

    with (
        patch.dict("sys.modules", {"assemblyai": _make_fake_aai_module(transcript)}),
        patch("ingestion.transcribe.settings") as mock_settings,
    ):
        mock_settings.ASSEMBLYAI_API_KEY = "fake-key"
        mock_settings.TRANSCRIPTION_HTTP_TIMEOUT_S = 30
        from ingestion.transcribe import _transcribe_assemblyai

        with pytest.raises(RuntimeError, match="unable to fetch audio"):
            _transcribe_assemblyai("/fake/audio.wav")


def test_assemblyai_completed_status_normalizes():
    """Happy path: a completed transcript passes the status gate and normalizes
    (ms → s) exactly as before the error-status check was added."""
    word = MagicMock()
    word.text = "hello"
    word.start = 1000
    word.end = 1500
    transcript = MagicMock()
    transcript.status = "completed"
    transcript.words = [word]
    transcript.text = "hello"

    with (
        patch.dict("sys.modules", {"assemblyai": _make_fake_aai_module(transcript)}),
        patch("ingestion.transcribe.settings") as mock_settings,
    ):
        mock_settings.ASSEMBLYAI_API_KEY = "fake-key"
        mock_settings.TRANSCRIPTION_HTTP_TIMEOUT_S = 30
        from ingestion.transcribe import _transcribe_assemblyai

        result = _transcribe_assemblyai("/fake/audio.wav")

    assert result["source"] == "assemblyai"
    assert result["segments"] == [
        {
            "start": 1.0,
            "end": 1.5,
            "text": "hello",
            "words": [{"word": "hello", "start": 1.0, "end": 1.5}],
        }
    ]


# ── Art. 30 record presence gate ─────────────────────────────────────────────


def test_subprocessors_md_exists():
    """docs/SUBPROCESSORS.md must exist as the Art. 30 record / public sub-processor list
    (Issue 251 — GDPR Art. 28 + Art. 30)."""
    repo_root = Path(__file__).resolve().parents[2]
    subprocessors = repo_root / "docs" / "SUBPROCESSORS.md"
    assert subprocessors.exists(), "docs/SUBPROCESSORS.md is missing — Art. 30 record required"


def test_subprocessors_md_names_required_vendors():
    """The sub-processor list must name all six required vendors."""
    repo_root = Path(__file__).resolve().parents[2]
    content = (repo_root / "docs" / "SUBPROCESSORS.md").read_text()
    required_vendors = ["Anthropic", "Voyage AI", "Deepgram", "Cloudflare", "Stripe", "Google"]
    for vendor in required_vendors:
        assert vendor in content, f"SUBPROCESSORS.md is missing vendor: {vendor}"


# ── Issue 418: speaker diarization — additive normalization ──────────────────


def _diarized_deepgram_raw() -> dict:
    """A trimmed diarized Deepgram response (utterances path, 2 speakers)."""
    return {
        "results": {
            "utterances": [
                {
                    "start": 0.0,
                    "end": 1.2,
                    "transcript": "Hi there.",
                    "speaker": 0,
                    "words": [
                        {
                            "word": "hi",
                            "punctuated_word": "Hi",
                            "start": 0.0,
                            "end": 0.5,
                            "speaker": 0,
                            "speaker_confidence": 0.91,
                        },
                        {
                            "word": "there",
                            "punctuated_word": "there.",
                            "start": 0.5,
                            "end": 1.2,
                            "speaker": 0,
                            "speaker_confidence": 0.88,
                        },
                    ],
                },
                {
                    "start": 1.4,
                    "end": 2.0,
                    "transcript": "Hello.",
                    "speaker": 1,
                    "words": [
                        {
                            "word": "hello",
                            "punctuated_word": "Hello.",
                            "start": 1.4,
                            "end": 2.0,
                            "speaker": 1,
                            "speaker_confidence": 0.97,
                        },
                    ],
                },
            ]
        }
    }


def test_normalize_deepgram_diarized_adds_speaker_fields():
    from ingestion.transcribe import _normalize_deepgram

    out = _normalize_deepgram(_diarized_deepgram_raw())
    segs = out["segments"]
    assert [s["speaker"] for s in segs] == [0, 1]
    first_word = segs[0]["words"][0]
    assert first_word["speaker"] == 0
    assert first_word["speaker_confidence"] == 0.91
    assert segs[1]["words"][0]["speaker"] == 1


def test_normalize_deepgram_undiarized_omits_speaker_keys():
    """No diarization in the response → the pre-418 shape EXACTLY (keys omitted,
    never null-filled) — old-transcript compatibility is the load-bearing rule."""
    from ingestion.transcribe import _normalize_deepgram

    raw = _diarized_deepgram_raw()
    for u in raw["results"]["utterances"]:
        u.pop("speaker")
        for w in u["words"]:
            w.pop("speaker")
            w.pop("speaker_confidence")
    out = _normalize_deepgram(raw)
    for seg in out["segments"]:
        assert "speaker" not in seg
        for w in seg["words"]:
            assert "speaker" not in w
            assert "speaker_confidence" not in w


def test_normalize_deepgram_tolerates_partial_speaker_fields():
    """A word with speaker but no confidence (or vice versa) must not KeyError."""
    from ingestion.transcribe import _normalize_deepgram

    raw = _diarized_deepgram_raw()
    words = raw["results"]["utterances"][0]["words"]
    words[0].pop("speaker_confidence")
    words[1].pop("speaker")
    out = _normalize_deepgram(raw)
    w0, w1 = out["segments"][0]["words"]
    assert w0["speaker"] == 0 and "speaker_confidence" not in w0
    assert "speaker" not in w1 and w1["speaker_confidence"] == 0.88


def test_normalize_deepgram_channels_path_carries_word_speakers():
    """The no-utterances fallback path must also normalize per-word speakers."""
    from ingestion.transcribe import _normalize_deepgram

    raw = {
        "results": {
            "channels": [
                {
                    "alternatives": [
                        {
                            "transcript": "hi hello",
                            "words": [
                                {"word": "hi", "start": 0.0, "end": 0.5, "speaker": 0},
                                {"word": "hello", "start": 0.5, "end": 1.0, "speaker": 1},
                            ],
                        }
                    ]
                }
            ]
        }
    }
    out = _normalize_deepgram(raw)
    seg = out["segments"][0]
    # Mixed speakers in the single synthetic segment → NO segment-level key.
    assert "speaker" not in seg
    assert [w["speaker"] for w in seg["words"]] == [0, 1]


# ── Issue 481: no-utterances fallback must WARN and set the degraded flag ────
#
# The fallback branch silently collapses the whole video into ONE synthetic
# segment (Issue 456's collapse input). Deepgram-specific by design: the
# AssemblyAI normalizer is structurally single-segment by construction, so a
# single segment there is normal, not degradation.


def _no_utterances_raw() -> dict:
    return {
        "results": {
            "channels": [
                {
                    "alternatives": [
                        {
                            "transcript": "hi there friend",
                            "words": [
                                {"word": "hi", "start": 0.0, "end": 0.5},
                                {"word": "there", "start": 0.5, "end": 1.0},
                                {"word": "friend", "start": 1.2, "end": 1.8},
                            ],
                        }
                    ]
                }
            ]
        }
    }


def test_normalize_deepgram_no_utterances_warns_and_sets_degraded(caplog):
    """The one-segment fallback must be LOUD: a WARNING log plus an additive
    ``degraded: no_utterances`` key, so silent degradation becomes visible to
    the worker (SSE step) and the transcript endpoint."""
    from ingestion.transcribe import _normalize_deepgram

    with caplog.at_level(logging.WARNING, logger="ingestion.transcribe"):
        out = _normalize_deepgram(_no_utterances_raw())

    assert out["degraded"] == "no_utterances"
    assert len(out["segments"]) == 1  # the collapse itself is unchanged behavior
    assert any(
        "no utterances" in r.message.lower()
        for r in caplog.records
        if r.levelno == logging.WARNING
    ), "the fallback must log a WARNING naming the no-utterances degradation"


def test_normalize_deepgram_utterances_path_omits_degraded_key():
    """Old-shape compatibility: the healthy utterances path must produce
    transcripts WITHOUT the key (additive field, omitted when absent — the
    same contract as the Issue-418 speaker fields)."""
    from ingestion.transcribe import _normalize_deepgram

    out = _normalize_deepgram(_diarized_deepgram_raw())
    assert "degraded" not in out


def test_normalize_assemblyai_never_sets_degraded():
    """AssemblyAI is single-segment BY CONSTRUCTION — its normalizer must not
    flag its normal shape as degraded."""
    from ingestion.transcribe import _normalize_assemblyai

    word = MagicMock()
    word.text, word.start, word.end, word.speaker = "hello", 1000, 1500, None
    transcript = MagicMock()
    transcript.words = [word]
    transcript.text = "hello"
    out = _normalize_assemblyai(transcript)
    assert "degraded" not in out


def test_assemblyai_letter_labels_map_to_ints():
    """AssemblyAI labels speakers "A"/"B"/… — normalized to 0-based ints."""
    from ingestion.transcribe import _normalize_assemblyai

    def _word(text: str, start: int, end: int, speaker: str | None) -> MagicMock:
        w = MagicMock()
        w.text, w.start, w.end, w.speaker = text, start, end, speaker
        return w

    transcript = MagicMock()
    transcript.text = "hi hello again"
    transcript.words = [
        _word("hi", 0, 500, "A"),
        _word("hello", 500, 1000, "B"),
        _word("again", 1000, 1500, None),  # missing label → key omitted
    ]
    out = _normalize_assemblyai(transcript)
    words = out["segments"][0]["words"]
    assert words[0]["speaker"] == 0
    assert words[1]["speaker"] == 1
    assert "speaker" not in words[2]


def test_assemblyai_speaker_int_mapping_edges():
    from ingestion.transcribe import _assemblyai_speaker_int

    assert _assemblyai_speaker_int("A") == 0
    assert _assemblyai_speaker_int("Z") == 25
    assert _assemblyai_speaker_int(3) == 3  # already an int passes through
    assert _assemblyai_speaker_int(None) is None
    assert _assemblyai_speaker_int("AA") is None  # unknown shape → omitted
    assert _assemblyai_speaker_int("a") is None


def test_assemblyai_transcribe_passes_speaker_labels_config():
    """speaker_labels must ride the same kill-switch as Deepgram's diarize."""
    transcript = MagicMock()
    transcript.status = "completed"
    transcript.words = []
    transcript.text = ""
    fake_aai = _make_fake_aai_module(transcript)

    with (
        patch.dict("sys.modules", {"assemblyai": fake_aai}),
        patch("ingestion.transcribe.settings") as mock_settings,
    ):
        mock_settings.ASSEMBLYAI_API_KEY = "fake-key"
        mock_settings.TRANSCRIPTION_HTTP_TIMEOUT_S = 30
        mock_settings.TRANSCRIPTION_DIARIZE_ENABLED = True
        from ingestion.transcribe import _transcribe_assemblyai

        _transcribe_assemblyai("/fake/audio.wav")

    fake_aai.TranscriptionConfig.assert_called_once_with(speaker_labels=True)
    call = fake_aai.Transcriber.return_value.transcribe.call_args
    assert call.args[1] is fake_aai.TranscriptionConfig.return_value


# ── Issue 418: consumers ignore speaker fields (regression guards) ───────────
#
# The speaker fields are additive; captions and filler MUST produce
# byte-identical output whether or not a transcript carries them.


def _segments_with_and_without_speakers() -> tuple[list[dict], list[dict]]:
    plain = [
        {
            "start": 0.0,
            "end": 3.0,
            "text": "well um this is a test sentence",
            "words": [
                {"word": "well", "start": 0.0, "end": 0.3},
                {"word": "um", "start": 0.4, "end": 0.6},
                {"word": "this", "start": 0.7, "end": 0.9},
                {"word": "is", "start": 1.0, "end": 1.1},
                {"word": "a", "start": 1.2, "end": 1.3},
                {"word": "test", "start": 1.4, "end": 1.8},
                {"word": "sentence", "start": 2.6, "end": 3.0},
            ],
        }
    ]
    import copy

    speaker_bearing = copy.deepcopy(plain)
    speaker_bearing[0]["speaker"] = 0
    for i, w in enumerate(speaker_bearing[0]["words"]):
        w["speaker"] = i % 2
        w["speaker_confidence"] = 0.9
    return plain, speaker_bearing


def test_captions_output_identical_on_speaker_bearing_transcript(tmp_path):
    """build_ass_subtitles (karaoke styles) must ignore speaker fields."""
    from clip_engine.captions import VALID_STYLES, build_ass_subtitles

    plain, speaker_bearing = _segments_with_and_without_speakers()
    for style in sorted(VALID_STYLES):
        out_a = tmp_path / f"plain_{style}.ass"
        out_b = tmp_path / f"speaker_{style}.ass"
        build_ass_subtitles(plain, style, 0.0, 3.0, out_a, 1080, 1920)
        build_ass_subtitles(speaker_bearing, style, 0.0, 3.0, out_b, 1080, 1920)
        assert out_a.exists() and out_b.exists(), f"style {style} produced no file"
        assert out_a.read_bytes() == out_b.read_bytes(), (
            f"caption style {style} output changed when speaker fields were present"
        )


def test_filler_cuts_identical_on_speaker_bearing_transcript():
    """detect_cut_segments must ignore speaker fields."""
    from clip_engine.filler import detect_cut_segments

    plain, speaker_bearing = _segments_with_and_without_speakers()
    cuts_plain = detect_cut_segments(plain[0]["words"], 0.0, 3.0)
    cuts_speaker = detect_cut_segments(speaker_bearing[0]["words"], 0.0, 3.0)
    assert [(c.start_s, c.end_s, c.reason) for c in cuts_plain] == [
        (c.start_s, c.end_s, c.reason) for c in cuts_speaker
    ]
    assert cuts_plain, "fixture must actually produce at least one cut (um + silence)"
