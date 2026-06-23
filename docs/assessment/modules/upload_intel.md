# upload_intel — assessed 2026-06-09

Scope: `upload_intel/__init__.py` (empty) and `upload_intel/timing.py` (80
lines). Pure deterministic logic over an in-memory row sequence — no DB
session, no LLM, no HTTP, no async, no filesystem. The DB query that feeds it
(and per-creator isolation) lives in `routers/upload_intel.py`, owned by the
routers slice. Code is byte-identical to the 2026-06-07 assessment; all four
prior cleanup findings re-verified at the same lines, none fixed. One new
SEV2 found in the gap heuristic.

## Findings

- [SEV2] upload_intel/timing.py:75–80 — `optimal_gap_hours` treats the week as
  a line, not a circle, and does not cluster adjacent peaks. Two concrete wrong
  outputs: (a) peaks straddling the Sat→Sun boundary (e.g. Sat 23:00 = 167,
  Sun 00:00 = 0) yield a gap of 167h when the real gap is 1h, skewing the
  average shown to the creator; (b) the common case where the top-3 peaks are
  consecutive hours of the same evening (Mon 19/20/21) yields
  `optimal_gap_hours = 1.0` — advice to upload hourly. Bounded blast radius
  (one advisory number on the upload-intel response) but it is user-facing and
  systematically wrong for real activity shapes | fix: (1) include the
  wraparound gap `168 - (times[-1] - times[0])` when computing gaps over the
  circular week, and (2) merge peaks within ±1h into a single cluster before
  taking top-3 (or take the top-3 from distinct days). Add two unit tests:
  boundary-straddling peaks → small gap; three consecutive hours → single
  cluster → `None` or a sane fallback.
- [cleanup] upload_intel/timing.py:21 — return type `list[dict]` is loose;
  the consumer (`routers/upload_intel.py:46`) treats each element as a
  fixed-shape record (day_of_week / day_name / hour / label /
  activity_index) | fix: introduce a `TypedDict UploadWindow` and annotate
  both the return type and the appended literal at line 44.
- [cleanup] upload_intel/timing.py:39 vs 70 — the bounds rule
  `0 <= dow <= 6 and 0 <= hour <= 23` is encoded twice in this file (DRY) |
  fix: extract `def _coerce_row(r: Any) -> tuple[int, int, float] | None`
  returning `(dow, hour, idx)` when valid, `None` otherwise; call it from
  both `best_upload_windows` and `optimal_gap_hours`.
- [cleanup] upload_intel/timing.py:68–71 — the comprehension calls
  `int(r.day_of_week)` three times and `int(r.hour)` twice per row (once in
  the bounds check, once in the value tuple). Behaviourally fine | fix:
  subsumed by `_coerce_row` — coerce once, validate once.
- [cleanup] upload_intel/timing.py:31, 35–36, 68–70 — the Issue-75d "skip
  malformed rows" hardening only covers out-of-range *ints*; non-coercible
  values still raise and surface as a 500: a `None`/str `activity_index`
  raises `TypeError` inside `sorted()` at line 31 before any per-row check
  runs, and `int(None)` at lines 35/68/70 raises inside the loop /
  comprehension, killing the whole call. Theoretical today — `ingestion`
  writes typed non-null columns — but the contract is asymmetric with the
  stated hardening intent | fix: have `_coerce_row` wrap the coercions in
  `try/except (TypeError, ValueError): return None`, and in
  `best_upload_windows` run it *before* the sort so the sort operates on the
  validated tuple list — one validated pipeline for all three fields.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | n/a — no DB session, external client, subprocess, or temp file |
| 2 Concurrency & scale | ok — pure sync functions; input bounded (`audience_activity` ≤ 7×24 = 168 rows/creator); sort is O(n log n) on n ≤ 168 |
| 3 Security & compliance | n/a in this module — per-creator isolation lives in `routers/upload_intel.py` (out of slice); no tokens, no PII, zero `logger` calls, no virality language in any string |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | n/a — docstring: "Pure deterministic logic — no LLM" |
| 6 Cleanliness & typing | 4 cleanup findings (typing tightness, duplicated bounds rule, asymmetric coercion hardening); no TODO, no commented-out code, no `print()`; both functions typed and under 30 effective lines |
| 7 Error handling / API | 1 SEV2 — `optimal_gap_hours` circular-week / adjacent-peak defect produces wrong user-facing values; defensive-skip of out-of-range rows otherwise correct (Issues 73/75/75d/103) |
| 8 Config & paths | n/a — no paths, no config, no `.env` keys |

## Module verdict

NEEDS-WORK — one real SEV2: `optimal_gap_hours` ignores week wraparound and
adjacent-peak clustering, so it emits systematically wrong gap advice for
boundary-straddling or single-evening activity shapes. The four cleanup items
collapse into one small refactor (`_coerce_row` + `TypedDict UploadWindow`)
that also closes the asymmetric-hardening gap. No blockers; security and
lifecycle concerns for the feature live in the router, not here.

## Issue 75 Reconciliation (2026-06-23)

| Finding | Disposition |
|---|---|
| [SEV2] optimal_gap_hours circular-week / adjacent-peak bug (upload_intel/timing.py:75-80) | → tracked in Issue 76 (post-hardening residual SEV-2 cluster) — sub-fix (A) in that issue |
| [cleanup] loose list[dict] return type (upload_intel/timing.py:21) | → tracked in Issue 109 (deferred design cleanups) |
| [cleanup] duplicated bounds rule (upload_intel/timing.py:39 vs 70) | → tracked in Issue 109 |
| [cleanup] redundant int() coercions (upload_intel/timing.py:68-71) | → tracked in Issue 109 |
| [cleanup] asymmetric coercion hardening (upload_intel/timing.py:31,35-36,68-70) | → tracked in Issue 109 |
