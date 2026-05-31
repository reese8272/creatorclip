# billing — assessed 2026-05-31

Wave-4 Fix 2 closed the carry-forward SEV2 "refund idempotency is a read-then-
write on `pack_id`". Verified: migration `alembic/versions/0013_refund_pack_id_unique.py`
creates `uq_minute_packs_refund_pack_id ON minute_packs (pack_id) WHERE
reason = 'refund'` CONCURRENTLY inside `autocommit_block()` (matches the 0006 /
0010 / 0011 pattern). `billing/refund.py:36-87` dropped the read-then-write
SELECT and now wraps `grant_minutes(...) + session.commit()` in
`try/except IntegrityError → rollback; return 0` — same shape as
`deduct_for_video`'s UNIQUE-race handler at `billing/ledger.py:159-161`.

Critical chain verified by reading: `grant_minutes` is called from refund with
`stripe_session_id=None`. The IntegrityError handler at
`billing/ledger.py:89-98` *re-raises* when `stripe_session_id is None` (because
the only UNIQUE race for keyed grants is the Stripe session race). The new
partial UNIQUE on `pack_id WHERE reason='refund'` produces an IntegrityError
that propagates UP through `grant_minutes` and lands in
`refund.py:68`'s except block → clean rollback + `return 0`. Pattern parity
with `MinuteDeduction.UNIQUE(video_id)` (Issue 34) and the
`creator_dna.build_job_id` partial UNIQUE (Issue 76 / migration 0008) holds.

No SEV1 introduced. Two carry-forward SEV2s re-verified open with identical
`file:line` shape; one SEV2 (refund transaction-boundary ambiguity) lingers
with reduced surface — early-return paths are still implicit-no-op only.

## Findings

- [SEV2] billing/stripe_client.py:20 — `_STRIPE = stripe.StripeClient(
  settings.STRIPE_SECRET_KEY)` is constructed at import time with whatever
  `STRIPE_SECRET_KEY` is in the environment, even in dev where it defaults
  to `""` (`config.py:142`). `Settings._require_prod_secrets`
  (`config.py:148-160`) only fails fast when `ENV == "production"` — a
  misconfigured `ENV=staging`/`preview` deploy silently binds an empty client;
  the failure surfaces only at first `/checkout` or `/webhook`. The router's
  503 gate (`routers/billing.py:90-91`) papers over `/checkout` but
  `construct_webhook_event` (`billing/stripe_client.py:70`) has no equivalent
  guard — a staging webhook delivery would raise a raw `stripe.SignatureVerificationError`
  with no clear "Stripe not configured" diagnostic. Carry-forward SEV2,
  re-verified open. Rubric 8. | fix: lazy-init via `functools.lru_cache`
  returning the client only when `settings.STRIPE_SECRET_KEY` is set and
  raising `RuntimeError("Stripe not configured")` from the accessor; OR
  extend `_require_prod_secrets` to also fail when `ENV in {"staging",
  "preview"}` and the keys are unset.

- [SEV2] billing/refund.py:36-87 — refund still triggers purely on Celery
  `on_failure` invocation (`worker/tasks.py:64`) without re-checking
  `Video.ingest_status`/`render_status`. If `on_failure` is ever invoked on
  a still-completable task (broker replay during chain reschedule, duplicate
  retry-exhausted firing), the `MinuteDeduction` row exists and the refund
  proceeds, double-crediting against an eventually-successful run. The new
  partial UNIQUE on `pack_id WHERE reason='refund'` catches a *duplicate
  refund* but NOT an *erroneous-first refund* against a video that ultimately
  succeeds. Carry-forward SEV2, re-verified open after Fix 2. Rubric 1
  (idempotency). | fix: gate refund on
  `select(Video.ingest_status).where(Video.id == video_id)` returning a
  terminal status (`IngestStatus.failed`) before calling `grant_minutes` —
  one extra SELECT in the same session. Or document the trade-off explicitly
  in `docs/DECISIONS.md` if the Celery chain is provably one-shot per video
  (the `worker/tasks.py:59-61` docstring claim that `on_failure` only fires
  on terminal exhaustion is a Celery contract, not an empirical guarantee
  under broker replay). (needs-runtime-confirmation that the Celery chain
  truly cannot fire `on_failure` on a succeeded task under broker replay.)

