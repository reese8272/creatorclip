# worker — assessed 2026-06-24

Slice: `worker/__init__.py`, `worker/anthropic_stream.py`, `worker/celery_app.py`,
`worker/progress.py`, `worker/schedule.py`, `worker/storage.py`, `worker/tasks.py` (4172 lines).
Reconciled against the prior 2026-06-16 assessment (line numbers there were from the 2976-line
version); dispositions of the carried items are recorded inline + in the table below.

## Findings

- [SEV1] worker/tasks.py:4053 — `mailer_send(...)` is a **synchronous, blocking** call
  (`notify.mailer.send` → `_send_resend` → `resend.Emails.send(params, options)` at
  notify/mailer.py:188, a blocking HTTP round-trip to Resend with **no timeout** — `resend` SDK
  default, and there is no RESEND timeout setting in config.py) invoked **directly inside
  `async def _send_notification_async` with no `asyncio.to_thread`**, while the `async with
  db.AdminSessionLocal()` session is open (opens line 3973, commit line 4084). Two compounding
  problems: (a) it blocks the worker's singleton event loop for the whole email round-trip; (b) it
  pins one of only **4** admin DB connections (admin engine pool_size=2 + max_overflow=2,
  db.py:65-66) across that round-trip. `send_notification` is fan-out-heavy (clips_ready, dna_built,
  trial_ending, refund_issued, reauth_required, fired per creator from the pipeline + daily Beat
  sweeps), so a Resend slowdown can exhaust the admin pool and stall the loop. The sibling blocking
  call `list_recent_paid_sessions` IS offloaded (line 2198), so this is an inconsistency, not an
  unavoidable constraint. | fix: commit the delivery + in-app rows first to release the DB
  connection, THEN send outside the session via
  `await asyncio.to_thread(mailer_send, to=..., template=..., context=..., idempotency_key=...)`;
  add a Resend HTTP timeout in config and thread it through notify/mailer.py.

- [SEV2] worker/tasks.py:199-203, 230-232, 256-258 — the `except SoftTimeLimitExceeded: raise`
  branch in `ingest_video` / `transcribe_video` / `build_signals` re-raises BEFORE the generic
  `except Exception` (204/233/259) that runs `_set_status(video_id, IngestStatus.failed)`.
  `SoftTimeLimitExceeded` is an `Exception` subclass so the specific clause wins and the failed-set
  never runs. The refund fires (RefundOnFailureTask.on_failure) but `ingest_status` is left
  `running` forever — perpetual UI spinner, user cannot retry. The `_*_async` bodies only
  `aemit("error", ...)` on the way out; they never set DB status to failed either. Carried from
  2026-06-16, UNCHANGED in HEAD. | fix: in the `SoftTimeLimitExceeded` branch call
  `run_async(_set_status(video_id, IngestStatus.failed))` before re-raising (soft limit leaves the
  300 s headroom to hard limit, celery_app.py:54 — a 1-row UPDATE fits).

- [SEV2] worker/tasks.py:675/717 (`_retrain_preference_async`) and 2062/2111
  (`_purge_stale_youtube_analytics_async`) — both hold a session-scoped `pg_try_advisory_lock` and
  release it in `finally: session.execute(pg_advisory_unlock)`, but neither rolls back first. If the
  session is in a failed-transaction state when `finally` runs (e.g. the `await session.commit()` at
  line 2109 raises), the unlock `execute` itself raises, the connection returns to the pool with the
  lock STILL HELD, and db.py has NO `pg_advisory_unlock_all` pool-reset listener (only the RLS
  `after_begin` at db.py:139). For purge the key is the **global** `"purge_stale_youtube_analytics"`,
  so a leak silently disables the ToS §III.E.4.b analytics-staleness purge for up to
  `pool_recycle=1800s` (30 min, db.py:41) — and re-leaks each tick if the commit keeps failing.
  Note the poll, sweep, and refresh sites that the prior report flagged have SINCE been fixed with a
  `rollback()` before unlock (poll: line 1824 w/ Issue-143 comment; sweep: 1704; refresh: per-creator
  rollbacks 2575/2588/2597) — these two are the residual unguarded sites. (needs-runtime-confirmation
  on commit-failure frequency) | fix: add `await session.rollback()` as the first line of each
  `finally` (mirroring poll at 1824), OR migrate both to `pg_advisory_xact_lock` (auto-released on
  commit/rollback, the pattern `_build_dna_async` uses at 1473), OR register a `PoolEvents.reset`
  listener emitting `SELECT pg_advisory_unlock_all()` on both engines in db.py.

