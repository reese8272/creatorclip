# worker — assessed 2026-07-20

Slice: `worker/__init__.py`, `worker/anthropic_stream.py`, `worker/celery_app.py`,
`worker/progress.py`, `worker/schedule.py`, `worker/storage.py`, `worker/tasks.py`
(now 5413 lines; all six non-init files changed since f70a857 — Issues 352 Batch B/D/F,
231 RLS sweep, 82a AsyncAnthropic, 290/291 spend guard, 284 kill switches, 191
render_summary, 78-R typing ratchet, 109b aclose registry, 349 mailer offload, 246
lifecycle sunset). Every prior (2026-07-01) finding re-verified against HEAD; dispositions
in "Resolved since 2026-07-01" and the reconciliation table. Issue 353 (styled re-render
resets render state) verified end-to-end per this run's method additions.

## Findings

- [SEV2] worker/tasks.py:5106-5118 — `_send_notification_async` swallows a FAILED email
  send: the dedupe `notification_deliveries` row is committed `status=sent` BEFORE the
  mailer call (:5029, :5078 — correct for the Issue-349 connection-release), but when
  `mailer_send` then times out or errors, the except block only marks the row `failed`
  and returns without re-raising — the task "succeeds", Celery never retries, and any
  FUTURE retry/redelivery hits the dedupe IntegrityError (:5034-5043) and skips. A
  transient Resend blip therefore permanently loses the email (in-app row survives;
  `clips_ready`/`trial_ending`/`refund_issued` all affected). | fix: re-raise after
  marking the row failed so the task-level `self.retry` (max_retries=3) fires, and make
  the dedupe short-circuit status-aware: on IntegrityError load the existing row and
  proceed with the send iff `status == failed` (flip it back to `sent` in the same
  transaction); add a unit test for fail-then-redeliver actually sending.

- [SEV2] worker/tasks.py:2559-2582 + clip_engine/ranking.py:187-192 (carry-forward) —
  `generate_clips` check-then-insert race still open: `load_existing_clips` runs in one
  session, the paid 30–120 s LLM `score_and_rank` runs sessionless, and
  `persist_ranked_clips` merely re-runs the same SELECT guard on a fresh session — no
  advisory lock, no UNIQUE backstop (clips are legitimately many-per-video). Duplicate
  concurrent deliveries are a real path: the `build_signals` wrapper enqueues
  `generate_clips.delay(video_id)` at :401 even when `_signals_async` idempotently
  skipped, so a redelivered build_signals spawns a second generate_clips that can run in
  a sibling prefork process. Both pass the empty-clips check → double paid Anthropic
  scoring; a photo-finish on persist can still double-insert rows (cascade-deleting
  feedback is NOT at risk — the guard never deletes). Contrast `_build_dna_async`
  (:2060-2077), which closes the identical race with `pg_advisory_xact_lock` + re-check
  under the lock. (needs-runtime-confirmation on observed frequency) | fix: take
  `pg_advisory_xact_lock(hashtext('genclips:' || video_id))` in the pre-scoring read
  session AND re-check existing clips under the same lock in `persist_ranked_clips`; or
  skip the :401 enqueue when the signals body reports it short-circuited.

- [SEV2] worker/tasks.py:3842 (`_generate_improvement_brief_async`), :4050
  (`_generate_video_analysis_async`), :4831 (`_chat_respond_async` — multi-round agentic
  loop via `run_chat_turn(..., session)`) — each holds an open `tenant_session`
  transaction across the 30–120 s Claude round-trip: one idle-in-transaction Postgres
  connection pinned per busy worker process for the LLM duration (blocks vacuum xmin,
  counts against Cloud SQL max_connections × worker processes). Inconsistent with the
  slice's own better pattern: title (:4180), thumbnail (:4389), hook (:4571), and
  chapters (:4703) all close the session before the call, and Issue 82b explicitly
  established release-across-external-calls. `_build_dna_async` (:2116) is the one
  justified exception — its `pg_advisory_xact_lock` idempotency serializer must live in
  the transaction. Bounded blast radius under prefork (one task per process), so SEV2
  not SEV1. (needs-runtime-confirmation under load) | fix: snapshot the needed fields,
  close the session before the LLM call, reopen to persist (exact shape of
  `_generate_title_suggestions_async`); for chat, pass a session-FACTORY into
  `run_chat_turn` so tool calls open short sessions instead of pinning one.

