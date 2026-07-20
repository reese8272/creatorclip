# routers — assessed 2026-07-20

Slice: `routers/` (23 files, incl. new `_owned.py` + `_schemas.py`). Scope: FastAPI
HTTP surface only; `auth` helpers, `db.py`, `flags.py`, `billing/spend_guard.py`,
`event_log.py`, `worker/*`, and the engines are owned by other modules and were
read ONLY to verify load-bearing claims. Method for this run: every finding from
the 2026-07-01 record re-verified against HEAD; `git diff f70a857..HEAD --
routers/` (19 files, +775/−167) read in full for new-code scrutiny; the rest of
the slice swept per the rubric.

## Findings

- [SEV1] routers/clips.py:221-227 — `POST /videos/{id}/clips/generate` has NO
  `require_flag("llm_generation")` and NO `require_budget` dependency, while
  every other LLM surface got both this cycle (analysis.py router-level :159-165;
  chat/improvement/titles/thumbnails/insights + clips title-suggestions :1118,
  caption-hooks :1223, explanation :1322 per-route). This is the single most
  expensive LLM route (per-candidate scoring pass), and `score_and_rank` is
  called in-request, so the worker-side `ensure_within_budget` guard
  (worker/tasks.py:145, :3792) never runs for it — verified `clip_engine/*.py`
  contains no flag/budget check. Consequence: the Issue-290 global breaker
  (which trips by flipping `llm_generation` off) and the per-creator spend
  cool-down are both ineffective against the main burn path; only the balance
  check + 10/hour rate limit bound it. | fix: add
  `dependencies=[Depends(require_flag("llm_generation")), Depends(require_budget)]`
  to the route decorator, exactly like the three sibling clip LLM routes; add a
  test asserting 503 with the flag off.

- [SEV2] (carry-forward) routers/clips.py:264-285 — `/clips/generate` still
  awaits the LLM scoring pass **inside the request/response cycle**. Issue 82b
  fixed the pool-starvation half (session closed at :264 before `score_and_rank`,
  persistence reacquires at :281 with the RLS `session.info` stamp — verified
  correct), but the HTTP connection is still held for the full 30–120s scoring
  pass while every other LLM surface is 202 + Celery + SSE
  (needs-runtime-confirmation on p95 vs LB idle timeout). | fix: convert to the
  202 + `TaskQueuedOut` + `aset_owner` pattern; the idempotency guard at :254
  already makes a worker retry safe.

- [SEV2] routers/clips.py:1534-1615 — `create_summary` is check-then-insert with
  no unique backstop: the in-flight-summary check (:1536-1547) and the
  `session.add(summary)` + commit (:1614-1615) race; `summaries` has NO unique
  constraint (models.py:802-850), so a double-click enqueues TWO
  `render_summary` Celery jobs (:1624) — duplicate ffmpeg renders + R2 objects,
  and no `IntegrityError` ever surfaces to dedupe. Same defect class the repo
  just fixed for videos (videos.py:310, :465). | fix: partial unique index
  `CREATE UNIQUE INDEX uq_summaries_active ON summaries (video_id) WHERE
  render_status IN ('pending','running')` + `except IntegrityError: rollback();
  re-select and return the winner`; add a two-concurrent-POST regression test.

- [SEV2] routers/clips.py:1486-1491 — `create_summary` stacks only
  `require_flag("render_intake")`; the sibling render-intake routes
  (`/render` :594-601, `/clean` :650-657, `/cuts` :810-817) all stack
  `require_budget` too, so a creator in spend cool-down (429 everywhere else)
  can still queue recap renders. | fix: add `Depends(require_budget)` to the
  route's dependencies for parity.

- [SEV2] (carry-forward, grew) auth.py:83,356; review.py:157; creators.py:461,
  517,568,638,743 — `asyncio.ensure_future(record_event(...))` fire-and-forget:
  the Task handle is never stored, so the loop holds only a weak reference and
  CPython may GC it before completion (documented asyncio footgun → silently
  dropped telemetry under load). Now EIGHT sites — Issue 203 added a new one
  (`data_gate_evaluated`, creators.py:568) using the same pattern. The lost
  events are exactly the activation/funnel signals (`clip_kept`,
  `oauth_completed`, `identity_saved`, `data_gate_evaluated`). | fix: `await`
  inline (record_event is best-effort and never raises), or
  `asyncio.create_task` with handles held in a module-level `set()` +
  `.add_done_callback(s.discard)` — one shared helper, applied to all 8 sites.

- [SEV2] (carry-forward) review.py:172-177 — `retrain_preference.delay` enqueued
  on EVERY feedback write (120/min limit). The task self-debounces only after
  dequeue, so a feedback-clicking creator can put up to 120 broker messages/min
  on the queue. | fix: debounce at enqueue with a per-creator Redis
  `SET NX EX 60` "retrain pending" key. (needs-runtime-confirmation that broker
  churn is material at beta scale.)

