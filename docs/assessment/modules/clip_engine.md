# clip_engine — assessed 2026-07-20 (post-fix)

Slice: candidates.py, captions.py, edits.py, filler.py, ranking.py, reframe.py,
render.py, scoring.py, summary_select.py, window.py, __init__.py.

Method note: re-verified every finding from this morning's 2026-07-20 assessment
against HEAD (`git diff ca3305c..HEAD -- clip_engine/` touched ONLY ranking.py —
the Issue 361 race backstop). The persist_ranked_clips fix was traced end to
end: migration 0046, the models.py constraint, both call sites, the loser path,
and the DEFERRABLE interaction with rerank_with_preference's rank permutation.

## Resolved since this morning (2026-07-20 AM)

- **[was SEV2] persist_ranked_clips check-then-insert with no DB backstop** —
  FIXED (commit cd872ca, Issue 361). Verified in full:
  - Migration `alembic/versions/0046_race_unique_backstops.py` builds
    `uq_clips_video_rank UNIQUE (video_id, rank)` CONCURRENTLY (autocommit
    block, online-safe), with a dedupe-first DELETE keeping the
    earliest-created row per (video_id, rank) so the build cannot fail on
    pre-existing race debris; then promotes it catalog-only via
    `ADD CONSTRAINT ... UNIQUE USING INDEX ... DEFERRABLE INITIALLY DEFERRED`.
    Mirrored in models.py:618-633 so metadata matches the DB.
  - **DEFERRABLE trace (the load-bearing question):** DEFERRED is required
    because `rerank_with_preference` (ranking.py:100-105) permutes rank values
    across the just-inserted set via per-row UPDATEs — an IMMEDIATE check would
    raise on the transient swap during flush. Deferred, the check runs at each
    COMMIT. Commit 1 (ranking.py:238) checks the freshly inserted set — this is
    where the race loser fails. Commit 2 (ranking.py:255, after rerank) checks
    a strict bijection of the winner's own rows (sort + reassign 1..N over the
    same list), which cannot conflict; no third party can insert between the
    two commits because `load_existing_clips` now sees the committed rows. The
    only production writer of `Clip.rank` is this module (verified by grep:
    ranking.py:105 and the insert at :232); NULL ranks are distinct and never
    conflict. **No path exists on which the deferred check misfires.**
  - **Loser path:** commit 1 raises `IntegrityError` → `await
    session.rollback()` → `return await load_existing_clips(...)`
    (ranking.py:237-248). The loser never touches its rolled-back ORM objects
    (no refresh/rerank — the MissingGreenlet trap is avoided), and the
    post-rollback re-select opens a fresh transaction that sees the winner's
    committed rows, so it correctly returns the winner's set (non-empty by
    construction: the constraint only fires because the winner committed).
    `session.info["creator_id"]` survives rollback (it is session state, not
    transaction state), so the RLS GUC re-arms for the re-select.
  - **Tests:** unit loser-path test `tests/test_ranking_persist_race.py`
    (asserts rollback + winner set returned + no refresh of rolled-back rows)
    AND a real-Postgres concurrent test
    `tests/test_generate_clips_idempotency_integration.py::test_concurrent_persist_inserts_exactly_one_clip_set`
    (two sessions `asyncio.gather`-race the same video; asserts exactly one
    set survives and both callers converge on the same clip ids). This is
    exactly the shape the morning finding specified.

## Findings

- [SEV2] clip_engine/reframe.py:446-481 — (carry-forward, gated,
  needs-runtime-confirmation) the sendcmd line format
  `"<t> [enter] crop x <v>;"` (single instantaneous timestamp + `[enter]`
  flag, build_sendcmd_script) remains unverified on a real ffmpeg build; the
  whole path is still behind `ACTIVE_SPEAKER_REFRAME_ENABLED=False`. Unchanged
  since the morning assessment | fix: run one gated render on real media in the
  render image and pin the produced crop-x sequence before the flag ever flips.

- [cleanup] clip_engine/ranking.py:239 — (new, from the fix) the
  `except IntegrityError` catch is unqualified: an FK violation at commit-time
  flush (e.g. the video cascade-deleted by account erasure mid-generation)
  would be misread as "lost the concurrent-generation race", log the misleading
  message, and silently return an empty list. Graceful, but the log lies |
  fix: inspect `exc.orig` for `uq_clips_video_rank` and re-raise (or log a
  distinct message) when it is any other constraint.

