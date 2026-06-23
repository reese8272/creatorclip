# worker — assessed 2026-06-16

## Findings

- [SEV2] worker/tasks.py:130–134, 151–153, 170–172 — `except SoftTimeLimitExceeded:
  raise` in `ingest_video` / `transcribe_video` / `build_signals` re-raises BEFORE
  the generic `except Exception` handler (136/155/174) that runs
  `_set_status(video_id, IngestStatus.failed)`. On soft-timeout the refund fires
  (`RefundOnFailureTask.on_failure`) but `ingest_status` stays `running` forever —
  the UI shows a perpetual spinner and the video can never be retried by the user |
  fix: call `run_async(_set_status(video_id, IngestStatus.failed))` inside the
  `SoftTimeLimitExceeded` branch before re-raising (soft limit leaves ~300 s of
  headroom before the hard limit; a 1-row UPDATE fits easily). Carried forward from
  2026-06-09; UNCHANGED in HEAD (re-verified: the grep-matched `_set_status` calls
  are all in the generic handler, not the soft-timeout branch).

- [SEV2] worker/tasks.py:373/415, 1295/1378, 1500/1525, 1590/1640, 1737/1877,
  1903/1983 — six sites use session-scoped `pg_try_advisory_lock` with the unlock in
  a `finally` INSIDE the `async with db.AdminSessionLocal()` block. If the unlock
  `session.execute` itself fails (connection reset, or the session is in
  PendingRollback after a failed commit — e.g. the per-creator `session.commit()` at
  1375 in `_poll_clip_outcomes_async` is UNGUARDED by a rollback and would propagate
  straight into its `finally` at 1377), the pooled connection returns to the pool
  with the lock STILL HELD and every later Beat tick skips ("advisory lock held")
  until the connection is recycled. db.py has NO pool-reset handler (re-verified:
  no `pg_advisory_unlock_all`, no `PoolEvents.reset`; the only event listener is the
  RLS `after_begin` at db.py:132) | fix: register a SQLAlchemy `PoolEvents.reset`
  listener on both engines emitting `SELECT pg_advisory_unlock_all()`, OR migrate
  these six to `pg_advisory_xact_lock` (auto-releases on commit/rollback — the
  pattern `_build_dna_async` already uses at tasks.py:1140). Note the refresh sweep
  (1958/1971/1980) and retrain DID add `rollback()` handlers, so their finally is
  now safer; `_poll_clip_outcomes_async`'s commit at 1375 is the live unguarded
  path. Carried forward; structurally UNCHANGED in HEAD.

- [SEV2] worker/tasks.py:1496–1548 — `_purge_stale_source_media_async` acquires the
  advisory lock, collects targets, then releases the lock in the `finally` at
  1523–1527 BEFORE the R2 delete loop (1533–1540) and the `UPDATE ... source_uri=NULL`
  (1545–1547). Two concurrent ticks can both pass the check, read the same targets,
  and race the deletes/updates — safe today only because both ops happen to be
  idempotent; the lock protects only the target read, not the purge work | fix: hold
  the lock across the whole sweep (release after the final commit), or accept the
  race and delete the lock with a comment — the current half-lock is misleading.
  Carried forward; UNCHANGED in HEAD.

- [SEV2] worker/tasks.py:1328–1375 (poll) and 1930–1985 (refresh) —
  `_poll_clip_outcomes_async` holds one DB session + the advisory lock across every
  `get_video_stats` YouTube round-trip (line 1348, inside the nested per-outcome
  loop); `_refresh_youtube_analytics_async` does the same across an UNBOUNDED
  all-creators sweep (`select(Creator)` with no limit at 1915–1921) and per creator
  runs `select(Video).where(creator_id)` with NO LIMIT (1943–1944). At hundreds of
  creators × videos this pins a pooled connection for minutes-to-hours per tick and
  can starve API requests (needs-runtime-confirmation for exact pool pressure) | fix:
  collect outcomes + tokens under the lock, close the session during the YouTube
  loop, reopen a short session per-creator for the commit (the shape
  `_purge_stale_source_media_async` already uses for R2); for refresh, bound the
  per-creator video sweep (e.g. `last_fetched ASC LIMIT 100` per tick). The
  per-creator `session.commit()` at 1375/1951 (good) is present but the session is
  still held across the whole loop. Carried forward; UNCHANGED in HEAD.

