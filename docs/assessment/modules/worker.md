# worker — assessed 2026-05-31

Slice: `worker/__init__.py`, `worker/anthropic_stream.py`, `worker/celery_app.py`,
`worker/progress.py`, `worker/schedule.py`, `worker/storage.py`, `worker/tasks.py`.

## Wave-9 + Issue 108 re-verification

All 13 declared fixes confirmed in place:

| Fix | Location | Status |
|---|---|---|
| `_transcribe_async` idempotency probe | worker/tasks.py:479–496 | ok |
| `_signals_async` idempotency probe | worker/tasks.py:570–583 | ok |
| `_ingest_async` `.wav` short-circuit | worker/tasks.py:392–404 | ok |
| `generate_clips` uses `base=RefundOnFailureTask` | worker/tasks.py:161 | ok |
| Advisory lock on `_retrain_preference_async` | worker/tasks.py:305–352 (try_advisory_lock + finally unlock) | ok |
| Advisory lock on `_poll_clip_outcomes_async` | worker/tasks.py:952–1039 | ok |
| Advisory lock on `_refresh_youtube_analytics_async` | worker/tasks.py:1501–1589 | ok |
| Advisory lock on `_purge_stale_source_media_async` | worker/tasks.py:1157–1186 | ok |
| Advisory lock on `_purge_stale_youtube_analytics_async` | worker/tasks.py:1247–1303 | ok |
| Advisory lock on `_sync_channel_catalog_async` | worker/tasks.py:1361–1478 | ok |
| `SoftTimeLimitExceeded` no longer triggers retry | worker/tasks.py:110–114, 131–133, 150–152 | ok |
| Redis singletons carry `socket_timeout=2.0` | worker/progress.py:103–108, 157–162 | ok |
| `worker/storage._local_root()` uses `.resolve()` | worker/storage.py:52 | ok |
| `worker/__init__.py` module docstring | worker/__init__.py:1 | ok |
| Typed `on_failure` signature | worker/tasks.py:66–73 | ok |
| `by_creator: dict[uuid.UUID, list[ClipOutcome]]` | worker/tasks.py:983 | ok |
| Parameterized `messages`/`tools` in anthropic_stream | worker/anthropic_stream.py:43–44 | ok |
| `from datetime import timedelta` in schedule.py | worker/schedule.py:21 | ok |

Carry-over findings from the prior pass: the orphan-mp4 retention finding
remains open (the `.wav` short-circuit closes the re-encode path but not the
orphan-cleanup gap). All other prior SEV2s are closed.

## Findings

- [SEV2] worker/tasks.py:437 (`_ingest_async` final commit) — when the audio
  WAV is committed as the new `source_uri`, the ORIGINAL uploaded mp4 (e.g.
  `source/{creator}/{youtube_id}.mp4`, set by `routers/videos.py:239` /
  `routers/clips.py:275`) is overwritten in the row and becomes unreachable
  to the purge sweep. `_purge_stale_source_media_async`
  (worker/tasks.py:1167–1175) only enumerates `Video.source_uri` values, so
  it never sees the orphan and never deletes it. At hundreds of creators ×
  one upload each this is **unbounded R2 storage growth + a YouTube ToS
  exposure** — source media must be purged within
  `SOURCE_MEDIA_RETENTION_HOURS` (compliance §III.E.4.b) and an orphan that
  the purge can't see is, by definition, never purged. The Wave-9 `.wav`
  short-circuit at worker/tasks.py:392–404 closes the *re-encode* hole but
  not the *cleanup* hole — by the time the short-circuit fires, the mp4 has
  already been orphaned by the prior successful run. | fix: capture
  `prior_source_uri = video.source_uri` at function entry; after the final
  commit (worker/tasks.py:445), if `prior_source_uri != audio_uri` and
  `prior_source_uri` is the mp4 (`startswith("source/")` or endswith `.mp4`),
  call `await adelete_file(prior_source_uri)` inside a try/except so a
  delete failure doesn't unwind the committed ingest. Same pattern used by
  the Wave-3 cleanup elsewhere in the codebase.

