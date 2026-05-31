# billing — assessed 2026-05-31

## Findings
- [SEV2] billing/stripe_client.py:20 — `_STRIPE = stripe.StripeClient(settings.STRIPE_SECRET_KEY)`
  is constructed with no explicit HTTP timeout. The Stripe Python SDK defaults
  to ~80s, which under a Stripe slowdown will pin one worker thread per stuck
  checkout call (the call runs in `asyncio.to_thread`, so it occupies a thread
  in the default executor — capped, exhaustible). Scale-checklist E says
  "Timeouts on every external call." | fix: pass an explicit short timeout to
  the client, e.g. `stripe.StripeClient(settings.STRIPE_SECRET_KEY,
  http_client=stripe.HTTPXClient(timeout=10.0))` (or set
  `stripe.default_http_client = stripe.HTTPXClient(timeout=10.0)`), and surface
  the configured value via `settings.STRIPE_TIMEOUT_S` with a default of 10.
- [SEV2] billing/stripe_client.py:23-67 — `create_checkout_session` does NOT
  pass `idempotency_key=` to `_STRIPE.checkout.sessions.create(params)`. The
  Stripe SDK auto-injects an `Idempotency-Key` only for its OWN
  transport-level retries (`max_network_retries=3`); a user double-click /
  router retry / Celery requeue that calls this function twice will create two
  Checkout sessions. Each session, if completed, creates a separate
  `MinutePack` row (the `stripe_session_id` UNIQUE protects against duplicate
  *grants* for the *same* session, not against two sessions being created from
  one purchase intent). With one-time `mode=payment` Stripe will only charge a
  session the user completes, so this is not a double-charge today — but if
  the user happens to complete both checkout flows (back-button, parallel
  tabs), they pay twice and the ledger grants twice. | fix: derive a stable
  idempotency key per (creator_id, pack_id, purchase-intent token) — e.g.
  accept an `intent_id: str` from the router that is a per-click uuid, and
  call `_STRIPE.checkout.sessions.create(params,
  options={"idempotency_key": intent_id})`. Document the contract in the
  router that the intent token is generated server-side at /pricing render
  time and stored in the session, not echoed from the client.
- [SEV2] billing/stripe_client.py:67 — `return session.url` is typed as `str`
  in the signature (`-> str`), but the Stripe SDK types `Session.url` as
  `Optional[str]` (a Checkout session in `mode=payment` always has a URL on
  create, but the type is nullable). A None slipping through would surface as
  a `"None"` string redirect or an `AttributeError` deeper in the router. |
  fix: assert / handle the None: `url = session.url; if url is None: raise
  RuntimeError(f"Stripe returned no checkout URL for session {session.id}");
  return url`.
- [cleanup] billing/stripe_client.py:36 — `params: dict = {…}` is missing a
  value type (CLAUDE.md mandates typed signatures and the body annotation is
  load-bearing for the heterogeneous dict passed to Stripe). | fix: declare
  `params: dict[str, Any]` and `from typing import Any`. Mypy gate may not
  flag the inner annotation depending on config but the discipline is the
  project standard.
- [cleanup] billing/stripe_client.py:18 — `stripe.max_network_retries = 3` is
  a module-import side-effect mutating a third-party library's global. It
  works but is order-dependent (any other import of `stripe` that runs first
  sees the default). | fix: move into an explicit `_configure_stripe()`
  called from app startup, or accept it as documented behavior with a one-line
  comment explaining the global mutation.
- [cleanup] billing/__init__.py:1 — file is empty (0 bytes). Fine as a
  package marker; flagged only to confirm it is intentional and not a
  partially-written module. No change needed.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — `AdminSessionLocal` used via `async with` in refund.py:50, callers of `grant_minutes`/`deduct_for_video` own outer commit (documented in docstrings), `_STRIPE` is a module-level singleton |
| 2 Concurrency & scale | ok by design — `create_checkout_session` is sync but called via `asyncio.to_thread` from routers (tests/test_billing.py:342 asserts this); no `requests.`, `time.sleep`, or sync DB driver inside any `async def` here. **1 SEV2 (timeout)** |
| 3 Security & compliance | ok — no PII or token in logger lines (creator_id, video_id, minutes, pack_id only). Per-creator isolation: ledger functions take `creator_id` as a parameter; refund derives it from the `MinuteDeduction` row (system action under BYPASSRLS, correctly documented). No virality promise. Parameterized SQL only (SQLAlchemy ORM/Core throughout) |
| 4 Clip-quality | n/a (billing module, not clip) |
| 5 Anthropic SDK | n/a (no LLM calls) |
| 6 Cleanliness & typing | mostly ok — 2 cleanup items (untyped dict, side-effect import). No TODOs, no commented blocks, no print() |
| 7 Error handling / API | n/a (routers own that surface; this module raises `HTTPException(402/404)` cleanly with safe messages — no DB errors or stack traces leaked) |
| 8 Config & paths | ok — `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` read from `settings` (pydantic-settings). Fail-fast handled at settings layer. **SEV2 above asks for a new STRIPE_TIMEOUT_S** to add to `.env.example` |

## Module verdict
**NEEDS-WORK** — no BLOCKER (idempotency math is rigorous: `MinuteDeduction.video_id` UNIQUE, `MinutePack.stripe_session_id` UNIQUE, partial UNIQUE on refund pack_id, SAVEPOINT+IntegrityError pattern, per-creator isolation honored), but Stripe-side has no explicit HTTP timeout, no Stripe idempotency-key on `checkout.sessions.create`, and a nullable `.url` typed as non-null — three SEV2s that should land before public launch.
