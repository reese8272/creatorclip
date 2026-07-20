# billing — assessed 2026-07-20 (post-fix)

Slice: `billing/ledger.py`, `billing/packs.py`, `billing/refund.py`,
`billing/spend_guard.py`, `billing/stripe_client.py`, `billing/__init__.py` (empty).
Re-assessment after the two fix waves merged since this morning
(`git diff ca3305c..e92b93a`). Diff scrutiny: `spend_guard.py` (+17/-8, commit 2279720
spend-latch fix) and `ledger.py` (+2, opus tier branch, commit 9bd8105). Every prior
finding re-verified against HEAD.

## Resolved since this morning's assessment

- **[was SEV2] global trip-latch set before the kill-switch flip — FIXED.**
  `billing/spend_guard.py:337-355`: the `_flip_llm_flag` + `_emit_spend_event` pair is now
  wrapped in `try/except`; on any exception the latch is deleted (`await
  r.delete(_TRIP_LATCH_KEY)`) before re-raising, so the next `record_spend` call
  re-attempts the flip instead of the breach going unenforced for the full
  `SPEND_COOLDOWN_TTL_S` (3600s) window. Ordering verified correct:
  - The `raise` propagates to `record_spend`'s catch-all, which fails open without
    surfacing to the caller — the documented posture, and exactly what the regression test
    asserts (no raise, latch released, retry re-acquires and flips:
    `tests/test_spend_guard.py:179-191`
    `test_failed_flag_flip_releases_latch_so_next_call_retries`).
  - Releasing the latch on `_emit_spend_event` failure (after a successful flip) can cause
    a second flip attempt on the next call — harmless because `set_flag` is an idempotent
    upsert, and the code comment says so.
  - Residual edge (accepted, not a finding): if `r.delete` itself fails, Redis is down —
    in which case the spend counters themselves fail open, so the stale latch is moot.

## New in the diff — verified correct

- `billing/ledger.py:167-168` — `_model_tier` gained the `opus-tier` branch for
  `COST_PER_MTOK_IN_OPUS`. **Consistency with `chat/runner._chat_model_rates`
  verified:** same tier vocabulary (`haiku-tier`/`sonnet-tier`/`opus-tier`/`other`), and
  no rate collision — the three input rates compared are distinct (3.0 / 1.0 / 5.0; the
  Opus input rate 5.0 equals only the Haiku *output* rate, which `_model_tier` never
  compares). Opus list price ($5 in / $25 out per MTok, `claude-opus-4-8`) confirmed
  against the /claude-api skill model reference (read 2026-07-20); constants documented in
  `.env.example:32-33`.

## Findings (all carry-forward cleanups)

- [cleanup] billing/ledger.py:50-54 — `send_notification.delay(...)` inside the
  `after_commit` listener is sync Redis/broker I/O executed on the event-loop thread
  during `await session.commit()`. Bounded today because `deduct_for_video` is only called
  from the worker's dedicated loop, not the FastAPI request loop | fix (consistency /
  future router callers): offload via
  `asyncio.get_running_loop().run_in_executor(None, lambda: send_notification.delay(...))`
  and hoist the import out of the per-pair loop.
- [cleanup] .env.example — `COST_CACHE_WRITE_MULTIPLIER` (consumed at
  billing/ledger.py:147) is still absent, though sibling `COST_CACHE_READ_MULTIPLIER` is
  documented. Safe default exists (config.py = 1.25) so not fail-fast-critical | fix: add
  `COST_CACHE_WRITE_MULTIPLIER=1.25  # cache-write multiplier (1.25× base input rate,
  5-min TTL; scoring passes 2.0 for 1h TTL)`.
- [cleanup] billing/ledger.py:174 — `record_llm_usage(usage: dict, ...)` still takes a
  bare unparameterized `dict` on the cost-accounting path; keys are fixed | fix:
  `dict[str, int]` or a small `TypedDict`.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — sessions via context manager; `_STRIPE`/Redis singletons; deduct/grant/refund idempotent (UNIQUE backstops + SAVEPOINT); balance-low notify transactional-outbox-correct |
| 2 Concurrency & scale | prior SEV2 (latch-before-flip stall window) FIXED + tested; 1 cleanup (`.delay` in listener, worker-loop-only today); Stripe retries active; spend counters one atomic Lua + mget |
| 3 Security & compliance | ok — tenant-scoped idempotency key; webhook signature verify; every query creator-/video-scoped; Redis spend keys embed creator_id; no secret/PII in logs; no virality promise |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | n/a — module prices token-usage dicts, makes no LLM call; new opus tier priced per the /claude-api price book |
| 6 Cleanliness & typing | 2 cleanups (bare `usage: dict`; listener `.delay`). Otherwise typed, no TODO/print/dead code |
| 7 Error handling / API | n/a (routers own the surface); fail-open posture on Redis errors documented and warn-once |
| 8 Config & paths | 1 cleanup (`.env.example` COST_CACHE_WRITE_MULTIPLIER gap); new OPUS constants ARE in `.env.example` |

## Module verdict

clean — the morning's SEV2 (trip-latch set before a possibly-failing kill-switch flip,
silencing the breaker for the 1h TTL) is verifiably fixed with the exact
release-on-failure ordering recommended, plus a regression test; the new opus tier branch
is rate-consistent with chat's mapping. Three low-risk carry-forward cleanups remain. No
BLOCKER, no cross-tenant leak.
