# worker — assessed 2026-05-29

Slice: `worker/celery_app.py`, `worker/schedule.py`, `worker/storage.py`, `worker/tasks.py`.
Load-bearing dependencies traced for verification: `db.py` (engine/session lifecycle),
`billing/ledger.py` (deduction idempotency), `clip_engine/ranking.py` (clip replace
semantics), `models.py` (cascade rules), `youtube/data_api.py` + `youtube/oauth.py`
(external-call timeouts), `docker-compose*.yml` (Celery pool model). Per the contract I do
not file findings against those files — only against `worker/` — but I cite them as evidence.

## Findings

- [SEV1] worker/tasks.py:76 — `build_signals` calls `generate_clips.delay(video_id)`
  *after* the try/except, on every successful run, and `generate_clips` →
  `clip_engine/ranking.py:88` does `delete(Clip).where(Clip.video_id == video_id)` then
  re-inserts. `Clip.feedback` and `Clip.outcome` are `cascade="all, delete-orphan"`
  (models.py:365 / models.py:368), so a re-run **destroys creator feedback labels and
  published-clip outcomes**. Under `task_acks_late=True`, any redelivery of `build_signals`
  (visibility-timeout expiry, worker restart between commit and ack) re-enqueues
  `generate_clips`, silently wiping a creator's labels/outcomes — this corrupts the
  preference-model training signal and is not idempotent (scale-checklist axis C). |
  fix: make `generate_clips` a no-op when clips already exist for the video unless they are
  all `render_status == pending` with zero feedback/outcome rows — e.g. at the top of
  `_generate_clips_async` `SELECT 1 FROM clips WHERE video_id = :v` and return early if
  present; or in `generate_and_rank_clips` replace only `pending` clips
  (`delete(Clip).where(Clip.video_id == video_id, Clip.render_status == RenderStatus.pending)`)
  and never touch clips that have feedback/outcome rows. Add a test that runs the
  ingest→signals chain twice and asserts existing feedback/outcomes survive.

- [SEV1] worker/celery_app.py:27 — `task_acks_late=True` is set but
  `task_reject_on_worker_lost` is **not**. With acks-late alone, a worker killed mid-task
  (OOM is routine during ffmpeg/WhisperX on large media) does **not** redeliver — the job is
  silently dropped and the video sticks in `running`/`pending` forever with no retry
  (scale-checklist axis C: "acks_late REQUIRES reject_on_worker_lost to be safe"). |
  fix: add `task_reject_on_worker_lost=True` to `celery.conf.update(...)`. This is only safe
  *with* the axis-C idempotency guarantees — pair it with the SEV1 above. Confirm media
  tasks are genuinely re-runnable (ingest is, via `deduct_for_video` UNIQUE(video_id);
  generate_clips is not yet — see above).

- [SEV1] worker/tasks.py:401-403 — `poll_clip_outcomes` selects outcomes where
  `ClipOutcome.fetched_at < cutoff_7d` with **no terminal/finalized guard**. There is no
  "done" marker on `ClipOutcome` (models.py:396-408 has no `final`/`checkpoints_done`
  column), so every clip outcome ever published re-qualifies one week after its last fetch
  and is re-polled against the YouTube Data API **forever**. At hundreds of creators ×
  growing published-clip history this is an unbounded, ever-growing quota drain that will
  eventually starve the daily quota (`youtube/quota.py`) and the real refresh job
  (scale-checklist axis E/F). | fix: add a `final: bool` (or `checkpoint: enum{48h,7d,done}`)
  column to `ClipOutcome`; in the 7d branch set it once the 7d checkpoint is recorded and
  exclude `final.is_(True)` from the query. Cap the candidate set explicitly (e.g. only
  `published_at` within the last 8 days) so the scan never grows without bound.

