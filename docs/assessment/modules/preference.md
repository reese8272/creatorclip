# preference — assessed 2026-05-31

## Findings

- [SEV1] preference/train.py:83 — `fit(X, y, w)` runs LogisticRegression or LightGBM
  training synchronously inside `async def build_and_save`, with no offload. LightGBM
  training on a creator with hundreds-thousands of labels will block the event loop for
  seconds, stalling every other request on the same worker | fix: wrap the fit call in
  `await asyncio.to_thread(fit, X, y, w)`, or move `build_and_save` out of the async
  request path entirely and into a Celery task (it already commits to DB, so it is
  naturally a background job). The `await session.commit()` afterward is fine; only the
  CPU-bound `fit` needs to leave the loop.

- [SEV1] preference/model.py:126-132 — `from_bytes` mutates the process-global
  `joblib.numpy_pickle.NumpyUnpickler` under `_UNPICKLER_LOCK`, then calls
  `joblib.load` which is blocking I/O + CPU. `load_latest` (`train.py:160`) awaits
  this on the event loop on every cache miss, and the global lock serializes ALL
  preference-model deserializations across the entire process — including across
  creators. Two creators hitting rerank at the same time on a cold cache will queue
  behind each other on a blocking call inside an async coroutine | fix: (a) run the
  load via `asyncio.to_thread(PreferenceScorer.from_bytes, blob)` so the loop is not
  blocked, and (b) replace the global-class-swap with a per-load `pickle.Unpickler`
  subclass operating on `io.BytesIO(data)` directly — joblib provides
  `joblib.numpy_pickle.NumpyUnpickler(filename, file_handle)` which can be subclassed
  and used without monkey-patching the module. That removes the process-wide lock.

- [SEV2] preference/train.py:37-46 — the feedback-fetch query has no `LIMIT` and
  builds the full Python list `rows = result.all()` before training. A power creator
  with years of feedback (10k+ labels) accumulates the full set in memory on every
  retrain; LightGBM also receives the full ndarray copy | fix: add an explicit cap
  (e.g. `LIMIT settings.PREFERENCE_MAX_TRAINING_LABELS`, default 5000, newest-first
  via `ORDER BY ClipFeedback.created_at DESC`) — recency-decayed weighting already
  makes old labels worth ~0, so truncating the long tail is free correctness-wise.
  Add `PREFERENCE_MAX_TRAINING_LABELS` to `config.py` + `.env.example`.

- [SEV2] preference/train.py:43 — `ClipFeedback.action.in_(list(_POSITIVE_ACTIONS)
  + list(_NEGATIVE_ACTIONS))` rebuilds the list literal on every call and discards
  the `TRAINABLE_ACTIONS` frozenset constant defined two lines above at line 28
  (DRY) | fix: use `ClipFeedback.action.in_(TRAINABLE_ACTIONS)` directly — SQLAlchemy
  accepts an iterable.

- [cleanup] preference/model.py:88 — `PreferenceScorer.__init__(model: Any, ...)`
  loses the type information that callers of `fit()` would benefit from. Acceptable
  given the model union (LogisticRegression | LGBMClassifier) is not nominally typed
  by either library | fix: leave as-is, or introduce a `Protocol` with
  `predict_proba(X) -> np.ndarray` and `n_features_in_: int` to document the contract.
  Cleanup-only.

- [cleanup] preference/train.py:46 — variable `result` is reused at line 93 for a
  different query (shadowed). Harmless but confusing on a re-read | fix: rename the
  second one `existing_result` or just inline into the `select(...).first()` chain.

- [cleanup] preference/decay.py:11 — `_LAMBDA = math.log(2) / 30` hardcodes the
  30-day half-life. The docstring documents it, but the value is also relevant to
  the honest-threshold story in CLAUDE.md (DNA uses 90 days; this uses 30) | fix:
  expose as `PREFERENCE_HALFLIFE_DAYS` setting if tuning is anticipated, otherwise
  leave the constant with the docstring as documentation. Low priority.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — sessions caller-owned, no leaks; LRU cache bounded by `PREFERENCE_SCORER_CACHE_SIZE` |
| 2 Concurrency & scale | 2 SEV1 (blocking CPU in async path), 1 SEV2 (unbounded training fetch) |
| 3 Security & compliance | ok — all queries `WHERE creator_id`; cache key includes creator_id; pickle allowlist + version-bumped cache key prevents stale-model serving; no PII in logs (creator_id UUID only); restricted unpickler closes RCE on tampered weights_blob |
| 4 Clip-quality | ok — `preference_weight()` enforces honest below-threshold zero, ramps linearly to `PREFERENCE_WEIGHT_CAP` at 2× threshold (matches CLAUDE.md "honest threshold" rule); 30-day exponential recency decay implemented in `decay.py`; feature-schema-drift fallback to DNA is the honest behavior; no virality promise |
| 5 Anthropic SDK | n/a — no LLM calls in this module |
| 6 Cleanliness & typing | 2 cleanups (DRY in train.py:43, shadowed `result`); functions all typed; no print/TODO/commented blocks |
| 7 Error handling / API | n/a — no routers in this module |
| 8 Config & paths | ok — `PERSONALIZATION_THRESHOLD_LABELS`, `PREFERENCE_WEIGHT_CAP`, `PREFERENCE_SCORER_CACHE_SIZE` all in `config.py` + `.env.example`; would need to add `PREFERENCE_MAX_TRAINING_LABELS` per SEV2 above |

## Module verdict

**NEEDS-WORK** — Security model and clip-quality honesty rules are solidly implemented
(restricted unpickler, version-keyed cache, honest threshold, recency decay), but two
SEV1 concurrency defects put CPU-bound training (`fit`) and lock-contended joblib
deserialization (`from_bytes`) directly on the event loop, which will stall workers
under concurrent rerank traffic — fix before scale launch.
