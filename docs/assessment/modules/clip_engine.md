# clip_engine — assessed 2026-07-01

Slice: candidates.py, captions.py, edits.py, filler.py, ranking.py, reframe.py,
render.py, scoring.py, window.py, __init__.py.

Method note (per user hard constraint): every best-practice / SDK / library claim
below is backed by the CURRENT official docs, cited with the URL + date checked.
Claims about this codebase are cited to `file:line` read directly.

## Findings

- [SEV2] clip_engine/ranking.py:127 — the idempotency guard
  `select(Clip).where(Clip.video_id == video_id)` reads the creator-scoped `clips`
  table with NO `creator_id` predicate. This is an internal Celery-worker path (not a
  request handler) and `video_id` is a primary key that maps to exactly one creator, so
  it is not a live cross-tenant leak — but it violates the "per-creator isolation on
  EVERY creator-scoped query" rule (rubric §3) and would silently return the wrong
  creator's clips if the pipeline were ever invoked with a mismatched
  `(creator_id, video_id)` pair | fix: add `.where(Clip.creator_id == creator_id)` to
  the guard query so the tenant predicate is structural, matching the always-filter
  posture the COMPLIANCE log (2026-05-28 Issue 33) mandates. Cheap defense-in-depth,
  no behavior change on the happy path.

- [SEV2] clip_engine/candidates.py:347-349 — after sentence-boundary snapping, `end_s`
  is re-extended to `setup_start_s + MIN_CLIP_S` and forward-snapped to a word `end`,
  but is never re-clamped to the timeline `duration_s`. Transcript word `end` values can
  marginally exceed the ffprobe container duration (encoder/transcriber rounding), so a
  clip whose window sits at the tail of the video can emit `end_s > source_duration`.
  `render.py:367` then raises `ValueError("end_s … exceeds source duration …")` and that
  clip's render fails | fix: after the snap block clamp
  `c["end_s"] = min(c["end_s"], duration_s)` (and re-apply the min-clip guard), so the
  persisted `Clip.end_s` can never fall outside the renderable range. Add an eval
  scenario with a peak in the final 30s of the video to pin it.

- [SEV2] clip_engine/reframe.py:193,197 — `_mediapipe_model_path()` returns the legacy
  Solutions asset `modules/face_detection/face_detection_short_range.tflite` as the
  `model_asset_path` for the **Tasks** `FaceDetectorOptions`. The MediaPipe Tasks
  FaceDetector expects a metadata-bearing `.task`/blaze-face bundle from the model hub,
  not the raw Solutions `.tflite`; loading the bare graph asset is likely to raise at
  `create_from_options` and drop the whole track to center-fallback. (needs-runtime-
  confirmation — the entire per-frame path is gated OFF by
  `ACTIVE_SPEAKER_REFRAME_ENABLED=False` and the code already marks it
  "render-env/staging-pending".) | fix: ship the hub `blaze_face_short_range.tflite`
  (Tasks-compatible) as a pinned asset under a known path (e.g. alongside the Dockerfile
  fonts) and point `_mediapipe_model_path()` at it; assert `Path(...).exists()` at
  worker start when the flag is on. Verified param name `min_detection_confidence` is
  valid on FaceDetectorOptions:
  https://developers.google.com/mediapipe/api/solutions/python/mp/tasks/vision/FaceDetectorOptions
  (checked 2026-07-01).

- [SEV2] clip_engine/reframe.py:151 — `FaceDetector.create_from_options(...)` is invoked
  **inside `_detect_faces_mediapipe`, i.e. once per sampled frame**. At the default
  5 fps over a 60–90s clip that is 300–450 detector constructions (each parsing the model
  asset) per render — an avoidable order-of-magnitude cost that scales with clip length.
  The inline comment concedes this. (gated OFF today, so not a live regression) | fix:
  construct the detector once in `build_crop_center_track` and pass it into the per-frame
  call (BlazeFace IMAGE-mode `.detect()` is safe to reuse serially within one thread);
  keep the lazy import. Also confirm the `sendcmd` single-timestamp `"<t> [enter] crop x
  <v>;"` line format on a real ffmpeg build before flipping the flag
  (needs-runtime-confirmation) — the `crop` filter's `x` IS commandable
  (ffmpeg-filters.html §crop "Commands", checked 2026-07-01), but the instantaneous
  interval + `[enter]` flag combo is unverified on real media.

