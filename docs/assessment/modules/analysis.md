# analysis — assessed 2026-06-07

Slice: `analysis/__init__.py` (empty), `analysis/brief.py` (Issue 121 video-analysis streaming brief). Caller orchestration (DB session, fan-out, Celery task) lives in `worker/tasks.py` and is owned by that module's assessor; only behaviour internal to `analysis/brief.py` is scored here.

## Findings

- [SEV2] `analysis/brief.py:86` — `cache_control` breakpoint sits on the static instructions block, but those instructions are ~700 chars / well under Sonnet 4.6's ~1024-token minimum cacheable prefix. The same trap is already documented for `improvement/brief.py` in `docs/DECISIONS.md` ("the static prefix is below Sonnet 4.6's minimum cacheable size, so the cache does not engage"). The token log at line 143 will therefore report `cache_read=0 cache_write=0` for every call, silently masking a missed cache. Rubric category 5 (Anthropic SDK — prompt caching mandatory). **Fix:** either (a) drop the `cache_control` breakpoint and add a `docs/DECISIONS.md` note that caching is intentionally off for this low-frequency call (matches improvement/), or (b) pad `_SYSTEM_INSTRUCTIONS` past the model's minimum cacheable size with stable, load-bearing guidance (clip principles registry, honesty constraint verbatim) and add a unit test asserting `cache_read > 0` on the second identical call against a recorded fixture.

- [SEV2] `analysis/brief.py:69` — `_build_request` returns bare `tuple`. Loses the `(list[dict], list[dict])` shape that callers and mypy both depend on; the Phase-4 typing gate cannot catch a regression that swaps the order. Rubric category 6. **Fix:** annotate `-> tuple[list[dict[str, Any]], list[dict[str, Any]]]`.

- [SEV2] `analysis/brief.py:90` — `system: list[dict]` is under-typed for the same reason. Rubric category 6. **Fix:** annotate `list[dict[str, Any]]` consistently with the return-type fix.

- [SEV2] `analysis/brief.py:152` — non-streaming branch re-wraps the module-level singleton with `.with_options(timeout=120.0)` on every call, duplicating the 120s timeout already configured at line 28. Same dead wrap at line 134 on the streaming path. Rubric category 6 (DRY/KISS). **Fix:** call `_ANTHROPIC.messages.create(...)` directly at line 152, and pass `_ANTHROPIC` to `stream_and_emit` at line 135 without the `.with_options` wrap.

- [SEV2] `analysis/brief.py:132` — streaming path imports `worker.anthropic_stream` inside the function body. No circular-import reason exists (worker/anthropic_stream.py does not import from `analysis/`); the deferred import hides the dependency from static tools and forces re-resolution on every streaming call. Rubric category 6. **Fix:** hoist `from worker.anthropic_stream import stream_and_emit` to module top.

- [cleanup] `analysis/brief.py:102` — `generate_video_analysis` body runs ~65 lines doing two distinct things (build request + dispatch to one of two SDK code paths). Rubric category 6. **Fix:** extract `_call_streaming(system, messages, task_id) -> str` and `_call_sync(system, messages) -> str`; top-level reduces to build-request + a one-line branch, mirroring `dna/brief.py`.

- [cleanup] `analysis/brief.py:143` — token-usage log uses positional `%d` for four counters; a future reorder of the `usage` dict will silently swap fields in production logs. Rubric category 6. **Fix:** use keyed format (`extra={...}`) or name each counter inline.

- [cleanup] `analysis/brief.py:163` — non-streaming branch picks `text_blocks[-1]`. Defensible (no tools means one text block) but the module docstring at line 7 promises "a single text block in the response" while the code defends against multiple. Rubric category 6 (code/comment disagreement). **Fix:** either index `[0]` (matches docstring) or drop the "single text block" promise from the docstring.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — `_ANTHROPIC` is a module-level singleton (line 26); no DB sessions in this slice (lives in `worker/tasks.py`). |
| 2 Concurrency & scale | ok — sync function; the only async-loop concern is the caller wrapping it in `asyncio.to_thread`, which `worker/tasks.py:2260` does correctly. No hidden blocking-in-async inside this slice. |
| 3 Security & compliance | ok — no token handling, no PII logged (only counts + the public youtube_video_id at lines 143/158). Honesty disclaimer hardcoded at line 32 and unconditionally appended (lines 150, 166); no virality promise present. Per-creator isolation enforced at the SQL layer in `worker/tasks.py`, not in scope here. |
| 4 Clip-quality | n/a — analysis module. |
| 5 Anthropic SDK | 1 SEV2 — cache breakpoint likely a no-op against the model's minimum cacheable size. Token logging present, streaming uses the sanctioned `stream_and_emit` helper, `max_tokens=2000` set. Free-form prose response — no structured output needed. |
| 6 Cleanliness & typing | 4 findings — bare `tuple` return, under-typed system list, duplicated `.with_options` wraps, deferred local import, oversized top-level function. |
| 7 Error handling / API | n/a — not a router. `RuntimeError` at line 165 is appropriate; caller catches and emits a safe operator message at `worker/tasks.py:2286`. |
| 8 Config & paths | ok — `ANTHROPIC_API_KEY` + `ANTHROPIC_MODEL` both read via `pydantic-settings` (`config.py:13`, `config.py:51`). No paths. |

## Module verdict

**NEEDS-WORK** — small, focused module with no security or correctness blockers, but the prompt-cache breakpoint is almost certainly a no-op (silent miss masked by a token log that always reports zeros), and several typing/cleanliness items should be tightened before launch. Fix the cache question first; the rest is cheap cleanup.
