# chat — assessed 2026-07-20

Slice: `chat/intake.py`, `chat/prompt.py`, `chat/runner.py`, `chat/tools.py`, `chat/__init__.py`,
plus the `chat_respond` entry in `worker/tasks.py` (session/RLS posture only).
Prior findings (2026-07-01) re-verified one by one; Anthropic SDK claims re-checked against the
claude-api skill docs (2026-07-20). Diff scrutiny: `git diff f70a857..HEAD -- chat/` touched
`intake.py`, `runner.py`, `tools.py` (Issues 82a AsyncAnthropic, 231 RLS, 352-Batch-I attribution).

## Resolved since 2026-07-01

- **[was SEV1] BYPASSRLS agentic loop — FIXED** (Issue 231, commit 1c5c720 + migrations 0040/0044).
  `worker/tasks.py:4795` now opens the chat turn with `db.tenant_session(cid)` — app role
  (no BYPASSRLS), `session.info["creator_id"]` stamped before the first statement so the
  `after_begin` listener (`db.py:154-184`) emits `SET LOCAL app.creator_id` on every transaction.
  RLS is FORCE'd on every chat-reachable table: parents in `alembic 0010` (`videos`, `clips`,
  `creator_dna`, `audience_activity`, `usage`), `chat_conversations` in `0026`, and the previously
  missing child tables (`video_metrics`, `retention_curves`, `transcripts`, `clip_outcomes`,
  `chat_messages`) in `0040` via parent-subquery policies with WITH CHECK. Regression coverage:
  `tests/test_rls_isolation_integration.py:548` (child-table cross-tenant read blocked, includes
  `chat_messages`) and `:602` (WITH CHECK on writes). RLS is now a real backstop behind the
  app-layer filters on the most injection-exposed surface.
- **[was SEV2] model attribution — FIXED** (Issue 352 Batch I, commit 2826b50).
  `runner.py:209` records `model=settings.ANTHROPIC_MODEL_CHAT`; `intake.py:277` records
  `model=settings.ANTHROPIC_MODEL_INTAKE` — no longer the generic `ANTHROPIC_MODEL`.
- **[was SEV2] silent truncation in runner — FIXED.** `runner.py:139-142` calls
  `warn_if_truncated` on the final round and surfaces `usage["truncated"] = 1` to the caller
  instead of passing a cut-off reply as complete. Explicit `pause_turn` handling was also added
  (`runner.py:95-134`, bounded by `_MAX_PAUSE_ROUNDS = 5`) matching the documented resume pattern.
- **[was cleanup] intake cache-token accounting — FIXED.** `intake.py:220-221` sums
  `cache_read_input_tokens` / `cache_creation_input_tokens` and forwards both to
  `record_llm_tokens` (`intake.py:276-283`), matching runner.

## Tenant-isolation verdict (the load-bearing check)

**Isolation holds and now has the RLS backstop.** Re-traced the chain: the creator id enters as
the authenticated session owner (never model input; no `creator_id` in any tool schema —
`tools.py:45-178`), the worker re-checks conversation ownership without leaking existence
(`worker/tasks.py:4799-4804`), and every one of the 8 executors filters by the injected
`creator_id` (`tools.py:214, 244, 249, 341, 372, 422, 478`); child-table reads resolve their
`video_id`/`clip_id` under a creator-scoped query first and are additionally gated by the 0040
RLS policies. Model-supplied `clip_id`/`video_query` are UUID-parsed / `ilike`'d and re-scoped.
A future executor that forgets `WHERE creator_id` is now caught by RLS (deny-by-default policies,
pinned by `tests/test_rls_isolation_integration.py`).

## Findings

- [SEV2] chat/runner.py:180-186,202 (carry-forward) — billing cost is still computed from the
  hardcoded `COST_PER_MTOK_IN_SONNET` / `COST_PER_MTOK_OUT_SONNET` rates and labelled
  `record_llm_cost("anthropic", "sonnet-tier", cost)`, decoupled from the configurable
  `ANTHROPIC_MODEL_CHAT` (config.py:118). Correct only while chat stays on Sonnet; pointing chat
  at Haiku/Opus silently charges the wrong rate into the real usage ledger and spend guard |
  fix: select `(in, out)` rates from a `{model_family: rates}` map keyed off
  `ANTHROPIC_MODEL_CHAT` (or assert at startup that the model is a Sonnet SKU), and derive the
  `record_llm_cost` tier label from the same lookup.
- [cleanup] worker/tasks.py:4831-4848 + chat/runner.py:188-203 — on the empty-reply path
  (`final_text` falsy, tasks.py:4833-4835) the worker returns before `session.commit()`, rolling
  back the `increment_usage` ledger row written inside `run_chat_turn` — but `record_spend`
  (Redis spend-guard counter) and `record_llm_cost` (Prometheus) already fired, so ledger and
  spend guard diverge for that turn (tokens spent, ledger not charged). Rare and in the
  creator's favor, but a reconciliation mismatch | fix: commit the usage write before the
  early return (or move the empty-reply check ahead of the billing block).
