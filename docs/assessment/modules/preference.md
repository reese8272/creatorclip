# preference — assessed 2026-05-31 (Wave 4 re-verification)

Wave 4 did not touch this module — `git log 67fddc9..HEAD -- preference/` returns empty,
so the slice is byte-identical to the Wave 3 baseline (last touched by `eb0953f` Issue 78c
and `a4fcb56` Issue 78a, both pre-baseline). Slice walked end-to-end:
`preference/__init__.py` (empty package marker, 1 line, no code),
`preference/_scorer_cache.py`, `preference/decay.py`, `preference/features.py`,
`preference/model.py`, `preference/train.py`. Callers in `clip_engine/ranking.py` and
`worker/tasks.py` traced for wiring confirmation only — they belong to other agents and
are not scored here. Both carry-forward SEV2s re-verified by re-reading the same line
numbers in the unchanged files and by re-running the supporting greps:
`grep -nE "lightgbm|LGBMClassifier|LightGBM" tests/test_preference.py tests/test_preference_scorer_cache.py`
returns zero matches (the LightGBM branch is still not round-tripped in CI, so the
silent-degrade risk on a library upgrade is still live); `grep -rn "joblib.load|joblib.dump"`
outside `preference/` returns zero hits (the process-global unpickler swap in `from_bytes`
still has only the one in-tree caller at `preference/model.py:130`). Config still wired:
`PERSONALIZATION_THRESHOLD_LABELS=20`, `PREFERENCE_WEIGHT_CAP=0.5`,
`PREFERENCE_SCORER_CACHE_SIZE=128` present in `config.py:108,112,116` and `.env.example:10,73,74`.

## Findings

- [SEV2] preference/model.py:46 — `_ALLOWED_CLASSES` is a hand-derived allowlist coupled to
  exact sklearn / lightgbm / numpy internal module paths
  (`sklearn.linear_model._logistic`, `lightgbm.sklearn`, `lightgbm.basic`,
  `numpy._core.multiarray`, `joblib.numpy_pickle`, `collections.defaultdict|OrderedDict`).
  Any library upgrade that relocates one of these symbols will make every legitimate
  `from_bytes` raise `UnpicklingError`, which `load_latest` (`train.py:159-163`) then
  swallows into a silent DNA fallback — personalization disappears with no alert. The
  existing round-trip tests in `tests/test_preference.py` only exercise the
  LogisticRegression branch (`n=10`, `threshold=20`); the LightGBM branch
  (`lightgbm.sklearn.LGBMClassifier`, `lightgbm.basic.Booster`,
  `collections.defaultdict|OrderedDict`) and the `numpy._core.multiarray.scalar` path are
  **not** round-tripped, so a relocation of any of those would pass CI and silently degrade
  in prod. Rubric category 3 (security/compliance — the allowlist is the RCE guard, and a
  failure mode that maps "blob rejected" onto "no personalization" is a correctness
  defect we cannot observe). Carry-forward from 2026-05-30 (Wave 1), 2026-05-31 (Wave 2),
  and 2026-05-31 (Wave 3); re-verified Wave 4: still no LightGBM symbol in
  `tests/test_preference.py` or `tests/test_preference_scorer_cache.py`. | fix: add
  `tests/test_preference.py::test_scorer_round_trips_lightgbm` that calls
  `fit(X, y, w, threshold=1)` (or `threshold=len(y)`) with enough samples in both classes
  to force the LGBM branch, then asserts
  `from_bytes(scorer.to_bytes()).predict_score(feats)` equals the original within fp
  tolerance. Same shape as the existing logistic round-trip — the assertion catches a
  future module-path move at build time instead of in production. Additionally,
  `load_latest` should emit a metric on "preference_deserialize_failed" so silent-degrade
  is observable (the WARNING log at `train.py:162` is necessary but not sufficient — a
  metric is required for alerting). The test is the primary fix; the metric is the
  secondary mitigation.

