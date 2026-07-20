# clip_engine — assessed 2026-07-20

Slice: candidates.py, captions.py, edits.py, filler.py, ranking.py, reframe.py,
render.py, scoring.py, summary_select.py, window.py, __init__.py.

Method note: re-verified every 2026-07-01 finding against HEAD (git diff
f70a857..HEAD touched candidates/ranking/reframe/render/scoring + new
summary_select.py — the Issue 109d signal-array hoist and 109e scoped-select
sweep were traced for correctness and tenant scoping). Codebase claims cited to
`file:line` read directly.

## Resolved since 2026-07-01

- **ranking.py idempotency guard missing `creator_id`** — FIXED. The guard now
  lives in `load_existing_clips` (ranking.py:122-127) with
  `.where(Clip.video_id == video_id, Clip.creator_id == creator_id)`; both call
  sites (routers/clips.py:254/284, worker/tasks.py:2559/2582) pass a matching
  `creator_id` and stamp `session.info["creator_id"]` for the RLS GUC before
  first query. The 109e refactor (split into `load_existing_clips` /
  `score_and_rank` / `persist_ranked_clips`) preserved tenant scoping and
  correctly moved the guard BEFORE the LLM spend on both paths.
- **Post-snap `end_s` exceeding source duration** — FIXED. candidates.py:350-360
  clamps `end_s` to `duration_s` after forward snap + MIN_CLIP_S re-extension and
  drops the candidate if the clamp breaks the MIN_CLIP_S invariant (same handling
  as the pre-NMS filter). See cleanup below: the fix landed without the
  regression test / tail-peak eval scenario the prior finding asked for.
- **MediaPipe Tasks model path (legacy Solutions asset)** — FIXED.
  `_mediapipe_model_path()` (reframe.py:212-254) now resolves
  `settings.MEDIAPIPE_FACE_MODEL_PATH` first (present in config.py:344 +
  .env.example:139; Dockerfile:29-34 fetches the hub
  `blaze_face_short_range.tflite` to a pinned path and sets the env var), falls
  back to the package `.task` bundle, and returns "" → center-fallback. The
  legacy `face_detection_short_range.tflite` path is gone.
- **FaceDetector constructed per sampled frame** — FIXED. `_create_face_detector`
  (reframe.py:116-151) builds ONE detector per track; `build_crop_center_track`
  (reframe.py:340-361) passes it per frame and releases it via
  `_close_face_detector` in a `finally`.
- **Issue 109d signal-array hoist** — verified correct: `score_candidates`
  builds the signal once inside the `asyncio.to_thread` worker
  (scoring.py:267-273) and threads it through `compute_features(candidate,
  timeline, signal)`; the `signal is None` fallback (scoring.py:132-133) keeps
  single-candidate callers unchanged. Cold-start principle citation corrected to
  "Pattern interrupt" (Issue 109c, scoring.py:182-196) — an honest match for a
  ~60% energy-weighted score, and a valid name in docs/CLIPPING_PRINCIPLES.md.

## Findings

- [SEV2] clip_engine/ranking.py:187-229 — `persist_ranked_clips` idempotency is
  check-then-insert with NO database backstop: `models.py` defines no unique
  constraint on `clips` covering `(video_id, rank)` or `(video_id, creator_id,
  rank)` (verified: the model at models.py:616-660 has none). Two concurrent
  executions — e.g. the router's `POST /generate` racing the worker pipeline for
  the same video, or two at-least-once Celery deliveries running simultaneously —
  can BOTH pass the `load_existing_clips` guard and insert the full clip set
  twice (duplicate clips, double LLM spend already happened upstream). The
  docstring's "can never delete+reinsert" guarantee holds, but "safe to run twice
  concurrently" (rubric §1) does not | fix: Alembic migration adding
  `sa.UniqueConstraint("video_id", "rank", name="uq_clips_video_rank")` (rank is
  always set on this path), and in `persist_ranked_clips` catch `IntegrityError`
  → `await session.rollback()` → return `await load_existing_clips(...)`. Add a
  test inserting the same ranked list twice on two sessions.

- [SEV2] clip_engine/reframe.py:446-481 — (carry-forward, gated,
  needs-runtime-confirmation) the sendcmd line format
  `"<t> [enter] crop x <v>;"` (single instantaneous timestamp + `[enter]` flag,
  build_sendcmd_script) remains unverified on a real ffmpeg build; the whole
  path is still behind `ACTIVE_SPEAKER_REFRAME_ENABLED=False`. The detector
  half of the original finding is fixed; this format check must happen in the
  render-env smoke test before the flag is ever flipped | fix: run one gated
  render on real media in the render image and pin the produced crop-x sequence.

