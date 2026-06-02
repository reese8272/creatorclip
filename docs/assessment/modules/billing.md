# billing — assessed 2026-06-02

## Findings
- [cleanup] billing/ledger.py:64 — fast-path idempotency check on `MinutePack.stripe_session_id` does not filter by `creator_id` | analysis: `stripe_session_id` is globally UNIQUE, so the query returns at most one row. Semantically safe; the INSERT would fail anyway with IntegrityError if attempted under a different creator. However, this is a defense-in-depth gap: if grant_minutes is ever called with a stripe_session_id from a different creator (e.g., via code path confusion), the fast-path silently no-ops instead of attempting the INSERT and surfacing the UNIQUE conflict. Adding creator_id filter makes the intent explicit and hardens against future bugs. | fix: change line 64 to `.where(MinutePack.stripe_session_id == stripe_session_id, MinutePack.creator_id == creator_id)`

- [cleanup] billing/stripe_client.py:34 — module-level side-effect: `stripe.max_network_retries = 3` mutates third-party global state at import time | analysis: Works correctly, but order-dependent (any earlier `import stripe` sees the default). Not a correctness bug, but not idiomatic Python. Better to call this from app startup (e.g., app.py or on first Stripe API call). | fix: wrap in a `def _configure_stripe_sdk()` function and call from application startup, or add a comment explaining the intentional global mutation.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — `AdminSessionLocal` used via `async with` in refund.py:50; callers own outer transaction commit (documented); `_STRIPE` module-level singleton with explicit `HTTPXClient(timeout=10s)` |
| 2 Concurrency & scale | ok — `create_checkout_session` is sync but wrapped in `asyncio.to_thread` (routers/billing.py:109); 10s timeout caps executor occupancy; no blocking calls in `async def`. Idempotency: `MinuteDeduction.video_id` UNIQUE (Issue 34), `MinutePack.stripe_session_id` UNIQUE (models.py:596), partial index on refund pack_id (migration 0013). All money mutations atomic: UPDATE…WHERE…RETURNING + SAVEPOINT + IntegrityError pattern |
| 3 Security & compliance | ok — per-creator isolation verified: `get_balance` (ledger.py:33), `grant_minutes` (ledger.py:86), `deduct_for_video` (ledger.py:145), refund derives creator from deduction row. No secrets/PII in logs (only public IDs). Parameterized SQL only (SQLAlchemy ORM). intent_id validated as UUID4 (stripe_client.py:65). Webhook creator_id sourced from Stripe metadata set by authenticated checkout endpoint (safe) |
| 4 Clip-quality | n/a |
| 5 Anthropic SDK | n/a |
| 6 Cleanliness & typing | ok — all functions fully typed (`-> int`, `-> None`, `-> str`). No TODOs, commented blocks, or print(). 2 cleanup items are cosmetic, no correctness impact. Functions <40 lines, single-responsibility |
| 7 Error handling | ok — raises HTTPException(402/404) for user-facing errors; RuntimeError/ValueError for infra faults. Webhook returns idempotent responses (ignored/already_fulfilled/ok). 402 messages actionable (show concrete minute gap) |
| 8 Config & paths | ok — `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_TIMEOUT_S` from pydantic settings. Fail-fast on missing keys. Documented in `.env.example` |

## Module verdict
clean — all money/minute mutations are atomic and idempotent (per-video, per-session, per-refund). Per-creator isolation on every balance/pack query. Stripe integration has 10s timeout, server-side idempotency-key, UUID4 validation. 2 cleanup items (import side-effect, fast-path defense-in-depth) are cosmetic and do not affect correctness.
