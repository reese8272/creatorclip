# analysis — assessed 2026-06-09

Slice: `analysis/__init__.py` (empty), `analysis/brief.py` (Issue 121 video-analysis brief, sync + streaming paths). Caller orchestration (DB session, retention/avg queries, Celery task, `asyncio.to_thread` wrap) lives in `worker/tasks.py` and is owned by that module's assessor; only behaviour internal to `analysis/brief.py` is scored here. `analysis/brief.py` is byte-identical to the 2026-06-08 assessment run — every prior finding re-verified against current line numbers; one new finding added (streaming disclaimer delivery).

## Findings

- [SEV2] analysis/brief.py:151 — streaming path appends `_DISCLAIMER` to the return value, but the streamed delivery channel this function itself drives (`stream_and_emit` → token-delta events on `task:{task_id}:events`) never carries it, and the caller (`worker/tasks.py:2317`) discards the return value. The module docstring (line 10: "The honesty disclaimer is always appended by Python") is therefore not effective in streaming mode. No live compliance gap today only because `static/analysis.html:564-567` hardcodes its own disclaimer under the output — the Python guarantee silently depends on the UI duplicating it. Rubric category 3. **Fix:** in the streaming branch, after `stream_and_emit` returns, emit the disclaimer onto the same stream — `from worker.progress import sync_emit; sync_emit(task_id, "token", chunk=_DISCLAIMER)` — so every consumer of the stream gets it, then keep the return-value append for parity.

- [SEV2] analysis/brief.py:69 — `_build_request` returns bare `tuple`. Loses the `(system, messages)` shape; mypy cannot catch a regression that swaps the order. Rubric category 6. **Fix:** annotate `-> tuple[list[dict[str, Any]], list[dict[str, Any]]]`.

- [SEV2] analysis/brief.py:95 — `system: list[dict]` is under-typed for the same reason. Rubric category 6. **Fix:** annotate `list[dict[str, Any]]` consistently with the return-type fix.

- [SEV2] analysis/brief.py:135 and 153 — both branches wrap the module-level singleton with `.with_options(timeout=120.0)` on every call. This not only duplicates the timeout already configured at line 28 — it *degrades* it: the constructor sets `httpx.Timeout(120.0, connect=10.0)`, and the flat `timeout=120.0` override replaces that with a 120s connect timeout too, so a black-holed TCP connect now hangs 120s instead of 10s before the SDK retries. Rubric categories 1 and 6. **Fix:** delete both `.with_options(...)` wraps — call `_ANTHROPIC.messages.create(...)` at line 153 and pass `_ANTHROPIC` to `stream_and_emit` at line 136.

- [SEV2] analysis/brief.py:133 — streaming path imports `worker.anthropic_stream` inside the function body. Verified no circular-import reason: `worker/anthropic_stream.py` imports only `worker.progress`, and `worker/__init__.py` is a bare docstring; `worker/tasks.py` imports `analysis.brief` lazily itself (worker/tasks.py:2214). The deferred import hides the dependency from static tools. Rubric category 6. **Fix:** hoist `from worker.anthropic_stream import stream_and_emit` to module top.

- [cleanup] analysis/brief.py:103 — `generate_video_analysis` body runs ~65 lines doing two distinct things (build request + dispatch to one of two SDK code paths). Rubric category 6. **Fix:** extract `_call_streaming(system, messages, task_id) -> str` and `_call_sync(system, messages) -> str`; top-level reduces to build-request + a one-line branch.

- [cleanup] analysis/brief.py:144-149 — token-usage log uses positional `%d` for four counters keyed off dict-access order; a future reorder silently swaps fields in production logs. Rubric category 6. **Fix:** name each counter inline or pass `extra={...}`.

- [cleanup] analysis/brief.py:167 — non-streaming branch picks `text_blocks[-1]` while the module docstring (line 7) promises "a single text block in the response". Defensible code, contradictory doc. Rubric category 6. **Fix:** either index `[0]` or drop the "single text block" claim from the docstring.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | 1 finding — `_ANTHROPIC` is a module-level singleton (line 26), good; but the per-call `.with_options(timeout=120.0)` wrap degrades the connect timeout (see SEV2 above). No DB sessions in this slice. |
| 2 Concurrency & scale | ok — sync functions only; caller offloads via `asyncio.to_thread` (worker/tasks.py:2317, verified). No hidden blocking-in-async inside this slice; context JSON is bounded (dna brief capped at 1000 chars, line 86; channel_avg/retention built bounded by the caller). |
| 3 Security & compliance | 1 finding — no token handling; logs carry only token counters (lines 144-149, 159-163), no PII. Honesty disclaimer hardcoded (line 32) and appended on both return paths (151, 167), but the streamed channel itself omits it (SEV2 above). No virality promise — disclaimer explicitly disclaims it. |
| 4 Clip-quality | n/a — analysis module. |
| 5 Anthropic SDK | ok — cache_control breakpoint deliberately absent, documented at lines 90-94 (175-token static prefix < 1024-token Sonnet 4.6 cacheable floor; one brief per video, low frequency; precedent improvement/brief.py — a justified deviation from the "caching mandatory" rule). Token usage logged after both call shapes (144-149, 159-163); `max_tokens=2000` set on both; streaming via the sanctioned `stream_and_emit` helper; web_search intentionally not used (docstring lines 5-8 — creator's own metrics are the authority, correct per north star). Free-form prose — no structured output needed. |
| 6 Cleanliness & typing | 7 findings — bare `tuple` return, under-typed system list, duplicated `.with_options` wraps, deferred local import, oversized top-level function, positional token-log formatting, docstring vs `text_blocks[-1]` mismatch. No TODO/print/commented-out code. |
| 7 Error handling / API | n/a — not a router. `RuntimeError` at line 166 is appropriate; caller catches and emits a safe operator message (worker/tasks.py:2333-2345, no stack trace to client). |
| 8 Config & paths | ok — `ANTHROPIC_API_KEY` (required, config.py:13) and `ANTHROPIC_MODEL` (config.py:51) via pydantic-settings; both documented in `.env.example` (lines 9, 11). No filesystem paths in this slice. |

## Module verdict

NEEDS-WORK — no security or cross-tenant defects; the new SEV2 (streamed channel never carries the honesty disclaimer — currently masked by the static page's own copy) and the connect-timeout-degrading `.with_options` wrap are the two items worth fixing before launch, plus typing/DRY cleanup carried over unfixed from the 2026-06-08 run.