- [SEV2] worker/tasks.py:1530–1549 (`_refresh_youtube_analytics_async`) —
  unbounded fan-out. `select(Creator).order_by(...)` then `list(...)`
  pulls EVERY creator into memory; per creator,
  `select(Video).where(creator_id == creator.id)` then `list(...)` pulls
  every video for that creator into memory. At the target scale of "hundreds
  of creators" with mature creators carrying thousands of videos each, this
  is two unbounded `fetchall`s nested — a single Beat tick allocates
  O(creators × videos) Python objects and a session that lives for the
  whole sweep, while running tens of thousands of synchronous-style
  `await sync_video_analytics(...)` calls in series under one DB
  transaction. The advisory lock guarantees only one run is happening,
  which makes this slower under load, not safer. CLAUDE.md §"Bounded work"
  prohibits unbounded fan-out. | fix: paginate the outer creators query
  (`LIMIT 50 OFFSET ...` keyset-paginated by
  `(last_analytics_refreshed_at, id)`, the same ordering already used) and
  commit + close the session at each page boundary so DB connections /
  memory don't pin for the full sweep. The Beat tick is hourly; spreading
  one cohort per tick is fine and matches the "fair ordering" intent of
  Issue 47. For the inner per-creator videos loop, cap to the N most-recent
  videos per refresh (mirrors the 50-row cap already used in
  `_generate_improvement_brief_async` at worker/tasks.py:1692) — older
  videos' analytics change slowly and don't need daily re-fetch.
  `(needs-runtime-confirmation)` — a Locust run at 200 creators × 1k
  videos each would settle the worst-case wall-clock + memory.

- [SEV2] worker/tasks.py:64–94 (`RefundOnFailureTask.on_failure`) — STILL
  fragile under prefetch > 1. `on_failure` is invoked from the worker's main
  thread; `run_async(refund_for_video(...))` dispatches onto the singleton
  loop via `loop.run_until_complete`. With `worker_prefetch_multiplier=1`
  + `acks_late` this is safe (one task at a time per process), but if
  prefetch is ever raised — a routine perf tuning move that doesn't look
  load-bearing — `run_until_complete` can race against the loop running
  another task body. The Wave-9 batch did not touch this. | fix: either
  pin a hard test that asserts `worker_prefetch_multiplier == 1` lives
  forever, or rewrite the refund to open its own sync SQLAlchemy session
  so it doesn't depend on the singleton loop being free.
  `(needs-runtime-confirmation)` — load test at prefetch=4 would expose
  the race.

- [SEV2] worker/celery_app.py:96–99 (`_shutdown_worker_loop`) — calls
  `_LOOP.run_until_complete(db.dispose_engine())` then
  `_LOOP.run_until_complete(_http.aclose())` sequentially. If
  `dispose_engine()` raises (e.g. PgBouncer already torn down by SIGTERM
  during a k8s rolling deploy), the `_http.aclose()` never runs and the
  httpx singleton's connection pool leaks. The `try/finally` correctly
  closes the loop itself, but the httpx client stays half-open until the
  process dies. | fix: wrap each `run_until_complete` in its own
  try/except that logs and continues, so a failure shutting one client
  down doesn't strand the other. Same pattern the prior assessment's
  `progress.aclose` already uses (worker/progress.py:280–301).

- [cleanup] worker/tasks.py:188–193 (`purge_stale_source_media` sync
  wrapper) — Beat-task sync wrappers (`purge_stale_source_media`,
  `purge_stale_youtube_analytics`, `refresh_youtube_analytics`,
  `poll_clip_outcomes`) all call `run_async(...)` with no exception
  handling, while the bound retry-able tasks above them wrap in try/except
  to set `IngestStatus.failed` etc. If a Beat task raises, Celery logs the
  failure but no observability event is emitted (no `aemit("error", ...)`
  because Beat tasks have no SSE consumer). This is consistent and probably
  correct, but worth documenting alongside the existing module docstring
  so a future maintainer doesn't add a misguided retry wrapper. | fix:
  one-line comment above each Beat sync wrapper noting "intentional —
  Beat re-fires on the next tick, no retry needed".

