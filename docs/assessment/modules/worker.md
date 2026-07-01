# worker — assessed 2026-07-01

Slice: `worker/__init__.py`, `worker/anthropic_stream.py`, `worker/celery_app.py`,
`worker/progress.py`, `worker/schedule.py`, `worker/storage.py`, `worker/tasks.py`
(now 4765 lines; +1016 in tasks.py + anthropic_stream/celery_app/schedule/storage churn
since a503ade). Reconciled against the prior 2026-06-24 assessment; carried findings
re-verified against current line numbers (dispositions in the table at the bottom).

Pinned versions grounding the findings (requirements.txt): `celery[redis]==5.4.0`,
`redis[hiredis]==5.2.0`, `celery-redbeat==2.3.3`, `anthropic==0.105.2`, `boto3==1.35.54`,
Python `>=3.12`. Admin DB pool (db.py:65-66): `pool_size=2 + max_overflow=2 = 4` connections.

## Findings

- [SEV1] worker/tasks.py:4626 — `mailer_send(...)` is a **synchronous, blocking** call
  (`notify.mailer.send` → `_send_resend` → `resend.Emails.send(params, options)` at
  notify/mailer.py:260, a blocking HTTP round-trip to Resend with **no timeout** — verified:
  no `timeout` anywhere in notify/mailer.py and no RESEND timeout in config.py) invoked
  **directly inside `async def _send_notification_async` with no `asyncio.to_thread`**, while
  the `async with db.AdminSessionLocal()` session is open (opened :4519, committed :4660). Two
  compounding problems: (a) it blocks the worker's singleton event loop for the whole email
  round-trip; (b) it pins one of only **4** admin DB connections across that round-trip.
  `send_notification` is fan-out-heavy (clips_ready, dna_built, trial_ending, refund_issued,
  reauth_required, first_clip_nudge, re_engagement — fired per creator from the pipeline +
  daily Beat sweeps), so a Resend slowdown can exhaust the admin pool and stall the loop. The
  sibling blocking call `list_recent_paid_sessions` IS offloaded (tasks.py:2723), so this is an
  inconsistency, not an unavoidable constraint. STILL PRESENT (carried, unchanged in HEAD). |
  fix: commit the delivery + in-app rows first to release the DB connection, THEN send outside
  the session via `await asyncio.to_thread(mailer_send, ...)`; add a Resend HTTP timeout in
  config and thread it through notify/mailer.py.

