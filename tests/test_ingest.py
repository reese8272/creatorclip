"""
Unit tests for ingestion/transcribe.py and youtube/ingest.py.

Pure function tests — no DB, no network, no ffmpeg.
Transcription backend calls are patched at the SDK boundary.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from ingestion.transcribe import (
    _normalize_assemblyai,
    _normalize_deepgram,
    _normalize_whisperx,
    transcribe_audio,
)
from youtube.ingest import download_via_ytdlp, extract_audio_wav

# ── _normalize_deepgram ───────────────────────────────────────────────────────


def test_normalize_deepgram_utterances_path():
    raw = {
        "results": {
            "utterances": [
                {
                    "start": 0.0,
                    "end": 2.5,
                    "transcript": "Hello world",
                    "words": [
                        {"punctuated_word": "Hello", "start": 0.0, "end": 0.5},
                        {"punctuated_word": "world", "start": 0.6, "end": 1.2},
                    ],
                }
            ]
        }
    }
    result = _normalize_deepgram(raw)
    assert result["source"] == "deepgram"
    assert len(result["segments"]) == 1
    seg = result["segments"][0]
    assert seg["start"] == 0.0
    assert seg["end"] == 2.5
    assert seg["text"] == "Hello world"
    assert len(seg["words"]) == 2
    assert seg["words"][0]["word"] == "Hello"


def test_normalize_deepgram_fallback_no_utterances():
    raw = {
        "results": {
            "utterances": [],
            "channels": [
                {
                    "alternatives": [
                        {
                            "transcript": "Hi there",
                            "words": [
                                {"word": "Hi", "punctuated_word": "Hi,", "start": 0.1, "end": 0.4},
                                {
                                    "word": "there",
                                    "punctuated_word": "there",
                                    "start": 0.5,
                                    "end": 0.9,
                                },
                            ],
                        }
                    ]
                }
            ],
        }
    }
    result = _normalize_deepgram(raw)
    assert result["source"] == "deepgram"
    assert len(result["segments"]) == 1
    assert result["segments"][0]["words"][0]["word"] == "Hi,"


def test_normalize_deepgram_empty_response():
    result = _normalize_deepgram({})
    assert result["source"] == "deepgram"
    assert result["segments"] == []


# ── _normalize_whisperx ───────────────────────────────────────────────────────


def test_normalize_whisperx_happy_path():
    raw = {
        "segments": [
            {
                "start": 0.0,
                "end": 3.0,
                "text": " Test sentence",
                "words": [
                    {"word": "Test", "start": 0.1, "end": 0.5},
                    {"word": "sentence", "start": 0.6, "end": 1.2},
                ],
            }
        ]
    }
    result = _normalize_whisperx(raw)
    assert result["source"] == "whisperx"
    assert result["segments"][0]["text"] == " Test sentence"
    assert result["segments"][0]["words"][1]["word"] == "sentence"


def test_normalize_whisperx_empty():
    result = _normalize_whisperx({"segments": []})
    assert result["segments"] == []


# ── transcribe_audio routing ──────────────────────────────────────────────────


def test_transcribe_audio_routes_to_deepgram(tmp_path, monkeypatch):
    # Use a real file so _guard_audio_size succeeds (Issue 103 fix #3 now raises on missing files).
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"RIFFxxxx")
    monkeypatch.setattr("config.settings.TRANSCRIPTION_BACKEND", "deepgram")
    with patch("ingestion.transcribe._transcribe_deepgram") as mock_dg:
        mock_dg.return_value = {"source": "deepgram", "segments": []}
        result = transcribe_audio(str(wav))
    mock_dg.assert_called_once_with(str(wav))
    assert result["source"] == "deepgram"


def test_transcribe_audio_routes_to_whisperx(tmp_path, monkeypatch):
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"RIFFxxxx")
    monkeypatch.setattr("config.settings.TRANSCRIPTION_BACKEND", "whisperx")
    with patch("ingestion.transcribe._transcribe_whisperx") as mock_wx:
        mock_wx.return_value = {"source": "whisperx", "segments": []}
        transcribe_audio(str(wav))
    mock_wx.assert_called_once()


def test_transcribe_audio_routes_to_assemblyai(tmp_path, monkeypatch):
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"RIFFxxxx")
    monkeypatch.setattr("config.settings.TRANSCRIPTION_BACKEND", "assemblyai")
    with patch("ingestion.transcribe._transcribe_assemblyai") as mock_ai:
        mock_ai.return_value = {"source": "assemblyai", "segments": []}
        transcribe_audio(str(wav))
    mock_ai.assert_called_once()


def test_transcribe_deepgram_raises_on_missing_sdk(monkeypatch):
    monkeypatch.setattr("config.settings.TRANSCRIPTION_BACKEND", "deepgram")
    with (
        patch("builtins.__import__", side_effect=ImportError("deepgram-sdk not installed")),
        pytest.raises((ImportError, Exception)),
    ):
        transcribe_audio("/tmp/audio.wav")


# ── transcription hardening (Issue 76: memory + SDK timeout) ───────────────────


def test_transcribe_rejects_oversize_audio(tmp_path, monkeypatch):
    """A file over TRANSCRIPTION_MAX_MB fails fast before any backend dispatch."""
    monkeypatch.setattr("config.settings.TRANSCRIPTION_BACKEND", "deepgram")
    monkeypatch.setattr("config.settings.TRANSCRIPTION_MAX_MB", 0)
    wav = tmp_path / "big.wav"
    wav.write_bytes(b"x" * 4096)  # > 0 MB cap
    with (
        patch("ingestion.transcribe._transcribe_deepgram") as mock_dg,
        pytest.raises(ValueError, match="transcription cap"),
    ):
        transcribe_audio(str(wav))
    mock_dg.assert_not_called()  # guard fires before the backend


def test_deepgram_streams_handle_and_passes_timeout(tmp_path, monkeypatch):
    """Deepgram gets the open file handle (not f.read() bytes) + an httpx timeout."""
    import sys
    import types

    fake_dg = types.ModuleType("deepgram")
    fake_dg.PrerecordedOptions = lambda **kw: object()
    monkeypatch.setitem(sys.modules, "deepgram", fake_dg)

    captured: dict = {}

    class _Resp:
        def to_dict(self):
            return {}

    class _RestV:
        # Real signature gained an `addons` positional (Issue 251 MIP opt-out:
        # transcribe.py passes addons={"mip_opt_out": True} before timeout).
        def transcribe_file(self, source, opts, addons=None, timeout=None):
            captured["buffer"] = source["buffer"]
            captured["addons"] = addons
            captured["timeout"] = timeout
            return _Resp()

    class _Client:
        listen = types.SimpleNamespace(rest=types.SimpleNamespace(v=lambda _v: _RestV()))

    monkeypatch.setattr("ingestion.transcribe._deepgram_client", lambda: _Client())

    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFFxxxx")
    from ingestion.transcribe import _transcribe_deepgram

    _transcribe_deepgram(str(wav))

    buf = captured["buffer"]
    assert hasattr(buf, "read")  # a streamed file handle …
    assert not isinstance(buf, (bytes, bytearray))  # … not the whole file in RAM
    assert isinstance(captured["timeout"], httpx.Timeout)


def test_assemblyai_sets_sdk_http_timeout(monkeypatch):
    """AssemblyAI's SDK-native http_timeout is set from config (bounds a hung socket)."""
    import sys
    import types

    fake_aai = types.ModuleType("assemblyai")
    fake_aai.settings = types.SimpleNamespace(api_key=None, http_timeout=None)

    class _Transcript:
        words: list = []
        text = ""

    fake_aai.Transcriber = lambda: types.SimpleNamespace(transcribe=lambda _p: _Transcript())
    monkeypatch.setitem(sys.modules, "assemblyai", fake_aai)
    monkeypatch.setattr("config.settings.ASSEMBLYAI_API_KEY", "k")
    monkeypatch.setattr("config.settings.TRANSCRIPTION_HTTP_TIMEOUT_S", 99)
    monkeypatch.setattr("ingestion.transcribe._ASSEMBLYAI_READY", False)

    from ingestion.transcribe import _transcribe_assemblyai

    _transcribe_assemblyai("/tmp/whatever.wav")
    assert fake_aai.settings.http_timeout == 99.0


