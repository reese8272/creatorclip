# improvement — assessed 2026-06-24

Slice: `improvement/__init__.py` (empty), `improvement/brief.py` (196 lines).
Module purpose: build a content-improvement brief via Claude + `web_search`, returning
`(brief_text, usage)`. All DB access and creator scoping live in `worker/tasks.py`
(`_generate_improvement_brief_async`), which is OUT of this slice; `brief.py` is a pure
LLM-call helper that receives pre-computed `analytics` + `dna_brief` as arguments.

## Findings
- [SEV2] improvement/brief.py:175-181 — the non-streaming token-usage log passes
  `getattr(response.usage, "cache_read_input_tokens", 0)` / `..._creation_input_tokens`
  straight into a `%d` format. The `getattr` default covers field *absence*, but the
  API may return an explicit JSON `null` → `None`, which fails `%d` and makes `logging`
  swallow the call: the MANDATORY (CLAUDE.md) token-usage line is silently dropped and a
  "--- Logging error ---" traceback prints per call. The `_usage` dict at lines 190-191
  is already hardened (`getattr(...) or 0`); the LOG args were not. The 2026 API generally
  returns these as ints (incl. 0), so it may never fire. (needs-runtime-confirmation) | fix:
  wrap each log arg as `(getattr(response.usage, "cache_read_input_tokens", 0) or 0)`
  (1 LOC each) — match the hardening already present on the `_usage` dict two lines below.
- [SEV2] improvement/brief.py:165 — neither call path handles `stop_reason == "pause_turn"`.
  `web_search` runs a server-side tool loop that pauses at its 10-iteration limit with
  `pause_turn`; on that path `text_blocks[-1].text` can be a "let me search…" preamble
  rather than the synthesised brief, and the partial answer is then stored `ready`. Low
  likelihood for a 3–5 item brief; bounded blast radius. (needs-runtime-confirmation) | fix:
  after the `.create()` call, if `response.stop_reason == "pause_turn"`, re-send
  `[user, assistant(response.content)]` and continue (bounded by `max_continuations`, e.g. 3)
  before extracting the final text block; the streaming path inherits the same gap via
  `worker/anthropic_stream.stream_and_emit` (out of slice).
- [cleanup] improvement/brief.py:7,61 — docstring + comment state the prompt-cache floor as
  "Sonnet 4.6's minimum cacheable size … the Sonnet 4.6 1024-token floor". Per the
  /claude-api skill and DECISIONS.md's own canonical correction (line 4850, Issue 138),
  Sonnet 4.6's floor is **2048 tokens**; 1024 is the Sonnet **4.5** floor. Code behaviour
  is unaffected (the static prefix is under 2048 too, so the `cache_control` marker at line
  90 genuinely never engages), but the rationale re-propagates a figure DECISIONS already
  fixed (and which line 150 of DECISIONS regressed to 1024 again today). | fix: change both
  references to "2048-token Sonnet 4.6 floor". The inert `cache_control` at line 90 stays a
  tracked DECISIONS follow-up (Issue 84 / Issue 218).
- [cleanup] improvement/brief.py:69,82,93,95,114 — loose container annotations: `_build_request`
  returns bare `tuple` (mypy → `tuple[Any, ...]`, so a wrong-arity unpack at line 129 is
  invisible to the gate); `system`/`tools` are bare `list[dict]`; return is `tuple[str, dict]`.
  No behaviour risk. | fix: annotate `_build_request -> tuple[list[dict], list[dict], list[dict]]`,
  tighten `list[dict]` → `list[dict[str, object]]`, and the usage return `dict` → `dict[str, int]`.
- [cleanup] improvement/brief.py:26-30 — `_ANTHROPIC` is constructed with
  `timeout=httpx.Timeout(60.0, connect=10.0)`, but BOTH call sites override it via
  `.with_options(timeout=120.0)` (lines 142, 165), discarding the 10s connect budget. The
  constructor timeout is dead config that misleads readers about the live budget. | fix:
  drop the constructor `timeout` kwarg (keep `max_retries=2`), or carry the connect bound
  forward with `.with_options(timeout=httpx.Timeout(120.0, connect=10.0))` at the call sites.

