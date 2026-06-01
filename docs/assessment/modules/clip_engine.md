# clip_engine — assessed 2026-05-31 (Wave 9)

## Findings
- [SEV2] clip_engine/ranking.py:102 — `select(Clip).where(Clip.video_id ==
  video_id)` still has no `creator_id` predicate (defense-in-depth gap;
  carry-forward from Wave 8, unchanged). `video_id` is unique and the caller
  path is internal (worker pipeline + `routers/clips.py` →
  `generate_and_rank_clips(...)`), so this is not a live cross-tenant leak
  today, BUT the rule from `CLAUDE.md` and the Issue 33 post-mortem is "filter
  on creator_id on EVERY query touching a creator-scoped table" — it's the
  only safeguard that survives a future refactor mis-passing `video_id`. The
  function already takes `creator_id: uuid.UUID` as a parameter
  (`clip_engine/ranking.py:85`), so adding the predicate is free | fix: add
  `.where(Clip.creator_id == creator_id)` to the existing-clips probe; add a
  regression test that asserts the existing-clips short-circuit refuses to
  surface a clip whose `creator_id` doesn't match the parameter.

- [SEV2] clip_engine/render.py:82-105 — `_detect_face_center_x` still runs
  Haar cascade on a SINGLE keyframe at mid-clip (carry-forward; unchanged).
  For a creator who pans/cuts/changes shot inside the clip window, the
  mid-frame may not contain the speaker. The 9:16 reframe is then anchored
  on whatever frontal face Haar happens to find in that one frame — a
  "default-position" miscrop is invisible until a creator reviews dozens of
  rendered clips. Industry-standard active-speaker reframing samples
  multiple frames | fix: sample 3 keyframes (start+25%, mid, start+75%),
  run detection on each, take the median x of detected centers (fall back
  to mid alone if 0/1 detections). Cheap (3 ffmpeg seeks + 3 Haar passes
  on 1080p ≈ <300ms) and matches industry practice for shot-stable
  reframing. (needs-runtime-confirmation that median-of-3 measurably
  reduces miscrops on the eval set before flipping the default — current
  single-frame is acceptable for v1.)

- [SEV2] clip_engine/scoring.py:23 — module-level `AsyncAnthropic(...)`
  singleton is correct per rubric §1, BUT `AsyncAnthropic` binds its
  underlying httpx client to the loop it sees at first use. Under the
  FastAPI app this is fine (one loop). In the Celery `run_async` path
  (`worker/tasks.py` → `run_async(_render_clip_async)` / related), each
  task creates a fresh loop, but the singleton's underlying httpx pool
  was bound to the FIRST loop the worker process saw. This is a known
  async-singleton-in-celery gotcha and will manifest as `RuntimeError:
  Event loop is closed` or stalled connections under load
  (carry-forward; unchanged in Wave 9) | fix: lazy-construct the client
  per-loop via a `contextvars.ContextVar` or `functools.lru_cache
  (maxsize=1)` keyed on `id(asyncio.get_event_loop())`; OR drop to a sync
  `Anthropic(...)` client called via `asyncio.to_thread`.
  (needs-runtime-confirmation under the actual `run_async` setup before
  refactoring; the symptom is loop-binding errors under concurrency.)

