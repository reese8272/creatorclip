# clip_engine — assessed 2026-06-01

## Findings
- [SEV2] clip_engine/ranking.py:102 — `select(Clip).where(Clip.video_id == video_id)` still has no `creator_id` predicate (defense-in-depth gap; carry-forward from previous reports). `video_id` is unique and the caller path is internal, so not a live cross-tenant leak today, BUT the CLAUDE.md rule is "filter on creator_id on EVERY query touching a creator-scoped table" — it's the only safeguard that survives a future refactor mis-passing `video_id`. The function already takes `creator_id: uuid.UUID` as a parameter, so adding the predicate is free | fix: add `.where(Clip.creator_id == creator_id)` to line 102; add regression test asserting the probe refuses clips whose `creator_id` doesn't match.

- [SEV2] clip_engine/scoring.py:23 — module-level `AsyncAnthropic(...)` singleton is correct per rubric §1 (external clients are module-level singletons), BUT `AsyncAnthropic` binds its underlying httpx client to the loop it sees at first use. Under FastAPI (one loop) this is fine. In the Celery `run_async` path (`worker/tasks.py:_generate_clips_async` → `worker/celery_app.run_async(...)` → creates fresh loop per task), the singleton's httpx pool was bound to the FIRST loop the worker process saw, leading to `RuntimeError: Event loop is closed` or stalled connections under load (carry-forward; unchanged in this cycle) | fix: lazy-construct the client per-loop via `contextvars.ContextVar` or `functools.lru_cache(maxsize=1)` keyed on `id(asyncio.get_event_loop())`; OR drop to sync `Anthropic(...)` called via `asyncio.to_thread`. (needs-runtime-confirmation under actual `run_async` concurrency.)

- [SEV2] clip_engine/render.py:131-146, routers/clips.py:61-72 — `RenderStyleIn` accepts arbitrary string values for `subtitle` and `background` with NO validation (Pydantic field: `subtitle: str | None = None`). Invalid values can be persisted to `Clip.style_preset` JSONB, polluting the database. At render time, `render.py:184` safely checks `subtitle_key in _SUBTITLE_FILTERS`, so invalid values are silently ignored — NOT a render-time injection risk — BUT allows garbage data into the database and could cause confusion in the UI or future features | fix: add Pydantic field validation to RenderStyleIn using `Literal["white_large" | "yellow_impact" | "captions_sm" | None]` for subtitle and `Literal["blur" | "black" | None]` for background; add regression test that asserts POST /clips/{id}/render rejects invalid style values with 422.

- [cleanup] clip_engine/render.py:125-128 — `_BACKGROUND_STYLES` dict is defined but never used in the render pipeline. The `background` field is accepted in the RenderStyleIn but not applied to the video filter chain. This is either incomplete implementation or dead code | fix: either (a) implement the background-style filter application in `render_clip_file` (similar to subtitle handling at line 184), or (b) remove the `_BACKGROUND_STYLES` dict and drop `background` from RenderStyleIn to match the actual feature scope.

- [cleanup] tests/test_render_style.py:96-131 — test `test_render_clip_file_passes_style_to_vf()` docstring claims "gracefully ignores unrecognised subtitle keys" but the test only uses a recognized key ("white_large") and never exercises the graceful-ignore path. The test does not verify that injection-like values (e.g., `"'; DROP"`, `"${VAR}"`) are safely rejected | fix: add a second test case passing `style_preset={"subtitle": "UNKNOWN_INJECTION_TEST"}` and assert that "drawtext" does NOT appear in the vf string (confirming graceful ignore).

- [carry-forward SEV2] clip_engine/render.py:82-105 — single keyframe face detection at mid-clip will miscrop on shot-changing content (carry-forward; unchanged in this cycle). Industry standard is 3-frame sampling with median-of-3 centering | fix: sample 3 keyframes, run detection on each, take median x (see previous assessment for cost/benefit).

- [carry-forward SEV2] clip_engine/scoring.py:203 — Haiku 4.5 A/B opportunity for clip scoring (deterministic JSON, narrow task) still not promoted to a tracked numbered issue. Issue 109's 10 deferred cleanups do NOT include the Haiku A/B follow-up from Issue 84 | fix: file Issue 112 "Evaluate Haiku 4.5 for clip_scoring" with A/B eval harness + threshold gate.

- [carry-forward cleanup] clip_engine/scoring.py:68-159 — `compute_features` rebuilds `build_signal_array(timeline)` once per candidate (up to 8 full rebuilds of the identical array). Explicitly captured in Issue 109 item #7 | fix: build signal once at top of `score_candidates`, pass to `compute_features`.

- [carry-forward cleanup] clip_engine/render.py:167 — `_extract_keyframe` timeout is bound to `render_timeout_s = max(120s, 4 × clip_duration)`. Pulling one JPEG should never exceed a few seconds; this masks ffmpeg health issues. Captured in Issue 109 item #8 | fix: hardcode `_extract_keyframe(..., timeout_s=30.0)`.

- [carry-forward cleanup] clip_engine/candidates.py:104 — `end_s` extension logic is silent and undocumented. Module docstring promises POST_PEAK_S=20s context but `end_s = min(duration_s, max(peak + POST_PEAK_S, setup + MIN_CLIP_S))` silently extends past the payoff window. Captured in Issue 109 item #5 | fix: update file docstring or drop the extension.

