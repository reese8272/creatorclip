"""
Transcription backend abstraction.

Routes to Deepgram (default), AssemblyAI, or WhisperX based on TRANSCRIPTION_BACKEND.
All backends are normalized to the same internal schema:
  {
    "source": str,
    "segments": [
      {"start": float, "end": float, "text": str,
       "words": [{"word": str, "start": float, "end": float}]}
    ]
  }
"""

import logging
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)


def transcribe_audio(audio_path: str | Path) -> dict:
    backend = settings.TRANSCRIPTION_BACKEND
    logger.info("Transcribing via %s", backend)
    if backend == "deepgram":
        return _transcribe_deepgram(str(audio_path))
    if backend == "assemblyai":
        return _transcribe_assemblyai(str(audio_path))
    return _transcribe_whisperx(str(audio_path))


# ── Deepgram ──────────────────────────────────────────────────────────────────


def _transcribe_deepgram(audio_path: str) -> dict:
    try:
        from deepgram import DeepgramClient, PrerecordedOptions
    except ImportError as exc:
        raise ImportError("deepgram-sdk not installed. Run: pip install deepgram-sdk") from exc
    if not settings.DEEPGRAM_API_KEY:
        raise ValueError("DEEPGRAM_API_KEY is not set")

    client = DeepgramClient(settings.DEEPGRAM_API_KEY)
    with open(audio_path, "rb") as f:
        payload = {"buffer": f.read(), "mimetype": "audio/wav"}
    opts = PrerecordedOptions(model="nova-3", smart_format=True, utterances=True, words=True)
    raw = client.listen.rest.v("1").transcribe_file(payload, opts).to_dict()
    return _normalize_deepgram(raw)


def _normalize_deepgram(raw: dict) -> dict:
    utterances = (raw.get("results") or {}).get("utterances") or []
    if utterances:
        segments = [
            {
                "start": u["start"],
                "end": u["end"],
                "text": u["transcript"],
                "words": [
                    {
                        "word": w.get("punctuated_word", w.get("word", "")),
                        "start": w["start"],
                        "end": w["end"],
                    }
                    for w in u.get("words", [])
                ],
            }
            for u in utterances
        ]
    else:
        channels = (raw.get("results") or {}).get("channels") or [{}]
        alts = channels[0].get("alternatives") or [{}]
        alt = alts[0]
        words = [
            {
                "word": w.get("punctuated_word", w.get("word", "")),
                "start": w["start"],
                "end": w["end"],
            }
            for w in alt.get("words") or []
        ]
        segments = (
            [
                {
                    "start": words[0]["start"],
                    "end": words[-1]["end"],
                    "text": alt.get("transcript", ""),
                    "words": words,
                }
            ]
            if words
            else []
        )
    return {"source": "deepgram", "segments": segments}


# ── AssemblyAI ────────────────────────────────────────────────────────────────


def _transcribe_assemblyai(audio_path: str) -> dict:
    try:
        import assemblyai as aai
    except ImportError as exc:
        raise ImportError("assemblyai not installed. Run: pip install assemblyai") from exc
    if not settings.ASSEMBLYAI_API_KEY:
        raise ValueError("ASSEMBLYAI_API_KEY is not set")

    aai.settings.api_key = settings.ASSEMBLYAI_API_KEY
    transcript = aai.Transcriber().transcribe(audio_path)
    return _normalize_assemblyai(transcript)


def _normalize_assemblyai(transcript) -> dict:
    words = [
        {"word": w.text, "start": w.start / 1000.0, "end": w.end / 1000.0}
        for w in (transcript.words or [])
    ]
    segments = (
        [
            {
                "start": words[0]["start"],
                "end": words[-1]["end"],
                "text": transcript.text or "",
                "words": words,
            }
        ]
        if words
        else []
    )
    return {"source": "assemblyai", "segments": segments}


# ── WhisperX ──────────────────────────────────────────────────────────────────


def _transcribe_whisperx(audio_path: str) -> dict:
    try:
        import whisperx
    except ImportError as exc:
        raise ImportError(
            "whisperx is not installed. Run: pip install git+https://github.com/m-bain/whisperX.git"
        ) from exc
    model = whisperx.load_model(settings.WHISPER_MODEL, device="cpu", compute_type="int8")
    audio = whisperx.load_audio(audio_path)
    result = model.transcribe(audio, batch_size=16)
    align_model, metadata = whisperx.load_align_model(
        language_code=result["language"], device="cpu"
    )
    result = whisperx.align(result["segments"], align_model, metadata, audio, device="cpu")
    return _normalize_whisperx(result)


def _normalize_whisperx(raw: dict) -> dict:
    segments = [
        {
            "start": seg.get("start", 0.0),
            "end": seg.get("end", 0.0),
            "text": seg.get("text", ""),
            "words": [
                {"word": w.get("word", ""), "start": w.get("start", 0.0), "end": w.get("end", 0.0)}
                for w in seg.get("words", [])
            ],
        }
        for seg in raw.get("segments", [])
    ]
    return {"source": "whisperx", "segments": segments}
