# billing — assessed 2026-06-07

## Findings

- [SEV2] billing/stripe_client.py:101 — Stripe `Idempotency-Key` is the raw
  client-supplied `intent_id` (a v4 UUID from sessionStorage). Stripe scopes
  idempotency keys per API key, not per Stripe Customer, so any creator who
  somehow obtains another creator's `intent_id` (XSS, shared device,
  copy-pasted browser state, debugging) can collide with their pending
  Checkout session and either replay it or poison it within Stripe's 24h
  window. v4 UUIDs make the collision space astronomical, but the threat
  model isn't random collision — it's adversarial reuse, and per-tenant
  scoping is the standard hardening. | fix: scope the key by tenant —
  `options={"idempotency_key": f"{creator_id}:{intent_id}"}`. No client
  change required; the router already passes `creator_id` in. Add a brief
  note in the module docstring explaining the scoping.

- [cleanup] billing/ledger.py:64 — fast-path idempotency check on
  `MinutePack.stripe_session_id` does not filter by `creator_id`. The column
  is globally UNIQUE so the query is semantically safe (at most one row), but
  the missing filter is a defense-in-depth gap: a future code path that
  passes a `stripe_session_id` from a different creator would silently
  no-op instead of attempting the INSERT and surfacing the UNIQUE conflict.
  | fix: `.where(MinutePack.stripe_session_id == stripe_session_id,
  MinutePack.creator_id == creator_id)`.

- [cleanup] billing/stripe_client.py:34 — `stripe.max_network_retries = 3`
  mutates third-party SDK global state at import time. Works correctly today
  but is order-dependent (any earlier `import stripe` elsewhere sees the
  default) and not idiomatic. | fix: move into a `_configure_stripe_sdk()`
  helper invoked from app startup (`app.py` lifespan) or set via the
  `StripeClient` constructor / `with_options` if the SDK exposes a
  per-client setter.

- [cleanup] billing/refund.py:50 — `AdminSessionLocal` (BYPASSRLS) is opened
  but the docstring justification is correct only as long as the on_failure
  callback never has creator context available. Today that's true. If the
  Celery task signature ever gains an explicit `creator_id` arg (likely when
  per-creator quotas land), the refund path should switch to a scoped
  session so the BYPASSRLS surface shrinks. | fix: leave as-is, but add a
  `# TODO(scoped-session-when-context-available)` is exactly the kind of
  thing CLAUDE.md bans, so instead log this as a follow-up in
  `docs/OFF_COURSE_BUGS.md` and link the future-work issue.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — `AdminSessionLocal` used via `async with` in refund.py:50; rollback on the IntegrityError branch (refund.py:74). `_STRIPE` is a module-level singleton with explicit `HTTPXClient(timeout=STRIPE_TIMEOUT_S)`. Callers own the outer transaction commit and that contract is documented. |
| 2 Concurrency & scale | ok — `create_checkout_session` is the sync Stripe SDK, wrapped in `asyncio.to_thread` at routers/billing.py:109; 10s timeout caps executor-slot occupancy under a Stripe slowdown. `construct_webhook_event` is pure HMAC (fast, fine sync inside `async def`). All money mutations are atomic via UPDATE…WHERE…RETURNING + SAVEPOINT + IntegrityError-catch. Idempotency keys are present at every layer: `MinuteDeduction.video_id` UNIQUE (Issue 34); `MinutePack.stripe_session_id` UNIQUE (models.py:578); partial UNIQUE on refund `pack_id` (migration 0013); Stripe `Idempotency-Key` on Checkout create (Issue 106). |
| 3 Security & compliance | ok with one SEV2 — per-creator isolation verified on every balance/pack/deduction query (`get_balance` ledger.py:33, `grant_minutes` ledger.py:86, `deduct_for_video` ledger.py:145; refund derives `creator_id` from the existing deduction row). No secrets/PII in any log line — only opaque IDs (`pack_id`, `creator_id`, `video_id`, `stripe_session_id`). Parameterized SQL only (SQLAlchemy ORM). `intent_id` shape-validated as v4 UUID at stripe_client.py:65 before becoming an idempotency key. The one open item is the un-scoped Stripe idempotency key (finding 1). |
| 4 Clip-quality | n/a (billing, not a clip module). |
| 5 Anthropic SDK | n/a — no LLM calls. |
| 6 Cleanliness & typing | ok — every function fully typed (`-> int`, `-> None`, `-> str`, `-> stripe.Event`). No TODOs, no commented-out blocks, no `print()`. All functions under ~30 LOC and single-responsibility. |
| 7 Error handling | ok — user-facing failures raise `HTTPException(402/404)` with actionable copy (the 402 in `check_balance_for_minutes` surfaces the concrete gap, not a generic "insufficient"). Infra faults raise `RuntimeError`/`ValueError` and surface as a 502 in the router wrapper without leaking Stripe internals (`"Could not create checkout session"`). Webhook returns idempotent JSON statuses (`ignored` / `already_fulfilled` / `ok`) and uses 400 only for bad signature / payload — Stripe's recommended pattern. |
| 8 Config & paths | ok — `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_TIMEOUT_S` all in `.env.example` with descriptions and wired through pydantic-settings; checkout endpoint fails fast with 503 if `STRIPE_SECRET_KEY` is unset. No paths in this module. |

## Module verdict

NEEDS-WORK (mild) — billing is structurally sound: every money/minute
mutation is atomic and idempotent, per-creator isolation is enforced on
every query, the sync Stripe SDK is offloaded to a thread with a 10s
timeout, and logs are clean. The one real finding is the un-scoped Stripe
`Idempotency-Key` (SEV2, defense-in-depth on a money path); the three
cleanup items are stylistic / hardening touches.
