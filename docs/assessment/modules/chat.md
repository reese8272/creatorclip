# chat — assessed 2026-07-01

Slice: `chat/intake.py`, `chat/prompt.py`, `chat/runner.py`, `chat/tools.py`, `chat/__init__.py`.
Anthropic SDK claims verified against current official docs (platform.claude.com,
fetched 2026-07-01) — citations inline.

## Tenant-isolation verdict (the load-bearing check)

**Isolation holds today, but has NO RLS backstop — it rests entirely on app-layer
`WHERE creator_id` filters on the app's most prompt-injection-exposed surface.**

Traced the full chain:
- `routers/chat.py:103` enqueues the task with `str(creator.id)` taken from the
  authenticated `get_current_creator` dependency — the creator id is the session
  owner, never model input.
- `worker/tasks.py:4364` opens the turn on **`db.AdminSessionLocal()`** — the
  `creatorclip_migrate` role, which has **BYPASSRLS** (`db.py:22-26,148-149`), and
  it sets **no** `session.info["creator_id"]`, so the `after_begin` GUC listener
  (`db.py:139-168`) emits nothing. The RLS policies in `alembic 0010` therefore do
  **not** gate any query in the chat tool path.
- Every one of the 8 executors in `chat/tools.py` filters explicitly by the injected
  `creator_id`, and the model never supplies it (no `creator_id` in any tool schema):
  `_get_recent_videos` (:216), `_get_video_performance` (:245,:250), `_get_channel_averages`
  (:320), `_get_upload_timing` (:342), `_list_top_clips` (:374), `_get_clip_detail` (:423),
  `_suggest_clip_titles` (:481), and `_get_channel_dna` → `dna.profile.get_active`
  (`dna/profile.py:164-176`, filters `CreatorDna.creator_id == creator_id`). The
  child-table reads (`VideoMetrics`, `RetentionCurve`, `ClipOutcome`, `Transcript`) filter
  by a `video_id`/`clip_id` that was itself resolved under a `creator_id` filter, so they
  are transitively isolated. Model-supplied `clip_id`/`video_query` are validated
  (UUID parse / `ilike`) and re-scoped by `creator_id`, so the model cannot pivot to
  another creator's row.

The gap: this is the one surface where an adversary's free text (the chat message) is
fed to an LLM that then chooses DB tool calls. RLS is the app's belt-and-suspenders
everywhere else, and here it is switched off. Compounding it, `alembic 0010`'s
`_TENANT_TABLES` doesn't even include `video_metrics`, `retention_curves`,
`clip_outcomes`, `transcripts`, or the `chat_*` tables — so RLS would not backstop those
reads even if the app role were used. Any future tool added to `_EXECUTORS` that forgets
a `WHERE creator_id` is an immediate cross-tenant leak with nothing to catch it.

The **intake** path (`chat/intake.py`) touches no DB at all (pure LLM + `dna.identity`
validators) and only ever *proposes* a profile the creator later confirms — no isolation
concern there.

## Findings
- [SEV1] worker/tasks.py:4364 (+ chat/runner.py:53, chat/tools.py:529) — the Pro chat
  agentic loop runs under the BYPASSRLS `AdminSessionLocal` with no `creator_id` GUC, so
  RLS is **not** a second line of defense on the app's most injection-exposed surface;
  cross-tenant safety depends solely on every executor remembering `WHERE creator_id`
  (verified present today, but one forgotten filter in a future tool = silent leak) |
  fix: run the chat turn on the app-role `AsyncSessionLocal` with
  `session.info["creator_id"] = cid` set before the first query (same pattern as
  `db.get_session` + the auth dependency), so RLS gates every query as a backstop; keep
  the explicit filters. Then extend `alembic 0010._TENANT_TABLES` to cover the
  chat-reachable child tables (`video_metrics`, `retention_curves`, `clip_outcomes`,
  `transcripts`, `chat_conversations`, `chat_messages`) so the backstop is real. Add a
  regression test: with a chat session scoped to creator B and the GUC set, a tool call
  crafted to reach creator A's clip/video id returns empty. (needs-runtime-confirmation
  that all chat-touched tables get RLS policies + that ChatMessage/Conversation writes
  still succeed under the app role.)
- [SEV2] chat/runner.py:169-175 & chat/intake.py:272-277 — token usage is recorded with
  `model=settings.ANTHROPIC_MODEL` (the generic default) instead of the model actually
  invoked (`ANTHROPIC_MODEL_CHAT` / `ANTHROPIC_MODEL_INTAKE`). Correct **only by
  coincidence** today because all three equal `"claude-sonnet-4-6"` (config.py:100,117,118);
  the moment chat or intake is pointed at Opus/Haiku, the Prometheus `model` label and
  cost dashboards misattribute usage | fix: pass the model that was used —
  `model=settings.ANTHROPIC_MODEL_CHAT` / `...INTAKE`.
- [SEV2] chat/runner.py:148-156 — the billing-ledger cost is computed with
  `COST_PER_MTOK_IN_SONNET` / `...OUT_SONNET` hardcoded, decoupled from
  `ANTHROPIC_MODEL_CHAT`. This feeds real usage-based billing (`increment_usage`), so if
  the chat model ever changes to Haiku (cheaper) or Opus (dearer) the charge is wrong |
  fix: select the cost rate from the model in use (a small `{model: (in,out)}` map) or
  assert `ANTHROPIC_MODEL_CHAT` is a Sonnet SKU, so cost tracks the model.
