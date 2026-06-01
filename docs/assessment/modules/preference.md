# preference — assessed 2026-06-01

## Findings

- [SEV2] preference/model.py:126-132 — `from_bytes` mutates process-global `joblib.numpy_pickle.NumpyUnpickler` under `_UNPICKLER_LOCK`. Module-global swap serializes all concurrent preference-model deserializes across every creator on the worker. With per-worker LRU cache absorbing repeats, this is a tail-latency papercut, not a scale blocker. Per DECISIONS 2026-05-31, joblib 1.x exposes no public per-load unpickler injection slot | fix: documented carry-forward — hold until joblib 2.x ships; consider hand-rolled pickle.Unpickler subclass if tail-latency becomes observable.

- [SEV2] preference/train.py:107 — variable `result` is re-bound at line 107 after being used at line 44 for the feedback fetch query. Harmless but confusing on re-read | fix: rename to `existing_result` or inline: `existing = (await session.execute(...)).scalars().first()`.

- [cleanup] preference/model.py:88 — `PreferenceScorer.__init__(model: Any, ...)` loses type information. Acceptable because LogisticRegression | LGBMClassifier union is not nominally typed by upstream libraries | fix: leave as-is, or add Protocol with `predict_proba(X) -> ndarray` and `n_features_in_: int` signatures.

- [cleanup] preference/decay.py:11 — `_LAMBDA = math.log(2) / 30` hardcodes 30-day half-life (intentionally shorter than DNA's 90 days). Not surfaced as config | fix: expose as `PREFERENCE_HALFLIFE_DAYS` setting if tuning is anticipated; otherwise leave with docstring.

- [cleanup] preference/train.py:107-113 — queries full PreferenceModel row just to read `.version` for max+1 assignment | fix: `select(func.max(PreferenceModel.version)).where(PreferenceModel.creator_id == creator_id)` and treat None as 0. Advisory xact lock already guarantees no concurrent retrain.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — sessions caller-owned (no leaks); LRU cache bounded by `PREFERENCE_SCORER_CACHE_SIZE=128`; no file handles or subprocesses; advisory xact lock auto-released on commit |
| 2 Concurrency & scale | ok — Issue-102 fixed: `fit` and `from_bytes` both run via `asyncio.to_thread`; training query newest-first + `LIMIT PREFERENCE_MAX_TRAINING_LABELS=5000` bounds memory; module-global unpickler lock is documented joblib-1.x limitation, not a scale blocker with cache in place |
| 3 Security & compliance | ok — all queries `WHERE creator_id`; cache key includes creator_id; pickle allowlist + version-bumped cache prevent stale-model serving and cross-tenant leak; no PII in logs (UUID + counts only); restricted unpickler closes RCE on tampered weights_blob; advisory lock prevents UNIQUE(creator_id, version) race |
| 4 Clip-quality | ok — `preference_weight()` enforces honest zero below-threshold (CLAUDE.md Clip-Engine Rules), ramps linearly to `PREFERENCE_WEIGHT_CAP=0.5` at 2× threshold; 30-day exponential recency decay with outcome multiplier for performed_well clips; feature-schema-drift fallback to DNA is honest behavior; predict_score raises on n_features_in_ mismatch rather than misleading 0.5; no virality promise |
| 5 Anthropic SDK | n/a — no LLM calls in this module |
| 6 Cleanliness & typing | 3 cleanups (shadowed `result`, untyped `model: Any`, over-fetched row); all functions fully typed; no print/TODO/commented blocks; `TRAINABLE_ACTIONS` frozenset properly used at query |
| 7 Error handling / API | n/a — no routers in this module |
| 8 Config & paths | ok — all settings in config.py + .env.example with descriptions; no hardcoded paths; `PERSONALIZATION_THRESHOLD_LABELS=20`, `PREFERENCE_WEIGHT_CAP=0.5`, `PREFERENCE_SCORER_CACHE_SIZE=128`, `PREFERENCE_MAX_TRAINING_LABELS=5000` all present |

## Module verdict

clean — No new defects this cycle. Two SEV2 carry-forwards (documented joblib limitation + shadowed variable) and three cleanup items (all low-risk cosmetic fixes) remain. Module correctly handles creator isolation, scales training data bounded by LIMIT, offloads CPU and lock-contended work from the event loop, and uses honest fallback to DNA when personalization data is insufficient.
