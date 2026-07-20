# chat — assessed 2026-07-20 (post-fix)

Slice: `chat/intake.py`, `chat/prompt.py`, `chat/runner.py`, `chat/tools.py`, `chat/__init__.py`,
plus the `chat_respond` entry in `worker/tasks.py` (session/RLS posture only).
Re-assessment after the two fix waves merged since this morning (`git diff ca3305c..e92b93a`).
Diff scrutiny: only `chat/runner.py` changed (+31/-3, commit 9bd8105 "chat billing by
configured model", Issue 361 llm-sdk batch). Every prior finding re-verified against HEAD;
Opus price constants re-verified against the /claude-api skill model reference (read
2026-07-20).

## Resolved since this morning's assessment

- **[was SEV2] billing rate/label hardcoded to Sonnet — FIXED.** `chat/runner.py:58-79`
  adds `_chat_model_rates() -> tuple[float, float, str]` resolving `(rate_in, rate_out,
  tier_label)` from `settings.ANTHROPIC_MODEL_CHAT` by family substring
  (haiku → sonnet → opus); the billing block now uses `rate_in`/`rate_out` in
  `_estimate_cost_usd` (runner.py:204-212) and `tier_label` in `record_llm_cost`
  (runner.py:227) instead of the hardcoded Sonnet constants.
  - **Never-under-bills invariant VERIFIED.** Unknown model families fall back to the Opus
    rates with label `"other"` plus a logged warning (runner.py:76-79). Opus IS the highest
    tier in the config price book — in: 5.0 > 3.0 (Sonnet) > 1.0 (Haiku); out: 25.0 > 15.0 >
    5.0 — so a misconfigured `ANTHROPIC_MODEL_CHAT` can only over-bill against the spend
    guard, never under-bill. Family-match ordering is safe: no real Anthropic model id
    contains two family names.
  - **Price constants VERIFIED against the /claude-api skill** (models table, cached
    2026-05-26, read 2026-07-20): Opus 4.8 (`claude-opus-4-8`) is $5.00/MTok in,
    $25.00/MTok out — matches `COST_PER_MTOK_IN_OPUS = 5.0` / `COST_PER_MTOK_OUT_OPUS =
    25.0` (config.py:142-143). Sonnet 4.6 $3/$15 and Haiku 4.5 $1/$5 also still match the
    existing constants. Both new constants are documented in `.env.example:32-33` (rubric 8
    satisfied).
  - **Tier-label consistency with `billing.ledger._model_tier` VERIFIED** — see billing.md;
    the label vocabulary (`haiku-tier`/`sonnet-tier`/`opus-tier`/`other`) matches, and
    `_model_tier` gained the corresponding opus branch in the same wave.
  - **Tests:** `tests/test_chat.py:227-259` pins all three family mappings AND the
    unknown-family → Opus-rates/`"other"` fallback.

## Tenant-isolation verdict (unchanged, re-confirmed)

No isolation-relevant line changed in the diff. The 2026-07-20-morning verdict stands:
creator id enters as the authenticated session owner, all 8 tool executors filter on the
injected `creator_id`, and FORCE'd RLS (migrations 0010/0026/0040/0044 via
`db.tenant_session`) backstops the app-layer filters, pinned by
`tests/test_rls_isolation_integration.py`.

## Findings

- [cleanup] worker/tasks.py:4983-4985 + chat/runner.py:201-228 (carry-forward) — on the
  empty-reply path (`if not final_text: return`) the worker returns before
  `session.commit()`, rolling back the `increment_usage` ledger row written inside
  `run_chat_turn` — but `record_spend` (Redis) and `record_llm_cost` (Prometheus) already
  fired, so ledger and spend guard diverge for that turn (tokens spent, ledger not
  charged). Rare and in the creator's favor | fix: commit the usage write before the early
  return (or move the empty-reply check ahead of the billing block).
- [cleanup] chat/runner.py:163 (carry-forward) — `warn_if_truncated` fires here AND inside
  `stream_message` (worker/anthropic_stream.py:184) for the same round → truncated reply
  logs the WARNING twice | fix: branch on `stop_reason == "max_tokens"` directly in runner
  for the flag; let the stream helper own the log line.
- [cleanup] chat/tools.py:45 (TOOLS) & chat/intake.py:60 (PROPOSE_PROFILE_TOOL)
  (carry-forward) — schemas use `additionalProperties: false` but not `"strict": true`;
  strict tool use is supported on Sonnet 4.6 and is cheap insurance on the
  injection-facing `propose_profile` and `clip_id` inputs | fix: add `"strict": true` to
  both custom tool definitions (keep validators as defense-in-depth).
- [cleanup] chat/runner.py:109 (carry-forward) — `client =
  _ANTHROPIC.with_options(timeout=120.0)` re-wraps the singleton already built with
  `httpx.Timeout(120.0, connect=10.0)` (runner.py:46-50); the flat float loses the
  granular 10s connect timeout | fix: drop `with_options` and pass `_ANTHROPIC` directly.

## Verified-correct (no action) — unchanged from the morning pass

Prompt caching (one ephemeral breakpoint on last stable system block, tools→system order);
AsyncAnthropic module singletons awaited on the loop; `is_error: true` semantics +
`execute_tool` never raises into the loop; runaway guards (`CHAT_MAX_TOOL_ITERATIONS`
forced-text final round, `_MAX_PAUSE_ROUNDS = 5`, `MAX_INTAKE_TURNS = 12`,
`CHAT_MAX_TOKENS`); truncation surfaced honestly via `usage["truncated"]`; honesty
constraint + `UNTRUSTED_CONTENT_POLICY` in both system prompts; bounded reads with
server-side clamps; error paths log exception types only (the new `_chat_model_rates`
warning logs only the model id — no PII/token); token usage logged after every call.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — singletons; DB session owned/closed by `db.tenant_session` in the worker |
| 2 Concurrency & scale | ok — fully async SDK path, sequential tool exec, bounded fetches |
| 3 Security & compliance | ok — isolation + RLS backstop unchanged and re-confirmed; no new logging surface |
| 4 Clip-quality | n/a — reads clips/scores, does not compute them |
| 5 Anthropic SDK | prior SEV2 (cost-rate coupling) FIXED + tested; 3 cleanups remain (double warn, strict, with_options); caching/pause_turn/is_error verified correct |
| 6 Cleanliness & typing | ok — `_chat_model_rates` fully typed; 1 cleanup counted above (with_options) |
| 7 Error handling / API | n/a — not a router |
| 8 Config & paths | ok — new OPUS price constants in config.py AND `.env.example:32-33` with source citation |

## Module verdict

clean — the one open SEV2 (chat billed at hardcoded Sonnet rates regardless of the
configured model) is verifiably fixed with a tested, never-under-bills Opus fallback and
price-book constants confirmed against the /claude-api reference ($5/$25 per MTok);
only four small carry-forward cleanups remain.
