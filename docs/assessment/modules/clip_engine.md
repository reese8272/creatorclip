# clip_engine — assessed 2026-05-31 (Wave 8)

## Findings
- [SEV2] clip_engine/ranking.py:139 — `dna_match=c.get("score")` (carry-forward from
  Waves 1+2+3+4, still unchanged — `git log -- clip_engine/` last touched at
  `1ffcf7b` Issue C, no Wave 8 commits to this slice). Grep re-confirms a single
  writer to `Clip.dna_match` (this line) and two readers — preference/train.py:56
  and ranking.py:57 — both of which feed it into the preference feature vector
  under the column name `"dna_match"` (preference/features.py:36) as if it were an
  independent DNA-fit feature. It is not: it is a verbatim copy of `c["score"]`,
  the Claude/signal composite that the preference model is trying to predict. The
  inline comment "refined when preference model is trained" remains aspirational —
  no path mutates `dna_match` after creation. Cuts directly against rubric category
  4 ("ranking is against THIS creator's DNA, not a generic score"): the preference
  model is told the composite is the DNA feature, so a real DNA-fit signal is
  silently absent and the feature is collinear with its own label-generating
  signal | fix: have `score_candidates` return BOTH the DNA-only fit (Claude
  `score` field BEFORE any blending; cold-start path returns the signal score
  separately) and the composite. Persist the DNA-only fit to `dna_match` and the
  composite to `score`. Add a unit test asserting that on the DNA path, `dna_match`
  equals the raw Claude score and `score` equals the composite, and that on the
  cold-start path `dna_match` is None (no DNA available) so `clip_features`
  zero-defaults it via the existing `dna_match is not None` branch in
  preference/features.py:24.

- [SEV2] clip_engine/candidates.py:113 — candidate windows still never deduped or
  merged for overlap (carry-forward; unchanged in Wave 8). `find_peaks(distance
  =min_distance_samples)` only enforces spacing between *peaks* (~MIN_CLIP_S = 30s
  apart). The backward setup scan in `_find_setup_start` happily pulls two
  adjacent peaks back to the SAME silence-end boundary inside the WINDOW_S=75s
  lookback, so two distinct peaks 39s apart can both anchor to a silence boundary
  at, say, peak1−40s — yielding two candidate windows whose [setup_start_s,
  end_s] overlap by >80%. The creator can be shown two clips that are essentially
  the same segment. Cuts against principle #9 ("One idea per Short") and wastes
  render/storage budget on duplicates | fix: after the chronological sort at
  candidates.py:113, do an IoU-merge pass: iterate in chronological order, and
  for each candidate compute IoU against the previously kept candidate's
  [setup_start_s, end_s]; if IoU > 0.5 drop the lower-prominence one (track
  original prominence in the dict). Add an eval scenario with two peaks 35s
  apart sharing a single silence boundary, asserting one merged window survives.

