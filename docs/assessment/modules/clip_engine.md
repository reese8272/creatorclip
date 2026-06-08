# clip_engine — assessed 2026-06-08

## Findings

- [SEV2] clip_engine/render.py:219 — `subtitles={ass_path}:fontsdir={_FONTS_DIR}`
  passes the ASS file path into ffmpeg's filter graph with **no libass escaping**.
  libass treats `:`, `\`, `'`, `[`, `]`, `,` inside a filter argument as syntax;
  any of those in `out_path` (and therefore in `ass_path = out_path.with_suffix(...)`)
  break the filter at parse time. The inline comment claims "lives in /tmp/ so colons
  are not a concern" but `out_path` is supplied by the worker — not constrained to
  `/tmp/` — and on dev hosts may include a username, branch name, or creator
  identifier with special characters. | fix: escape via the standard libass dance —
  `path.replace('\\', '\\\\').replace(':', '\\:').replace("'", "\\'")` and wrap in
  single quotes inside the filter, e.g.
  `f"subtitles='{escaped}':fontsdir='{escaped_fonts}'"`. Add a unit test that
  passes a path containing `:` and asserts the rendered filter string survives.

- [SEV2] clip_engine/candidates.py:244-253 — forward sentence-boundary snap can
  push `end_s` up to `max_snap_s` (3.0s) past the video duration. The pre-snap
  line 190 clamps with `min(duration_s, ...)`; the post-snap block re-rounds and
  reapplies the MIN_CLIP_S guarantee but never re-clamps to `duration_s`. A clip
  whose pre-snap end already sat at `duration_s` will be persisted with
  `end_s > duration_s`, then the renderer's `_extract_keyframe(mid_s, ...)` and
  `-t duration` silently truncate — so the persisted boundary lies past the video.
  | fix: after the forward snap, add `c["end_s"] = min(c["end_s"],
  round(duration_s, 2))` and re-run the MIN_CLIP_S floor.

- [SEV2] clip_engine/scoring.py:259-264 — model output parsed with bare
  `json.loads(text)` and no `response_format`/structured-output enforcement. A
  Claude response wrapped in ``` fences, prefixed with prose, or truncated at
  `max_tokens=1200` (8 candidates × ~150 tokens of `reasoning` is on the
  knife-edge) falls into the "warning + signal-only fallback" branch — silently
  discarding the DNA scoring effort and the prompt-cache write. | fix: (a) use
  the Anthropic tool-use / structured-output pattern (`tools=[{...}]`) to force
  a JSON array shape; (b) raise `max_tokens` to 2000 to cover the worst case;
  (c) extend the `json.loads` `except` to also try `text.strip("`").lstrip("json")`
  before falling back so a fenced response is still salvaged.

- [SEV2] clip_engine/scoring.py:23-27 — `_ANTHROPIC = AsyncAnthropic(...)` is
  constructed at import time. In production with `pytest`/celery preforking the
  underlying httpx pool is bound to whichever event loop first touches it; the
  child fork then reuses the parent's connections and the pool will raise
  "got Future attached to a different loop" intermittently under load.
  (needs-runtime-confirmation) | fix: switch to a module-level **lazy** singleton
  via `functools.lru_cache` (`_get_client()`), and ensure celery workers set
  `worker_pool=prefork` + `worker_max_tasks_per_child` so the client is
  re-created per worker process.

- [SEV2] clip_engine/captions.py:160-167 — `_iter_clipped_words` yields words
  twice when transcript segments overlap (WhisperX VAD occasionally emits
  overlapping segments at speaker-change boundaries). The bold_pop renderer
  then emits two `SSAEvent`s for the same word at the same start_ms, which
  libass renders as a flicker / double-scale-pop. | fix: dedupe inside the
  generator — track `seen_start_ms` (or word `id` when present) and skip
  duplicates; or de-dupe segments up-front before any of the `_build_*`
  functions run.

- [SEV2] clip_engine/scoring.py:106 — `_in_window` only checks
  `e.get("start_s")` against `[setup_s, end_s]`. An event whose `start_s` falls
  before `setup_s` but whose `end_s` extends into the window (a long laughter
  burst that begins just before the clip) is excluded from `has_laughter` /
  `silence_duration`. This understates `silence_ratio` and undercounts laughter
  for the very moments the engine cares about most. | fix: change to
  `setup_s <= e.get("end_s", e.get("start_s", 0.0)) and e.get("start_s", 0.0) <= end_s`
  (overlap check, not containment).

- [SEV2] clip_engine/render.py:274 — `render_cleaned_clip_file(timeout_s=120.0)`
  default is too aggressive for a multi-segment clean render of a 90s clip with
  multi-cut filter_complex re-encode. The Issue-134 cut list can produce 10+
  concat segments; libx264 `fast` preset on 1080p with 10 trims will routinely
  exceed 120s on modest workers. (needs-runtime-confirmation) | fix: mirror
  `render_clip_file`'s pattern — accept `clip_duration_s` and compute
  `timeout_s = max(120.0, clip_duration_s * 4)`; or default to 300s and let
  callers tighten when they know the segment count.

- [cleanup] clip_engine/scoring.py:238-247 — the `# type: ignore` comment on
  line 242 masks an SDK-stub lag that has lingered. Anthropic SDK 0.40.0 (current
  pin in requirements.txt) has full `cache_control` support; the ignore is now
  unnecessary. | fix: drop the `# type: ignore[typeddict-unknown-key]` comment
  and re-run mypy to confirm clean output.

