# ingestion — assessed 2026-06-07

## Findings

- [SEV1] transcribe.py:106 — `client.listen.rest.v("1").transcribe_file(...)` is a blocking SDK call inside the Celery task worker pool (run via `run_async` in worker/tasks.py), but no explicit async boundary (asyncio.to_thread) wraps it | fix: wrap the _transcribe_deepgram call in `asyncio.to_thread(...)` so the blocking HTTP call doesn't hold the event loop; apply the same to _transcribe_assemblyai and _transcribe_whisperx.
- [SEV2] transcribe.py:84 — DEEPGRAM_API_KEY stored in settings but passed directly to DeepgramClient constructor; not traced to encrypt() | fix: verify that all API keys (DEEPGRAM_API_KEY, ASSEMBLYAI_API_KEY) are encrypted at rest in the config/database layer (outside this module's scope); add a code comment confirming the contract ("keys decrypted at settings instantiation time").
- [cleanup] signals.py:11–16 — Type annotation `Sequence[Any]` with comment about SQLAlchemy Mapped[T] is inconsistent with the rest of the codebase; many modules use TypedDict or Protocol for structural typing | fix: define a Protocol for retention_curve contract (requires `timestamp_s: float`, `audience_watch_ratio: float`, `relative_retention_performance: float | None`, `is_rewatch_spike: bool`) and use it in place of `Sequence[Any]`; same pattern should apply to signals.py usage.
- [cleanup] transcribe.py — No type annotations on return value of _http_timeout() function | fix: add return type annotation `-> httpx.Timeout` on line 32.
- [cleanup] audio.py:24 — Return type is `dict` instead of a specific TypedDict | fix: define a TypedDict for the return shape (`{duration_s: float, energy_spikes: list[...], ...}`) and annotate accordingly.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | SEV1 — blocking transcription SDK calls (Deepgram, AssemblyAI, WhisperX) are called synchronously inside tasks but not wrapped in asyncio.to_thread; file handles for audio reading are properly closed via context manager (transcribe.py:102 with open); temporary media cleaned up in worker/tasks.py finally blocks |
| 2 Concurrency & scale | SEV1 — transcribe calls block the event loop under load; the 300s task-level timeout (TRANSCRIPTION_TIMEOUT_S) bounds duration but cannot prevent thread starvation on warm workers handling multiple videos concurrently; librosa.load resampling to 16 kHz on audio.py:41 mitigates OOM (Issue 74); all thresholds are tunable constants; bounded by file size guard (transcribe.py:56, TRANSCRIPTION_MAX_MB) |
| 3 Security & compliance | ok — no API keys logged (logger.info only logs backend name, not credentials); no transcript content in logs; audio files not logged; video URL not exposed in error messages; tempfile cleanup implied via task failure handler; per-creator isolation not applicable (ingestion is invoked by authenticated user only) |
| 4 Clip-quality | n/a — ingestion extracts signals, does not score clips |
| 5 Anthropic SDK | n/a — no LLM calls in this module |
| 6 Cleanliness & typing | 4 cleanup — _http_timeout() return type missing; return types should be TypedDict not bare `dict`; Sequence[Any] duck-typing notes should use Protocol; all main functions typed, no print/TODO/debug statements |
| 7 Error handling / API | n/a — ingestion is internal (no HTTP router); exceptions from backends (ImportError, ValueError on missing keys, transcription failures) are re-raised for task retry logic to catch |
| 8 Config & paths | ok — all required config present (.env.example: TRANSCRIPTION_BACKEND, TRANSCRIPTION_TIMEOUT_S, TRANSCRIPTION_HTTP_TIMEOUT_S, TRANSCRIPTION_MAX_MB, DEEPGRAM_API_KEY, ASSEMBLYAI_API_KEY); audio_path parameter is str | Path (caller is responsible for absolute paths per Issue 76) |

## Module verdict

NEEDS-WORK — 1 SEV1 (blocking SDK calls not wrapped in asyncio.to_thread, creating event loop stalls under concurrency) and 4 cleanup (type annotations). The file-handle lifecycle is correct; security baseline is sound. SEV1 must be fixed before production load testing. Recommend wrapping all three transcription backend calls in asyncio.to_thread, consistent with worker/tasks.py pattern (Issue 102).

