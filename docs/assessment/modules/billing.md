# billing — assessed 2026-06-24

Slice: `billing/` — `__init__.py` (empty), `packs.py`, `refund.py`, `stripe_client.py`,
`ledger.py`. Re-verified every carried-over finding against current line numbers, traced every
creator-scoped query to its `WHERE`, every `logger.*` for token/PII leakage, the LLM-cost path
(new since the 2026-06-09 cycle), and the cross-module callers (routers/billing.py,
routers/auth.py, routers/insights.py, worker/tasks.py, clip_engine/scoring.py, chat/runner.py)
for session/transaction + async-offload correctness. Installed Stripe SDK: 11.4.0.

## Findings

- [SEV2] stripe_client.py:34 — `stripe.max_network_retries = 3` is a NO-OP for `_STRIPE`
  (carried from 2026-06-09, STILL UNFIXED). `StripeClient` (SDK 11.4.0) builds its own
  `RequestorOptions(max_network_retries=None)` and never falls back to the module global;
  `HTTPClient.request_with_retries` resolves `None → 0`. Checkout-session creation therefore
  runs with ZERO network retries despite the documented intent, so a transient Stripe blip
  surfaces straight to the user as a failed checkout. | fix: pass to the constructor —
  `stripe.StripeClient(settings.STRIPE_SECRET_KEY, http_client=stripe.HTTPXClient(timeout=settings.STRIPE_TIMEOUT_S), max_network_retries=3)` —
  and delete the inert global. Safe because the `intent_id` Idempotency-Key already dedupes at
  Stripe. (Tracked Issue 206.)

- [SEV2] stripe_client.py:119 — Stripe `Idempotency-Key` is the raw client-supplied `intent_id`
  (carried from 2026-06-09, STILL UNFIXED). Stripe scopes idempotency keys per API key, not per
  customer, so an adversarially reused `intent_id` (leaked via XSS / shared device / pasted
  browser state) can poison or replay another creator's pending Checkout session inside Stripe's
  24h window. | fix: tenant-scope the key — `options={"idempotency_key": f"{creator_id}:{intent_id}"}`;
  `creator_id` is already a parameter, no client change. (Tracked Issue 206.)

- [SEV2] ledger.py:276 (caller worker/tasks.py:848) — `deduct_for_video` raises FastAPI
  `HTTPException(402)` but is invoked from the Celery ingest chain (`_ingest_async`), whose
  generic `except Exception` re-raises with `aemit(..., message="Ingest failed; retrying.")`
  (tasks.py:870-880). When a balance is drained between the upload pre-check and worker
  execution (two concurrent uploads both pass `check_balance_for_minutes`), the 402 is treated
  as retryable: each Celery retry re-downloads + re-ffmpeg-extracts the full video only to hit
  the same 402, then fails terminally with the actionable "purchase a pack" copy lost. Bounded
  to the narrow concurrent-drain race (pre-flight guards catch the common case), hence SEV2.
  | fix: raise a domain `InsufficientBalanceError(detail)` from the ledger; routers map it to
  `HTTPException(402, detail)` (the router-only pre-flight guards may keep raising HTTPException
  directly); `_ingest_async` treats it as non-retryable (re-raise without `self.retry`, like
  `SoftTimeLimitExceeded`) so the refund/on_failure path fires immediately and the failure
  reason carries the balance copy. (Tracked Issue 205.)

