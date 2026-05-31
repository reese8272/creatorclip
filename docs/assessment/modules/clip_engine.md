# clip_engine — assessed 2026-05-31 (Wave 3)

## Findings
- [SEV2] clip_engine/ranking.py:139 — `dna_match=c.get("score")` (carry-forward from
  2026-05-30 / Wave 1 + 2026-05-31 / Wave 2; unchanged in Wave 3 — `git diff 84a7e9f..HEAD
  -- clip_engine/` is empty, this surface was not touched). Grep re-confirms a single
  writer to `Clip.dna_match` (this line) and two readers — preference/train.py:56 and
  ranking.py:57 — both of which feed it into the preference feature vector under the
  column name `"dna_match"` (preference/features.py:36) as if it were an independent
  DNA-fit feature. It is not: it is a verbatim copy of `c["score"]`, the
  Claude/signal composite that the preference model is trying to predict. The inline
  comment "refined when preference model is trained" remains aspirational — no path
  mutates `dna_match` after creation. Cuts directly against rubric category 4
  ("ranking is against THIS creator's DNA, not a generic score"): the preference
  model is told the composite is the DNA feature, so a real DNA-fit signal is
  silently absent and the feature is collinear with its own label-generating signal |
  fix: have `score_candidates` return BOTH the DNA-only fit (Claude `score` field
  BEFORE any blending; cold-start path returns the signal score separately) and the
  composite. Persist the DNA-only fit to `dna_match` and the composite to `score`.
  Add a unit test asserting that on the DNA path, `dna_match` equals the raw Claude
  score and `score` equals the composite, and that on the cold-start path `dna_match`
  is None (no DNA available) so `clip_features` zero-defaults it via the existing
  `dna_match is not None` branch in preference/features.py:24.

- [SEV2] clip_engine/candidates.py:113 — candidate windows still never deduped or merged
  for overlap (carry-forward from Wave 1 + Wave 2; unchanged in Wave 3 — re-verified).
  `find_peaks(distance=min_distance_samples)` only enforces spacing between *peaks*
  (~MIN_CLIP_S = 30s apart). The backward setup scan in `_find_setup_start` happily
  pulls two adjacent peaks back to the SAME silence-end boundary inside the
  WINDOW_S=75s lookback, so two distinct peaks 39s apart can both anchor to a silence
  boundary at, say, peak1−40s — yielding two candidate windows whose
  [setup_start_s, end_s] overlap by >80%. The creator can be shown two clips that are
  essentially the same segment. Cuts against principle #9 ("One idea per Short") and
  wastes render/storage budget on duplicates | fix: after the chronological sort at
  candidates.py:113, do an IoU-merge pass: iterate in chronological order, and for
  each candidate compute IoU against the previously kept candidate's
  [setup_start_s, end_s]; if IoU > 0.5 drop the lower-prominence one (track original
  prominence in the dict). Add an eval scenario with two peaks 35s apart sharing a
  single silence boundary, asserting one merged window survives.

