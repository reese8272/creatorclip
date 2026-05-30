# preference — assessed 2026-05-30

Re-assessment following Issue 78a (per-(creator,version) scorer cache) and Issue 86 (live
progress streaming — did not touch this module). Slice: `preference/__init__.py`,
`preference/_scorer_cache.py`, `preference/decay.py`, `preference/features.py`,
`preference/model.py`, `preference/train.py`. Callers in `clip_engine/ranking.py` and
`worker/tasks.py` were traced for wiring confirmation only (those files belong to other
agents and are not scored here).

## Findings

- [SEV2] preference/model.py:46 — `_ALLOWED_CLASSES` is a hand-derived allowlist coupled to
  exact sklearn / lightgbm / numpy internal module paths
  (`sklearn.linear_model._logistic`, `lightgbm.sklearn`, `lightgbm.basic`,
  `numpy._core.multiarray`, `joblib.numpy_pickle`, `collections.defaultdict|OrderedDict`).
  Any library upgrade that relocates one of these symbols will make every legitimate
  `from_bytes` raise `UnpicklingError`, which `load_latest:159-163` then swallows into a
  silent DNA fallback — personalization disappears with no alert. The existing round-trip
  tests (`tests/test_preference.py:146,156`) both fit with `n=10, threshold=20`, exercising
  only the LogisticRegression branch; the LightGBM branch (`lightgbm.sklearn.LGBMClassifier`,
  `lightgbm.basic.Booster`, `collections.defaultdict|OrderedDict`) and the
  `numpy._core.multiarray.scalar` path are **not** round-tripped, so a relocation of any of
  those would pass CI and silently degrade in prod. Carry-forward from the prior assessment.
  | fix: add `tests/test_preference.py::test_scorer_round_trips_lightgbm` that calls
  `fit(X, y, w, threshold=1)` (or `threshold=len(y)`) with sufficient data to force the LGBM
  branch, then asserts `from_bytes(scorer.to_bytes()).predict_score(feats)` matches the
  original. Same shape as the existing logistic round-trip — the assertion is what catches
  a future module-path move at build time instead of in production.

- [SEV2] preference/_scorer_cache.py:23 — module-level `_cache` is keyed by
  `(creator_id, version)` and shared by **all** creators on the worker process. With Celery
  `worker_concurrency` > 1 (the prefork pool default) each forked child has its own copy, so
  isolation is by-process; but the in-process LRU has no ceiling on total memory — only on
  entry count (`PREFERENCE_SCORER_CACHE_SIZE=128`). A LightGBM booster with 100 trees plus
  the sklearn pipeline can easily be a few MB; 128 entries × N workers × N replicas can grow
  to hundreds of MB of resident memory per pod. Worker `prefetch_multiplier=1` keeps this
  bounded today, but a future tuning that raises concurrency will silently inflate footprint.
  | fix: pick one — (a) document the per-entry-size estimate in the docstring and lower the
  default to a size that matches the expected concurrent-creator working set per worker
  (e.g. 32), or (b) gate the cap on bytes using `sys.getsizeof` + entry-size estimation, or
  (c) clarify in the docstring that this is a per-worker-process cache and that the operator
  must size `PREFERENCE_SCORER_CACHE_SIZE × worker_concurrency × replicas × ~few-MB` against
  pod memory. Option (a) is the lowest-risk default for the current single-tenant-per-worker
  posture.

- [cleanup] preference/__init__.py:1 — package marker is empty (1 line). Not a defect;
  flagged only because the contract asks every file be walked. No action needed.

- [cleanup] preference/model.py:37 — `_UNPICKLER_LOCK` doc-comment says the lock prevents a
  concurrent load from restoring the unrestricted unpickler mid-load; the implementation is
  correct, but the lock is per-process and the swap is to a module-global of
  `joblib.numpy_pickle`. If any other code in the same process called `joblib.load` while
  this lock was held it would also pick up the restricted unpickler (currently nothing else
  in the codebase does — verified). No action needed; tracked here so a future direct
  `joblib.load` import elsewhere triggers a second look.

## Verified correct (no finding)

- **Prior SEV2 RESOLVED — per-(creator,version) deserialize cache exists and is wired.**
  `preference/_scorer_cache.py:23,26,35` provides a thread-safe `OrderedDict` LRU bounded by
  `settings.PREFERENCE_SCORER_CACHE_SIZE`; `train.py:18,147,164` hits it on every `load_latest`,
  fetching the blob (`train.py:151`) only on a cache miss. Tests in
  `tests/test_preference_scorer_cache.py:69,86,102,115,124` cover same-version hit, new-version
  bust, schema-drift miss, no-model miss, and LRU eviction. The scale concern from the prior
  assessment is closed.
- Cache invariant is sound: a retrain in `build_and_save` (train.py:99) always assigns
  `new_version = (existing.version or 0) + 1`, so a fresh version is a fresh key and a stale
  entry can never be served. No manual invalidation needed; the version is the natural key.