- [SEV2] ledger.py:109 (config.py:86) — `_estimate_cost_usd` reads
  `settings.COST_CACHE_WRITE_MULTIPLIER` (default 1.25× — the 5-min-TTL cache-write premium that
  prices EVERY non-1h LLM call's cache-creation tokens), but this key is **absent from
  `.env.example`** while its sibling `COST_CACHE_READ_MULTIPLIER` IS documented (env line 19).
  NEW this cycle — the LLM-cost code did not exist in the 2026-06-09 slice. An operator
  correcting the premium after an Anthropic price change has no documented knob, so the cost
  ledger silently keeps the stale 1.25× fleet-wide (clip scoring overrides to 2.0× inline, but
  every other caller inherits the default). | fix: add next to env line 19 —
  `COST_CACHE_WRITE_MULTIPLIER=1.25      # Anthropic 5-min cache-write premium (1.25× input rate); scoring passes 2.0 for ttl:"1h". Source: platform.claude.com/docs/en/about-claude/pricing`.

- [SEV2] ledger.py:301-307 — `deduct_for_video` enqueues the balance-low notification
  (`send_notification.delay(...)`) BEFORE the caller commits the outer transaction (docstring
  line 244: "Caller is responsible for committing"). The Celery task can run and read the
  creator's balance before the deduction is durable, so "balance low" can fire against the
  pre-deduction balance, or fire for a deduction the outer transaction then rolls back. The
  `entity_id=str(video_id)` dedupe bounds this to one stray notification per video → SEV2. NEW
  this cycle (Trigger-6 / Issue 244 code is new in this slice). | fix: don't enqueue inside the
  deduction helper — return a "balance_low" flag (or remaining balance) and let the caller
  `.delay(...)` after `session.commit()`, or move the enqueue to a session `after_commit` event.

- [SEV2] stripe_client.py:131-188 — `list_recent_paid_sessions` paginates with `limit=100` in
  an unbounded `while True`, accumulating every paid session into an in-memory list with no hard
  page cap. Fine at the 48h `STRIPE_RECONCILE_LOOKBACK_HOURS` default, but a widened window /
  backfill lets one reconcile task accumulate unbounded rows and pin one executor thread (sync
  SDK call offloaded via `run_in_executor`, tasks.py:2198). Rubric 2 (bounded work). NEW this
  cycle. | fix: add a `max_pages` guard (e.g. 50 → 5000 sessions); log+break with a WARNING when
  exceeded so an over-wide window degrades loudly instead of OOM-ing the worker.

- [cleanup] ledger.py:301 — redundant `from config import settings` inside `deduct_for_video`'s
  hot path even though `settings` is already imported at module top (line 24). Harmless but
  implies the module-level binding is unavailable. | fix: delete the local import; use the
  module-level `settings`.

- [cleanup] stripe_client.py:65 — `uuid.UUID(intent_id, version=4)` does NOT enforce v4: the
  stdlib `version` kwarg OVERRIDES the parsed version/variant bits, so any valid UUID (v1/v3/v5/
  nil) passes. The shape validation (the load-bearing part) works; the "must be a v4 UUID" claim
  in the docstring + error message is not actually checked. (carried, still unfixed.) | fix:
  `parsed = uuid.UUID(intent_id); if parsed.version != 4: raise ValueError(...)`, or relax the
  copy to "must be a UUID". (Tracked Issue 109.)

- [cleanup] stripe_client.py:69 — `per_min = pack.price_cents / pack.minutes` duplicates
  `Pack.per_minute_cents` (packs.py:44) (DRY). (carried, still unfixed.) | fix: use
  `pack.per_minute_cents` and drop the local. (Tracked Issue 109.)

- [cleanup] ledger.py:64,144,71 — `_estimate_cost_usd` and `record_llm_usage` swallow ALL
  exceptions best-effort (`except Exception ... logger.warning`, ledger.py:152; peers at
  scoring.py:285, chat/runner.py:127). Intentional ("never block the pipeline") but a *systemic*
  failure (bad `period`, missing `cost_estimate` column, unbound `AdminSessionLocal`) hides
  behind a per-call WARNING and silently zeroes the cost ledger fleet-wide with no alarm. | fix:
  keep the catch but emit a rate-limited ERROR + a metric counter (`billing.usage_write_failed`)
  so a sustained failure is visible; optionally narrow to `(SQLAlchemyError, OperationalError)`
  so a programming error still surfaces in CI rather than being masked. (Bordering SEV2;
  rated cleanup because no money/balance row is affected — only the analytics cost ledger.)

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — every DB session via `async with` (refund.py:60, ledger.record_llm_usage:149, worker callers); SAVEPOINTs (`begin_nested`) wrap INSERT+UPDATE atomically and roll back on the 402/IntegrityError path; explicit `rollback()` on refund's IntegrityError branch (refund.py:84); `_STRIPE` is a module-level singleton with an explicit `HTTPXClient(timeout=settings.STRIPE_TIMEOUT_S)`; ledger callers own the outer commit and that contract is documented in the docstrings |
| 2 Concurrency & scale | 4 findings — money mutations are sound (atomic `UPDATE … WHERE minutes_balance >= n … RETURNING` ledger.py:267-272; SAVEPOINT+flush+IntegrityError-catch on grant AND deduct; UNIQUE keys verified at models.py:783 stripe_session_id, models.py:803-807 video_id, migration 0013 refund pack_id partial; webhook + reconcile idempotent under Stripe at-least-once). But: Stripe runs 0 network retries (finding 1), worker-side 402 burns full-reprocess retries (finding 3), balance-low enqueue races the commit (finding 5), reconcile pagination is unbounded (finding 6). No blocking call inside `async def` — sync Stripe SDK correctly offloaded at every call site (routers/billing.py:146 `asyncio.to_thread`, tasks.py:2198 `run_in_executor`) |
| 3 Security & compliance | ok (+ open carried) — per-creator isolation verified on every query: `Creator.id == creator_id` (get_balance:157, grant UPDATE:209-211, deduct UPDATE:269, _trial_expired:324), `MinutePack.stripe_session_id ==` and `MinuteDeduction.video_id ==` are globally-UNIQUE idempotency keys, `Usage` upsert keyed on UNIQUE(creator_id, period). Webhook stamps `session.info["creator_id"]` before the RLS-gated read (routers/billing.py:231); worker/refund use `AdminSessionLocal` (BYPASSRLS) deliberately for cross-tenant system sweeps. No token/PII in any `logger.*` line — only UUIDs, pack_id, minute counts, stripe session ids. Parameterized ORM/Core SQL only — no f-string/% SQL. No virality promise in any string. Open: un-scoped Stripe idempotency key (finding 2) |
| 4 Clip-quality | n/a (billing) |
| 5 Anthropic SDK | partial (NEW surface) — billing does not CALL Anthropic; it PRICES it. `_estimate_cost_usd` correctly bills cache-read (0.1×) and cache-creation (1.25× / 2.0× for ttl:"1h") tokens SEPARATELY from `usage.input_tokens` (the documented cache under-bill fix). 1 finding: the cache-write multiplier knob is undocumented in `.env.example` (finding 4). Token usage IS logged after the call by the LLM callers (scoring.py:276, chat/runner.py); structured-output/max_tokens live in those caller modules, not this slice |
| 6 Cleanliness & typing | 4 cleanups — every public signature typed; no TODO/print/dead code; redundant re-import (finding 7), illusory v4 check (finding 8), per_minute_cents DRY (finding 9), broad usage-ledger catch (finding 10). All functions single-purpose and under ~35 LOC |
| 7 Error handling / API surface | n/a (no router in slice — routers/billing.py is the routers module's; reviewed only as a caller). Helpers raise correct `HTTPException(402/404)` with safe, actionable, internals-free detail; `RuntimeError` on missing checkout URL gives the router a clean 502. The cross-context 402 (finding 3) is the one real defect here |
| 8 Config & paths | 1 finding (SEV2) — `COST_CACHE_WRITE_MULTIPLIER` used (ledger.py:109) + defined (config.py:86) but missing from `.env.example` (finding 4). All other billing config present + described: STRIPE_SECRET_KEY/PUBLISHABLE_KEY/WEBHOOK_SECRET/TIMEOUT_S/TAX_ENABLED/RECONCILE_LOOKBACK_HOURS, LOW_BALANCE_THRESHOLD_MINUTES, COST_CACHE_READ_MULTIPLIER. No filesystem paths in this slice |

## Module verdict
NEEDS-WORK — no BLOCKER, no SEV1: the money core is correct (atomic, idempotent,
tenant-isolated mutations under verified UNIQUE constraints, no event-loop blocking). The gaps
are six SEV2s — two unfixed Stripe-client items (inert retries, un-scoped idempotency key), a
cross-context 402 that burns full-reprocess retries, an undocumented cache-write cost knob, a
balance-low notification enqueued before commit, and an unbounded reconcile pagination — plus
four cleanups.
