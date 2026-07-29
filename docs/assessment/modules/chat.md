# chat — assessed 2026-07-29 (ready-pass delta)

Slice: `chat/intake.py`, `chat/prompt.py`, `chat/runner.py`, `chat/tools.py`,
`chat/__init__.py`, plus the `chat_respond` entry in `worker/tasks.py` (session/RLS
posture only). Delta re-assessment on `w3/ready-pass-closeout` (deployed prod content)
after the 2026-07-29 user-approved money-path ready-pass. Diff scrutiny vs the
2026-07-20-clean baseline (`git diff e92b93a..HEAD -- chat/`): `intake.py` (+18, commit
452a700 — intake turns now billed) and `runner.py` (+39/−10, commit 57f03a3 —
session-factory posture). Money-path rigor: billing 1:1-ness, spend-guard integration,
and RLS of every factory-opened session re-traced by reading.

## 2026-07-29 ready-pass delta

**1. `run_chat_turn` now takes a tenant-session FACTORY (runner.py:84-104) — verified
correct.**
- No DB session is held across any streamed LLM round-trip: the phase-1 read session in
  `_chat_respond_async` (worker/tasks.py:5089-5123) closes before the loop; each tool
  execution opens a short-lived session (`async with session_factory() as tool_session`,
  runner.py:188-191); the usage-ledger write opens its own and commits explicitly
  (runner.py:230-239).
- **RLS correctness:** the factory is `functools.partial(db.tenant_session, cid)`
  (worker/tasks.py:5131); `db.tenant_session` is an `@asynccontextmanager` that stamps
  `session.info["creator_id"]` BEFORE the first statement, so the `after_begin` listener
  emits the `app.creator_id` GUC on every transaction of every factory-opened session —
  tool queries and the `usage` upsert (table RLS-covered by migrations 0010/0045) are both
  policy-gated. Structurally impossible to open an unstamped session through this path.
- **No missed caller:** repo grep finds exactly one production caller
  (worker/tasks.py:5130), updated; all `tests/test_chat.py` call sites pass a factory.
- **Double-billing:** `chat_respond` stays `max_retries=0`, so the now-inside-the-turn
  ledger commit runs at most once per user message. Side effect verified: the 7-20
  carry-forward cleanup (empty-reply early return at worker/tasks.py:5134 rolling back the
  usage row while Redis spend counters kept it) is **RESOLVED** — the ledger session
  commits inside `run_chat_turn` before the worker's early return, so ledger and spend
  guard now agree on empty-reply turns.

