# upload_intel — assessed 2026-05-30

Re-assessment after Issue 86 (live progress streaming). Confirmed by `git log`:
`upload_intel/timing.py` last changed in commit `c2d6335` (Batch 8, Issues 73/74/75) —
no edits since the prior 2026-05-29 assessment. Findings carry forward unchanged.

Slice: `upload_intel/__init__.py` (empty), `upload_intel/timing.py` (60 lines, 2
pure functions: `best_upload_windows`, `optimal_gap_hours`). No LLM, no DB
session, no external client, no I/O, no async, no Celery task, no
file/subprocess handling. Per-creator isolation is enforced one layer up in
`routers/upload_intel.py:23` (`WHERE AudienceActivity.creator_id == creator.id`);
this module only sees pre-scoped rows. Isolation regression: `tests/test_isolation.py:377`.

## Findings
- [VERIFIED-FIX] upload_intel/timing.py:26-31 — the prior IndexError→500 fix
  remains intact in `best_upload_windows`. `dow = int(row.day_of_week)` /
  `hour = int(row.hour)` are coerced (lines 26-27), then guarded at line 30
  (`if not (0 <= dow <= 6) or not (0 <= hour <= 23): continue`) BEFORE the
  `_DAY_NAMES[dow]` index at lines 38, 40. A malformed activity row is dropped,
  not raised. Regression test `tests/test_input_hardening.py:33` still passing
  by inspection. Confirmed by reading.
- [SEV2] upload_intel/timing.py:54-55 — CARRIED FORWARD. `optimal_gap_hours`
  still does NOT receive the same hardening. Line 54 sorts on raw
  `r.activity_index`; line 55 computes `r.day_of_week * 24 + r.hour` on raw,
  unvalidated attributes with no `int()` coercion and no bounds check. A
  malformed row that `best_upload_windows` correctly drops is silently consumed
  here, corrupting the `optimal_gap_hours` value returned alongside the windows
  in the SAME response (`routers/upload_intel.py:42-43,47`). Two functions on
  the same payload now disagree on what a valid row is — the exact
  inconsistency Issue 75d aimed to eliminate. No crash, so not a 500, but the
  returned `optimal_gap_hours` becomes misleading whenever any row has
  out-of-range fields. | fix: filter and coerce first —
  `valid = [r for r in activity_rows if 0 <= int(r.day_of_week) <= 6 and 0 <= int(r.hour) <= 23]`;
  return `None` if `len(valid) < 2`; do the sort/`day_of_week*24+hour`/gap
  computation over `valid` only (and cast both fields to `int` in the
  comprehension). Add a regression test mirroring
  `test_best_upload_windows_skips_malformed_rows` that feeds one bad row and
  asserts the returned gap equals the gap computed from the valid rows alone.
- [cleanup] upload_intel/timing.py:10 and :47 — CARRIED FORWARD.
  `activity_rows: list` is a bare `list` with no element type; both functions
  duck-type `.activity_index`, `.day_of_week`, `.hour`, so the real row
  contract is invisible to mypy and to callers (CLAUDE.md: "type hints on
  every signature"; rubric 6). | fix: define a `typing.Protocol`
  (`class ActivityRow(Protocol): day_of_week: int; hour: int; activity_index: float`)
  and annotate params `Sequence[ActivityRow]`. Folds naturally into the SEV2 fix.
- [cleanup] upload_intel/timing.py:22 and :54 — CARRIED FORWARD. The sort
  `sorted(rows, key=lambda r: r.activity_index, reverse=True)` is duplicated
  across both functions (DRY, rubric 6). | fix: extract `_by_activity_desc(rows)`
  and call from both, keeping the `[:top_n]` / `[:3]` slicing at each call site.
  Folds naturally into the SEV2 fix above.
- [cleanup] upload_intel/timing.py:54 — CARRIED FORWARD. `optimal_gap_hours`
  hardcodes a top-3 peak count while `best_upload_windows` exposes a
  configurable `top_n`; docstring says "top-3" but the function runs with as
  few as 2 rows. No behavior bug. | fix: accept `top_n: int = 3` for symmetry,
  or note in the docstring that fewer than 3 rows uses all available.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | n/a (no DB session, client, task, or file handle) |
| 2 Concurrency & scale | ok (pure sync CPU on bounded `top_n`/3-element slices; no blocking call, no async def, no I/O; input row count bounded upstream by per-creator activity rows) |
| 3 Security & compliance | ok (no tokens, no logging, no PII, no SQL, no virality string; isolation enforced in router and tested at `tests/test_isolation.py:377`) |
| 4 Clip-quality | n/a (not a clip/dna/preference module; deterministic timing, no principle citation required) |
| 5 Anthropic SDK | n/a (no LLM call — docstring explicitly "no LLM"; no anthropic import) |
| 6 Cleanliness & typing | 1 SEV2 (unhardened `optimal_gap_hours`) + 3 cleanup (bare `list` typing, duplicated sort, top_n inconsistency); no TODO/print/dead code |
| 7 Error handling / API | n/a (not a router); prior IndexError→500 surface is fixed at source (timing.py:30) |
| 8 Config & paths | n/a (no config, no filesystem paths) |

## Module verdict
NEEDS-WORK — Issue 86 did not touch this module, so the prior SEV2 on
`optimal_gap_hours` (timing.py:54-55) carries forward: it was left out of the
Issue 75d bounds/coercion guard that hardened `best_upload_windows`, so the two
functions consuming the same payload disagree on what a valid row is. Three
typing/DRY cleanups also carry forward. No BLOCKER.