- [SEV2] worker/celery_app.py:62-64 — the broker `visibility_timeout` (hardcoded `3600`) is
  decoupled from `CELERY_SOFT_TIME_LIMIT_S`, and the load-bearing invariant *soft < hard <
  visibility_timeout* is stated only in a comment, not enforced. At the default (soft `3000`,
  hard `soft+300=3300`, visibility `3600`) the margin is a thin 300 s; a task reserved briefly
  under acks_late+prefetch=1 before running to the hard limit can exceed the 3600 s window, at
  which point the Redis broker **redelivers a still-running copy** (documented Redis-broker
  behaviour — an unacked message past the visibility timeout is requeued and re-executed:
  celery/celery#6229, Celery Redis-broker docs). The same file's comment explicitly invites
  operators to *raise* `CELERY_SOFT_TIME_LIMIT_S` ("Long-form sources on CPU WhisperX may need a
  per-task override"); pushing soft past `3300` makes hard exceed `3600` and *guarantees*
  concurrent double-execution. The per-row idempotency guards (`with_for_update`, done-checks)
  prevent double *completion* but NOT double *paid work* — two ffmpeg encodes / two paid
  Anthropic calls run concurrently before either commits `done`; `task_reject_on_worker_lost`
  cannot help because the original worker never crashed. (needs-runtime-confirmation on the
  exact reserved-buffer timing at default) | fix: derive it —
  `broker_transport_options={"visibility_timeout": settings.CELERY_SOFT_TIME_LIMIT_S + 600}` (or
  hardcode `7200`) so it comfortably exceeds `task_time_limit`, AND add a config.py validator
  asserting `CELERY_SOFT_TIME_LIMIT_S + 300 < visibility_timeout` next to the existing
  transcription-timeout validator (config.py:696).

- [SEV2] worker/tasks.py:201-205, 234-236, 262-264 — the `except SoftTimeLimitExceeded: raise`
  branch in `ingest_video` / `transcribe_video` / `build_signals` re-raises BEFORE the generic
  `except Exception` that runs `_set_status(video_id, IngestStatus.failed)`. Because
  `SoftTimeLimitExceeded` is an `Exception` subclass the specific clause wins and the failed-set
  never runs; `ingest_status` is left `running` forever → perpetual UI spinner, no retry
  affordance. The refund still fires (RefundOnFailureTask.on_failure) but the video looks stuck.
  Note `render_clip` (tasks.py:330-341) DOES set failed in its soft-timeout branch — the three
  ingest-chain tasks are the inconsistent ones. STILL PRESENT (carried, unchanged). | fix: in
  each `SoftTimeLimitExceeded` branch call `run_async(_set_status(video_id, IngestStatus.failed))`
  before re-raising (the 300 s soft→hard headroom fits a 1-row UPDATE).

- [SEV2] worker/tasks.py:836-837 (`_retrain_preference_async`) and 2516-2520
  (`_purge_stale_youtube_analytics_async`) — both hold a session-scoped `pg_try_advisory_lock`
  and release it in `finally: session.execute(pg_advisory_unlock)` **without rolling back
  first**. If the session is in a failed-transaction state when `finally` runs (e.g. the
  `await session.commit()` at :2515 raises), the unlock `execute` itself raises, the connection
  returns to the pool with the lock STILL HELD, and db.py has no `pg_advisory_unlock_all`
  pool-reset listener. For the purge the key is the **global** `"purge_stale_youtube_analytics"`,
  so a leak silently disables the ToS §III.E.4.b analytics-staleness purge until `pool_recycle`
  (30 min) cycles the connection — and re-leaks each tick if the commit keeps failing. The
  poll/sweep/refresh sites the prior report flagged have SINCE been fixed (poll rolls back before
  unlock at :2117 w/ the Issue-143 comment; sweep rolls back in its `except` at :1953-1955;
  refresh rolls back per-creator) — these two are the residual unguarded sites.
  (needs-runtime-confirmation on commit-failure frequency) | fix: add `await session.rollback()`
  as the first line of each `finally` (mirroring poll:2117), OR migrate both to
  `pg_advisory_xact_lock` (auto-released on commit/rollback — the pattern `_build_dna_async`
  uses at :1722), OR register a `PoolEvents.reset` listener running `SELECT pg_advisory_unlock_all()`.

- [SEV2] worker/tasks.py:400-407, 568-582, 1470/1602 — `clean_clip(clip_id)` /
  `edit_clip(clip_id, cut_segments)` and their async bodies (`_clean_clip_async`,
  `_edit_clip_async`) never re-verify clip ownership; authorization lives solely in
  routers/clips.py. A malformed/forged Celery message re-encodes any creator's clip.
  Defence-in-depth gap, not a live exploit (broker not internet-facing). STILL PRESENT
  (tracked Issue 231). | fix: add `creator_id` to the task signature (mirrors
  `generate_chapters`/`analyze_hook`, which already take it) and assert `clip.creator_id == cid`
  at the top of each async body.

- [SEV2] worker/tasks.py:3094-3098 (`_refresh_youtube_analytics_async`) — loads ALL videos for
  a creator (`select(Video).where(Video.creator_id == creator.id)`, no cap) and calls
  `sync_video_analytics` sequentially per video while holding the global refresh advisory lock +
  one admin connection for the whole per-creator loop. The catalog-sync path deliberately caps
  this to `DNA_LONGS_CAP + DNA_SHORTS_CAP` (tasks.py:2928-2960); the daily refresh does not, so
  one large channel can hold the global lock a long time and starve the rest of the daily run.
  Quota-gated (breaks on `QuotaExhaustedError`) and off the request path, so bounded.
  (needs-runtime-confirmation) | fix: cap the per-creator video set the same way catalog-sync
  does (most-recent N longs + N shorts by `published_at`), letting older videos age in over ticks.

- [SEV2] worker/tasks.py:3174 (`_collect_creator_export`) — the GDPR export loads a creator's
  entire history into memory unbounded (`select(Video)...all()`, then all clips, all
  chat_messages via `conversation_id.in_(convo_ids)`, all metrics/feedback/outcomes), assembles
  one in-memory dict and `json.dump`s it. A power user with very large history spikes a worker
  process's RSS with no ceiling. Single-tenant + infrequent, so bounded.
  (needs-runtime-confirmation on the memory ceiling) | fix: stream the export as NDJSON
  per data-class (write each `.all()` batch to the temp file then release it) or paginate by PK
  rather than holding all classes simultaneously.

- [SEV2] worker/tasks.py:465-473 (`_publish_to_youtube_async`) — acknowledged idempotency hole
  (flagged Issue 336 in-code): a `ClipPublication` row that is `status==done` but has NULL
  `youtube_video_id` (prior run uploaded to YouTube but crashed before the success-commit)
  bypasses the guard, so the redelivery re-uploads — a **duplicate Short on the creator's
  channel** — and re-consumes `COST_DATA_VIDEOS_INSERT` quota. Narrow crash window but real
  double-post + non-refundable quota burn. | fix: write `youtube_video_id` and `status=done` in
  a single atomic transaction (currently split across two sessions at :494-496 and :536-563),
  OR treat `done`-with-NULL as "already uploaded, do not re-post" and reconcile the id
  out-of-band.

- [cleanup] worker/anthropic_stream.py:16-24, :231-234 — stale comments assert "For 0.40, do not
  pass thinking={...}" / "won't fire on anthropic==0.40 (SDK predates extended thinking) … wakes
  up once Issue 84 bumps the SDK". The SDK is now pinned at `anthropic==0.105.2` (extended
  thinking shipped long before this), so Issue 84's bump landed but the comments and the
  decision not to enable thinking were never revisited; the `thinking_delta` branch is dead
  because neither `stream_and_emit` nor `stream_message` accepts/sets a `thinking` param. | fix:
  correct the comments to the real pin and either delete the dead branch or expose a `thinking`
  kwarg; capture the choice in docs/DECISIONS.md.

- [cleanup] worker/tasks.py:2723 — `await asyncio.get_event_loop().run_in_executor(None,
  list_recent_paid_sessions, ...)` works but `get_event_loop()` is soft-deprecated inside a
  running loop (3.12) and is inconsistent with the `asyncio.to_thread(...)` idiom used
  everywhere else. | fix:
  `await asyncio.to_thread(list_recent_paid_sessions, settings.STRIPE_RECONCILE_LOOKBACK_HOURS)`.

- [cleanup] typing gaps the mypy gate hasn't caught: worker/storage.py:29 `def _r2():` (no
  return type); worker/tasks.py:2928 `def _unmeasured_query(kind, cap):` (no `-> Select`);
  worker/tasks.py:3169 `def _row_to_dict(obj) -> dict:` (`obj` untyped); worker/tasks.py:3174
  `async def _collect_creator_export(session, creator) -> dict:` (both params untyped). Ruff
  clean. | fix: `_r2() -> Any`, `_unmeasured_query(...) -> Select[tuple[Video]]`,
  `_row_to_dict(obj: Any)`, `_collect_creator_export(session: AsyncSession, creator: Creator)`.

