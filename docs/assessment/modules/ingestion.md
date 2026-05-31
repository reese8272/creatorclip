# ingestion — assessed 2026-05-31 (Wave 2 re-assessment, no-touch)

Slice: `ingestion/__init__.py` (empty), `ingestion/audio.py`, `ingestion/signals.py`,
`ingestion/transcribe.py`. (No `ingestion/source.py` exists in the tree — the brief
listed it as conditional; the slice is the three Python files above plus the empty
`__init__.py`.) Worker call sites in `worker/tasks.py:378-415` (`_transcribe_async`,
`_signals_async`) traced only to confirm async/blocking, timeout, and temp-cleanup
boundaries — the fixes there live in another slice but are intrinsic to how this slice
runs. Issue 92's progress-event wiring (step events around the transcribe call) lives
in the **caller** (`worker/tasks.py:386,401-407`) and does not touch this slice; the
slice's behavior, signatures, and call surface are byte-identical to the 2026-05-30
state. **Wave 2 did NOT touch ingestion** — `git log f5d44df..HEAD -- ingestion/`
returns zero commits — so the two carry-forward SEV2s remain open. Re-verified
against current code at HEAD (`74431e7`).

## Findings

- [SEV2] ingestion/transcribe.py:116-123,137-138 — Deepgram normalizer still uses
  hard-key indexing (`u["start"]`, `u["end"]`, `u["transcript"]`, `w["start"]`,
  `w["end"]`) on the provider payload. A response missing a timestamp on any
  utterance/word raises `KeyError`, surfacing as an opaque ingest failure and burning
  a Celery retry instead of skipping the partial item. The WhisperX normalizer
  (lines 236-243) already uses `.get(..., default)`; the AssemblyAI normalizer
  (lines 180-184) uses attribute access on SDK objects — only Deepgram is at risk.
  Carry-forward from 2026-05-30 assessment; UNADDRESSED in Wave 1 and Wave 2. | fix:
  switch both list comprehensions in `_normalize_deepgram` to `.get("start")` /
  `.get("end")` and skip any utterance/word where either is `None`; default text via
  `u.get("transcript", "")` and `w.get("punctuated_word", w.get("word", ""))`
  (already done for `word`, finish the job for the timestamps).

- [SEV2] ingestion/transcribe.py:43-60 — `_guard_audio_size` swallows `OSError` and
  silently returns when the file is missing/unreadable, deferring to the SDK to
  surface a not-found. For WhisperX, `whisperx.load_audio` will produce a cryptic
  ffmpeg/torch error; for AssemblyAI, the SDK will upload a 0-byte stream that
  succeeds and returns an empty transcript — burning the per-job budget on a
  guaranteed-empty pipeline run and (under Issue 57) triggering an automatic refund
  for a cause we could have detected up front. Carry-forward from 2026-05-30;
  UNADDRESSED in Wave 1 and Wave 2. | fix: in the `except OSError` branch, raise
  `FileNotFoundError(f"audio not found: {audio_path}")` so the caller's retry/refund
  pathway sees a clear terminal error rather than a silent empty success.

- [cleanup] ingestion/transcribe.py:77 — `_deepgram_client()` has no return
  annotation (CLAUDE.md mandates typed signatures). | fix: annotate
  `-> "DeepgramClient"` under a `TYPE_CHECKING` import of
  `from deepgram import DeepgramClient`.

