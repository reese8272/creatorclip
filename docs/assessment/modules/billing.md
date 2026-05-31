# billing — assessed 2026-05-31

Wave 2 did not modify any file in `billing/` or `routers/billing.py` (verified
`git diff f5d44df..HEAD -- billing/ routers/billing.py models.py` returns
empty). All Wave-1 carry-forward findings are reverified against current code
and remain open with identical `file:line` shape.

## Findings

- [SEV1] billing/stripe_client.py:65 — `_STRIPE.checkout.sessions.create(params)`
  is the synchronous Stripe SDK (urllib3 under the hood) called directly inside
  the `async def` route `routers/billing.py:84 checkout` via `create_checkout_session`.
  Each checkout HTTP round-trip (typical Stripe p95 300–800ms) blocks the FastAPI
  event loop, capping concurrent checkouts to one per worker process and dragging
  unrelated request latency. Confirmed by reading: `routers/billing.py:94` awaits
  nothing around `create_checkout_session(...)`; the helper is a plain `def`;
  `stripe.StripeClient` is the sync client (not `stripe.AsyncStripeClient`).
  Industry-standard check (2026): sync-SDK-in-async-route remains the #1
  documented FastAPI event-loop pitfall — Anthropic SDK Issue 84 research
  confirms `await asyncio.to_thread(...)` is the standard mitigation when the
  upstream SDK is sync-only; the Stripe Python SDK ≥7 now also exposes an
  async surface (`stripe.AsyncStripeClient`) as the canonical fix. Rubric 2.
  | fix: prefer (a) swap `_STRIPE = stripe.StripeClient(...)` for
  `_STRIPE = stripe.AsyncStripeClient(...)` and `await` the call (single-line
  swap, idiomatic); or (b) wrap the existing sync client in
  `url = await asyncio.to_thread(_STRIPE.checkout.sessions.create, params)`
  and make `create_checkout_session` `async`. Add a regression test
  asserting two concurrent `/checkout` calls under a 500ms synthetic Stripe
  latency complete in <800ms wall-clock (i.e. don't serialize).

- [SEV2] billing/refund.py:57-71 — refund idempotency is still a read-then-write
  on `pack_id`: `select(MinutePack.id).where(MinutePack.pack_id == pack_id)`
  (line 58) followed by `grant_minutes(... pack_id=pack_id ...)` (line 64).
  `models.py:498-516` confirms `MinutePack` has NO UNIQUE constraint on
  `pack_id` or `(reason, pack_id)` — only `stripe_session_id` is unique
  (`models.py:510-512`). Two concurrent `on_failure` callbacks for the same
  `video_id` would both see "no existing refund" and both insert a refund
  row + double-credit the balance. The module's own docstring acknowledges
  this (`billing/refund.py:11-15`). Carry-forward SEV2 confirmed open after
  Wave 2. Rubric 1/3. | fix: add an Alembic migration creating
  `CREATE UNIQUE INDEX minute_packs_refund_key ON minute_packs (pack_id)
  WHERE reason = 'refund'` (partial index — required because non-refund
  rows reuse `pack_id` literals like `"trial"`/`"starter"`/`"grant"`, see
  `routers/auth.py:98` and `billing/ledger.py:45`). Then drop the
  read-then-write guard at lines 57-62 and let `grant_minutes`'s existing
  `IntegrityError` catch in `billing/ledger.py:89-98` no-op the duplicate
  (note: that catch currently only swallows when `stripe_session_id is not
  None` — extend it to also swallow on the refund pack_id UNIQUE, or have
  `refund_for_video` catch `IntegrityError` itself). Update the docstring
  ("not reachable in the current pipeline") — Celery `task_acks_late=True`
  + worker preemption CAN produce concurrent terminal failures for the same
  task id; the no-UNIQUE claim is wrong even on a single chain.

- [SEV2] billing/stripe_client.py:20 — `_STRIPE = stripe.StripeClient(
  settings.STRIPE_SECRET_KEY)` is constructed at import time with whatever
  `STRIPE_SECRET_KEY` is in the environment, even in dev where it defaults to
  `""` (`config.py:130`). `Settings._require_prod_secrets` (`config.py:136-148`)
  only fails-fast in `ENV == "production"`, so a misconfigured staging/preview
  deploy (e.g. `ENV=staging` with no key) silently binds an empty client; the
  failure surfaces only at first `/checkout`. The router's 503 gate
  (`routers/billing.py:89-90`) papers over it for that route but `_STRIPE` is
  now a global landmine — `construct_webhook_event` does not have the same
  guard. Rubric 8. | fix: lazy-init via `functools.lru_cache` returning the
  client only when `settings.STRIPE_SECRET_KEY` is set, and raise a clear
  `RuntimeError("Stripe not configured")` from the accessor; or extend
  `_require_prod_secrets` to also fail for `ENV == "staging"`.

- [SEV2] billing/refund.py:48-72 — refund opens its own `AdminSessionLocal()`
  and explicitly commits at the end (line 72), but the inner `grant_minutes`
  call uses `session.begin_nested()` SAVEPOINT (`billing/ledger.py:71`). The
  early-return paths (`return 0` at lines 53 and 62) exit the `async with`
  block without explicitly committing or rolling back — currently harmless
  because only read queries occurred (no writes to roll back), but the
  intent is ambiguous and a future refactor that adds a write before the
  early-return would silently lose it. Rubric 1. | fix: wrap the body in
  `async with session.begin():` to make the outer transaction explicit and
  drop the trailing `await session.commit()` — documents the unit-of-work
  boundary and removes the early-return ambiguity.

- [SEV2] billing/refund.py:34-72 — refund triggers purely on Celery
  `on_failure` invocation (`worker/tasks.py:64-85`) without re-checking the
  video's terminal state (`video.render_status`). If `on_failure` were ever
  invoked on a still-completable task (broker replay during chain reschedule,
  duplicate retry-exhausted firing), the `MinuteDeduction` row exists and the
  refund proceeds, double-crediting against an eventually-successful run.
  The pack_id UNIQUE partial index above would catch a *duplicate refund*
  but not an *erroneous-first refund* against a video that ultimately
  succeeds. Rubric 1 (idempotency). | fix: gate refund on
  `select(Video.render_status).where(Video.id == video_id)` returning a
  terminal status (`failed`/`errored`) before calling `grant_minutes` —
  single extra SELECT inside the same session. Or document the trade
  explicitly in `docs/DECISIONS.md` if the chain is provably one-shot per
  video. (needs-runtime-confirmation that the Celery chain truly cannot
  fire `on_failure` on a succeeded task under broker replay.)

- [cleanup] billing/stripe_client.py:36 — `params: dict = {...}` missing
  generic type parameters. Rubric 6. | fix: annotate
  `params: dict[str, object] = {...}` (or a `TypedDict` if the structure
  is to be locked down).

- [cleanup] billing/stripe_client.py:65-67 — `session.url` is typed as
  `str | None` by the Stripe typestubs; `create_checkout_session` declares
  `-> str` (`stripe_client.py:29`) and returns `session.url` without a
  None check. Rubric 6/7. | fix: `assert session.url is not None,
  "Stripe Checkout session missing url"` before the return, or change
  the return type to `str | None` and surface the None to the router as
  a 502 in `routers/billing.py:94-103`.

- [cleanup] billing/ledger.py:35 / 182 / 204 — duplicated `HTTPException(
  status_code=402, detail="...Purchase a pack at /pricing...")` shape
  across `get_balance` 404, `check_positive_balance`, and
  `check_balance_for_minutes`/`deduct_for_video`. Rubric 6 (DRY). | fix:
  extract `_raise_insufficient(detail: str) -> NoReturn` to centralize the
  402 + the "/pricing" copy so future copy edits don't drift across three
  call sites.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | 3 findings (refund pack_id race, implicit outer tx, terminal-state gate) |
| 2 Concurrency & scale | 1 SEV1 (sync Stripe SDK in async route — still open) |
| 3 Security & compliance | ok — no token handling in this slice; per-tenant isolation OK (`MinuteDeduction.video_id UNIQUE`, deduction-tied `creator_id` used downstream); refund's `AdminSessionLocal` is intentional + documented (Hotfix B closes the RLS no-op SEV1); no PII in any log line (verified by reading every `logger.*` call in the slice) |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | n/a (no LLM calls in slice) |
| 6 Cleanliness & typing | 3 cleanups (dict generics, return-type narrowing, 402 DRY) |
| 7 Error handling / API | n/a (router lives in `routers/billing.py`, not this slice) |
| 8 Config & paths | 1 SEV2 (module-level Stripe client + non-prod fail-fast gap) |

## Module verdict
NEEDS-WORK — Wave 2 did not touch this module; the post-Wave-1 carry-forward
register is intact. The sync-Stripe-in-async route (SEV1) and the refund
double-credit race (SEV2) both remain open and must close before launch.
