# upload_intel — assessed 2026-05-29

Re-assessment after the Issue 58–75 hardening session. Scored against CURRENT
code, verified by reading.

Slice: `upload_intel/__init__.py` (empty), `upload_intel/timing.py`.
Two pure, deterministic functions (`best_upload_windows`, `optimal_gap_hours`)
that rank `AudienceActivity` rows into upload-time recommendations. No LLM, no DB
session, no external client, no I/O, no async, no Celery task, no
file/subprocess handling. Per-creator isolation is enforced one layer up in
`routers/upload_intel.py:23` (`WHERE AudienceActivity.creator_id == creator.id`);
this module only ever sees rows the caller already scoped, so it opens no
cross-tenant surface. Isolation regression: `tests/test_isolation.py:377`.

## Findings
- [VERIFIED-FIX] upload_intel/timing.py:26-31 — the prior SEV2 IndexError→500 is
  FIXED (Issue 75d). `dow = int(row.day_of_week)` / `hour = int(row.hour)` are
  coerced (lines 26-27), then guarded at line 30
  (`if not (0 <= dow <= 6) or not (0 <= hour <= 23): continue`) BEFORE the
  `_DAY_NAMES[dow]` index at lines 39-40. A single malformed activity row is now
  dropped, not raised, so it can no longer 500 `GET /me/upload-intel`. Regression
  test `tests/test_input_hardening.py:33` feeds `day_of_week=7`/`hour=25` and
  asserts the row is skipped (1 result, not IndexError). Confirmed by reading.
- [SEV2] upload_intel/timing.py:54-55 — `optimal_gap_hours` did NOT receive the
  same hardening. It reads `r.day_of_week * 24 + r.hour` (line 55) on raw,
  unvalidated attributes with no bounds check and no `int()` coercion. A
  malformed row that `best_upload_windows` correctly drops is still silently
  consumed here, corrupting the `optimal_gap_hours` value returned alongside the
  windows in the same response (`routers/upload_intel.py:28,32`). No crash (no
  list indexing), so not a 500 — but the two functions now disagree on what a
  valid row is, which is exactly the inconsistency Issue 75d aimed to remove. |
  fix: filter first — `valid = [r for r in activity_rows if 0 <= int(r.day_of_week)
  <= 6 and 0 <= int(r.hour) <= 23]`; return `None` if `len(valid) < 2`; sort/gap
  over `valid`. Add a test mirroring `test_best_upload_windows_skips_malformed_rows`.
- [cleanup] upload_intel/timing.py:9 and :47 — `activity_rows: list` is a bare
  `list` with no element type; both functions duck-type `.activity_index`,
  `.day_of_week`, `.hour`, so the real row contract is invisible to mypy and to
  callers (CLAUDE.md: "type hints on every signature"; rubric 6). | fix: define a
  `typing.Protocol` (`class ActivityRow(Protocol): day_of_week: int; hour: int;
  activity_index: float`) and annotate params `Sequence[ActivityRow]`.
- [cleanup] upload_intel/timing.py:22 and :54 — the sort
  `sorted(rows, key=lambda r: r.activity_index, reverse=True)` is duplicated
  across both functions (DRY, rubric 6). | fix: extract
  `_by_activity_desc(rows)` and call from both, keeping the `[:top_n]` / `[:3]`
  slicing at each call site. Folds naturally into the SEV2 fix above.
- [cleanup] upload_intel/timing.py:54 — `optimal_gap_hours` hardcodes the top-3
  peak count while `best_upload_windows` exposes a configurable `top_n`; the
  docstring says "top-3" but the function runs with as few as 2 rows. Minor
  inconsistency, no behavior bug. | fix: accept `top_n: int = 3` for symmetry, or
  note in the docstring that fewer than 3 rows uses all available.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | n/a (no DB session, client, task, or file handle) |
| 2 Concurrency & scale | ok (pure sync CPU on bounded `top_n`/3-element slices; no blocking call, no async def, no I/O; input row count bounded upstream by per-creator activity rows) |
| 3 Security & compliance | ok (no tokens, no logging, no PII, no SQL, no virality string; isolation enforced in router and tested) |
| 4 Clip-quality | n/a (not a clip/dna/preference module; deterministic timing, no principle citation required) |
| 5 Anthropic SDK | n/a (no LLM call — docstring explicitly "no LLM"; no anthropic import) |
| 6 Cleanliness & typing | 1 SEV2 (unhardened `optimal_gap_hours`) + 3 cleanup (bare `list` typing, duplicated sort, top_n inconsistency); no TODO/print/dead code |
| 7 Error handling / API | not a router; prior IndexError→500 surface is fixed at source (timing.py:30) |
| 8 Config & paths | n/a (no config, no filesystem paths) |

## Module verdict
NEEDS-WORK — the Issue 75d IndexError→500 fix is verified solid in
`best_upload_windows`, but `optimal_gap_hours` (timing.py:54-55) was left out of
the same bounds/coercion guard (SEV2), so the two functions disagree on a valid
row; plus three typing/DRY cleanups. No BLOCKER.
