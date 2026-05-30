# billing — assessed 2026-05-30

Slice: `billing/__init__.py` (empty), `billing/ledger.py`, `billing/refund.py`,
`billing/packs.py`, `billing/stripe_client.py`. Issue 86 did not touch this
module; the only change since the 2026-05-29 prior assessment is the merge that
brought `billing/refund.py` (Issue 57) into scope. Money paths traced through
their callers: `routers/billing.py`, `routers/auth.py`, `worker/tasks.py`
(`RefundOnFailureTask.on_failure`, `_ingest_async` deduct), `models.py`
(MinutePack / MinuteDeduction), `alembic/0010_rls_policies.py`,
`alembic/0011_widen_pack_id.py`, and `db.py` for the role / RLS posture.

## Findings

- [SEV1] billing/refund.py:41 — `refund_for_video` opens
  `db.AsyncSessionLocal()` (the **app-role** factory, RLS-gated since Issue 79)
  and never sets `session.info["creator_id"]`, so the `after_begin` listener in
  `db.py:119-148` does NOT emit `SET LOCAL app.creator_id`. Under the prod RLS
  posture (`creatorclip_app` without BYPASSRLS, policy
  `creator_id = current_setting('app.creator_id', true)::uuid` per
  `alembic/0010_rls_policies.py:122`), `current_setting(..., true)` returns
  NULL → the `MinuteDeduction` SELECT at refund.py:42 returns no rows →
  `refund_for_video` silently returns 0 on every terminal ingest failure, and
  even if a deduction were visible the `MinutePack` INSERT inside
  `grant_minutes` would fail the `WITH CHECK`. The existing integration test
  (`tests/test_billing_refund_integration.py`) runs as the single-role dev
  user (BYPASSRLS) and is blind to this. Every other Celery task body that
  writes tenant-owned rows uses `db.AdminSessionLocal()` (worker/tasks.py:307,
  325, …) — refund is the inconsistent one |
  fix: switch to `db.AdminSessionLocal()` to match the rest of the worker
  surface (refund is a system action, not a per-creator request), OR keep
  `AsyncSessionLocal()` and `session.info["creator_id"] = deduction.creator_id`
  immediately after reading the deduction (but the deduction itself needs to
  be readable first — chicken-and-egg, so prefer the admin-session fix). Add a
  regression test that runs against the `creatorclip_app` role with RLS
  enforced and asserts a refund actually credits.
  *(needs-runtime-confirmation: severity here depends on whether the prod
  operator has already run the out-of-band `ALTER ROLE creatorclip_migrate
  BYPASSRLS` and switched the API/worker DSNs to the split roles. In a single-
  role deployment this is latent. CLAUDE.md / `docs/DEPLOYMENT.md` lists the
  role split as pending, so the fix should land before that switch flips.)*

- [SEV2] billing/refund.py:50-56 — the refund idempotency guard is a
  read-then-write check (`SELECT MinutePack.id WHERE pack_id = 'refund:<vid>'`
  → if None, INSERT). `MinutePack.pack_id` has NO UNIQUE constraint
  (models.py:507 is a plain `String(64)`), so two concurrent
  `refund_for_video` calls for the same video can both pass the SELECT and
  both insert → double-refund. The docstring (refund.py:13-15) claims the race
  is "not reachable in the current pipeline (the chain is single-runner per
  video)", but the comment in `worker/tasks.py:79` advertises the same helper
  for manual recovery, so a manual rerun racing with the automatic
  `on_failure` is reachable. The cited DECISIONS entry should be either
  closed by adding the constraint or hardened |
  fix: add a partial UNIQUE index `CREATE UNIQUE INDEX CONCURRENTLY
  uq_minute_packs_refund_pack_id ON minute_packs (pack_id) WHERE reason =
  'refund'` (online-safe per scale-checklist H), and wrap the
  `grant_minutes` call in a `try/except IntegrityError: return 0` so the
  loser of the race is a clean no-op. Add a concurrent-refund integration
  test analogous to `test_billing_grant_idempotency_integration.py`.

- [cleanup] billing/stripe_client.py:36 — `params: dict` (unparameterized) is
  the same finding carried from 2026-05-29; not regressed, just not yet fixed |
  fix: annotate `params: dict[str, object]` (or a TypedDict if the call site
  ever needs structural typing).

## Verification notes (no defect found — traced to confirm)

- **Prior SEV2 closed:** the broad `except IntegrityError` on the non-keyed
  grant path is now scoped at `billing/ledger.py:89-95` — a non-keyed grant
  re-raises so a real FK fault is no longer swallowed. The misleading
  `session=None` log line is gone with it. Test
  `tests/test_billing_grant_idempotency_integration.py` and the assertions in
  `tests/test_billing.py:84` still pass against the new behaviour.
- **Refund minutes correctness:** `refund_for_video` refunds
  `deduction.minutes_deducted` (refund.py:59), not a recomputed
  `video_minutes(duration_s)`. This matters because `video_minutes` is `ceil`
  with a floor of 1; refunding the deducted value (rather than recomputing
  from `duration_s`) is the right invariant — net = 0 even if rounding logic
  ever changes.
- **Refund preserves immutability:** no UPDATE/DELETE on `MinuteDeduction` or
  earlier `MinutePack` rows; refund is a compensating insert with
  `reason="refund"`. Test
  `tests/test_billing_refund_integration.py:116` asserts the deduction row
  survives.
