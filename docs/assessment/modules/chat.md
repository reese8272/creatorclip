# chat — assessed 2026-06-24

Slice: `chat/__init__.py`, `chat/intake.py`, `chat/prompt.py`, `chat/runner.py`,
`chat/tools.py`. The Pro chatbot (Issue 152) + chat-driven onboarding intake (Issue 96).

Verified dependencies (read, not assumed): `config.py` (ANTHROPIC_MODEL=`claude-sonnet-4-6`,
CHAT_MAX_TOKENS=1500, CHAT_MAX_TOOL_ITERATIONS=4), `worker/anthropic_stream.py`
(`stream_message` returns full message + usage dict incl. cache tokens), `observability.record_llm_tokens`,
`db.py` (RLS GUC emitter + `AdminSessionLocal` BYPASSRLS), `models.py` (creator scoping),
`dna/profile.get_active`, `upload_intel/timing.{best_upload_windows,optimal_gap_hours}`,
`billing/ledger.{increment_usage,_estimate_cost_usd}`, and both call sites
(`worker/tasks.py:_chat_respond_async`, `routers/creators.py:identity_chat`).

## Findings
- [SEV1] chat/tools.py:115-280 (whole executor surface) — the Pro chat runs in
  `worker/tasks.py:_chat_respond_async` under `db.AdminSessionLocal()`, which connects as the
  **BYPASSRLS** migration role and does **not** set `session.info["creator_id"]` (db.py:22-23,148).
  So the RLS `tenant_isolation` policy does NOT gate any chat tool query — per-creator isolation
  rests ENTIRELY on the application-level `WHERE creator_id == creator_id`. Today every executor
  is correctly scoped (verified below), so there is no live leak — but there is zero
  database safety net, and any future tool added here that forgets a `WHERE creator_id` is an
  immediate cross-tenant leak with nothing to catch it. | fix: open the chat turn on the
  RLS-scoped factory — `db.AsyncSessionLocal()` with `session.info["creator_id"] = cid` set
  before first query (mirrors the FastAPI auth dependency) — so RLS backstops the app filter;
  OR add a test asserting creator B's session cannot read creator A's video/DNA/metrics through
  `execute_tool`. (needs-runtime-confirmation that AdminSessionLocal is the only session reaching
  these tools — confirmed for the one caller traced.)
- [SEV2] chat/intake.py:247-252 — `record_llm_tokens(...)` omits `cache_read_tokens` /
  `cache_creation_tokens`, yet the intake system block carries `cache_control: ephemeral`
  (intake.py:178). Cache activity for the intake path is therefore never observed, and the
  `total_in` sum (intake.py:196) folds `cache_read` into `input_tokens` indistinguishably — so
  the Prometheus cache-hit signal and any cost attribution for intake are blind. | fix: read
  `getattr(usage, "cache_read_input_tokens", 0)` / `cache_creation_input_tokens` off the
  non-streaming `create` response and pass them through (runner.py already does this).
- [SEV2] chat/intake.py:46-50 — the `AsyncAnthropic` singleton sets `max_retries=2` and a 60s
  timeout, but `run_intake_turn` can issue **two** sequential `create()` calls (the validation
  self-correction loop, intake.py:187), each itself retrying twice. Worst case a single intake
  turn fans out to ~4 LLM round-trips under the router's 60s request budget
  (routers/creators.py is request/response, not Celery). On Anthropic 429/529 this can blow the
  HTTP request timeout and surface a 5xx to the creator mid-onboarding. | fix: cap the
  correction loop's second call with `with_options(max_retries=0)` (the model already had its
  one retry via the loop), or move intake behind the SSE/Celery path if turns can be slow.
  (needs-runtime-confirmation under live 429s.)
