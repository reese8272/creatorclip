# clip_engine — assessed 2026-07-29 (delta re-assessment, w3/ready-pass-closeout)

Slice: candidates.py, captions.py, edits.py, filler.py, ranking.py, reframe.py,
render.py, scoring.py, summary_select.py, window.py, __init__.py.

Method note (2026-07-29 delta): `git log 1ed2473..HEAD -- clip_engine/` touches
ONLY candidates.py (f68fd39, peak-detection DRY) and scoring.py (f6839bd,
midpoint context rule); tests added in 99bfb29 (end_s clamp) + f6839bd
(straddler regression). Each delta was verified against the pre-change source
line by line; all other 2026-07-20 findings were re-checked at their current
locations (unchanged). `pytest tests/test_clip_engine.py tests/test_scoring.py`
= 83 passed on this branch.

## Delta 2026-07-29 — resolved since 2026-07-20

- **[was cleanup] candidates.py peak-detection duplication — FIXED (f68fd39),
  behavior-preservation verified.** `_detect_peaks(timeline)` (candidates.py:175-194)
  now owns `build_signal_array → resolution_s → min_distance_samples →
  find_peaks(distance, prominence=_PEAK_PROMINENCE)`; both callers keep their
  exact prior semantics:
  - `derive_skip_reason` (:216): old code returned NO_SIGNAL before ever calling
    find_peaks when the signal was empty; the helper mirrors this by returning
    `(times, signal, np.array([], dtype=int), {})` without invoking find_peaks,
    and the caller's `len(signal)==0` check fires first. For non-empty signals
    the distance/prominence math is character-identical (`_PEAK_PROMINENCE=0.5`
    == the old literal). Skip-reason priority order and all four reason strings
    unchanged — identical signals produce identical skip reasons.
  - `extract_candidates` (:261): the two early returns were merged into
    `len(signal)==0 or len(peak_indices)==0` — equivalent, and `times`/
    `properties` are only consumed after that guard. `properties["prominences"]`
    is always present when prominence is passed, so the `.get` fallback is as
    dead/alive as before.
- **[was cleanup] scoring.py `_transcript_context._gather` straddling-segment
  gap — FIXED (f6839bd), boundary correctness verified.** Selection switched
  from full containment to midpoint with half-open `[start_min, end_max)`
  bounds (scoring.py:215-227). The three sections `[before_start, setup_s)`,
  `[setup_s, end_s)`, `[end_s, after_end)` now form a strict partition:
  - a segment whose midpoint lands EXACTLY on setup_s or end_s is assigned to
    exactly one section (the right-hand one, start-inclusive) — no double count,
    no drop; the regression test asserts `count("straddles setup") == 1`;
  - empty sections still collapse to `""` and are omitted from the joined
    output (`if before:` guards unchanged); `setup_s=0` degenerates the BEFORE
    range to `[0,0)` which correctly matches nothing;
  - at the OUTER edges a straddler whose midpoint falls inside the context
    window is now included where full containment excluded it — the intended
    direction (more context, never less). Known tradeoff, acceptable: a
    pathologically coarse segment (much longer than a section) contributes all
    its text to the single section holding its midpoint; typical ASR segments
    are seconds long vs the 30–90 s sections, and this is exactly the fix shape
    the 2026-07-20 assessment specified. captions.py keeps its own (correct)
    overlap semantics for timing; the two serve different purposes.
- **[was cleanup] end_s-clamp regression test — LANDED (99bfb29).**
  `test_extract_candidates_end_clamped_to_duration`: forward snap latches a
  terminal-punct word ending at 112.5 > duration 111.0 and asserts the clamp
  back to exactly 111.0 (commit verified the raw snap reaches 112.5 pre-clamp,
  so the test fails without the fix). Covers the clamp branch; the defensive
  drop branch is unreachable by construction, per 80/20 left untested.
- **Eval-harness integrity re-confirmed:** `SCENARIO_FLOOR = 21` (as of 2026-08-05)
  (tests/test_clip_engine.py:201) with 16 scenario yamls on disk +
  ranking fixture; the setup-before-peak invariant is enforced three ways —
  `test_setup_always_before_peak` (:180), the per-scenario
  `all_setup_before_peak` gate (:333), and the post-snap invariant test (:537).
  All green in this run.

