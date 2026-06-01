# preference — assessed 2026-05-31

## Findings

- [SEV2] preference/train.py:107 — variable `result` is re-bound at line 107 after
  being used at line 44 for a different query (the feedback fetch). Harmless but
  confusing on a re-read, and ruff/pyright will not catch it because both are
  awaited correctly | fix: rename the second one `existing_result` or inline into
  `existing = (await session.execute(...)).scalars().first()`.

- [SEV2] preference/model.py:126-132 — `from_bytes` still mutates the process-global
  `joblib.numpy_pickle.NumpyUnpickler` under `_UNPICKLER_LOCK`. Wave-9 (Issue 102)
  correctly moved the call off the event loop with `asyncio.to_thread`, which
  removes the "blocks the loop" defect — but the module-global swap remains a
  process-wide serialization point: every concurrent preference-model deserialize
  across every creator on this worker still queues behind one lock. With the
  per-worker LRU cache absorbing repeats, this is now a tail-latency papercut, not
  a scale BLOCKER. Per DECISIONS 2026-05-31, joblib 1.x exposes no public per-load
  unpickler injection slot, so the workaround is being held until joblib does
  | fix: either (a) accept as-is (current decision — documented in DECISIONS),
  (b) pin joblib and monkey-patch a `_unpickle` shim that takes an unpickler
  factory, or (c) hand-roll a `pickle.Unpickler` subclass that reads the joblib
  numpy frames directly. Re-evaluate when joblib 2.x ships.

- [cleanup] preference/model.py:88 — `PreferenceScorer.__init__(model: Any, ...)`
  loses type information that callers of `fit()` would benefit from. Acceptable
  because the model union (LogisticRegression | LGBMClassifier) is not nominally
  typed by either upstream library | fix: leave as-is, or introduce a `Protocol`
  with `predict_proba(X) -> np.ndarray` and `n_features_in_: int` to document
  the contract. Cleanup-only.

- [cleanup] preference/decay.py:11 — `_LAMBDA = math.log(2) / 30` hardcodes the
  30-day half-life. The docstring documents it and the value is intentionally
  shorter than DNA's 90 days (channel evolves faster than identity), but it is
  not surfaced as config | fix: expose as `PREFERENCE_HALFLIFE_DAYS` setting if
  tuning is anticipated; otherwise leave the constant with the docstring. Low
  priority.

- [cleanup] preference/train.py:107 — second `select(PreferenceModel)` query at
  build_and_save fetches the full row (`select(PreferenceModel)`) just to read
  `.version` for the max+1 assignment. Cheaper to project the column |
  fix: `select(func.max(PreferenceModel.version)).where(PreferenceModel.creator_id
  == creator_id)` and treat None as 0. The advisory xact lock on line 102 already
  guarantees no concurrent retrain for this creator, so the projection is safe.
  Micro-optimization.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — sessions caller-owned (no leaks); LRU cache bounded by `PREFERENCE_SCORER_CACHE_SIZE=128`; no file handles or subprocesses; advisory xact lock auto-released on commit |
| 2 Concurrency & scale | ok — Wave-9/Issue-102 fixed both prior SEV1s: `fit` and `from_bytes` both run via `asyncio.to_thread`; training query newest-first + `LIMIT settings.PREFERENCE_MAX_TRAINING_LABELS=5000` bounds memory; module-global unpickler lock is now a tail-latency papercut not a scale blocker (SEV2 above) |
| 3 Security & compliance | ok — all queries `WHERE creator_id`; cache key includes creator_id; pickle allowlist + version-bumped cache key prevents stale-model serving and cross-tenant leak; no PII in logs (creator_id UUID + counts only); restricted unpickler closes RCE on tampered weights_blob; advisory lock prevents UNIQUE(creator_id, version) race |
| 4 Clip-quality | ok — `preference_weight()` enforces honest below-threshold zero (CLAUDE.md Clip-Engine Rules), ramps linearly to `PREFERENCE_WEIGHT_CAP=0.5` at 2× threshold; 30-day exponential recency decay in `decay.py` with outcome multiplier for performed_well clips; feature-schema-drift fallback to DNA is the honest behavior; predict_score raises on n_features_in_ mismatch rather than returning misleading 0.5; no virality promise; ranking cites principle 11 (audience-fit over generic virality) by construction |
| 5 Anthropic SDK | n/a — no LLM calls in this module |
| 6 Cleanliness & typing | 3 cleanups (shadowed `result`, untyped `model: Any`, over-fetched PreferenceModel row); all functions typed; no print/TODO/commented blocks; `TRAINABLE_ACTIONS` frozenset properly used at the query (Wave-9 DRY fix verified) |
| 7 Error handling / API | n/a — no routers in this module |
| 8 Config & paths | ok — `PERSONALIZATION_THRESHOLD_LABELS=20`, `PREFERENCE_WEIGHT_CAP=0.5`, `PREFERENCE_SCORER_CACHE_SIZE=128`, `PREFERENCE_MAX_TRAINING_LABELS=5000` all in `config.py` + `.env.example` with descriptive comments; no hardcoded paths |

## Module verdict

**clean** — Wave-9/Issue-102 closed both prior SEV1s (CPU-bound fit and joblib
deserialize are now off the event loop via `asyncio.to_thread`); training data is
bounded by a newest-first LIMIT; `TRAINABLE_ACTIONS` frozenset is used at the
query. Remaining items are one SEV2 (shadowed `result` binding) and three minor
cleanups — none ship a defect at scale. The module-global unpickler lock is a
documented joblib-1.x limitation tracked in DECISIONS, not a regression.