**2. `run_intake_turn` now writes the cost ledger (intake.py:289-301) — previously
entirely unbilled and invisible to the Issue-290 spend guard.** The write goes through
`record_llm_usage` (the choke point feeding ledger + spend counters + cost metric), sums
both attempts of the validation-correction loop, includes both cache tiers, and correctly
leaves `cache_write_multiplier` at the 1.25× default — the intake system block carries a
plain 5-min ephemeral marker (intake.py:178), not ttl:"1h" (same for Pro chat,
prompt.py:64, so runner's default-multiplier cost math is also correct). The runaway-guard
early return (intake.py:167-171) bails before any LLM call, so nothing unbilled escapes.
Pinned by `tests/test_identity_chat.py:132-162` and the repo-wide AST sweep in
`tests/test_usage_coverage.py` (which found this leak and now fails CI on the next one).
Two real gaps remain in HOW it bills — the findings below.

## Findings

- [SEV2] routers/creators.py:723-744 (cross-module fix; the intake feature is
  chat-owned) — `POST /creators/me/identity/chat` is now a BILLED LLM route but is the
  only one NOT stacked with `Depends(require_flag("llm_generation"))` +
  `Depends(require_budget)` (every other LLM route has both — clips/titles/thumbnails/
  chat/improvement/insights/analysis; grep verified). Intake now *records* spend into the
  guard but ignores its enforcement: a creator in cool-down or over the daily cap — or the
  globally tripped kill switch — can keep spending via intake turns. Blast radius bounded
  by the 40/hour limiter and small Sonnet turns, but the global breaker exists precisely
  to hard-stop all LLM spend | fix: add both dependencies to the `identity_chat` route
  decorator; regression test asserting 429/blocked when the flag is off or
  `creator_block_status` blocks.
- [SEV2] chat/intake.py:299-300 — intake bills at hardcoded
  `COST_PER_MTOK_IN_SONNET`/`OUT_SONNET` while the model is configurable
  (`settings.ANTHROPIC_MODEL_INTAKE`, config.py:120) — the exact defect class fixed for
  Pro chat in 9bd8105 (`_chat_model_rates`, runner.py:60-81). An operator pointing intake
  at an opus-family model silently under-bills ~40% against the spend guard. Default is
  sonnet, so dormant today | fix: extract `_chat_model_rates` into a shared
  `model_rates(model: str)` helper (natural home: billing/ledger.py beside `_model_tier`)
  and call it with `ANTHROPIC_MODEL_INTAKE`; keeps the never-under-bill Opus fallback.
- [cleanup] chat/runner.py:217-249 — the ledger write, `record_llm_cost`, and
  `record_spend` share ONE try block, ledger first: a DB failure on the ledger session
  skips the spend-guard counters for the turn. `record_llm_usage` (ledger.py:214-233)
  deliberately separates these so DB loss can't blind the caps | fix: mirror that — run
  metric + `record_spend` in their own try before/independent of the ledger session.
- [cleanup] chat/runner.py:139-156 — an LLM error mid-turn propagates past the billing
  block, so rounds already completed in that turn are never billed or spend-counted
  (pre-existing posture, in the creator's favor, errored turns only) | fix: move the
  billing block into a `finally` (bill whatever `total` accumulated).
- [cleanup] chat/runner.py:172 (carry-forward) — `warn_if_truncated` fires here AND inside
  `stream_message` for the same round → duplicate WARNING | fix: branch on
  `stop_reason == "max_tokens"` in runner; let the stream helper own the log line.
- [cleanup] chat/tools.py:45 (TOOLS) & chat/intake.py:60 (PROPOSE_PROFILE_TOOL)
  (carry-forward) — `additionalProperties: false` but no `"strict": true`; cheap insurance
  on the injection-facing inputs | fix: add `"strict": true` to both tool definitions.
- [cleanup] chat/runner.py:118 (carry-forward) — `_ANTHROPIC.with_options(timeout=120.0)`
  re-wraps the singleton and flattens the granular `connect=10.0` timeout
  (runner.py:48-52) | fix: pass `_ANTHROPIC` directly.

## Tenant-isolation verdict (re-confirmed under the new factory posture)

Strengthened, not weakened: creator id still enters as the authenticated session owner,
all 8 tool executors filter on the injected `creator_id`, and every session the loop now
opens is RLS-stamped by construction (`tenant_session` requires the id as an argument).
FORCE'd RLS backstop (migrations 0010/0026/0040/0044/0045) unchanged, pinned by
`tests/test_rls_isolation_integration.py`.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — improved: no pooled connection held across LLM round-trips; factory sessions short-lived, ledger commit explicit |
| 2 Concurrency & scale | ok — connection-pool hold time across multi-round turns eliminated (the point of the W2 change); bounded loops unchanged |
| 3 Security & compliance | 1 SEV2 — newly-billed intake route ungated by spend-guard flag/budget deps (routers/creators.py); isolation itself intact |
| 4 Clip-quality | n/a — reads clips/scores, does not compute them |
| 5 Anthropic SDK | ok — caching/pause_turn/is_error posture unchanged; 1 SEV2 (intake rate/model coupling) + 3 carry-forward cleanups |
| 6 Cleanliness & typing | ok — factory signature fully typed (`Callable[[], AbstractAsyncContextManager[AsyncSession]]`) |
| 7 Error handling / API | n/a — not a router; billing block best-effort, never breaks the turn |
| 8 Config & paths | ok — no new config introduced by the delta |

## Module verdict

NEEDS-WORK — the session-factory refactor is verifiably correct (no session across LLM
calls, every factory session RLS-stamped, sole caller updated, empty-reply ledger/spend
divergence resolved) and intake is finally billed through the spend-guard choke point;
but the newly-billed intake path shipped with two SEV2 gaps: the route lacks the
`require_flag`+`require_budget` gates every other LLM route stacks, and it bills at
hardcoded Sonnet rates despite a configurable `ANTHROPIC_MODEL_INTAKE` — both small,
contained fixes.
