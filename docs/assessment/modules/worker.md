# worker — assessed 2026-05-31

## Findings

- [SEV2] worker/progress.py:154-163 — _async_client() creates redis.asyncio client bound to None when called from sync context | fix: check if current is None BEFORE recreating _AIO; raise RuntimeError("_async_client called outside event loop") or return None, requiring callers to enforce the sync-context constraint documented at line 115 (redis-py will bind to event loop at first async operation, failing hard if that loop is closed).

- [cleanup] worker/tasks.py:305-352, 986-1073, 1191-1240, 1281-1338, 1392-1512 — advisory lock acquire-release pattern duplicated 5+ times (pg_try_advisory_lock / pg_advisory_unlock with identical structure) | fix: extract helper `async def _advisory_lock(session, key: str) -> bool` and context manager `@asynccontextmanager async def _acquire_advisory_lock(session, key: str)` to consolidate, reducing surface area for unlock-omission bugs.

- [cleanup] worker/storage.py:93 — unused import `_shutil` shadowing builtin shutil at line 93 | fix: remove line 93 `import shutil as _shutil` (shutil already imported at module level line 15).

- [cleanup] worker/anthropic_stream.py:40 — `client: Any` parameter unnecessarily broad; SDK exposes Anthropic class | fix: import Anthropic SDK type and annotate `client: anthropic.Anthropic` (or appropriate sync client class from SDK 0.40).

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — DB sessions via context managers, temp files cleaned in finally blocks, async Redis client singletons with event-loop binding, boto3 soft client via lazy singleton with cleanup on service shutdown. One SEV2 on _async_client initialization. |
| 2 Concurrency & scale | ok — async/to_thread used correctly for sync I/O, no hidden blocking calls in async def, advisory locks for Beat tasks + per-creator lock serialization (retrain, catalog-sync), bounded queries (limit, cutoff windows). Pattern DRY violation flagged as cleanup. |
| 3 Security & compliance | ok — per-creator isolation verified on all queries, no OAuth token logging (access_token passed by reference, not logged), safe error messages (no stack trace/token in user-facing emit), YouTube ToS respected (retention purges with hard cutoffs). |
| 4 Clip-quality | n/a (worker is orchestration/scheduling, not clip-scoring) |
| 5 Anthropic SDK | ok — cache hit/miss + input tokens surfaced on message_start, token deltas forwarded, usage dict constructed defensively with getattr fallbacks for older SDK versions, tools kwarg dropped safely when None. Streaming integration tested (anthropic_stream.py wraps sync context manager correctly). |
| 6 Cleanliness & typing | ok with 2 cleanup items — all function signatures typed, no print/TODO/commented-out code, logger calls safe. One `Any` broad type for client param, one unused shadowing import. |
| 7 Error handling / API | n/a (worker module has no HTTP routers; tasks swallow Redis errors, re-raise logical errors for retry). |
| 8 Config & paths | ok — all config accessed via settings singleton, S3 URIs parsed safely, local paths via expanduser().resolve(), .env.example covers all used settings. |

## Module verdict

clean with 2 easily-fixed cleanup items and 1 SEV2 resource initialization issue that is (needs-runtime-confirmation) — _async_client's behavior when called from sync context may be benign in practice if all callers enforce the async-only contract, but the code path is reachable and could silently bind a client to a dead loop.