# ── extract_audio_wav ─────────────────────────────────────────────────────────


def test_extract_audio_wav_calls_ffmpeg_correctly(tmp_path):
    src = tmp_path / "video.mp4"
    src.touch()
    dest = tmp_path / "audio.wav"

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        extract_audio_wav(src, dest)

    cmd = mock_run.call_args[0][0]
    assert "ffmpeg" in cmd
    assert "-ar" in cmd
    assert "16000" in cmd
    assert "-ac" in cmd
    assert "1" in cmd
    assert str(src) in cmd
    assert str(dest) in cmd


def test_extract_audio_wav_raises_on_ffmpeg_failure(tmp_path):
    src = tmp_path / "video.mp4"
    src.touch()
    dest = tmp_path / "audio.wav"

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="Error: no such file")
        with pytest.raises(RuntimeError, match="ffmpeg"):
            extract_audio_wav(src, dest)


# ── yt-dlp guard ──────────────────────────────────────────────────────────────


def test_ytdlp_raises_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr("config.settings.YTDLP_ENABLED", False)
    with pytest.raises(ValueError, match="YTDLP_ENABLED"):
        download_via_ytdlp("dQw4w9WgXcQ", tmp_path)


def test_extract_audio_wav_timeout_raises_runtimeerror():
    """A wedged ffmpeg must surface as RuntimeError (clean Celery retry), not hang. (Issue A)"""
    import subprocess

    with (
        patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1),
        ),
        pytest.raises(RuntimeError, match="timed out"),
    ):
        extract_audio_wav("/tmp/in.mp4", "/tmp/out.wav")


