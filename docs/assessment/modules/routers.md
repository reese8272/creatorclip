# routers — assessed 2026-07-20 (post-fix)

Layer-1 re-assessment after the two fix waves (`ca3305c..e92b93a`). Method: every
finding from the 2026-07-20 morning record re-verified at HEAD; `git diff
ca3305c..HEAD -- routers/` (clips.py only, +86/−16) read in full for regressions
(Issue 357 gate, Issue 359/359c render recovery, Issue 361 summary race);
supporting artifacts traced outside the slice only to verify load-bearing claims
(migration 0046, models.py Summary index, worker `ais_render_stale` /
`_sweep_stale_renders_async`, tests/test_flags.py gate tests).

## Resolved since the 2026-07-20 morning run

- **[was SEV1] `/clips/generate` missing kill switch + spend guard** — FIXED and
  correct: clips.py:222-228 now stacks
  `dependencies=[Depends(require_flag("llm_generation")), Depends(require_budget)]`
  exactly like the sibling LLM routes; both imported at module top (:21, :24);
  gate tests exist (tests/test_flags.py:183-220 — 503 flag-off, budget path).
- **[was SEV2] `create_summary` check-then-insert double-render race** — FIXED
  and correct: migration 0046 creates partial unique index `uq_summaries_active
  ON summaries (video_id) WHERE render_status IN ('pending','running')`
  (CONCURRENTLY inside an autocommit block, pre-deduping existing dup rows by
  flipping older ones to `failed`), mirrored in models.py:841-849. The handler
  wraps the commit in `except IntegrityError: rollback → re-select winner via
  _active_summary → return winner's queued envelope` (clips.py:1664-1683), with
  a 409 fallback if the winner already left the in-flight window — exactly one
  `render_summary` job can ever be enqueued. Regression test landed (3478d50).

## Findings

Still open (carried, re-verified at HEAD):

- [SEV2] routers/clips.py:264-292 — `/clips/generate` still awaits the LLM
  scoring pass inside the request/response cycle (session closed at :271 before
  `score_and_rank` :273 — the Issue-82b pool fix holds; RLS restamp at :289
  verified) while every other LLM surface is 202 + Celery + SSE
  (needs-runtime-confirmation on p95 vs LB idle timeout). | fix: convert to the
  202 + `TaskQueuedOut` + `aset_owner` pattern; the idempotency guard at :260
  already makes a worker retry safe.
- [SEV2] routers/clips.py:1551 — `create_summary` still stacks only
  `require_flag("render_intake")`; the sibling render-intake routes
  (`/render` :437, `/clean` :675, `/cuts` :854) all add `require_budget` — a
  creator in spend cool-down (429 everywhere else) can still queue recap
  renders. The wave fixed this exact gap on `/clips/generate` but not here. |
  fix: add `Depends(require_budget)` to the :1551 dependency list.
- [SEV2] auth.py:83,356; review.py:157; creators.py:461,517,568,638,743 —
  `asyncio.ensure_future(record_event(...))` fire-and-forget at 8 sites: task
  handle never stored, so CPython may GC it before completion → silently
  dropped activation/funnel telemetry (`clip_kept`, `oauth_completed`,
  `identity_saved`, `data_gate_evaluated`) under load. Unchanged by the waves. |
  fix: `await` inline (record_event never raises) or `asyncio.create_task` with
  handles in a module-level `set()` + `.add_done_callback(s.discard)` — one
  shared helper for all 8 sites.
- [SEV2] review.py:172-177 — `retrain_preference.delay` still enqueued on EVERY
  feedback write (120/min limit); the task self-debounces only after dequeue,
  so up to 120 broker messages/min per feedback-clicking creator. | fix:
  debounce at enqueue with a per-creator Redis `SET NX EX 60` key.
  (needs-runtime-confirmation that broker churn is material at beta scale.)

NEW (from the wave diffs):

- [SEV2] routers/clips.py:1694 — `create_summary` enqueue is NOT
  failure-protected: the pending Summary row commits at :1664 BEFORE
  `await asyncio.to_thread(render_summary_task.delay, ...)` at :1694 with no
  try/except. A broker throw → 500 with the pending row persisted; that row is
  then returned forever by the idempotency probe (:1596) AND hard-protected by
  the new `uq_summaries_active` index, and the Issue-359 stale sweep
  (worker/tasks.py:2383-2447) recovers only `render_status == running` rows —
  a stuck `pending` summary never gets a render-start marker sweep, so the
  video's recap is permanently blocked with no user-visible recovery path.
  render_clip received exactly this protection this wave (359c,
  clips.py:519-534) but create_summary did not. | fix: mirror 359c — wrap the
  `.delay()` in try/except, flip the row to `render_status=failed` (or delete
  it) + commit, return 503 "could not queue — try again"; add an
  enqueue-raises regression test.

Still open cleanups (carried, re-verified):

