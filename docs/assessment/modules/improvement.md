# improvement — assessed 2026-05-29

Slice: `improvement/__init__.py` (empty), `improvement/brief.py`.
Caller `routers/improvement.py` read for trace-through only (owned by the routers
subagent); cross-module findings noted but scored against this module's design where
the defect originates in `brief.py`.

## Findings

- [SEV1] improvement/brief.py:69 + 33-52 — Prompt caching is effectively dead.
  The per-creator/per-request variable payload (`analytics_json`, which includes
  channel title, computed averages, and DNA summary) is `.format()`-interpolated
  INTO the single cached system block. `cache_control: ephemeral` is set, but the
  cache key is the full prefix, so the prefix changes on every call and across
  every creator — cache hit rate is ~0. CLAUDE.md makes prompt caching mandatory;
  this satisfies it only cosmetically (the unit test at tests/test_upload_intel.py:118
  asserts the marker is present, not that anything is cacheable).
  | fix: split `system` into two blocks — a STATIC instructions block (the
  strategist role + output rules, no interpolation) carrying `cache_control:
  ephemeral`, followed by a SEPARATE uncached block holding the variable
  `analytics_json`. Only the static prefix is then reused across all creators and
  calls, which is where the cache savings actually come from. Add a test asserting
  the cached block contains no per-creator data.

- [SEV1] improvement/brief.py:55-93 — Synchronous, blocking Anthropic call with a
  120s timeout is the module's only entry point, and `routers/improvement.py:65`
  invokes it directly inside `async def get_improvement_brief` with no
  `asyncio.to_thread`. Under concurrency this pins the FastAPI event-loop thread
  for up to 120s per request (web_search round-trips), stalling every other
  request on that worker. At hundreds of creators this collapses p99 latency
  (scale-checklist B). | fix: either make this the contract by offloading at the
  call site (`await asyncio.to_thread(generate_improvement_brief, ...)`) or, better
  for a 60-120s job, move brief generation to a Celery task and have the endpoint
  return a job handle / poll. Document the chosen pattern. At minimum, brief.py's
  docstring must state it MUST NOT be called on the event loop directly.

- [SEV2] improvement/brief.py:103-107 — With the web_search tool enabled, Claude
  emits interleaved blocks (preamble text → server_tool_use → search results →
  final synthesized text). `text_blocks[0].text` returns the FIRST text block,
  which is frequently a "Let me search for..." preamble rather than the final
  recommendations, silently truncating/replacing the actual brief. (dna/brief.py:94
  shares the pattern but does not use web_search, so it is not exposed.)
  | fix: return the LAST text block: `return text_blocks[-1].text + _DISCLAIMER`,
  or concatenate all text blocks emitted after the final tool_use. Add a fixture
  test with a multi-block (preamble + tool_use + answer) response asserting the
  answer block is returned.

- [SEV2] improvement/brief.py:64-69 — No bound/validation on `analytics` or
  `channel_title` content placed into the prompt. `dna_brief` is capped to 1000
  chars (good) but `analytics` is whatever the caller passes; a large or
  attacker-influenced `channel_title` (from YouTube channel metadata) is
  interpolated into both the system block and the user message unescaped. Low
  injection blast radius (read-only research task, disclaimer forced in Python),
  but unbounded channel/analytics text can blow the cacheable prefix and token
  budget. | fix: cap `channel_title` length (e.g. `[:120]`) and serialize only a
  known allow-list of analytics keys, not the raw dict, before formatting.

- [SEV2] improvement/brief.py:73 — Model id `claude-sonnet-4-6` and tool version
  `web_search_20250305` are hardcoded string literals duplicated across
  dna/brief.py:65 and clip_engine/scoring.py:189 (DRY) and not validated against a
  current Anthropic model/tool catalog. The `/claude-api` skill referenced by
  CLAUDE.md is not present on disk to confirm these identifiers.
  (needs-runtime-confirmation) | fix: hoist the model id and web_search tool
  version into `config.py` (pydantic-settings) as `ANTHROPIC_MODEL` /
  `ANTHROPIC_WEB_SEARCH_TOOL`, referenced by all three call sites; verify the
  values against the live model list before launch.

- [cleanup] improvement/brief.py:46,58,64 — Typing gaps the mypy gate may not
  catch: `analytics: dict` should be `dict[str, object]` (or a TypedDict matching
  the router's payload), and `payload: dict[str, object]` is untyped at line 64.
  | fix: annotate `analytics: dict[str, object]`; declare `payload: dict[str,
  object] = {...}`.

- [cleanup] improvement/brief.py:98-99 — `getattr(response.usage, "cache_read_input_tokens", 0)`
  defensively defaults the cache fields to 0; this masks the SEV1 caching defect in
  the logs (a perpetual `cached_read=0` would otherwise flag it). Acceptable, but
  once caching is fixed, add an alert/assertion in monitoring that
  `cache_read_input_tokens` is non-zero after warmup. | fix: track cache-read ratio
  as an observability metric (scale-checklist G) rather than swallowing it.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — `_ANTHROPIC` is a module-level singleton with explicit timeout + max_retries; no DB/file handles in this module |
| 2 Concurrency & scale | 1 SEV1 (blocking 120s call reachable on the event loop via the router) |
| 3 Security & compliance | ok — no token handling here; no PII in the single log line (token counts only); disclaimer + no-virality enforced in Python; 1 SEV2 noted for unbounded prompt input. Isolation is enforced upstream in routers/improvement.py (Issue 33 fix verified) |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | 1 SEV1 (caching ineffective), 1 SEV2 (text-block extraction wrong under web_search), 1 SEV2 (hardcoded/unverified model + tool version). Token logging present (good); web_search tool wired (good) |
| 6 Cleanliness & typing | 2 cleanup (dict typing, masked cache metric) |
| 7 Error handling / API | n/a (not a router; raises RuntimeError on empty response, mapped to 502 by the caller — sane) |
| 8 Config & paths | 1 SEV2 (model/tool ids should live in config); no filesystem paths in module; ANTHROPIC_API_KEY is required via pydantic-settings (fail-fast, good) |

## Module verdict
NEEDS-WORK — no cross-tenant or BLOCKER defect in this slice, but mandatory prompt
caching is effectively inert, the only entry point is a 120s blocking call reachable
on the async loop, and web_search responses are extracted incorrectly (first text
block instead of the final answer).
