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

import functools
import logging
from pathlib import Path
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)

# Module-level singletons (Issue 74): the SDK clients and the WhisperX model were
# being reconstructed/reloaded on every call. A warm worker now reuses them.
_DEEPGRAM_CLIENT = None
_ASSEMBLYAI_READY = False


def _http_timeout() -> httpx.Timeout:
    """Per-request timeout for hosted backends (Issue 76).

    The job-level ``asyncio.wait_for`` (worker/tasks.py) bounds the task but cannot
    cancel the OS thread the blocking SDK call runs on, so a hung provider socket
    would leak that thread for the process lifetime. An SDK-native httpx timeout
    makes the blocking call itself return on a stall. Read/write/pool use
    ``TRANSCRIPTION_HTTP_TIMEOUT_S`` (< the job bound); connect is short.
    """
    return httpx.Timeout(float(settings.TRANSCRIPTION_HTTP_TIMEOUT_S), connect=10.0)


def _guard_audio_size(audio_path: str | Path) -> None:
    """Reject an oversize audio file before any read/upload (Issue 76).

    A normal 16 kHz mono WAV is ~115 MB/hour; a pathological multi-hour or
    mis-extracted file would otherwise be buffered/uploaded in full. Fail fast with
    a clear error instead. This guards size only — a missing/unreadable file is left
    for the backend to surface, so callers that pass a fake path aren't affected.
    """
    try:
        size_mb = Path(audio_path).stat().st_size / (1024 * 1024)
    except OSError as exc:
        raise FileNotFoundError(f"audio not found: {audio_path}") from exc
    if size_mb > settings.TRANSCRIPTION_MAX_MB:
        raise ValueError(
            f"Audio file {audio_path} is {size_mb:.0f} MB, over the "
            f"{settings.TRANSCRIPTION_MAX_MB} MB transcription cap "
            "(TRANSCRIPTION_MAX_MB)."
        )


def transcribe_audio(audio_path: str | Path) -> dict[str, Any]:
    backend = settings.TRANSCRIPTION_BACKEND
    logger.info("Transcribing via %s", backend)
    _guard_audio_size(audio_path)
    if backend == "deepgram":
        return _transcribe_deepgram(str(audio_path))
    if backend == "assemblyai":
        return _transcribe_assemblyai(str(audio_path))
    return _transcribe_whisperx(str(audio_path))


# ── Deepgram ──────────────────────────────────────────────────────────────────


def _deepgram_client() -> Any:
    """Lazy module-level DeepgramClient singleton (Issue 74)."""
    global _DEEPGRAM_CLIENT
    if _DEEPGRAM_CLIENT is None:
        from deepgram import DeepgramClient

        if not settings.DEEPGRAM_API_KEY:
            raise ValueError("DEEPGRAM_API_KEY is not set")
        _DEEPGRAM_CLIENT = DeepgramClient(settings.DEEPGRAM_API_KEY)
    return _DEEPGRAM_CLIENT


def _transcribe_deepgram(audio_path: str) -> dict:
    try:
        from deepgram import PrerecordedOptions
    except ImportError as exc:
        raise ImportError("deepgram-sdk not installed. Run: pip install deepgram-sdk") from exc

    client = _deepgram_client()
    opts = PrerecordedOptions(model="nova-3", smart_format=True, utterances=True, words=True)
    # Stream the open file handle rather than f.read(): Deepgram's FileSource.buffer
    # accepts a BufferedReader, so httpx uploads in chunks and we never hold the whole
    # (~115 MB/hour) WAV in a Python bytes object — the OOM vector under warm
    # concurrent workers (Issue 76). The per-request timeout bounds a hung socket.
    with open(audio_path, "rb") as f:
        source = {"buffer": f, "mimetype": "audio/wav"}
        raw = (
            client.listen.rest.v("1")
            .transcribe_file(source, opts, timeout=_http_timeout())
            .to_dict()
        )
    return _normalize_deepgram(raw)


def _normalize_deepgram(raw: dict) -> dict:
    utterances = (raw.get("results") or {}).get("utterances") or []
    if utterances:
        segments = []
        for u in utterances:
            # Skip utterances missing start/end — a partial Deepgram response must not
            # KeyError the entire job. Matches WhisperX and AssemblyAI normalizer pattern.
            u_start = u.get("start")
            u_end = u.get("end")
            if u_start is None or u_end is None:
                continue
            words = [
                {
                    "word": w.get("punctuated_word", w.get("word", "")),
                    "start": w.get("start"),
                    "end": w.get("end"),
                }
                for w in u.get("words", [])
                if w.get("start") is not None and w.get("end") is not None
            ]
            segments.append(
                {
                    "start": u_start,
                    "end": u_end,
                    "text": u.get("transcript", ""),
                    "words": words,
                }
            )
    else:
        channels = (raw.get("results") or {}).get("channels") or [{}]
        alts = channels[0].get("alternatives") or [{}]
        alt = alts[0]
        words = [
            {
                "word": w.get("punctuated_word", w.get("word", "")),
                "start": w.get("start"),
                "end": w.get("end"),
            }
            for w in alt.get("words") or []
            if w.get("start") is not None and w.get("end") is not None
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

    global _ASSEMBLYAI_READY
    if not _ASSEMBLYAI_READY:
        # Set the global API key once, not on every call (Issue 74). Bound every HTTP
        # request (upload + poll) with the SDK-native timeout so a hung socket returns
        # the blocking thread the job-level wait_for cannot cancel (Issue 76).
        aai.settings.api_key = settings.ASSEMBLYAI_API_KEY
        aai.settings.http_timeout = float(settings.TRANSCRIPTION_HTTP_TIMEOUT_S)
        _ASSEMBLYAI_READY = True
    transcript = aai.Transcriber().transcribe(audio_path)
    return _normalize_assemblyai(transcript)


def _normalize_assemblyai(transcript: Any) -> dict:
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


@functools.lru_cache(maxsize=2)
def _whisperx_model(model_name: str, device: str, compute_type: str) -> Any:
    """Cache the loaded WhisperX model — it was reloaded from disk every call (Issue 74)."""
    import whisperx

    return whisperx.load_model(model_name, device=device, compute_type=compute_type)


@functools.lru_cache(maxsize=4)
def _whisperx_align_model(language_code: str, device: str) -> Any:
    import whisperx

    return whisperx.load_align_model(language_code=language_code, device=device)


def _transcribe_whisperx(audio_path: str) -> dict:
    try:
        import whisperx
    except ImportError as exc:
        raise ImportError(
            "whisperx is not installed. Run: pip install git+https://github.com/m-bain/whisperX.git"
        ) from exc
    model = _whisperx_model(settings.WHISPER_MODEL, "cpu", "int8")
    audio = whisperx.load_audio(audio_path)
    result = model.transcribe(audio, batch_size=16)
    align_model, metadata = _whisperx_align_model(result["language"], "cpu")
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