- [SEV2] clip_engine/scoring.py:203 (model selection) —
  `model=settings.ANTHROPIC_MODEL` still hardcodes Sonnet 4.6 as the single
  source of truth across all 3 Claude call sites including clip scoring.
  Issue 84 (closed 2026-05-31) flagged Haiku 4.5 as a ~67% cost-reduction
  opportunity for clip_scoring specifically (deterministic JSON shape,
  short reasoning, narrow scoring task — exactly Haiku's competency band)
  and called for per-call-site model settings + an A/B eval against
  `tests/eval/scenarios/*.yaml`. As of Wave 9, the follow-up remains
  **partially tracked but not yet acted on**: `docs/issues.md:1725`
  recorded the deliverable, Issue 109 (`docs/issues.md:2522`, filed
  2026-05-31 as Issue 108 follow-up) collected 10 deferred design-work
  cleanups — **but the Haiku 4.5 A/B is not among Issue 109's 10 items**
  (re-read all 10 — it's `_enrich_videos` split, `_fernet()` lru_cache,
  lifespan registry, `Settings.psycopg_dsn`, fetch-then-validate rewrite,
  cold-start principle misattribution, build_signal_array rebuild,
  keyframe timeout, decay `_LAMBDA`, `dna/conflict.py` keyword
  coverage). The Haiku A/B item from
  `docs/assessment/llm/clip_scoring.md:68` ("File as Issue (post-84)
  'Evaluate Haiku 4.5 for clip scoring'") has still not been promoted to a
  numbered, tracked issue. Not a correctness defect — DNA scoring still
  works on Sonnet 4.6 — but the cost gap compounds at 10k-creator scale |
  fix: file a tracked numbered issue (e.g. Issue 111): (1) introduce
  `ANTHROPIC_MODEL_CLIP_SCORING` config defaulting to current Sonnet 4.6,
  (2) run the eval harness A/B with Haiku 4.5 on the labeled scenarios in
  `tests/eval/scenarios/*.yaml`, asserting setup-start anchoring +
  principle-citation parity + Top-1 principle agreement, (3) flip the
  default to Haiku 4.5 only if eval delta is within noise.

- [cleanup] clip_engine/scoring.py:68 — `compute_features` rebuilds
  `build_signal_array(timeline)` once per candidate (up to 8 full rebuilds
  of the identical array) inside the `_compute_features_all` loop at
  `clip_engine/scoring.py:157`. Carry-forward — explicitly captured in
  Issue 109 item #7 (`docs/issues.md:2533`) for measurement-first
  treatment. The `asyncio.to_thread` offload hides the cost from the event
  loop but still wastes worker CPU and is straightforwardly DRY-able |
  fix (per Issue 109 #7): build `(times, signal)` once at the top of
  `score_candidates` (or thread it through from `extract_candidates`,
  which already produces it) and pass it into `compute_features(candidate,
  timeline, signal)`. One array build per video, not per candidate.

- [cleanup] clip_engine/render.py:138 — `_extract_keyframe` is called
  with `timeout_s=render_timeout_s` (= max(120s, 4 × clip_duration)).
  Pulling ONE JPEG frame should never take more than a few seconds;
  binding it to the full render budget means a hung ffmpeg keyframe step
  can chew through the whole 4 × duration budget before the actual encode
  even starts, masking underlying ffmpeg health issues. Carry-forward —
  captured in Issue 109 item #8 (`docs/issues.md:2534`) | fix (per Issue
  109 #8): hardcode a short ceiling on the keyframe extraction call —
  `_extract_keyframe(..., timeout_s=30.0)` — and keep `render_timeout_s`
  for the encode step only.

- [cleanup] clip_engine/candidates.py:104 — `end_s` is computed as
  `min(duration_s, max(peak_s + POST_PEAK_S, setup_start_s + MIN_CLIP_S))`,
  which silently extends the clip past the peak's natural payoff window
  whenever the silence-anchored `setup_start_s` is more than `WINDOW_S −
  POST_PEAK_S = 55s` before the peak. This is silent and undocumented;
  the module docstring at `clip_engine/candidates.py:1` promises
  POST_PEAK_S=20s context. Not load-bearing for correctness today (the
  clip still starts at the setup per principle #2), but it diverges from
  the documented contract | fix: drop the `setup_start_s + MIN_CLIP_S`
  extension, or hoist the min-length filter at
  `clip_engine/candidates.py:106` to be the SOLE enforcement (discard
  rather than silently extend). At minimum, update the file-level
  docstring to describe the actual behaviour: "end_s = min(duration_s,
  max(peak + POST_PEAK_S, setup + MIN_CLIP_S)) — extended forward when
  the silence-anchored setup is too far back to satisfy MIN_CLIP_S".

- [cleanup] clip_engine/scoring.py:170 — cold-start path cites
  `"Retention curve is ground truth"` (principle #6) but the cold-start
  case actually has NO retention data (DNA brief is built from
  analytics; absence of one often means absence of the other). The
  cited principle is mis-attributed in the cold-start case — the
  `_signal_score` weights (density 0.40, hook 0.20, retention_spike
  0.30, laughter 0.10) actually measure principles #3 (tension/release)
  and #4 (pattern interrupt), not #6. Carry-forward — captured in Issue
  109 item #6 (`docs/issues.md:2532`) | fix (per Issue 109 #6): semantic
  decision required; recommendation is `"Pattern interrupt"` and update
  the cold-start reasoning string to "Scored on signal density and hook
  energy — DNA profile not available yet."

- [cleanup] clip_engine/ranking.py:36-38 — local imports of
  `preference.features`, `preference.model`, `preference.train` inside
  `rerank_with_preference` pay an import cost on EVERY rerank call (not
  just first). At scale on a worker handling many concurrent reranks,
  this is wasted CPU. Grepped: preference module imports nothing from
  clip_engine, so there's no actual circular risk. Carry-forward;
  unchanged in Wave 9 | fix: move the three imports to module top.

## Wave 9 verification (Issue 103 fixes — re-checked, confirmed FIXED)
- **(a) `dna_match` collinearity** — RESOLVED. `clip_engine/scoring.py:241`
  now reads a separate `dna_score` field from Claude's response, clamps it
  to [0,1], and assigns it to `c["dna_match"]` independently of the
  composite `c["score"]`. Cold-start path at
  `clip_engine/scoring.py:169` sets `c["dna_match"] = None` (graceful
  zero-default via `preference/features.py:24` `dna_match if dna_match is
  not None else 0.0`). `clip_engine/ranking.py:142` persists
  `dna_match=c.get("dna_match")` — no longer the composite. Regression
  tests `tests/test_scoring.py:268`
  (`test_score_candidates_separates_dna_match_from_composite`) and
  `tests/test_scoring.py:297`
  (`test_score_candidates_cold_start_dna_match_is_none`) pin the
  invariant. The collinearity finding from Waves 1-8 is now closed.
- **(b) Overlap dedup** — RESOLVED. `clip_engine/candidates.py:119-140`
  now runs a greedy IoU pass in prominence-descending order, suppressing
  any candidate whose [setup_start_s, end_s] window has IoU > 0.5 against
  an already-kept candidate. Threshold and algorithm are canonical
  (SumMe/TVSum + standard object-detection NMS); inline comment cites
  the source. Regression test `tests/test_clip_engine.py:274`
  (`test_candidates_dedups_overlapping_windows`) pins the behaviour with
  two peaks sharing a single silence boundary. The principle-#9
  violation surfaced in Waves 1-8 is now closed.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — keyframe tempfile unlinked in `finally` (`clip_engine/render.py:140`); out_path cleanup owned by worker caller; Anthropic client is a module-level singleton (`clip_engine/scoring.py:23`) with explicit `httpx.Timeout(60s read, 10s connect)` + `max_retries=2`. |
| 2 Concurrency & scale | 1 SEV2 (`clip_engine/scoring.py:23` AsyncAnthropic loop-binding under celery `run_async` — needs-runtime-confirm; carry-forward). `extract_candidates` wrapped in `asyncio.to_thread` (`clip_engine/ranking.py:115`); feature computation wrapped in `asyncio.to_thread` (`clip_engine/scoring.py:161`); Anthropic call is async-native. `render.py` uses `subprocess.run` synchronously but is invoked only from the Celery worker via `asyncio.to_thread`, so no loop starvation. |
| 3 Security & compliance | 1 SEV2 (`clip_engine/ranking.py:102` missing `creator_id` predicate on the existing-clips probe — defense-in-depth, not live leak; carry-forward). No token/PII in any `logger.*` call in slice — grep returns 0 hits. No SQL string-building (parameterized SQLAlchemy throughout). No virality promise anywhere — cold-start reasoning stays honest ("Scored on signal density — DNA profile not available yet."). YouTube ToS not directly touched in this slice. |
| 4 Clip-quality | 1 SEV2 (Haiku 4.5 A/B opportunity still not promoted to a tracked numbered issue — Issue 109's 10 items do NOT include it) + 1 cleanup (cold-start principle misattribution, Issue 109 #6). Issue 103 closed BOTH prior Wave-8 clip-quality SEV2s: (a) `dna_match` collinearity now FIXED (`scoring.py:241` separates `dna_score` from composite; tests pin), (b) overlap dedup now FIXED (`candidates.py:119` greedy IoU NMS @ 0.5; test pins). Setup anchoring remains correct: `_find_setup_start` returns silence END (principle #2 backward look); every path cites a named principle from `CLIPPING_PRINCIPLES.md`; honest threshold + DNA fallback on preference rerank verified at `clip_engine/ranking.py:48`. |
| 5 Anthropic SDK | ok (architecturally — model-selection efficiency captured as SEV2 above) — two-block system: static instructions lead, per-creator DNA brief carries the cache breakpoint with 1h TTL (`clip_engine/scoring.py:205-211`), correctly designed per Issue 84 audit; usage incl. cache read/write logged after every call (`clip_engine/scoring.py:216`); `max_tokens=1200`; structured JSON parse with signal-score fallback on bad JSON. Web-search n/a for this surface. |
| 6 Cleanliness & typing | 4 cleanup (signal-array rebuild — Issue 109 #7; over-long keyframe timeout — Issue 109 #8; undocumented end_s extension; local imports in rerank). No TODO/print/debug — grepped: 0 hits. Every signature typed. Functions under ~30 lines except `score_candidates` (~110 lines, orchestration of the LLM call — within KISS bounds). `__init__.py` docstring landed in Issue 108. |
| 7 Error handling / API | n/a (not a router) |
| 8 Config & paths | ok — `pathlib.Path` throughout; `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` present in `.env.example`; per-call-site model config (`ANTHROPIC_MODEL_CLIP_SCORING`) flagged as the deliverable for the still-unfiled Haiku-4.5 A/B follow-up issue. |

## Module verdict
NEEDS-WORK — no blockers. Wave 9 (Issue 103) materially improved this
module: both standing clip-quality SEV2s from Waves 1-8 — the `dna_match`
collinearity into the preference feature vector AND the candidate overlap
that could yield near-duplicate clips — are now closed with regression
tests. Remaining concerns are 3 carry-forward SEV2s: (1) defense-in-depth
`creator_id` predicate still missing on `ranking.py:102`'s existing-clips
probe (free fix; `creator_id` is already in scope), (2) single-keyframe
face detection in `render.py:82` will miscrop on shot-changing clips
(needs-runtime-confirm), (3) `AsyncAnthropic` singleton's loop-binding
under celery `run_async` (needs-runtime-confirm), plus (4) the Issue-84
Haiku 4.5 A/B follow-up STILL hasn't been promoted to a tracked numbered
issue — Issue 109 collected 10 deferred design-work cleanups but Haiku
is not among them, leaving the audit chain `Issue 84 close-out → DECISIONS
follow-up → tracked issue` broken at the last step. Then the cleanups,
most of which are explicitly captured in Issue 109.
