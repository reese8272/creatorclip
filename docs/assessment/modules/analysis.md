# analysis — assessed 2026-06-08

Slice: `analysis/__init__.py` (empty), `analysis/brief.py` (Issue 121 video-analysis streaming brief). Caller orchestration (DB session, fan-out, Celery task) lives in `worker/tasks.py` and is owned by that module's assessor; only behaviour internal to `analysis/brief.py` is scored here.

## Findings

- [SEV2] `analysis/brief.py:69` — `_build_request` returns bare `tuple`. Loses the `(list[dict], list[dict])` shape that callers and mypy both depend on; the Phase-4 typing gate cannot catch a regression that swaps the order. Rubric category 6. **Fix:** annotate `-> tuple[list[dict[str, Any]], list[dict[str, Any]]]`.

- [SEV2] `analysis/brief.py:95` — `system: list[dict]` is under-typed for the same reason. Rubric category 6. **Fix:** annotate `list[dict[str, Any]]` consistently with the return-type fix.

- [SEV2] `analysis/brief.py:135` and `153` — non-streaming and streaming branches both wrap the module-level singleton with `.with_options(timeout=120.0)` on every call, duplicating the 120s timeout already configured at line 28. Rubric category 6 (DRY/KISS). **Fix:** call `_ANTHROPIC.messages.create(...)` directly at line 153, and pass `_ANTHROPIC` to `stream_and_emit` at line 136 without the `.with_options` wrap.

- [SEV2] `analysis/brief.py:133` — streaming path imports `worker.anthropic_stream` inside the function body. No circular-import reason exists (worker/anthropic_stream.py does not import from `analysis/`); the deferred import hides the dependency from static tools and forces re-resolution on every streaming call. Rubric category 6. **Fix:** hoist `from worker.anthropic_stream import stream_and_emit` to module top.

- [cleanup] `analysis/brief.py:103` — `generate_video_analysis` body runs ~65 lines doing two distinct things (build request + dispatch to one of two SDK code paths). Rubric category 6. **Fix:** extract `_call_streaming(system, messages, task_id) -> str` and `_call_sync(system, messages) -> str`; top-level reduces to build-request + a one-line branch, mirroring `dna/brief.py`.

- [cleanup] `analysis/brief.py:144-149` — token-usage log uses positional `%d` for four counters; a future reorder of the `usage` dict will silently swap fields in production logs. Rubric category 6. **Fix:** use keyed format (`extra={...}`) or name each counter inline.

- [cleanup] `analysis/brief.py:167` — non-streaming branch picks `text_blocks[-1]`. Defensible (no tools means one text block) but the module docstring at line 7 promises "a single text block in the response" while the code defends against multiple. Rubric category 6 (code/comment disagreement). **Fix:** either index `[0]` (matches docstring) or drop the "single text block" promise from the docstring.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — `_ANTHROPIC` is a module-level singleton (line 26); no DB sessions in this slice (lives in `worker/tasks.py`). |
| 2 Concurrency & scale | ok — sync function; the only async-loop concern is the caller wrapping it in `asyncio.to_thread`, which `worker/tasks.py:2260` does correctly. No hidden blocking-in-async inside this slice. |
| 3 Security & compliance | ok — no token handling, no PII logged (only counts + the public youtube_video_id at lines 145/162). Honesty disclaimer hardcoded at line 32 and unconditionally appended (lines 151, 167); no virality promise present. Per-creator isolation enforced at the SQL layer in `worker/tasks.py`, not in scope here. |
| 4 Clip-quality | n/a — analysis module. |
| 5 Anthropic SDK | ok — cache_control breakpoint was removed per Issue-135 audit (lines 90-94 document the rationale: 175-token prefix vs 1024-token Sonnet 4.6 floor means cache does not engage). Token logging present at lines 144-149, streaming uses the sanctioned `stream_and_emit` helper, `max_tokens=2000` set. Free-form prose response — no structured output needed. |
| 6 Cleanliness & typing | 5 findings — bare `tuple` return, under-typed system list, duplicated `.with_options` wraps, deferred local import, oversized top-level function, positional-format token logging, docstring vs code mismatch on text-block count. |
| 7 Error handling / API | n/a — not a router. `RuntimeError` at line 166 is appropriate; caller catches and emits a safe operator message at `worker/tasks.py:2286`. |
| 8 Config & paths | ok — `ANTHROPIC_API_KEY` + `ANTHROPIC_MODEL` both read via `pydantic-settings` (`config.py:13`, `config.py:51`). No paths. |

## Module verdict

**NEEDS-WORK** — the cache_control BLOCKER from the prior run was already fixed and is now correctly documented. Remaining items are typing/DRY cleanup: two bare-type annotations on the hot path (tuple return and system list), duplicated timeout wrap, deferred import, and function-size refactoring. No correctness or security defects; safe to ship as-is if cleanup is deferred, but should be tightened before general launch.
