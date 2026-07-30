# worker — assessed 2026-07-29 (ready-pass delta)

Slice: `worker/__init__.py`, `worker/anthropic_stream.py`, `worker/celery_app.py`,
`worker/progress.py`, `worker/schedule.py`, `worker/storage.py`, `worker/tasks.py`
(now 5784 lines). DELTA re-assessment against the 2026-07-20 post-fix record (below):
full review of the e92b93a..HEAD ready-pass diff (+364/−194 in tasks.py across d7230c4,
83dec23, 44e386f, 13930c3, e816494, eff0d28, 57f03a3, 452a700) plus targeted re-reads of
every changed task for idempotency/retry-safety, RLS GUC preservation across the new
session splits, refund-exactly-once, publish metadata correctness (incl. rolling-deploy
skew), and advisory-lock release under failure.

## 2026-07-29 ready-pass delta

### Findings (severity-ranked)

- [SEV2] worker/tasks.py:511-512 — CARRY-FORWARD, unchanged in substance: `build_signals`
  still enqueues `generate_clips.delay(video_id)` unconditionally even when
  `_signals_async` idempotently short-circuited on redelivery, so a redelivered
  build_signals still runs the paid 30–120 s Anthropic scoring twice (rows backstopped by
  `uq_clips_video_rank`; loser discarded at commit — double SPEND only). The NEW enqueue
  guard (:513-533) covers broker-publish failure, not the idempotent-skip case. | fix
  unchanged: have `_signals_async` report its short-circuit and skip the enqueue, or take
  `pg_advisory_xact_lock(hashtext('genclips:' || video_id))` before scoring.

- [SEV2] routers/clips.py (owner: routers; tracked here since this run re-verified the
  render lifecycle) — CARRY-FORWARD: the manual re-render reset still nulls `render_uri`
  before the worker runs, so a permanent re-render failure ends `failed` with
  `render_uri=None`, losing the pointer to the prior-good artifact. | fix unchanged:
  don't null `render_uri` at the endpoint; key the player off `render_status != done`.

- [cleanup] worker/tasks.py:4124-4159 (NEW — noticed while verifying the brief session
  split; same exposure existed pre-split, so NOT a regression) — the brief's ready-write
  phase sits OUTSIDE the try: if the fresh write session or its commit fails, no
  `_mark_failed` and no SSE error fire, and the Celery retry re-runs the full paid ~120 s
  LLM call (the `row.job_id == job_id and status == ready` redelivery short-circuit can't
  help because ready was never written); four consecutive write failures leave the row
  stuck non-terminal for the poller. Narrow window (needs a DB failure timed exactly
  between LLM success and commit; `record_llm_usage` itself is no-raise). | fix: wrap the
  write phase in its own try → `_mark_failed(...)` + error emit + re-raise.

- [cleanup] worker/tasks.py:1916-1917 — the batch auto-render path
  (`_render_video_clips_async`) still raises plain `ValueError` for a missing source at
  the batch level, so that failure mode gets no actionable SSE (per-clip renders inside
  the batch DO get the SourceExpiredError message via `_render_clip_async`). Implausible
  window — auto-render runs seconds after ingest, retention is 72 h — but inconsistent. |
  fix: raise `SourceExpiredError` there too for message parity.

- [cleanup] worker/tasks.py:877 — publish success log hardcodes `"private"` while the
  actual upload privacy is `settings.YOUTUBE_PUBLISH_PRIVACY` (:824); the log lies to an
  operator the day the setting changes. Pre-existing line, surfaced by the publish-path
  re-audit. | fix: log the setting value.

- [cleanup] tests/test_render_recovery.py — the sweep photo-finish fix (conditional
  UPDATE, resolved below) has no regression test asserting a row that turned `done`
  between SELECT and commit is NOT flipped; `test_sweep_flips_only_stale_rows_to_failed`
  covers staleness selectivity only. | fix: one test stubbing the row to `done` before
  the sweep's commit and asserting rowcount 0 / status preserved.

- [cleanup] carry-forwards, all still open: `asyncio.get_event_loop().run_in_executor`
  idiom at tasks.py:3345 (→ `asyncio.to_thread`); `_worker_redis` (tasks.py:73) still
  lacks the loop-rebind guard and is never aclosed — `_shutdown_worker_loop`
  (celery_app.py:137) still doesn't call `shared_resources.close_all()`; GDPR export
  still accumulates the full payload in memory (tasks.py:3896-3902); tasks.py at 5784
  lines still wants the brief-runner + render-plan extraction; `session: Any` residuals
  at tasks.py:148, :186, :216, :1200, :2697, :3176, :3202, :3786.

### Delta-change review (verified sound, with evidence)