- [cleanup] worker/tasks.py:63-77 — `_thumb_redis()` singleton does NOT rebind on event-loop
  mismatch, unlike `progress._async_client()` (progress.py:146-165) which documents exactly the
  pytest per-test-loop hazard. Fine in production (one loop/worker); DRY: the two async-redis
  singletons should share one loop-aware factory. | fix: route `_thumb_redis()` through the same
  loop-aware builder as `progress._async_client` (extract a shared `_aio_redis(url)`).

- [cleanup] worker/celery_app.py:110-126 — `_shutdown_worker_loop` closes the engine
  (`db.dispose_engine`) and the youtube HTTP client (`_http.aclose`) but never closes
  `worker.progress._AIO` (an unused `progress.aclose()` exists at progress.py:269) nor
  `worker.tasks._THUMB_REDIS`; shutdown-time connection leak only. | fix: also run
  `progress.aclose()` and aclose the thumb singleton in the hook.

- [cleanup] worker/tasks.py — the six brief-style LLM tasks share the same ~120-line shape (emit
  step → load DNA/identity/transcript → `asyncio.to_thread` Claude → `record_llm_usage` → parse
  JSON → emit done/error), driving the file to 4765 lines. | fix: extract a
  `worker/_brief_runner.py` helper (job_id, load_context_fn, call_fn, parse_fn, stage).

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | 2 findings — SEV1 holds an admin DB connection across the blocking Resend send (:4519-4660); SEV2 advisory-lock leak at two unguarded `finally: unlock` sites (:836-837, :2516-2520) with no pool-reset in db.py. Otherwise solid: DB sessions always via `async with`; every temp file `unlink(missing_ok=True)` in `finally` (ingest, render, clean, edit, export); external clients (`_r2`, sync/async Redis, `_thumb_redis`) are module-level singletons; render uses `with_for_update` + status-skip for idempotency; per-worker singleton loop + engine rebind (celery_app.py:99-126) with clean engine/HTTP dispose on shutdown. |
| 2 Concurrency & scale | 4 findings — SEV1 blocking `mailer_send` on the singleton loop; SEV2 visibility_timeout/hard-limit invariant unenforced; SEV2 uncapped per-creator video loop in the daily refresh; SEV2 unbounded GDPR-export accumulation. All other blocking work (ffmpeg, librosa, transcription, boto3, Stripe, Anthropic SDK) is correctly offloaded via `asyncio.to_thread`/`run_in_executor`. `prefetch_multiplier=1` + `acks_late` + `reject_on_worker_lost` give at-least-once with idempotent bodies. |
| 3 Security & compliance | ok — tokens read only via `get_valid_access_token` → `decrypt()`; no OAuth token or email in any `logger.*`/`aemit` line (`_expire_trials_async` logs `creator=%s` id-only; `send_notification` logs creator_id/event_type/dedupe_key; progress `error` events carry `type(exc).__name__` only, never `str(exc)`; `_humanize_failure` returns coarse stage-only strings). Per-creator isolation enforced on every creator-scoped query — cross-tenant Beat sweeps use `AdminSessionLocal` (BYPASSRLS, justified) but each query carries an explicit `WHERE creator_id ==` / `video.creator_id != cid` re-check (video-analysis/title/thumbnail/hook/chapters/export/chat verified); `_generate_improvement_brief_async` sets `session.info["creator_id"]` for the RLS listener. Parameterized SQL only (advisory-lock `text()` binds `:k`). Source-media + audio purge honored (`_purge_stale_source_media_async` gated on `ingest_done_at`, migration-0039 posture); 30-day analytics purge implemented; refund idempotent on `pack_id`. No virality promise (honesty copy at tasks.py:4726). |
| 4 Clip-quality | n/a — orchestration; clip math lives in clip_engine. `_render_start_for` (:1190) renders from `setup_start_s` per CLIPPING_PRINCIPLE #2 with a `start_s` fallback. |
| 5 Anthropic SDK | ok (worker side) w/ 1 cleanup — every sync streaming SDK call is offloaded via `asyncio.to_thread`; `messages.stream()` + `get_final_message()` usage is current for 0.105.2; `task_id` threaded so cache/token deltas stream as SSE; token usage logged after every call via `vlog_llm_response` + `record_llm_usage`; `warn_if_truncated` on stop_reason. Prompt caching / max_tokens / structured output are set in the called modules (dna/, knowledge/, improvement/, analysis/, chat/), outside this slice. Stale "0.40" comments = cleanup. |
| 6 Cleanliness & typing | 5 cleanup — stale SDK comments; `get_event_loop` vs `to_thread`; a few missing param/return annotations (ruff clean); `_thumb_redis` loop-rebind DRY gap; shutdown not closing the Redis singletons; 4765-line file (brief-runner extraction). No TODO/FIXME, no `print()`, no commented-out code in the slice. |
| 7 Error handling / API | n/a — Celery worker, not a router. Error posture otherwise sound: ValueError/auth errors terminal, transient errors retry, render soft-timeout marks failed; every UI-facing `aemit("error", ...)` carries `exc_type` (class name) only — never the message or a DB error. The ingest-chain soft-timeout branch is the one gap (SEV2 above). |
| 8 Config & paths | 1 finding (tied to the SEV2 visibility_timeout) — no validator ties `visibility_timeout` to `CELERY_SOFT_TIME_LIMIT_S`, and no Resend HTTP timeout setting exists (tied to the SEV1). Paths absolute (`storage._local_root()` does `expanduser().resolve()`, tempfiles); no new undocumented config introduced in the slice. |

