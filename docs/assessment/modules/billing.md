# billing — assessed 2026-07-20

Slice: `billing/ledger.py`, `billing/packs.py`, `billing/refund.py`,
`billing/spend_guard.py`, `billing/stripe_client.py`, `billing/__init__.py`
(empty). Prior assessment: 2026-07-01. Diff scrutiny: `f70a857..HEAD` touched
`ledger.py`, `spend_guard.py` (new module, Issues 290/291), `stripe_client.py`
(Issue 352 Batch B).

## Resolved since 2026-07-01
- **[SEV1] `stripe.max_network_retries = 3` module-global no-op — FIXED.**
  The module global is gone; `max_network_retries=3` is now passed to the
  `StripeClient` constructor (stripe_client.py:38-42), which is the documented
  v8 mechanism. Verified there is no remaining `stripe.<global> =` assignment
  in the module. Checkout creation and reconciliation listing now retry.
- **[SEV2] account-scoped bare `intent_id` Idempotency-Key — FIXED.**
  Key is now derived server-side as `checkout:{creator_id}:{intent_id}`
  (stripe_client.py:129), tenant-scoping it structurally; the misleading
  "UUID-shape check closes cross-tenant collision" comment was corrected to
  say isolation comes from the creator_id prefix, not the shape check
  (stripe_client.py:66-69). Commit 1f9daf7 (Issue 352 Batch B).
- **[SEV2] pre-commit `send_notification.delay` in `deduct_for_video` — FIXED.**
  The enqueue is now staged in `session.info` (ledger.py:376-379) and drained
  by a class-level `after_commit` listener (ledger.py:37-61), with an
  `after_rollback` listener discarding staged entries (ledger.py:64-67).
  Verified the mechanism is sound for the async path: listeners registered on
  the sync `Session` class fire for the sync session underlying `AsyncSession`,
  and `AsyncSession.info` proxies to that same dict. Staging happens only
  after the SAVEPOINT succeeds, so a 402 or savepoint failure never stages.

## Findings
- [SEV2] billing/spend_guard.py:328-337 — the global trip-latch (`SETNX` on
  `_TRIP_LATCH_KEY`, 1h TTL) is set BEFORE `_flip_llm_flag` runs. If the flag
  flip raises (DB down, `set_flag` commit failure), `record_spend`'s catch-all
  swallows it as fail-open, but the latch is already set — so no worker will
  re-attempt the flip for `SPEND_COOLDOWN_TTL_S` (3600s) while the global
  daily/monthly/velocity cap breach continues unenforced (that is the exact
  runaway-spend window the breaker exists to close; bounded only by cap ×
  1h of extra burn). | fix: on exception from `_flip_llm_flag`, delete the
  latch before propagating: wrap lines 333-344 in `try/except`, `await
  r.delete(_TRIP_LATCH_KEY)` then `raise`, so the next `record_spend` call
  re-attempts the flip. (`set_flag` is an idempotent upsert, so a rare
  double-flip under the race is harmless.)
- [cleanup] billing/ledger.py:50-54 — `send_notification.delay(...)` inside
  the `after_commit` listener is sync Redis/broker I/O executed on the event
  loop thread (during `await session.commit()`); the codebase's own
  scale-checklist B treats bare `.delay()` on a loop as an audit item and
  offloads it in routers (routers/clips.py:494,1623). Bounded today because
  `deduct_for_video` is only called from worker/tasks.py:1297, which runs on
  the worker's dedicated loop — not the FastAPI request loop. | fix (for
  consistency / future router callers): fire-and-forget via
  `asyncio.get_running_loop().run_in_executor(None, lambda: send_notification.delay(...))`
  inside the existing try; also hoist the `from worker.tasks import
  send_notification` out of the per-pair loop.
- [cleanup] (carry-forward) .env.example — `COST_CACHE_WRITE_MULTIPLIER`
  (consumed at billing/ledger.py:147) is still absent from `.env.example`,
  though sibling `COST_CACHE_READ_MULTIPLIER` is documented (line 33). Safe
  default exists (config.py = 1.25) so not fail-fast-critical. | fix: add
  `COST_CACHE_WRITE_MULTIPLIER=1.25  # Anthropic cache-write multiplier
  (1.25× base input rate, 5-min TTL; scoring passes 2.0 for 1h TTL)`.
- [cleanup] (carry-forward) billing/ledger.py:172 — `record_llm_usage(usage:
  dict, ...)` still takes a bare unparameterized `dict` on the cost-accounting
  path; keys are fixed (`input_tokens`/`output_tokens`/`cache_read`/
  `cache_creation`). | fix: `dict[str, int]` or a small `TypedDict`.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — DB via `async with AdminSessionLocal()` / passed-in `AsyncSession`; `_STRIPE` and Redis clients are module singletons; deduct/grant/refund idempotent (UNIQUE(video_id), UNIQUE(stripe_session_id), partial UNIQUE on refund pack_id) with SAVEPOINT + IntegrityError race handling; balance-low notify now transactional-outbox-correct |
| 2 Concurrency & scale | 1 SEV2 (trip-latch-before-flip stall window), 1 cleanup (`.delay` in listener, worker-loop-only today). Stripe retries now active (constructor `max_network_retries=3`); 10s HTTP timeout bounds executor pin; spend counters are one atomic multi-key Lua + one mget; worker uses a persistent per-process loop (worker/celery_app.py:122) so the loop-bound async-Redis singleton is safe in Celery |
| 3 Security & compliance | ok — idempotency key now tenant-scoped (`checkout:{creator_id}:{intent_id}`); webhook via `stripe.Webhook.construct_event` + `STRIPE_WEBHOOK_SECRET` (default 300s replay tolerance); every DB query creator-/video-scoped, refund derives creator from the deduction row; Redis spend keys embed creator_id; no secret/PII in logs; spend-guard 429 copy honest, no virality promise |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | n/a — module prices token-usage dicts; makes no LLM call (cache-read/write multipliers priced correctly per pricing book) |
| 6 Cleanliness & typing | 2 cleanups (bare `usage: dict` carry-forward; listener `.delay` pattern). Otherwise typed, no TODO/print/dead code |
| 7 Error handling / API | n/a (routers own the surface). `ValueError`/`RuntimeError`/`HTTPException(402/429)` messages safe; `SpendCapExceededError` carries actionable copy; fail-open posture on Redis errors is documented and warn-once |
| 8 Config & paths | 1 cleanup (carry-forward `.env.example` gap). All SPEND_*/STRIPE_TIMEOUT_S config in pydantic settings AND `.env.example`; no filesystem paths in module |

## Module verdict
NEEDS-WORK — all three 2026-07-01 findings (SEV1 Stripe retries, SEV2
idempotency key, SEV2 pre-commit notify) are verified fixed; one new SEV2
remains: the spend-guard global trip-latch is set before the kill-switch flip,
so a failed flip silences the breaker for the full 1h latch TTL. No BLOCKER,
no cross-tenant leak.