# ── Issue 103: _guard_audio_size OSError → FileNotFoundError ─────────────────


def test_guard_audio_size_raises_filenotfound_on_missing(tmp_path):
    """_guard_audio_size must raise FileNotFoundError for non-existent paths so the
    caller's Celery retry/refund pathway sees a clear terminal error, not a silent
    no-op that wastes the per-job budget. (Issue 103 fix #3)
    """
    from ingestion.transcribe import _guard_audio_size

    non_existent = str(tmp_path / "does_not_exist.wav")
    with pytest.raises(FileNotFoundError, match="audio not found"):
        _guard_audio_size(non_existent)


# ── Issue 103: Deepgram normalizer skips missing timestamps ──────────────────


def test_deepgram_normalizer_skips_missing_timestamps():
    """_normalize_deepgram must skip utterances missing start/end rather than KeyError.
    One valid + one timestamp-missing utterance: only the valid one must survive.
    (Issue 103 fix #2)
    """
    raw = {
        "results": {
            "utterances": [
                {
                    # Valid utterance — has all fields.
                    "start": 0.0,
                    "end": 2.0,
                    "transcript": "Hello world",
                    "words": [
                        {"punctuated_word": "Hello", "start": 0.0, "end": 0.5},
                        {"punctuated_word": "world", "start": 0.6, "end": 1.0},
                    ],
                },
                {
                    # Malformed utterance — missing start and end.
                    "transcript": "broken utterance",
                    "words": [{"punctuated_word": "broken", "start": 3.0, "end": 3.5}],
                },
            ]
        }
    }
    result = _normalize_deepgram(raw)
    # Only the valid utterance survives.
    assert len(result["segments"]) == 1
    assert result["segments"][0]["text"] == "Hello world"