- [SEV2] worker/tasks.py:2628-2681 `_collect_creator_export` / :2640 — the GDPR export loads the
  creator's entire history into memory unbounded (`select(Video)...all()`, then all clips, all
  chat_messages via `conversation_id.in_(convo_ids)`, all metrics/feedback/outcomes), assembles one
  in-memory dict and `json.dump`s it. A power user with very large history spikes a worker process's
  RSS with no ceiling. Single-tenant + infrequent, so bounded. (needs-runtime-confirmation on the
  memory ceiling) | fix: stream the export as NDJSON per data-class (write each `.all()` batch to the
  temp file then release it) or paginate by PK rather than holding all classes simultaneously.

- [SEV2] worker/tasks.py:2560-2564 `_refresh_youtube_analytics_async` — loads ALL videos for a
  creator (`select(Video).where(Video.creator_id == creator.id)`, no cap) and calls
  `sync_video_analytics` sequentially per video while holding the global refresh advisory lock + one
  admin connection for the whole per-creator loop. The catalog-sync path deliberately caps this to
  DNA_LONGS_CAP+DNA_SHORTS_CAP (lines 2397-2429); the daily refresh does not. Gated by YouTube quota
  (breaks on QuotaExhaustedError) and off the request path, so blast radius is bounded, but one large
  channel can hold the global lock a long time and starve the rest of the daily run.
  (needs-runtime-confirmation) | fix: cap the per-creator video set the same way catalog-sync does
  (most-recent N longs + N shorts by published_at), letting older videos age in over later ticks.

- [SEV2] worker/tasks.py:309-316, 459-473, 1335 — `clean_clip` / `edit_clip` / `_edit_clip_async`
  take only `clip_id` (+ `cut_segments`); the worker never re-verifies clip ownership. Authorization
  lives solely in routers/clips.py. A malformed/forged Celery message re-encodes any creator's clip.
  Defence-in-depth gap, not a current exploit (broker not internet-facing). Carried from 2026-06-16
  (tracked Issue 231), UNCHANGED. | fix: add `creator_id` to the task signature (mirrors
  `generate_chapters`/`analyze_hook` which already take it) and assert `clip.creator_id == cid` at
  entry of the async body.

- [cleanup] worker/tasks.py:2198 — `await asyncio.get_event_loop().run_in_executor(None,
  list_recent_paid_sessions, ...)` works (inside a coroutine `get_event_loop()` returns the running
  loop) but is inconsistent with the `asyncio.to_thread(...)` idiom used everywhere else. | fix:
  `await asyncio.to_thread(list_recent_paid_sessions, settings.STRIPE_RECONCILE_LOOKBACK_HOURS)`.

- [cleanup] typing gaps the mypy gate hasn't caught: worker/storage.py:29 `def _r2():` (no return
  type); worker/tasks.py:2397 `def _unmeasured_query(kind: VideoKind, cap: int):` (no `-> Select`);
  worker/tasks.py:2623 `def _row_to_dict(obj) -> dict:` (`obj` untyped); worker/tasks.py:2628
  `async def _collect_creator_export(session, creator) -> dict:` (`session`/`creator` untyped).
  Ruff passes clean. | fix: annotate `_r2() -> Any`, `_unmeasured_query(...) -> Select[tuple[Video]]`,
  `_row_to_dict(obj: Any)`, `_collect_creator_export(session: AsyncSession, creator: Creator)`.