- [carry-forward cleanup] clip_engine/scoring.py:170 — cold-start path cites principle #6 ("Retention curve is ground truth") but has NO retention data (DNA brief is built from analytics; absence of DNA often means absence of retention data). The cited principle is mis-attributed. Captured in Issue 109 item #6 | fix: semantic decision required; recommendation is principle #4 ("Pattern interrupt").

- [carry-forward cleanup] clip_engine/ranking.py:36-38 — local imports of preference module inside `rerank_with_preference` pay an import cost on EVERY rerank call. No circular risk. Captured in Issue 109 item #4 | fix: move to module top.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — keyframe tempfile unlinked in `finally` (render.py:170); Anthropic client is a module-level singleton (scoring.py:23) with explicit `httpx.Timeout(60s read, 10s connect)` + `max_retries=2`. 1 SEV2: AsyncAnthropic loop-binding under celery `run_async`. |
| 2 Concurrency & scale | 1 SEV2 (AsyncAnthropic loop-binding; carry-forward). `extract_candidates` and feature computation wrapped in `asyncio.to_thread` (ranking.py:115, scoring.py:161). Anthropic call is async-native. `render.py` uses synchronous subprocess but only called from worker `asyncio.to_thread`, no loop starvation. |
| 3 Security & compliance | 1 SEV2 (ranking.py:102 missing `creator_id` predicate; carry-forward). 1 SEV2 (RenderStyleIn lacks validation, allows garbage into database). No token/PII in `logger.*` calls — grep 0 hits. Parameterized SQLAlchemy throughout. No virality promise. At render time: subtitle check is safe (guard at render.py:184), background field unused. No injection risk at render time, but data integrity issue upstream. |
| 4 Clip-quality | ok — setup anchoring correct, every path cites a named principle from CLIPPING_PRINCIPLES.md. Issue 103 closed both prior clip-quality SEV2s: (a) `dna_match` collinearity fixed (dna_score separated from composite; tests pin), (b) overlap dedup fixed (greedy IoU NMS @ 0.5; test pins). Honest preference threshold. Cold-start principle mis-attribution captured in Issue 109 #6. |
| 5 Anthropic SDK | ok (architecturally) — two-block system: static instructions lead, per-creator DNA carries cache breakpoint with 1h TTL (scoring.py:205-211); usage incl. cache read/write logged after every call (scoring.py:216-222); `max_tokens=1200`; structured JSON parse with signal-score fallback. 1 SEV2: AsyncAnthropic loop-binding. 1 carry-forward: Haiku 4.5 A/B efficiency gap. |
| 6 Cleanliness & typing | 3 cleanup (unused _BACKGROUND_STYLES dict, incomplete background feature, test missing injection coverage). Carry-forward: signal-array rebuild (Issue 109 #7), keyframe timeout (Issue 109 #8), end_s extension undocumented, cold-start principle (Issue 109 #6), local imports (Issue 109 #4). No TODO/print/debug — grepped 0 hits. Every signature typed. |
| 7 Error handling / API | 1 cleanup (POST /clips/{id}/render should validate RenderStyleIn fields and return 422 on invalid style). HTTP status codes and error messages are safe (no stack trace). |
| 8 Config & paths | ok — `pathlib.Path` throughout; `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` in `.env.example`. Per-call-site model config flagged in carry-forward. |

## Module verdict
NEEDS-WORK — 3 new SEV2s: (1) ranking.py:102 missing `creator_id` predicate (defense-in-depth; free fix), (2) AsyncAnthropic loop-binding under celery `run_async` (needs-runtime-confirm; carry-forward), (3) RenderStyleIn validation missing (allows invalid data into DB, new in this cycle). Plus 1 new cleanup (unused _BACKGROUND_STYLES) and 6 carry-forward cleanups. No blockers. Issue 103 fixes (dna_match collinearity, overlap dedup) remain solid with regression tests.

## Code coverage
| File | Lines | Finding count |
|---|---|---|
| candidates.py | 149 | 1 carry-forward cleanup (end_s extension undocumented) |
| render.py | 219 | 1 carry-forward SEV2 (single-keyframe face), 1 new cleanup (unused _BACKGROUND_STYLES), 1 carry-forward cleanup (keyframe timeout), NEW CONTENT (style_preset parameter + filter dicts at lines 108-128, 182-186) |
| ranking.py | 166 | 1 SEV2 (missing creator_id predicate at line 102), 1 carry-forward cleanup (local imports), NEW CONTENT (generate_and_rank_clips at lines 82-165) |
| scoring.py | 253 | 1 SEV2 (AsyncAnthropic loop-binding at line 23), 1 carry-forward cleanup (signal-array rebuild), 1 carry-forward cleanup (cold-start principle), 1 carry-forward SEV2 (Haiku 4.5 unevaluated). Logging is correct. NEW CONTENT (no changes in this cycle). |
| window.py | 50 | ok — no findings. |

## Issues referenced
- Issue 103: clip-quality fixes (dna_match, overlap dedup) — FIXED + tested
- Issue 109: 10 deferred design-work cleanups (4 in this module: #4 local imports, #5 end_s extension, #6 cold-start principle, #7 signal-array rebuild, #8 keyframe timeout)
- Issue 119: styled renders (new feature in render.py) — has injection-safety guard but validation gap upstream in router
- Issue 112 (recommended filing): Haiku 4.5 A/B eval for clip_scoring