def test_deepgram_normalizer_skips_words_missing_timestamps():
    """Words inside a valid utterance that are missing start/end are also skipped."""
    raw = {
        "results": {
            "utterances": [
                {
                    "start": 0.0,
                    "end": 3.0,
                    "transcript": "Good and bad",
                    "words": [
                        {"punctuated_word": "Good", "start": 0.0, "end": 0.5},
                        # missing timestamps on this word
                        {"punctuated_word": "and"},
                        {"punctuated_word": "bad", "start": 1.0, "end": 1.5},
                    ],
                }
            ]
        }
    }
    result = _normalize_deepgram(raw)
    assert len(result["segments"]) == 1
    # Only the two words with timestamps are present.
    assert len(result["segments"][0]["words"]) == 2
    assert result["segments"][0]["words"][0]["word"] == "Good"
    assert result["segments"][0]["words"][1]["word"] == "bad"


# ── _normalize_assemblyai (Issue 334) ────────────────────────────────────────


def _aai_word(text: str, start: int | None, end: int | None) -> SimpleNamespace:
    """Build a minimal AssemblyAI word object with the same attribute shape as the SDK."""
    return SimpleNamespace(text=text, start=start, end=end)


def _aai_transcript(
    words: list | None, text: str = "hello world"
) -> SimpleNamespace:
    return SimpleNamespace(words=words, text=text)


def test_normalize_assemblyai_happy_path():
    """Valid words produce one segment with correct ms→s conversion."""
    transcript = _aai_transcript(
        words=[_aai_word("hello", 0, 500), _aai_word("world", 600, 1200)],
        text="hello world",
    )
    result = _normalize_assemblyai(transcript)
    assert result["source"] == "assemblyai"
    assert len(result["segments"]) == 1
    seg = result["segments"][0]
    assert seg["start"] == pytest.approx(0.0)
    assert seg["end"] == pytest.approx(1.2)
    assert seg["text"] == "hello world"
    assert seg["words"][0]["word"] == "hello"
    assert seg["words"][0]["start"] == pytest.approx(0.0)
    assert seg["words"][1]["start"] == pytest.approx(0.6)


def test_normalize_assemblyai_none_timestamps_filtered():
    """CONFIRMED BUG (Issue 334): w.start/1000.0 when start=None raised TypeError.

    Words with None start or end must be filtered, not passed to the division.
    After the fix, the valid word survives and the None-timestamp word is dropped.
    """
    transcript = _aai_transcript(
        words=[
            _aai_word("good", 0, 400),
            _aai_word("bad", None, None),  # None timestamps — was TypeError before fix
        ],
        text="good bad",
    )
    result = _normalize_assemblyai(transcript)
    # Should not raise TypeError.
    assert result["source"] == "assemblyai"
    # Only the word with valid timestamps survives.
    assert len(result["segments"]) == 1
    assert len(result["segments"][0]["words"]) == 1
    assert result["segments"][0]["words"][0]["word"] == "good"


def test_normalize_assemblyai_all_none_timestamps_gives_empty_segments():
    """If every word has None timestamps, words list is empty → segments == []."""
    transcript = _aai_transcript(
        words=[_aai_word("oops", None, None), _aai_word("nope", None, None)],
        text="oops nope",
    )
    result = _normalize_assemblyai(transcript)
    assert result["segments"] == []


def test_normalize_assemblyai_empty_words_gives_empty_segments():
    """Empty words list → segments == [] (no IndexError on words[0])."""
    result = _normalize_assemblyai(_aai_transcript(words=[], text=""))
    assert result["segments"] == []


def test_normalize_assemblyai_none_words_gives_empty_segments():
    """transcript.words = None → segments == [] (guards or [] fallback)."""
    result = _normalize_assemblyai(_aai_transcript(words=None, text=None))
    assert result["segments"] == []


