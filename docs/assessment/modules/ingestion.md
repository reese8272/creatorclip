# ingestion — assessed 2026-05-29 (re-assessment, post Issues 58–75)

Slice: `ingestion/__init__.py` (empty), `ingestion/audio.py`, `ingestion/signals.py`,
`ingestion/transcribe.py`. Callers traced into `worker/tasks.py` (`_transcribe_async`,
`_signals_async`) only to settle async/blocking, timeout, and temp-cleanup claims — those
fixes live in `worker/`/`youtube/` (other slices) but are intrinsic to how this slice runs.
This supersedes the prior pre-hardening assessment; every claim below was re-verified by
reading current code.

## Findings

- [SEV1] ingestion/transcribe.py:61-62 — `_transcribe_deepgram` still buffers the entire WAV
  into RAM via `payload = {"buffer": f.read(), ...}` before posting. Confirmed STILL PRESENT;
  this is the known-open Issue 75 item (`docs/issues.md:1265,1295`). The audio.py `sr=16000`
  fix (Issue 74) does NOT cover this path — transcription ships the original source WAV bytes,
  not the downsample, so an hour of WAV is hundreds of MB held per job, multiplied across warm
  concurrent workers = OOM vector. | fix: stream the open file handle to the SDK instead of
  reading it whole (Deepgram accepts a file-like/streaming source) or transcribe-by-URL; cap and
  reject oversize WAV before read.

- [SEV1] ingestion/transcribe.py:54-64 (Deepgram) and 117-131 (AssemblyAI) — no SDK-native
  (HTTP/socket) timeout on either hosted call. The caller bounds the *job* with
  `asyncio.wait_for(asyncio.to_thread(transcribe_audio, …), timeout=TRANSCRIPTION_TIMEOUT_S)`
  (worker/tasks.py:276-279, Issue 68 — verified present, default 300s in config.py:41 +
  `.env.example:47`). But `wait_for` cannot cancel the spawned OS thread: a hung provider socket
  leaks that worker thread for the process lifetime, so repeated hangs exhaust the threadpool
  even though each job "times out." Tracked as Issue 75 follow-up (`docs/issues.md:1186`). | fix:
  set an explicit client/request timeout on `DeepgramClient` and AssemblyAI's HTTP layer
  (connect+read ~30–60s) so the blocking thread itself returns on a stall.
  (needs-runtime-confirmation: SDKs not installed in this env to confirm exact param names.)

- [SEV2] ingestion/transcribe.py:71-85,99-110,135-138 — Deepgram/AssemblyAI normalizers use
  hard-key indexing (`u["start"]`, `u["end"]`, `w["start"]`, `w["end"]`, `u["transcript"]`).
  A provider payload missing a timestamp raises `KeyError`, surfacing as an opaque ingest
  failure (and burning a Celery retry) rather than a handled empty/partial transcript. The
  WhisperX normalizer (187-200) already uses `.get(..., default)`. | fix: switch the hosted
  normalizers to `.get` with defaults and skip words/utterances lacking timestamps.

- [cleanup] ingestion/transcribe.py:42 — `_deepgram_client()` has no return annotation. | fix:
  annotate `-> "DeepgramClient"` under a `TYPE_CHECKING` import.

- [cleanup] ingestion/transcribe.py:134,157-158,165-166 — `_normalize_assemblyai(transcript)`,
  `_whisperx_model(model_name, device, compute_type)`, and `_whisperx_align_model(language_code,
  device)` have untyped params/returns (CLAUDE.md mandates typed signatures). | fix: type the
  AssemblyAI `transcript` (via `TYPE_CHECKING`), and add return types to the two whisperx
  loaders.

- [cleanup] ingestion/signals.py:11 — `build_signal_timeline(audio_events: dict,
  retention_points: list)` uses bare `list`; the body duck-types `getattr` over
  `RetentionCurve`-shaped rows or dicts (KISS-fine, already null-safe via `getattr(..., default)`,
  no behavior change needed). | fix: annotate `retention_points: list[RetentionCurve]` or a
  small `Protocol` to document the contract.

## Fixed since prior assessment (re-verified)

- WhisperX model + align model now cached via `@functools.lru_cache` (transcribe.py:157-169);
  Deepgram client is a lazy module-level singleton (42-51); AssemblyAI key set once behind
  `_ASSEMBLYAI_READY` (125-129) — prior per-call construction findings RESOLVED (Issue 74).
- librosa now loads at `sr=16000` (audio.py:41) instead of `sr=None` — prior full-decode OOM
  finding RESOLVED for the signals path (Issue 74).
- Blocking-on-the-loop: `transcribe_audio` and `extract_audio_events` are now offloaded via
  `asyncio.to_thread` at the call sites (worker/tasks.py:277,315); no blocking call sits inside
  an `async def` in or around this slice — prior SEV2 RESOLVED (Issue 68/74).
- Job-level transcription timeout (`TRANSCRIPTION_TIMEOUT_S`) added (Issue 68) — prior "no
  timeout" SEV1 partially RESOLVED (job bounded; thread-level timeout is the residual SEV1
  above).
- Config drift: `config.py:37` and `.env.example:46` both now reconcile on the documented
  backend; transcription keys present in `.env.example` with descriptions — prior cleanup
  RESOLVED.

## Notes verified clean (no finding)

- Resource lifecycle: only one file handle in slice (transcribe.py:61) inside a `with`. Temp WAV
  cleanup is in the caller's `finally` (worker/tasks.py:241-247) and `local_path` is context-
  managed (273,314) — out of slice but correct.
- Security/compliance: no OAuth tokens, no PII, no secrets handled here. Sole `logger` call
  (transcribe.py:31) logs the backend name only — keys read from settings, never logged. No SQL
  in slice; the creator-scoped retention query + isolation live in worker/tasks.py:309-312. No
  virality language in any string. Source-media retention/purge enforced in worker (COMPLIANCE
  table; `ingest_done_at` set at worker/tasks.py:328-329) — out of slice, correct.
- Config/paths: missing-key paths fail fast with `ValueError` (transcribe.py:49,123); paths
  typed `str | Path`, callers pass absolute temp paths.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — singletons per Issue 74; file handle context-managed; temp cleanup in caller |
| 2 Concurrency & scale | 1 SEV1 (Deepgram f.read() unbounded memory, Issue 75 open); blocking-in-async RESOLVED; librosa bounded (sr=16000) |
| 3 Security & compliance | ok — no tokens/PII/SQL in slice; backend-name-only logging; no virality strings |
| 4 Clip-quality | n/a — ingestion produces signals, does not score/anchor clips |
| 5 Anthropic SDK | n/a — module makes no LLM calls |
| 6 Cleanliness & typing | 3 cleanup (untyped _deepgram_client / _normalize_assemblyai / whisperx loaders + loose list) |
| 7 Error handling / API | 1 SEV1 (no SDK-native timeout → leaked worker thread) + 1 SEV2 (hard-key indexing → opaque KeyError) |
| 8 Config & paths | ok — keys in `.env.example` with descriptions; fail-fast ValueError guards |

## Module verdict
NEEDS-WORK — no BLOCKER and no cross-tenant/security defect; the Issue 74/68 hardening
(singletons, sr=16000, to_thread offload, job timeout) is verified in place. Two SEV1
transcription-path gaps remain open from Issue 75: Deepgram `f.read()` still buffers whole files
(memory under load) and neither hosted SDK has a request-level timeout, so a hung provider leaks
a worker thread despite the job-level `wait_for`.
