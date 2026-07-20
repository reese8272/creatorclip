# worker — assessed 2026-07-20 (post-fix)

Slice: `worker/__init__.py`, `worker/anthropic_stream.py`, `worker/celery_app.py`,
`worker/progress.py`, `worker/schedule.py`, `worker/storage.py`, `worker/tasks.py`
(now 5614 lines). Re-assessment after the two 2026-07-20 fix waves (ca3305c..e92b93a):
every finding from this morning's run re-verified against HEAD, plus a full review of the
wave diff for new regressions — the Issue-359 stale-render recovery (Redis markers +
`sweep_stale_renders` Beat task + 409-override), the Issue-359-companion notification
retry (status-aware dedupe + re-raise), the Issue-361 `uq_clips_video_rank` race
backstop, and the new `stream_until_final` pause_turn helper.

## Findings

- [SEV2] worker/tasks.py:3886/:3992 (`_generate_improvement_brief_async`), :4106/:4200
  (`_generate_video_analysis_async`), :4945/:4981 (`_chat_respond_async` —
  `run_chat_turn(..., session)`) — STILL OPEN, untouched by both waves: each holds an
  open `tenant_session` transaction across the 30–120 s Claude round-trip (idle-in-
  transaction connection pinned per busy worker; blocks vacuum xmin; counts against
  max_connections). Title (:4302), thumbnail (:4449), hook (:4641), chapters (:4757)
  still show the correct release-before-call shape; `_build_dna_async` remains the one
  justified exception (xact-lock must live in the transaction). (needs-runtime-
  confirmation under load) | fix: snapshot needed fields, close the session before the
  LLM call, reopen to persist (shape of `_generate_title_suggestions_async`); for chat,
  pass a session-FACTORY into `run_chat_turn`.