- [cleanup] worker/tasks.py — six brief-style LLM tasks share the same ~120-line shape (emit step →
  load DNA/identity/transcript → `asyncio.to_thread` Claude → parse JSON → emit done/error). The
  duplication has grown the file to 4172 lines. Carried from 2026-06-16. | fix: extract a
  `worker/_brief_runner.py` helper (job_id, load_context_fn, call_fn, parse_fn, stage).

- [cleanup] worker/celery_app.py:101-117 — `_shutdown_worker_loop` closes the engine
  (`db.dispose_engine`) and the youtube HTTP client (`_http.aclose`) but never closes
  `worker.progress._AIO` (an unused `progress.aclose()` exists at progress.py:269) nor
  `worker.tasks._THUMB_REDIS`; shutdown-time connection leak only. Carried from 2026-06-16. | fix:
  also run `progress.aclose()` and aclose the thumb singleton in the hook.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | 2 findings — SEV1 holds an admin DB connection across the blocking Resend send (3973-4084); SEV2 advisory-lock leak at two unguarded `finally: unlock` sites (675/717, 2062/2111) with no pool-reset in db.py. Otherwise solid: DB sessions always via `async with`; every temp file `unlink(missing_ok=True)` in `finally`; locks in poll/sweep/refresh now roll back before unlock; external clients (`_r2`, sync/async Redis, `_thumb_redis`) are module-level singletons; render uses `with_for_update` + status-skip for idempotency. |
