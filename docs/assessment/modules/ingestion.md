# ingestion — assessed 2026-06-09

## Findings

- [SEV2] ingestion/audio.py:41 — `librosa.load(...)` decodes the whole waveform into
  memory (~115 MB/hour float32 at 16 kHz mono before downmix buffers); there is no
  size/duration cap mirroring the `TRANSCRIPTION_MAX_MB` guard at transcribe.py:62
  (verified: no `AUDIO_MAX_*` in config.py or worker/tasks.py). A pathological
  multi-hour WAV across concurrent workers is an OOM vector. Unchanged since the
  2026-06-07 assessment. | fix: add `AUDIO_MAX_MB` (or a duration probe via
  `librosa.get_duration(path=...)`) at the top of `extract_audio_events`, fail fast
  with `ValueError`, document in `.env.example` next to `TRANSCRIPTION_MAX_MB`.
- [SEV2] ingestion/audio.py:46,49 — RMS/ZCR are normalised to the per-file peak
  (`rms / (rms.max() + 1e-8)`), so `_ENERGY_THRESHOLD=0.6` / `_SILENCE_THRESHOLD=0.03`
  (audio.py:15–16) are relative, not anchored to physical loudness: a uniformly quiet
  recording still "spikes" at 0.6 of its own peak; a constantly loud room never emits
  a silence event (breaks principle 5, Dead-air elimination, downstream). Unchanged
  since 2026-06-07. | fix: threshold against dBFS (`librosa.amplitude_to_db`) or a
  rolling noise floor; at minimum log a warning when the absolute peak is so low the
  relative signal is meaningless.
- [SEV2] ingestion/audio.py:106 — `end_s = float(times[min(end_idx - 1, len(times) - 1)])
  + frame_duration` can push the trailing run's `end_s` past `duration_s` (last frame
  is centred before the audio end, then a full hop is added), so out-of-range events
  are written into `signals.timeline_jsonb`. Unchanged since 2026-06-07. | fix: clamp
  in `_emit` — pass `duration_s` down and `end_s = min(end_s, duration_s)` before append.
- [SEV2] ingestion/transcribe.py:200–201 — `aai.Transcriber().transcribe(audio_path)`
  result is normalised without checking `transcript.status`: the AssemblyAI SDK
  returns a transcript with `status == error` instead of raising, in which case
  `transcript.words` is `None` and `_normalize_assemblyai` (transcribe.py:204–221)
  silently returns `{"source": "assemblyai", "segments": []}`. The job then "succeeds"
  with an empty transcript — no Celery retry, no terminal-failure refund
  (docs/COMPLIANCE.md billing section), and garbage downstream clip windows.
  (needs-runtime-confirmation on the exact SDK error path, but the code has no status
  check at all.) | fix: after `transcribe()`, `if transcript.status == aai.TranscriptStatus.error:
  raise RuntimeError(f"AssemblyAI transcription failed: {transcript.error}")` so the
  worker retry/refund chain engages.
- [cleanup] ingestion/signals.py:54 — `"relative_retention": rrp or 0.0` uses
  truthiness where the surrounding code (line 48) uses explicit `is not None`. For
  `float | None` the behaviour is identical (`0.0 or 0.0 == 0.0`), so this is a
  readability trap, not a defect — downgraded from the SEV2 in the 2026-06-07
  assessment, which itself noted "not a behaviour bug today". | fix:
  `rrp if rrp is not None else 0.0`.
- [cleanup] ingestion/transcribe.py:200 — a new `aai.Transcriber()` (with its own
  HTTP client/pool) is constructed per call, contrary to the module-singleton pattern
  used for Deepgram (transcribe.py:84–98); bounded churn since AssemblyAI is not the
  default backend, but inconsistent. | fix: cache a module-level `_ASSEMBLYAI_TRANSCRIBER`
  inside the existing `_ASSEMBLYAI_LOCK` init block.
- [cleanup] ingestion/audio.py:24,76,109 — return types are bare `dict` / `list[dict]`;
  the shape only lives in docstrings, and signals.py:31–38 compensates with defensive
  `.get(..., [])`. | fix: define `EventInterval` / `AudioEvents` TypedDicts at the
  top of audio.py and use them as annotations.
- [cleanup] ingestion/signals.py:24 — `build_signal_timeline` annotated `-> dict`;
  same TypedDict fix (`Timeline` with `version: int`, `duration_s: float`,
  `events: list[dict]`).