- **`SourceExpiredError` + actionable SSE (83dec23) — correct.** `ValueError` subclass
  (tasks.py:1738-1747) so `render_clip`'s permanent/no-retry classification (:581-592)
  and `render_video_clips`' batch classification are untouched; both raise sites
  converted (`_load_clip_render_plan`:1739, `_load_summary_render_plan`:5643); both async
  renderers emit the actionable message BEFORE re-raising and ahead of the generic
  handler (:1868-1878, :5705-5715), so no double emit; plan loaders still short-circuit
  already-rendered rows (redelivery-idempotent). Test:
  `test_render_clip_async_source_expired_emits_actionable_message`.
- **Render log relabels (44e386f) — correct.** `render_clip`/`render_summary`
  started/failed_permanent/done events now carry `clip_id`/`summary_id` instead of the
  mislabeled `video_id`; log-only change (any operator dashboard keyed on the old field
  needs a one-time update).
- **`build_signals` enqueue guard (e816494) — correct, refund-exactly-once holds.**
  Broker-publish failure marks the video `failed` with actionable copy, logs
  `generate_clips_enqueue_failed`, and re-raises WITHOUT `self.retry` (right call — the
  retry message would go to the same dead broker), so `RefundOnFailureTask.on_failure`
  fires. Refund is idempotent on the partial UNIQUE index `minute_packs(pack_id) WHERE
  reason='refund'` with `pack_id=refund:{video_id}` (billing/refund.py, migration 0013;
  IntegrityError → clean no-op), so duplicate on_failure invocations under at-least-once
  delivery refund once. The refund NOTIFICATION enqueue (tasks.py:368) also hits the dead
  broker in this scenario — caught and logged in on_failure (:330-338); refund stands,
  notification lost (acceptable). `auto_render_enqueued` now fires only on successful
  publish, with an `auto_render_enqueue_failed` counterpart (:2882-2905). Test:
  `test_build_signals_enqueue_failure_marks_failed_and_refunds_once`.
- **Publish consumes applied metadata (13930c3) — correct, incl. skew posture.**
  `applied_title`/`applied_description` (migration 0047) read at RUN time in the same
  tenant session (:787-792), so a PATCH landing between enqueue and execution is honored.
  The worker's trust in pre-validation is backed: the ONLY writer is PATCH /clips/{id}
  whose validators enforce `max_length=100`, no `<`/`>`, description ≤5000 UTF-8 bytes
  (routers/clips.py:183-209) — matching YouTube's documented videos.insert limits; the
  fallback path defensively bracket-strips + caps `[:100]` with a `"New Short"` backstop;
  empty-string applied_title falls through to the fallback (`if clip.applied_title:`).
  Publish idempotency guard (done + youtube_video_id) unchanged. Rolling-deploy skew: an
  OLD worker + NEW schema publishes the fallback title during the deploy window (applied
  metadata silently ignored — transient, bounded to the compose bounce); a NEW worker +
  OLD schema fails loudly on the unknown column until 0047 is applied — deploy runs
  migrations first (needs-runtime-confirmation only on that ordering). Tests:
  `test_publish_uses_applied_metadata`, `test_publish_falls_back_when_applied_metadata_null`.
- **Advisory-lock hardening (eff0d28) — correct on every axis checked.** All 8
  `pg_try_advisory_lock` sites converted to `_try_advisory_lock` (:1153, :2417, :2490,
  :2595, :2958, :3061, :3500, :3669), each still paired with `_rollback_then_unlock` in a
  `finally`; skips now observable (`beat_lock_skip` event + `BEAT_LOCK_SKIPS_TOTAL`,
  label = task portion of the key with per-creator suffixes stripped — bounded
  cardinality). The guarded unlock: rollback/unlock failure → log + event +
  `session.invalidate()` so the connection is DISCARDED and PostgreSQL frees the
  session-level lock at backend exit (the Issue-143 leak class — a poisoned pooled
  connection permanently holding the lock and silently disabling the sweep — is closed);
  invalidate itself is suppressed so the sweep's own exception still propagates through
  the caller's finally. `build_dna`'s xact-scoped lock correctly untouched. 7 unit tests
  in `tests/test_worker_advisory_locks.py` (happy path, rollback-failure invalidate,
  unlock-failure invalidate, invalidate-failure swallowed, skip metric + label strip).