- [cleanup] worker/tasks.py:1138, 1237 — `from datetime import timedelta`
  is imported inside `_purge_stale_source_media_async` and
  `_purge_stale_youtube_analytics_async` even though it's already imported
  at module level (worker/tasks.py:16: `from datetime import UTC,
  datetime`). DRY / KISS — function-local re-imports were a workaround
  for older test patterns, not needed here. | fix: drop the inner imports
  and add `timedelta` to the module-level `from datetime import ...`.

- [cleanup] worker/tasks.py:697, 932 — function-local imports
  (`import tempfile`, `import statistics`, `from collections import
  defaultdict`, `from datetime import datetime, timedelta` re-import) are
  scattered through task bodies. The top-level imports already cover
  `tempfile` (worker/tasks.py:13). Function-local imports were probably
  added to keep Celery worker boot fast, but the modules in question are
  cheap stdlib — the cost is invisible. | fix: hoist `tempfile`,
  `statistics`, `defaultdict` to module-level; leaves only the genuinely
  expensive lazy imports (`youtube.data_api`, `youtube.oauth`,
  `improvement.brief`) function-local.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | 2 findings (orphan-mp4 retention gap on `_ingest_async`; httpx leak path on worker shutdown) |
| 2 Concurrency & scale | 2 findings (unbounded fan-out in `_refresh_youtube_analytics_async`; refund `on_failure` fragile under prefetch > 1) |
| 3 Security & compliance | orphan-mp4 finding doubles as a ToS exposure (flagged under lifecycle). All `logger.*` calls scanned — no token / PII in any log line. `creator_id` is logged as a UUID, which is opaque. Per-creator queries are correctly filtered in `_generate_improvement_brief_async` (worker/tasks.py:1689–1693) and other creator-scoped paths; `AdminSessionLocal` use in Beat tasks is intentional global scope. |
| 4 Clip-quality | n/a (worker dispatches to `clip_engine`; no scoring math lives here) |
| 5 Anthropic SDK | ok (`anthropic_stream.stream_and_emit` uses `messages.stream(...)`; cache stats emitted via `message_start.usage` event at anthropic_stream.py:110–123; usage_dict includes `cache_read` + `cache_creation` at anthropic_stream.py:94–99; tools forwarded conditionally at anthropic_stream.py:69–76 so web_search lands correctly) |
| 6 Cleanliness & typing | 3 cleanup findings (function-local re-imports, Beat sync wrapper docstrings, scattered stdlib imports) |
| 7 Error handling / API | n/a (no routers in this module) |
| 8 Config & paths | ok (`_local_root` now resolves; LOCAL_MEDIA_DIR has the production validator per the prior closure) |

## Module verdict

**NEEDS-WORK** — no BLOCKERs; the Wave-9 + Issue 108 batch landed cleanly and
closed 11 of the 12 prior SEV2s (every advisory-lock, idempotency-probe,
typing, redis-timeout, and `.resolve()` fix is verified in place). The remaining
open items are: (1) the orphan-mp4 retention gap — the `.wav` short-circuit
prevents re-encode on retry but the *original* mp4 that the WAV replaced is
still orphaned in R2 after a normal successful first run, which violates the
ToS purge promise the `_purge_stale_source_media_async` sweep is built to
honor; (2) `_refresh_youtube_analytics_async` is the one Beat task still
doing two nested unbounded `fetchall`s under one session, which doesn't break
at today's scale but is the predicted next bottleneck at 200+ creators with
mature catalogs; (3) two carry-over fragilities (refund-thread coupling under
prefetch > 1; httpx leak path on worker shutdown). Each is small, local,
and bounded — none ships a cross-tenant leak or money-loss path.
