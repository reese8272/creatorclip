# billing — assessed 2026-05-31

## Findings
- [SEV1] billing/stripe_client.py:65 — `_STRIPE.checkout.sessions.create(params)`
  is the synchronous Stripe SDK (urllib3 under the hood) called directly inside
  an `async def` route (`routers/billing.py:84` `checkout`); each checkout HTTP
  round-trip (often 300–800ms p95) blocks the FastAPI event loop, capping
  concurrent checkouts to one per worker process and dragging unrelated request
  latency. Rubric 2. | fix: either (a) use Stripe's async client
  (`stripe.AsyncStripeClient`/`stripe.aio`) — preferred, single-line swap; or
  (b) wrap the call in `await asyncio.to_thread(_STRIPE.checkout.sessions.create, params)`
  and add a regression test asserting the route returns under a synthetic 500ms
  Stripe latency without serializing requests.

- [SEV2] billing/refund.py:57-71 — refund idempotency is still a read-then-write
  on `pack_id`: `select(MinutePack.id).where(MinutePack.pack_id == pack_id)`
  followed by `grant_minutes(...)`. `models.py:507` has no UNIQUE constraint on
  `(reason, pack_id)` or `pack_id` (only `stripe_session_id` is unique). Two
  concurrent `on_failure` callbacks for the same `video_id` would both see "no
  existing refund" and both insert a refund + double-credit the balance. Hotfix B
  (AdminSessionLocal) closed the RLS no-op SEV1 but did NOT close this race.
  Carry-forward SEV2 confirmed open. Rubric 1/3. | fix: add an Alembic migration
  creating `UNIQUE INDEX minute_packs_refund_key ON minute_packs (pack_id) WHERE
  reason = 'refund'` (partial index avoids colliding with non-refund packs that
  share `pack_id` like `"trial"`/`"starter"`), then drop the read-then-write
  guard and let `grant_minutes`'s existing `IntegrityError` catch path no-op the
  duplicate. Update the docstring (currently says "not reachable in the current
  pipeline" — Celery's `task_acks_late=True` + worker preemption can in fact
  produce concurrent terminal failures for the same task id).

- [SEV2] billing/stripe_client.py:20 — `_STRIPE = stripe.StripeClient(settings.STRIPE_SECRET_KEY)`
  is constructed at import time with whatever `STRIPE_SECRET_KEY` is in the
  environment, even in dev where it defaults to `""`. `Settings._require_prod_secrets`
  (`config.py:131`) only fails-fast in `ENV == "production"`, so a misconfigured
  staging/preview deploy (e.g. `ENV=staging` with no key) silently binds an empty
  client; the failure surfaces only at first `/checkout`. The router's 503 gate
  (`routers/billing.py:89`) papers over it for that route but `_STRIPE` is now a
  global landmine. Rubric 8. | fix: lazy-init via `functools.lru_cache` returning
  the client only when `settings.STRIPE_SECRET_KEY` is set, and raise a clear
  `RuntimeError("Stripe not configured")` from the accessor; or extend
  `_require_prod_secrets` to also fail for `ENV == "staging"`.

- [SEV2] billing/refund.py:48-72 — refund opens its own `AdminSessionLocal()`
  and commits inside, but `grant_minutes` inside `refund_for_video` opens a
  `session.begin_nested()` SAVEPOINT. SQLAlchemy 2.0 async will implicitly begin
  the outer transaction on the first `await session.scalar(...)`, so the
  SAVEPOINT path works today, but the early-return paths (`return 0` at line 54
  / 62) exit the `async with` without an explicit commit — currently harmless
  because no writes occurred, but masks intent and is fragile to refactor. Rubric
  1. | fix: wrap the entire body in `async with session.begin():` to make the
  outer transaction explicit, and drop the trailing `await session.commit()` —
  fixes the early-return-without-commit ambiguity and documents the unit-of-work
  boundary.

- [SEV2] billing/refund.py — refund triggers purely on Celery `on_failure`
  invocation without re-checking the video's terminal state. If `on_failure`
  were ever invoked on a still-completable task (e.g. broker replay during a
  chain reschedule), the `MinuteDeduction` row exists and the refund proceeds,
  double-crediting against an eventually-successful run. Rubric 1
  (idempotency). | fix: gate refund on `video.render_status IN ('failed',
  'errored')` (single extra SELECT inside the same session), or document the
  trade explicitly in `docs/DECISIONS.md` and rely on the pack_id UNIQUE
  partial-index fix above. (needs-runtime-confirmation that the chain truly
  cannot fire `on_failure` on a succeeded task.)

- [cleanup] billing/stripe_client.py:36 — `params: dict = {...}` missing type
  parameters. Rubric 6. | fix: annotate `params: dict[str, object] = {...}`.

- [cleanup] billing/stripe_client.py:67 — `create_checkout_session` returns
  `session.url`, which the Stripe typestub types as `str | None`; the function
  signature promises `str`. Rubric 6/7. | fix: `assert session.url is not
  None, "Stripe Checkout session missing url"` before the return, or change
  the return type to `str | None` and surface the None to the router as a 502.

- [cleanup] billing/ledger.py:36 / 182 / 204 — duplicated `HTTPException(
  status_code=402, detail="...Purchase a pack at /pricing...")` shape across
  `check_positive_balance`, `check_balance_for_minutes`, and `deduct_for_video`.
  Rubric 6 (DRY). | fix: extract `_raise_insufficient(detail: str) -> NoReturn`
  to centralize the 402 + copy.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | 3 findings (refund pack_id race, implicit outer tx, terminal-state gate) |
| 2 Concurrency & scale | 1 SEV1 (sync Stripe SDK in async route) |
| 3 Security & compliance | ok — no token handling in this slice; per-tenant isolation OK (MinuteDeduction.video_id is UNIQUE, deduction.creator_id used downstream); refund's Admin session is intentional + documented (Hotfix B closes the RLS no-op SEV1); no PII in any log line |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | n/a (no LLM calls) |
| 6 Cleanliness & typing | 3 cleanups (dict generics, return-type narrowing, 402 DRY) |
| 7 Error handling / API | n/a (router lives in `routers/billing.py`, not this slice) |
| 8 Config & paths | 1 SEV2 (module-level Stripe client + non-prod fail-fast gap) |

## Module verdict
NEEDS-WORK — Hotfix B closes the refund-under-RLS SEV1, Issue 89 closes the
display-vs-filter SEV1, but the refund double-credit race (carry-forward SEV2)
and the sync-Stripe-in-async route (SEV1) both remain open before launch.
