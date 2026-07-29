# routers — assessed 2026-07-20 (post-fix); delta re-assessed 2026-07-29 (ready-pass)

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

---

## 2026-07-29 ready-pass delta

Delta re-assessment of the surfaces changed by PRs #61/#62 (`e92b93a..6c431a7`,
11 files in the slice, +523/−349). Method: full diff read; every new endpoint
traced for isolation/limits/status codes against its siblings; all 18 collapsed
enqueue sites compared line-by-line against their pre-collapse bodies;
`record_event_nowait` (event_log.py:141-190), `validate_user_cuts` /
`MIN_KEEP_SEGMENT_S` (clip_engine/edits.py:35,79-160), `record_llm_usage`
(billing/ledger.py:172-179), migration 0047 + models.py:675-676 traced outside
the slice to verify load-bearing claims. Targeted tests green: 43/43 across
tests/test_trim_render.py, test_clip_metadata.py, test_enqueue_helper.py,
test_event_log.py.

### Resolved since 2026-07-20 (post-fix)

- **[was SEV2] `ensure_future(record_event)` GC hazard ×8** — FIXED and correct:
  all 8 sites (auth.py:84,346; review.py:197; creators.py:462,503,543,611,714)
  now call `record_event_nowait` (Issue 352), which holds a strong reference in
  a module-level `_pending_tasks` set with `add_done_callback(discard)`, bounds
  the backlog at `_MAX_PENDING` (drops instead of stampeding), prunes tasks from
  closed loops, and never raises. Zero `ensure_future` calls remain in routers/.
- **[was SEV2] `create_summary` unprotected enqueue → permanently blocked recap**
  — FIXED (d7230c4): clips.py:1762-1771 wraps `render_summary_task.delay` in
  try/except → flips the row to `render_status=failed` (leaving the
  `uq_summaries_active` partial-index predicate so a retry starts fresh) +
  commit + 503 with a safe message — exact mirror of the Issue-359c render_clip
  guard the prior record asked for.
- **[was cleanup] DRY `to_thread(.delay)` + `aset_owner` copied ~19×** — FIXED:
  routers/_enqueue.py (`enqueue_stream_task` / `stamp_stream_owner`) collapses
  18 sites across 10 files; bespoke-ordering sites (359c render compensation,
  improvement's job_id commit, Issue-313 stamp-before-`start_pipeline`, OAuth
  302) correctly kept route-local and use only `stamp_stream_owner`.

### New findings

- [SEV2] routers/review.py:155-160 — Save-trim bounds check has NO right-edge
  tolerance: `submit_feedback` 422s when `trim_end_s > clip_duration_s`
  strictly, while the sibling `/trim-render` (:265) and `validate_user_cuts`
  (edits.py:123) deliberately allow one frame (`+ MIN_KEEP_SEGMENT_S = 0.04s`)
  precisely because UI/transcript-vs-mp4 rounding puts drag-to-end a few ms past
  the computed duration. Same timebase, same UI slider → the Save action can
  spuriously 422 on a trim the re-render endpoint accepts. | fix: compare
  against `clip_duration_s + MIN_KEEP_SEGMENT_S` (import from
  clip_engine.edits) in submit_feedback, or extract one shared clip-relative
  bounds helper used by both routes; add a drag-to-end (duration+0.02s)
  regression test.

### Verified clean (the ready-pass surfaces)

- **routers/_enqueue.py helper** — behavior-preserving at all 18 collapsed
  sites: same `redis.RedisError`-only catch (fail-open `stream_url=None`, task
  already enqueued), same stream keys (clip_id for render/clean/edit,
  video_id for upload/ingest/queue, summary_id for recap, Celery task.id for
  the LLM/chat/DNA/catalog routes), same response dict shapes and status
  codes; `.delay()` still runs via `asyncio.to_thread` (scale-checklist B);
  `progress.aset_owner` resolved at call time so existing test patches hold.
- **PATCH /clips/{clip_id}** (clips.py:1131-1153) — ownership via `get_owned`
  (isolation intact); tri-state via `model_dump(exclude_unset=True)` (the
  FastAPI body-updates idiom); YouTube `videos.insert` bounds enforced at the
  boundary as 422 (title ≤100 chars via Field, description ≤5000 UTF-8 bytes,
  `<`/`>` rejected, stripped-empty → None so "" can never shadow the publish
  fallback); pure DB write correctly ungated by flag/budget; 60/min limit;
  `ClipOut`/`_clip_response` both carry the new applied fields (migration
  0047 + models.py:675-676 verified).