## Verified clean (load-bearing, traced by reading)
- **Per-creator isolation**: no SQL in this slice. The caller (`worker/tasks.py:2795,2848`)
  scopes every query (`ImprovementBrief.creator_id == cid`, `Video.creator_id == creator.id`)
  and stamps `session.info["creator_id"]` for the RLS `after_begin` listener.
  `tests/test_improvement_isolation.py` asserts creator A's brief receives only A's metrics.
- **Token/PII in logs**: the two `logger.info` calls (lines 152–158, 175–181) log ONLY
  integer token counts. `analytics`, `dna_brief`, and `brief_text` are never logged.
- **OAuth/token handling**: none in this module. `ANTHROPIC_API_KEY` read once at import
  (line 27); never logged.
- **Concurrency / sync-in-async**: `generate_improvement_brief` is intentionally SYNC (the
  Anthropic sync client blocks); the caller invokes it via `asyncio.to_thread`
  (tasks.py:2879), so the blocking SDK call never runs on the event loop. `_ANTHROPIC` is a
  module-level singleton reused via `.with_options()` — not reconstructed per call.
- **Anthropic usage**: `max_tokens=2000` on both paths (non-streaming 2000 < ~16K guard, no
  ValueError); token usage logged after every call and returned for
  `billing.ledger.record_llm_usage` (tasks.py:2901). `web_search_20260209` is the correct
  dynamic-filtering tool version for Sonnet 4.6, wired on BOTH paths (Wave-3 Fix A intact).
  Honesty disclaimer appended in Python (never delegated to the LLM); system prompt forbids
  promising virality (line 53); `UNTRUSTED_CONTENT_POLICY` prepended to the static prefix.
- **Idempotency**: handled by the caller (tasks.py:2811 short-circuits a redelivery whose
  row is already `ready` for this job_id) — not this slice's responsibility.
- **Empty-text guard**: present on both the `.create()` path (line 184) and the streaming
  callee (`worker/anthropic_stream.py:89`) — symmetric.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — module-level client singleton; no DB/handles/subprocess in slice (1 dead-timeout cleanup) |
| 2 Concurrency & scale | ok — sync fn run via `to_thread`; no sync-in-async; bounded work (1 call, max_tokens=2000, DNA sliced to 1000 chars) |
| 3 Security & compliance | ok — no SQL in slice; no token/PII in logs; isolation enforced by caller + integration test; no virality promise |
| 4 Clip-quality | n/a (content-strategy brief, not clip scoring) |
| 5 Anthropic SDK | 2 SEV2 (null cache-token field can drop mandatory log line; `pause_turn` unhandled on web_search path) + 1 cleanup (wrong cache-floor in docstring); caching structure / tokens / max_tokens / tool wiring otherwise correct |
| 6 Cleanliness & typing | 2 cleanup (loose tuple/list/dict annotations; dead constructor timeout); no TODO/print/commented-out code |
| 7 Error handling / API | n/a (no router in slice; `RuntimeError` on no-text is internal, caller maps to a safe message) |
| 8 Config & paths | ok — `ANTHROPIC_API_KEY/MODEL/WEB_SEARCH_TOOL` in config.py + `.env.example` with descriptions; no filesystem paths |

## Module verdict
NEEDS-WORK — no blockers; two SEV2s (both needs-runtime-confirmation: a nullable cache-token
field can silently drop the mandatory token-usage log line, and `pause_turn` from the
web_search server loop is unhandled so a partial brief can be stored `ready`) plus three
cleanups (a cache-floor figure the docstring states wrong, loose container annotations, and a
dead constructor timeout). Security, isolation, disclaimer handling, and web_search wiring are
sound; the module is two `or 0`s, a `pause_turn` branch, and a typing pass from clean.