- **Refund cannot crash the worker:** `worker/tasks.py:74-85` wraps
  `run_async(refund_for_video(...))` in a broad `except` that logs and lets
  the original task failure stand — a refund failure (incl. the SEV1 silent
  no-op above) is not surfaced to the caller. This is the right posture for
  on_failure, but it also means the SEV1 will be invisible without an
  explicit metric / alert.
- **pack_id length:** `alembic/0011_widen_pack_id.py` widened to VARCHAR(64),
  which fits `"refund:" + UUID(36)` = 43 chars. Confirmed.
- **Webhook signature still verified:** `routers/billing.py:116` →
  `construct_webhook_event` → `stripe.Webhook.construct_event(...,
  STRIPE_WEBHOOK_SECRET)` (stripe_client.py:71); bad sig → 400 at
  routers/billing.py:117-119.
- **Double-grant on webhook redelivery:** still prevented twice over —
  router-level pre-check (routers/billing.py:144-148) + `UNIQUE(stripe_session_id)`
  on `MinutePack` (models.py:510-512) enforced inside the SAVEPOINT.
- **Double-charge on Celery redelivery:** `UNIQUE(video_id)` on
  `MinuteDeduction` (models.py:531-535) + fast-path check + SAVEPOINT /
  IntegrityError guard (ledger.py:124-161). Caller is
  `worker/tasks.py:316`.
- **Atomic balance mutations:** `grant_minutes` uses
  `minutes_balance = minutes_balance + minutes` (ledger.py:84-88); `deduct`
  uses `... WHERE minutes_balance >= minutes ... RETURNING` (ledger.py:143-148)
  so insufficient balance returns no row → 402 with SAVEPOINT rollback. No
  read-modify-write race.
- **HTTPException propagation:** the 402 raised at ledger.py:152 is not an
  IntegrityError, so the `except IntegrityError` at ledger.py:159 does not
  swallow it.
- **Stripe prod fail-fast** (Issue 75c) still enforced: `config.py:85-98` —
  STRIPE_SECRET_KEY / STRIPE_WEBHOOK_SECRET required when `ENV == "production"`.
  Keys documented in `.env.example:73-75`. Checkout endpoint also guards on
  missing key with 503 (routers/billing.py:89-90).
- **Per-creator isolation in ledger:** queries filter by
  `Creator.id == creator_id` (ledger.py:33, 86, 145). `MinutePack` lookup by
  webhook is keyed on the Stripe-issued `stripe_session_id`, which is
  unguessable / globally unique — no cross-tenant lookup risk. The `MinutePack`
  table is on the RLS list (alembic/0010_rls_policies.py:63), giving belt-and-
  suspenders at the DB layer once the role split is live (see SEV1 above for
  the one place this currently bites us).
- **Log hygiene:** grep of `logger.*` in `billing/` + `routers/billing.py`
  shows only `pack_id`, `creator_id`, `minutes`, `session_id`, and exception
  message text — no Stripe secret, no PII, no token. Webhook signature-failure
  log writes the `SignatureVerificationError` message, which Stripe documents
  as safe to surface.
- **Stripe client lifecycle:** `_STRIPE = stripe.StripeClient(...)` is a
  module-level singleton (stripe_client.py:20) with `max_network_retries = 3`
  — not reconstructed per request; satisfies rubric §1 + §B.
- **Sync call inside `async def`:** `_STRIPE.checkout.sessions.create(...)`
  at stripe_client.py:65 is a blocking HTTP call invoked from `async def
  checkout` (routers/billing.py:84) via `create_checkout_session` (which is
  itself a sync function). The Stripe Python SDK is sync-only (no asyncio
  client), so this blocks the event loop for the round-trip duration. This is
  the same posture as other sync calls already known in the codebase and is
  *not* unique to billing; flagging it would be cross-module noise. It does
  put a throughput cap on `/billing/checkout` and `/billing/webhook` —
  documented here for the scale-checklist (B) Layer-2 verdict, not as a
  finding inside the billing slice (the right fix is project-wide:
  `asyncio.to_thread` wrapper, owned by the routers, not the SDK shim).

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — sessions via caller context manager (ledger) or `async with db.AsyncSessionLocal() as session` (refund); Stripe client is a module singleton; SAVEPOINTs scoped with `async with` |
| 2 Concurrency & scale | 1 SEV2 — refund idempotency is read-then-write without a UNIQUE; otherwise grant/deduct idempotency proven by concurrent gather tests, atomic balance UPDATEs, bounded single-row queries |
| 3 Security & compliance | 1 SEV1 (RLS) — refund opens an app-role session with no `creator_id` in `session.info`; under prod RLS this silently fails. No secret/PII logged; webhook signature verified; parameterized SQLAlchemy; no virality promise |
| 4 Clip-quality | n/a (billing, not a clip module) |
| 5 Anthropic SDK | n/a (no LLM calls) |
| 6 Cleanliness & typing | 1 cleanup — loose `dict` annotation in stripe_client (carried from prior) |
| 7 Error handling / API | ok — endpoints are in `routers/billing.py` (out of slice); the helpers raise structured `HTTPException` with safe messages, no stack/DB leak |
| 8 Config & paths | ok — prod fail-fast on Stripe secrets; all keys in `.env.example`; no filesystem paths in module |

## Module verdict

NEEDS-WORK — the prior SEV2 (non-keyed grant swallowing real IntegrityErrors)
is fixed; the Issue 57 refund pulled in two new defects: (a) refund opens an
RLS-gated app-role session with no `creator_id` set, which will silently
no-op every refund once the prod role split is live, and (b) refund
idempotency is a read-then-write without a UNIQUE on `pack_id`, so a
manual-recovery + automatic on_failure race can double-refund. Both have a
clear, mechanical fix.
