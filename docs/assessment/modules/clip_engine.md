# clip_engine — assessed 2026-05-29

Slice: `clip_engine/candidates.py`, `ranking.py`, `render.py`, `scoring.py`,
`window.py`, `__init__.py`. Load-bearing docs cross-checked:
`docs/CLIPPING_PRINCIPLES.md`, `docs/COMPLIANCE.md`.

## Findings

- [SEV1] worker/tasks.py:291 (render call) + clip_engine/render.py:108 — the
  render task cuts the clip from `clip.start_s`, which `candidates.py:97` defines
  as the *hard fallback* (`max(0.0, peak_s - WINDOW_S)`, i.e. always 75s before
  the peak), NOT `clip.setup_start_s`, the backward-found content boundary that is
  the engine's core differentiator (principle #2 "Clip the setup, not the
  aftermath", `docs/CLIPPING_PRINCIPLES.md`). The rendered Short therefore does
  not start at the setup the engine computed and advertises; it starts at a fixed
  -75s offset. The scoring, the API response (`routers/clips.py:25`), and the
  clip-quality eval all key on `setup_start_s`, but the actual bytes do not.
  | fix: pass `start_s=clip.setup_start_s` to `render_clip_file` in
  `worker/tasks.py:291` (keep `end_s=clip.end_s`). Add a clip-quality eval
  assertion in `tests/eval/` that the *rendered* segment start equals
  `setup_start_s`, not `start_s`. If `start_s` is meant only as a clamp guard,
  document that in `docs/DECISIONS.md` and still render from `setup_start_s`.