- [SEV2] worker/tasks.py:828, 973, 1060 — ffmpeg encodes run via
  `asyncio.to_thread(render_clip_file / render_cleaned_clip_file, ...)`. On
  `SoftTimeLimitExceeded` the main loop raises (refund/retry fires) but the thread +
  ffmpeg subprocess keep burning CPU. clip_engine/render.py now bounds the burn with
  `subprocess.run(..., timeout=...)`, but `start_new_session=True` is still absent so
  the orphan encode cannot be killed early and overlaps the retry's encode on the
  same box | fix (cross-module, owned by clip_engine): add `start_new_session=True`
  and kill the process group on cancellation; worker side needs no change once that
  lands. Carried forward, partially mitigated (timeout present).

- [SEV2] worker/tasks.py:207–214, 217–231, 1003 — `clean_clip` / `edit_clip` /
  `_edit_clip_async` take only `clip_id` (+`cut_segments`); the worker never
  re-verifies clip ownership. Authorization lives solely in routers/clips.py. A
  malformed/forged Celery message re-encodes any clip. Defence-in-depth gap, not a
  current exploit (broker not internet-facing) | fix: add `creator_id` to the task
  signature (mirrors `generate_chapters` which already takes `creator_id`) and assert
  `clip.creator_id == cid` at entry. Carried forward; UNCHANGED.

- [SEV2] worker/tasks.py:196–204, 207–231 — render/clean/edit re-renders are
  invisible to the billing ledger: `render_clip` is not a `RefundOnFailureTask` (by
  design — minutes charged at ingest) but a creator hitting clean/edit N times burns
  N ffmpeg encodes with zero ledger entry or quota | fix: record the free-by-design
  decision in docs/DECISIONS.md, or add a per-re-render ledger debit/quota check
  before enqueue. Carried forward; still undocumented.

- [SEV2] worker/tasks.py:50–64 — `_thumb_redis()` async-Redis singleton has no
  loop-binding guard (contrast worker/progress.py:142–165 `_async_client`, which
  rebuilds on loop mismatch and resets on failure). Test-only friction today (one
  loop per worker process in prod), but the inconsistency is a trap if the worker
  loop is ever recreated | fix: track `_THUMB_REDIS_LOOP` and rebuild on mismatch —
  copy the 6 lines from progress.py. Carried forward; UNCHANGED.

- [cleanup] worker/tasks.py:808, 961, 1048 (`import tempfile` ×3, already imported at
  line 13); 930 (`from config import settings as _s` inline alias, also at line 56) —
  dead duplicate imports | fix: delete the inner imports; use module-top names.
  Carried forward; UNCHANGED.

- [cleanup] worker/tasks.py — file is 2976 lines; six brief-style tasks share the
  same ~120-line shape (emit step → load DNA/identity/transcript → `asyncio.to_thread`
  Claude → parse JSON → emit done/error) | fix: extract a `worker/_brief_runner.py`
  helper (job_id, load_context_fn, call_fn, parse_fn, stage) — cuts ~500 lines.
  Carried forward.

- [cleanup] worker/storage.py:93 — `import shutil as _shutil` inside `delete_prefix`
  duplicates the module-top import; storage.py:29 — `def _r2():` missing return
  annotation; storage.py:139 — `alocal_path` duplicates `local_path`'s
  tempfile/download logic (102+) | fix: drop the inner import, annotate the client
  return, and have `alocal_path` delegate the s3 branch to `asyncio.to_thread` over
  shared helpers. Carried forward.

- [cleanup] worker/celery_app.py:88–102 — `_shutdown_worker_loop` closes the engine
  (`db.dispose_engine`) and the youtube HTTP client (`_http.aclose`) but never closes
  `worker.progress._AIO` (a `progress.aclose()` exists at progress.py:269 and is
  unused here) nor `worker.tasks._THUMB_REDIS`; shutdown-time connection leak only |
  fix: also run `progress.aclose()` and aclose the thumb singleton in the hook.
  Carried forward.

- [cleanup] worker/tasks.py:2762–2764 — `_analyze_hook_async` issues one
  RetentionCurve query per other video (N+1, capped at 20) | fix: single query
  `WHERE video_id IN (...)` grouped in Python. Carried forward.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | 3 SEV2 (advisory-lock leak on pooled connection ×6 sites; purge lock released before work; ffmpeg orphan on soft-timeout — now timeout-bounded). Temp media cleaned in `finally`; sessions via context manager everywhere. |