| 2 Concurrency & scale | 3 findings — SEV1 blocking `mailer_send` on the singleton loop; SEV2 unbounded GDPR-export accumulation; SEV2 uncapped per-creator video loop in the daily refresh. All other blocking work (ffmpeg, librosa, transcription, boto3, Stripe, Anthropic SDK) is correctly offloaded via `asyncio.to_thread`/`run_in_executor`. Per-worker singleton loop + engine rebind (celery_app.py:90-98) is correct; `prefetch_multiplier=1` + `acks_late` + `reject_on_worker_lost` give at-least-once with idempotent bodies. |
| 3 Security & compliance | ok — tokens read only via `get_valid_access_token` → `crypto.decrypt()` (youtube/oauth.py:266/325/375); the two log lines mentioning "token" (tasks.py:1775, 2377) log only `creator_id` + the exception, never the value. No PII in worker log lines (trial-expiry logs `creator=%s` not email — prior SEV1 verified CLOSED, Issue 138; notification logs creator_id/event_type/dedupe_key, not email). Per-creator isolation enforced on every creator-scoped query (every LLM task re-checks `video.creator_id != cid`; export/notification/chat filter `WHERE creator_id == cid`; AdminSessionLocal is justified for cross-tenant system sweeps + RLS-spanning tables, with explicit WHERE predicates and `session.info["creator_id"]` set where the RLS listener is relied on, tasks.py:2792). Parameterized SQL only (advisory-lock `text()` binds `:k`). GDPR export EXCLUDES the encrypted YoutubeToken columns (model not in the export list; only the creator's own email/stripe_customer_id appear, which is correct for a right-of-access export). No virality promise (honesty copy at tasks.py:4150). |
| 4 Clip-quality | n/a (orchestration; clip math lives in clip_engine). `_render_start_for` renders from `setup_start_s` per CLIPPING_PRINCIPLE #2 (tasks.py:1064-1073). |
| 5 Anthropic SDK | ok (worker side) — every sync streaming SDK call is offloaded via `asyncio.to_thread`, `task_id` is threaded so cache/token deltas stream as SSE (anthropic_stream.py), and token usage is logged after every call via `record_llm_usage` (billing/ledger.py logs tokens_in/out). Prompt caching / max_tokens / structured output are set in the called modules (dna/, knowledge/, improvement/, analysis/, chat/), outside this slice. |
| 6 Cleanliness & typing | 4 cleanup — a few missing param/return annotations (ruff clean); `run_in_executor` vs `to_thread` inconsistency; 4172-line file (brief-runner extraction); shutdown not closing the Redis singletons. No TODO, no `print()`, no commented-out code. |
| 7 Error handling / API | n/a (Celery worker, not a router). Error posture is sound: ValueError/auth errors terminal (no retry), transient errors retry, soft-timeout re-raises to fire on_failure, and every UI-facing `aemit("error", ...)` carries `exc_type` (class name) only — never the exception message or a DB error. |
| 8 Config & paths | ok — all media paths absolute (`_local_root().expanduser().resolve()`, tempfiles); new config present in config.py with validators. One gap tied to the SEV1: no Resend HTTP timeout setting. |

## Module verdict
NEEDS-WORK — a well-engineered, idempotent, isolation-clean worker. The new real defect is the
blocking `resend.Emails.send` run on the event loop while holding one of only four admin DB
connections (SEV1); the residual carried SEV2s are the soft-timeout `ingest_status=running` stuck
state, two unguarded advisory-lock `finally: unlock` sites (one on a global compliance-purge key),
the unbounded GDPR-export/refresh loops, and the clean/edit ownership-recheck gap. Offloading the
mailer to a thread (outside the session) + adding `rollback()` to the two `finally` blocks (or
migrating them to `pg_advisory_xact_lock`) + the soft-timeout failed-status fix close the
highest-risk items.

## Reconciliation with prior (2026-06-16) findings
| Prior finding | Disposition in HEAD (4172-line tasks.py) |
|---|---|
| SEV1 PII in `expire_trials` log | CLOSED (Issue 138) — tasks.py:2154-2160 logs `creator=%s`, no email. Re-verified. |
| SEV2 soft-timeout leaves ingest_status=running | STILL PRESENT — tasks.py:199-203/230-232/256-258. Re-flagged SEV2. |
| SEV2 advisory-lock leak ×6 sites | PARTIALLY FIXED — poll(1824)/sweep(1704)/refresh(2575+) now rollback before unlock; `_retrain` (675/717) + global `purge_stale_youtube_analytics` (2062/2111) still unguarded. Re-flagged SEV2 (narrowed to the 2 residual sites). |
| SEV2 purge lock released before R2 sweep | NOW INTENTIONAL — tasks.py:1995-1999 lock guards only the read; both purge ops are idempotent. Downgraded to non-issue (documented in code). |
| SEV2 session held across YouTube loops (poll) | IMPROVED — poll still holds the session across `get_video_stats` but commits per-creator + rolls back before unlock; bounded by the 10-day `cutoff_created` candidate set (1731). Not re-flagged (refresh's uncapped loop is the live one, re-flagged separately). |
| SEV2 unbounded refresh sweep | STILL PRESENT — tasks.py:2560-2564 uncapped per-creator video loop. Re-flagged SEV2. |
| SEV2 ffmpeg orphan on soft-timeout | cross-module (clip_engine owns the subprocess); worker side needs no change once `start_new_session=True` lands there. Not re-flagged in this slice. |
| SEV2 clean/edit lack ownership re-check | STILL PRESENT (Issue 231) — re-flagged SEV2. |
| SEV2 re-renders invisible to billing | WONT-FIX (free-by-design, docs/DECISIONS.md). Not re-flagged. |
| SEV2 _thumb_redis no loop-binding guard | STILL PRESENT but low-risk (redis-py pool auto-reconnects; best-effort cache). Folded into the shutdown/cleanup note; not re-flagged as SEV2. |
| cleanup duplicate imports / DRY / brief-runner / shutdown Redis / hook N+1 | hook N+1 (now tasks.py:3552-3561, capped at 20, per-click LLM task) is acceptable; brief-runner + shutdown-Redis re-flagged cleanup; inner duplicate imports remain (minor, not separately re-flagged). |