- [SEV2] chat/runner.py:110-111 — the loop treats any `stop_reason != "tool_use"` as a
  finished answer; a `max_tokens` truncation (CHAT_MAX_TOKENS=1500, config.py:194) mid-answer
  is returned to the creator silently, and intake calls `warn_if_truncated` (intake.py:218)
  while runner does not, so the two paths are inconsistent | fix: call
  `warn_if_truncated(ANTHROPIC_MODEL_CHAT, message.stop_reason, task=task_id)` after each
  round in runner, matching intake. `pause_turn` is correctly **n/a** here — it only fires
  for server tools (web_search/web_fetch/code_execution) and chat uses only client-side
  custom tools (Anthropic handling-stop-reasons, fetched 2026-07-01).
- [cleanup] chat/intake.py:200-217,272-277 — the non-streaming intake call never records
  `cache_read_input_tokens` / `cache_creation_input_tokens`; only `input_tokens`/`output_tokens`
  are summed and passed to `record_llm_tokens`, so intake's cache-hit ratio is invisible and
  `total_in` undercounts when the cache is warm (the API reports cached tokens separately —
  Anthropic prompt-caching usage fields, fetched 2026-07-01) | fix: read
  `usage.cache_read_input_tokens` / `usage.cache_creation_input_tokens` and forward them to
  `record_llm_tokens(cache_read_tokens=…, cache_creation_tokens=…)`, as runner already does.
- [cleanup] chat/tools.py:46 (TOOLS) & chat/intake.py:60 (PROPOSE_PROFILE_TOOL) — schemas use
  `additionalProperties: false` but not `strict: true`. Current Anthropic docs recommend
  strict tool use to *guarantee* schema conformance and eliminate invalid tool calls
  (Anthropic tool-use, fetched 2026-07-01), which is especially cheap insurance on the
  injection-facing `propose_profile` and `clip_id` inputs | fix: add `"strict": true` to the
  custom tool definitions (validators stay as defense-in-depth).
- [cleanup] chat/runner.py:72 — `client = _ANTHROPIC.with_options(timeout=120.0)` re-wraps the
  singleton that is already constructed with a 120s timeout (runner.py:41-45); redundant |
  fix: drop the `with_options` call and use `_ANTHROPIC` directly.

## Verified-correct (no action)
- Prompt caching is set up per the docs: one `cache_control: ephemeral` breakpoint on the
  last (stable) system block caches the tool schemas + instructions together, with per-creator
  channel context in a second uncached block (prompt.py:54-71, intake.py:177-179). Confirmed
  against Anthropic prompt-caching "cache prefix ordering (tools→system→messages)" and
  "breakpoint on last system block caches tools too" (fetched 2026-07-01). Both models are
  Sonnet (min 1024 cacheable tokens) — the chat prompt with 8 tool schemas clears this;
  intake's single tool + niche list is likely near the threshold (needs-runtime-confirmation
  via `usage.cache_*` non-zero).
- `is_error: true` is set on tool_result exactly when the executor fails (runner.py:131-132,
  tools.py:544,549; intake.py:258) — matches the documented recovery signal, and the
  worker never propagates tool exceptions into the loop (tools.py:547-550).
- tool_result message shape is correct: results-only user message, no leading text
  (runner.py:134), satisfying the "tool_result must come first / no text before it" rule.
- Honesty constraint is embedded verbatim in both system prompts (prompt.py:19-24,49-51;
  intake.py:110-125) with explicit no-virality language — matches CLAUDE.md.
- Runaway guards present: `CHAT_MAX_TOOL_ITERATIONS` with a forced text-only final round
  (runner.py:71-77), `MAX_INTAKE_TURNS` (intake.py:54,167), output capped at CHAT_MAX_TOKENS.
- Concurrency: the blocking sync Anthropic stream is offloaded via `asyncio.to_thread`
  (runner.py:79); tools are awaited **sequentially** over one AsyncSession, so no concurrent
  use of a single async session. Clients are module-level singletons (runner.py:41, intake.py:46).

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — clients are singletons; the DB session is owned/closed by the worker context manager (worker/tasks.py:4364), chat module borrows it |
| 2 Concurrency & scale | ok — sync client off-loaded to thread; sequential tool exec on one session; all fetches bounded (limits capped _MAX_VIDEOS/_MAX_CLIPS) |
| 3 Security & compliance | 1 SEV1 — isolation correct today but no RLS backstop under BYPASSRLS; honesty constraint verified |
| 4 Clip-quality | n/a — reads clips/scores, does not compute them |
| 5 Anthropic SDK | caching/is_error/tool_result-shape verified correct; 1 SEV2 (missing truncation warn in runner) + 2 cleanup (cache-token accounting, strict) |
| 6 Cleanliness & typing | 1 cleanup (redundant with_options); signatures fully typed |
| 7 Error handling / API | n/a — not a router; HTTP surface owned by routers/chat.py & routers/creators.py |
| 8 Config & paths | ok — CHAT_* config in config.py with defaults and documented in .env.example; no filesystem paths |

## Module verdict
NEEDS-WORK — cross-tenant isolation is correct today but rides entirely on app-layer
filters with the RLS backstop switched off on the app's most injection-exposed surface;
close the SEV1 (app-role session + GUC + extend RLS table list) and fix the model/cost
attribution SEV2s.