- **POST /clips/{clip_id}/trim-render** (review.py:218-320) — full sibling
  parity with /render//clean//cuts: `require_flag("render_intake")` +
  `require_budget` deps, 20/hour + RENDER_DAILY_LIMIT, `check_positive_balance`
  before `get_owned` (same order as clips.py:505/:928); mirrors /cuts'
  `pending_clean_or_edit` 409 so the worker idempotency probe can't silently
  drop the edit; trim→cuts inversion is bounded — sub-frame edges skipped,
  empty inversion → 422 `trim_noop`, and every degenerate window I traced
  (start past end-tolerance, full-cover, near-duration) terminates in a 422
  from the pre-check or `validate_user_cuts` (≥5s kept / ≤85% removed /
  overlap / bounds); re-encodes from `render_uri` so trims survive the source
  purge; 202 + `TaskQueuedOut`.
- **Save-trim timebase fix** (review.py:147-160, dd92fcd) — bounds now checked
  clip-relative (origin = `setup_start_s ?? start_s`), the same timebase as
  /transcript, /cuts and /trim-render; the old video-absolute comparison that
  422'd nearly every mid-video clip is gone (negatives/inversion still rejected
  by the shared `_validate_trim_pair` model validator).
- **Structured `source_expired` 409s ×2** (clips.py:512-528 render pre-check,
  :1646-1661 recap) — `{code, message}` shape matches `pending_clean_or_edit`;
  retention hours now from `settings.SOURCE_MEDIA_RETENTION_HOURS` (was a
  hardcoded "72-hour" string); the render pre-check fetches the Video via the
  already-ownership-checked clip's FK — no cross-tenant exposure, worker keeps
  its own guard for the enqueue-to-run race.
- **Billing multiplier threading** (clips.py:1296,1398,1510 + thumbnails.py
  `_compute_and_bill`) — `cache_write_multiplier=2.0` keyed off the
  `usage["cache_1h"]` flag that all three knowledge helpers emit
  (clip_titles/captions/explain :284/:232/:286); `record_llm_usage` accepts the
  kwarg; thumbnails bills inside the single-flight compute so cache hits and
  waiters pay nothing.
- **No tenant-isolation regressions**: every new/changed handler reaches its
  rows via `get_owned` or a creator-scoped query; no new log line carries PII
  or tokens; no virality language on any new wire string.

### Still open (carried from 2026-07-20, re-verified at HEAD)

- [SEV2] clips.py:282-344 — `/clips/generate` still awaits the LLM scoring
  pass in-request (session released at :322 before `score_and_rank`; RLS
  restamp at :340 intact). Fix unchanged: 202 + `TaskQueuedOut` conversion.
- [SEV2] clips.py:1612 — `create_summary` still stacks only
  `require_flag("render_intake")`; every sibling render route — including the
  brand-new /trim-render — adds `require_budget`. Fix unchanged: add
  `Depends(require_budget)`.
- [SEV2] review.py:215 — `retrain_preference.delay` still enqueued on every
  feedback write. Fix unchanged: per-creator Redis `SET NX EX 60` debounce at
  enqueue. (needs-runtime-confirmation on broker churn at beta scale.)
- [cleanup ×6] generate_clips bare `{"clips": ...}` envelope (:313,:334,:344);
  chat.py:196,:227 reads without `response_model`; insights.py:172 internal
  `_compute_virality_score` name; tasks.py:81 `get_event_loop()`;
  creators.py:153-157 `object`-typed helper; clips.py:1600 `_active_summary`
  dead `status != failed` filter vs the render_status-only index predicate.

### Delta rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — enqueue guard now covers create_summary; helper preserves fail-open; no new leak paths |
| 2 Concurrency & scale | improved — ensure_future ×8 closed via bounded strong-ref scheduler; 2 SEV2 carries (in-request LLM, retrain enqueue); trim-render TOCTOU on cleaned_render_uri matches /cuts (worker probe absorbs it, sibling parity) |
| 3 Security & compliance | 1 SEV2 carry (summary budget-parity); isolation verified on PATCH + trim-render + render pre-check; no PII in new logs |
| 4 Clip-quality | n/a (router layer) |
| 5 Anthropic SDK | ok — 1h-cache billing multiplier now threaded on all 3 clip LLM routes + thumbnails patterns |
| 6 Cleanliness & typing | 6 cleanups carried; DRY-enqueue cleanup closed; new helper/models fully typed |
| 7 Error handling / API | 1 NEW SEV2 (Save-trim right-edge tolerance); new 409/422/503 surfaces structured and safe; PATCH validates at the boundary |
| 8 Config & paths | ok — retention hours moved from hardcoded string to settings; no new config surface |

## Module verdict
NEEDS-WORK — the 2026-07-29 ready-pass shipped clean: the enqueue-DRY helper is
behavior-preserving at all 18 sites, PATCH metadata and /trim-render match
their siblings on isolation/limits/gating, two carried SEV2s (ensure_future ×8,
summary enqueue guard) and the DRY cleanup are closed, and no tenant-isolation
or contract regressions were found. Open ledger: 3 carried SEV2s (in-request
LLM on /clips/generate, create_summary budget-parity, per-feedback retrain
enqueue) + 1 new SEV2 (Save-trim right-edge tolerance mismatch vs /trim-render)
+ 6 cleanups.
