# billing — assessed 2026-06-09

## Findings

- [SEV2] billing/stripe_client.py:34 — `stripe.max_network_retries = 3` is
  silently a NO-OP for every `_STRIPE` call. Verified empirically against the
  installed SDK (stripe 11.4.0): `StripeClient` builds its own
  `RequestorOptions(max_network_retries=None)` and never falls back to the
  module global (`_GlobalRequestorOptions` is only used by the legacy global
  API surface); `HTTPClient.request_with_retries` resolves `None → 0`. So
  Checkout-session creation runs with ZERO network retries despite the
  documented hardening intent — a transient Stripe network blip surfaces
  straight to the user as a failed checkout. | fix: pass it to the
  constructor — `stripe.StripeClient(settings.STRIPE_SECRET_KEY,
  http_client=stripe.HTTPXClient(timeout=settings.STRIPE_TIMEOUT_S),
  max_network_retries=3)` — and delete the inert global assignment. Retries
  stay safe because the `intent_id` Idempotency-Key already dedupes at Stripe.

- [SEV2] billing/stripe_client.py:101 — (carried over from 2026-06-08,
  unfixed) Stripe `Idempotency-Key` is the raw client-supplied `intent_id`.
  Stripe scopes idempotency keys per API key, not per customer, so an
  adversarially reused `intent_id` (leaked via XSS / shared device / pasted
  browser state) can poison or replay another creator's pending Checkout
  session within Stripe's 24h window. | fix: tenant-scope the key —
  `options={"idempotency_key": f"{creator_id}:{intent_id}"}`. No client
  change required; `creator_id` is already a parameter.

- [SEV2] billing/ledger.py:152 — `deduct_for_video` raises FastAPI
  `HTTPException(402)` but is called from a Celery worker context
  (worker/tasks.py:521, inside `_ingest_async`). When a creator's balance is
  drained between the upload pre-check and worker execution (two concurrent
  uploads both pass `check_balance_for_minutes`), the 402 propagates into
  `ingest_video`'s generic `except Exception` (worker/tasks.py:134-135),
  which retries the task 3× at 30s intervals — each retry re-downloads and
  re-extracts the full video only to hit the same 402 — then terminally
  fails with the actionable "purchase a pack" copy lost (the user sees a
  generic failed video). | fix: raise a domain exception
  `InsufficientBalanceError(detail)` from the ledger; routers map it to
  `HTTPException(402, detail)` (the pre-flight guards `check_positive_balance`
  / `check_balance_for_minutes` are router-only and may keep raising
  HTTPException directly); `ingest_video` treats it as non-retryable
  (re-raise without `self.retry`, like `SoftTimeLimitExceeded`) so the
  on_failure/refund path fires immediately and the video's failure reason
  can carry the balance copy.

- [cleanup] billing/ledger.py:64 — (carried over, unfixed) grant fast-path
  idempotency check filters on `stripe_session_id` only. Column is globally
  UNIQUE so at most one row matches, but adding
  `MinutePack.creator_id == creator_id` is free defense-in-depth: a future
  caller passing another creator's session id would surface the UNIQUE
  conflict instead of silently no-oping. Same pattern applies to the deduct
  fast-path at ledger.py:126 (`video_id` only — lower risk, system-generated
  key). | fix: add the `creator_id` predicate to both fast-path SELECTs.

- [cleanup] billing/stripe_client.py:65 — `uuid.UUID(intent_id, version=4)`
  does NOT enforce v4: per the stdlib contract, a supplied `version`
  OVERRIDES the version/variant bits of the parsed value, so any valid UUID
  string (v1, v3, v5, nil) passes. The shape validation — the load-bearing
  part for key hygiene — works; the "must be a v4 UUID" claim in the
  docstring and error message is not actually checked. | fix: parse with
  `parsed = uuid.UUID(intent_id)` then `if parsed.version != 4: raise
  ValueError(...)`, or relax the docstring/error to "must be a UUID".

- [cleanup] billing/stripe_client.py:69 — `per_min = pack.price_cents /
  pack.minutes` duplicates `Pack.per_minute_cents` (billing/packs.py:21)
  (DRY). | fix: use `pack.per_minute_cents` and drop the local.