- [cleanup] clips.py:262,282,292 — `generate_clips` returns bare
  `{"clips": [...]}` so `ClipListOut`'s default `state="populated"` is emitted
  even on the zero-candidate `return {"clips": []}` path — contradicts the
  list path's empty-state envelope. | fix: return
  `state=build_envelope_state(len(items))` + honest empty message.
- [cleanup] chat.py:199-244 — `list_conversations` / `get_messages` still bare
  `dict`, no `response_model=` (OpenAPI-undocumented, unvalidated outbound). |
  fix: `ConversationListOut` / `ConversationMessagesOut` models.
- [cleanup] insights.py:172,294 — internal `_compute_virality_score` name
  persists (wire field remains `performance_score`; no promise on the wire). |
  fix: rename `_compute_performance_score`.
- [cleanup] tasks.py:81 — `_event_stream` still uses `asyncio.get_event_loop()`
  inside a running coroutine (deprecated path). | fix: `get_running_loop()`.
- [cleanup] creators.py:153-157 — `_upsert_style_field(creator_id: object, ...,
  value: object)` still loosely typed. | fix: `creator_id: uuid.UUID`,
  `value: str | bool | None`.
- [cleanup] DRY — the `to_thread(x.delay)` + `aset_owner` try/except
  `RedisError` → `stream_url=None` block remains copied ~19× across
  auth/chat/titles/analysis/clips/thumbnails/creators/videos/improvement. |
  fix: extract `async def enqueue_with_stream(...)` into routers/_schemas.py
  or routers/_enqueue.py.

NEW cleanup:

- [cleanup] clips.py:1524-1541 vs migration 0046 — `_active_summary` filters
  `Summary.status != SummaryStatus.failed` but the unique-index predicate is
  render_status-only. No code path currently sets `SummaryStatus.failed` (grep:
  the only reference is this filter), but if one ever does, a
  status=failed/render_status=pending row would block inserts while the winner
  re-select returns None → permanent 409. | fix: drop the dead `status` filter
  so probe and index share one predicate.

## Wave-diff regression review (verified clean)

- **Issue 359 stale-running override** (clips.py:456-471): `ais_render_stale`
  is a genuinely async Redis read (worker/tasks.py:127-141, lazy per-process
  `redis.asyncio` singleton with 2s socket timeouts — no blocking-in-async, no
  cross-loop binding in the single-loop app process); fail-closed on Redis
  errors (reports fresh → 409 preserved, no duplicate-render storm); absent
  marker counts stale by design so pre-fix stuck rows recover. Log line carries
  clip_id only — no PII.
- **Issue 359c enqueue-failure restore** (clips.py:504-534): snapshot of
  `render_uri` taken BEFORE the reset commit; restore runs only when the
  exception came from `.delay()` (session clean, commit safe); attribute set on
  the expired instance is safe (no lazy load on column-attr assignment); 503
  message safe. A non-`done` clip left `pending` on enqueue failure is
  retryable (pending does not 409) — correct asymmetry.
- **Summary race handler**: rollback-then-re-select uses plain scalars; the
  loser returns the winner's `/tasks/{id}/events` stream (owner key set by the
  winner's request); creator scoping intact (`_active_summary` filters
  `creator_id`; video ownership pre-checked via `get_owned`).
- Diff touched only clips.py in the slice; no other route's dependencies,
  isolation, or error surface changed.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — 359c restores render_uri on failed enqueue; sessions via DI; new Redis marker client is a lazy per-process singleton |
| 2 Concurrency & scale | 4 findings — in-request LLM (carry); ensure_future GC ×8 (carry); per-feedback retrain enqueue (carry); NEW summary enqueue-failure permanent block. Summary double-POST race now closed by uq_summaries_active + IntegrityError handler |
| 3 Security & compliance | 1 SEV2 — create_summary still missing require_budget. The SEV1 generate gate is FIXED (flag+budget deps + tests). Isolation verified on all new/changed paths; no PII in new log lines; no virality on the wire |
| 4 Clip-quality | n/a (router layer) |
| 5 Anthropic SDK | ok — unchanged this wave; AsyncAnthropic singleton, tokens logged, limits set |
| 6 Cleanliness & typing | 7 cleanups — 6 carried + NEW _active_summary/index predicate mismatch |
| 7 Error handling / API | ok — new 409/503 paths safe and correctly coded; 2 chat reads still lack response_model (cleanup) |
| 8 Config & paths | ok — no new config surface in the slice; stale thresholds derive from existing Settings |

## Module verdict
NEEDS-WORK — the two targeted fixes landed correctly (SEV1 `/clips/generate`
flag+budget gate; SEV2 summary double-render race via partial unique index +
IntegrityError winner-return, both verified in code and migration). No
regressions in the wave diff. But four SEV2s carry forward (in-request LLM,
summary budget-parity, ensure_future ×8, retrain enqueue) and the wave itself
opened one new SEV2: `create_summary`'s unprotected enqueue can now permanently
block a video's recap behind the very index that fixed the race.
