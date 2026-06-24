# clip_engine — assessed 2026-06-24

Slice: `clip_engine/` (10 files: `__init__.py`, `window.py`, `scoring.py`,
`ranking.py`, `candidates.py`, `captions.py`, `edits.py`, `filler.py`,
`reframe.py`, `render.py`). Verified call sites in `worker/tasks.py`,
`routers/clips.py`, and `billing/ledger.py` to settle blocking/isolation claims.

## Findings

- [SEV2] reframe.py:151 — `FaceDetector.create_from_options(...)` is rebuilt
  **per sampled frame** inside `_detect_faces_mediapipe`. At `REFRAME_SAMPLE_FPS=5`
  over a 60–90s clip that is 300–450 BlazeFace detector constructions (model load
  + graph init) per clip. The inline comment makes this a deliberate thread-safety
  tradeoff, and the whole path is gated OFF (`ACTIVE_SPEAKER_REFRAME_ENABLED=False`)
  and runs in a worker thread (not the event loop), so it is a render-cost/latency
  defect, not a loop-blocker. (needs-runtime-confirmation on actual magnitude) |
  fix: when the flag is flipped on, build the detector **once** per
  `build_crop_center_track` call and reuse it across frames (one `FaceDetector`
  per clip, closed in a `finally`); a per-thread cache is unnecessary since each
  clip already owns its worker thread. Add a render-env benchmark before launch.
- [SEV2] candidates.py:189 — `derive_skip_reason` re-runs `build_signal_array` +
  `find_peaks` with the SAME params as `extract_candidates` (candidates.py:238–249),
  duplicating the peak-detection work (DRY). Blast radius is tiny: it only fires on
  the zero-clips path from `routers/clips.py`, so the redundant compute is once-per-
  empty-video. | fix: have `extract_candidates` optionally return a structured
  `(candidates, skip_reason)` (or accept a precomputed `peak_indices`) so the skip
  reason is derived from the single peak pass instead of recomputing it.
- [cleanup] render.py:434 — `subtitles={ass_path}:fontsdir=...` interpolates the
  temp ASS path straight into the libass filter arg. `ass_path` is
  `out_path.with_suffix(...)` where `out_path` is a worker `NamedTemporaryFile`;
  the inline comment waves off colons "because /tmp". If an operator sets `TMPDIR`
  to a directory whose name contains `:` `'` or `\`, the filter string breaks the
  render. Low likelihood, bounded. | fix: escape the path for the filtergraph
  (libass wants `\:` `\'` escaped) or pass it via the worker's known-safe temp
  root; at minimum drop the "not a real concern" comment and assert no special
  chars in `ass_path`.
- [cleanup] render.py:324,334 — `from config import settings as _settings` and
  `import clip_engine.reframe as _reframe_mod` are function-local imports. The
  config import is justified in-comment (circular-dep at module init) and the
  reframe import is justified (patch-target stability for tests), so both are
  intentional — flagged only so the next reader does not "tidy" them to module
  scope and reintroduce the cycle / break the test seam.
- [cleanup] captions.py:85 / filler.py:35,43 — three hard-coded English
  stopword/filler lexicons. `_STOPWORDS` (captions) and the Tier-2 filler set
  (filler) overlap heavily ("like", "okay", "you know"). Not duplicated *within*
  the slice and they serve different purposes (highlight-suppression vs excision),
  so KISS says leave them; flagged only as a future-consolidation pointer.

### Verified clean (load-bearing claims traced, not assumed)

- **Per-creator isolation — PASS.** `ranking.py:101` queries `Clip` by `video_id`
  only, but the sole caller `routers/clips.py:208 generate_clips` gates
  `video.creator_id != creator.id` (404) **before** invoking
  `generate_and_rank_clips`, and the persisted `Clip` row carries `creator_id`
  (ranking.py:141). Every clip-facing endpoint (`list_clips`, `render_clip`,
  `clean_*`, `submit_cuts`, `get_clip`, `download_clip`) re-checks
  `clip.creator_id == creator.id`. No cross-tenant path found.
- **Blocking-in-async — PASS.** Every `subprocess.run` (render.py: `_run`,
  `_frame_dimensions`, `_measure_loudnorm_filter`) lives inside
  `render_clip_file` / `render_cleaned_clip_file`, and all three worker call sites
  (`worker/tasks.py:1159, 1305, 1392`) wrap them in `asyncio.to_thread`. The CPU
  candidate-extraction + feature build in `ranking.py:119` / `scoring.py:212` are
  also offloaded via `asyncio.to_thread`. The remaining on-loop pure-Python work
  (`detect_cut_segments` in `routers/clips.py:_clip_clean_cuts`) is
  O(words×phrase_len)≈ a few thousand iterations per clip — sub-millisecond, not a
  finding.
