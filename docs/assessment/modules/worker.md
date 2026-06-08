# worker — assessed 2026-06-08

## Findings

- [SEV1-FIXED] worker/tasks.py:352 — `_retrain_preference_async` previously used
  `db.AsyncSessionLocal()` without setting `session.info["creator_id"]`, causing
  RLS to silently return zero ClipFeedback rows under the production role split.
  Now correctly uses `db.AdminSessionLocal()` at line 364, matching the pattern
  at lines 401, 424, 1114, 1272, etc. | status: FIXED in HEAD.

- [SEV1-FIXED] worker/tasks.py:2003 — `_generate_improvement_brief_async` previously
  used `db.AsyncSessionLocal()` without stamping creator_id, causing the brief
  query + VideoMetrics join to return empty under role split. Now correctly stamps
  `session.info["creator_id"] = str(cid)` at line 2035 BEFORE any query (matches
  the RLS `after_begin` listener pattern). The session type was intentionally kept
  as AsyncSessionLocal (not AdminSessionLocal) because brief generation is
  user-scoped, not a cross-tenant sweep. | status: FIXED in HEAD.

- [SEV1-FIXED] worker/tasks.py:874+1006 — `_clean_clip_async` and `_edit_clip_async`
  previously shared `clip.cleaned_render_uri` as idempotency key, causing a race
  where clean→edit (or edit→clean) before confirm would silently drop the second
  operation's work. Now the router checks for pending `cleaned_render_uri` and
  rejects with HTTP 409 at lines 361–368 (clean endpoint) and 549–556 (edit
  endpoint) with a descriptive error guiding the user to confirm or discard first.
  The 409 response mirrors the WAI "one pending edit at a time" UX. | status: FIXED
  in HEAD.

- [SEV2] worker/tasks.py:1498–1527 — `_purge_stale_source_media_async` acquires an
  advisory lock, collects R2 URIs under lock, then releases the lock at line
  1525 in a `finally` BEFORE the actual R2 delete loop at line 1537. Two concurrent
  Beat ticks (or a redelivery) can both pass the lock check, both read the same
  targets, and race the R2 deletes + database UPDATEs. R2 delete is idempotent and
  the UPDATE is too, so today this is safe-by-luck; the lock is doing nothing. |
  fix: move the lock-release to a finally that wraps the entire function body
  (after the R2 loop at line 1541 and the database update at line 1547), or use
  `pg_advisory_xact_lock` inside a single transaction the whole sweep is part of
  (matches `_build_dna_async:1122` pattern).

- [SEV2] worker/tasks.py:1290–1375 — `_poll_clip_outcomes_async` holds the session
  connection AND the advisory lock for the duration of every YouTube API round-trip.
  The `get_video_stats(...)` call at line 1348 runs inside the loop while the
  session is open and locked. With 1000 outcomes per tick × ~200ms each, that's
  >3 minutes holding a pooled connection. With pool_size=15 across replicas, a
  single Beat task can starve API requests. | fix: collect all outcomes + access
  tokens under lock (lines 1303–1326), close the session, make all YouTube calls
  outside the session, then reopen a session for per-creator commits. Matches the
  shape used in `_ingest_async` and other tasks that separate data collection from
  long-latency external calls.

- [SEV2] worker/tasks.py:339–397 + 1290–1362 + 1489–1527 + 1568–1640 + 1682–1876
  + 1846–1933 — `pg_try_advisory_lock` is session-scoped (= PostgreSQL backend
  connection = pooled handle), but the unlock is wrapped in `try/finally` that
  runs INSIDE the `async with db.AsyncSessionLocal()` block. If the unlock
  execute itself fails (network blip, cancelled task, connection reset),
  the connection returns to the pool with the lock STILL HELD, and the next caller
  that checks out that connection inherits a phantom lock on `retrain:<other-uuid>`
  or another key. SQLAlchemy's `AsyncSession.__aexit__` does not emit
  `pg_advisory_unlock_all` on return-to-pool. Same pattern appears at all six sites
  listed above. | fix: register a SQLAlchemy `reset` event on both engines (in
  db.py) that emits `SELECT pg_advisory_unlock_all()` when a connection is
  returned to the pool (one-line defense in depth), OR migrate all of these to
  `pg_advisory_xact_lock` which auto-releases on commit/rollback (the xact variant
  is what `_build_dna_async:1122` already uses successfully).