- [SEV2] routers/clips.py:478-487 (flagged here because this run was asked to verify
  Issue 353; owner is the routers module) — the re-render reset clears `render_uri`
  BEFORE the new render succeeds. Worker-side the change is correct and idempotent
  (verified: the skip guard at worker/tasks.py:1556 requires `done AND render_uri`, so
  the reset re-renders; a stale-message redelivery still no-ops; the fixed R2 key
  `clips/{clip_id}.mp4` makes the re-encode overwrite-idempotent). But on a PERMANENT
  re-render failure the clip ends `failed` with `render_uri=None` — the pointer to the
  previously-good artifact (still sitting un-overwritten at `clips/{id}.mp4`) is lost
  and the player unmounts a clip the creator had already reviewed. | fix: don't null
  `render_uri` in the endpoint (the worker overwrites it on success anyway); if the UI
  needs to unmount the stale player, key that off `render_status != done` instead.

- [cleanup] worker/tasks.py:3106 (carry-forward) —
  `asyncio.get_event_loop().run_in_executor(None, list_recent_paid_sessions, ...)`:
  `get_event_loop()` inside a running loop is soft-deprecated on 3.12 and inconsistent
  with the `asyncio.to_thread` idiom used everywhere else in the file. | fix:
  `await asyncio.to_thread(list_recent_paid_sessions, settings.STRIPE_RECONCILE_LOOKBACK_HOURS)`.

- [cleanup] worker/tasks.py:71-82 + worker/celery_app.py:136-152 (carry-forward,
  partially addressed) — `_thumb_redis()` still lacks the loop-rebind guard that
  `progress._async_client()` (progress.py:148-167) documents, and the worker shutdown
  hook still closes only the engine + youtube `_http`: `progress.aclose` is registered
  in the Issue-109b `shared_resources` registry but `close_all()` is only invoked from
  the FastAPI lifespan (main.py:111), never from `_shutdown_worker_loop`; `_THUMB_REDIS`
  is registered nowhere. Shutdown-time connection leak only. | fix: route
  `_thumb_redis` through a shared loop-aware factory with progress's client, register
  its aclose, and call `shared_resources.close_all()` inside `_shutdown_worker_loop`.

- [cleanup] worker/tasks.py:3558-3631, 3673-3675 (carry-forward, downgraded SEV2 →
  cleanup) — the GDPR export now reads every table through `_keyset_batches` (bounded
  500-row round-trips, plain column dicts — Issue 352 Batch F), but `_collect_creator_export`
  still accumulates the creator's ENTIRE history into one payload dict and `json.dump`s
  it (a second full-size string buffer). Constant factor much improved; total memory
  still O(history). Acceptable for the ≤100-user beta. | fix (when scheduled): stream
  NDJSON per data-class to the temp file, releasing each batch.

- [cleanup] worker/tasks.py — 5413 lines and growing (carry-forward): the six
  brief-style LLM tasks still share the same ~120-line shape (spend-guard → emit →
  load context → call → `record_llm_usage` → parse → done/error), and render_summary
  now mirrors render_clip's plan/encode/status trio nearly verbatim
  (:5249-5356 vs :1522-1710). | fix: extract `worker/_brief_runner.py` and a shared
  render-plan helper parametrized on model/status-column.

- [cleanup] worker/tasks.py:3558 `_collect_creator_export(session: Any, ...)`, :2455
  `_brand_kit_style(session: Any, ...)`, :2937/:2963 lifecycle helpers — residual
  `session: Any` params after the Issue-78-R ratchet (which fixed `_r2`,
  `_unmeasured_query`, `_row_to_dict`). | fix: annotate `session: AsyncSession`.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | 1 finding (sessions across LLM calls, SEV2). Much improved since 2026-07-01: the mailer now commits BEFORE the offloaded send (Issue 349); every `pg_try_advisory_lock` site funnels through the shared `_rollback_then_unlock` epilogue (:85-99 — retrain :1041, sweep :2295, poll :2447, media-purge :2734, analytics-purge :2867, catalog :3420, refresh :3532) so a failed transaction can no longer strand a lock; temp media unlinked in `finally` everywhere (ingest, render, clean, edit, summary, export); external clients singletons; render/summary use `with_for_update` + status-skip. |