- Personalization loop end-to-end: `worker/tasks.py` (`retrain_preference` → `build_and_save`)
  trains; `clip_engine/ranking.py:38-40` (`generate_and_rank_clips` → `rerank_with_preference`
  → `load_latest`) scores. Both ends real.
- Exponential recency decay applied: `decay.py:11,16` `λ = ln(2)/30`, `w = e^(-λ·age)`,
  combined with a 3× outcome multiplier (`decay.py:36-37`) and fed to `fit` as
  `sample_weight=` (`model.py:176,182`). Reaches both LogisticRegression and LightGBM paths.
- Below-threshold fallback is honest: `preference_weight` (`model.py:139-154`) returns `0.0`
  below `PERSONALIZATION_THRESHOLD_LABELS`, ramping linearly to `PREFERENCE_WEIGHT_CAP` at
  2× threshold; `rerank_with_preference` returns DNA ranking unchanged when weight is 0
  (`ranking.py:47-49`). No false personalization, no virality promise in any string.
- Pickle/unpickler lock-guard (Issue 71): `_UNPICKLER_LOCK` (`model.py:37`) serializes the
  process-global `NumpyUnpickler` swap (`model.py:126-132`), closing the race where a
  concurrent load could restore the unrestricted unpickler mid-load.
  `_RestrictedUnpickler.find_class` (`model.py:78-82`) rejects out-of-allowlist classes
  before any `__reduce__` fires. Attack vectors covered by `tests/test_preference.py:197,211`
  (os.system gadget; subprocess.Popen gadget).
- Advisory-lock version race (Issue 71): `build_and_save` takes `pg_advisory_xact_lock`
  keyed on creator_id (`train.py:88-90`) before the `max(version)+1` select+insert; the
  caller catches `IntegrityError` against the `uq_pref_model_creator_version` UNIQUE
  constraint. Belt-and-suspenders; correct.
- Schema-drift → DNA fallback: `load_latest` compares stored
  `feature_schema_jsonb.features` against current `FEATURE_NAMES` and returns `None`
  (DNA fallback) on mismatch (`train.py:138-144`). `predict_score` also guards on
  `n_features_in_` and raises rather than returning a misleading 0.5 (`model.py:100-104`);
  `rerank_with_preference` catches and keeps the DNA ranking (`ranking.py:66-70`). Honest on
  both layers.
- Per-creator isolation: every query filters `creator_id` — `build_and_save` feedback select
  (`train.py:42`), version select (`train.py:95`), `load_latest` version+schema lookup
  (`train.py:127`), `load_latest` blob fetch (`train.py:153`). The cache key includes
  `creator_id` so no cross-tenant scorer can be served from cache. Parameterized SQL
  throughout; the one raw `text()` is the advisory lock with a bound `:k` param
  (`train.py:88-90`).
- Resource lifecycle: training session opened by `worker/tasks.py` via
  `async with db.AsyncSessionLocal()` and committed/rolled back there; this module commits
  inside `build_and_save` (`train.py:109`) but does not own the session — correct
  separation. Cache uses a stdlib `threading.Lock`, no external resources. External model
  libs imported lazily inside `fit` (`model.py:173,179`); no per-call client construction.
- Concurrency: the CPU-heavy `fit` runs on the worker's singleton loop via
  `run_until_complete` (celery_app) under `worker_prefetch_multiplier=1`, so one task per
  worker process — no co-resident coroutine for the blocking fit to starve. The
  `from_bytes` joblib deserialize is bracketed by `_UNPICKLER_LOCK`, so even if a future
  change runs multiple loads concurrently the swap-and-restore is atomic. Cache LRU
  operations are guarded by `_lock`. Acceptable for a background worker.
- Logging: no creator email/identity or token in any `logger.*` line; ids logged are UUIDs
  (`train.py:73,112,141`; `model.py:177,183`). No PII or secret in any log statement.
- Config: `PERSONALIZATION_THRESHOLD_LABELS`, `PREFERENCE_WEIGHT_CAP`, and the new
  `PREFERENCE_SCORER_CACHE_SIZE` are all in `config.py` with defaults and in `.env.example`
  with descriptions (verified lines 71-72 of `.env.example`, line 94 of `config.py`).

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok |
| 2 Concurrency & scale | 1 finding (SEV2 — cache memory ceiling is entry-count, not bytes) |
| 3 Security & compliance | 1 finding (SEV2 — LightGBM round-trip not in CI; silent-degrade risk on lib upgrade) |
| 4 Clip-quality | ok (recency decay + honest below-threshold fallback verified) |
| 5 Anthropic SDK | n/a (module makes no LLM calls) |
| 6 Cleanliness & typing | ok (2 cleanup notes; all signatures typed) |
| 7 Error handling / API | n/a (no router surface in this module) |
| 8 Config & paths | ok (cache size in .env.example with description) |

## Module verdict
NEEDS-WORK — the prior SEV2 (per-version deserialize cache) is resolved and well-tested.
Two SEV2s remain: the allowlist still needs a LightGBM round-trip in CI to avoid silent
personalization loss on a library upgrade, and the new cache's memory bound is by entry
count not bytes, which will inflate footprint if worker concurrency is raised later.
