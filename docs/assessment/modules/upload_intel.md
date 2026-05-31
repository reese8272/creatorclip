# upload_intel — assessed 2026-05-31

Wave 9 re-assessment. Confirmed by `git log -- upload_intel/` returning only
two commits: the initial `2f84f1b` and the Batch 8 hardening commit `c2d6335`
(Issues 73/74/75). No Wave 4, 5, 6, 7, 8, or 9 commit has touched this
slice. Findings carry forward unchanged from Waves 1–8 by inspection of the
current file contents.

Slice: `upload_intel/__init__.py` (0 lines — confirmed via `wc -l`) and
`upload_intel/timing.py` (59 lines, 2 pure functions: `best_upload_windows`,
`optimal_gap_hours`). No LLM, no DB session, no external client, no I/O,
no async, no Celery task, no file/subprocess handling. Per-creator isolation
is enforced one layer up in `routers/upload_intel.py:38`
(`select(AudienceActivity).where(AudienceActivity.creator_id == creator.id)`,
re-verified this wave); this module only sees pre-scoped rows. Isolation
regression test in `tests/test_isolation.py` (`test_upload_intel_scoped_to_creator`).
Both functions are consumed together inside the same router response, which
is what makes the consistency gap between them load-bearing.

## Findings
- [VERIFIED-FIX] upload_intel/timing.py:26-30 — the prior IndexError→500 fix
  in `best_upload_windows` remains intact. `dow = int(row.day_of_week)` /
  `hour = int(row.hour)` are coerced (lines 26-27), then guarded at line 30
  (`if not (0 <= dow <= 6) or not (0 <= hour <= 23): continue`) BEFORE the
  `_DAY_NAMES[dow]` index at lines 38, 40. A malformed activity row is
  dropped, not raised. Regression test at
  `tests/test_input_hardening.py:33` (`test_best_upload_windows_skips_malformed_rows`)
  re-confirmed by grep this wave.
- [SEV2] upload_intel/timing.py:54-55 — CARRIED FORWARD from Waves 1–8
  (rubric 6 Cleanliness, with cross-cutting correctness impact).
  `optimal_gap_hours` still does NOT receive the same hardening as its
  sibling. Line 54 sorts on raw `r.activity_index`; line 55 computes
  `r.day_of_week * 24 + r.hour` on raw, unvalidated attributes with no
  `int()` coercion and no bounds check. A malformed row that
  `best_upload_windows` correctly drops is silently consumed here,
  corrupting the `optimal_gap_hours` value returned alongside the windows
  in the SAME router response (`routers/upload_intel.py:10` imports both).
  The two functions consuming one payload disagree on what a valid row
  is — the exact inconsistency Issue 75d aimed to eliminate. No crash, so
  not a 500, but the returned `optimal_gap_hours` becomes misleading
  whenever any row has out-of-range fields. | fix: filter and coerce
  first —
  `valid = [(int(r.day_of_week), int(r.hour), float(r.activity_index)) for r in activity_rows if 0 <= int(r.day_of_week) <= 6 and 0 <= int(r.hour) <= 23]`;
  return `None` if `len(valid) < 2`; do the sort / `dow*24+hour` / gap
  computation over the coerced tuples only. Add a regression test mirroring
  `test_best_upload_windows_skips_malformed_rows` that feeds one bad row
  and asserts the returned gap equals the gap computed from the valid
  rows alone.
- [cleanup] upload_intel/timing.py:10 and :47 — CARRIED FORWARD (rubric 6).
  `activity_rows: list` is a bare `list` with no element type; both
  functions duck-type `.activity_index`, `.day_of_week`, `.hour`, so the
  real row contract is invisible to mypy and to callers (CLAUDE.md mandates
  "type hints on every signature"). | fix: define a `typing.Protocol`
  (`class ActivityRow(Protocol): day_of_week: int; hour: int; activity_index: float`)
  and annotate params `Sequence[ActivityRow]`. Folds naturally into the
  SEV2 fix.
- [cleanup] upload_intel/timing.py:22 and :54 — CARRIED FORWARD (rubric 6,
  DRY). The sort
  `sorted(rows, key=lambda r: r.activity_index, reverse=True)` is
  duplicated across both functions. | fix: extract
  `_by_activity_desc(rows)` and call from both, keeping the `[:top_n]` /
  `[:3]` slicing at each call site. Folds naturally into the SEV2 fix.
- [cleanup] upload_intel/timing.py:54 — CARRIED FORWARD (rubric 6, KISS /
  consistency). `optimal_gap_hours` hardcodes a top-3 peak count while
  `best_upload_windows` exposes a configurable `top_n`; the docstring says
  "top-3" but the function actually runs with as few as 2 rows. No
  behavior bug. | fix: accept `top_n: int = 3` for symmetry, or note in
  the docstring that fewer than 3 rows uses all available.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | n/a (no DB session, client, task, or file handle) |
| 2 Concurrency & scale | ok (pure sync CPU on bounded `top_n`/3-element slices; no blocking call, no `async def`, no I/O; input row count bounded upstream by per-creator activity rows) |
| 3 Security & compliance | ok (no tokens, no logging, no PII, no SQL, no virality string; isolation enforced in router at `routers/upload_intel.py:38` and tested in `tests/test_isolation.py`) |
| 4 Clip-quality | n/a (not a clip/dna/preference module; deterministic timing, no principle citation required) |
| 5 Anthropic SDK | n/a (no LLM call — docstring explicitly "no LLM"; no anthropic import) |
| 6 Cleanliness & typing | 1 SEV2 (unhardened `optimal_gap_hours`) + 3 cleanup (bare `list` typing, duplicated sort, top_n inconsistency); no TODO/print/dead code |
| 7 Error handling / API | n/a (not a router); prior IndexError→500 surface is fixed at source (timing.py:30) |
| 8 Config & paths | n/a (no config, no filesystem paths) |

## Module verdict
NEEDS-WORK — Wave 9 did not touch this module (no commits since `c2d6335`),
so the SEV2 on `optimal_gap_hours` (timing.py:54-55) carries forward
unchanged from Waves 1–8: it was left out of the Issue 75d bounds/coercion
guard that hardened `best_upload_windows`, so the two functions consuming
the same payload still disagree on what a valid row is. Three typing/DRY
cleanups also carry forward. No BLOCKER.
