# ingestion — assessed 2026-05-29

Slice: `ingestion/__init__.py`, `ingestion/audio.py`, `ingestion/signals.py`,
`ingestion/transcribe.py`. Callers traced into `worker/tasks.py`
(`_transcribe_async`, `_signals_async`) and `worker/celery_app.py` (`run_async`)
to settle async/blocking and lifecycle claims. Temp-file download/cleanup is
owned by `worker/storage.local_path` (out of slice; verified to unlink in a
`finally`), so ingestion itself receives a path and does not leak temp files.

## Findings

- [SEV1] ingestion/transcribe.py:36-49 (`_transcribe_deepgram`) and 101-111
  (`_transcribe_assemblyai`) — **no timeout on any transcription network call**
  (Deepgram `transcribe_file`, AssemblyAI `Transcriber().transcribe`). A slow or
  hung provider stalls the worker's only event loop for the whole job with no
  upper bound; under provider degradation this cascades into stuck workers and a
  growing queue (scale-checklist E, backpressure). | fix: pass an explicit
  timeout — Deepgram `PrerecordedOptions`/client supports a request timeout
  (set ~300s for long media); AssemblyAI `TranscriptionConfig` / poll timeout.
  Add a `TRANSCRIPTION_TIMEOUT_S` setting (default 300) in config.py +
  `.env.example`, and wrap the call so a timeout raises a retryable error that
  the Celery `self.retry` path already handles.

- [SEV2] ingestion/transcribe.py:45-46 (`_transcribe_deepgram`) —
  `payload = {"buffer": f.read()}` reads the **entire WAV into RAM** before
  upload. For long videos (an hour of WAV is hundreds of MB) times concurrent
  workers this is unbounded memory growth and an OOM risk at scale (rubric 2:
  bounded work / no unbounded reads). | fix: hand Deepgram a file stream instead
  of a full buffer (the SDK accepts a file-like / streaming source), or upload by
  URL. Avoid materializing the whole file; at minimum cap acceptable input size
  and reject oversized inputs before read.

- [SEV2] ingestion/transcribe.py:144 (`_transcribe_whisperx`) —
  `whisperx.load_model(...)` (and `load_align_model` at 147) are invoked **on
  every call**, reloading the model from disk per video. This is an expensive
  per-call construction, not a module-level singleton (rubric 1: clients/heavy
  resources should be singletons). | fix: cache the loaded model + align model in
  module-level lazily-initialized singletons keyed by model name/language (e.g.
  an `@functools.lru_cache` factory or module globals guarded by a lock), so a
  warm worker reuses them across videos.

- [SEV2] ingestion/transcribe.py:44 (`DeepgramClient(...)`) and 109
  (`aai.settings.api_key = ...` + `aai.Transcriber()`) — external API clients are
  **constructed per call** rather than as module-level singletons (rubric 1).
  Mutating the global `aai.settings.api_key` on every call is also a shared-state
  smell under concurrency. | fix: build one module-level `DeepgramClient`
  singleton (lazily, after the key check) and one configured AssemblyAI
  transcriber; set the AssemblyAI key once at import/first-use, not per call.

- [SEV2] worker/tasks.py:206 (`transcribe_audio`) and worker/tasks.py:242
  (`extract_audio_events`) — these ingestion functions are fully **blocking**
  (librosa decode, WhisperX inference, Deepgram/AssemblyAI SDK + `f.read()`) yet
  are called directly inside `async def` coroutines that run on the per-worker
  **singleton event loop** (`run_async` → `loop.run_until_complete`,
  worker/celery_app.py:41-51). Today the worker pool is prefork
  `--concurrency=2` (docker-compose.yml:26), so each child processes one task
  serially and there is no co-scheduled coroutine to starve — blast radius is
  bounded. But this violates the loop-hygiene rule (scale-checklist B) and
  becomes a SEV1 the moment the pool changes to threads/gevent or a second
  coroutine is co-scheduled on that loop. (needs-runtime-confirmation that the
  prefork pool is retained.) | fix: run the blocking work off the loop —
  `await asyncio.to_thread(transcribe_audio, str(audio_path))` and
  `await asyncio.to_thread(extract_audio_events, str(audio_path))` — so the
  singleton loop (and the SQLAlchemy async engine bound to it) stays responsive
  regardless of pool type. (Fix lives at the call sites in worker/, flagged here
  because the blocking is intrinsic to the ingestion functions.)

- [SEV2] ingestion/audio.py:37 (`extract_audio_events`) —
  `librosa.load(str(audio_path), sr=None, mono=True)` loads the **entire decoded
  waveform into a float32 numpy array** in memory (`sr=None` keeps native rate,
  so an hour at 48 kHz ≈ 170M samples ≈ 690 MB). Unbounded with input length;
  multiplied across workers this is an OOM vector (rubric 2: bounded work). | fix:
  resample down on load (`sr=16000` is ample for RMS/ZCR energy/silence/laughter
  heuristics and the standard rate for these features), and/or stream the file in
  blocks via `librosa.stream` and accumulate per-frame RMS/ZCR incrementally
  rather than holding the whole signal.

- [cleanup] ingestion/transcribe.py:114 (`_normalize_assemblyai(transcript)`) —
  parameter `transcript` is untyped (CLAUDE.md mandates a type on every
  signature). | fix: annotate with the AssemblyAI transcript type (or a
  `Protocol`/`Any` if the SDK type is awkward to import) and keep the `-> dict`.

- [cleanup] config.py:31 vs .env.example:43 — default for
  `TRANSCRIPTION_BACKEND` disagrees: config.py defaults to `"deepgram"` while
  `.env.example` documents `whisperx`. With no `.env` override the app silently
  runs Deepgram and fails fast with `ValueError: DEEPGRAM_API_KEY is not set`
  (transcribe.py:42) — a confusing first-run experience. | fix: make the two
  agree on one default and state in `.env.example` which key that default
  requires.

- [cleanup] ingestion/signals.py:11 (`retention_points: list`) — element type is
  unspecified; the function relies on duck-typed `getattr` over
  `RetentionCurve`-shaped objects. | fix: type as
  `list[RetentionCurve | Mapping[str, Any]]` (or a small `Protocol`) to document
  the contract; the existing `getattr(..., default)` calls already make it
  null-safe so no behavior change is needed.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | 2 findings (per-call WhisperX model load; per-call SDK clients) |
| 2 Concurrency & scale | 3 findings (blocking on singleton loop; Deepgram full read; librosa full decode) |
| 3 Security & compliance | ok — no creator-scoped queries in slice; no token handling; no PII/secrets in `logger` calls (only backend name logged at transcribe.py:25); no f-string SQL; no virality strings; `yt-dlp`/source-download not in this slice |
| 4 Clip-quality | n/a — ingestion produces signals, does not score/anchor clips |
| 5 Anthropic SDK | n/a — module makes no LLM calls |
| 6 Cleanliness & typing | 2 findings (untyped `transcript` param; loose `list` type); no TODO/print/dead code |
| 7 Error handling / API | n/a — no routers in slice (errors raise to Celery retry path, which is correct) |
| 8 Config & paths | 1 finding (backend default drift); transcription config keys present in `.env.example`; no missing-config fail-fast gap beyond the runtime `ValueError` guards |

## Module verdict
NEEDS-WORK — no cross-tenant/security BLOCKER (the module touches no
creator-scoped table and handles no tokens), but transcription calls have no
timeout (SEV1 backpressure risk) and both the Deepgram full-file read and the
`librosa.load` full decode are unbounded-memory vectors that need fixing before
hundreds of concurrent media jobs.