## Module verdict
NEEDS-WORK — a well-engineered, idempotent, isolation-clean worker with no BLOCKER and no
cross-tenant leak. Highest-risk item is the blocking `resend.Emails.send` run on the event loop
while holding one of only four admin DB connections (SEV1). Then: the unenforced
visibility_timeout↔hard-limit invariant (silent double-execution/double-spend if the soft limit
is raised as the code itself suggests), the soft-timeout `ingest_status=running` stuck state, two
unguarded advisory-lock `finally: unlock` sites (one on the global ToS-purge key), the uncapped
refresh/GDPR-export loops, the clean/edit ownership-recheck gap, and the publish done-but-NULL
double-post window. Offloading the mailer (outside the session), deriving+validating the
visibility_timeout, adding `rollback()` to the two `finally` blocks, and the soft-timeout
failed-status fix close the top risks.

## Reconciliation with prior (2026-06-24) findings
| Prior finding | Disposition in HEAD (4765-line tasks.py) |
|---|---|
| SEV1 blocking `mailer_send` holding admin conn | STILL PRESENT — tasks.py:4626 (session :4519-4660); resend SDK sync, no timeout (notify/mailer.py:260). Re-flagged SEV1. |
| SEV2 soft-timeout leaves ingest_status=running | STILL PRESENT — tasks.py:201-205/234-236/262-264. Re-flagged SEV2. |
| SEV2 advisory-lock leak (2 residual sites) | STILL PRESENT — `_retrain` (:836-837) + global `purge_stale_youtube_analytics` (:2516-2520) still unguarded; poll/sweep/refresh now roll back. Re-flagged SEV2. |
| SEV2 unbounded GDPR export accumulation | STILL PRESENT — tasks.py:3174. Re-flagged SEV2. |
| SEV2 uncapped daily-refresh per-creator video loop | STILL PRESENT — tasks.py:3094-3098. Re-flagged SEV2. |
| SEV2 clean/edit lack ownership re-check | STILL PRESENT (Issue 231) — tasks.py:400/568, bodies :1470/:1602. Re-flagged SEV2. |
| cleanup `get_event_loop` vs `to_thread` | STILL PRESENT — tasks.py:2723. Re-flagged cleanup. |
| cleanup typing gaps (_r2/_unmeasured_query/_row_to_dict/_collect_creator_export) | STILL PRESENT — re-flagged cleanup (updated line nums). |
| cleanup shutdown not closing progress/_thumb Redis | STILL PRESENT — celery_app.py:110-126. Re-flagged cleanup. |
| cleanup brief-runner extraction / file size | STILL PRESENT (now 4765 lines). Re-flagged cleanup. |
| SEV2 purge lock released before R2 sweep | NON-ISSUE — `_purge_stale_source_media_async` holds the lock only for the read (:2355-2385) then deletes+commits in a separate session; both ops idempotent. Not re-flagged. |
| SEV2 re-renders invisible to billing | WONT-FIX (free-by-design, docs/DECISIONS.md). Not re-flagged. |
| SEV1 PII in `expire_trials` log | CLOSED (Issue 138) — re-verified id-only. |
| NEW this pass | visibility_timeout↔hard-limit invariant unenforced (SEV2, celery_app.py:62-64); publish done-but-NULL double-post (SEV2, :465-473); stale anthropic 0.40 comments (cleanup). |
