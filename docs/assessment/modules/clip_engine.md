# clip_engine — assessed 2026-06-16

Issue 138 fix VERIFIED CLOSED: the prior SEV1 (extended-cache-TTL outage risk) is
resolved. `requirements.txt:35` now pins `anthropic==0.105.2`; `scoring.py` was
the only clip_engine file changed since the 2026-06-09 run (commit 41e5eaf). All
nine carried-forward SEV2s were re-verified line-by-line against current code and
remain UNCHANGED — none were touched by the SDK bump. One environment caveat is
logged below (installed SDK still 0.40.0 — `pip install` not run on this branch;
the assessment scores against requirements.txt as source of truth, per the contract).

## Findings

- [SEV1→CLOSED] clip_engine/scoring.py:237-263 — the prior 2026-06-09 SEV1
  (DNA-scoring outage if 1h TTL were gated behind `anthropic-beta:
  extended-cache-ttl-2025-04-11`) is CLOSED by Issue 138. Verified all three
  contract conditions: (a) the now-redundant
  `# type: ignore[typeddict-unknown-key]` on the ttl block was REMOVED
  (scoring.py:242 diff vs da36382 — 0.105.2 stubs model `ttl` first-class);
  (b) the request shape is UNCHANGED — still `cache_control: {"type":
  "ephemeral", "ttl": "1h"}` on the DNA-brief system block only, static
  instructions in the leading uncached block (caching best-practice: stable
  first); (c) token logging now also emits `cached_write_1h` from
  `getattr(_cache_creation, "ephemeral_1h_input_tokens", 0)` (scoring.py:255-262),
  with defensive `or 0` guards for the no-write case. Confirmed via the
  `/claude-api` skill that the 1h TTL is GA — no beta header required on the
  current API, so the prior outage path no longer exists. **Residual (not a
  finding, env note):** the live venv still has 0.40.0 installed; CI/dev must
  `pip install -r requirements.txt` so the 0.105.2 stubs are actually present —
  otherwise mypy would re-flag the removed `type: ignore` as unused. Not a code
  defect.

- [SEV2] clip_engine/ranking.py:101-161 — UNCHANGED. `generate_and_rank_clips`
  idempotency is still check-then-insert: the `existing` SELECT (line 101) and
  the `session.add` loop + `commit` (135-161) are not atomic, and `clips` has no
  unique constraint on `(video_id, rank)`. Two concurrent at-least-once
  deliveries both pass the guard and double-insert the full clip set (and
  double-bill the Claude scoring call). Rubric 1 requires "safe to run twice
  concurrently". | fix: `pg_advisory_xact_lock(hashtext(video_id::text))` at the
  top, or `UniqueConstraint("video_id", "rank")` + `ON CONFLICT DO NOTHING` with
  a re-select; add a test firing two concurrent calls and asserting one clip set.

