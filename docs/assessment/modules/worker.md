# worker — assessed 2026-06-07

## Findings

- [SEV1] worker/tasks.py:346 — `_retrain_preference_async` uses `db.AsyncSessionLocal()`
  (app-role engine, subject to RLS via the `after_begin` listener) but never sets
  `session.info["creator_id"]`. Under the production role split (Issue 79 / alembic
  0010), `current_setting('app.creator_id', true)` returns NULL on every query,
  the RLS USING clause `creator_id = NULL` matches no rows, and the retrain
  silently sees zero ClipFeedback rows even when there are millions. Today this is
  masked because dev/single-role mode uses the BYPASSRLS migration role for both
  factories — the moment role split lands, retraining permanently no-ops for every
  creator. | fix: switch to `db.AdminSessionLocal()` (every other worker async helper
  already does this — lines 401, 424, 1114, 1272 etc.) OR set
  `session.info["creator_id"] = cid` before the first query. AdminSessionLocal is
  the canonical worker pattern.

- [SEV1] worker/tasks.py:1978 — same defect as :346 in
  `_generate_improvement_brief_async`. Uses `AsyncSessionLocal` without setting
  `session.info["creator_id"]`, so under role split the `select(ImprovementBrief)`
  + `select(VideoMetrics).join(Video).where(Video.creator_id == cid)` return empty,
  the row.status flips to `ready` with an empty analytics payload, and the creator
  sees a useless brief. | fix: switch to `AdminSessionLocal` (matches every other
  brief-style task: title-suggestions, thumbnail-concepts, hook, chapters, video
  analysis all use Admin).

- [SEV1] worker/tasks.py:874+1006 — `_clean_clip_async` and `_edit_clip_async`
  share `clip.cleaned_render_uri` as their idempotency key. The two tasks come
  from distinct user actions (auto-clean vs. user-supplied transcript cuts) and
  produce different outputs (`{id}_clean.mp4` vs. `{id}_edit.mp4`). If a user
  runs `POST /clean` and then changes their mind and runs `POST /edit` before
  hitting `/clean/confirm`, the edit task short-circuits at line 1006 with
  "Already edited" and the cleaned_render_uri still points at the clean version
  — the editor's `cut_segments` are silently dropped. The reverse (edit then
  clean) has the same shape. | fix: either (a) reject the second request at the
  router with 409 when a pending cleaned_render_uri exists, or (b) split into
  two columns (`auto_cleaned_render_uri` + `user_edited_render_uri`) so each task
  has its own idempotency key. Option (a) is the smaller diff and matches the
  "one pending edit at a time" UX shape the comments at :990 imply was intended.

- [SEV2] worker/tasks.py:1489-1509 — `_purge_stale_source_media_async` releases
  the advisory lock at line 1505-1509 immediately after the SELECT, BEFORE the
  R2 delete loop (1515-1522) and the source_uri null-out (1527-1529). Two
  concurrent Beat ticks (clock skew across replicas, manual `celery beat` for
  testing, or a redelivery) both pass the `acquired` check, both read the same
  `targets`, and both race the R2 deletes + UPDATEs. R2 delete is idempotent and
  the UPDATE is too, so today this is safe-by-luck, but the lock is doing
  nothing useful. Sister task `_purge_stale_youtube_analytics_async` (line
  1568-1624) holds the lock across all DELETEs correctly — match that pattern. |
  fix: move the lock-release to a finally AROUND the whole function body, not
  just the SELECT. Either keep the session open across the R2 loop (matches
  sister task) or use `pg_advisory_xact_lock` inside a single transaction the
  whole sweep is part of.

- [SEV2] worker/tasks.py:53-64 — `_thumb_redis` singleton has no loop-binding
  guard (compare progress.py:146 `_async_client`, which rebuilds on loop
  mismatch). Pytest's per-test loop scope will hit
  `RuntimeError: no running event loop` if test 1 instantiates the singleton on
  loop A and test 2 reuses it on loop B. Production worker has one long-lived
  loop per process so this is test-only friction today; ditto if a future
  Celery prefork hook ever recreates the worker loop without nulling the
  singleton. | fix: lift the loop-binding rebuild pattern from
  `worker/progress.py::_async_client` (track `_THUMB_REDIS_LOOP`, rebuild on
  mismatch). Two extra lines of code.

- [SEV2] worker/tasks.py:810,955,1042 — ffmpeg shellouts run via
  `asyncio.to_thread(render_clip_file, ...)` / `render_cleaned_clip_file`. On
  Celery `SoftTimeLimitExceeded` the worker raises in the main loop but the
  underlying thread (and the ffmpeg subprocess it spawned via
  `subprocess.run`) keeps executing — zombie ffmpeg processes can pile up after
  repeated soft-timeouts. The temp file in the `finally` is unlinked but the
  subprocess still holds it open until kernel cleanup, and the
  `RefundOnFailureTask` refund fires while the encode is still burning CPU. |
  fix: pass a process group + timeout into `render_clip_file` (subprocess
  helpers should `Popen` with `start_new_session=True` and accept a `timeout=`
  kwarg matching `CELERY_SOFT_TIME_LIMIT_S - 30s`), so ffmpeg gets SIGKILL on
  soft-timeout instead of orphaning. The `clip_engine.render` module owns the
  subprocess — worker only needs to forward the timeout.

- [SEV2] worker/tasks.py:1290-1357 — `_poll_clip_outcomes_async` calls
  `get_video_stats` (httpx) per outcome inside a loop that holds the session
  connection AND the advisory lock for the duration of every YouTube API
  round-trip. With 1000 outcomes per tick × ~200ms each, that's >3 minutes
  holding a pooled connection. With pool_size=15 across replicas, a single Beat
  task can starve API requests. | fix: collect all outcomes + access tokens
  first, release the session (and lock), make the YouTube calls outside the
  session, then reopen a session for the per-creator commit. Matches the shape
  used in `_purge_stale_source_media_async` even though that one has its own
  lock-scope issue above.

