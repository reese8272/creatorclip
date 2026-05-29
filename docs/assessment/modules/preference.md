# preference — assessed 2026-05-29

Re-assessment after the Issues 58–75 hardening session. Slice: `preference/__init__.py`,
`preference/decay.py`, `preference/features.py`, `preference/model.py`, `preference/train.py`.
Callers traced into `clip_engine/ranking.py` and `worker/tasks.py` to confirm wiring (the
caller files themselves are owned by other agents and are not scored here).

## Findings

- [SEV2] preference/train.py:116 — `load_latest` calls `PreferenceScorer.from_bytes` on every
  invocation, and `rerank_with_preference` (clip_engine/ranking.py:39,148) calls `load_latest`
  on every clip-generation run. The joblib deserialization (LightGBM booster reconstruction)
  is repeated per video even though the blob is immutable for a given (creator_id, version).
  The task brief asked to verify "per-(creator,version) caching of from_bytes" — that cache
  does **not** exist in the current code | fix: add a process-local `functools`-style cache
  keyed on `(creator_id, version)` returning the deserialized `PreferenceScorer`. Bound it
  (e.g. `cachetools.LRUCache(maxsize=256)` or an `OrderedDict` with manual eviction) so a
  many-creator worker does not accumulate models unbounded; the version is the natural
  invalidation key (a new model is a new version row, so a stale entry is never served).

- [cleanup] preference/__init__.py:1 — package marker is effectively empty (1 blank line). Not
  a defect; flagged only because the contract asks every file to be walked. No action needed.

- [cleanup] preference/model.py:46 — `_ALLOWED_CLASSES` is a hand-derived allowlist coupled to
  the exact sklearn/lightgbm/numpy internal module paths (e.g. `sklearn.linear_model._logistic`,
  `numpy._core.multiarray`). A library upgrade that moves one of these symbols will make every
  legitimate `from_bytes` raise `UnpicklingError`, which `load_latest:138` then swallows into a
  silent DNA fallback — personalization would quietly disappear with no alert | fix: keep the
  allowlist (correct security posture) but add a CI test that round-trips a freshly-fit model of
  BOTH classes through `to_bytes`/`from_bytes` so a path move fails the build instead of
  silently degrading in prod. `tests/test_preference.py:146,160` already round-trip one model;
  extend to assert the LightGBM path too (current round-trip tests fit with `threshold=20` and
  small n, exercising only the LogisticRegression branch).

## Verified correct (no finding)

- Personalization loop is wired end-to-end: `worker/tasks.py:207` (`retrain_preference` →
  `_retrain_preference_async` → `build_and_save`) trains; `clip_engine/ranking.py:148`
  (`generate_and_rank_clips` → `rerank_with_preference` → `load_latest`) scores. Both ends real.
- Exponential recency decay applied: `decay.py:11,16` `λ = ln(2)/30`, `w = e^(-λ·age)`, combined
  with a 3× outcome multiplier in `sample_weight` (decay.py:26) and fed to `fit` as
  `sample_weight=` (model.py:176,182). Confirmed reaching both LogisticRegression and LightGBM.
- Below-threshold fallback is honest and explicit: `preference_weight` (model.py:139) returns
  `0.0` below `PERSONALIZATION_THRESHOLD_LABELS`, ramping linearly to `PREFERENCE_WEIGHT_CAP` at
  2× threshold; `rerank_with_preference` returns the DNA ranking unchanged when weight is 0
  (ranking.py:47). No false personalization, no virality promise in any string.
- Pickle/unpickler lock-guard (Issue 71): `_UNPICKLER_LOCK` (model.py:37) serializes the
  process-global `NumpyUnpickler` swap in `from_bytes` (model.py:126-132), closing the race
  where a concurrent load restores the unrestricted unpickler mid-load. `_RestrictedUnpickler`
  rejects out-of-allowlist classes in `find_class` before any `__reduce__` fires (model.py:78-82).
  Attack vector covered by `tests/test_preference.py:197,211`.
- Advisory-lock version race (Issue 71): `build_and_save` takes `pg_advisory_xact_lock` keyed on
  creator_id (train.py:87) before the `max(version)+1` select+insert, and the caller catches
  `IntegrityError` against the `uq_pref_model_creator_version` UNIQUE constraint
  (worker/tasks.py:208, models.py:433). Belt-and-suspenders; correct.
- Schema-drift → DNA fallback: `load_latest` compares stored `feature_schema_jsonb.features`
  against current `FEATURE_NAMES` and returns `None` (DNA fallback) on mismatch
  (train.py:129-135). `predict_score` also guards on `n_features_in_` and raises rather than
  returning a misleading 0.5 (model.py:101-104); `rerank_with_preference` catches and keeps the
  DNA ranking (ranking.py:65-69). Honest on both layers.
- Per-creator isolation: every query filters creator_id — `build_and_save` feedback select
  (train.py:41), version select (train.py:94), `load_latest` (train.py:119). Parameterized SQL
  throughout; the one raw `text()` is the advisory lock with a bound `:k` param (train.py:88).
- Resource lifecycle: training session opened via `async with db.AsyncSessionLocal()`
  (worker/tasks.py:177), committed in `build_and_save` (train.py:108), rolled back on the race
  path (worker/tasks.py:210). No leak. External model libs imported lazily inside `fit`; no
  per-call client construction.
- Concurrency: the CPU-heavy `fit` runs on the worker's singleton loop via
  `run_until_complete` (celery_app.py:70) under `worker_prefetch_multiplier=1`, so one task per
  worker process — no co-resident coroutine for the blocking fit to starve. Acceptable for a
  background worker; not flagged.
- Logging: no creator email/identity or token in any `logger.*` line; ids logged are UUIDs
  (train.py:71,111; ranking.py warnings carry only the exception text).

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok |
| 2 Concurrency & scale | 1 finding (SEV2 — repeated deserialize, no cache) |
| 3 Security & compliance | ok (isolation + restricted unpickler + lock-guard verified) |
| 4 Clip-quality | ok (recency decay + honest below-threshold fallback verified) |
| 5 Anthropic SDK | n/a (module makes no LLM calls) |
| 6 Cleanliness & typing | ok (2 cleanup notes; all signatures typed) |
| 7 Error handling / API | n/a (no router surface in this module) |
| 8 Config & paths | ok (PERSONALIZATION_THRESHOLD_LABELS, PREFERENCE_WEIGHT_CAP in .env.example) |

## Module verdict
NEEDS-WORK — security/correctness hardening (Issue 71) is solid and verified, but the
per-(creator,version) deserialization cache the brief expected is not present, and the
internal-path allowlist needs a both-model round-trip test to avoid silent personalization loss
on a library upgrade.
