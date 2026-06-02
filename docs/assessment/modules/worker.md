# worker — assessed 2026-06-02

## Findings
- SEV2 worker/tasks.py:1603-1606 — `_refresh_youtube_analytics_async` fetches ALL videos per creator without pagination or limit. On channels with 1000+ videos, this unbounded fetchall pins the connection and risks memory exhaustion. Fix: add `.limit(settings.DNA_LONGS_CAP)` or implement streaming per-video fetch with periodic commits.
- SEV2 worker/progress.py:145-165 — `_async_client()` rebuilds the async Redis singleton on loop mismatch but never logs the rebuild. This silent reconstruction masks connection pool thrashing under test scenarios. Fix: add `logger.debug("async redis client rebuilt on loop mismatch")`.
- cleanup worker/tasks.py:889-895 — `asyncio.to_thread(generate_brief, ...)` inside `_build_dna_async` dispatches to sync Anthropic context manager but passes `job_id` (always truthy in production) to control progress emission. Test-only callers must pass `job_id=None` explicitly. This coupling works but should be documented in a docstring parameter. Fix: update `_build_dna_async` docstring to note job_id=None disables progress events.

## Rubric coverage
| Category | Status |
|---|---|
| Resource lifecycle | PASS — all DB sessions via context manager; cleanup guaranteed in `finally` blocks; temp media (`.wav`, `.mp4`) removed via `.unlink(missing_ok=True)`. HTTP client aclose() wired in celery_app.py shutdown. |
| Concurrency & scale | NEEDS-WORK — unbounded `select(Video).where(creator_id)` at line 1604 loads all videos for each creator in refresh loop. worker_prefetch_multiplier=1 + task_reject_on_worker_lost + advisory locks all correct. |
| Security & compliance | PASS — per-creator UUID filters on every analytics/video query; all SQL parameterized via SQLAlchemy; no PII in logs (exception args stripped in error events); creator_id scoped strictly in AdminSessionLocal queries. |
| Clip-quality | N/A — not a clip-scoring module. |
| Anthropic SDK | PASS — prompt caching configured with `cache_control: ephemeral` breakpoints in dna/brief.py and improvement/brief.py; usage_dict (input, output, cache_read, cache_creation tokens) captured and emitted as cache/token progress events via stream_and_emit; SDK version managed in .env.example. |
| Code cleanliness & typing | PASS — all functions fully typed; no print(), TODO, or commented code blocks; zero duplicate logic (idempotency patterns consistently applied). Module docstrings explain Issue references. anthropic_stream.py correctly uses `getattr(..., default=0)` for backward-compat with older SDK responses. |
| Error handling | PASS — SoftTimeLimitExceeded re-raised immediately (no retry on terminal timeout); YouTubeAuthError terminal (no retry); ValueError (data gates) terminal; transient errors retry with jitter. RefundOnFailureTask.on_failure catches and logs refund errors (never re-raises). |
| Config & paths | PASS — all file paths absolute (LOCAL_MEDIA_DIR configured as absolute in .env); SOURCE_MEDIA_RETENTION_HOURS, YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS, TRANSCRIPTION_TIMEOUT_S, CELERY_SOFT_TIME_LIMIT_S all in .env.example. |

## Module verdict
NEEDS-WORK — unbounded SQL fetch on per-creator video roster in refresh_youtube_analytics poses memory risk under scale; redis client rebuild logging gap masks pool issues during test; documentation gap on job_id parameter coupling to progress emission. All issues are bounded (SEV2) and fixable without refactoring.
