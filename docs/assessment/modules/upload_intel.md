# upload_intel — assessed 2026-05-31

Wave 9 re-assessment. `git log -- upload_intel/` now shows four commits:
the initial `2f84f1b`, the Batch-8 hardening `c2d6335`, the Wave-9
carry-forward fix `7bd1cfe` (Issue 103), and the Issue-108 cleanup sweep
`d6a7393`. Both Wave-9 commits land in this slice — the SEV2 that carried
forward 8 cycles is now **closed in code AND backed by regression tests**.

Slice: `upload_intel/__init__.py` (0 lines, confirmed via `wc -l`) and
`upload_intel/timing.py` (81 lines, 2 pure functions: `best_upload_windows`,
`optimal_gap_hours`). No LLM, no DB session, no external client, no I/O,
no async, no Celery task, no file/subprocess handling. Per-creator isolation
is enforced one layer up in `routers/upload_intel.py:42`
(`select(AudienceActivity).where(AudienceActivity.creator_id == creator.id)`,
re-verified this wave); this module only sees pre-scoped rows. Isolation
regression test in `tests/test_isolation.py`. Both functions are consumed
together in the same router response, which is what made the prior
consistency gap between them load-bearing.

## Findings
- [VERIFIED-FIX] upload_intel/timing.py:39 — the prior IndexError→500 fix
  in `best_upload_windows` remains intact. `dow = int(row.day_of_week)` /
  `hour = int(row.hour)` are coerced (lines 35-36), then guarded at line 39
  (`if not (0 <= dow <= 6) or not (0 <= hour <= 23): continue`) BEFORE the
  `_DAY_NAMES[dow]` index at lines 47, 49. Regression test at
  `tests/test_input_hardening.py:33` (`test_best_upload_windows_skips_malformed_rows`)
  re-confirmed by grep this wave.
- [VERIFIED-FIX] upload_intel/timing.py:67-71 — **SEV2 CLOSED** (Wave 9 /
  Issue 103). `optimal_gap_hours` now applies the same bounds-and-coercion
  guard `best_upload_windows` has had since Issue 75d: rows are filtered
  through `0 <= int(r.day_of_week) <= 6 and 0 <= int(r.hour) <= 23` and
  coerced to a `(int, int, float)` tuple BEFORE the
  `dow * 24 + hour` arithmetic at line 76. The two functions consuming the
  same payload now agree on what a valid row is. Comment at line 64-66
  cites Issue 75d as the precedent. Returns `None` early if fewer than 2
  valid rows survive the filter. The 8-cycle carry-forward is dead.
  Regression coverage at `tests/test_upload_intel.py:80-99` — TWO new
  tests verify (a) a malformed row is dropped while valid rows still yield
  the correct gap, (b) all-malformed input returns `None` rather than
  raising. Both tests present in the current tree, re-grepped this wave.
- [VERIFIED-FIX] upload_intel/timing.py:19, 56 — typing tightened (Issue
  108). Signatures are now `Sequence[Any]` (was bare `list`), satisfying
  the CLAUDE.md "type hints on every signature" rule under the mypy gate.
  The original-rubric ideal would be a `Protocol(ActivityRow)` with the
  three attributes, but the comment at lines 9-13 documents the explicit
  tradeoff: SQLAlchemy `Mapped[T]` descriptors don't satisfy a structural
  Protocol under mypy, so `Sequence[Any]` keeps the duck-typed contract
  honest. Recorded intent is preserved in the docstring (lines 9-13);
  acceptable per KISS — no need to fight the ORM type system for a
  two-function module.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | n/a (no DB session, client, task, or file handle) |
| 2 Concurrency & scale | ok (pure sync CPU on bounded top-N slices; no blocking call, no `async def`, no I/O; input row count bounded upstream by per-creator activity rows — at most 7×24=168 rows) |
| 3 Security & compliance | ok (no tokens, no logging, no PII, no SQL, no virality string; isolation enforced in router at `routers/upload_intel.py:42` and tested in `tests/test_isolation.py`) |
| 4 Clip-quality | n/a (not a clip/dna/preference module; deterministic timing, no principle citation required) |
| 5 Anthropic SDK | n/a (docstring explicitly "no LLM"; no anthropic import) |
| 6 Cleanliness & typing | ok (SEV2 closed Wave 9; bare-`list` typing closed Wave 9 / Issue 108; no TODO/print/dead code). DRY note: the `sorted(..., key=lambda r: r.activity_index, reverse=True)` shape still appears in both functions but they now operate on different element types (raw row vs. coerced tuple), so the prior "extract `_by_activity_desc`" cleanup is no longer a clean DRY win — not flagging. |
| 7 Error handling / API | n/a (not a router); IndexError→500 surface fixed at source (timing.py:39) |
| 8 Config & paths | n/a (no config, no filesystem paths) |

## Module verdict
clean — Wave 9 closed the 8-cycle SEV2 carry-forward (Issue 103) and the
bare-`list` typing cleanup (Issue 108). Code now matches the rubric on
every applicable category, both Wave-9 fixes are backed by regression
tests, and no net-new findings surfaced on re-read. No BLOCKER, no SEV1,
no SEV2, no cleanup.
