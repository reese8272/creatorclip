# clip_engine — assessed 2026-05-30

## Findings
- [SEV2] clip_engine/ranking.py:139 — `dna_match=c.get("score")` is seeded to the
  composite Claude/signal score and is NEVER refined afterward. Grep confirms this
  is the only writer to `Clip.dna_match` in the codebase; readers at
  preference/train.py:56 and ranking.py:57 consume it AS IF it were an
  independent DNA-fit feature. The inline comment "refined when preference model
  is trained" is false — no training path mutates `dna_match`. Consequence: the
  preference feature vector is told a duplicate of the composite score (i.e. a
  duplicate of its own label-generating signal) is an independent "DNA fit"
  feature, so it's collinear with the label-generating score and a genuine
  DNA-fit signal is absent | fix: have `score_candidates` return the DNA-only
  fit (Claude `score` BEFORE the signal-feature blend; cold-start path returns
  `_signal_score` separately) and persist that to `dna_match`, keeping
  `clip.score` as the ranked composite. Alternatively, rename the column to
  `seed_score` and drop the misleading comment so the preference layer is
  honest about what it's consuming. Add a unit test asserting `dna_match`
  carries DNA-only fit, not the composite.

- [SEV2] clip_engine/candidates.py:113 — candidate windows are never deduped or
  merged for overlap. `find_peaks(distance=min_distance_samples)` only enforces
  spacing between *peaks*; the backward setup scan in `_find_setup_start` can
  pull two adjacent peaks' `setup_start_s` into heavily overlapping (near-
  identical) windows, so the creator can be shown two clips that are essentially
  the same segment. Cuts against principle #9 "One idea per Short" | fix: after
  the chronological sort at candidates.py:113, drop any candidate whose
  [setup_start_s, end_s] overlaps the previous kept candidate by more than a
  threshold (e.g. >50% IoU), keeping the higher-prominence one; add an eval
  scenario with two close peaks asserting a single merged window.

- [cleanup] clip_engine/scoring.py:70 — `compute_features` rebuilds
  `build_signal_array(timeline)` once per candidate (up to
  CLIPS_PER_VIDEO_DEFAULT=8 full rebuilds of the identical array) inside
  the `_compute_features_all` loop at scoring.py:158. The to_thread offload
  hides the cost from the loop but still wastes CPU on a worker (DRY/KISS) |
  fix: build the `(times, signal)` once in `score_candidates` (or reuse what
  `extract_candidates` already produced) and pass slice indices into
  `compute_features` — change `compute_features(candidate, timeline)` to
  `compute_features(candidate, timeline, signal)` and call once.

- [cleanup] clip_engine/render.py:138 — `_extract_keyframe` is invoked with
  `timeout_s=render_timeout_s` (max(120s, 4×clip_duration)). Pulling ONE jpeg
  frame should never take more than a few seconds; binding it to the full
  render budget means a hung ffprobe/keyframe step can chew the whole 4×duration
  budget before the actual render even starts | fix: hardcode a short ceiling
  (e.g. 30s) on the keyframe extraction call — `_extract_keyframe(...,
  timeout_s=30.0)` — and keep `render_timeout_s` for the encode step only.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — keyframe tempfile unlinked in `finally` (render.py:140); out_path cleanup owned by the worker caller (worker/tasks.py:465); Anthropic client is a module-level singleton (scoring.py:23) with explicit httpx.Timeout(60s read, 10s connect) + max_retries=2 |
| 2 Concurrency & scale | ok — prior SEV2 "CPU in the request loop" is FIXED: `extract_candidates` is now wrapped in `asyncio.to_thread` (ranking.py:115) and feature computation is wrapped in `asyncio.to_thread` (scoring.py:161). render.py uses subprocess.run synchronously, but it's only invoked from the Celery worker (worker/tasks.py:457) — one task per worker process, no loop starvation |
| 3 Security & compliance | ok — no token/PII handling here; no SQL string-building; idempotency query (ranking.py:101) is video_id-scoped (video is creator-owned and video_id is the PK); preference load_latest is creator-scoped (train.py); no virality promise in any string or prompt (cold-start reasoning text stays honest: "Scored on signal density — DNA profile not available yet.") |
| 4 Clip-quality | 2 SEV2 (dna_match seed never refined; no overlap dedup). Setup anchoring CORRECT: `_find_setup_start` returns silence END = where speech resumes (principle #2 backward look); render keys on `setup_start_s` per worker/tasks.py; every path cites a named principle; recency-decay + below-threshold honest fallback verified in `rerank_with_preference` (ranking.py:46–48) |
| 5 Anthropic SDK | ok — prior cleanup FIXED: static instructions now lead and DNA brief carries the cache breakpoint (scoring.py:201-208), so the long static prefix is shared across creators. Token usage incl. cache read/write logged after the call (scoring.py:212); max_tokens=1200; structured JSON parse with signal-score fallback. Web-search n/a |
| 6 Cleanliness & typing | 2 cleanup (signal-array rebuild DRY; over-long keyframe timeout); no TODO/print/debug; all signatures typed |
| 7 Error handling / API | n/a (not a router; route surface owned by routers/clips.py) |
| 8 Config & paths | ok — pathlib.Path throughout; CLIPS_PER_VIDEO_DEFAULT + ANTHROPIC_* present in .env.example; no new config introduced |

## Module verdict
NEEDS-WORK — no blockers; isolation, compliance, async hygiene, and setup-anchoring are sound. The remaining defects are (1) `dna_match` is a duplicate of the composite score and is silently fed to the preference model as an "independent" feature, and (2) overlapping candidate windows can surface near-duplicate clips. Fix both, then the two cleanups (DRY signal array, scoped keyframe timeout).
