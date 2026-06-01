# billing — assessed 2026-05-31

## Findings
- [cleanup] billing/ledger.py:62-68 — fast-path check on `MinutePack.stripe_session_id` does not filter by `creator_id` before returning | analysis: The `stripe_session_id` is globally UNIQUE (models.py:562) and issued by Stripe, so only one row per session can exist regardless of creator. The returned row MUST belong to this transaction. However, for code clarity and defense-in-depth, add the creator_id filter: `.where(MinutePack.stripe_session_id == stripe_session_id, MinutePack.creator_id == creator_id)` to make the intent explicit and prevent future subtle bugs if the constraint is ever relaxed. | fix: add `MinutePack.creator_id == creator_id` to the WHERE clause in the fast-path query at ledger.py:64.

- [cleanup] billing/stripe_client.py:34 — module import side-effect: `stripe.max_network_retries = 3` mutates third-party global state at module load time | analysis: Works correctly but is order-dependent (any earlier `import stripe` sees the default). Not blocking, but not idiomatic. | fix: replace with `_configure_stripe()` function called from app startup (app.py or main.py), or add a comment: `# Stripe network retry policy — set here on first import of this module` to document the intentional global mutation.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — `AdminSessionLocal` used via `async with` in refund.py:50; callers of `grant_minutes`/`deduct_for_video` own outer commit (documented); `_STRIPE` is a module-level singleton with explicit `HTTPXClient(timeout=10s)` |
| 2 Concurrency & scale | ok — `create_checkout_session` is sync but called via `asyncio.to_thread` from routers/billing.py:109; explicit 10s timeout caps thread-pool occupancy; no blocking calls inside `async def`. Idempotency: `MinuteDeduction.video_id` UNIQUE (Issue 34), `MinutePack.stripe_session_id` UNIQUE (models.py:562), partial UNIQUE on refund pack_id (migration 0013), SAVEPOINT+IntegrityError pattern in grant/deduct/refund paths. All mutations are atomic UPDATE…WHERE…RETURNING |
| 3 Security & compliance | ok — **per-creator isolation verified on all queries**: `get_balance` filters `WHERE Creator.id == creator_id` (ledger.py:33); `grant_minutes` updates `WHERE Creator.id == creator_id` (ledger.py:86); `deduct_for_video` updates `WHERE Creator.id == creator_id, Creator.minutes_balance >= minutes` (ledger.py:145); `refund_for_video` derives creator_id from MinuteDeduction row (refund.py:60, safe under BYPASSRLS). No PII/token/secret in any logger line — only public identifiers (creator_id, video_id, minutes, pack_id, stripe_session_id). Parameterized SQL only (SQLAlchemy ORM/Core throughout). intent_id validated as UUID4 before Stripe call (stripe_client.py:65) |
| 4 Clip-quality | n/a (billing module) |
| 5 Anthropic SDK | n/a (no LLM calls) |
| 6 Cleanliness & typing | ok — all signatures fully typed; no TODOs, no commented-out blocks, no `print()`; 2 cleanup items (module import side-effect, fast-path clarity). Functions under 40 lines and single-responsibility |
| 7 Error handling / API | n/a for billing internals (routers own surface); module raises `HTTPException(402/404)` and `RuntimeError`/`ValueError` cleanly |
| 8 Config & paths | ok — `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_TIMEOUT_S` read from `settings` (pydantic-settings); fail-fast on required keys at config.py; all 3 documented in `.env.example` |

## Module verdict
**clean** — Idempotency is rigorous on all money/minute mutations (per-video, per-session, per-refund keys). Per-creator isolation verified on every balance/pack query. Stripe surface has explicit timeout, server-side idempotency-key, UUID4 validation, and sound return contracts. 2 cleanup items are cosmetic (import side-effect, fast-path clarity); no correctness risk.