- **Session splits (57f03a3) — RLS GUC preserved everywhere; the 2026-07-20 SEV2 is
  RESOLVED.** Brief: read phase snapshots plain values (`analytics` dict,
  `channel_title`, `dna_brief`) and closes before the ~120 s `build_brief` call;
  `_mark_failed` and the ready-write each re-acquire a FRESH `db.tenant_session(cid)`
  (GUC re-stamped by the `after_begin` listener per session); the write phase re-fetches
  rather than merging the detached row and handles row-vanished (erasure) cleanly.
  Analysis: session closed before `build_analysis`; the post-close
  `creator.channel_title` read is a loaded attribute on a detached instance with
  `expire_on_commit=False` and no commit in the read phase — no implicit IO. Chat:
  `run_chat_turn` now takes a session FACTORY (`partial(db.tenant_session, cid)`); the
  agentic loop holds no session across LLM round-trips — each tool executes in its own
  short-lived tenant session (chat/runner.py:188-191) and the usage-ledger write opens
  another (:230-239), every one RLS-stamped; the write phase re-fetches the conversation
  under a fresh tenant session; `max_retries=0` keeps the no-double-spend posture.
  `_build_dna_async` remains the one justified holder (xact lock). Tests: 5 in
  `tests/test_worker_session_release.py` (read-closed-before-LLM ×2, failed-mark on
  fresh session, redelivery skip, chat split) + updated `tests/test_chat.py`.
- **Thumbnail-patterns billing (452a700) — correct at both compute paths.**
  `analyze_thumbnail_patterns` now returns `(patterns, usage)`; the worker compute path
  bills at tasks.py:4654 and the router compute path bills inside its single-flight
  compute closure (routers/thumbnails.py:236) — Redis-cache hits and single-flight
  waiters bill nothing (right: no LLM call). The multiplier's ABSENCE on patterns is
  correct: that call sends no system blocks / no cache_control (the `cache_1h` flag in
  knowledge/thumbnails.py:368 belongs to `generate_thumbnail_concepts`, whose billing
  site does pass `cache_write_multiplier=2.0` at :4698, as does titles at :4474).
  `record_llm_usage` is no-raise by design, so billing cannot poison the split phases.
- **Sweep photo-finish flip (d7230c4) — the 2026-07-20 SEV2 is RESOLVED.** The stale
  flip is now a conditional UPDATE gated on `render_status == running` for both Clip and
  Summary (:2455-2463) with the warning log rowcount-gated — a render committing `done`
  between the sweep's SELECT and commit is untouched. (Regression-test gap noted above.)

### Resolved since 2026-07-20
- **SEV2 tenant sessions held across 30–120 s LLM calls in brief/analysis/chat** —
  RESOLVED (57f03a3), verified above with tests.
- **SEV2 sweep_stale_renders unconditional flip could clobber a photo-finish `done`** —
  RESOLVED (d7230c4 conditional UPDATE).
- **Advisory-lock leak-on-unlock-failure** (previously folded into the `_worker_redis`
  cleanup posture) — RESOLVED (eff0d28 guarded invalidate + skip observability).
- **Unbilled thumbnail-patterns multimodal call** — RESOLVED (452a700, both paths).
- **`auto_render_enqueued` fired even when the enqueue failed** — RESOLVED (e816494).
- **Generic "Render failed." on the enqueue-to-run source-purge race** — RESOLVED
  (83dec23 actionable SSE, Issue 362 residual).

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — the LLM-under-session SEV2 is RESOLVED; every split re-acquires via `tenant_session`; temp-media/finally posture unchanged. Residual: `_worker_redis` aclose/loop-rebind cleanup. |
| 2 Concurrency & scale | 1 SEV2 carry-forward (build_signals redelivery double-LLM-spend); sweep flip race fixed; lock skips now observable; splits shorten connection hold times materially under load. |
| 3 Security & compliance | ok — RLS GUC re-stamped on every re-acquired session (verified per split + per tool call); publish metadata validated at the API boundary (≤100 chars, no `<>`, ≤5000 bytes) matching YouTube ToS-documented limits; new log lines carry ids/lock keys only, no PII/token. |
| 4 Clip-quality | n/a — orchestration. |
| 5 Anthropic SDK | ok, improved — patterns call billed at both compute paths; 1h cache-write 2× multiplier threaded at title/thumbnail-concepts and correctly ABSENT on the uncached patterns call; token logging preserved. |
| 6 Cleanliness & typing | 7 cleanup (2 new: brief write-phase guard, publish log literal; plus carry-forwards). No TODO/print introduced. |
| 7 Error handling / API | ok, improved — actionable SSE on expired source; broker-failure enqueue guard with refund-exactly-once (partial-unique-backed); brief write-phase failure gap noted as cleanup. |
| 8 Config & paths | ok — no new env knobs; publish privacy still config-driven (`YOUTUBE_PUBLISH_PRIVACY`). |