- [SEV2] worker/tasks.py:339-397 — `pg_try_advisory_lock` is session-scope
  (= PostgreSQL backend session = connection), but the unlock is wrapped in
  `try/finally` that runs INSIDE the `async with db.AsyncSessionLocal()` block.
  If `await session.execute(text("...unlock..."))` itself fails (network blip,
  cancelled task), the connection returns to the pool with the lock STILL HELD,
  and the next caller that checks out that connection inherits a phantom lock
  on `retrain:<other-uuid>`. SQLAlchemy's `AsyncSession.__aexit__` does not run
  `pg_advisory_unlock_all` on return-to-pool. Same shape at lines 1290-1362,
  1489-1509, 1568-1624, 1682-1826, 1846-1933. | fix: register a SQLAlchemy
  `reset` event on the engine that emits `SELECT pg_advisory_unlock_all()` when
  a connection is returned to the pool (one-line defense in depth), OR migrate
  all of these to `pg_advisory_xact_lock` which auto-releases on
  commit/rollback. The xact variant is what `_build_dna_async:1122` already uses
  successfully.

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

- [SEV2] worker/tasks.py:790,943,1030 — `import tempfile` is inlined INSIDE the
  function body three times despite being already imported at module top
  (line 13). Dead duplicate import; not a defect but the second occurrence
  signals copy-paste from the render_clip template into clean/edit. | fix:
  delete the three nested `import tempfile` lines.

- [SEV2] worker/tasks.py:985-1069 — `_edit_clip_async` has no creator_id
  argument and the worker doesn't re-verify clip ownership before re-encoding.
  Authorization lives entirely in the router (routers/clips.py:553). If a
  future code path enqueues this task directly (or a malformed Celery message
  arrives), the worker happily reads any clip's render_uri and writes a new
  cleaned_render_uri. This matches the existing worker convention (clip_id-only
  signatures), but the new tasks should at least log
  `(clip_id, creator_id)` for audit, matching the `analyze_hook` /
  `generate_chapters` shape. | fix: add `creator_id` to the task signature
  (matches generate_chapters at line 2807), assert `clip.creator_id == cid` at
  line 873/1004, raise a permanent error otherwise. Defence-in-depth, not a
  current exploit.

- [cleanup] worker/tasks.py:2152-2153 — `_generate_video_analysis_async`
  re-imports `Sequence` and `Any` inside the function despite both being
  imported at module top (lines 15, 18). | fix: delete the inner imports.

- [cleanup] worker/tasks.py:2106+2625+2798 — section banners `# ── Title
  suggestions ──` / `# ── Hook analyzer ──` / `# ── Auto chapter markers ──`
  proliferate; the file is now 2919 lines and 13 task pairs. The brief-style
  tasks (title, thumbnail, hook, chapters, video_analysis, improvement_brief)
  all follow the same shape: emit step → fetch DNA/identity/transcript →
  asyncio.to_thread Claude → parse JSON → emit done. | fix: extract a
  `worker/_brief_runner.py` helper that takes (job_id, load_context_fn,
  call_claude_fn, parse_fn, stage_label) and runs the boilerplate. Cuts ~500
  lines and removes the duplicated emit-error-shape across six tasks. Pure
  KISS/DRY — no behaviour change.

- [cleanup] worker/tasks.py:912 — `from config import settings as _s` inlined
  inside `_clean_clip_async` despite being imported elsewhere with the same
  alias pattern. | fix: hoist a single module-level `from config import
  settings` (line 56 already does it inside `_thumb_redis`) — pick one alias
  shape and use it everywhere.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | 3 findings (advisory-lock-on-pooled-connection leak; lock released before work in purge; ffmpeg zombie on soft-timeout) |
| 2 Concurrency & scale | 2 findings (sync ffmpeg cancellation; `_poll_clip_outcomes` holds connection across YouTube API loop) |
| 3 Security & compliance | 2 findings (RLS-blind sessions in retrain + improvement_brief under role split; shared cleaned_render_uri idempotency key conflates clean and edit) |
| 4 Clip-quality | n/a (worker is the scheduler — scoring lives in clip_engine/) |
| 5 Anthropic SDK | covered by anthropic_stream.py — clean (prompt caching, token usage logged, structured output, no virality strings); `tools=None` drop at line 75 is correct for older SDK |
| 6 Cleanliness & typing | 3 findings (duplicate tempfile imports x3; duplicate Sequence/Any imports; 2919-line file needs brief-runner extraction) |
| 7 Error handling / API | n/a (worker is not a router; emit shape is reviewed at progress.py and is safe — exc_type only, never exc.args) |
| 8 Config & paths | ok (all paths via Path / tempfile; STORAGE_BACKEND/REDIS_URL/SOURCE_MEDIA_RETENTION_HOURS/YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS/CELERY_SOFT_TIME_LIMIT_S all flow through pydantic-settings) |

## Module verdict

NEEDS-WORK — Issue 105 advisory-lock pattern is intact across all six sites and
the new `clean_clip`/`edit_clip` pair is idempotent + emits clean SSE shape, but
two `AsyncSessionLocal` callers will silently no-op under prod RLS, the
clean/edit pair share an idempotency key that conflates two distinct user
actions, and the source-media purge releases its advisory lock before doing the
work it's meant to guard. None block today's single-role deployment; all three
SEV1s land the moment role split or a stressed user hits clean→edit in the same
session.