- [cleanup] clip_engine/candidates.py:193-207 — (carry-forward)
  `derive_skip_reason` still re-derives the exact `find_peaks(signal,
  distance=max(1, int(MIN_CLIP_S/resolution_s)), prominence=0.5)` setup that
  `extract_candidates` owns (DRY) | fix: extract `_detect_peaks(timeline)` and
  call from both.

- [cleanup] clip_engine/reframe.py:50-51 — (carry-forward) dead
  `if TYPE_CHECKING: pass` block | fix: delete. Also `frame_width` is still an
  unused parameter of `_detect_faces_mediapipe` | fix: drop it (update the call
  site).

- [cleanup] clip_engine/render.py:499-502 — (carry-forward) the burned-in
  `subtitles={ass_path}:fontsdir={_FONTS_DIR}` filter arg is still built by
  f-string with no libass escaping of `:` `,` `'` `\` in the path (low risk:
  worker-created /tmp paths, list argv, no shell) | fix: quoted form via a
  shared `_escape_ffmpeg_filter_path()` helper (also for the `sendcmd=f=` arg).

- [cleanup] clip_engine/scoring.py:215-221 — (carry-forward)
  `_transcript_context._gather` selects segments by full containment, so a
  segment straddling `setup_s` is dropped from BOTH [BEFORE] and [CLIP] —
  the clip's opening sentence can vanish from the LLM context.
  captions.py:206-210 already uses correct overlap semantics | fix: switch
  `_gather` to overlap selection, assigning a straddler by segment midpoint.

- [cleanup] tests/test_clip_engine.py — (carry-forward) the end_s-clamp fix
  (candidates.py:350-360) still has no regression test / eval scenario (no
  scenario with a peak in the final 30s and word `end` values past
  `duration_s`; the 2026-07-20 diff added no clip_engine tests beyond the race
  pair) | fix: add a unit test where forward-snap words extend past
  `duration_s` and assert `end_s <= duration_s` or the candidate is dropped.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — the double-insert race now has a DB backstop (uq_clips_video_rank, DEFERRABLE, migration 0046) with IntegrityError→rollback→return-winner; "safe to run twice concurrently" verified by a real-Postgres gather-race test. Temp artifacts unlinked in `finally`; MediaPipe detector closed in `finally`; `_ANTHROPIC` module-level singleton; ledger session via context manager |
| 2 Concurrency & scale | ok — no DB session held across the 30–120 s LLM call on either call path; CPU work offloaded via `asyncio.to_thread`; DEFERRED constraint check adds no lock beyond the commit-time uniqueness probe; render fns sync (Celery); recap render bounded by a duration-derived timeout |
| 3 Security & compliance | ok — creator_id predicate structural on the clips guard (backs RLS; loser re-select re-arms the GUC via surviving session.info); no token/PII in logger calls; parameterized ORM only; no virality language; transcript context via `wrap_untrusted` |
| 4 Clip-quality | 1 cleanup (straddling-segment context gap, carry-forward) — setup anchored by backward look from peak (#2); Clean Context Boundary snapping + duration clamp (#12); every score path cites a valid named principle; DNA-first with explicit signal-only fallback; loser-path return preserves the winner's ranking untouched (never a half-blended set) |
| 5 Anthropic SDK | ok — two-block cached system with 1024-token floor guard; tokens + cache tiers logged; `max_tokens=1200`; truncation warned; fenced-JSON extraction |
| 6 Cleanliness & typing | 6 cleanups (5 carried forward + 1 new broad-catch nit) — no TODO/print/debug; signatures typed |
| 7 Error handling / API | n/a (no router/HTTP surface in this slice) |
| 8 Config & paths | ok — no config changes in this diff; all paths absolute worker-provided `Path`s |

## Module verdict
NEEDS-WORK — no blocker; the morning's live SEV2 (clip idempotency race) is
FIXED correctly (constraint + deferred-check trace clean on every path, loser
returns the winner's set, unit + real-PG concurrent tests added); what remains
is the gated reframe-sendcmd SEV2 (runtime confirmation before the flag flips)
plus six cleanups, one new (unqualified IntegrityError catch in the fix).