| 2 Concurrency & scale | 2 SEV2 (session+lock held across YouTube API loops in poll + unbounded all-creators/all-videos refresh sweep; ffmpeg overlap). Loop-singleton pattern (Issue 39) is sound; all blocking work goes through `asyncio.to_thread`. |
| 3 Security & compliance | RESOLVED: prior SEV1 #4 (creator email in `expire_trials` log) is CLOSED — commit e12111f (Issue 138) dropped `Creator.email` from the SELECT (tasks.py:1675 now selects `id, trial_ends_at, minutes_balance`) and the log line at 1685 is `creator=%s trial_ends_at=%s`, PII-free (comment at 1680 records the invariant). Grepped all 64 `logger.*` calls in worker/: no email/token/name/secret leaks; the two `Cannot get token`/`no valid token` warnings (1332, 1760) log only `creator_id` + a generic exception, never the token. 1 SEV2 remains (clean/edit tasks lack ownership re-check — defence-in-depth). Per-creator isolation intact; ToS purges (source media, 30-day analytics) implemented. |
| 4 Clip-quality | n/a (worker schedules; scoring lives in clip_engine/ — render correctly uses the `setup_start_s` fallback). |
| 5 Anthropic SDK | ok — anthropic_stream.py forwards cache/token/thinking deltas, returns a usage dict for caller-side logging, swallows per-event Redis hiccups without losing `get_final_message()`; no virality strings. |
| 6 Cleanliness & typing | 5 cleanup (duplicate imports; brief-runner extraction at 2976 lines; storage DRY/typing; shutdown not closing Redis singletons; hook N+1). |
| 7 Error handling / API | n/a (not a router). Emit payloads carry `exc_type`/string only, never raw DB errors to the wire. |
| 8 Config & paths | ok — all config via pydantic-settings (REDIS_URL, retention/staleness/soft-time-limit/filler thresholds); relative paths resolved; temp files via `tempfile`. |

## Module verdict

NEEDS-WORK — the prior SEV1 (PII in `expire_trials` log) is verified CLOSED by
Issue 138 (commit e12111f). No new defects introduced; e12111f is the only worker
change since the prior assessment. The structural SEV2s from 2026-06-08/09 are all
still present and re-verified unchanged: advisory-lock leak across six session-scoped
sites (no `pg_advisory_unlock_all` pool-reset in db.py), soft-timeout leaves
`ingest_status=running`, purge lock released before the R2 sweep, session held across
YouTube API loops + unbounded refresh sweep, ffmpeg orphan (now timeout-bounded),
clean/edit lacking ownership re-check. A single `pg_advisory_unlock_all` pool-reset
listener in db.py (or migrating the six to `pg_advisory_xact_lock`) plus the
soft-timeout `failed`-status fix would close the highest-risk half of the list.

## Issue 75 Reconciliation (2026-06-23)

| Finding | Disposition |
|---|---|
| [SEV2] SoftTimeLimitExceeded leaves ingest_status=running (worker/tasks.py:130-134) | → tracked in Issue 82 (async migration wave 2 — worker correctness cluster) |
| [SEV2] advisory lock leak across 6 session-scoped sites | → tracked in Issue 82 |
| [SEV2] purge lock released before R2 sweep (worker/tasks.py:1496-1548) | → tracked in Issue 82 |
| [SEV2] session held across YouTube API loops (worker/tasks.py:1328-1375, 1930-1985) | → tracked in Issue 82 |
| [SEV2] ffmpeg orphan on SoftTimeLimitExceeded (worker/tasks.py:828,973,1060) | → tracked in Issue 82 |
| [SEV2] clean/edit tasks lack ownership re-check (worker/tasks.py:207-214) | → tracked in Issue 231 (worker tenant tasks under RLS) |
| [SEV2] re-renders invisible to billing (worker/tasks.py:196-204) | → wont-fix: free-by-design decision logged in docs/DECISIONS.md (re-renders charged at ingest; per Issue 208 rationale) |
| [SEV2] _thumb_redis() no loop-binding guard (worker/tasks.py:50-64) | → tracked in Issue 82 |
| [cleanup] duplicate imports in tasks.py | → tracked in Issue 109 (deferred design cleanups) |
| [cleanup] 2976-line tasks.py (_brief_runner extraction) | → tracked in Issue 82 |
| [cleanup] storage DRY/typing (worker/storage.py) | → tracked in Issue 109 |
| [cleanup] shutdown not closing Redis singletons | → tracked in Issue 82 |
| [cleanup] hook N+1 (worker/tasks.py:2762-2764) | → tracked in Issue 109 |
