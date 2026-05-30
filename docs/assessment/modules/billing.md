# billing — assessed 2026-05-29

Slice: `billing/ledger.py`, `billing/packs.py`, `billing/stripe_client.py`,
`billing/__init__.py`. Money-correctness paths traced through their callers
(`routers/billing.py`, `routers/auth.py`, `worker/tasks.py`), `models.py`
(MinutePack / MinuteDeduction), and `config.py` for the prod fail-fast.

## Findings

- [SEV2] billing/ledger.py:89-92 — non-keyed grants (`stripe_session_id=None`:
  free trial via routers/auth.py:80, manual grants) share the same broad
  `except IntegrityError` no-op path as keyed grants. With no UNIQUE key on the
  non-keyed path, the only IntegrityError reachable is a *real* fault (e.g. the
  `creators` FK violation if the creator row is gone). It is swallowed, the
  balance is silently never credited, and it is logged as
  `"billing grant race skip session=None"` — a misleading message that hides a
  genuine failure | fix: scope the duplicate-suppression to keyed grants only —
  `except IntegrityError: if stripe_session_id is None: raise` (or split into a
  keyed vs. one-shot helper). Add a test that a non-keyed grant against a missing
  creator surfaces the error rather than no-op'ing.

- [cleanup] billing/ledger.py:91 — the race-skip log interpolates
  `stripe_session_id` which is `None` on every non-keyed call, producing
  `session=None` noise; resolves naturally once the finding above scopes the
  catch to keyed grants only.

- [cleanup] billing/stripe_client.py:36 — `params: dict` is an unparameterized
  `dict` annotation; `dict[str, object]` (or a TypedDict) is more precise for the
  mypy gate | fix: annotate `params: dict[str, object]`.

## Verification notes (no defect found — traced to confirm)

- Webhook signature is verified: routers/billing.py:116 calls
  `construct_webhook_event`, which is `stripe.Webhook.construct_event(..., STRIPE_WEBHOOK_SECRET)`
  (stripe_client.py:71); a bad signature raises `SignatureVerificationError` →
  400 (routers/billing.py:117-119). No fulfillment on unverified payloads.
- Double-grant on webhook redelivery is prevented twice over: router-level
  pre-check (routers/billing.py:144-148) and, against concurrent redelivery, the
  `UNIQUE(stripe_session_id)` constraint (models.py:452-454) enforced inside the
  SAVEPOINT with an IntegrityError no-op (ledger.py:62-92). Concurrency is
  covered by tests/test_billing_grant_idempotency_integration.py:50
  (`asyncio.gather` of two deliveries → credited once).
- Double-charge on Celery at-least-once redelivery is prevented by
  `UNIQUE(video_id)` on MinuteDeduction (models.py:473-477) + fast-path check +
  SAVEPOINT/IntegrityError guard (ledger.py:118-155). Deduct caller is
  worker/tasks.py:258.
- Balance mutations are atomic in-DB: grant uses
  `minutes_balance = minutes_balance + minutes` (ledger.py:87); deduct uses a
  guarded `... WHERE minutes_balance >= minutes ... RETURNING` (ledger.py:138-142)
  so an insufficient balance returns no row → 402 with SAVEPOINT rollback (clean
  ledger). No read-modify-write race.
- The 402 HTTPException (ledger.py:146) is raised inside the
  `try/except IntegrityError`; HTTPException is not an IntegrityError, so it
  propagates correctly rather than being swallowed.
- Stripe prod fail-fast (Issue 75c) is enforced: config.py:85-98 raises in
  `ENV == "production"` when STRIPE_SECRET_KEY / STRIPE_WEBHOOK_SECRET are unset.
  All three Stripe keys are documented in .env.example:73-75. Checkout also
  guards on missing key with 503 (routers/billing.py:89-90).
- Per-creator isolation: ledger queries filter by `Creator.id == creator_id`
  (ledger.py:33, 86, 139) and the deduction/grant records carry `creator_id`.
  MinutePack lookup is keyed by the globally-unique `stripe_session_id`
  (Stripe-issued, unguessable) so no cross-tenant lookup risk.
- No secret logged: grep of `logger.*` in billing/ + routers/billing.py shows
  only pack_id / creator_id / minutes / session_id — no Stripe secret, no PII,
  no token. Webhook signature failures log the exception message, not the secret
  (routers/billing.py:118).
- Stripe client is a module-level singleton (stripe_client.py:20) with
  `max_network_retries = 3`; not reconstructed per request.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — sessions via caller context managers; Stripe client is a module singleton; savepoints scoped with `async with` |
| 2 Concurrency & scale | ok — UNIQUE-key + SAVEPOINT idempotency proven by concurrent gather test; atomic balance UPDATEs; bounded single-row queries |
| 3 Security & compliance | ok — webhook signature verified; per-creator isolation present; no secret/PII logged; no virality promise; parameterized SQLAlchemy |
| 4 Clip-quality | n/a (billing, not a clip module) |
| 5 Anthropic SDK | n/a (no LLM calls) |
| 6 Cleanliness & typing | 2 cleanup — `session=None` log noise; loose `dict` annotation |
| 7 Error handling / API | 1 SEV2 — non-keyed grant swallows real IntegrityErrors as a no-op |
| 8 Config & paths | ok — prod fail-fast on Stripe secrets; all keys in .env.example; no filesystem paths in module |

## Module verdict
NEEDS-WORK — money correctness, idempotency, signature verification, and prod
fail-fast are all sound and test-backed; the one real defect is the over-broad
`except IntegrityError` on the non-keyed grant path, which can silently drop a
legitimate trial/manual grant and mislabel a genuine fault as a race.