| 2 Concurrency & scale | 2 findings (generate_clips double-spend race; sessions across LLM calls). Fixed since prior: visibility_timeout now DERIVED from the soft limit (`visibility_timeout_s`, celery_app.py:49-90) so the soft<hard<visibility invariant holds even when operators raise `CELERY_SOFT_TIME_LIMIT_S`; daily refresh keyset-paginated (:3479) + per-creator quota sub-budget (Issue 260) bounds one large channel; export keyset-batched. All blocking work (ffmpeg, librosa, transcription, boto3, Stripe, mailer) offloaded; AsyncAnthropic streams natively on the worker loop (Issue 82a). |
| 3 Security & compliance | ok — Issue 231 moved every per-creator task body onto RLS-gated `db.tenant_session` with the GUC stamped (pipeline, renders, clean/edit, publish, retrain, DNA, briefs, chat — chat additionally re-checks `conv.creator_id != cid` at :4800); `AdminSessionLocal` remains only for documented tenant-id bootstrap, failure-path status writes, and genuine cross-tenant Beat sweeps, each carrying an explicit WHERE. Tokens only via `get_valid_access_token` → `decrypt()`; no PII/token in any logger/aemit line (send_to never logged; SSE errors carry `exc_type` class name only). Parameterized SQL throughout. ToS purges intact (source media, 30-day analytics, event logs); no virality promise. clean/edit still lack a caller-supplied creator_id assert (Issue 231 residual) but RLS + clip-derived tenant now confine any forged message to the clip's own tenant — residual is defense-in-depth polish, tracked. |
| 4 Clip-quality | n/a — orchestration; renders from `setup_start_s` per CLIPPING_PRINCIPLE #2 (`_render_start_for` :1510, encode :1644). |
| 5 Anthropic SDK | ok — `anthropic_stream.py` rewritten async (Issue 82a): `AsyncAnthropic.messages.stream` awaited on the worker loop, per-event forward guarded so a Redis hiccup can't lose `get_final_message()`; cache/token/thinking deltas → SSE; `warn_if_truncated` + `vlog_llm_response` + `record_llm_usage` after every call; stale "0.40" comments gone. Spend guard (Issue 290) gates every paid task before the first token. |
| 6 Cleanliness & typing | 4 cleanup — run_in_executor idiom, `_thumb_redis`/shutdown aclose gap, residual `session: Any` annotations, file-size/brief-runner extraction. No TODO/FIXME/print in the slice. |
| 7 Error handling / API | ok — n/a as a router; posture sound and now CONSISTENT: all three ingest-chain tasks + render_clip + render_summary set `failed` in their `SoftTimeLimitExceeded` branches (prior stuck-spinner gap closed); 402 is terminal with safe copy; SSE errors never carry exception messages. One gap: the swallowed email-send failure (SEV2 above). |
| 8 Config & paths | ok — `RESEND_TIMEOUT_S` added to config.py:723 + .env.example:222 (closes the prior no-Resend-timeout gap); visibility timeout derived in code rather than validated apart; paths absolute (`storage._local_root()` resolves; tempfiles). |

## Resolved since 2026-07-01
- **SEV1 blocking `mailer_send` holding an admin DB conn (tasks.py old :4626)** — FIXED
  (Issue 349): context captured in-session, `session.commit()` frees the connection
  BEFORE the send (:5077-5078), then `asyncio.wait_for(asyncio.to_thread(mailer_send,…),
  timeout=RESEND_TIMEOUT_S)` (:5089-5098) with a fresh short session only to mark
  failure. New `RESEND_TIMEOUT_S=10` in config + .env.example. (Follow-on SEV2 above:
  the failure branch swallows instead of retrying.)
- **SEV2 visibility_timeout ↔ hard-limit invariant unenforced (celery_app.py old
  :62-64)** — FIXED (Issue 352 Batch F): `visibility_timeout_s(soft) = max(3600,
  soft + 300 + 300)` derives the broker setting from `CELERY_SOFT_TIME_LIMIT_S`
  (celery_app.py:49-90); raising the soft limit now raises visibility with it.