- [SEV2] clip_engine/render.py:219 — UNCHANGED. `f"subtitles={ass_path}:fontsdir={_FONTS_DIR}"`
  interpolates the ASS path into the filter graph with no libass escaping; `:`,
  `'`, `[`, `]`, `,`, `\` in `out_path` (worker-supplied; only conventionally in
  /tmp — a `TMPDIR` override breaks the assumption) kill the filter at parse
  time. The inline comment at 217-218 acknowledges the /tmp assumption but does
  not fix it. | fix: escape
  `str(ass_path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")`
  and single-quote both args; unit-test with a `:` in the path.

- [SEV2] clip_engine/candidates.py:244-253 — UNCHANGED. The forward
  sentence-boundary snap can push `end_s` up to `max_snap_s` (3.0s) past
  `duration_s`, and the MIN_CLIP_S re-floor at 252 (`setup_start_s + MIN_CLIP_S`)
  can extend it further; the pre-snap clamp `min(duration_s, ...)` (line 190) is
  never re-applied post-snap. A clip ending at video end persists
  `end_s > duration_s`; the renderer silently truncates, so the stored boundary
  lies. | fix: after the snap block, `c["end_s"] = min(c["end_s"], round(duration_s, 2))`
  then re-apply the MIN_CLIP_S floor by moving `setup_start_s` back instead of
  `end_s` forward.

- [SEV2] clip_engine/scoring.py:265-270 — UNCHANGED. Claude scoring response
  parsed with bare `json.loads(text)`, no structured-output enforcement; a
  fenced/prose-prefixed or `max_tokens=1200`-truncated reply (8 candidates ×
  per-item `reasoning` is on the knife-edge) silently discards the entire DNA
  scoring pass via the signal-only fallback (276-291). The user prompt does say
  "Return ONLY a valid JSON array — no prose, no markdown fences" (line 71), but
  that is a soft instruction, not enforcement. | fix: force the array shape with
  the structured-output `output_config.format` json_schema pattern (the 0.105.2
  SDK supports it per the /claude-api skill); raise `max_tokens` to 2000; or at
  minimum strip backtick fences / a leading `json` tag before the fallback.

- [SEV2] clip_engine/scoring.py:23-27 — UNCHANGED. `_ANTHROPIC = AsyncAnthropic(...)`
  built at import time; under Celery prefork the forked child inherits the
  parent's httpx pool and can hit "Future attached to a different loop"
  intermittently under load. (needs-runtime-confirmation) | fix: lazy
  module-level singleton (`@functools.lru_cache def _get_client() -> AsyncAnthropic`),
  and ensure worker config recreates clients per child process.

- [SEV2] clip_engine/scoring.py:105-106 — UNCHANGED. `_in_window` tests
  containment of `start_s` only (`setup_s <= e.get("start_s", 0.0) <= end_s`); a
  laughter/silence event starting just before `setup_s` but extending into the
  clip is dropped from `has_laughter`/`silence_duration`, understating
  `silence_ratio` exactly where it matters. | fix: overlap test —
  `e.get("start_s", 0.0) <= end_s and e.get("end_s", e.get("start_s", 0.0)) >= setup_s`
  (mirror the overlap predicate captions.py:96 already uses).

- [SEV2] clip_engine/captions.py:160-167 — UNCHANGED. `_iter_clipped_words`
  yields a word twice when WhisperX emits overlapping segments at speaker-change
  boundaries; bold_pop then renders two SSAEvents at the same start_ms (flicker /
  double-pop). | fix: dedupe in the generator on a `(start, word)` seen-set.

- [SEV2] clip_engine/render.py:274 — UNCHANGED. `render_cleaned_clip_file`
  default `timeout_s=120.0` and BOTH worker call sites (worker/tasks.py:973-978,
  1060-1065 — re-verified: neither passes `timeout_s`) rely on the default; a
  10+-segment filter_complex re-encode of a 90s clip on a modest worker can
  exceed 120s, failing renders that would have finished. Note `render_clip_file`
  (the primary path) correctly computes `max(120.0, duration*4)` at render.py:171
  — the cleaned path is the asymmetry. (needs-runtime-confirmation) | fix:
  `timeout_s = max(120.0, sum(e - s for s, e in keep_ranges) * 4)`.

- [cleanup] clip_engine/edits.py:152-169 — UNCHANGED. `_invert_cuts`
  re-implements `filler.invert_to_keep_ranges` (filler.py:207-230): same
  cursor-walk inversion, same min-width filter (DRY — second occurrence). | fix:
  fold into one helper in filler.py taking a `min_keep_s` parameter; edits.py
  passes `MIN_KEEP_SEGMENT_S`, filler callers pass 0.

- [cleanup] clip_engine/scoring.py:76-124 — UNCHANGED. `compute_features` rebuilds
  the full signal array (`build_signal_array(timeline)` at line 78) on every call,
  and `_compute_features_all` (192-196) calls it per candidate — O(candidates ×
  duration). | fix: build `(times, signal)` once and pass into `compute_features`.

- [cleanup] clip_engine/candidates.py:26-29 — PARTIALLY ADDRESSED.
  `_TERMINAL_PUNCT` now includes curly quotes and quote-suffixed terminators
  (`…`, `."`, `?"`, `!"`), closing the prior curly-quote gap, but `_is_sentence_end`
  is still an O(N) `any(text.endswith(p) for p in _TERMINAL_PUNCT)` per word. |
  fix: `text.endswith(tuple(_TERMINAL_PUNCT))` — `str.endswith` accepts a tuple
  natively, dropping the comprehension.

- [cleanup] clip_engine/filler.py:140-145 — UNCHANGED. Inner phrase loop
  re-normalises each word up to `max_phrase_len` times via the nested
  `_normalise_word(...)` comprehension. | fix: precompute
  `normalised = [_normalise_word(w.get("word") or "") for w in in_window]` once.

- [cleanup] clip_engine/render.py:139-260 — UNCHANGED. `render_clip_file` is
  ~120 lines doing probe → keyframe → face → crop math → caption build → ffmpeg
  → cleanup (KISS, >30-line rule). | fix: extract `_build_caption_filter()` and
  `_build_vf_chain()`.

- [cleanup] clip_engine/scoring.py:127-133 — UNCHANGED. `_signal_score` magic
  constants (5.0/3.0/0.30/0.10/0.40/0.20) uncommented; the principle citation
  lives only at the call site. | fix: named module-level weight constants + cite
  "Retention curve is ground truth" in the docstring.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | 1 finding — ass_path/script_path/keyframe temp files all cleaned in `finally` (render.py:186-187, 255-257, 358-359); DB session via injected `AsyncSession`, `_ANTHROPIC` is module-level (but import-time, see cat 2); `generate_and_rank_clips` check-then-insert still not safe to run twice concurrently (SEV2). |
| 2 Concurrency & scale | 2 findings — import-time `AsyncAnthropic` loop binding (SEV2, needs-runtime-confirmation); 120s cleaned-render timeout used by both worker call sites (SEV2, re-verified). CPU-bound extraction/scoring correctly offloaded via `asyncio.to_thread` (ranking.py:119, scoring.py:196). Work bounded by `max_candidates=8`. |
| 3 Security & compliance | ok — no PII/token in any logger call (verified every `logger.*`); no SQL strings (ORM only); ranking.py:102 queries by `video_id`, with `creator_id` set from the video row at the worker call site, then persisted on every Clip (ranking.py:139) — internally consistent; adding `Clip.creator_id == creator_id` to the WHERE would be cheap defense-in-depth. No virality promise in any string/prompt (prompt says "fit"; principle #11 "Audience-fit over generic virality"). |
| 4 Clip-quality | 2 findings (SEV2) — post-snap `end_s` can exceed `duration_s` (candidates.py); `_in_window` undercounts boundary-overlap events (scoring.py). Core mechanics sound: setup anchored via backward `_find_setup_start` + sentence snap (principles #2/#12), NMS dedup of overlapping peaks, every scored candidate carries a named principle (DNA path cites Claude's choice with "Audience-fit" fallback; cold-start cites "Retention curve is ground truth"), DNA-vs-cold-start paths honest (`dna_match=None` below DNA), `rerank_with_preference` returns ranking untouched below threshold or on model failure (ranking.py:44-70). |
| 5 Anthropic SDK | ok (prior SEV1 CLOSED) + 1 SEV2 — `ttl:"1h"` + cache_control now ride on the GA-typed 0.105.2 SDK, no beta header, `type: ignore` removed, `cached_write_1h` logged; caching architecture correct (stable-first two-block system, breakpoint on the volatile DNA brief); token usage logged after the call. Remaining: response parse still bare `json.loads` with no structured-output enforcement and tight `max_tokens=1200` (SEV2). |
| 6 Cleanliness & typing | 5 cleanups — edits/filler inversion DRY, per-candidate signal rebuild, terminal-punct matcher (now O(N) endswith only — curly quotes fixed), filler re-normalisation, 120-line `render_clip_file`, magic score weights. No TODOs, no prints, all signatures typed. |
| 7 Error handling / API | n/a (no router surface; `CutValidationError.code` gives routers a clean mapping) |
| 8 Config & paths | ok — absolute paths via `Path`/tempfile throughout; no new config introduced this branch; `_FONTS_DIR` matches the Dockerfile install location. |

## Module verdict

NEEDS-WORK — the Issue 138 SEV1 is genuinely closed (SDK bumped to 0.105.2, ttl
GA, type-ignore removed, 1h cache-write tier logged) and the clip-quality core
remains sound, but the entire 2026-06-09 SEV2 cluster (concurrent-insert race,
libass path escaping, post-snap `end_s > duration`, bare `json.loads` on the
scoring response, import-time client loop binding, `_in_window` boundary
undercount, caption word dup, 120s cleaned-render timeout) is carried forward
untouched, and one env note stands: the live venv must actually install the
0.105.2 pin or mypy will flag the removed `type: ignore`.

## Issue 75 Reconciliation (2026-06-23)

| Finding | Disposition |
|---|---|
| [SEV2] generate_and_rank_clips concurrent check-then-insert (ranking.py:101-161) | → tracked in Issue 76 (post-hardening residual SEV-2 cluster) |
| [SEV2] libass path escaping (render.py:219) | → tracked in Issue 76 |
| [SEV2] post-snap end_s > duration_s (candidates.py:244-253) | → tracked in Issue 76 |
| [SEV2] bare json.loads on scoring response (scoring.py:265-270) | → tracked in Issue 222 (tool-result is_error flag + structured output) |
| [SEV2] AsyncAnthropic import-time loop binding (scoring.py:23-27) | → tracked in Issue 82 (async migration wave 2) |
| [SEV2] _in_window boundary undercount (scoring.py:105-106) | → tracked in Issue 76 |
| [SEV2] caption word duplicate at speaker-change boundary (captions.py:160-167) | → tracked in Issue 76 |
| [SEV2] cleaned-render 120s timeout asymmetry (render.py:274) | → tracked in Issue 76 |
| [cleanup] _invert_cuts DRY (edits.py:152-169) | → tracked in Issue 109 (deferred design cleanups) |
| [cleanup] per-candidate signal rebuild (scoring.py:76-124) | → tracked in Issue 109 |
| [cleanup] terminal-punct matcher O(N) (candidates.py:26-29) | → tracked in Issue 109 |
| [cleanup] filler re-normalisation (filler.py:140-145) | → tracked in Issue 109 |
| [cleanup] render_clip_file too long (render.py:139-260) | → tracked in Issue 109 |
| [cleanup] magic score weights (scoring.py:127-133) | → tracked in Issue 109 |