- [cleanup] (carry-forward) clips.py:256,276,285 — `generate_clips` returns bare
  `{"clips": [...]}` so `ClipListOut`'s default `state="populated"` is emitted
  even for the new `return {"clips": []}` zero-candidate path (:276) —
  contradicts the empty-state envelope the list path implements (:376-390). |
  fix: return `state=build_envelope_state(len(items))` + an honest "no
  candidates met the threshold" message on empty.

- [cleanup] (carry-forward) chat.py:201-241 — `list_conversations` /
  `get_messages` still return bare `dict` with no `response_model=` (rubric 7:
  wire shape undocumented in OpenAPI, unvalidated outbound). | fix: add
  `ConversationListOut` / `ConversationMessagesOut` models.

- [cleanup] (carry-forward) insights.py:140,172,273,294 — internal
  `_compute_virality_score` name + "virality score" docstrings. Wire field is
  correctly `performance_score` and no response promises virality, but the name
  dirties the no-virality structural grep. | fix: rename to
  `_compute_performance_score`.

- [cleanup] (carry-forward, half-fixed) tasks.py:81 — `_event_stream` still uses
  `asyncio.get_event_loop()` inside a running coroutine (deprecation path per
  the asyncio docs); the `task_events` return annotation from the prior record
  IS fixed (`-> StreamingResponse`, :121). | fix: `asyncio.get_running_loop()`.

- [cleanup] (carry-forward, narrowed) creators.py:153-157 —
  `_upsert_style_field(creator_id: object, ..., value: object)` still loosely
  typed (notifications.py sessions and `_identity_to_dict` were fixed). | fix:
  `creator_id: uuid.UUID`, `value: str | bool | None` per the style schema.

- [cleanup] (carry-forward, grew) DRY — the `await asyncio.to_thread(x.delay,…)`
  + `aset_owner` try/except `RedisError` → `stream_url=None` block is now copied
  ~19× (auth.py:311, chat.py:106, titles.py:92, analysis.py:141/236/312,
  clips.py:505/679/877/1017/1630, thumbnails.py:298, creators.py:500/551,
  videos.py:493/581, improvement.py:168) — `create_summary` added another. |
  fix: extract `async def enqueue_with_stream(task_sig, owner_key, creator_id)
  -> tuple[str, str | None]` into routers/_schemas.py or a new routers/_enqueue.py.

## Resolved since 2026-07-01

- **activity.py log-injection / 500 (SEV2)** — FIXED (Issue 352 Batch C):
  `_sanitize_extra` (:36-57) allowlists scalar types + clamps keys/lengths, and
  the payload is passed as ONE server-controlled `extra=<bounded JSON string>`
  field (:75-91), never `**`-splatted — kwarg/LogRecord collisions impossible.
- **activity.py unsanitized DB sink (SEV2)** — FIXED: `record_event` now
  receives `safe_extra or None` (:103), bounding per-row telemetry writes.
- **auth.py refresh token in revoke query string (SEV2)** — FIXED: token now in
  the form-encoded POST body (`data={"token": refresh_token}`, auth.py:470-473);
  still read via `decrypt()`; token read moved before `session.rollback()` so no
  pooled connection is held across the Google round-trip (Issue 82b).
- **videos.py double-submit `IntegrityError` → 500 race (SEV2)** — FIXED: both
  `link_video` (:310-317) and `upload_video` (:465-473) wrap the commit in
  `except IntegrityError: rollback → HTTPException(409)`.
- **Unthrottled routes (SEV2)** — FIXED: `/auth/login` 30/min IP-keyed (:64),
  `/auth/connect-publishing` 30/min (:88), `/auth/callback` 20/min IP-keyed
  (:245), chat `list_conversations`/`get_messages`/`delete_conversation`
  120/min creator-keyed (:200,:231,:273), logs `/api/logs/me` 120/min (:40).
- **notifications.py untyped `session` params + `_get_or_create_prefs`
  (cleanup)** — FIXED: `AsyncSession` annotations throughout; `_identity_to_dict`
  now typed `(row: CreatorIdentity)` (creators.py:392).
- **tasks.py `task_events` missing return annotation (cleanup)** — FIXED
  (`-> StreamingResponse`); the `get_event_loop` half remains (above).

## Notes verified clean (not findings)