- [SEV2] worker/tasks.py:810, 961, 1048 — ffmpeg shellouts run via
  `asyncio.to_thread(render_clip_file, ...)` / `render_cleaned_clip_file`. On
  Celery `SoftTimeLimitExceeded` the worker raises in the main loop but the
  underlying thread (and the ffmpeg subprocess it spawned via `subprocess.run`)
  keeps executing — zombie ffmpeg processes can pile up after repeated soft-timeouts.
  The temp file in the `finally` is unlinked but the subprocess still holds it open
  until kernel cleanup, and the `RefundOnFailureTask` refund fires while the encode
  is still burning CPU. The `clip_engine/render.py` module's `subprocess.run` calls
  do NOT use `start_new_session=True`, so only the parent Python process is in a
  killable process group. | fix: modify `clip_engine/render.py::_run` and
  `render_cleaned_clip_file` to accept a `timeout_s` parameter (already computed
  per clip) and pass `start_new_session=True` to all `subprocess.run` calls. This
  puts ffmpeg in its own process group, so `asyncio.to_thread` timeout or
  `SoftTimeLimitExceeded` can `os.killpg()` the entire subprocess tree. Timeout
  values are already computed (render_timeout_s at line 171 of render.py);
  thread-timeout coordination matches the CELERY_SOFT_TIME_LIMIT_S architecture
  in docs/DEPLOYMENT.md.

- [SEV2] worker/tasks.py:1290–1357 — `_poll_clip_outcomes_async` is a Beat task
  that holds the session + lock while making YouTube API calls. Secondary finding:
  the per-creator retry loop at line 1328–1375 commits per creator (line 1375),
  which is correct for partial-failure resilience. However, if a YouTube call
  fails mid-batch, the next retry tick will re-fetch all outcomes from line 1303
  (a concurrent query while another instance holds the lock will timeout / return
  nothing). The intent was stated in comments at line 1373–1374 ("partial progress
  survives"), but the architecture (single read + recheck under lock per tick)
  doesn't guarantee it. | fix: (deferred to next phase — this is defensive
  hardening, not a correctness bug today). Consider a "mark fetched" flag per
  outcome so retries skip already-polled rows, or split the poll into per-creator
  sub-tasks that can retry independently.

- [SEV2] worker/tasks.py:2152–2153 — `_generate_video_analysis_async`
  re-imports `Sequence` and `Any` inside the function despite both being
  imported at module top (lines 15, 18). | fix: delete the inner imports at lines
  2152–2153.

- [SEV2] worker/tasks.py:790, 943, 1030 — `import tempfile` is inlined INSIDE
  the function body three times despite being already imported at module top
  (line 13). | fix: delete the three nested `import tempfile` lines and use the
  module-top import.

- [SEV2] worker/tasks.py:912 — `from config import settings as _s` inlined
  inside `_clean_clip_async` despite being imported elsewhere with the same
  alias pattern. | fix: establish a single module-level alias shape
  (recommend `from config import settings` at module top without alias, or
  `from config import settings as _settings` consistently) and use it everywhere.

- [SEV2] worker/tasks.py:985–1069 — `_edit_clip_async` has no `creator_id`
  argument and the worker doesn't re-verify clip ownership before re-encoding.
  Authorization lives entirely in the router (routers/clips.py:553). If a
  future code path enqueues this task directly (or a malformed Celery message
  arrives), the worker happily reads any clip's render_uri and writes a new
  cleaned_render_uri. This matches the existing worker convention (clip_id-only
  signatures), but the new tasks should at least log `(clip_id, creator_id)` for
  audit, matching the `analyze_hook` / `generate_chapters` shape. | fix: add
  `creator_id` to the task signature (mirrors `generate_chapters` at line 2807),
  assert `clip.creator_id == cid` at the start of `_edit_clip_async`, raise a
  permanent error otherwise. Defence-in-depth, not a current exploit.