- [SEV2] worker/tasks.py:558-569 — `_refresh_youtube_analytics_async` loads **all** creators
  (`list(result.scalars())`) and for each loads **all** their videos
  (`list(videos_result.scalars())`) into memory, then iterates. This is an unbounded
  `fetchall` fan-out (scale-checklist "bounded work"): at thousands of creators × hundreds of
  videos the daily beat tick holds the whole working set in one process and one DB session
  for the duration. | fix: stream creators with `.execution_options(yield_per=100)` or page
  by `(last_analytics_refreshed_at, id)` keyset; commit/close per creator (already commits
  per creator — also release rows). Same pattern applies to
  `_purge_stale_source_media_async:516` and `_poll_clip_outcomes_async:406`.

- [SEV2] worker/tasks.py:415-453 — `_poll_clip_outcomes_async` holds **one** AsyncSession
  open across an N-creator × M-clip loop of awaited network calls
  (`get_valid_access_token`, `get_video_stats`) and commits only once at the very end
  (line 453). A single slow/failing YouTube call holds the DB connection (and its
  transaction) open for the whole batch, and a failure anywhere loses the entire batch's
  progress on rollback. With the pool at `pool_size=10, max_overflow=20` (db.py:17-18) this
  ties up a connection for minutes (scale-checklist axis A/E). | fix: fetch tokens/stats
  outside the write transaction, then open a short session per creator to persist; commit per
  creator so partial progress survives a mid-batch failure, mirroring the per-creator commit
  already used in `_refresh_youtube_analytics_async`.

- [SEV2] worker/celery_app.py:20-29 — no `task_time_limit` / `task_soft_time_limit` and no
  broker `visibility_timeout` override. The default Redis visibility timeout is 1h; a render
  or transcription that legitimately exceeds it is redelivered **while the first copy is
  still running**, so two workers render the same clip concurrently and both write
  `render_uri` (last-writer-wins, wasted ffmpeg + R2 cost) (scale-checklist axis C). |
  fix: set `task_soft_time_limit`/`task_time_limit` (e.g. 1500s/1800s) and
  `broker_transport_options={"visibility_timeout": 3600}` aligned to the hard limit, and make
  `render_clip` idempotent on a state transition (`WHERE render_status = 'pending'` /
  skip when `render_status == done`).

- [SEV2] worker/tasks.py:53-55, 64-65, 74-75, 96 — the failure path runs a **second**
  `run_async(_set_status(..., failed))` and then `self.retry()`. On a retry that later
  succeeds the video has already been stamped `failed` in the DB between attempts; more
  importantly, when retries are exhausted the chain (`ingest | transcribe | build_signals`)
  silently stops and there is no terminal "permanently failed after N retries" state distinct
  from transient `failed` — a stuck video is indistinguishable from a retrying one. | fix:
  only mark `failed` in the task's `on_failure`/after `max_retries` is exhausted (check
  `self.request.retries >= self.max_retries`), leaving the row in `running` between transient
  retries; or add a distinct `permanently_failed` status so operators can find dead jobs.

- [SEV2] worker/storage.py:22, 39 — `_r2()` and `_local_root()` have no return type
  annotations (`_r2()` returns an untyped boto3 client). CLAUDE.md mandates a type hint on
  every signature. boto3 has no stub by default, but the intent should be explicit. | fix:
  annotate `def _r2() -> "S3Client"` (via `boto3-stubs[s3]`) or at minimum `-> Any` with a
  `# boto3 client is untyped` note; `def _local_root() -> Path` is trivially addable.

- [cleanup] worker/tasks.py:284 — `import tempfile` is performed inside `_render_clip_async`
  even though `tempfile` is already imported at module top (worker/tasks.py:12). Duplicate /
  shadowing import. | fix: delete the local `import tempfile`.

- [cleanup] worker/tasks.py:81, 233, etc. — `from ... import` statements scattered inside
  function bodies across most async impls (clip_engine, dna, youtube, billing, sqlalchemy).
  Some are deliberate to avoid circular imports / heavy import cost on the API process, but
  the volume obscures the real dependency surface. | fix: hoist the import-cycle-safe ones
  (sqlalchemy `select`/`and_`/`or_`, `config.settings`) to module top; keep only genuinely
  cycle-breaking imports local and comment why.

- [cleanup] worker/tasks.py:189, 459, 461 — re-imports of `settings`, `Signals`, `Transcript`
  inside functions when they (or equivalents) are already imported at module top
  (models import block lines 18-30 already imports `Signals`, `Transcript`). | fix: drop the
  redundant local re-imports.