## Module verdict
NEEDS-WORK — all seven ready-pass changes verified correct and tested (SourceExpiredError
classification + actionable SSE, log relabels, enqueue guard with idempotent refund,
applied-metadata publish with boundary-validated trust, guarded advisory-lock release
with invalidate-on-failure across all 8 sites, RLS-preserving session splits in
brief/analysis/chat, thumbnail-patterns billing at both compute paths). No BLOCKER, no
SEV1. Remaining: the build_signals redelivery double-LLM-spend SEV2 (carry-forward,
spend-only), the routers-owned render_uri reset residual, and cleanup-grade items (brief
write-phase failure guard, sweep photo-finish regression test, `_worker_redis` lifecycle,
file-size extraction).

---

# Archived: worker — assessed 2026-07-20 (post-fix)

Slice: `worker/__init__.py`, `worker/anthropic_stream.py`, `worker/celery_app.py`,
`worker/progress.py`, `worker/schedule.py`, `worker/storage.py`, `worker/tasks.py`
(then 5614 lines). Re-assessment after the two 2026-07-20 fix waves (ca3305c..e92b93a):
every finding from that morning's run re-verified against HEAD, plus a full review of the
wave diff for new regressions — the Issue-359 stale-render recovery (Redis markers +
`sweep_stale_renders` Beat task + 409-override), the Issue-359-companion notification
retry (status-aware dedupe + re-raise), the Issue-361 `uq_clips_video_rank` race
backstop, and the new `stream_until_final` pause_turn helper.

## Findings (2026-07-20 — see delta section above for current status)

- [SEV2 → RESOLVED 2026-07-29] worker/tasks.py `_generate_improvement_brief_async` /
  `_generate_video_analysis_async` / `_chat_respond_async` — each held an open
  `tenant_session` transaction across the 30–120 s Claude round-trip (idle-in-
  transaction connection pinned per busy worker; blocks vacuum xmin; counts against
  max_connections). Fixed by the ready-pass session splits (57f03a3).

- [SEV2 — STILL OPEN] worker/tasks.py build_signals unconditional `generate_clips.delay`
  — double LLM SPEND on redelivery; rows backstopped by `uq_clips_video_rank`
  (migration 0046 + ranking.py IntegrityError→winner). Carried in the delta section.

- [SEV2 → RESOLVED 2026-07-29] `_sweep_stale_renders_async` unconditional ORM flip could
  overwrite a photo-finish `done` → `failed`. Fixed by d7230c4's conditional UPDATE.

- [SEV2 — STILL OPEN, owner routers] routers/clips.py render reset nulls `render_uri`
  before the worker runs; permanent re-render failure loses the prior-good artifact
  pointer. Carried in the delta section.

- [cleanup — STILL OPEN] `asyncio.get_event_loop().run_in_executor` idiom (now :3345);
  `_worker_redis` loop-rebind/aclose gap + `_shutdown_worker_loop` never calls
  `close_all()`; GDPR export full-payload accumulation; brief-runner/render-plan
  extraction (file still growing); `session: Any` residuals.

## Wave-change review (2026-07-20 — new code verified sound)

- **`send_notification` retry (Issue 359 companion) — FIXED as specified.** The mailer
  failure branch marks the delivery `failed` AND re-raises so the task's `self.retry`
  ladder fires; the dedupe short-circuit is status-aware: on `IntegrityError` it loads
  the existing row and proceeds iff `status == failed`, adopting the row, flipping it
  back to `sent` in the same transaction, reloading creator/prefs after the rollback
  (MissingGreenlet guard), and suppressing the duplicate in-app row via
  `retry_of_failed`. Resend's `Idempotency-Key` covers the timeout-but-sent race.
- **`sweep_stale_renders` (Issue 359)** — idempotent one-way flip; re-runs/overlaps
  serialized by `pg_try_advisory_lock` with the shared `_rollback_then_unlock` epilogue;
  marker semantics: absent/unparseable → stale, Redis READ errors fail closed as fresh;
  marker stamped only AFTER the `running` commit; threshold derived from the Celery
  limits. `AdminSessionLocal` use allowlisted as a genuine cross-tenant sweep; Beat
  schedule at 15 min. 9 unit tests in `tests/test_render_recovery.py`.
- **`stream_until_final` (worker/anthropic_stream.py:201-256)** — bounded at
  `max_rounds + 1` calls; usage sums all four counters across every round; continuation
  re-sends paused assistant content with the SAME tools. All 4 call sites migrated with
  summed token logging preserved.

## Module verdict (2026-07-20, superseded)
NEEDS-WORK — both targeted fixes verified correct and well-tested. No BLOCKER, no SEV1.
Remaining then: the sweep photo-finish flip race (now fixed), the double-LLM-spend
residual (still open), DB transactions across LLM calls in three tasks (now fixed), and
the routers-owned render_uri residual (still open).