- [cleanup] clip_engine/candidates.py:23 — `_TERMINAL_PUNCT` mixes single chars
  and 2-char suffixes (`'."'`, `'?"'`). `_is_sentence_end` does
  `any(text.endswith(p) for p in _TERMINAL_PUNCT)` which is O(N) per word and
  redundant: `endswith(('.', '?', '!', '…', '"'))` over a precomputed tuple is
  simpler and faster, and a closing curly quote (`'."'`) is not covered.
  | fix: simplify to a tuple of single-char terminators
  `_TERMINAL_CHARS = (".", "?", "!", "…", "\"", """, "'", "'")` and check
  `text.endswith(_TERMINAL_CHARS)` once.

- [cleanup] clip_engine/filler.py:140-161 — the nested `for i / for length`
  phrase-matching loop is O(N × max_phrase_len) and rebuilds the lowercased
  phrase per window. For a 90s clip with ~250 words and max_phrase_len=5 this
  is fine, but the in-loop `_normalise_word` call repeats work — every word is
  re-normalised up to 5 times. | fix: pre-compute
  `normalised = [_normalise_word(w["word"]) for w in in_window]` once and index
  into it. Minor; the suite stays well under budget without it.

- [cleanup] clip_engine/render.py:139-260 — `render_clip_file` is ~120 lines
  doing seven things (probe → keyframe → face → crop math → caption build →
  ffmpeg → cleanup). KISS / >30-line rule from CLAUDE.md. | fix: extract
  `_build_caption_filter()` and `_build_vf_chain()` helpers — leaves the
  outer function as orchestration.

- [cleanup] clip_engine/scoring.py:130 — `_signal_score` has the magic constants
  `5.0`, `3.0`, `0.30`, `0.10`, `0.40`, `0.20` inline with no comment tying
  them to a principle. The function does not cite a `CLIPPING_PRINCIPLES.md`
  entry in code (it's named "Retention curve is ground truth" only at the
  call-site in `score_candidates`). | fix: pull the weights into module-level
  constants with docstring + cite the principle once in the function docstring.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — ass_path + script_path both cleaned in `finally`; keyframe tempfile cleaned; no leaks observed. Anthropic client is module-level (with caveat below). |
| 2 Concurrency & scale | 1 finding — module-level `AsyncAnthropic` constructed at import time may bind to the wrong loop under celery prefork (SEV2, needs-runtime-confirmation). `extract_candidates` + `compute_features` correctly offloaded via `asyncio.to_thread` in ranking.py. No N+1. |
| 3 Security & compliance | ok — no PII or token in logs; no f-string SQL; ranking.py queries scoped by `video_id` (worker is responsible for video→creator scoping). No virality promise in any string or prompt. |
| 4 Clip-quality | 2 findings (SEV2) — forward-snap end can exceed `duration_s`; `_in_window` undercounts overlap-style events. Engine correctly cites named principles (#2, #6, #11, #12) and clip start is anchored to `setup_start_s` via `_find_setup_start` (backward look) + sentence-boundary snap. Honest fallback in `rerank_with_preference` when weight == 0. |
| 5 Anthropic SDK | 2 findings (SEV2 + cleanup) — prompt caching present + token usage logged + 1h TTL correct; missing structured-output / tool enforcement on the response parse and `max_tokens=1200` is tight. type: ignore on cache_control no longer needed in SDK 0.40.0. |
| 6 Cleanliness & typing | 3 cleanups — `render_clip_file` ~120 lines; `_signal_score` magic constants; `_normalise_word` recomputed in inner loop. No TODOs or print statements. |
| 7 Error handling / API | n/a (no router surface in this module) |
| 8 Config & paths | ok — all paths absolute (`Path` + tempfile); no new config introduced; `_FONTS_DIR` is the docker-installed location. |

## Module verdict

NEEDS-WORK — clip-engine math, idempotency, and prompt-caching architecture are
sound, but the SEV2 cluster (subtitles-path escaping, forward-snap exceeding
duration, fragile JSON-array parse of the scoring LLM call, possible per-loop
Anthropic client binding under celery prefork, transcript-overlap word dedup,
`_in_window` overlap semantics, and the 120s cleaned-render timeout) remain
unfixed from the prior run and will surface under production load or with
non-trivial transcripts. No BLOCKERs. One cleanup item (SDK type: ignore) is now
resolved by virtue of SDK upgrade to 0.40.0; the remaining 9 items from the
2026-06-07 run persist.