- [SEV2] clip_engine/scoring.py:199 (model selection) — `model=settings
  .ANTHROPIC_MODEL` still hardcodes Sonnet 4.6 (`.env.example`) as the single
  source of truth across all 3 Claude call sites, including clip scoring.
  Issue 84 (closed 2026-05-31) explicitly flagged Haiku 4.5 as a ~67%
  cost-reduction opportunity for clip_scoring specifically (deterministic JSON
  shape, short reasoning, narrow scoring task — exactly Haiku's competency band)
  and called for per-call-site model settings + an A/B eval against
  `tests/eval/scenarios/*.yaml`. As of Wave 8, the follow-up is still NOT filed
  as a tracked issue — re-grepped `docs/issues.md`: only line 1725
  ("deliverables to be filed") references Haiku, no tracked issue exists; recent
  issues 92–100 cover SSE / UI / onboarding / catalog-sync / walkthrough /
  insights / transparency — no clip-scoring efficiency item among them. Not
  shipped (rubric category 4 honesty: this is an efficiency finding, not a
  correctness defect — DNA scoring still works on Sonnet 4.6), but the cost gap
  compounds at 10k-creator scale | fix: file as a new tracked issue:
  (1) introduce `ANTHROPIC_MODEL_CLIP_SCORING` config defaulting to current
  Sonnet 4.6, (2) run the eval harness A/B with Haiku 4.5 on the labeled
  scenarios in `tests/eval/scenarios/*.yaml`, asserting setup-start anchoring +
  principle citation parity, (3) flip the default to Haiku 4.5 only if eval
  delta is within noise.

- [SEV2] clip_engine/ranking.py:102 — `select(Clip).where(Clip.video_id ==
  video_id)` has no `creator_id` predicate (defense-in-depth gap). `video_id`
  is unique and the caller path is internal (worker pipeline +
  `routers/clips.py:100` `generate_and_rank_clips(...)`), so this is not a
  live cross-tenant leak today, BUT the rule from CLAUDE.md and the Issue 33
  post-mortem is "filter on creator_id on EVERY query touching a creator-scoped
  table" — it's the only safeguard that survives a future refactor mis-passing
  `video_id`. Function already takes `creator_id: uuid.UUID` as a parameter
  (line 85), so adding the predicate is free | fix: add `.where(Clip.creator_id
  == creator_id)` to the existing-clips probe; add a regression test that asserts
  the existing-clips short-circuit refuses to surface a clip whose `creator_id`
  doesn't match the parameter.

- [SEV2] clip_engine/render.py:82-105 — `_detect_face_center_x` runs Haar
  cascade on a SINGLE keyframe at mid-clip. For a creator who pans/cuts/changes
  shot in the clip window, the mid-frame may not contain the speaker. The 9:16
  reframe is then anchored on whatever frontal face Haar happens to find in that
  one frame — a "default-position" miscrop is invisible until a creator reviews
  dozens of rendered clips. Industry-standard active-speaker reframing samples
  multiple frames | fix: sample 3 keyframes (start+25%, mid, start+75%), run
  detection on each, take the median x of detected centers (fall back to mid
  alone if 0/1 detections). Cheap (3 ffmpeg seeks + 3 Haar passes on 1080p ≈
  <300ms) and matches industry practice for shot-stable reframing.
  (needs-runtime-confirmation that median-of-3 measurably reduces miscrops on
  the eval set before flipping the default — current single-frame is acceptable
  for v1.)

- [SEV2] clip_engine/scoring.py:23 — module-level `AsyncAnthropic(...)`
  singleton is correct per rubric §1, BUT `AsyncAnthropic` binds its underlying
  httpx client to the loop it sees at first use. Under the FastAPI app this is
  fine (one loop). In the Celery `run_async` path
  (`worker/tasks.py:154` `run_async(_render_clip_async)`), each task creates a
  fresh loop, but the singleton's underlying httpx pool was bound to the FIRST
  loop the worker process saw. This is a known async-singleton-in-celery gotcha
  and will manifest as `RuntimeError: Event loop is closed` or stalled
  connections under load | fix: lazy-construct the client per-loop via a
  `contextvars.ContextVar` or `functools.lru_cache(maxsize=1)` keyed on
  `asyncio.get_event_loop()`; OR drop to a sync `Anthropic(...)` client called
  via `asyncio.to_thread`.
  (needs-runtime-confirmation under the actual `run_async` setup before
  refactoring; the symptom is loop-binding errors under concurrency.)

- [cleanup] clip_engine/scoring.py:70 — `compute_features` rebuilds
  `build_signal_array(timeline)` once per candidate (up to 8 full rebuilds of
  the identical array) inside the `_compute_features_all` loop at scoring.py:
  158 (carry-forward; unchanged in Wave 8). The `asyncio.to_thread` offload
  hides the cost from the event loop but still wastes worker CPU and is
  straightforwardly DRY-able | fix: build `(times, signal)` once at the top of
  `score_candidates` (or thread it through from `extract_candidates`, which
  already produces it) and pass it into `compute_features(candidate, timeline,
  signal)`. One array build per video, not per candidate.

- [cleanup] clip_engine/render.py:138 — `_extract_keyframe` is called with
  `timeout_s=render_timeout_s` (= max(120s, 4 × clip_duration)) (carry-forward;
  unchanged in Wave 8). Pulling ONE JPEG frame should never take more than a
  few seconds; binding it to the full render budget means a hung ffmpeg keyframe
  step can chew through the whole 4 × duration budget before the actual encode
  even starts, masking underlying ffmpeg health issues | fix: hardcode a short
  ceiling on the keyframe extraction call — `_extract_keyframe(..., timeout_s
  =30.0)` — and keep `render_timeout_s` for the encode step only.

- [cleanup] clip_engine/candidates.py:99 — `end_s` is recomputed as
  `min(duration_s, max(peak_s + POST_PEAK_S, setup_start_s + MIN_CLIP_S))`,
  which silently extends the clip past the peak's natural payoff window
  whenever the silence-anchored `setup_start_s` is more than `WINDOW_S −
  POST_PEAK_S = 55s` before the peak. This is silent and undocumented; the
  module docstring at candidates.py:1 promises POST_PEAK_S=20s context. Not
  load-bearing for correctness today (the clip still starts at the setup, per
  principle #2), but it diverges from the documented contract | fix: drop the
  `setup_start_s + MIN_CLIP_S` extension, or hoist the min-length filter at
  candidates.py:101 to be the SOLE enforcement (discard rather than silently
  extend). At minimum, update the file-level docstring to describe the actual
  behaviour: "end_s = min(duration_s, max(peak + POST_PEAK_S, setup +
  MIN_CLIP_S)) — extended forward when the silence-anchored setup is too far
  back to satisfy MIN_CLIP_S".