- [cleanup] clip_engine/candidates.py:193-207 — (carry-forward) `derive_skip_reason`
  still re-derives the exact `find_peaks(signal, distance=max(1,
  int(MIN_CLIP_S/resolution_s)), prominence=0.5)` setup that `extract_candidates`
  owns at lines 246-253 (DRY): a future tweak to peak params must be made twice
  or the skip-reason will lie | fix: extract `_detect_peaks(timeline) -> (times,
  signal, peak_indices, properties)` and call from both.

- [cleanup] clip_engine/reframe.py:50-51 — (carry-forward) dead
  `if TYPE_CHECKING: pass` block | fix: delete. Also line 164-167:
  `frame_width` is still a parameter of `_detect_faces_mediapipe` and still
  unused in the body | fix: drop it (update the call at line 351).

- [cleanup] clip_engine/render.py:499-502 — (carry-forward) the burned-in
  `subtitles={ass_path}:fontsdir={_FONTS_DIR}` filter arg is still built by
  f-string with no libass escaping of `:` `,` `'` `\` in the path. Worker-created
  temp paths today (low risk, list argv, no shell), but a metacharacter in the
  path corrupts the `-vf` chain rather than failing cleanly | fix: quoted form
  `subtitles=filename='…'` via a small `_escape_ffmpeg_filter_path()` helper
  shared with the `sendcmd=f=` arg (render.py:465).

- [cleanup] clip_engine/scoring.py:215-221 — `_transcript_context._gather`
  selects segments by full containment (`seg.start >= start_min AND
  seg.end <= end_max`), so a transcript segment straddling `setup_s` is dropped
  from BOTH the [BEFORE] and [CLIP] sections — the clip's opening sentence can
  vanish from the LLM's context when transcriber segment bounds don't align with
  the snapped boundary. captions.py:206-210 already uses the correct overlap
  semantics (`end > lo and start < hi`) | fix: switch `_gather` to overlap
  selection with the boundary assigning a straddler to exactly one section
  (e.g. by segment midpoint).

- [cleanup] tests/test_clip_engine.py — the end_s-clamp fix
  (candidates.py:350-360) landed without the regression test / eval scenario the
  2026-07-01 finding specified (no scenario with a peak in the final 30s and
  word `end` values past `duration_s`; verified none of the 16 scenarios nor the
  37 unit tests pin it) | fix: add a unit test where forward-snap words extend
  past `duration_s` and assert `end_s <= duration_s` or the candidate is
  dropped.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | 1 finding (no DB backstop for concurrent double-insert) — temp artifacts (keyframe, ASS, sendcmd, `.filter`/`.measure.filter` scripts) all unlinked in `finally`; MediaPipe detector closed in `finally`; `_ANTHROPIC` module-level singleton; ledger session via context manager |
| 2 Concurrency & scale | ok — Issue 82b split verified: no DB session held across the 30–120 s LLM call on either call path; CPU work (find_peaks, feature build incl. the 109d single signal build) offloaded via `asyncio.to_thread`; render fns sync (Celery); recap render bounded by a duration-derived timeout |
| 3 Security & compliance | ok — creator_id predicate now structural on the clips guard (backs RLS); no token/PII in any logger call (checked all); parameterized ORM only; no virality language in skip-reason labels, principles, or the scoring system prompt; transcript context routed through `wrap_untrusted` |
| 4 Clip-quality | 1 cleanup (straddling-segment context gap) — setup anchored by backward look from peak (#2); Clean Context Boundary snapping with post-snap invariants + duration clamp (#12); every score path cites a valid named principle (cold-start now honestly "Pattern interrupt", Issue 109c); DNA-first with explicit signal-only fallback; recap selection is chronological + budget-capped and carries score/principle per segment |
| 5 Anthropic SDK | ok — two-block cached system with 1024-token floor guard (`ttl:"1h"` only above floor); tokens + cache tiers logged every call; `max_tokens=1200`; truncation warned; fenced-JSON extraction (Issue 342) |
| 6 Cleanliness & typing | 5 cleanups (3 carried forward + 2 new) — no TODO/print/debug; signatures typed |
| 7 Error handling / API | n/a (no router/HTTP surface in this slice) |
| 8 Config & paths | ok — `MEDIAPIPE_FACE_MODEL_PATH` and `RECAP_TARGET_DURATION_MIN/MAX_S` present in config.py + .env.example with descriptions; Dockerfile ships the model asset; all paths absolute worker-provided `Path`s |

## Module verdict
NEEDS-WORK — no blocker; one live SEV2 (the clip idempotency guard has no DB
unique constraint behind it, so concurrent generation can double-insert), one
gated SEV2 to settle before the reframe flag flips, plus five cleanups. All four
substantive 2026-07-01 findings are fixed and the 109d/109e refactors are
correct and tenant-safe.
