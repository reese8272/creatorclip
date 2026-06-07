# upload_intel — assessed 2026-06-07

## Findings

- [SEV2] timing.py:42 — `display_hour = 12 if display_hour == 0 else display_hour` — the conversion from 24-hour to 12-hour format has a logic error: hour 0 (00:00 midnight) should map to 12 AM, but hour 12 (noon) should stay 12 PM; the current code sets display_hour=12 for hour=0 but then line 41 sets period="AM" for hours < 12, so hour=0 produces "12 AM" (correct by accident); however, hour=12 (noon) produces "12 PM" only because the code reaches the `if hour <= 12` branch — the logic is fragile and would fail if the period calculation moved | fix: use a more explicit conversion: `display_hour = hour % 12 or 12; period = "AM" if hour < 12 else "PM"` to correctly handle hour=0 (midnight) → 12 AM and hour=12 (noon) → 12 PM.
- [cleanup] timing.py:39 — The malformed-row skip (line 39 `continue`) silently drops bad data without logging | fix: add `logger.warning("Skipping malformed activity row: day=%s hour=%s", row.day_of_week, row.hour)` before the continue so ops can identify data corruption; import logging at the top of the module.
- [cleanup] timing.py:56–80 — Function `optimal_gap_hours` duplicates the validation logic from `best_upload_windows` (lines 68–70) | fix: extract `_validate_activity_row(row) -> tuple[int, int, float] | None` helper to avoid duplication (DRY).

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — no external clients or DB sessions held; module is stateless; caller (routers/upload_intel.py) manages session context |
| 2 Concurrency & scale | ok — pure functions, no shared state; no unbounded loops; bounded by top_n=3 default (best_upload_windows line 21); optimal_gap_hours processes all rows but time complexity is O(n log n) due to sort (line 31) which is reasonable for activity data (typically <100 rows) |
| 3 Security & compliance | ok — no PII logged or returned; day_of_week and hour are integers (safe); activity_index is float (aggregated metric, not creator data); creator isolation handled by caller (routers/upload_intel.py line 42 filters by creator.id) |
| 4 Clip-quality | n/a — upload timing is strategy guidance, not clip scoring |
| 5 Anthropic SDK | n/a — no LLM calls |
| 6 Cleanliness & typing | 2 cleanup — malformed-row case silently skipped (no log); validation logic duplicated between best_upload_windows and optimal_gap_hours; return types are properly annotated (list[dict], float | None); all main functions typed |
| 7 Error handling / API | ok — malformed rows are skipped rather than raising (defensive, Issue 73/75); out-of-range day_of_week or hour triggers skip (lines 39, 70); ValueError not raised, graceful degradation (returns empty list or None) |
| 8 Config & paths | ok — no config required; hardcoded _DAY_NAMES list is reasonable for locale-invariant 24-hour API response |

## Module verdict

clean — No blockers, no SEV1; 1 SEV2 (hour-to-12-hour conversion logic is fragile and could fail under refactor) and 2 cleanup (missing log on malformed rows, duplicated validation logic). The core logic is sound; error handling is defensive. SEV2 should be fixed to use explicit modulo conversion before any caller depends on 12-hour formatting. Recommend adding logger import and _validate_activity_row helper.