- [SEV2] worker/tasks.py:202 — `render_clip` does NOT inherit
  `RefundOnFailureTask` even though it consumes minutes via the same upstream
  ledger (the ingest tasks all use it). If a clip render exhausts retries
  (3 × 60s), no refund fires — the creator was already charged at ingest. This
  matches the design (refund is per-VIDEO, not per-clip, and the clip's
  embedded minutes were already paid at ingest), but the same pattern leaves
  `clean_clip`/`edit_clip` (Issues 134/135) silently re-using minutes the
  ledger has no view of: a creator hitting "edit" 10× burns 10× the ffmpeg
  cost. | fix: confirm in DECISIONS.md that clean/edit re-renders are FREE by
  design (covered by the original video's minutes), or wire a tiny per-render
  cost into the ledger before enqueueing. Track in OFF_COURSE_BUGS.md and route
  to billing review.

- [SEV2] worker/tasks.py:53–64 — `_thumb_redis` singleton has no loop-binding
  guard (compare progress.py:146 `_async_client`, which rebuilds on loop
  mismatch). Pytest's per-test loop scope will hit `RuntimeError: no running
  event loop` if test 1 instantiates the singleton on loop A and test 2 reuses
  it on loop B. Production worker has one long-lived loop per process so this
  is test-only friction today; ditto if a future Celery prefork hook ever
  recreates the worker loop without nulling the singleton. | fix: lift the
  loop-binding rebuild pattern from `worker/progress.py::_async_client` (track
  `_THUMB_REDIS_LOOP`, rebuild on mismatch). Two extra lines of code.

- [cleanup] worker/tasks.py:2106 + 2625 + 2798 — section banners `# ── Title
  suggestions ──` / `# ── Hook analyzer ──` / `# ── Auto chapter markers ──`
  proliferate; the file is now 2919 lines and 13 task pairs. The brief-style
  tasks (title, thumbnail, hook, chapters, video_analysis, improvement_brief)
  all follow the same shape: emit step → fetch DNA/identity/transcript →
  asyncio.to_thread Claude → parse JSON → emit done. | fix: extract a
  `worker/_brief_runner.py` helper that takes (job_id, load_context_fn,
  call_claude_fn, parse_fn, stage_label) and runs the boilerplate. Cuts ~500
  lines and removes the duplicated emit-error-shape across six tasks. Pure
  KISS/DRY — no behaviour change.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | 3 SEV2 findings (advisory-lock-on-pooled-connection leak; lock released before work in purge; ffmpeg zombie on soft-timeout) — all carried forward from prior run. |
| 2 Concurrency & scale | 2 SEV2 findings (ffmpeg subprocess cancellation; `_poll_clip_outcomes` holds connection across YouTube API loop). Zombie subprocess issue is inherited; connection-holding issue is carried forward. |
| 3 Security & compliance | 3 SEV1s FIXED (RLS-blind sessions in retrain + improvement_brief, shared idempotency key for clean/edit). All three previous SEV1s are now resolved in HEAD. |
| 4 Clip-quality | n/a (worker is the scheduler — scoring lives in clip_engine/) |
| 5 Anthropic SDK | covered by anthropic_stream.py — clean (prompt caching, token usage logged, structured output, no virality strings); `tools=None` drop at line 75 is correct for older SDK. |
| 6 Cleanliness & typing | 4 findings (duplicate tempfile imports ×3; duplicate Sequence/Any imports; brief-runner extraction; settings alias consistency). |
| 7 Error handling / API | n/a (worker is not a router; emit shape is reviewed at progress.py and is safe — exc_type only, never exc.args) |
| 8 Config & paths | ok (all paths via Path / tempfile; STORAGE_BACKEND/REDIS_URL/SOURCE_MEDIA_RETENTION_HOURS/YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS/CELERY_SOFT_TIME_LIMIT_S all flow through pydantic-settings) |

## Module verdict

NEEDS-WORK — The three critical SEV1s from the prior run are now FIXED (RLS blindness,
idempotency collision). However, the six SEV2s regarding advisory locks, connection
pooling, ffmpeg subprocess lifecycle, and concurrent YouTube polling remain open.
The file also accumulates 4 cleanup findings (duplicate imports, brief-runner
extraction, settings alias). None block today's production deployment, but the
advisory-lock + connection-pooling pattern is structural and appears in six
locations — fixing it once in `db.py` via a `reset` event handler would harden
all of them simultaneously.