- [SEV2] worker/tasks.py:464 + clip_engine/ranking.py — PARTIALLY RESOLVED, residual is
  double LLM SPEND only: migration 0046 adds `uq_clips_video_rank` (DEFERRABLE, checked
  at COMMIT) and `persist_ranked_clips` now catches the loser's `IntegrityError` →
  rollback → returns the winner's set (ranking.py:237-248; integration test
  `test_generate_clips_idempotency_integration.py` green) — duplicate ROWS can no longer
  land. But the `build_signals` wrapper still enqueues `generate_clips.delay(video_id)`
  unconditionally at :464 even when `_signals_async` idempotently skipped, so a
  redelivered build_signals (or a router race, now rarer behind the Issue-357
  flag+budget gate) still runs the full paid 30–120 s Anthropic scoring twice and
  discards the loser at commit. | fix: skip the :464 enqueue when the signals body
  reports it short-circuited, or take `pg_advisory_xact_lock(hashtext('genclips:' ||
  video_id))` before scoring (build_dna's pattern).

- [SEV2] worker/tasks.py:2427-2437 (NEW — wave regression in `_sweep_stale_renders_async`)
  — the stale flip is an unconditional ORM attribute write (`row.render_status =
  RenderStatus.failed`) on a row SELECTed earlier in the sweep, with no re-check at
  write time. Photo-finish ordering: sweep SELECTs the row as `running` (marker absent —
  e.g. the best-effort `_amark_render_started` write failed at :113-124, which the code
  explicitly tolerates), the live render then commits `done` + `render_uri`, and the
  sweep's later commit overwrites `done` → `failed` — a completed, watchable render
  presents as failed with no self-heal (the reverse ordering self-heals; this one does
  not). Probability is small (needs marker-write failure + a finish inside the sweep's
  body) but the fix is one line. | fix: flip via conditional UPDATE —
  `update(Clip).where(Clip.id == row.id, Clip.render_status == RenderStatus.running)
  .values(render_status=RenderStatus.failed)` (same for Summary) — so a row that left
  `running` between SELECT and commit is untouched; add a unit test asserting a
  done-during-sweep row is not flipped.

- [SEV2] routers/clips.py:500-535 (owner: routers; tracked here since this run verified
  the render lifecycle) — PARTIALLY RESOLVED: Issue 359c now snapshots
  `render_status`/`render_uri` before the done→pending reset and restores both when
  `.delay()` throws (broker outage no longer strips a watchable clip with no task
  coming), and the 409 gate consults `ais_render_stale` so stuck-`running` rows regain
  the retry affordance. STILL OPEN: the reset itself still nulls `render_uri` before the
  worker runs, so a PERMANENT re-render failure (all 3 worker retries exhausted) still
  ends `failed` with `render_uri=None` — the pointer to the prior-good artifact (un-
  overwritten at `clips/{id}.mp4`) is lost. | fix unchanged: don't null `render_uri` in
  the endpoint (the worker overwrites on success); key the player unmount off
  `render_status != done`.

- [cleanup] worker/tasks.py:3256 (carry-forward) — `asyncio.get_event_loop()
  .run_in_executor(None, list_recent_paid_sessions, ...)`: soft-deprecated idiom on
  3.12, inconsistent with `asyncio.to_thread` used everywhere else. | fix:
  `await asyncio.to_thread(list_recent_paid_sessions, ...)`.

- [cleanup] worker/tasks.py:71-88 + worker/celery_app.py (carry-forward, now MORE
  load-bearing) — `_thumb_redis` was renamed `_worker_redis` and now also carries the
  Issue-359 render-start markers, but it still lacks the loop-rebind guard that
  `progress._async_client()` documents, is still registered nowhere for aclose, and
  `_shutdown_worker_loop` still never calls `shared_resources.close_all()`.
  Shutdown-time connection leak only. | fix: share a loop-aware factory with progress,
  register aclose, invoke `close_all()` in `_shutdown_worker_loop`.

- [cleanup] worker/tasks.py:3708-3781 (carry-forward) — GDPR export reads via bounded
  `_keyset_batches` but `_collect_creator_export` still accumulates the full history
  into one payload dict + one `json.dump` buffer; acceptable at beta scale. | fix (when
  scheduled): stream NDJSON per data-class.

- [cleanup] worker/tasks.py — 5614 lines (was 5413; carry-forward): the brief-style LLM
  tasks still share the ~120-line shape and render_summary still mirrors render_clip's
  plan/encode/status trio. The wave DID extract the four pause_turn loops into
  `stream_until_final` (right direction); the brief-runner + render-plan extraction
  remains. | fix: `worker/_brief_runner.py` + a shared render-plan helper.

- [cleanup] worker/tasks.py:148, :169, :1126, :2605, :3087, :3113, :3708 (carry-forward)
  — residual `session: Any` params after the 78-R ratchet. | fix: annotate
  `session: AsyncSession`.

## Wave-change review (new code verified sound)

- **`send_notification` retry (Issue 359 companion) — FIXED as specified.** The mailer
  failure branch now marks the delivery `failed` AND re-raises (tasks.py:5312-5319) so
  the task's `self.retry` ladder (max_retries=3, delay 30, :5071) fires; the dedupe
  short-circuit is status-aware (:5188-5233): on `IntegrityError` it loads the existing
  row and proceeds iff `status == failed`, adopting the row, flipping it back to `sent`
  in the same transaction, reloading creator/prefs after the rollback (MissingGreenlet
  guard), and suppressing the duplicate in-app row via `retry_of_failed`. Resend's
  `Idempotency-Key` covers the timeout-but-sent race. Tests cover fail-then-redeliver
  (`test_failed_delivery_row_is_retried`, `test_mailer_failure_marks_delivery_failed_and_reraises`).
- **`sweep_stale_renders` (Issue 359) — correct on every axis checked but one** (the
  photo-finish SEV2 above). Idempotent: one-way running→failed flip; flipped rows leave
  the selection; re-runs/overlaps serialized by `pg_try_advisory_lock` with the shared
  `_rollback_then_unlock` epilogue in `finally` (:2438) and a clean no-unlock early
  return when the lock is held (:2404-2406). Marker semantics as designed: absent or
  unparseable → stale (pre-fix rows and Redis-flush rows stay recoverable); Redis READ
  errors fail closed as fresh (:127-145) so an outage cannot trigger a re-render storm;
  marker stamped only AFTER the `running` commit for both clips (:1680) and summaries
  (:5493); threshold derived from the Celery limits (`render_stale_after_s`, :105-110,
  soft + HARD_LIMIT_MARGIN_S + 300). `AdminSessionLocal` use is a genuine cross-tenant
  sweep, allowlisted in `tests/test_worker_invariants.py:169`; Beat schedule registered
  at 15 min (worker/schedule.py:41-44, asserted by
  `test_beat_schedule_registers_stale_render_sweep`). 9 unit tests in
  `tests/test_render_recovery.py` cover marker semantics, fail-closed, threshold
  derivation, lock skip, selective flip, noop, and schedule registration.
- **`stream_until_final` (worker/anthropic_stream.py:201-256) — correct.** Bounded at
  `max_rounds + 1` total calls with a for-else round-cap warning that fires only when
  the last round still paused; usage sums all four counters across every round (billing
  correctness); continuation re-sends the paused assistant content with the SAME tools;
  API errors propagate to the callers' `log_llm_error`. All 4 call sites migrated
  (knowledge/titles.py:245, hooks.py:248, thumbnails.py:329, improvement/brief.py:144)
  and each still logs summed tokens + `record_llm_metric`; `warn_if_truncated` +
  `vlog_llm_response` fire per round inside `stream_message`. Tests:
  `test_stream_until_final_continues_on_pause_turn_and_sums_usage`,
  `test_stream_until_final_bounds_rounds_and_warns`.
- No stale `_thumb_redis`/`_THUMB_REDIS` references remain; only the two `running`
  writers exist and both stamp markers.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | 1 finding (sessions across LLM calls, SEV2, carry-forward). Advisory-lock epilogue pattern correctly extended to the new sweep; temp-media/finally posture unchanged and sound. |
| 2 Concurrency & scale | 3 findings — double-LLM-spend residual (rows now backstopped by uq_clips_video_rank), the NEW sweep photo-finish flip race, LLM-under-session. Sweep work is bounded (running rows only, 15-min cadence). |
| 3 Security & compliance | ok — sweep logs entity id + creator id only (operator correlation, no PII); notification retry path logs dedupe_key/event/creator, never send_to; RLS/tenant_session posture unchanged from the morning run; `_sweep_stale_renders_async` properly allowlisted as a cross-tenant Beat sweep. |
| 4 Clip-quality | n/a — orchestration. |
| 5 Anthropic SDK | ok, improved — `stream_until_final` consolidates the pause_turn loops with cross-round usage summing (a final-round-only figure would under-bill); caching/token-logging/warn hooks preserved at all 4 call sites. |
| 6 Cleanliness & typing | 5 cleanup (all carry-forwards; `_worker_redis` note upgraded in load-bearing-ness). No TODO/print introduced by the waves. |
| 7 Error handling / API | ok — the swallowed-email-failure SEV2 is FIXED (re-raise + status-aware dedupe); render endpoint's stale-409 override + enqueue-failure restore improve the recovery story; residual render_uri-on-permanent-failure tracked above (owner routers). |
| 8 Config & paths | ok — Issue-359 thresholds derived from existing `CELERY_SOFT_TIME_LIMIT_S` + code constants (no new env knob needed); no new config introduced in worker/. |

## Resolved since the 2026-07-20 morning run
- **SEV2 `_send_notification_async` swallowed failed sends behind the dedupe ledger** —
  FIXED (Issue 359 companion, commit cb8c54b): failure branch re-raises into the task
  retry ladder; dedupe is status-aware (`failed` rows adopted and re-sent, `sent`/
  `skipped` short-circuit); in-app row not duplicated on retry; unit tests added.
- **SEV2 `persist_ranked_clips` double-INSERT half of the generate_clips race** — FIXED
  (Issue 361, migration 0046 + ranking.py IntegrityError→winner; dedupe-first migration,
  DEFERRABLE so `rerank_with_preference`'s rank permutation still works). The
  double-SPEND half remains (SEV2 above, narrowed).
- **NEW capability, verified: stale-render recovery (Issue 359)** — `running` rows
  orphaned by SIGKILL/OOM/deploy are now swept to `failed` within ~hard-limit + 15 min,
  and the render endpoint's 409 yields to a stale override; one photo-finish flip race
  filed (SEV2 above).
- **Carried unchanged**: LLM-under-session (3 tasks); build_signals unconditional
  enqueue; get_event_loop idiom (:3256); `_worker_redis` loop-rebind/aclose gap; GDPR
  export accumulation; brief-runner/file-size extraction; `session: Any` residuals;
  routers render_uri-on-reset residual (enqueue-failure path now restored).

## Module verdict
NEEDS-WORK — both targeted fixes verified correct and well-tested (notification
status-aware dedupe + re-raise; sweep_stale_renders idempotent, lock-epilogued,
fail-closed on Redis reads, allowlisted, scheduled; stream_until_final sums usage and
caps rounds properly across all 4 call sites). No BLOCKER, no SEV1. Remaining: one NEW
narrow race (sweep's unconditional flip can overwrite a photo-finish `done` — one-line
conditional-UPDATE fix), the double-LLM-spend residual of the generate_clips race, DB
transactions still held across LLM calls in three tasks, and the routers-owned
render_uri permanent-failure residual.