- **SEV2 soft-timeout left `ingest_status=running`** — FIXED: `ingest_video` (:288-294),
  `transcribe_video` (:347-353), `build_signals` (:388-394) all set
  `IngestStatus.failed` with a user-facing reason before re-raising terminally.
- **SEV2 advisory-lock unlock-without-rollback (`_retrain` + global analytics purge)** —
  FIXED: shared `_rollback_then_unlock` helper (:85-99) is the `finally` epilogue at all
  seven `pg_try_advisory_lock` sites, including retrain (:1041) and the global
  `purge_stale_youtube_analytics` key (:2867).
- **SEV2 publish done-but-NULL `youtube_video_id` double-post window** — FIXED:
  `youtube_video_id` + `status=done` + the ClipOutcome upsert now commit atomically in
  ONE session (:707-734); legacy done-with-NULL rows are detected and logged (:634-642).
  Uploads also debit a dedicated `videos.insert` bucket (`consume_insert`, :673 — Issue
  352 Batch D) instead of the shared read pool.
- **SEV2 uncapped daily-refresh per-creator video loop** — RESOLVED: keyset-paginated
  batches (:3479-3485) bound memory, and the per-creator daily refresh sub-budget
  (Issue 260, `QuotaSubBudgetExhaustedError` handled :3491-3502) bounds one channel's
  quota/time without starving the fan-out.
- **SEV2 chat under AdminSessionLocal/BYPASSRLS** — RESOLVED (Issue 231):
  `_chat_respond_async` runs on `tenant_session` with an explicit ownership re-check
  (:4795-4804).
- **SEV2 clean/edit ownership re-check** — SUBSTANTIALLY MITIGATED (Issue 231): both
  bodies resolve the tenant FROM the clip row and run under RLS `tenant_session`
  (:1801, :1936), confining any forged message to the clip's own tenant. Residual
  (caller-asserted `creator_id` in the task signature) folded into the security row
  above as tracked polish, no longer a standalone SEV2.
- **SEV2 GDPR export unbounded accumulation** — PARTIALLY RESOLVED (keyset batches,
  plain dicts); residual single-payload assembly downgraded to cleanup (above).
- **cleanup stale anthropic "0.40" comments / dead thinking branch** — RESOLVED:
  anthropic_stream.py rewritten async (Issue 82a); the `thinking_delta` forward is now
  intentional and documented.
- **cleanup typing gaps** — MOSTLY RESOLVED (Issue 78-R ratchet): `_r2() -> Any`,
  `_unmeasured_query(...) -> Select[tuple[Video]]`, `_row_to_dict(obj: db.Base)`.
  Residual `session: Any` params re-flagged as cleanup.
- **Issue 353 verification (this run's method addition)** — CORRECT worker-side: the
  endpoint's done→pending + `render_uri=None` reset (single transaction with the merged
  style) defeats the worker's `done AND render_uri` skip guard (:1556) exactly as
  intended; redelivered stale messages still no-op; the fixed R2 key keeps re-renders
  idempotent. One failure-path defect filed (SEV2 above: prior-good `render_uri`
  discarded if the re-render fails permanently).
- **Carried unchanged**: `get_event_loop().run_in_executor` (:3106); `_thumb_redis`
  loop-rebind + shutdown-aclose gap (now partially addressed by the Issue-109b registry,
  which the worker shutdown hook doesn't invoke); brief-runner extraction / file size
  (4765 → 5413 lines).

## Module verdict
NEEDS-WORK — a markedly improved slice: all four highest-risk 2026-07-01 findings (the
blocking-mailer SEV1, the visibility-timeout invariant, the stuck-spinner soft-timeout
gap, and the advisory-lock leak) are verifiably fixed, and Issue 231 put every tenant
task under RLS. No BLOCKER, no cross-tenant leak. Remaining work: the swallowed
transient email-send failure (permanent email loss behind the dedupe ledger), the
generate_clips double-LLM-spend race (still lacking build_dna's xact-lock pattern), DB
transactions held across LLM calls in three tasks, and the Issue-353 failure path
discarding a previously-good render pointer.