- [cleanup] clip_engine/candidates.py:193-207 — `derive_skip_reason` re-derives the
  exact `find_peaks(signal, distance=max(1, int(MIN_CLIP_S/resolution_s)),
  prominence=0.5)` setup that `extract_candidates` (lines 246-253) already owns (DRY). A
  future tweak to the peak params must be made in two places or the skip-reason will lie
  | fix: extract a `_detect_peaks(timeline) -> (times, signal, peak_indices, properties)`
  helper and call it from both.

- [cleanup] clip_engine/reframe.py:50-51 — dead `if TYPE_CHECKING: pass` block (no
  type-only imports guarded) | fix: delete it. Also line 119: `frame_width` is a
  parameter of `_detect_faces_mediapipe` but never used in the body | fix: drop the
  parameter (call site at line 300 passes it positionally — update it) or use it.

- [cleanup] clip_engine/render.py:504 — the burned-in `subtitles={ass_path}:fontsdir=…`
  filter arg is built by f-string with no libass escaping of `:` `,` `'` `\` in the
  path. Paths are worker-created temp files today (low risk, no shell — args are a list),
  but a path containing a filtergraph metacharacter would corrupt the `-vf` chain rather
  than fail cleanly | fix: use the quoted form `subtitles=filename='…'` with `\`/`:`/`'`
  escaped, or route the value through a small `_escape_ffmpeg_filter_path()` helper
  shared with the `sendcmd=f=` path (render.py:467).

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — every temp artifact (keyframe, ASS, sendcmd, measure/apply filter scripts) unlinked in a `finally`; `_ANTHROPIC` is a module-level singleton; DB commits/refreshes on the async session are caller-managed |
| 2 Concurrency & scale | 2 findings — CPU work (`find_peaks`, feature build) correctly offloaded via `asyncio.to_thread`; async Anthropic client awaited; render fns are sync (Celery); detector-per-frame + reframe path gated off |
| 3 Security & compliance | 1 finding (creator_id defense-in-depth, ranking.py:127) — no OAuth token / PII / secret in any log line; no f-string/`%` SQL (parameterized ORM); no virality language in any skip-reason label or the scoring system prompt (verified strings) |
| 4 Clip-quality | 1 finding (end-clamp, candidates.py) — setup is anchored by backward look from peak (Principle #2); Clean-Context-Boundary snapping present (#12); every score carries a named principle; DNA-first with honest signal-only cold start; recency decay lives in preference/ (out of slice) |
| 5 Anthropic SDK | ok — prompt caching used (two-block system, DNA marked `ttl:"1h"` only above the 1024-token floor); tokens + cost logged every call; `max_tokens=1200` set; truncation warned. Verified current docs need NO beta header for the 1h TTL (platform.claude.com/docs prompt-caching, checked 2026-07-01) — code is correct |
| 6 Cleanliness & typing | 3 cleanups (DRY peak-detect, dead TYPE_CHECKING block + unused param, filter-path escaping) — no TODO/print/debug; signatures typed |
| 7 Error handling / API | n/a (no router / HTTP surface in this slice) |
| 8 Config & paths | ok — reframe/render read absolute `Path`s; `ACTIVE_SPEAKER_REFRAME_ENABLED`/`REFRAME_SAMPLE_FPS` gated via settings; no new config introduced here |

## Module verdict
NEEDS-WORK — no blocker; two live SEV2s worth fixing now (add `creator_id` to the
idempotency guard, clamp post-snap `end_s` to source duration), plus two gated-path
SEV2s to resolve before `ACTIVE_SPEAKER_REFRAME_ENABLED` is ever flipped on.