- [SEV2] preference/_scorer_cache.py:23 — module-level `_cache` is keyed by
  `(creator_id, version)` and shared by all creators on the worker process; with Celery
  `worker_concurrency` > 1 each forked child has its own copy, so isolation is by process,
  but the in-process LRU has no ceiling on total memory — only on entry count
  (`PREFERENCE_SCORER_CACHE_SIZE=128`, enforced at `_scorer_cache.py:40` as
  `while len(_cache) > settings.PREFERENCE_SCORER_CACHE_SIZE`). A LightGBM booster with
  100 trees plus the sklearn pipeline can easily be a few MB; 128 entries × N workers ×
  N replicas can grow to hundreds of MB resident per pod. Today
  `worker_prefetch_multiplier=1` keeps this bounded, but a future tuning that raises
  concurrency will silently inflate footprint. Rubric category 2 (concurrency & scale —
  bounded work / no unbounded in-memory accumulation per creator). Carry-forward from
  2026-05-30 (Wave 1), 2026-05-31 (Wave 2), and 2026-05-31 (Wave 3); re-verified Wave 4:
  `_scorer_cache.py:40` still uses entries, not bytes. | fix: pick one — (a) document
  per-entry estimate in the docstring and lower the default to match the expected
  concurrent-creator working set per worker (e.g. 32), (b) gate the cap on bytes using a
  stored size estimate at `put` time (`scorer._size = len(scorer.to_bytes())` cached once)
  summed across the OrderedDict, or (c) clarify in the docstring that this is a
  per-worker-process cache and the operator must size
  `PREFERENCE_SCORER_CACHE_SIZE × worker_concurrency × replicas × ~few-MB` against pod
  memory. Option (a) is the lowest-risk default for the current single-tenant-per-worker
  posture.

- [cleanup] preference/__init__.py:1 — empty package marker (1 line, zero code). Not a
  defect; flagged only because the contract asks every file be walked. No action needed.

- [cleanup] preference/model.py:37 — `_UNPICKLER_LOCK` correctly serializes the
  process-global `NumpyUnpickler` swap, but if any other code in the same process called
  `joblib.load` while the lock was held it would also pick up the restricted unpickler
  (currently nothing else in the codebase does — re-verified Wave 4 with
  `grep -rn "joblib.load|joblib.dump" --include="*.py"`: only `preference/model.py:130`
  uses `joblib.load` and only `preference/model.py:111` uses `joblib.dump` for production
  code; the three other `joblib.dump` hits are inside `tests/test_preference.py` crafting
  malicious blobs to exercise the allowlist). No action needed; tracked here so a future
  direct `joblib.load` import elsewhere triggers a second look.

## Verified correct (no finding)

- **Prior SEV2 RESOLVED (Issue 78a) — per-(creator,version) scorer cache exists and is wired.**
  `preference/_scorer_cache.py:22-23,26,35` provides a thread-safe `OrderedDict` LRU bounded
  by `settings.PREFERENCE_SCORER_CACHE_SIZE`; `train.py:18,146-149,164` hits it on every
  `load_latest`, fetching the blob (`train.py:151`) only on a cache miss. Tests in
  `tests/test_preference_scorer_cache.py` cover same-version hit, new-version bust,
  schema-drift miss, no-model miss, and LRU eviction.
- Cache invariant is sound: a retrain in `build_and_save` (`train.py:99`) always assigns
  `new_version = (existing.version + 1) if existing else 1`, so a fresh version is a fresh
  key and a stale entry can never be served. No manual invalidation needed; the version is
  the natural key.
- Personalization loop end-to-end: `worker/tasks.py` (`retrain_preference` →
  `build_and_save`) trains; `clip_engine/ranking.py:27-79` (`generate_and_rank_clips` →
  `rerank_with_preference` → `load_latest`) scores. Both ends real, both ends gated by
  `preference_weight`.
- Exponential recency decay applied (rubric category 4): `decay.py:11` `_LAMBDA = ln(2)/30`,
  `decay.py:16` `w = e^(-λ·age)`, combined with a 3× outcome multiplier (`decay.py:36-37`)
  and fed to `fit` via `sample_weight=` (`model.py:176,182`). Reaches both
  LogisticRegression and LightGBM paths.
