# improvement — assessed 2026-06-08

## Findings

- [CLOSED] cache_control breakpoint inert — formally exempted by DECISIONS.md A4 
  as acceptable for low-frequency one-shot-per-video calls. The static prefix 
  intentionally sits below Sonnet 4.6's 1024-token minimum; caching is a no-op 
  but the documented posture is correct. No fix needed.
- [SEV2] brief.py:67 — `_build_request` returns `tuple` (untyped element types);
  mypy cannot catch a caller unpacking the wrong arity, and the streaming /
  non-streaming paths both unpack three values without static guarantee. | fix:
  annotate `-> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]`.
- [SEV2] brief.py:147 — streaming path (lines 139-147) has no empty-text guard.
  If `stream_and_emit` returns an empty `final_text` (e.g. model emitted only 
  `tool_use` blocks before stopping), the disclaimer is appended to an empty string 
  and persisted as a "ready" brief. The `.create()` path at line 179 raises 
  `RuntimeError` for this case. | fix: add `if not final_text: raise RuntimeError("Claude 
  returned no text in streaming improvement brief")` immediately after the `stream_and_emit` 
  call (after line 147).
- [SEV2] brief.py:27 — `_ANTHROPIC` is constructed with a 60s timeout, then 
  every caller overrides it to 120s via `.with_options(timeout=120.0)` (lines 
  138, 161). The module-level timeout is dead config that misleads readers 
  about the live budget. | fix: drop `timeout=httpx.Timeout(60.0, connect=10.0)` 
  from the constructor and keep `max_retries=2`; document at the top of the 
  module that callers set per-call timeouts because web_search latency varies 
  60-120s.
- [cleanup] brief.py:80, 93, 94 — `system: list[dict]`, `tools: list[dict]`,
  `messages = [...]` are typed as bare `list[dict]` rather than 
  `list[dict[str, object]]`. Mypy already passes (the gate is fine),
  but readers hit `dict` with no value type. | fix: tighten to 
  `list[dict[str, object]]`.
- [cleanup] brief.py:134 — `from worker.anthropic_stream import stream_and_emit`
  is a function-local import to break a circular dependency. The *reason* is not 
  documented; a future refactor may move it to module-top unknowingly and break 
  the cycle. | fix: add a one-line comment explaining the cycle it prevents 
  (verify the actual shape: likely "worker → improvement → worker" via config).

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — `_ANTHROPIC` is a module-level singleton; `.with_options` returns a wrapped client without re-opening the HTTP pool; no DB sessions, file handles, or subprocesses |
| 2 Concurrency & scale | ok — module is fully synchronous; Celery task at worker/tasks.py:2065 wraps `build_brief` in `asyncio.to_thread`; bounded work (max_tokens=2000, single LLM call); no N+1 |
| 3 Security & compliance | ok — no DB queries (per-creator isolation at call site); `ANTHROPIC_API_KEY` via `settings` only; token logs safe (no PII/prompt text); honesty disclaimer appended in Python (line 158/183, never left to LLM); no virality promise in system block (line 51: "never promise virality") |
| 4 Clip-quality | n/a (improvement brief is content-strategy guidance, not clip scoring) |
| 5 Anthropic SDK | ok — cache structure correct (stable prefix → `cache_control: ephemeral` → volatile per-creator block); breakpoint inert per design (DECISIONS A4); token logging present both paths (lines 148–154, 169–175); `max_tokens=2000` set; `web_search` tool wired both paths (lines 93, 146) |
| 6 Cleanliness & typing | 3 SEV2 + 2 cleanup — `_build_request -> tuple` unparameterised; missing empty-text guard on streaming path; dead module-level timeout; loose `list[dict]` annotations; undocumented local import |
| 7 Error handling / API | n/a (no router/endpoint in this module; FastAPI surface lives in routers/improvement.py) |
| 8 Config & paths | ok — `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `ANTHROPIC_WEB_SEARCH_TOOL` in config; no filesystem paths; `_DNA_BRIEF_MAX_CHARS = 1000` documented (line 56–60) |

## Module verdict

NEEDS-WORK — the cache_control defect is formally closed by DECISIONS.md A4. 
However, three SEV2 items remain: untyped tuple return hiding arity bugs from 
mypy, missing empty-text guard on streaming path that can persist blank briefs, 
and dead module-level timeout that misleads about the actual 120s budget. Security, 
token logging, and per-creator isolation are sound; web_search wiring is correct.
