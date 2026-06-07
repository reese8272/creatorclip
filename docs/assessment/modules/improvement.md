# improvement — assessed 2026-06-07

## Findings

- [cleanup] brief.py:67 — Function `_build_request` parameter `dna_brief: str | None` is documented in the docstring but the function signature lacks a return type annotation | fix: add explicit return type annotation `-> tuple[list[dict], list[dict], list[dict]]` to match the actual return statement on line 104.
- [cleanup] brief.py:25–29 — Module-level `_ANTHROPIC` client is instantiated with httpx.Timeout but the timeout is immediately overridden on every call via `.with_options(timeout=120.0)` (lines 138, 161); the module-level timeout is unused | fix: remove the timeout from the _ANTHROPIC constructor (lines 27–28) and document that per-call timeouts are set by callers to handle web_search latency (60–120s).

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — Anthropic client is module-level singleton (line 25); properly instantiated once and reused; httpx timeout bounds blocking calls; streaming path delegates JSON emission to worker/anthropic_stream which holds no resources beyond the stream context; no connection leaks on error (timeout raises cleanly) |
| 2 Concurrency & scale | ok — Anthropic SDK calls are blocking but invoked from Celery task queue (worker/tasks.py delegates via run_async to the event loop); no sync/blocking calls inside async def; streaming path (task_id parameter) uses asyncio.to_thread wrapper (implicit in worker/tasks.py); bounded work: single LLM call per request, max_tokens=2000, fixed system block (no unbounded concatenation) |
| 3 Security & compliance | ok — analytics dict is passed as JSON (line 78); no PII in system instructions (line 38–53); creator channel_title is user-facing (not PII); token usage logged without exposing API key (line 148–154, 169–175); no virality promise (disclaimer appended line 158, 183); ANTHROPIC_API_KEY accessed via settings only (line 26), never logged |
| 4 Clip-quality | n/a — improvement brief is content strategy guidance, not clip scoring |
| 5 Anthropic SDK | ok — token usage logged after every call (lines 148–154 for streaming, 169–175 for blocking); cache_control set to ephemeral (line 88); max_tokens bounded (2000, line 143, 163); web_search tool passed correctly when task_id is set (line 146) and when None (line 165); streaming path uses stream_and_emit which handles tool_use blocks correctly (Issue 92) |
| 6 Cleanliness & typing | 2 cleanup — _build_request return type missing (should be explicit tuple[...][...][...]); module-level _ANTHROPIC timeout parameter unused (overridden per-call); all other functions typed; no print/TODO/debug statements |
| 7 Error handling / API | n/a — improvement is internal (no HTTP router); callers (routers/improvement.py assumed) handle CloudflareError or LLM failures; RuntimeError raised when no text block returned (line 179); streaming failures logged but do not abort iteration (worker/anthropic_stream.py:84) |
| 8 Config & paths | ok — all required settings present (.env.example: ANTHROPIC_API_KEY, ANTHROPIC_MODEL, ANTHROPIC_WEB_SEARCH_TOOL); DNA_BRIEF_MAX_CHARS constant (line 60) documented as preventing prompt-token overflow; no paths used |

## Module verdict

clean — No blockers, no SEV1; 2 trivial cleanup items (missing return type annotation, unused timeout parameter). Token logging is correct; security posture is sound; cache breakpoint structure (Issue 69) and web_search tool integration (Issue 92) are correctly implemented. Streaming path properly delegates to worker.anthropic_stream.