- [cleanup] ingestion/transcribe.py:180,204,212 — `_normalize_assemblyai(transcript)`,
  `_whisperx_model(model_name, device, compute_type)`, and
  `_whisperx_align_model(language_code, device)` have untyped params and/or untyped
  returns. | fix: type AssemblyAI `transcript` via `TYPE_CHECKING` import of
  `aai.Transcript`; add explicit return types to the two WhisperX loaders (annotate
  with `Any` if the exact types aren't importable without the optional dep).

- [cleanup] ingestion/signals.py:11 — `build_signal_timeline(audio_events: dict,
  retention_points: list)` uses bare `list`; the body duck-types `getattr` over
  `RetentionCurve`-shaped rows or dicts (KISS-fine, already null-safe via
  `getattr(..., default)`). | fix: annotate `retention_points: list[RetentionCurve]`
  (or a small `Protocol` declaring `timestamp_s`, `audience_watch_ratio`,
  `relative_retention_performance`) to document the contract.

## Fixed since prior assessment (re-verified at HEAD `74431e7`)

- **Deepgram whole-file buffering (was SEV1):** `_transcribe_deepgram` opens the WAV
  and passes the file handle directly to `client.listen.rest.v("1").transcribe_file(...)`
  (transcribe.py:101-107). httpx streams the upload — no full-file `bytes` held in
  Python. Issue 76 / commit b8a8735. STILL RESOLVED.
- **No SDK-native transcription timeout (was SEV1):** `_http_timeout()` constructs
  `httpx.Timeout(TRANSCRIPTION_HTTP_TIMEOUT_S, connect=10.0)` and is passed to
  Deepgram's `transcribe_file(..., timeout=...)` (transcribe.py:105). For AssemblyAI,
  `aai.settings.http_timeout = float(TRANSCRIPTION_HTTP_TIMEOUT_S)` is set once at
  module init (transcribe.py:174). Config keys present in `config.py:61` and
  `.env.example:48` with the "keep < TRANSCRIPTION_TIMEOUT_S" guidance. The blocking
  SDK thread now returns on a hung socket, so the job-level `asyncio.wait_for`
  (worker/tasks.py:359-362) no longer leaks worker threads on a stall. STILL RESOLVED.
- **Size guard added (defense-in-depth):** `_guard_audio_size` rejects audio over
  `TRANSCRIPTION_MAX_MB` (default 1024 MB, `config.py:65`) before any read/upload
  (transcribe.py:66). Caps the blast radius of an extracted-WAV anomaly that would
  otherwise stream-upload many GB. The OSError-swallowing footgun is captured as the
  open SEV2 above. STILL IN PLACE.
- **AssemblyAI normalizer hardened (prior SEV2 partially resolved):**
  `_normalize_assemblyai` reads `w.text/w.start/w.end` off SDK objects, not dict keys
  (transcribe.py:180-184), so the AssemblyAI half of the hard-key SEV2 is closed.
  The Deepgram normalizer is the remaining offender (open SEV2 above).

## Notes verified clean (no finding)

- **Resource lifecycle:** the one file handle in slice (transcribe.py:101) is inside
  a `with`. SDK clients are module-level singletons (`_DEEPGRAM_CLIENT` guarded by
  `_deepgram_client()`, `_ASSEMBLYAI_READY` flag, `@functools.lru_cache` on WhisperX
  load/align models at sizes 2/4). Temp WAV cleanup is in the caller's
  `alocal_path` context manager (worker/tasks.py:355,397 — out of slice but correct).
- **Concurrency & scale:** no blocking call sits inside an `async def` in this
  slice (the slice exposes only sync functions; the worker offloads them via
  `asyncio.to_thread` at `worker/tasks.py:360,400`). librosa is bounded at
  `sr=16000` (audio.py:41), capping memory ~3× under the native-rate decode.
  Deepgram upload is now streamed; `_merge_runs` is O(n) over frame count with no
  unbounded accumulation.
- **Security & compliance:** no OAuth tokens, no PII, no SQL in slice. Sole
  `logger.info` call (transcribe.py:65) logs only the backend name — API keys are
  read from settings, never logged. No virality language in any docstring/log/error
  string. Source-media retention/purge enforced by the caller (`worker/tasks.py`,
  out of slice). Per `docs/COMPLIANCE.md`, transcripts are derived data (not
  YouTube-origin) and retained until video deletion — slice produces but does not
  store, so retention is the caller's responsibility.
- **Anthropic SDK:** module makes no LLM calls. n/a.
- **Config & paths:** required-key guards fail fast with `ValueError`
  (transcribe.py:84,166). All four transcription keys
  (`TRANSCRIPTION_BACKEND` / `_TIMEOUT_S` / `_HTTP_TIMEOUT_S` / `_MAX_MB`) present
  in `.env.example:46-49` with descriptions. Paths typed `str | Path`; callers pass
  absolute temp paths from `alocal_path`.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — singletons + lru_cache; file handle context-managed; caller-owned temp cleanup |
| 2 Concurrency & scale | ok — Deepgram streams; httpx timeout set; librosa sr=16000; sync-only API offloaded by caller |
| 3 Security & compliance | ok — no tokens/PII/SQL in slice; backend-name-only logging; no virality strings |
| 4 Clip-quality | n/a — ingestion produces signals, does not score/anchor clips |
| 5 Anthropic SDK | n/a — module makes no LLM calls |
| 6 Cleanliness & typing | 3 cleanup (untyped `_deepgram_client`, `_normalize_assemblyai`, whisperx loaders; loose `list` in signals) |
| 7 Error handling / API | 2 SEV2 (Deepgram-normalizer hard-key indexing → opaque `KeyError`; size-guard swallows `OSError` → silent empty-pipeline run) |
| 8 Config & paths | ok — keys + descriptions in `.env.example`; fail-fast guards |

## Module verdict
NEEDS-WORK — Neither Wave 1 nor Wave 2 touched ingestion (zero commits in
`git log f5d44df..HEAD -- ingestion/`), so the two carry-forward SEV2s (Deepgram
normalizer hard-key indexing and `_guard_audio_size` swallowing OSError) remain
open. No BLOCKER, no SEV1, no security or cross-tenant defect; the prior SEV1s
closed by Issue 76 (Deepgram streaming + SDK-native timeouts) are still green at
HEAD. Issue 92's caller-side step events do not change the slice's contract.