### Verified OK (load-bearing claims traced, no finding)

- Per-worker singleton event loop + engine rebind on fork is correct
  (celery_app.py:54-62 → `db.recreate_engine()` disposes inherited pool with `close=False`
  and rebinds the sessionmaker to a fresh engine on the child's loop). This resolves the
  "Future attached to a different loop" class (Issue 39). `run_async` reuses the one loop;
  the `asyncio.run` fallback is test-only and gated on `_LOOP is None`.
- Sync subprocess/CPU work (ffmpeg via `youtube/ingest.py` + `clip_engine/render.py`,
  WhisperX in `ingestion/transcribe.py`) is invoked inside `async def` task bodies, but the
  Celery pool is **prefork** with `--concurrency=2/4` (docker-compose*.yml) — one task per
  process, so the blocking call has no co-resident coroutines to stall. Axis-B "sync on the
  loop" does NOT bite here because this is the worker, not the API request loop. (It would
  bite if the pool were switched to gevent/eventlet — note that constraint in DEPLOYMENT.)
- Temp-media cleanup is in `finally` on every media path (tasks.py:179-180, 296-297;
  storage.py:100-101) and survives exceptions. ffmpeg/ffprobe subprocess calls have explicit
  `timeout=` (render.py, ingest.py), so no hung subprocess leak.
- DB sessions are always `async with db.AsyncSessionLocal()` context managers — guaranteed
  close on every path including exceptions.
- Billing deduction is genuinely idempotent (billing/ledger.py UNIQUE(video_id) +
  fast-path + IntegrityError catch), and is the correct pattern; `ingest_done_at` is stamped
  once behind `if video.ingest_done_at is None` (tasks.py:255) so retries don't re-stamp the
  one-time marker (axis-C discipline honored there).
- Source-media purge gates on `ingest_done_at` not `created_at` (tasks.py:504-513), honoring
  the COMPLIANCE.md retention clock (Issue 43); per-video try/except so one bad delete
  doesn't abort the batch.
- No OAuth token, PII, or secret in any `logger.*` line in the module — logs carry
  `creator_id`/`video_id`/`clip_id` UUIDs and view counts only (tasks.py logging audited).
  Tokens are fetched via `get_valid_access_token` and never logged.
- Every creator-scoped query in the module carries `WHERE creator_id`/`video_id` (poll
  outcomes joins `Clip` and groups by `clip.creator_id`; analytics refresh filters
  `Video.creator_id == creator.id`; median views filters `Video.creator_id`). No
  cross-tenant leak found in this slice. (Defense-in-depth RLS is tracked as Issue 56.)
- No virality-promise string anywhere in the module.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | 3 findings (SEV1 acks-late/reject-on-lost; SEV2 long-held session; SEV2 fail-status) — sessions/temp-media themselves OK |
| 2 Concurrency & scale | 3 findings (SEV2 unbounded fetchall fan-out; SEV2 missing time-limit/visibility-timeout; loop/engine binding OK; sync-on-loop n/a for prefork worker) |
| 3 Security & compliance | ok — isolation, token handling, purge, no-virality all verified |
| 4 Clip-quality | n/a (worker orchestrates the engine; scoring lives in clip_engine/) |
| 5 Anthropic SDK | n/a (no direct Anthropic SDK call in worker; brief generation delegated to dna/brief.py) |
| 6 Cleanliness & typing | 4 findings (untyped storage helpers; duplicate/in-function imports) |
| 7 Error handling / API | n/a (no HTTP surface in this module) |
| 8 Config & paths | ok — all worker config in `.env.example`; `LOCAL_MEDIA_DIR` relative but dev-only |

## Module verdict
NEEDS-WORK — no cross-tenant leak or open BLOCKER, but two SEV1 idempotency defects
(`generate_clips` destroys feedback/outcomes on re-run; `acks_late` without
`reject_on_worker_lost` silently drops crashed media jobs) plus an unbounded
`poll_clip_outcomes` quota drain must be fixed before this module is safe at
at-least-once-delivery scale.