- **Anthropic SDK — PASS.** `scoring.py:28` `AsyncAnthropic` is a module-level
  singleton (timeout + `max_retries=2`). Prompt caching present and correct:
  static instructions in a leading system block, per-creator DNA in a second block
  carrying `cache_control ephemeral ttl:"1h"` (stable-first ordering). `max_tokens`
  set (1200); structured-output JSON-array contract enforced with graceful
  signal-score fallback on `JSONDecodeError`. Token usage logged after the call
  (scoring.py:276) including the 1h-tier cache-write tokens, and billed via
  `_estimate_cost_usd`+`increment_usage` with `cache_write_multiplier=2.0` — i.e.
  the OFF_COURSE_BUGS 2026-06-24 cache-token under-bill is **already fixed in this
  module** (cached read/write tiers priced separately).
- **Untrusted-content / injection — PASS.** Transcript sections routed through
  `wrap_untrusted` (JSON-encodes each value) and `UNTRUSTED_CONTENT_POLICY` leads
  the system prefix (scoring.py:178–186, 52–54). `dna_brief` is creator-owned
  derivative text from `dna_profile.brief_text`.
- **Resource lifecycle — PASS.** Every render temp file (keyframe, ASS, sendcmd,
  `.filter`, `.measure.filter`, out_path) is removed in a `finally` /
  `unlink(missing_ok=True)`. `FaceDetector` opened with a `with` block;
  `cv2.VideoCapture` released. No DB session is opened in this module (sessions are
  passed in / owned by the caller).
- **Idempotency — PASS.** `generate_and_rank_clips` short-circuits when any clip
  exists for the video (ranking.py:101–110) and deliberately never delete+reinserts
  (would cascade-destroy feedback/outcomes — Issue 61). Clean/edit worker paths
  short-circuit on populated `cleaned_render_uri`.
- **Clip-quality + honesty — PASS.** Setup-start anchored by backward look from
  peak (`_find_setup_start`, candidates.py:135) + sentence-boundary snapping
  (principle #12, candidates.py:316–334). Every score path emits a named principle
  from CLIPPING_PRINCIPLES.md; cold-start = "Retention curve is ground truth", DNA
  path cites Claude's chosen principle with a safe default. Skip reasons cite
  principles. No virality language in any string. Below-threshold preference
  fallback is honest (`rerank_with_preference` returns DNA ranking unchanged at
  weight 0).
- **Config & paths — PASS.** `ACTIVE_SPEAKER_REFRAME_ENABLED`, `REFRAME_SAMPLE_FPS`
  present in `config.py` + `.env.example` with descriptions. All media paths are
  `Path` objects from the worker's temp/storage layer. No new config introduced by
  this slice that is missing from `.env.example`.

Tests: `tests/test_clip_engine.py test_scoring.py test_render.py test_captions.py
test_filler.py test_edits.py test_reframe.py test_preference_rerank.py` →
**222 passed**.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — temp files cleaned in `finally`; detector/capture closed |
| 2 Concurrency & scale | ok — all blocking subprocess + CPU offloaded to threads; 1 gated per-frame detector-rebuild (SEV2) |
| 3 Security & compliance | ok — per-creator isolation gated upstream; no PII/token in logs; no raw SQL; honesty held |
| 4 Clip-quality | ok — setup-anchored, principle-cited, DNA-relative, honest fallback |
| 5 Anthropic SDK | ok — singleton, prompt-cache (1h TTL), token-logged, max_tokens + structured-output, cache-cost fixed |
| 6 Cleanliness & typing | 2 cleanup (DRY skip-reason, lexicon overlap) — no print/TODO/dead code; signatures typed |
| 7 Error handling / API | n/a (no router code in slice — endpoints live in `routers/clips.py`) |
| 8 Config & paths | ok — flags in `.env.example` w/ descriptions; paths absolute via `Path` |

## Module verdict
NEEDS-WORK — no blockers and no SEV1; ships safely as-is with the gated reframe
path OFF, but two SEV2s (per-frame detector rebuild before flipping the reframe
flag; duplicated peak detection in `derive_skip_reason`) plus minor cleanups should
be cleared before the active-speaker reframe is turned on in production.
