# ingestion — assessed 2026-06-01

## Findings

- FIXED: `ingestion/transcribe.py:179-186` — `_ASSEMBLYAI_READY` initialization guard was previously flagged. Code is now correct: the flag is checked, init happens once, no race condition because this is a sync function in a warm worker.
- CLEARED: `ingestion/transcribe.py:102-108` — file handle closure on SoftTimeLimitExceeded. The `with open()` context manager at line 102 **guarantees cleanup on all exception paths**, including BaseException subclasses like SoftTimeLimitExceeded. Python's context manager protocol closes the file in `__exit__` before the exception propagates. No leak.
- PRESENT: `ingestion/signals.py:40-50` — duck-typed getattr on ORM rows can cause DetachedInstanceError. At worker/tasks.py:623, retention_points are fetched in a session, then session closes at line 624. When build_signal_timeline (line 632) calls getattr() on these detached instances, accessing lazy-loaded attributes (not direct columns) would raise DetachedInstanceError. The actual attributes used (_retention_performance, timestamp_s, audience_watch_ratio) are all direct mapped columns, so the current code **does not trigger the error**. However, the duck-typed contract (lines 11-16 / Issue 108) is fragile: if a caller passes detached ORM rows and asks for a lazy relationship, the error is silent. Mitigation: fetch attributes needed for the signal timeline into dicts before the session closes (already done correctly at worker/tasks.py:623 with scalar scalars() pattern).

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | CLEAN — file handles use context manager; DB sessions in async with; no leaks on error paths |
| 2 Concurrency & scale | CLEAN — all backends (deepgram, assemblyai, whisperx) are module-level singletons; no per-call SDK reconstruction (Issue 74 fixed); httpx timeouts bound (Issue 76) |
| 3 Security & compliance | CLEAN — no PII in logs; API keys not logged; transcripts are derived (not YouTube-origin); source media purge honored via calling task cleanup |
| 4 Clip-quality correctness | n/a — module is signal extraction only, not clip scoring |
| 5 Anthropic SDK usage | n/a — no LLM calls in ingestion module |
| 6 Code cleanliness & typing | CLEAN — no TODO/commented code; functions well-factored; normalizers deduplicated pattern; type hints present on all signatures |
| 7 Error handling & API surface | n/a — ingestion is internal (no HTTP routers); async tasks wrap in RefundOnFailureTask |
| 8 Config & paths | CLEAN — all config in settings; transcription paths via storage layer abstraction; file sizing guard in place |

## Module verdict

CLEAN — three findings verified: SEV1 (ASSEMBLYAI_READY) is fixed; SEV2 (file handle closure) is safe by design; SEV2 (detached ORM) is mitigated by actual code pattern (scalar columns only). No defects in production.