- [cleanup] chat/runner.py:139 — `warn_if_truncated` fires here AND inside `stream_message`
  (worker/anthropic_stream.py:184) for the same round, so a truncated reply logs the WARNING
  twice | fix: branch on `stop_reason == "max_tokens"` directly in runner for the flag and let
  the stream helper own the log line (warn_if_truncated is log-only — observability.py:315-333).
- [cleanup] chat/tools.py:45 (TOOLS) & chat/intake.py:60 (PROPOSE_PROFILE_TOOL)
  (carry-forward) — schemas use `additionalProperties: false` but not `"strict": true`. Strict
  tool use guarantees schema conformance and is supported on Sonnet 4.6 (verified via claude-api
  skill, 2026-07-20) — cheap insurance on the injection-facing `propose_profile` and `clip_id`
  inputs | fix: add `"strict": true` to both custom tool definitions (keep the validators as
  defense-in-depth).
- [cleanup] chat/runner.py:85 (carry-forward) — `client = _ANTHROPIC.with_options(timeout=120.0)`
  re-wraps the singleton already built with `httpx.Timeout(120.0, connect=10.0)` (runner.py:46-50),
  and the flat float actually *loses* the granular 10s connect timeout | fix: drop the
  `with_options` call and pass `_ANTHROPIC` directly.

## Verified-correct (no action)

- Prompt caching: one `cache_control: ephemeral` breakpoint on the last stable system block
  (prompt.py:60-66, intake.py:177-179); tools render before system so the tool schemas cache with
  it; per-creator channel title sits in a second uncached block. Matches current guidance
  (prefix-match, tools→system→messages, breakpoint on last system block).
- AsyncAnthropic migration (Issue 82a): both clients are module-level `AsyncAnthropic` singletons
  (runner.py:46, intake.py:46) awaited directly on the loop — the old `asyncio.to_thread` hop is
  gone; no blocking call inside async paths. Tools execute sequentially on one AsyncSession (no
  concurrent session use).
- `is_error: true` set exactly when an executor fails (runner.py:157-164, tools.py:540,545,
  intake.py:264); `execute_tool` never raises into the loop (tools.py:541-546). tool_result
  message shape correct (results-only user message, runner.py:165).
- Runaway guards: `CHAT_MAX_TOOL_ITERATIONS` with a forced no-tools final round (runner.py:87-90),
  `_MAX_PAUSE_ROUNDS = 5` on pause_turn resumes with a warning on exhaustion (runner.py:95-134),
  `MAX_INTAKE_TURNS = 12` (intake.py:54,167), output capped at `CHAT_MAX_TOKENS`.
- Honesty constraint verbatim in both system prompts (prompt.py:19-33, intake.py:110-111) plus
  `UNTRUSTED_CONTENT_POLICY` on both injection surfaces (prompt.py:27, intake.py:105); no
  virality promise anywhere in the module.
- Bounded reads everywhere: `_MAX_VIDEOS=25`, `_MAX_CLIPS=20`, channel averages LIMIT 50; model
  cannot exceed the caps (server-side clamp at tools.py:203-207, 361-365).
- Error paths log exception *types*, not token/PII content (runner.py:107-112, intake.py:208-212);
  full-prompt logging only via `vlog_*`, which is a no-op unless `verbose_logging_enabled`
  (verbose.py:45,86) with an explicit prod opt-in.
- Chat billing feeds the spend guard and per-creator monthly ledger via an atomic upsert in a
  savepoint (`billing/ledger.increment_usage`), with cache tiers priced separately.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — singletons; DB session owned/closed by `db.tenant_session` context manager in the worker; chat borrows it |
| 2 Concurrency & scale | ok — fully async SDK path (82a), sequential tool exec, all fetches bounded |
| 3 Security & compliance | ok — prior SEV1 resolved; RLS backstop active + tested; isolation re-traced on all 8 executors; honesty constraint verified |
| 4 Clip-quality | n/a — reads clips/scores, does not compute them |
| 5 Anthropic SDK | 1 SEV2 (cost-rate coupling) + 3 cleanup (double warn, strict, with_options); caching/pause_turn/is_error/tool_result verified correct |
| 6 Cleanliness & typing | ok — fully typed; 1 cleanup counted above (with_options) |
| 7 Error handling / API | n/a — not a router; HTTP surface owned by routers/chat.py & routers/creators.py |
| 8 Config & paths | ok — CHAT_* documented in .env.example:41-44; no filesystem paths |

## Module verdict

NEEDS-WORK — the 2026-07-01 SEV1 (BYPASSRLS agentic loop) and both attribution SEV2s are
verifiably fixed; what remains is one SEV2 (billing rate/label hardcoded to Sonnet while the
chat model is configurable) and four small cleanups.
