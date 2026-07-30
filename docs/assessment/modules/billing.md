# billing — assessed 2026-07-29 (ready-pass delta)

Slice: `billing/ledger.py`, `billing/packs.py`, `billing/refund.py`,
`billing/spend_guard.py`, `billing/stripe_client.py`, `billing/__init__.py` (empty).
Delta re-assessment on `w3/ready-pass-closeout` (deployed prod content) after the
2026-07-29 user-approved money-path ready-pass. Diff scrutiny vs the 2026-07-20-clean
baseline (`git diff e92b93a..HEAD -- billing/`): only `ledger.py` changed (+9, commit
452a700 w2/billing-audit). Money-path rigor applied: idempotency under retries, spend-guard
choke-point integrity, and the 1h-TTL multiplier math all re-traced by reading.

## 2026-07-29 ready-pass delta

**What changed:** `record_llm_usage` gained a keyword-only
`cache_write_multiplier: float | None = None` (ledger.py:177-178) threaded verbatim into
`_estimate_cost_usd` (ledger.py:212). `None` falls through to
`settings.COST_CACHE_WRITE_MULTIPLIER` (1.25×, the 5-min-TTL default); callers pass `2.0`
for ttl:"1h" cache writes.

**Verified correct (by reading, not assuming):**

- **Backward compatibility / missed callers.** The new param is keyword-only with a `None`
  default — every pre-existing positional caller is unaffected. Repo grep confirms the
  callers that DO thread it gate on real evidence: `routers/clips.py:1297,1397,1509` and
  `worker/tasks.py:4474,4698` pass `2.0 if usage.get("cache_1h") else None`, where
  `cache_1h` is produced by `knowledge.util.has_1h_cache_marker(system)` — computed from
  the ACTUAL system blocks sent (marker attached only when the floor-gated
  `dna_system_block` prefix cleared the 1024-token cache floor), so the 2.0 premium is
  charged exactly when a 1h write was requested, never when the marker was omitted.
  `clip_engine/scoring.py:389` gates on the equivalent `prefix_clears_floor`. Pinned by
  `tests/test_usage_ledger.py:154-171`, `tests/test_thumbnails.py:575,608`,
  `tests/test_knowledge_util.py`, `tests/test_scoring.py:397-446`.
- **Math direction is safe.** `_estimate_cost_usd` prices ALL `cache_creation_tokens` at
  the single multiplier; a hypothetical request mixing a 1h and a 5-min marker would bill
  the 5-min share at 2.0× — over-estimates against the spend guard, never under-bills.
- **Double-billing / idempotency.** `record_llm_usage` remains 1:1 with actual LLM spend:
  request-path callers bill once per completed inference; the worker helpers bill after
  the call inside the task body; `chat_respond` is `max_retries=0` so no Celery re-bill.
  `increment_usage` is intentionally additive (upsert accumulator), and the money-credit /
  deduction idempotency (UNIQUE `stripe_session_id` / `video_id` + SAVEPOINT) is untouched
  by this diff.
- **Choke-point integrity.** Spend guard (`record_spend`) and the cost metric still run in
  their own try before the ledger write, so a DB failure cannot blind the Issue-290 caps;
  the new multiplier flows into the SAME `cost` all three rails see (ledger.py:205-224).
- **New billed callers** (`chat/intake.py` run_intake_turn, `routers/thumbnails.py`
  patterns path) enter through this unchanged choke point, and the repo-wide AST sweep in
  `tests/test_usage_coverage.py` (`_ANTHROPIC_CALL_SITES` + discovery visitor) now fails
  CI on any future Anthropic call site without mapped billing evidence — a strong
  structural guard for the unbilled-LLM leak class.

## Findings (all carry-forward cleanups; no new defects in the delta)

- [cleanup] billing/ledger.py:50-54 — `send_notification.delay(...)` inside the
  `after_commit` listener is sync broker I/O on the event-loop thread during
  `await session.commit()`. Bounded today (worker-loop-only caller) | fix: offload via
  `asyncio.get_running_loop().run_in_executor(None, lambda: send_notification.delay(...))`.
- [cleanup] .env.example — `COST_CACHE_WRITE_MULTIPLIER` (consumed at
  billing/ledger.py:147) is still undocumented, though sibling
  `COST_CACHE_READ_MULTIPLIER` is (.env.example:35). Slightly more load-bearing now that
  five features thread the 1h override against this default | fix: add
  `COST_CACHE_WRITE_MULTIPLIER=1.25  # 5-min-TTL cache-write multiplier; 1h-TTL callers
  pass 2.0 explicitly`.
- [cleanup] billing/ledger.py:174 — `record_llm_usage(usage: dict, ...)` still takes a
  bare unparameterized `dict` on the cost-accounting path; keys are fixed | fix:
  `dict[str, int]` or a small `TypedDict` (now also carrying the `cache_1h` bool flag).

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — unchanged; sessions via context manager, singletons, UNIQUE+SAVEPOINT idempotency intact |
| 2 Concurrency & scale | ok — 1 carry-forward cleanup (`.delay` in listener); spend counters still one atomic Lua + mget; latch-release fix (7-20) unregressed |
| 3 Security & compliance | ok — no new logging surface in the diff; tenant-scoped keys/queries unchanged; no virality promise |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | n/a — module prices usage dicts; 1h-write 2.0× premium matches the Anthropic pricing model already cited in config.py:152 |
| 6 Cleanliness & typing | 2 carry-forward cleanups (bare `usage: dict`; listener `.delay`); new param typed and documented |
| 7 Error handling / API | n/a (routers own the surface); best-effort/fail-open posture unchanged and documented |
| 8 Config & paths | 1 carry-forward cleanup (`.env.example` COST_CACHE_WRITE_MULTIPLIER gap) |

## Module verdict

clean — the delta is a minimal, backward-compatible keyword-only multiplier correctly
threaded into the cost math; the 2.0-only-when-attached contract is verified end-to-end
(producer flag computed from the actual sent blocks, five call sites gate on it, tests pin
both directions), no double-billing path exists (additive usage upsert is 1:1 with real
spend; money mutations keep their UNIQUE idempotency keys), and the spend-guard choke
point sees the identical USD. Three low-risk carry-forward cleanups remain.
