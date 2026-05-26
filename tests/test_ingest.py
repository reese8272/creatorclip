"""
Unit tests for ingestion/transcribe.py and youtube/ingest.py.

Pure function tests — no DB, no network, no ffmpeg.
Transcription backend calls are patched at the SDK boundary.
"""

from unittest.mock import MagicMock, patch

import pytest

from ingestion.transcribe import (
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


def test_transcribe_audio_routes_to_deepgram(monkeypatch):
    monkeypatch.setattr("config.settings.TRANSCRIPTION_BACKEND", "deepgram")
    with patch("ingestion.transcribe._transcribe_deepgram") as mock_dg:
        mock_dg.return_value = {"source": "deepgram", "segments": []}
        result = transcribe_audio("/tmp/audio.wav")
    mock_dg.assert_called_once_with("/tmp/audio.wav")
    assert result["source"] == "deepgram"


def test_transcribe_audio_routes_to_whisperx(monkeypatch):
    monkeypatch.setattr("config.settings.TRANSCRIPTION_BACKEND", "whisperx")
    with patch("ingestion.transcribe._transcribe_whisperx") as mock_wx:
        mock_wx.return_value = {"source": "whisperx", "segments": []}
        transcribe_audio("/tmp/audio.wav")
    mock_wx.assert_called_once()


def test_transcribe_audio_routes_to_assemblyai(monkeypatch):
    monkeypatch.setattr("config.settings.TRANSCRIPTION_BACKEND", "assemblyai")
    with patch("ingestion.transcribe._transcribe_assemblyai") as mock_ai:
        mock_ai.return_value = {"source": "assemblyai", "segments": []}
        transcribe_audio("/tmp/audio.wav")
    mock_ai.assert_called_once()


def test_transcribe_deepgram_raises_on_missing_sdk(monkeypatch):
    monkeypatch.setattr("config.settings.TRANSCRIPTION_BACKEND", "deepgram")
    with (
        patch("builtins.__import__", side_effect=ImportError("deepgram-sdk not installed")),
        pytest.raises((ImportError, Exception)),
    ):
        transcribe_audio("/tmp/audio.wav")


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