- Below-threshold fallback is honest (rubric category 4): `preference_weight`
  (`model.py:139-154`) returns `0.0` below `PERSONALIZATION_THRESHOLD_LABELS=20`, ramping
  linearly to `PREFERENCE_WEIGHT_CAP=0.5` at 2× threshold; `rerank_with_preference` returns
  DNA ranking unchanged when weight is 0 (`clip_engine/ranking.py:47-49`). No false
  personalization, no virality promise in any string.
- Pickle/unpickler lock-guard (Issue 71): `_UNPICKLER_LOCK` (`model.py:37`) serializes the
  process-global `NumpyUnpickler` swap (`model.py:126-132`), closing the race where a
  concurrent load could restore the unrestricted unpickler mid-load.
  `_RestrictedUnpickler.find_class` (`model.py:78-82`) rejects out-of-allowlist classes
  before any `__reduce__` fires.
- Advisory-lock version race (Issue 71): `build_and_save` takes `pg_advisory_xact_lock`
  keyed on `hashtext(creator_id)` (`train.py:88-90`) before the `max(version)+1`
  select+insert, eliminating the race against the
  `UNIQUE(creator_id, version)` constraint.
- Schema-drift → DNA fallback: `load_latest` compares stored
  `feature_schema_jsonb.features` against current `FEATURE_NAMES` and returns `None`
  (DNA fallback) on mismatch (`train.py:138-144`). `predict_score` also guards on
  `n_features_in_` and raises rather than returning a misleading 0.5
  (`model.py:100-104`); `rerank_with_preference` catches and keeps the DNA ranking
  (`clip_engine/ranking.py:66-70`). Honest on both layers.
- Per-creator isolation (rubric category 3): every query filters `creator_id` —
  `build_and_save` feedback select (`train.py:42`), version select (`train.py:95`),
  `load_latest` version+schema lookup (`train.py:127`), `load_latest` blob fetch
  (`train.py:153`). The cache key includes `creator_id` so no cross-tenant scorer can be
  served from cache. Parameterized SQL throughout; the one raw `text()` is the advisory
  lock with a bound `:k` param (`train.py:88-90`).
- Resource lifecycle (rubric category 1): training session opened by `worker/tasks.py` via
  `async with db.AsyncSessionLocal()` and committed/rolled back there; this module commits
  inside `build_and_save` (`train.py:109`) but does not own the session — correct
  separation. Cache uses a stdlib `threading.Lock`, no external resources. External model
  libs imported lazily inside `fit` (`model.py:173,179`); no per-call client construction.
- Concurrency (rubric category 2): the CPU-heavy `fit` runs on the worker's singleton loop
  under `worker_prefetch_multiplier=1`, so one task per worker process — no co-resident
  coroutine for the blocking fit to starve. The `from_bytes` joblib deserialize is
  bracketed by `_UNPICKLER_LOCK`. Cache LRU operations are guarded by `_lock`. Acceptable
  for a background worker.
- Logging (rubric category 3): no creator email/identity or token in any `logger.*` line;
  ids logged are UUIDs (`train.py:73,112,141`; `model.py:177,183`). No PII or secret.
- Config (rubric category 8): `PERSONALIZATION_THRESHOLD_LABELS=20`,
  `PREFERENCE_WEIGHT_CAP=0.5`, and `PREFERENCE_SCORER_CACHE_SIZE=128` are all in
  `config.py:108,112,116` with defaults and in `.env.example:10,73,74` with descriptions
  (re-verified Wave 4).

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
NEEDS-WORK — no regressions, no Wave 4 changes to this module (slice byte-identical to
baseline `67fddc9`); the two SEV2s carry forward unchanged from 2026-05-30 (Wave 1),
2026-05-31 (Wave 2), and 2026-05-31 (Wave 3): the allowlist still needs a LightGBM
round-trip in CI to avoid silent personalization loss on a library upgrade, and the
cache's memory bound is by entry count not bytes, which will inflate footprint if worker
concurrency is raised later.
