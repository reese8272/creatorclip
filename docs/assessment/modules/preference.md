# preference — assessed 2026-06-08

## Findings

No new findings since 2026-06-07. Prior assessment stands.

Remaining cleanups (non-blocking):
- [cleanup] model.py:88 — `Any` used for `PreferenceScorer.__init__`'s `model` parameter | fix: define a `_ProbaModel` Protocol with `predict_proba` and `n_features_in_`.
- [cleanup] train.py:80 — cold-start log collapses two distinct failure modes | fix: split the branch to explicitly say "single-class feedback" vs "insufficient samples".
- [cleanup] features.py:6 — no length guard on `clip_features()` output vs `FEATURE_NAMES` | fix: assert `len(returned) == len(FEATURE_NAMES)` at module import.
- [cleanup] decay.py:11 — `_LAMBDA` hardcoded, not exposed via config | fix: optionally expose `RECENCY_HALF_LIFE_DAYS` via `config.settings` (default 30).

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — `session.commit()` runs on the success path; AsyncSession owned by caller (correct for injection); scorer LRU bounded by `PREFERENCE_SCORER_CACHE_SIZE` |
| 2 Concurrency & scale | ok — `asyncio.to_thread(fit, ...)` and `asyncio.to_thread(PreferenceScorer.from_bytes, ...)` keep the API event loop free; `pg_try_advisory_lock` at task level prevents concurrent runs; self-debouncing (line 401) collapses repeated clicks to no-ops; `PREFERENCE_MAX_TRAINING_LABELS=5000` cap verified; `_UNPICKLER_LOCK` serializes global joblib swap correctly |
| 3 Security & compliance | ok — `_RestrictedUnpickler.find_class` enforces allowlist before construction; no PII/token in any log; every query creator-scoped via `WHERE creator_id`; SQL parameterized including advisory-lock key |
| 4 Clip-quality | ok — exponential recency decay with 30d half-life (`e^(-ln(2)/30 * age_days)`); `preference_weight` returns 0 below threshold, ramps linearly to cap at 2× threshold (honest fallback to DNA); feature-schema drift detection returns `None` rather than scoring with stale set; `predict_score` raises on mismatch |
| 5 Anthropic SDK | n/a (no LLM calls) |
| 6 Cleanliness & typing | 4 cleanup — `Any` on `_model`; collapsed cold-start log; missing FEATURE_NAMES length guard; `_LAMBDA` not configurable. No TODO, no commented blocks, no `print()`, all public signatures typed |
| 7 Error handling / API | n/a (internal errors raised as `ValueError` / `pickle.UnpicklingError`, caught with safe fallback by callers) |
| 8 Config & paths | ok — all settings registered in config.py and documented in `.env.example`; no filesystem paths used |

## Module verdict

clean — Issue 102 mitigations remain in place and correct (`asyncio.to_thread` wraps CPU-bound fit and joblib deserialize). The task-level `pg_try_advisory_lock` (non-blocking, session-scoped) prevents concurrent runs; the transaction-scoped `pg_advisory_xact_lock` inside `build_and_save` serializes version assignment. The unpickler allowlist is comprehensive and properly protected by `_UNPICKLER_LOCK`. Per-creator isolation holds on every query. Four cleanup items remain (non-blocking); zero defects.
