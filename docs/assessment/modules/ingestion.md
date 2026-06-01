# ingestion — assessed 2026-05-31

## Findings

- [SEV1] ingestion/transcribe.py:179-186 — `_transcribe_assemblyai()` sets global `_ASSEMBLYAI_READY = True` on line 180 before completing initialization checks on lines 184-186; if those checks fail (e.g., ValueError on missing API key or error setting timeout), the next call to `_transcribe_assemblyai()` will skip initialization and fail with a confusing error | fix: move `_ASSEMBLYAI_READY = True` to after line 186, AFTER all initialization is complete. Wrap lines 184-186 in try-except or reorder the check sequence so the flag is set only after successful initialization.

- [SEV2] ingestion/transcribe.py:102-108 — `_transcribe_deepgram()` opens file in `with open(...)` context but the docstring (lines 98-101) acknowledges that the blocking `transcribe_file()` call can be hung by a provider socket; the current implementation is safe under normal exception handling (context manager closes the file), but under SoftTimeLimitExceeded (Celery hard kill), the context manager exit is not guaranteed to run before the signal handler fires | fix: explicitly call `f.close()` before the transcribe call, or add a finally clause: `try: ... finally: f.close()` to ensure cleanup even under SIGPROF.

- [SEV2] ingestion/signals.py:40-50 — `build_signal_timeline()` uses duck-typing with `getattr()` for retention curve fields; if a lazy-loaded SQLAlchemy ORM row is passed with attributes not yet loaded and the session is closed, accessing the attribute will raise DetachedInstanceError | fix: at the call site in worker/tasks.py:620-623, convert retention rows to dicts BEFORE exiting the db session context: `retention_points = [{"timestamp_s": r.timestamp_s, "audience_watch_ratio": r.audience_watch_ratio, "relative_retention_performance": r.relative_retention_performance} for r in retention_result.scalars()]`.

- [cleanup] ingestion/transcribe.py:28-29, 179-186 — inconsistent module-level singleton patterns: `_DEEPGRAM_CLIENT` is lazily initialized in a function (line 78-87), `_ASSEMBLYAI_READY` is a boolean flag with side-effect initialization (line 179-186), and `_WHISPER` models use `@functools.lru_cache` (lines 214, 222) | fix: unify on the cached-function pattern. Replace `_ASSEMBLYAI_READY` with `_assemblyai_module()` function decorated with `@functools.lru_cache(maxsize=1)` that returns the configured aai module, matching `_deepgram_client()` style.

- [cleanup] ingestion/audio.py:34 — `import librosa` is deferred (inside the function) despite librosa being a hard production dependency per config.py and requirements.txt | fix: move `import librosa` to module top with other imports to follow CLAUDE.md pattern (no deferred imports except for optional transitive dependencies).

- [cleanup] ingestion/transcribe.py:32-41 — `_http_timeout()` function is called once per transcription (lines 106, 185) and recreates the httpx.Timeout object on each call | fix: compute once at module load: `_HTTP_TIMEOUT = httpx.Timeout(float(settings.TRANSCRIPTION_HTTP_TIMEOUT_S), connect=10.0)` after settings import, then use `timeout=_HTTP_TIMEOUT` at call sites.

- [cleanup] ingestion/signals.py:52 — `events.sort(key=...)` performs in-place mutation without documenting the side-effect; the pattern is correct but implicit | fix: make it explicit by assigning the result rather than relying on in-place behavior: document in a comment `# Sort in-place for efficiency (events is local)`, or use functional style `sorted(events, ...)` and assign to the dict key directly.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — file handles in transcribe.py:102 use context manager; temp files cleaned up in worker/tasks.py:445-446 with finally block |
| 2 Concurrency & scale | 2 findings — lazy singleton initialization race in AssemblyAI backend; duck-typing retention curves may implicitly touch closed session on lazy-loaded attributes |
| 3 Security & compliance | ok — no logging of OAuth tokens; no PII in ingestion output; source media purged per policy via worker/tasks.py and COMPLIANCE.md requirements |
| 4 Clip-quality | n/a (ingestion is not a clip-scoring module) |
| 5 Anthropic SDK | n/a (ingestion does not call the LLM) |
| 6 Cleanliness & typing | 4 findings — inconsistent singleton patterns, deferred import, unnecessary timeout function reconstruction, implicit in-place sort |
| 7 Error handling / API | ok (routers/videos.py owns API surface; ingestion is pure library with no HTTP endpoints) |
| 8 Config & paths | ok — all settings present in .env.example; TRANSCRIPTION_TIMEOUT_S < CELERY_SOFT_TIME_LIMIT_S validated in config.py:168-185 |

## Module verdict

has SEV1 — one production defect (AssemblyAI initialization state corruption on error path can cause silent failures in subsequent calls), plus one SEV2 file handle safety issue under hard kill and one SEV2 ORM session closure race condition on retention data access.

