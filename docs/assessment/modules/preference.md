# preference — assessed 2026-06-02

## Findings

### Correctness & Recency Decay
- **SEV1** preference/decay.py — recency_weight uses exponential decay correctly with 30-day half-life, tested thoroughly. No issue.
- **SEV1** preference/model.py::preference_weight — ramp formula correctly bounds output to [0, PREFERENCE_WEIGHT_CAP]. At threshold (20 labels) weight is 0.0, reaches cap (0.5) at 2× threshold (40 labels). Matches design spec. No issue.

### Concurrency & Async Safety
- **ok** preference/train.py::build_and_save — offloads CPU-bound fit() to asyncio.to_thread, preserving event loop integrity. Serializes retrains with pg_advisory_xact_lock to prevent UNIQUE(creator_id, version) race. No blocking call in async context.
- **ok** preference/train.py::load_latest — offloads joblib.load to asyncio.to_thread, respecting the _UNPICKLER_LOCK (module-global threading.Lock). Two deserializations of the same blob never race.
- **ok** preference/_scorer_cache.py — LRU cache protected by threading.Lock, bounded to PREFERENCE_SCORER_CACHE_SIZE. Eviction is FIFO (LRU), stale entries after retrain are keys (creator_id, version) which can never collide with new versions (version monotonically increases). No stale-model serving.
- **ok** preference/model.py::from_bytes — swaps joblib.numpy_pickle.NumpyUnpickler under _UNPICKLER_LOCK for the duration of load. Guarantees all joblib internal code paths use the restricted unpickler. Lock serializes threads; coroutines queued via to_thread do not block each other on the event loop.

### Security & Isolation
- **ok** preference/train.py::build_and_save — query filters .where(ClipFeedback.creator_id == creator_id). Per-creator isolation on feedback fetch. Single load_latest query per creator.
- **ok** preference/train.py::load_latest — queries filter .where(PreferenceModel.creator_id == creator_id). Two queries (metadata, blob) both scoped to creator_id. No cross-creator leakage.
- **ok** preference/model.py::_RestrictedUnpickler — allowlist of 12 (module, name) tuples, frozen at module load. Only sklearn LogisticRegression, LightGBM LGBMClassifier + Booster, joblib NumpyArrayWrapper, numpy arrays/dtypes, and collections allowed. Any other class → UnpicklingError before object instantiation. RCE surface closed.
- **ok** preference/train.py::build_and_save — parameterized SQL: text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": str(creator_id)}. No string interpolation.
- **ok** No PII in logs. Logs show creator_id (UUID), counts, version numbers. No clip signals, feedback text, or transcript content logged.

### Resource Lifecycle
- **ok** preference/train.py::build_and_save — session.add() + session.commit() issued. Session lifecycle managed by AsyncSession context manager (caller's responsibility, not this module's). No explicit session close needed; AsyncSession.__aexit__ releases the connection.
- **ok** preference/train.py::load_latest — session.execute() and session.scalar() both bound to the session lifecycle. No resource leak on exception path (Exception caught in rerank_with_preference, falls back to DNA ranking).
- **ok** preference/model.py::from_bytes — joblib.load(io.BytesIO(data)) wraps bytes in BytesIO (memory buffer, not file). No file handle. Lock released in finally block even on exception.

### Clip-Quality Correctness
- **ok** preference/train.py::build_and_save — label_count < 2 or fewer than 2 classes → returns None. Caller (rerank_with_preference) receives None and falls back to DNA. Below-threshold behavior is explicit and honest.
- **ok** preference/train.py::load_latest — feature schema drift detection: stored_features != FEATURE_NAMES → logs warning, returns None (fallback to DNA). No silent misprediction on schema mismatch.
- **ok** preference/train.py::build_and_save — training samples limited to PREFERENCE_MAX_TRAINING_LABELS (5000) to bound memory and training time. Power creators with years of feedback don't cause OOM.
- **ok** preference/model.py::PreferenceScorer.predict_score — raises ValueError if feature count drift detected (x.shape[1] != n_features_in_). Caller catches this and falls back to DNA. Honest behavior.
- **ok** preference/features.py::clip_features — 8-element fixed-length feature vector. Order stable. dna_match defaults to 0.0 if None. No undefined indices.
- **ok** preference/decay.py — sample_weight multiplies recency_weight by outcome_multiplier (3.0) only if performed_well is explicitly True. Neutral clips and skips (performed_well=None) receive recency weight only. Correct weighting.

### Code Cleanliness & Typing
- **ok** All functions typed: model.py has type hints on all signatures. features.py has full type hints. decay.py has type hints. train.py has full type hints including AsyncSession, uuid.UUID, Optional returns.
- **ok** No TODO/FIXME/XXX comments, no commented-out code blocks, no print() statements.
- **ok** No duplicate logic. clip_features called once per location (train.py and ranking.py). recency_weight called only in decay.py. No repeated SQL patterns.
- **ok** Imports well-organized. Lazy imports in model.py::fit (sklearn, lightgbm) for cold-start optimization. No unused imports.

### Config & Paths
- **ok** No hardcoded file paths. All config values (PERSONALIZATION_THRESHOLD_LABELS, PREFERENCE_WEIGHT_CAP, PREFERENCE_SCORER_CACHE_SIZE, PREFERENCE_MAX_TRAINING_LABELS) sourced from settings object.

### Error Handling Context
- **ok** Not a router (no status codes, no try/except/JsonResponse). Module is library code. Caller (clip_engine/ranking.py::rerank_with_preference) handles exceptions with try/except, logs, and falls back to DNA ranking. Proper error propagation.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — AsyncSession lifecycle managed by caller. joblib BytesIO + lock-protected. No resource leak on error. |
| 2 Concurrency & scale | ok — CPU-bound fit() and joblib.load offloaded to asyncio.to_thread. No sync call in async def. Shared cache protected by lock. Queries indexed on creator_id. No unbounded fetchall. LIMIT 5000 on training data. |
| 3 Security & compliance | ok — All queries filter by creator_id. Parameterized SQL only. _RestrictedUnpickler allowlist closes RCE. No PII in logs. |
| 4 Clip-quality correctness | ok — Exponential recency decay (30d half-life). Below-threshold returns None, falls back to DNA. Feature schema drift detected. predict_score validates feature count. Outcome multiplier conditional on performed_well==True. |
| 5 Anthropic SDK | N/A — module does not call LLM. |
| 6 Code cleanliness & typing | ok — Full type coverage. No TODOs, print(), or duplicate logic. Lazy imports for sklearn/lightgbm. |
| 7 Error handling | N/A — library code. Caller handles. |
| 8 Config & paths | ok — No hardcoded paths. All settings via config.settings. |

## Module verdict
**clean** — The preference module is production-ready. Concurrency is correct (asyncio.to_thread + locks). Security is tight (RCE allowlist, per-creator isolation, parameterized SQL). Correctness is honest (feature drift detected, below-threshold fallback, outcome-aware weighting). Testing is thorough (half-life verified, cache eviction, schema drift, RCE surface). No blockers.
