# improvement — assessed 2026-06-09

## Findings

- [SEV2] improvement/brief.py:171-174 — token-usage log uses
  `getattr(response.usage, "cache_read_input_tokens", 0)` / `cache_creation_input_tokens`.
  On the pinned SDK (anthropic==0.40.0) the non-beta `Usage` model defines only
  `input_tokens`/`output_tokens` (verified in
  `.venv/.../anthropic/types/usage.py`); the cache fields arrive as pydantic
  extras and are nullable in the API. The `getattr` default covers *absence*
  but not an explicit `null` → `None` hits the `%d` format → logging swallows
  the error and the MANDATORY token-usage log line is silently dropped
  (plus a "--- Logging error ---" traceback on stderr per call). Current
  (2026) API generally returns these as ints, so this may never fire in
  production (needs-runtime-confirmation). The streaming log at lines 148-154
  shares the hazard via the callee's usage dict (callee owned by `worker`).
  | fix: wrap both fields as `(getattr(response.usage, "cache_read_input_tokens", 0) or 0)`
  (1-LOC each); same `or 0` in the streaming log's consumption of the dict.
- [cleanup] improvement/brief.py:67 — `_build_request` returns bare `tuple`;
  mypy treats it as `tuple[Any, ...]` so a wrong-arity unpack at the call site
  (line 125) is invisible to the gate. No behavior risk today. | fix:
  annotate `-> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]`.
  (Carried from 2026-06-08; downgraded SEV2 → cleanup per rubric scale: typing,
  no behavior risk.)
- [cleanup] improvement/brief.py:25-29 — `_ANTHROPIC` constructed with
  `timeout=httpx.Timeout(60.0, connect=10.0)`, but BOTH call sites override it
  via `.with_options(timeout=120.0)` (lines 138, 161). The constructor timeout
  (and its 10s connect budget) is dead config that misleads readers about the
  live 120s budget. | fix: drop the constructor `timeout` kwarg, keep
  `max_retries=2`; if the 10s connect bound is wanted, use
  `.with_options(timeout=httpx.Timeout(120.0, connect=10.0))` at the call
  sites. (Carried; downgraded SEV2 → cleanup: no behavior risk, both paths
  override.)
- [cleanup] improvement/brief.py:80,93 — `system: list[dict]` and
  `tools: list[dict]` are bare `dict` (no value type). | fix: tighten to
  `list[dict[str, object]]`. (Carried from 2026-06-08.)
- [cleanup] improvement/brief.py:134 — function-local
  `from worker.anthropic_stream import stream_and_emit` with no stated reason.
  Verified there is NO import cycle (`worker/__init__.py` is docstring-only;
  `worker/anthropic_stream.py` imports only `worker.progress`); the real effect
  is keeping the redis-backed `worker.progress` chain out of import time for
  non-streaming callers/tests. | fix: either move the import to module top, or
  add a one-line comment stating the lazy-import reason so a future refactor
  doesn't cargo-cult or silently break it. (Carried from 2026-06-08, reason now
  verified.)

### Closed since 2026-06-08

- [CLOSED — finding invalid] "streaming path has no empty-text guard"
  (prev SEV2): re-verified — `worker/anthropic_stream.py:89-90` raises
  `RuntimeError("Claude returned no text block in streaming response")` inside
  `stream_and_emit` when no text blocks exist, symmetric with the `.create()`
  guard at improvement/brief.py:178-179. The "only tool_use blocks" scenario is
  already handled in the callee; no fix needed in this module.
- [CLOSED — tracked deviation] inert `cache_control` at improvement/brief.py:88
  (static prefix below Sonnet 4.6's 1024-token floor → 1.25× write premium,
  zero reads): explicitly captured in `docs/DECISIONS.md` (Issue 84 follow-ups:
  "Drop unproductive cache_control markers from DNA brief + improvement brief…
  Needs SDK bump first") and the A4 precedent. Documented, queued, not counted
  here.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — `_ANTHROPIC` is a module-level singleton; `.with_options()` copies the wrapper but reuses the underlying httpx transport (timeouts are applied per-request in the Stainless SDK); no DB sessions, file handles, or subprocesses in this module |
| 2 Concurrency & scale | ok — module is fully synchronous and invoked from the Celery worker via `asyncio.to_thread` (worker/tasks.py `_generate_improvement_brief_async`); bounded work (single LLM call, `max_tokens=2000`, DNA brief sliced to 1000 chars at line 76); no queries, no N+1 |
| 3 Security & compliance | ok — no DB access (per-creator isolation enforced at the worker call site); `ANTHROPIC_API_KEY` via `settings` only, never logged; log lines carry token counts only (no PII, no prompt text); honesty disclaimer appended in Python at lines 158/183, never delegated to the LLM; system prompt says "never promise virality" (line 51) |
| 4 Clip-quality | n/a (content-strategy brief, not clip scoring) |
| 5 Anthropic SDK | 1 finding — cache structure correct (stable prefix → `cache_control` → volatile per-creator block; marker inert by tracked design, see Closed); token logging present on both paths (lines 148-154, 169-175) but the None-vs-absent `getattr` hazard (SEV2 above) can silently drop the line; `max_tokens=2000` set; `web_search` tool wired on BOTH paths (lines 93, 146 — Wave-3 Fix A intact) |
| 6 Cleanliness & typing | 4 cleanup — bare `tuple` return, dead constructor timeout, loose `list[dict]` annotations, undocumented lazy import; no TODO/print/commented-out code |
| 7 Error handling / API | n/a (no router in this slice; FastAPI surface lives in routers/improvement.py) — module raises `RuntimeError` on no-text responses (line 179) for the caller to map |
| 8 Config & paths | ok — `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `ANTHROPIC_WEB_SEARCH_TOOL` all in config.py (13, 51, 57) and `.env.example` (9, 11-12) with descriptions; no filesystem paths in module |

## Module verdict

NEEDS-WORK — one SEV2 (nullable cache-token fields can silently drop the
mandatory token-usage log line on anthropic 0.40; needs-runtime-confirmation)
plus four cleanups. The 2026-06-08 streaming empty-text SEV2 is invalid (guard
exists in the callee) and the inert cache marker is a tracked DECISIONS
follow-up. Security, isolation, disclaimer handling, and web_search wiring are
sound; the module is one `or 0` and a typing pass away from clean.

## Issue 75 Reconciliation (2026-06-23)

| Finding | Disposition |
|---|---|
| [SEV2] nullable cache-token fields can drop token-usage log (improvement/brief.py:171-174) | → tracked in Issue 218 (re-enable prompt caching on repeated-prefix brief endpoints — fixes SDK and token logging) |
| [cleanup] bare tuple return (improvement/brief.py:67) | → tracked in Issue 109 (deferred design cleanups) |
| [cleanup] dead constructor timeout (improvement/brief.py:25-29) | → tracked in Issue 82 (async migration wave 2) |
| [cleanup] loose list[dict] annotations (improvement/brief.py:80,93) | → tracked in Issue 109 |
| [cleanup] undocumented lazy import (improvement/brief.py:134) | → tracked in Issue 109 |