- **Per-creator isolation — NO cross-tenant leak found.** New `routers/_owned.py`
  standardizes the ownership fetch into a single-shot
  `SELECT … WHERE id = :id AND creator_id = :creator_id` with uniform 404 (no
  existence oracle) and is adopted across clips/videos/insights/publications/
  review/api_keys; remaining scoped queries carry `WHERE creator_id ==
  creator.id` or verified-parent child access, backstopped by RLS. The new
  Issue-82b early-commit/close patterns all restamp `session.info["creator_id"]`
  (or rely on the auth-dependency stamp) before reacquired transactions —
  traced on `generate_clips` (:281-284), `ingest_clip` (:949-953), `upload_video`
  (:356-361, :445-449), `erase_creator` (:519-524), `_persist_oauth_grant`
  (:174-177). New summaries endpoints (`create/list/get/download`) all isolate
  via `get_owned` + `creator_id` predicates.
- **erase_creator sequencing** — token read+decrypt, then `rollback()` so no
  pooled connection is held across the Google revoke / R2 purge; `event_logs`
  purge runs on the separate logs engine with an explicit creator filter;
  audit row is PII-free; cascade delete last. Correct.
- **New OAuth kill switch** — `SignupsPausedError` raised before the single
  commit in `_persist_oauth_grant`, so a paused signup persists nothing; the
  callback maps it to a clean redirect with no PII logged (auth.py:271-276).
- **Anthropic** — insights.py now uses a module-level `AsyncAnthropic` singleton
  awaited directly (:463-467, Issue 82a — no `to_thread` hop); `max_tokens` set;
  tokens logged (`record_llm_tokens` + billing) on every LLM path; untrusted
  YouTube titles wrapped; caching-absence documented (below Haiku's 4096-token
  cacheable floor). The clip title/caption/explanation routes now await async
  knowledge-module callables — no blocking-in-async.
- **Blocking-in-async** — all sync I/O still offloaded (`.delay()`, boto3,
  ffprobe, Stripe) via `asyncio.to_thread`; thumbnails single-flight `compute`
  is now an awaited coroutine; billing `checkout` commits before the Stripe
  round-trip so no pooled connection is pinned. No `requests.`/`time.sleep`/
  `subprocess.run`/`print()` in the slice.
- **RFC 8058 unsubscribe** — POST one-click handler added beside the GET landing
  page; both idempotent, generic 404 (no existence leak), IP-limited 30/min,
  admin session justified (no creator context; table not creator-queryable).
- **Error surface** — `detail=str(exc)` sites carry only controlled domain
  `ValueError`/`CutValidationError` messages; status codes correct (202 enqueue,
  402 balance, 409 conflict/expired-source, 413 oversize, 422 validation,
  429 budget, 502/503 LLM; new summaries routes follow suit).
- **Resource lifecycle** — upload paths unlink temp files in `finally`; SSE slot
  released in `finally`; sessions via DI context manager; re-render reset
  (Issue 353) clears `render_status`/`render_uri` atomically with the style merge.
- **Honesty/compliance** — no virality promise on the wire; recap flow refuses
  YouTube-sourced videos with ToS-honest copy (clips.py:1516-1523) and honors
  the 72-hour source purge (409 at :1526-1532).

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — temp files/SSE slots in finally; singletons; sessions via DI; Issue-82b early-release patterns traced correct |
| 2 Concurrency & scale | 5 findings — in-request LLM on /clips/generate (carry); create_summary check-then-insert double-render race; ensure_future GC of telemetry (8 sites); per-feedback retrain enqueue; (budget-gap counted under 3) |
| 3 Security & compliance | 1 SEV1 + 1 SEV2 — generate_clips bypasses llm_generation kill switch + spend guard; create_summary missing require_budget. Isolation + RLS verified on every query incl. all new Issue-82b reacquired-session paths; decrypt() confirmed; revoke token now in POST body; no token/PII in logs; no virality on the wire |
| 4 Clip-quality | n/a (router layer; noted recap segments use setup_start_s — setup, not aftermath) |
| 5 Anthropic SDK | ok — AsyncAnthropic singleton, tokens logged, limits set, caching-absence justified |
| 6 Cleanliness & typing | 6 cleanups — generate empty-state, chat response models, virality-name grep, get_event_loop, _upsert_style_field typing, ~19× aset_owner DRY |
| 7 Error handling / API | ok — Pydantic on requests, safe messages, correct codes; prior 500-path races fixed; 2 chat reads still lack response_model (cleanup) |
| 8 Config & paths | ok — paths absolute; settings via pydantic-settings; RECAP_TARGET_DURATION_MAX_S sourced from Settings |

## Module verdict
NEEDS-WORK — no BLOCKER and no cross-tenant leak; five of the eight 2026-07-01
SEV2s are fixed (activity sanitization ×2, revoke-token query string, video
double-submit race, unthrottled routes). But the new kill-switch/spend-guard
rollout MISSED the most expensive LLM route: `/clips/generate` ignores both the
`llm_generation` breaker and `require_budget` (SEV1), and the new recap endpoint
shipped a check-then-insert double-render race plus the same budget gap. Three
prior SEV2s persist (in-request LLM, fire-and-forget telemetry — now 8 sites,
per-feedback retrain enqueue).