- [SEV2] chat/runner.py:125-147 — the billing-ledger write (`increment_usage`) runs INSIDE the
  same `session` that `worker/tasks.py:_chat_respond_async` later `commit()`s after appending the
  assistant `ChatMessage`. `increment_usage` is wrapped in a best-effort try/except that swallows
  the error (good), but on success it leaves an uncommitted UPDATE in the session; if the
  outer assistant-message append then fails before `commit`, usage is silently rolled back with
  it (under-bill), and if `increment_usage` does its own commit the half-built turn is exposed.
  The lifecycle is split across two modules with no documented contract. | fix: pull the ledger
  write out of `run_chat_turn` and have the worker do it in the same transaction as the
  assistant-message append (one atomic commit), or document that `run_chat_turn` must not commit.
- [cleanup] chat/runner.py:125-127 — `from datetime import UTC, datetime` and
  `from billing.ledger import ...` are imported inside the function body, after the token log,
  rather than at module top. KISS/readability; no behavior risk (the billing import is plausibly
  deferred to dodge a cycle — if so, note it). | fix: hoist to module imports or add a one-line
  WHY comment if the deferral is a real cycle break.
- [cleanup] chat/runner.py:48 vs chat/intake.py:152 — `_text_of()` is defined twice with
  near-identical bodies (one filters falsy text, one checks `b.text`), DRY. | fix: extract a
  single `text_of(message)` into a shared `chat/_util.py` and import in both.
- [cleanup] chat/runner.py:72 — `client = _ANTHROPIC.with_options(timeout=120.0)` re-sets a
  timeout the singleton already carries (runner.py:44 `httpx.Timeout(120.0, ...)`); redundant. |
  fix: drop the `with_options` call, or use it to set a per-turn override that actually differs.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | 1 finding — singletons ✓ (both Anthropic clients module-level); session owned by caller, committed by caller; ledger write straddles the session boundary (SEV2 runner.py:125) |
| 2 Concurrency & scale | 1 finding — `asyncio.to_thread` correctly offloads the blocking sync stream (runner.py:78); no sync call on the loop; intake uses native `AsyncAnthropic`; tool-round + intake-correction fan-out bounded but retry-stacked (SEV2 intake.py:187) |
| 3 Security & compliance | 1 finding — isolation correct at app layer on ALL 5 tools (traced each WHERE), but no RLS backstop because chat runs BYPASSRLS (SEV1 tools.py); untrusted creator text fenced by `UNTRUSTED_CONTENT_POLICY` + tool-only `propose_profile` + re-validation; no token/PII in any logger line; honesty constraint embedded + test-pinned; parameterized SQL only (`.ilike(f"%{q}%")` is a bound param, not string-built) |
| 4 Clip-quality | n/a (chat surface, not a clip/dna/preference engine) |
| 5 Anthropic SDK | ok — prompt caching present on both paths (runner via build_system, intake via ephemeral system block); token usage logged after every call; `max_tokens` set (CHAT_MAX_TOKENS=1500); structured tool schema with `additionalProperties:false`; cache tokens logged on chat path, MISSING on intake (SEV2 intake.py:247) |
| 6 Cleanliness & typing | 3 cleanups — no print/TODO/debug; ruff clean; every signature typed; duplicate `_text_of` (DRY) + function-body imports + redundant `with_options` |
| 7 Error handling / API | n/a (no router in slice; the owning endpoints live in routers/creators.py + worker/tasks.py). Tool errors correctly surfaced via `is_error:true`, never raised into the loop (tools.py:292) |
| 8 Config & paths | ok — CHAT_MAX_TOKENS / CHAT_MAX_TOOL_ITERATIONS / ANTHROPIC_MODEL all in `.env.example` with descriptions; no filesystem paths in slice; config via pydantic-settings |

## Module verdict
NEEDS-WORK — no live cross-tenant leak (every chat tool query is correctly creator-scoped), but
the Pro chat runs under a BYPASSRLS session with no database isolation backstop, so the SEV1 is a
standing fragility that turns the next forgotten `WHERE` into a silent leak; plus three bounded
SEV2s (intake cache-token blindness, retry-stacked intake fan-out, ledger write straddling the
session boundary).