def test_normalize_assemblyai_partial_none_start_only():
    """word.start is None but end is valid → word is still filtered (both required)."""
    transcript = _aai_transcript(
        words=[_aai_word("x", None, 500), _aai_word("y", 600, 1000)],
        text="x y",
    )
    result = _normalize_assemblyai(transcript)
    assert len(result["segments"][0]["words"]) == 1
    assert result["segments"][0]["words"][0]["word"] == "y"


# ── _normalize_whisperx edge: missing timestamps default to 0.0 ───────────────


def test_normalize_whisperx_missing_segment_timestamps_defaults_to_zero():
    """WhisperX segments with no start/end keys default to 0.0 (not KeyError)."""
    raw = {"segments": [{"text": "no timestamps here", "words": []}]}
    result = _normalize_whisperx(raw)
    seg = result["segments"][0]
    assert seg["start"] == 0.0
    assert seg["end"] == 0.0
    assert seg["text"] == "no timestamps here"


def test_normalize_whisperx_missing_word_timestamps_defaults_to_zero():
    """WhisperX words with no start/end keys default to 0.0 (not KeyError)."""
    raw = {
        "segments": [
            {
                "start": 1.0,
                "end": 2.0,
                "text": "hi",
                "words": [{"word": "hi"}],  # no start/end keys
            }
        ]
    }
    result = _normalize_whisperx(raw)
    word = result["segments"][0]["words"][0]
    assert word["start"] == 0.0
    assert word["end"] == 0.0


# ── Deepgram utterance with empty words list keeps segment (Issue 334) ─────────


def test_normalize_deepgram_utterance_with_empty_words_list_keeps_segment():
    """An utterance with words=[] still produces a valid segment (no words filtered
    out since there were none to begin with).  The segment text is preserved.
    """
    raw = {
        "results": {
            "utterances": [
                {
                    "start": 0.0,
                    "end": 3.0,
                    "transcript": "music only",
                    "words": [],  # empty — no timestamps to filter
                }
            ]
        }
    }
    result = _normalize_deepgram(raw)
    assert len(result["segments"]) == 1
    assert result["segments"][0]["text"] == "music only"
    assert result["segments"][0]["words"] == []


# ── transcribe_done vlog: word_count + transcript duration_s (Issue 334) ───────


def test_transcribe_done_log_includes_word_count_and_duration(tmp_path, monkeypatch):
    """transcribe_audio must log word_count and transcript_duration_s on success."""

    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"RIFFxxxx")
    monkeypatch.setattr("config.settings.TRANSCRIPTION_BACKEND", "deepgram")

    fake_result = {
        "source": "deepgram",
        "segments": [
            {
                "start": 0.0,
                "end": 5.0,
                "text": "hello world foo",
                "words": [
                    {"word": "hello", "start": 0.0, "end": 0.5},
                    {"word": "world", "start": 0.6, "end": 1.0},
                    {"word": "foo", "start": 1.1, "end": 1.5},
                ],
            }
        ],
    }

    captured_vlogs: list[dict] = []

    def fake_vlog(event: str, **kwargs: object) -> None:
        captured_vlogs.append({"event": event, **kwargs})

    # vlog / now_ms are imported inside transcribe_audio via `from verbose import …`
    # so patch the originals in the verbose module, not in ingestion.transcribe.
    with (
        patch("ingestion.transcribe._transcribe_deepgram", return_value=fake_result),
        patch("verbose.vlog", side_effect=fake_vlog),
        patch("verbose.now_ms", return_value=0),
    ):
        transcribe_audio(str(wav))

    done_logs = [v for v in captured_vlogs if v["event"] == "transcribe_done"]
    assert done_logs, "no transcribe_done vlog emitted"
    done = done_logs[0]
    assert done.get("word_count") == 3
    assert done.get("transcript_duration_s") == pytest.approx(5.0)
