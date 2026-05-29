# billing — assessed 2026-05-29

Slice: `billing/__init__.py`, `billing/ledger.py`, `billing/packs.py`,
`billing/stripe_client.py`. Callers in `routers/billing.py`, `routers/auth.py`,
`worker/tasks.py` and the `MinutePack` / `MinuteDeduction` models were traced only
to verify the billing functions' idempotency / isolation contracts (those files are
owned by other agents and are not scored here).

## Findings

- [SEV1] billing/ledger.py:39-66 — `grant_minutes()` is NOT idempotent on its own:
  it unconditionally `session.add(MinutePack(...))` and increments
  `Creator.minutes_balance`. Money-credit idempotency depends entirely on the
  caller doing a check-then-act (`routers/billing.py:144-148` selects an existing
  `MinutePack` by `stripe_session_id`, then calls grant). That check-then-act is a
  TOCTOU window: two concurrent deliveries of the same `checkout.session.completed`
  webhook (Stripe retries + at-least-once delivery) both pass the `existing is None`
  check and both call `grant_minutes`. The only thing preventing a double credit is
  the `UNIQUE(stripe_session_id)` constraint on `minute_packs` (models.py:446-448) —
  but `grant_minutes` does not catch `IntegrityError`, so the loser of the race
  raises an unhandled error at `session.commit()` and returns HTTP 500 to Stripe
  (Stripe then retries and gets `already_fulfilled` — so no double credit lands, but
  the path is fragile and noisy). | fix: make grant idempotent at the source — wrap
  the INSERT+UPDATE in a `session.begin_nested()` SAVEPOINT and catch `IntegrityError`
  on the `stripe_session_id` UNIQUE violation, returning a "no-op / already granted"
  signal (mirror the proven pattern in `deduct_for_video` at ledger.py:97-127). Add an
  `idempotency_key` / `stripe_session_id`-keyed guard so grant is safe regardless of
  caller discipline. Add a test that fires two grants with the same `stripe_session_id`
  via `asyncio.gather` on two separate sessions (like
  tests/test_billing_idempotency.py:117) and asserts exactly one MinutePack row and a
  single balance increment.

- [SEV2] tests/test_billing_integration.py:186 — webhook/grant idempotency is only
  covered for SEQUENTIAL duplicate delivery; there is no truly-concurrent grant test
  (two sessions racing the same `stripe_session_id`). The deduction path has this
  coverage (tests/test_billing_idempotency.py:117) but the credit path — the one that
  mutates money upward — does not. | fix: add the concurrent grant test described
  above; it is the only thing that proves the SEV1 fix and exercises the
  `UNIQUE(stripe_session_id)` backstop under real contention.

- [SEV2] billing/stripe_client.py:20 — `_STRIPE = stripe.StripeClient(settings.STRIPE_SECRET_KEY)`
  is constructed at module import with the empty-string default
  (config.py:54 `STRIPE_SECRET_KEY: str = ""`). It is a module-level singleton
  (good per rubric §1), but it is built even when billing is unconfigured, and there
  is no fail-fast on missing key — the failure surfaces only at first checkout call.
  The webhook path (`construct_webhook_event`, line 70-71) likewise passes an
  empty `STRIPE_WEBHOOK_SECRET` straight to `construct_event`; an unset secret means
  signature verification effectively cannot succeed (fails closed — acceptable) but
  is silent. | fix: validate at startup — either make `STRIPE_SECRET_KEY` /
  `STRIPE_WEBHOOK_SECRET` required when `ENV == "production"` via a pydantic-settings
  validator (fail-fast per CLAUDE.md Production Standards), or guard the singleton
  build behind a configured check and have the webhook route 503 when
  `STRIPE_WEBHOOK_SECRET` is empty instead of relying on a verification failure.

- [SEV2] billing/ledger.py:69-136 — `deduct_for_video` relies on the caller's outer
  transaction (`session.begin_nested()` requires an active outer transaction; the doc
  at line 86 says "Caller is responsible for committing"). The worker caller
  (worker/tasks.py:182-192) opens `AsyncSessionLocal()` and commits, which autobegins,
  so it works today — but the contract is implicit. If `deduct_for_video` raises
  `HTTPException(402)` on insufficient balance (line 116-124) inside a Celery task, a
  402 is a strange exception type to surface from a worker (it is an HTTP concept),
  and it propagates uncaught out of the worker path. | fix: raise a domain exception
  (e.g. `InsufficientMinutesError`) from the ledger and let the HTTP layer translate
  to 402; the worker can then catch the domain error and mark the video
  `failed_insufficient_minutes` rather than crashing the task. Keeps the ledger free
  of HTTP coupling (single responsibility).

- [cleanup] billing/ledger.py:17,118 — `from fastapi import HTTPException` and raising
  `HTTPException` from a service-layer module couples billing logic to the web
  framework (same root cause as the SEV2 above; flagged separately as the import is
  the structural smell). | fix: move HTTP translation to the router; ledger raises a
  plain domain exception.

- [cleanup] billing/stripe_client.py:36 — `params: dict` is untyped on its value side
  (`dict[str, object]` or a TypedDict would be clearer for the Stripe params shape);
  minor, mypy may already accept it. | fix: annotate `params: dict[str, object]` or a
  Stripe-params TypedDict.

- [cleanup] billing/ledger.py:32 — `get_balance` returns `int` but the `select` of a
  nullable column / missing creator is handled (raises 404). Fine; noted only that the
  same HTTP-in-service-layer coupling applies (line 35 raises HTTPException). Roll into
  the domain-exception refactor above.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — `_STRIPE` is a module-level singleton; deduct uses SAVEPOINT; no leaks. Caller owns commit (documented). |
| 2 Concurrency & scale | ok — `deduct_for_video` is concurrency-safe via UNIQUE(video_id) + atomic `UPDATE … WHERE balance >= minutes`; no blocking sync calls in async paths. Stripe SDK calls are sync but live in sync functions called from routers (not inside `async def`) — verify the router does not call them on the loop (caller's scope). |
| 3 Security & compliance | ok — no token/PII in logs (logs creator_id + counts only); per-creator scoping present on all mutations (`WHERE Creator.id == creator_id`); webhook signature verified by Stripe SDK in caller; creator_id derived from session JWT in callers, not request body. No virality language. |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | n/a (no LLM calls) |
| 6 Cleanliness & typing | 3 cleanup findings — HTTP coupling in service layer, untyped `params` dict value. |
| 7 Error handling / API | n/a here (no router in slice) — but see SEV2: HTTPException raised from service layer is the wrong altitude. |
| 8 Config & paths | 1 finding (SEV2) — Stripe keys default to "" with no fail-fast; present in `.env.example` (good). No filesystem paths in this module. |

## Module verdict
NEEDS-WORK — no cross-tenant leak and no actual double-credit (the
`UNIQUE(stripe_session_id)` constraint holds), but `grant_minutes` is not
self-idempotent and pushes money-credit idempotency onto a TOCTOU check in the
caller; harden grant at the source with an IntegrityError-guarded SAVEPOINT and add a
concurrent-grant test before launch.
