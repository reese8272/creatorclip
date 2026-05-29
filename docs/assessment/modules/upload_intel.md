# upload_intel — assessed 2026-05-29

Slice: `upload_intel/__init__.py` (empty), `upload_intel/timing.py`.
The module is two pure, deterministic functions (`best_upload_windows`,
`optimal_gap_hours`) that rank `AudienceActivity` rows into upload-time
recommendations. No LLM, no DB session, no external client, no I/O, no Celery
task, no file/subprocess handling. Per-creator isolation is enforced one layer
up in `routers/upload_intel.py:23` (`WHERE AudienceActivity.creator_id ==
creator.id`); the functions only ever see rows the caller already scoped, so the
module itself opens no cross-tenant surface.

## Findings
- [SEV2] upload_intel/timing.py:33-35 — `_DAY_NAMES[int(row.day_of_week)]`
  indexes a 7-element list with an unvalidated DB value. A `day_of_week` outside
  0–6 (bad ingest / future enum drift) raises `IndexError`, which surfaces as an
  unhandled 500 on `GET /me/upload-intel` (rubric 7: unsafe error → internal
  detail/stack to client; rubric 3: unvalidated input). Same unchecked
  `int(row.hour)` at :26 feeds AM/PM math. | fix: guard at the top of the loop —
  `dow = int(row.day_of_week); hr = int(row.hour);` then
  `if not 0 <= dow <= 6 or not 0 <= hr <= 23: continue` (skip malformed rows) so
  one corrupt activity row cannot 500 the whole endpoint; add a unit test feeding
  `day_of_week=7` / `hour=25` asserting the row is dropped rather than raising.
- [cleanup] upload_intel/timing.py:9 — `activity_rows: list` (and :42, :47) are
  bare `list` with no element type; the functions duck-type `.activity_index`,
  `.day_of_week`, `.hour` on each element, so the real contract is invisible to
  mypy and to callers (CLAUDE.md: "type hints on every signature"; rubric 6). |
  fix: define a `typing.Protocol` (e.g. `class ActivityRow(Protocol): day_of_week:
  int; hour: int; activity_index: float`) in the module and annotate the params
  `list[ActivityRow]`; this also documents the shape the router's ORM rows must
  satisfy.
- [cleanup] upload_intel/timing.py:22 and :49 — the sort
  `sorted(rows, key=lambda r: r.activity_index, reverse=True)` is duplicated
  across both functions (DRY, rubric 6). | fix: extract
  `def _by_activity_desc(rows: list[ActivityRow]) -> list[ActivityRow]` and call
  it from both; keeps the slicing (`[:top_n]` vs `[:3]`) at each call site.
- [cleanup] upload_intel/timing.py:49 — `optimal_gap_hours` hardcodes the top-3
  peak count while `best_upload_windows` exposes a configurable `top_n`; the
  docstring also says "top-3" but the function is entered with as few as 2 rows.
  Minor inconsistency, no behavior bug. | fix: either accept a `top_n: int = 3`
  parameter for symmetry, or note in the docstring that fewer than 3 rows uses
  all available rows.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | n/a (no DB session, client, task, or file handle) |
| 2 Concurrency & scale | ok (pure sync CPU on bounded `top_n`/3-element slices; no blocking call, no I/O; input row count bounded upstream by per-creator activity rows) |
| 3 Security & compliance | ok (no tokens, no logging, no PII, no SQL; isolation enforced in router; no virality string) — 1 SEV2 input-validation finding above |
| 4 Clip-quality | n/a (not a clip/dna/preference module; deterministic timing, no principle citation required) |
| 5 Anthropic SDK | n/a (no LLM call — docstring explicitly "no LLM") |
| 6 Cleanliness & typing | 3 cleanup findings (bare `list` typing, duplicated sort, top_n inconsistency); no TODO/print/dead code |
| 7 Error handling / API | not a router, but the SEV2 IndexError propagates to the router as an unhandled 500 — counted under category 3/7 |
| 8 Config & paths | n/a (no config, no filesystem paths) |

## Module verdict
NEEDS-WORK — logic is clean, isolation-safe, and well-tested; one SEV2
(unvalidated `day_of_week`/`hour` can `IndexError` into a 500) plus three typing/
DRY cleanups are the only items, no BLOCKER.