- [cleanup] ingestion/transcribe.py:38–47,50–57,109–112 — docstrings restate the full
  Issue 74/76/123 rationale already recorded in DECISIONS/issues; drift risk only. |
  fix: shorten to one line + issue ID.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | 2 findings — Deepgram upload streams from an open handle inside `with open(...)` (transcribe.py:113–119) so the WAV is never fully buffered; Deepgram client and AssemblyAI init are lock-guarded module singletons (transcribe.py:32–35, 89–98, 190–199); WhisperX model/align cached via `lru_cache` (transcribe.py:227, 235). Gaps: per-call `aai.Transcriber()` (cleanup) and the missing AssemblyAI error-status check (SEV2). Temp-media cleanup is the caller's job (worker/storage). |
| 2 Concurrency & scale | 1 finding — both heavy entry points are dispatched off the loop via `asyncio.to_thread` (worker/tasks.py:611 for `transcribe_audio`, :692 for `extract_audio_events`); double-checked locking verified at transcribe.py:90–98 and 191–199; `lru_cache` is thread-safe under CPython. Remaining gap: no audio size/duration cap in audio.py (SEV2 above) — unbounded in-memory decode. |
| 3 Security & compliance | ok — only log line is `logger.info("Transcribing via %s", backend)` (transcribe.py:72); no API key, transcript text, or PII logged. No SQL in the module; per-creator isolation is the caller's boundary (pure transformation over a path + pre-filtered retention rows). No virality language. Source-media retention enforced upstream per docs/COMPLIANCE.md. Config keys verified present in config.py:58–74 and .env.example:47–54. |
| 4 Clip-quality | partial — `is_rewatch_spike` honoured as ground-truth crowd signal even when rrp is unavailable (signals.py:47–48, principle 6); events sorted by `start_s` (signals.py:59). The per-file-peak normalisation (audio.py:46,49) undermines silence detection on loud recordings — load-bearing for principle 5 via downstream consumers; see SEV2. This module emits raw signal only; principle citation happens in clip_engine. |
| 5 Anthropic SDK | n/a (no LLM calls) |
| 6 Cleanliness & typing | 4 cleanup — bare-`dict` returns (audio.py, signals.py), `rrp or 0.0` truthiness, docstring rationale drift; no TODO / commented blocks / `print()`; every signature typed. |
| 7 Error handling / API | ok — internal module, no HTTP surface; ImportError/ValueError/FileNotFoundError raise upward into the worker retry chain (transcribe.py:59–67, 95–96, 104–105, 187–188), which is the correct boundary. The AssemblyAI silent-empty path is the one hole (SEV2 above). |
| 8 Config & paths | ok — `TRANSCRIPTION_BACKEND/HTTP_TIMEOUT_S/MAX_MB`, `DEEPGRAM_API_KEY`, `ASSEMBLYAI_API_KEY`, `WHISPER_MODEL` all via `config.settings` with `.env.example` entries; paths accepted as `str | Path` from the worker, which passes absolute tmp paths. The recommended `AUDIO_MAX_MB` would extend this. |

## Module verdict

NEEDS-WORK — no BLOCKER or SEV1. The Issue 74/76/123 hardening (singletons, locks,
streaming upload, size guard, HTTP timeouts) holds up to re-reading. Four SEV2s:
the three carried over from 2026-06-07 (audio size/duration cap, peak-relative
thresholds, trailing-run `end_s` overshoot) are confirmed unfixed, and one new —
AssemblyAI error-status is never checked, so a failed hosted transcription can
silently become an empty "successful" transcript, bypassing both the retry chain
and the terminal-failure refund.

## Issue 75 Reconciliation (2026-06-23)

| Finding | Disposition |
|---|---|
| [SEV2] audio size/duration cap missing (ingestion/audio.py:41) | → tracked in Issue 228 (per-creator pre-job quota + config hardening) |
| [SEV2] per-file-peak relative normalisation (ingestion/audio.py:46,49) | → tracked in Issue 76 (post-hardening residual SEV-2 cluster) |
| [SEV2] trailing-run end_s overshoot (ingestion/audio.py:106) | → tracked in Issue 76 |
| [SEV2] AssemblyAI error-status not checked (ingestion/transcribe.py:200-201) | → tracked in Issue 76 |
| [cleanup] rrp or 0.0 truthiness (ingestion/signals.py:54) | → tracked in Issue 109 (deferred design cleanups) |
| [cleanup] per-call aai.Transcriber() (ingestion/transcribe.py:200) | → tracked in Issue 109 |
| [cleanup] bare dict returns (ingestion/audio.py, signals.py) | → tracked in Issue 109 |
| [cleanup] docstring rationale drift (ingestion/transcribe.py:38-57) | → tracked in Issue 109 |
