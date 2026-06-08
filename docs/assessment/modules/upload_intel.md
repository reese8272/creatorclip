# upload_intel — assessed 2026-06-07

Scope: `upload_intel/__init__.py` (empty) and `upload_intel/timing.py`. This
module is pure deterministic logic over an in-memory row sequence — no DB
session, no LLM, no HTTP, no async, no filesystem. The DB query that feeds it
(and per-creator isolation) lives in `routers/upload_intel.py`, which is owned
by the routers slice.

## Findings

- [cleanup] upload_intel/timing.py:21 — return type `list[dict]` is loose;
  consumers (`routers/upload_intel.py:46`) treat each element as a fixed-shape
  record (day_of_week / day_name / hour / label / activity_index) | fix:
  tighten to `list[dict[str, Any]]` at minimum, or introduce a `TypedDict
  UploadWindow` and annotate both the return type and the appended literal.
  The same TypedDict can replace the loose `dict` inferred for `results` on
  line 33.
- [cleanup] upload_intel/timing.py:39 vs 70 — the same bounds rule
  `0 <= dow <= 6 and 0 <= hour <= 23` is encoded twice in this file (DRY) |
  fix: extract a single helper, e.g. `def _coerce_row(r) -> tuple[int, int,
  float] | None` that returns `(dow, hour, idx)` if in bounds and `None`
  otherwise. Call it from both `best_upload_windows` and `optimal_gap_hours`.
  Keeps the "what counts as a valid activity row" answer in one place.
- [cleanup] upload_intel/timing.py:68–71 — list comprehension calls
  `int(r.day_of_week)` three times and `int(r.hour)` twice per row (once
  inside the bounds check, once in the value tuple). Behaviourally fine,
  cosmetic redundancy | fix: subsumed by the `_coerce_row` helper above —
  coerce once, validate once.
- [cleanup] upload_intel/timing.py:31 — `sorted(activity_rows, key=lambda r:
  r.activity_index, ...)` runs before the per-row bounds check, so a row
  whose `activity_index` is non-numeric will raise inside `sorted()` and
  surface as a 500 — which is exactly the failure mode Issue 75d hardened
  the dow/hour legs against. Today's ingest only writes floats so the risk
  is theoretical, but the contract is asymmetric | fix: when the
  `_coerce_row` helper above lands, run it first so the sort operates on the
  validated tuple list — single validated pipeline for all three fields.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | n/a — no DB session, no external client, no
  subprocess, no temp file in this module |
| 2 Concurrency & scale | n/a — pure sync functions; input is bounded
  (`audience_activity` is at most 7×24 = 168 rows per creator); sort is
  O(n log n) on n ≤ 168 |
| 3 Security & compliance | n/a in this module — per-creator isolation lives
  in `routers/upload_intel.py` (out of slice). No tokens, no PII touched, no
  `logger` calls at all, no virality language anywhere in the strings |
| 4 Clip-quality | n/a — not a clip-engine / dna / preference module |
| 5 Anthropic SDK | n/a — docstring explicitly: "Pure deterministic logic — no
  LLM" |
| 6 Cleanliness & typing | 4 cleanup findings — all about typing tightness
  and a duplicated bounds rule. Both function signatures typed; no TODO, no
  commented-out code, no `print()`, no debug statements. Both functions are
  under 30 lines and do one thing |
| 7 Error handling / API | n/a — not a router; defensive-skip behaviour for
  out-of-range rows is correct (Issue 73 / 75 / 75d / 103) |
| 8 Config & paths | n/a — no paths, no config, no `.env` keys |

## Module verdict

clean — pure, well-bounded, defensively validated module. No blockers, no
sev1/sev2 defects. The four cleanup items collapse into one small refactor:
introduce `_coerce_row` so the dow/hour/activity_index validation lives in
one place, and tighten the return type with a `TypedDict`. None of these
change behaviour. The load-bearing security and lifecycle concerns for the
upload-intel feature live in the router, not here.
