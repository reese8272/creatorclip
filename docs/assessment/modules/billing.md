# billing — assessed 2026-07-01

Slice: `billing/ledger.py`, `billing/packs.py`, `billing/refund.py`,
`billing/stripe_client.py`, `billing/__init__.py` (empty).

All Stripe-SDK claims below are verified against CURRENT official Stripe
documentation and the stripe-python `master` source (fetched 2026-07-01), not
from memory. Citations inline.

## Findings

- [SEV1] billing/stripe_client.py:34 — `stripe.max_network_retries = 3` is a
  **no-op** for every request in this module. All Stripe calls go through
  `_STRIPE = stripe.StripeClient(...)` (line 36), and the v8 `StripeClient`
  deliberately ignores module-level globals ("No global config" — you must pass
  options to the constructor). I traced the resolution in the SDK source: when
  `max_network_retries` is not passed to `StripeClient`, `RequestorOptions`
  keeps it `None`, and `_http_client._should_retry` resolves it as
  `max_network_retries if max_network_retries is not None else 0`. Net effect:
  checkout-session creation and reconciliation listing run with **0 automatic
  retries** despite the code clearly intending 3, so a transient network blip
  surfaces to the user as a 502 (routers/billing.py:157) / a missed
  reconciliation instead of a safe retry. Idempotency keys are already present,
  so retries would be safe. | fix: delete line 34 and pass the option to the
  constructor:
  `_STRIPE = stripe.StripeClient(settings.STRIPE_SECRET_KEY, http_client=stripe.HTTPXClient(timeout=settings.STRIPE_TIMEOUT_S), max_network_retries=3)`.
  Sources: Migration guide for v8 (StripeClient) —
  https://github.com/stripe/stripe-python/wiki/Migration-guide-for-v8-(StripeClient)
  ("stripe.max_network_retries = 3 → client = stripe.StripeClient('sk_test_123',
  max_network_retries=3)"; "No global config"); default-to-0 resolution in
  https://raw.githubusercontent.com/stripe/stripe-python/master/stripe/_http_client.py
  (retrieved 2026-07-01).

- [SEV2] billing/stripe_client.py:60-67,119 — the Stripe `Idempotency-Key` is
  the bare client UUID (`intent_id`), which is **account-scoped** (shared across
  all creators in the one CreatorClip Stripe account), and the comment's
  security rationale is misleading. UUID-*shape* validation does NOT "close the
  vector where a client sends a garbage string that collides with another
  creator's idempotency key" — it does nothing to stop a client submitting
  another creator's *valid* UUID. What actually protects the money path is
  (a) v4-UUID randomness and (b) Stripe's documented parameter-mismatch guard:
  "The idempotency layer compares incoming parameters to those of the original
  request and errors if they're not the same" — so a reused key with a different
  `metadata.creator_id` errors rather than charging/leaking. There is therefore
  no exploitable cross-tenant leak today, but the key is not tenant-scoped as a
  structural property. | fix (defense-in-depth): scope the key to the tenant —
  `options={"idempotency_key": f"{creator_id}:{intent_id}"}` (Stripe keys allow
  up to 255 chars), which makes cross-tenant reuse impossible regardless of UUID
  entropy; and correct the misleading comment. Sources: Idempotent requests —
  https://docs.stripe.com/api/idempotent_requests (24h window, ≤255 chars,
  parameter-mismatch error, UUID-v4 recommendation, retrieved 2026-07-01).

- [SEV2] billing/ledger.py:303-314 — `send_notification.delay(...)` (balance_low
  trigger) is enqueued INSIDE `deduct_for_video`, after the SAVEPOINT but BEFORE
  the caller commits the outer transaction (the docstring at line 244 states
  "Caller is responsible for committing the outer transaction"). If the outer
  transaction rolls back after this function returns, the deduction is undone but
  the notification was already enqueued to Celery → a spurious "balance low"
  alert for a charge that never persisted (a transactional-outbox violation).
  On a Celery *retry* the deduction fast-paths to 0 so it won't re-fire, but the
  pre-commit enqueue window remains. | fix: return a `low_balance` flag (or the
  `remaining` value) to the caller and enqueue the notification only after the
  caller's `commit()`, or attach it to a SQLAlchemy `after_commit` session event.
  (needs-runtime-confirmation that a caller path can roll back after deduct
  returns.)

- [cleanup] .env.example — `COST_CACHE_WRITE_MULTIPLIER` (consumed at
  billing/ledger.py:109 for the cache-write cost term) is absent from
  `.env.example`, though its sibling `COST_CACHE_READ_MULTIPLIER` is present
  (line 33). It has a safe default (config.py:145 = 1.25) so it is not
  fail-fast-critical, but the rubric requires new config documented in the
  template. | fix: add `COST_CACHE_WRITE_MULTIPLIER=1.25  # Anthropic cache-write
  multiplier (1.25× base input rate, 5-min TTL; scoring passes 2.0 for 1h TTL)`.

- [cleanup] billing/ledger.py:118-123 — `record_llm_usage(usage: dict, ...)`
  takes a bare, unparameterized `dict` on a cost-accounting path whose keys are
  fixed and known (`input_tokens`/`output_tokens`/`cache_read`/`cache_creation`).
  | fix: type as `dict[str, int]` or a small `TypedDict` so the money math has a
  checked shape.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — every DB session via `async with ... AdminSessionLocal()` / passed-in `AsyncSession`; `_STRIPE` is a module-level singleton; deduct/grant/refund idempotent via UNIQUE keys + SAVEPOINT. 1 SEV2 (pre-commit notification enqueue) |
| 2 Concurrency & scale | 1 SEV1 (retries no-op). Sync Stripe calls ARE offloaded by callers (routers/billing.py:147 `asyncio.to_thread`, worker/tasks.py:2722 `run_in_executor`); `construct_webhook_event` runs inline but is offline HMAC (no network) — fine. Pagination in `list_recent_paid_sessions` is bounded by the lookback window |
| 3 Security & compliance | 1 SEV2 (idempotency key not tenant-scoped, non-exploitable). Per-creator isolation ok: every query scoped by `Creator.id`/`creator_id`; refund derives creator_id from the deduction row; AdminSessionLocal BYPASSRLS is documented/justified for system paths. Parameterized ORM only. Webhook verified via `stripe.Webhook.construct_event` with `STRIPE_WEBHOOK_SECRET` (default 300s replay tolerance). No secret/PII in logs (creator_id/video_id are UUIDs; `cs_...`/`whsec_` session ids are non-secret). No virality copy |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | n/a — module only prices token-usage dicts; it makes no LLM call |
| 6 Cleanliness & typing | 1 cleanup (bare `dict` on record_llm_usage). Otherwise fully typed; no TODO/print/dead code |
| 7 Error handling / API | n/a (routers own the API surface). stripe_client raises `ValueError`/`RuntimeError` with safe messages for the router to map |
| 8 Config & paths | 1 cleanup (`COST_CACHE_WRITE_MULTIPLIER` missing from .env.example). No filesystem paths in this module; Stripe/cost/balance config all via pydantic settings |

## Module verdict
NEEDS-WORK — one SEV1 (Stripe automatic-retries silently disabled: the module
global is a verified no-op under the v8 StripeClient) plus two SEV2 hardening
items; no BLOCKER and no cross-tenant leak.