Status of 2026-06-08 findings: the SEV1 (webhook fast-path dead under RLS)
lives in routers/billing.py — outside this slice as of this cycle; owned by
the routers agent. The stripe_client.py:101 SEV2 and ledger.py:64 cleanup
remain unfixed and are re-listed above with current line numbers. The
`stripe.max_network_retries` item was previously rated cleanup
("order-dependent, not idiomatic"); re-verification against the installed
SDK shows it is functionally inert for `StripeClient`, so it is promoted to
SEV2. The refund.py:50 AdminSessionLocal note is dropped — the BYPASSRLS
justification is documented and correct today; flagging a future-state
migration is speculative (rubric: flag only present defects).

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — refund.py:50 opens `AdminSessionLocal` via `async with`; explicit `rollback()` on the IntegrityError branch (refund.py:74); `_STRIPE` is a module-level singleton with an explicit `HTTPXClient(timeout=settings.STRIPE_TIMEOUT_S)`; ledger callers own the outer commit and that contract is documented in both docstrings. |
| 2 Concurrency & scale | 2 findings — money mutations are sound (atomic `UPDATE … WHERE balance >= n … RETURNING` at ledger.py:144-148; SAVEPOINT + flush + IntegrityError-catch on both grant and deduct; UNIQUE keys verified at models.py:603 (stripe_session_id), models.py:627 (video_id), migration 0013 (refund pack_id partial)), but Stripe calls run with 0 network retries (finding 1) and a worker-side 402 triggers 3 wasted full-reprocess retries (finding 3). |
| 3 Security & compliance | 1 SEV2 + 2 cleanup — per-creator isolation verified: `get_balance` ledger.py:33 and `_trial_expired` ledger.py:178 key on `Creator.id`; grant/deduct UPDATEs at ledger.py:86/145 filter `Creator.id == creator_id`; refund derives `creator_id` from the deduction row. Parameterized ORM SQL only. No PII/secret in any logger call — opaque IDs only. No virality language. Open: un-scoped Stripe idempotency key (finding 2), fast-path creator filters (finding 4), illusory v4 check (finding 5). |
| 4 Clip-quality | n/a (billing) |
| 5 Anthropic SDK | n/a (no LLM calls) |
| 6 Cleanliness & typing | 1 cleanup — every signature typed; no TODO / commented-out code / print(); one DRY miss (finding 6); all functions single-purpose and under ~35 LOC. |
| 7 Error handling / API | 1 finding — 402/404 copy is actionable and safe (no internals leaked); `RuntimeError` on missing checkout URL gives the router a clean 502 path; but `HTTPException` raised from the ledger in a non-HTTP (Celery) context loses the copy and triggers pointless retries (finding 3). |
| 8 Config & paths | ok — `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_TIMEOUT_S` all present in `.env.example` (lines 83-90) with descriptions, wired via pydantic-settings; no filesystem paths in this module. |

## Module verdict
NEEDS-WORK — the money core is correct (atomic, idempotent, tenant-isolated
mutations under verified UNIQUE constraints), but the Stripe client's retry
hardening is provably inert, the idempotency key is still not tenant-scoped,
and an out-of-balance race in the worker burns three full reprocess retries
before failing with the actionable message lost.

## Issue 75 Reconciliation (2026-06-23)

| Finding | Disposition |
|---|---|
| [SEV2] Stripe max_network_retries NO-OP (billing/stripe_client.py:34) | → tracked in Issue 206 (verify payment_status + Stripe robustness) |
| [SEV2] Idempotency-Key not tenant-scoped (billing/stripe_client.py:101) | → tracked in Issue 206 |
| [SEV2] HTTPException(402) from Celery worker (billing/ledger.py:152) | → tracked in Issue 205 (Stripe ↔ ledger reconciliation Beat task) |
| [cleanup] fast-path creator_id filter (billing/ledger.py:64) | → tracked in Issue 205 |
| [cleanup] illusory v4 UUID check (billing/stripe_client.py:65) | → tracked in Issue 109 (deferred design cleanups) |
| [cleanup] per_minute_cents DRY (billing/stripe_client.py:69) | → tracked in Issue 109 |