- [SEV1] clip_engine/ranking.py:26-62 — `rerank_with_preference()` is never
  called by any caller (`generate_and_rank_clips` at :65 ranks on the raw DNA/
  signal score only; grep shows zero invocations outside its own def and tests).
  The preference model (recency-decayed reranker) is therefore not applied in the
  live ranking path, violating the Clip-Engine rule "Ranking reflects DNA +
  (above threshold) preference model" and "preference model weights recent
  feedback more heavily." Consequently `dna_match` is seeded to the DNA score at
  ranking.py:101 and the promised refinement ("refined when preference model is
  trained") never happens. | fix: call `rerank_with_preference(clips, session,
  creator_id)` at the end of `generate_and_rank_clips` (after persist/refresh,
  re-persisting blended `score`/`rank`), gated on `load_latest` returning a model
  (it already falls back silently below threshold). Add an integration test
  asserting a trained model reorders clips and an untrained creator falls back to
  DNA order.

- [SEV1] clip_engine/ranking.py:56 — the preference blend is a hardcoded
  `0.5 * dna + 0.5 * pref` equal weight. CLAUDE.md / Clip-Engine rules require a
  communicated personalization threshold and honest below-threshold fallback;
  a fixed 50/50 blend with no confidence weighting means a freshly-trained,
  low-data model immediately gets equal authority over ranking. | fix: weight the
  preference contribution by model maturity (e.g. label count or CV score from
  `preference/train`), and record the chosen blend + threshold in
  `docs/DECISIONS.md`. (needs cross-module confirmation of what `load_latest`
  exposes — keep within clip_engine by reading a maturity field off the scorer.)

- [SEV2] clip_engine/scoring.py:217 — when Claude returns a `principle` string,
  it is trusted verbatim and stored; there is no validation that it is one of the
  11 names in `_PRINCIPLES` / `docs/CLIPPING_PRINCIPLES.md`. A hallucinated or
  reworded principle silently violates the rule "Every clip score cites a named
  principle from the registry." | fix: validate `hit.get("principle")` against
  the `_PRINCIPLES` set; if not a member, fall back to
  "Audience-fit over generic virality" (as the default already does) and log a
  warning. Add a structural test asserting every persisted clip's principle is in
  the registry.

- [SEV2] clip_engine/scoring.py:188 — the Anthropic call sets `max_tokens=1200`
  for an unbounded number of candidates (default 8, but `max_candidates` is
  caller-controlled). With long `transcript_excerpt`s and many candidates the JSON
  array can be truncated mid-array, producing a `JSONDecodeError` that drops ALL
  LLM scores to the signal-only fallback (the `score_map` ends up partial/empty).
  No structured-output / tool-use enforcement is used. | fix: scale `max_tokens`
  with candidate count (e.g. `200 + 90 * len(candidates)`), or use the Anthropic
  tool-use / structured-output path to force a well-formed array per the
  `/claude-api` skill; on truncation, retry once before falling back. (Prompt
  caching IS present at :191 and tokens ARE logged at :195 — those rubric items
  pass.)

- [SEV2] clip_engine/render.py:152-156 — ffmpeg uses `-ss <start>` placed BEFORE
  `-i` (input seeking) with `libx264` re-encode but no `-accurate_seek` /
  output-seek pairing; fast input seek lands on the nearest keyframe, so the
  actual cut can drift up to one GOP (often 1-2s) from the requested
  `setup_start_s`. For a hook that must land "in the first 3 seconds"
  (principle #1) and start exactly at the setup (principle #2), seconds of drift
  is clip-quality-significant. | fix: use accurate seeking — either keep `-ss`
  before `-i` plus `-accurate_seek` and an output `-ss 0`, or move `-ss`/`-t`
  after `-i` (slower but frame-accurate); verify against the eval harness that the
  rendered start matches `setup_start_s` within one frame.

- [SEV2] clip_engine/render.py:82-105 — face center is detected from a SINGLE
  keyframe at the clip midpoint (`render.py:137`). For an active-speaker reframe
  over a 30-90s clip this fixes the 9:16 crop on whoever's face is at the
  midpoint; if the speaker moves or the shot changes, the subject leaves frame for
  the rest of the clip. The module docstring claims "active-speaker" reframe but
  the implementation is "single-frame largest-face." | fix: sample faces at
  several offsets (e.g. every 5s) and either track or pick a stable crop window;
  at minimum, rename/redocument to "single-keyframe face crop" and log the
  limitation in `docs/DECISIONS.md` so the claim is honest. (needs-runtime-
  confirmation of acceptable quality at scale.)

- [SEV2] clip_engine/scoring.py:126-135 — `_transcript_excerpt` truncates the
  window text to 300 chars but only includes segments fully contained in
  `[setup_s, end_s]` (`seg.start >= setup_s and seg.end <= end_s`). A segment that
  straddles `setup_s` (the very hook line) is dropped, so the LLM frequently
  scores the clip without its opening words — the most retention-relevant text.
  | fix: include any segment that OVERLAPS the window
  (`seg.end > setup_s and seg.start < end_s`), then trim. Keep the 300-char cap.

- [cleanup] clip_engine/render.py:103 — `except Exception` is bare/broad around
  face detection; it will swallow e.g. a `KeyboardInterrupt`-adjacent or
  programming error as "face detection failed." | fix: narrow to
  `(cv2.error, ValueError, OSError)` plus `ImportError` for the optional cv2
  import; let unexpected errors propagate.

- [cleanup] clip_engine/ranking.py:36-37 + clips.py:60 + scoring.py — repeated
  function-local imports (`from preference... import` inside `rerank_with_
  preference`, `from dna.profile import get_active` inside the router). These are
  presumably to dodge import cycles, but they are undocumented. | fix: if the
  cycle is real, add a one-line WHY comment; otherwise hoist to module top per
  PEP 8 / project style.

- [cleanup] clip_engine/candidates.py:14-16 vs window.py:7 / scoring.py — the
  magic constants `WINDOW_S=75`, `POST_PEAK_S=20`, `MIN_CLIP_S=30`,
  `RESOLUTION_S=0.5`, and the cold-start score divisors (`/5.0`, `/3.0`) and
  weights (`0.40/0.20/0.30/0.10` at scoring.py:119-123) are scattered tunables
  with no single registry and no DECISIONS justification for the values.
  | fix: centralize clip tunables in `config.py` (pydantic-settings) or one
  module constant block, and record the chosen values + rationale in
  `docs/DECISIONS.md` so scoring math is auditable per the "named principle / no
  guessing" rule.

- [cleanup] clip_engine/scoring.py:69 — `timeline.get("duration_s", 0.0)` and the
  same lookup recurs in window.py:28 and candidates.py:91; `build_signal_array`
  is also recomputed in both `extract_candidates` and `compute_features` per
  candidate (scoring.py:67 calls it inside `compute_features`, which is called
  once per candidate at ranking time). For 8 candidates that rebuilds the whole
  signal array 8× (DRY + minor inefficiency). | fix: build the signal array once
  in `score_candidates` and pass `(times, signal)` into `compute_features`.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — keyframe/out temp files cleaned in `finally` (render.py:140, tasks.py:296); Anthropic client is a module singleton (scoring.py:22) |
| 2 Concurrency & scale | ok for this slice — `subprocess.run` ffmpeg correctly runs only inside the Celery render task, not on the API loop; the LLM call is `await`ed via AsyncAnthropic. `build_signal_array` recomputed per candidate (1 cleanup, not a scale blocker at 8 candidates) |
| 3 Security & compliance | ok — no tokens/PII handled in this module; no raw SQL (ORM only at ranking.py:89); creator_id is stamped from caller, and the delete is scoped by `video_id` whose ownership is checked upstream in routers/clips.py. No virality promise in any string/prompt (verified scoring.py prompts) |
| 4 Clip-quality | 3 findings — render uses `start_s` not `setup_start_s` (SEV1, defeats principle #2); preference rerank never invoked (SEV1); principle not validated against registry + inaccurate ffmpeg seek + midpoint-only face + dropped hook transcript (SEV2s) |
| 5 Anthropic SDK | 1 finding — prompt caching present + tokens logged (pass); but `max_tokens=1200` can truncate JSON and no structured-output/tool-use enforcement (SEV2) |
| 6 Cleanliness & typing | 4 cleanups — broad except, function-local imports, scattered magic constants, recomputed signal array; signatures are typed |
| 7 Error handling / API | n/a — no routers in this slice (endpoints live in routers/clips.py) |
| 8 Config & paths | partial — paths are `Path` objects passed in (ok); but clip tunables are hardcoded module constants, not pydantic-settings config with `.env.example` entries (folded into cleanup above) |

## Module verdict
NEEDS-WORK — no cross-tenant or resource BLOCKER, but two SEV1 clip-quality
defects undermine the product's core promise: the render cuts from the fixed
`start_s` fallback instead of the computed `setup_start_s` (so Shorts do not
actually "clip the setup"), and the preference reranker is dead code never wired
into the live ranking path.