- [cleanup] clip_engine/scoring.py:166 — cold-start path cites `"Retention
  curve is ground truth"` (principle #6) but the cold-start case actually has
  NO retention data (DNA brief is built from analytics; absence of one often
  means absence of the other). The cited principle is mis-attributed in the
  cold-start case — the `_signal_score` weights (density 0.40, hook 0.20,
  retention_spike 0.30, laughter 0.10) actually measure principles #3
  (tension/release) and #4 (pattern interrupt), not #6 | fix: change
  cold-start principle to `"Pattern interrupt"` and update the cold-start
  reasoning string to "Scored on signal density and hook energy — DNA profile
  not available yet." Add a one-line `# why` comment.

- [cleanup] clip_engine/ranking.py:36-38 — local imports of
  `preference.features`, `preference.model`, `preference.train` inside
  `rerank_with_preference` pay an import-cost on EVERY rerank call (not just
  first). At scale on a worker handling many concurrent reranks, this is wasted
  CPU. Grepped: preference module imports nothing from clip_engine, so there's
  no actual circular risk | fix: move the three imports to module top.

- [cleanup] clip_engine/__init__.py — file is empty (0/1 lines per `wc -l`).
  Add a one-line module docstring | fix: `"""Clip engine: candidates →
  scoring → ranking → render."""`.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — keyframe tempfile unlinked in `finally` (render.py:140); out_path cleanup owned by worker caller (worker/tasks.py:617); Anthropic client is a module-level singleton (scoring.py:23) with explicit httpx.Timeout(60s read, 10s connect) + max_retries=2 |
| 2 Concurrency & scale | 1 SEV2 (scoring.py:23 AsyncAnthropic loop-binding under celery `run_async` — needs-runtime). `extract_candidates` wrapped in `asyncio.to_thread` (ranking.py:115); feature computation wrapped in `asyncio.to_thread` (scoring.py:161); Anthropic call is async-native. render.py uses `subprocess.run` synchronously but is only invoked from a Celery worker (worker/tasks.py:607 via `asyncio.to_thread`), so no loop starvation |
| 3 Security & compliance | 1 SEV2 (ranking.py:102 missing `creator_id` predicate, defense-in-depth). No token/PII in any `logger.*` call in slice — grepped: 0 hits. No SQL string-building (parameterized SQLAlchemy throughout). No virality promise anywhere — cold-start reasoning stays honest ("Scored on signal density — DNA profile not available yet."). YouTube ToS not directly touched in this slice (no API calls) |
| 4 Clip-quality | 3 SEV2 (dna_match seed is the composite → preference feature collinear with its label; no overlap dedup → can violate principle #9; Haiku 4.5 A/B opportunity still unfiled per Issue 84 close-out) + 1 cleanup (cold-start principle misattribution). Setup anchoring CORRECT: `_find_setup_start` returns silence END = where speech resumes (principle #2 backward look); worker keys render on `setup_start_s` (worker/tasks.py:610); every path cites a named principle from CLIPPING_PRINCIPLES.md (DNA path → cited per-clip by Claude; cold-start → "Retention curve is ground truth" [mis-attributed, see cleanup]; fallback → same); honest threshold + DNA-fallback on preference rerank verified in `rerank_with_preference` (ranking.py:46–48) |
| 5 Anthropic SDK | ok (architecturally — model-selection efficiency captured as SEV2 above) — two-block system: static instructions lead, per-creator DNA brief carries the cache breakpoint with 1h TTL (scoring.py:201–208), correctly designed per Issue 84 audit; usage incl. cache read/write logged after every call (scoring.py:212); `max_tokens=1200`; structured JSON parse with signal-score fallback on bad JSON. Web-search n/a for this surface |
| 6 Cleanliness & typing | 4 cleanup (signal-array rebuild DRY; over-long keyframe timeout; undocumented end_s extension; local imports in rerank; empty `__init__.py`). No TODO/print/debug — grepped: 0 hits. Every signature typed. Functions under ~30 lines except `score_candidates` (~100 lines) which is the orchestration of the LLM call — within KISS bounds |
| 7 Error handling / API | n/a (not a router) |
| 8 Config & paths | ok — `pathlib.Path` throughout; `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `CLIPS_PER_VIDEO_DEFAULT` all present in `.env.example`; per-call-site model config (`ANTHROPIC_MODEL_CLIP_SCORING`) flagged as the deliverable for the Haiku-A/B follow-up issue |

## Module verdict
NEEDS-WORK — no blockers; isolation, compliance, async hygiene, and
setup-anchoring all remain sound. Wave 8 did not touch this module (`git log
-- clip_engine/` shows last commit `1ffcf7b` Issue C; recent Wave 8 commits
landed in routers/static/insights/walkthrough — nothing in clip_engine/). All
three carry-forward SEV2s persist unchanged: (1) `dna_match` is still a
duplicate of the composite score silently fed to the preference model as an
"independent" feature, (2) candidate windows still aren't merged on overlap,
(3) the Issue-84 Haiku 4.5 A/B follow-up has still not been filed as a tracked
issue. New SEV2s flagged this pass: (4) defense-in-depth `creator_id`
predicate missing on the existing-clips probe in `ranking.py:102`,
(5) single-keyframe face detection in `render.py:82` will miscrop on
shot-changing clips, (6) `AsyncAnthropic` loop-binding under celery
`run_async` (needs-runtime-confirm). Then the cleanups.
