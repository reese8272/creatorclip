# billing — assessed 2026-05-31

## Findings
- [resolved] billing/stripe_client.py:36-39 — **(was SEV2)** explicit HTTP
  timeout now wired: `_STRIPE = stripe.StripeClient(settings.STRIPE_SECRET_KEY,
  http_client=stripe.HTTPXClient(timeout=settings.STRIPE_TIMEOUT_S))` with a
  default of 10s in `config.py:165` and a documented entry in `.env.example:87`.
  Scale-checklist E ("Timeouts on every external call") satisfied for the
  Stripe surface. CLOSED.
- [resolved] billing/stripe_client.py:42-108 — **(was SEV2)**
  `create_checkout_session` now requires `intent_id: str`, validates it is a
  v4 UUID (`uuid.UUID(intent_id, version=4)`), and passes
  `options={"idempotency_key": intent_id}` to
  `_STRIPE.checkout.sessions.create`. Router (`routers/billing.py:54`) types
  the Pydantic body field as `UUID4` so a client-side garbage payload is
  rejected at 422 before it ever reaches the function. Double-pay vector
  (back-button, parallel tabs within Stripe's 24h idempotency window) closed.
  Integration test at `tests/test_billing.py:413` (`test_create_checkout_session_passes_idempotency_key`)
  asserts the kwarg shape; `tests/test_billing.py:452` asserts the UUID
  validation. CLOSED.
- [resolved] billing/stripe_client.py:103-106 — **(was SEV2)** `session.url`
  None-check now in place: raises `RuntimeError(f"Stripe returned no checkout
  URL for session {session.id}")` before returning, keeping the `-> str` type
  contract sound. Test at `tests/test_billing.py:470`
  (`test_create_checkout_session_raises_when_session_url_is_none`) covers it.
  CLOSED.
- [cleanup] billing/stripe_client.py:34 — `stripe.max_network_retries = 3` is
  still a module-import side-effect mutating a third-party library's global.
  Carried over from prior assessment; works correctly but is order-dependent
  (any earlier `import stripe` sees the default until this module loads). Not
  blocking — flag for future tidy-up. | fix: move into an explicit
  `_configure_stripe()` invoked from app startup, OR add a one-line comment
  acknowledging the intentional global mutation.
- [cleanup] billing/__init__.py:1 — file is empty (1 line, no content). Fine
  as a package marker; no change needed.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — `AdminSessionLocal` used via `async with` in refund.py:50; callers of `grant_minutes`/`deduct_for_video` own outer commit (documented); `_STRIPE` is a module-level singleton with an explicit `HTTPXClient` |
| 2 Concurrency & scale | ok — `create_checkout_session` is sync but called via `asyncio.to_thread` from routers (tests/test_billing.py:343 asserts this); explicit 10s timeout caps thread-pool occupancy under Stripe slowdown; no `requests.`, `time.sleep`, or sync DB driver inside any `async def`. Section C (Celery idempotency): structural — `MinuteDeduction.video_id` UNIQUE, `MinutePack.stripe_session_id` UNIQUE, partial UNIQUE on refund pack_id (migration 0013), SAVEPOINT+IntegrityError pattern in grant/deduct/refund |
| 3 Security & compliance | ok — no PII or token in logger lines (creator_id, video_id, minutes, pack_id, stripe session_id only — no card/email/secret). Section D (per-tenant isolation): ledger fns take `creator_id` as a parameter; refund derives it from the `MinuteDeduction` row (system action under BYPASSRLS, correctly documented at refund.py:42-48). No virality promise. Parameterized SQL only (SQLAlchemy ORM/Core throughout). intent_id UUID4 validation closes the cross-tenant idempotency-key collision vector |
| 4 Clip-quality | n/a (billing module, not clip) |
| 5 Anthropic SDK | n/a (no LLM calls) |
| 6 Cleanliness & typing | ok — `params: dict[str, Any]` typed, all signatures typed, no TODOs, no commented blocks, no print(). 1 cleanup (side-effect import of `stripe.max_network_retries`) |
| 7 Error handling / API | n/a for this module (routers own that surface); raises `HTTPException(402/404)` and `RuntimeError`/`ValueError` cleanly — no DB errors or stack traces leaked to clients |
| 8 Config & paths | ok — `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_TIMEOUT_S` all read from `settings` (pydantic-settings); fail-fast on the required two at `config.py:195`; `STRIPE_TIMEOUT_S=10` documented in `.env.example:87` |

## Module verdict
**clean** — all 3 prior Wave-8 SEV2s are CLOSED with verifying tests; remaining
items are cleanup-only (one global mutation, one empty `__init__.py`). Idempotency
math is rigorous on every money/minute mutation; Stripe surface now has explicit
HTTP timeout, server-side idempotency-key, UUID4 validation, and a sound return
contract.