- [SEV2] clip_engine/scoring.py:199 (model selection) — `model=settings.ANTHROPIC_MODEL`
  still hardcodes Sonnet 4.6 (`.env.example:11`) as the single source of truth across
  all 3 Claude call sites, including clip scoring. Issue 84 (closed 2026-05-31)
  explicitly flagged Haiku 4.5 as a ~67% cost-reduction opportunity for clip_scoring
  specifically (deterministic JSON shape, short reasoning, narrow scoring task —
  exactly Haiku's competency band) and called for per-call-site model settings + an
  A/B eval against `tests/eval/scenarios/*.yaml`. As of Wave 3, the follow-up is
  still NOT filed as a tracked issue (`docs/issues.md` issue numbers 92–100 cover
  SSE/UI/onboarding work; no Haiku/clip-scoring item among them — only
  `docs/PROJECT_STATE.md:16` close-out narrative + `docs/issues.md:1725` deliverables
  list mention it as "to be filed"). Not shipped (rubric category 4 honesty: this is
  an efficiency finding, not a correctness defect — DNA scoring still works on Sonnet
  4.6), but the cost gap compounds at 10k-creator scale | fix: file as a new tracked
  issue: (1) introduce `ANTHROPIC_MODEL_CLIP_SCORING` config defaulting to current
  Sonnet 4.6, (2) run the eval harness A/B with Haiku 4.5 on the labeled scenarios
  in `tests/eval/scenarios/*.yaml`, asserting setup-start anchoring + principle
  citation parity, (3) flip the default to Haiku 4.5 only if eval delta is within
  noise. Defer until after the SDK 0.40 → 0.105.2 bump so TTL-tier observability
  is available to measure cache-read parity across the two models.

- [cleanup] clip_engine/scoring.py:70 — `compute_features` rebuilds
  `build_signal_array(timeline)` once per candidate (up to 8 full rebuilds of the
  identical array) inside the `_compute_features_all` loop at scoring.py:158
  (carry-forward; unchanged in Wave 3). The `asyncio.to_thread` offload hides the
  cost from the event loop but still wastes worker CPU and is straightforwardly
  DRY-able | fix: build `(times, signal)` once at the top of `score_candidates`
  (or thread it through from `extract_candidates`, which already produces it) and
  pass it into `compute_features(candidate, timeline, signal)`. One array build
  per video, not per candidate.

- [cleanup] clip_engine/render.py:138 — `_extract_keyframe` is called with
  `timeout_s=render_timeout_s` (= max(120s, 4 × clip_duration)) (carry-forward;
  unchanged in Wave 3). Pulling ONE JPEG frame should never take more than a few
  seconds; binding it to the full render budget means a hung ffmpeg keyframe step
  can chew through the whole 4 × duration budget before the actual encode even
  starts, masking underlying ffmpeg health issues | fix: hardcode a short ceiling
  on the keyframe extraction call — `_extract_keyframe(..., timeout_s=30.0)` —
  and keep `render_timeout_s` for the encode step only.

- [cleanup] clip_engine/candidates.py:99 — `end_s` is recomputed as
  `min(duration_s, max(peak_s + POST_PEAK_S, setup_start_s + MIN_CLIP_S))`, which
  silently extends the clip past the peak's natural payoff window whenever the
  silence-anchored `setup_start_s` is more than `WINDOW_S − POST_PEAK_S = 55s`
  before the peak. This is silent and undocumented; the module docstring at
  candidates.py:1 promises POST_PEAK_S=20s context. Not load-bearing for
  correctness today (the clip still starts at the setup, per principle #2), but
  it diverges from the documented contract | fix: drop the
  `setup_start_s + MIN_CLIP_S` extension, or hoist the min-length filter at
  candidates.py:101 to be the SOLE enforcement (discard rather than silently
  extend). At minimum, update the file-level docstring to describe the actual
  behaviour: "end_s = min(duration_s, max(peak + POST_PEAK_S, setup +
  MIN_CLIP_S)) — extended forward when the silence-anchored setup is too far
  back to satisfy MIN_CLIP_S".

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — keyframe tempfile unlinked in `finally` (render.py:140); out_path cleanup owned by worker caller (worker/tasks.py); Anthropic client is a module-level singleton (scoring.py:23) with explicit httpx.Timeout(60s read, 10s connect) + max_retries=2 |
| 2 Concurrency & scale | ok — `extract_candidates` wrapped in `asyncio.to_thread` (ranking.py:115); feature computation wrapped in `asyncio.to_thread` (scoring.py:161); Anthropic call is async-native. Wave-3 SSE check: `generate_and_rank_clips` is awaited from `_generate_clips_async` (worker/tasks.py:960) between two `aemit` calls — verified no sync/blocking call introduced on this path; both CPU-bound steps are off-loop. render.py uses `subprocess.run` synchronously but is only invoked from a Celery worker (worker/tasks.py:587 via `asyncio.to_thread`), so no loop starvation |
| 3 Security & compliance | ok — no token/PII handling here; no SQL string-building; idempotency query (ranking.py:101) is `video_id`-scoped (video_id is PK and video is creator-owned); preference `load_latest` is creator-scoped (train.py); no virality promise anywhere — cold-start reasoning stays honest ("Scored on signal density — DNA profile not available yet.") |
| 4 Clip-quality | 3 SEV2 (dna_match seed is the composite → preference feature collinear with its label; no overlap dedup → can violate principle #9; Haiku 4.5 A/B opportunity still unfiled per Issue 84 close-out). Setup anchoring CORRECT: `_find_setup_start` returns silence END = where speech resumes (principle #2 backward look); worker keys render on `setup_start_s` (worker/tasks.py:519,590); every path cites a named principle from CLIPPING_PRINCIPLES.md (DNA path → cited per-clip by Claude; cold-start → "Retention curve is ground truth"; fallback → same); honest threshold + DNA-fallback on preference rerank verified in `rerank_with_preference` (ranking.py:46–48) |
| 5 Anthropic SDK | ok (architecturally — model-selection efficiency captured as SEV2 above) — two-block system: static instructions lead, per-creator DNA brief carries the cache breakpoint with 1h TTL (scoring.py:201–208), correctly designed per Issue 84 audit (`docs/assessment/llm/clip_scoring.md`); usage incl. cache read/write logged after every call (scoring.py:212); `max_tokens=1200`; structured JSON parse with signal-score fallback on bad JSON. Web-search n/a for this surface |
| 6 Cleanliness & typing | 3 cleanup (signal-array rebuild DRY; over-long keyframe timeout; undocumented end_s extension). No TODO/print/debug; all signatures typed |
| 7 Error handling / API | n/a (not a router) |
| 8 Config & paths | ok — pathlib.Path throughout; ANTHROPIC_API_KEY, ANTHROPIC_MODEL, CLIPS_PER_VIDEO_DEFAULT all present in `.env.example`; per-call-site model config (`ANTHROPIC_MODEL_CLIP_SCORING`) flagged as the deliverable for the Haiku-A/B follow-up issue |

## Module verdict
NEEDS-WORK — no blockers; isolation, compliance, async hygiene, and setup-anchoring all remain sound. Wave 3 did not touch this module (`git diff 84a7e9f..HEAD -- clip_engine/` is empty); the new SSE emit sequence in `_generate_clips_async` brackets `generate_and_rank_clips` cleanly — both CPU-bound steps already offload via `asyncio.to_thread`, so no blocking call is introduced on the worker loop during the emit window. All three Wave-1/Wave-2 SEV2s carry forward unchanged: (1) `dna_match` is still a duplicate of the composite score silently fed to the preference model as an "independent" feature, (2) candidate windows still aren't merged on overlap, and (3) the Issue-84 Haiku 4.5 A/B follow-up has not been filed as a tracked issue yet — needs per-call-site model config + eval-harness A/B before flipping clip_scoring off Sonnet 4.6. Then the three carry-forward cleanups (DRY signal array, scoped keyframe timeout, documented end_s extension).