## Findings

- [SEV2] clip_engine/reframe.py:446-481 — (carry-forward, gated,
  needs-runtime-confirmation) the sendcmd line format
  `"<t> [enter] crop x <v>;"` (single instantaneous timestamp + `[enter]`
  flag, build_sendcmd_script) remains unverified on a real ffmpeg build; the
  whole path is still behind `ACTIVE_SPEAKER_REFRAME_ENABLED=False`. No diff
  to reframe.py this pass | fix: run one gated render on real media in the
  render image and pin the produced crop-x sequence before the flag ever flips.

- [cleanup] clip_engine/ranking.py:239 — (carry-forward) the
  `except IntegrityError` catch is unqualified: an FK violation at commit-time
  flush (e.g. the video cascade-deleted by account erasure mid-generation)
  would be misread as "lost the concurrent-generation race", log the misleading
  message, and silently return an empty list. Graceful, but the log lies |
  fix: inspect `exc.orig` for `uq_clips_video_rank` and re-raise (or log a
  distinct message) when it is any other constraint.

- [cleanup] clip_engine/reframe.py:50-51 — (carry-forward) dead
  `if TYPE_CHECKING: pass` block | fix: delete. Also `frame_width` remains an
  unused parameter of `_detect_faces_mediapipe` (signature + docstring only,
  re-verified) | fix: drop it (update the call site).

- [cleanup] clip_engine/render.py:465,502 — (carry-forward, **DEFERRED by
  decision** — OFF_COURSE_BUGS.md 2026-07-29 row: SEV4-theoretical,
  multi-level ffmpeg escaping semantics, not unit-test-guardable, worker-created
  /tmp paths, list argv, no shell) the `subtitles={ass_path}:fontsdir=` and
  `sendcmd=f={sendcmd_path}` filter args are f-strings with no libass/filter
  escaping. Stays deferred; revisit only if user-influenced paths ever reach
  these filters.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — unchanged this delta; the uq_clips_video_rank DEFERRABLE backstop + loser-path trace from 2026-07-20 stands (no diff to ranking.py); temp artifacts unlinked in `finally`; `_ANTHROPIC` singleton; ledger session via context manager |
| 2 Concurrency & scale | ok — delta is pure-CPU list comprehension / find_peaks refactor, no new async or DB surface; no session held across the LLM call; CPU offloaded via `asyncio.to_thread` |
| 3 Security & compliance | ok — no new logger lines, no token/PII surface in the delta; transcript context still routed through `wrap_untrusted` (the midpoint change alters WHICH segments feed it, not how they are wrapped); no virality language |
| 4 Clip-quality | ok — the straddling-segment context gap is FIXED (strict-partition midpoint rule); setup anchored by backward look from peak (#2); Clean Context Boundary snapping + duration clamp (#12) now regression-tested; every score path cites a named principle; eval floor 15/16 scenarios + setup-before-peak invariant green |
| 5 Anthropic SDK | ok — untouched this delta; two-block cached system with 1024-token floor guard; tokens + cache tiers logged; `max_tokens=1200` |
| 6 Cleanliness & typing | 3 cleanups (2 carry-forward + 1 deferred-by-decision); the candidates.py DRY item is resolved; `_detect_peaks` fully typed; no TODO/print/debug in the delta |
| 7 Error handling / API | n/a (no router/HTTP surface in this slice) |
| 8 Config & paths | ok — no config changes; `_PEAK_PROMINENCE` is a module constant, not config (correct: it is policy, not deployment-tunable) |

## Module verdict
NEEDS-WORK — no blocker and nothing live-reachable is defective: all three
ready-pass deltas verified correct (peak-detection dedup is exactly
behavior-preserving; the midpoint rule is a strict half-open partition with
edge segments assigned exactly once and empty sections handled; the end_s-clamp
regression test genuinely exercises the clamp). What keeps the verdict at
NEEDS-WORK is the one gated SEV2 — the reframe sendcmd format must be
runtime-confirmed before `ACTIVE_SPEAKER_REFRAME_ENABLED` ever flips — plus two
small cleanups and one explicitly deferred escaping item (OFF_COURSE
2026-07-29).
