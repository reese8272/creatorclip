# clip_engine — assessed 2026-05-29

## Findings
- [SEV2] clip_engine/ranking.py:129 — `dna_match=c.get("score")` is seeded to the
  composite Claude/signal score and is NEVER refined afterward (only writer in the
  whole codebase; grep confirms reads at preference/train.py:55 and
  ranking.py:56, no second writer). The inline comment "refined when preference
  model is trained" is false. The preference feature vector therefore feeds the
  initial blended score in as a "DNA-fit" feature, so a genuine DNA-fit signal is
  absent and the feature is collinear with the label-generating score | fix: store
  a true DNA-fit value distinct from the composite — e.g. have score_candidates
  return the DNA-only fit (the Claude `score` before any signal blend) and persist
  it to `dna_match`, keeping `clip.score` as the ranked composite; OR drop the
  misleading comment and rename the column/feature to `seed_score` so the
  preference model is not told a duplicate of its own target is an independent
  feature. Add a unit test asserting dna_match carries DNA-only fit, not the
  composite score.

- [SEV2] clip_engine/candidates.py:94-113 — candidate windows are never deduped or
  merged for overlap. `find_peaks(distance=min_distance_samples)` only enforces
  spacing between *peaks*; the backward setup scan in `_find_setup_start` can pull
  two adjacent peaks' `setup_start_s` into heavily overlapping (near-identical)
  windows, so the creator can be shown two clips that are essentially the same
  segment. Cuts against principle #9 "One idea per Short" | fix: after the
  chronological sort at line 113, drop a candidate whose [setup_start_s, end_s]
  overlaps the previous kept candidate by more than a threshold (e.g. >50% IoU),
  keeping the higher-prominence one; add an eval scenario with two close peaks
  asserting a single merged window.

- [SEV2] routers/clips.py:67 (caller of clip_engine.ranking.generate_and_rank_clips)
  — the synchronous CPU work in `extract_candidates` (numpy array build +
  scipy.signal.find_peaks over `duration_s/0.5` samples) and `compute_features`
  runs directly on the FastAPI event loop inside the request handler. For a
  multi-hour source this is a non-trivial blocking-CPU stall on the loop that
  serves all other requests (rubric 2) | fix: this CPU path belongs on the worker
  (the equivalent worker/tasks.py:_generate_clips_async already exists). Either
  dispatch generation to Celery and return 202, or wrap the candidate/feature CPU
  in `await asyncio.to_thread(...)`. (needs-runtime-confirmation on stall magnitude
  for typical 10–30 min sources.)

- [cleanup] clip_engine/scoring.py:68 — `compute_features` calls
  `build_signal_array(timeline)` once per candidate (up to CLIPS_PER_VIDEO_DEFAULT=8
  full rebuilds of the same array) inside the `score_candidates` loop at line 153
  (DRY/KISS) | fix: build the signal array once in `score_candidates` (or reuse the
  `(times, signal)` already produced in `extract_candidates`) and pass slice indices
  into `compute_features`.

- [cleanup] clip_engine/scoring.py:182-191 — prompt-cache prefix places the
  per-creator volatile `{dna_brief}` BEFORE the static `{principles}` list inside
  the single cached system block, so the cache key is creator-specific and the
  static principle text can only be reused within one creator's 5-min TTL, never
  across creators. Not a bug (caching is correctly on the stable system block,
  volatile candidates correctly in the uncached user turn), but a missed reuse
  optimization | fix: put the static principles block first and the dna_brief last
  in the system text so the long static prefix is shared across all creators.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — keyframe tempfile unlinked in `finally` (render.py:140); out_path cleanup owned by worker caller (tasks.py:386); Anthropic client is a module-level singleton (scoring.py:22) |
| 2 Concurrency & scale | 1 SEV2 (CPU-in-request route path); render's blocking subprocess runs on the per-worker singleton loop but that process handles one task at a time, so no cross-task loop starvation — acceptable |
| 3 Security & compliance | ok — no token/PII handling here; no SQL string-building; idempotency query (ranking.py:101) is video_id-scoped (video is creator-owned, video_id PK-unique) and preference load_latest is creator-scoped (train.py:120); no virality promise in any string or prompt (cold-start reasoning text stays honest) |
| 4 Clip-quality | 2 SEV2 (dna_match seed never refined; no overlap dedup). Setup anchoring CORRECT: `_find_setup_start` returns silence END = where speech resumes (principle #2 backward look); render keys on `setup_start_s` via `_render_start_for`; every path cites a named principle; recency-decay + below-threshold honest fallback verified in rerank_with_preference |
| 5 Anthropic SDK | ok — prompt caching on system block (scoring.py:191); token usage incl. cache read/write logged after the call (scoring.py:195); max_tokens=1200; structured JSON parse with signal-score fallback. 1 cleanup (cache prefix ordering). Web-search n/a |
| 6 Cleanliness & typing | 1 cleanup (signal-array rebuild DRY); no TODO/print/debug; all signatures typed |
| 7 Error handling / API | n/a (not a router; route surface owned by routers/clips.py) |
| 8 Config & paths | ok — pathlib.Path throughout; CLIPS_PER_VIDEO_DEFAULT + ANTHROPIC_* present in .env.example; no new config introduced |

## Module verdict
NEEDS-WORK — no blockers; isolation, compliance, and setup-anchoring are sound. Fix the dna_match seed (a duplicate of the score is handed to the preference model as an independent feature) and add candidate overlap dedup, then move the in-request candidate CPU off the event loop.