- [SEV2] billing/refund.py:50-79 — outer transaction boundary is still
  implicit. The `async with db.AdminSessionLocal() as session:` block opens
  a session, the early-return path at line 54-56 (`return 0` when no
  deduction exists) exits without commit/rollback, and the success path
  hits `await session.commit()` at line 67. Currently harmless because the
  early-return path performed only a SELECT, but the intent is ambiguous
  and a future refactor that adds a write before the early-return would
  silently drop it (auto-commit on session close is NOT the SQLAlchemy
  async default). The IntegrityError path at line 74 now explicitly
  rollbacks — good — but the no-deduction path at line 54 still does not.
  Rubric 1. | fix: wrap the body in `async with session.begin():` to make
  the outer transaction explicit and drop the trailing
  `await session.commit()` — documents the unit-of-work boundary and
  removes the early-return ambiguity. (Reduced from Wave-3 to Wave-4: Fix
  2 made the success/race paths explicit; only the no-deduction early
  return remains implicit.)

- [cleanup] billing/stripe_client.py:36 — `params: dict = {...}` missing
  generic type parameters. Rubric 6. | fix: annotate
  `params: dict[str, object] = {...}` (or a `TypedDict` if the structure
  should be locked down).

- [cleanup] billing/stripe_client.py:65-67 — `session.url` is typed as
  `str | None` by the Stripe typestubs; `create_checkout_session` declares
  `-> str` (`stripe_client.py:29`) and returns `session.url` without a None
  check. Rubric 6/7. | fix:
  `assert session.url is not None, "Stripe Checkout session missing url"`
  before the return, or widen the return type to `str | None` and have
  `routers/billing.py:101-112` surface the None as a 502.

- [cleanup] billing/ledger.py:35 / 182 / 204 — duplicated `HTTPException(
  status_code=402, detail="...Purchase a pack at /pricing...")` shape
  across `get_balance` (404), `check_positive_balance`,
  `check_balance_for_minutes`, and `deduct_for_video`. Rubric 6 (DRY).
  | fix: extract `_raise_insufficient(detail: str) -> NoReturn` to
  centralize the 402 + the "/pricing" copy so future copy edits don't
  drift across four call sites.

- [cleanup] billing/refund.py:23 — `from sqlalchemy import select` is now
  the only sqlalchemy import; `IntegrityError` is imported from
  `sqlalchemy.exc`. Both used — no dead import, but worth a glance during
  ruff sweep. Rubric 6 (housekeeping; no action required if ruff is green).

## Wave-4 closed (no longer open)

- [SEV2 → CLOSED] billing/refund.py — read-then-write idempotency
  (Wave-3 finding). Closed by migration 0013 (partial UNIQUE on
  `pack_id WHERE reason='refund'`) + IntegrityError catch in
  `refund_for_video`. Verified: the `grant_minutes` re-raise path
  (`billing/ledger.py:89-95`) propagates the IntegrityError up to refund
  because the refund call passes `stripe_session_id=None`. The race is
  now closed structurally at the DB layer, not at the application layer.

- [SEV1 → CLOSED, Wave 3] billing/stripe_client.py:65 — sync
  `_STRIPE.checkout.sessions.create(params)` inside an async route.
  Closed at the caller side: `routers/billing.py:101-108` wraps the call
  in `asyncio.to_thread(...)`. `construct_webhook_event` is called from
  the webhook route against `request.body()` payload bytes
  (CPU-bound HMAC verification, μs-scale) — no thread offload needed.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | 2 open (terminal-state gate; refund outer-tx boundary). Refund pack_id race CLOSED by migration 0013. |
| 2 Concurrency & scale | ok — sync-Stripe-in-async closed Wave-3; refund pack_id race closed Wave-4. |
| 3 Security & compliance | ok — no token handling in this slice; per-tenant isolation preserved (`MinuteDeduction.video_id UNIQUE` + deduction-tied `creator_id` carried into the refund grant); refund's `AdminSessionLocal` is intentional + documented (`billing/refund.py:42-48`); no PII in any `logger.*` call (verified). |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | n/a (no LLM calls in slice) |
| 6 Cleanliness & typing | 3 cleanups (dict generics, `session.url` None-narrow, 402 DRY). |
| 7 Error handling / API | n/a (router lives in `routers/billing.py`, not this slice) |
| 8 Config & paths | 1 SEV2 carry-forward (module-level Stripe client + non-prod fail-fast gap). |

## Module verdict
NEEDS-WORK — Wave-4 Fix 2 closed the refund pack_id double-credit race
structurally (migration 0013 + IntegrityError catch). Two SEV2s remain open
(Stripe client landmine on non-prod envs; refund without terminal-state
re-check) plus one reduced-surface tx-boundary SEV2. None are blockers; the
billing slice is now race-safe under Celery at-least-once delivery for the
duplicate-on_failure case, but the erroneous-first-refund case (broker
replay against a succeeded task) is still unresolved and should be either
gated or explicitly documented in `docs/DECISIONS.md` before launch.
