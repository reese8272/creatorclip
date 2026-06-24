# upload_intel — assessed 2026-06-24

Scope: `upload_intel/__init__.py` (empty) and `upload_intel/timing.py` (80 lines).
Pure deterministic logic over an in-memory row sequence — no DB session, no LLM,
no HTTP, no async, no filesystem, no OAuth/token handling. The DB query that feeds
it (and per-creator isolation) lives in the callers — `routers/upload_intel.py`,
`routers/publications.py`, `chat/tools.py` — all owned by other slices and not
assessed here. Code is byte-identical to the 2026-06-09 assessment; all prior
findings re-verified at the same lines, none fixed. One NEW SEV2 surfaced this pass
in `best_upload_windows` (filter ordering) that the prior run missed.

## Findings

- [SEV2] upload_intel/timing.py:31 vs 39 — `best_upload_windows` slices the top-N
  (`[:top_n]`, line 31) BEFORE applying the malformed-row bounds check (line 39), so
  a malformed row that ranks inside the top-N by `activity_index` is discarded *after*
  the cut and the function returns **fewer than `top_n` valid windows** even when more
  valid windows exist lower in the ranking. This is inconsistent with the sibling
  `optimal_gap_hours`, which correctly filters first (lines 67–71) then slices
  (line 75). Blast radius: a degraded/under-filled upload-intel response, not a crash
  or leak | fix: filter for `0 <= dow <= 6 and 0 <= hour <= 23` first, then sort, then
  `[:top_n]` — mirror the order `optimal_gap_hours` already uses (subsumed by the
  `_coerce_row` refactor below). Add a regression test where a malformed row outranks
  `top_n` valid rows and assert `len(result) == top_n`.
- [SEV2] upload_intel/timing.py:75–80 — `optimal_gap_hours` treats the week as a line,
  not a circle, and does not cluster adjacent peaks. Two concrete wrong outputs:
  (a) peaks straddling the Sat→Sun boundary (Sat 23:00 = slot 167, Sun 00:00 = slot 0)
  yield a 167h gap when the real gap is 1h, skewing the average shown to the creator;
  (b) the common case where the top-3 peaks are consecutive hours of one evening
  (Mon 19/20/21) yields `optimal_gap_hours = 1.0` — advice to upload hourly. Bounded
  blast radius (one advisory number on the response) but user-facing and systematically
  wrong for real activity shapes | fix: (1) include the wraparound gap
  `168 - (times[-1] - times[0])` when computing gaps over the circular week, and
  (2) merge peaks within ±1h into a single cluster before taking top-3 (or take top-3
  from distinct days). Add unit tests: boundary-straddling peaks → small gap; three
  consecutive hours → single cluster → `None` or a sane fallback.
- [cleanup] upload_intel/timing.py:21, 44 — return type `list[dict]` is loose; every
  element is a fixed-shape record (day_of_week / day_name / hour / label /
  activity_index) the consumers index by key | fix: introduce
  `TypedDict UploadWindow` and annotate both the return type and the appended literal.
- [cleanup] upload_intel/timing.py:39 vs 70 — the bounds rule
  `0 <= dow <= 6 and 0 <= hour <= 23` is encoded twice in this file (DRY) | fix:
  extract `def _coerce_row(r: Any) -> tuple[int, int, float] | None` returning
  `(dow, hour, idx)` when valid else `None`; call from both functions. This single
  refactor also closes the SEV2 filter-ordering finding above.
- [cleanup] upload_intel/timing.py:68–71 — the comprehension calls `int(r.day_of_week)`
  three times and `int(r.hour)` twice per row. Behaviourally fine | fix: subsumed by
  `_coerce_row` — coerce once, validate once.
- [cleanup] upload_intel/timing.py:31, 35–36, 68–70 — the Issue-75d "skip malformed
  rows" hardening only covers out-of-range *ints*; non-coercible values still raise and
  surface as a 500: a `None`/str `activity_index` raises `TypeError` inside `sorted()`
  at line 31 before any per-row check runs, and `int(None)` at lines 35/68/70 raises
  inside the loop/comprehension. Theoretical today — `ingestion` writes typed non-null
  columns — but the contract is asymmetric with the stated hardening intent | fix: have
  `_coerce_row` wrap the coercions in `try/except (TypeError, ValueError): return None`,
  and in `best_upload_windows` run it *before* the sort so the sort operates on the
  validated tuple list — one validated pipeline for all three fields.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | n/a — no DB session, external client, subprocess, or temp file |
| 2 Concurrency & scale | ok — pure sync functions; input bounded (`audience_activity` ≤ 7×24 = 168 rows/creator); sort is O(n log n) on n ≤ 168; no `async def`, no blocking call, no fetchall/fan-out in slice |
| 3 Security & compliance | n/a in this module — per-creator isolation lives in the callers (out of slice); no tokens, no PII, zero `logger` calls, no virality language in any string |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | n/a — docstring: "Pure deterministic logic — no LLM"; verified no Anthropic import/call |
| 6 Cleanliness & typing | 4 cleanup findings (typing tightness, duplicated bounds rule, redundant coercions, asymmetric hardening); no TODO, no commented-out code, no `print()`; both functions typed and under 30 effective lines |
| 7 Error handling / API | 2 SEV2 — `best_upload_windows` filter-after-slice under-fills results; `optimal_gap_hours` circular-week / adjacent-peak defect emits wrong user-facing values. Defensive-skip of out-of-range rows otherwise correctly avoids `IndexError`→500 (Issues 73/75/75d/103) |
| 8 Config & paths | n/a — no paths, no config, no `.env` keys |

## Module verdict

NEEDS-WORK — two real SEV2s, no blockers: (1) `best_upload_windows` filters malformed
rows *after* the top-N slice, so it can return fewer than `top_n` windows when a
malformed row outranks valid ones; (2) `optimal_gap_hours` ignores week wraparound and
adjacent-peak clustering, emitting systematically wrong gap advice for boundary or
single-evening activity. Both collapse into one small `_coerce_row` + `TypedDict`
refactor that also absorbs all four cleanups. Security and lifecycle concerns for the
feature live in the callers, not here.

## Issue 75 Reconciliation (2026-06-23)

| Finding | Disposition |
|---|---|
| [SEV2] optimal_gap_hours circular-week / adjacent-peak bug (upload_intel/timing.py:75-80) | → tracked in Issue 76 (post-hardening residual SEV-2 cluster) — sub-fix (A) in that issue |
| [cleanup] loose list[dict] return type (upload_intel/timing.py:21) | → tracked in Issue 109 (deferred design cleanups) |
| [cleanup] duplicated bounds rule (upload_intel/timing.py:39 vs 70) | → tracked in Issue 109 |
| [cleanup] redundant int() coercions (upload_intel/timing.py:68-71) | → tracked in Issue 109 |
| [cleanup] asymmetric coercion hardening (upload_intel/timing.py:31,35-36,68-70) | → tracked in Issue 109 |
| [SEV2] best_upload_windows filter-after-slice under-fill (upload_intel/timing.py:31 vs 39) | NEW 2026-06-24 — not yet tracked; fold into the Issue 76 `_coerce_row` refactor |
