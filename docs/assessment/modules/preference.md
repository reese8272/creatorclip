# preference — assessed 2026-06-07

## Findings

- [SEV2] preference/train.py:97 — `await asyncio.to_thread(fit, X, y, w)` runs the
  CPU-bound LightGBM/sklearn fit BEFORE the per-creator `pg_advisory_xact_lock`
  is acquired (line 102). Concurrent retrains for the same creator (e.g. two
  Celery workers racing on backlogged feedback events) will both burn through
  a fit, then serialize on the lock and one will discard its result. The
  advisory lock IS held from before the `SELECT MAX(version)` through commit,
  so version assignment is correct — this is a wasted-compute / cost issue,
  not a correctness one. | fix: gate the retrain on an idempotency key
  upstream (Celery task name = `preference-retrain:{creator_id}` with a
  short-lived Redis lock or `acks_late=True` + unique-task), OR acquire the
  advisory lock at the top of `build_and_save` before the fetch+fit. A
  `pg_advisory_xact_lock` held across `asyncio.to_thread(fit, ...)` is fine —
  the lock is on the AsyncSession, not the worker thread.
- [SEV2] preference/train.py:107 — variable `result` is rebound; the first
  assignment (line 44) is the feedback fetch, the second (line 107) is the
  version lookup. The shadow is harmless today but invites a future edit to
  re-iterate the wrong `result`. | fix: rename the second to
  `version_result` (or call `.scalars().first()` inline without binding).
- [cleanup] preference/model.py:23 — `Any` used for `PreferenceScorer.__init__`'s
  `model` parameter and for `find_class`'s return type. The allowlist is the
  load-bearing typing barrier, but the wrapper could expose a narrower
  Protocol (`predict_proba(X) -> np.ndarray`) to make drift between
  LogisticRegression and LGBMClassifier explicit. | fix: define a
  `_ProbaModel` Protocol with `predict_proba` and `n_features_in_` and use it
  in `__init__`; keep `find_class -> Any` (it matches pickle's contract).
- [cleanup] preference/train.py:80 — `len(X_list) < 2 or len(set(y_list)) < 2`
  is logged at `.info`. This is a normal cold-start outcome for a new creator,
  not a warning, but the message format collapses two distinct failure modes
  (too few rows vs. only one class) into one line. | fix: split the branch so
  the log explicitly says "single-class feedback (all upvotes)" vs.
  "insufficient samples"; helps ops triage why a creator's model isn't
  building.
- [cleanup] preference/features.py:6 — `clip_features` signature has eight
  defaulted floats/bools but no validation that callers passed the right
  semantic features. A silent reorder at the call site would train on
  scrambled features without any error. | fix: keep the keyword-only call
  pattern used in train.py (it already passes kwargs) but assert
  `len(returned) == len(FEATURE_NAMES)` once at module import as a guard
  against accidental drift between the function body and `FEATURE_NAMES`.
- [cleanup] preference/decay.py:11 — `_LAMBDA` is module-private, but the
  30-day half-life is a load-bearing product number cited in
  `docs/CLIPPING_PRINCIPLES.md` and `CLAUDE.md`. A future tweak via env
  override would need a code change. | fix: optionally expose
  `RECENCY_HALF_LIFE_DAYS` via `config.settings` (default 30) so tuning
  doesn't require a deploy; flag as cleanup, not SEV — the current value is
  the documented standard.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — `session.commit()` runs on the success path; `from_bytes` deserialization errors are caught in `train.py:182` and the function returns `None` (the AsyncSession is owned by the caller, not closed here, which is correct for shared-session injection); the scorer LRU is bounded by `PREFERENCE_SCORER_CACHE_SIZE` |
| 2 Concurrency & scale | 1 SEV2 — `asyncio.to_thread(fit, ...)` and `asyncio.to_thread(PreferenceScorer.from_bytes, ...)` keep the API event loop free (Issue 102 verified in place at train.py:97 and train.py:181); `PREFERENCE_MAX_TRAINING_LABELS=5000` cap on the newest-first SQL `LIMIT` (train.py:53) verified; `_UNPICKLER_LOCK` (threading.Lock at model.py:37) correctly wraps the entire `NumpyUnpickler` module-swap + `joblib.load` + restore (model.py:126–132), so concurrent `from_bytes` callers serialize on the lock and never observe a half-swapped global — the SEV2 from the prior pass on this was incorrect and is removed; remaining SEV2 is the wasted-fit window before the advisory lock |
| 3 Security & compliance | ok — `_RestrictedUnpickler.find_class` (model.py:78–82) rejects any (module, name) outside `_ALLOWED_CLASSES` before construction, closing the joblib-pickle RCE surface; no PII or token in any log line (only `creator_id` UUID, n, version); every query is creator-scoped via `WHERE creator_id` (train.py:49, 109, 141, 167); SQL is parameterized including the advisory-lock key (train.py:103) |
| 4 Clip-quality | ok — recency decay is exponential with 30d half-life (decay.py:11, `λ = ln(2)/30`); `preference_weight` (model.py:139–154) returns 0 below `PERSONALIZATION_THRESHOLD_LABELS` and ramps linearly to `PREFERENCE_WEIGHT_CAP` at 2× threshold — below-threshold fallback to DNA + signals is structurally honest; feature-schema drift detection (train.py:152–158) returns `None` and logs a warning rather than scoring with a stale feature set; predict_score raises rather than returning a misleading 0.5 on feature-count mismatch (model.py:101–104) |
| 5 Anthropic SDK | n/a (no LLM calls in this module) |
| 6 Cleanliness & typing | 4 cleanup — `Any` on `_model` and `find_class`; collapsed cold-start log; missing FEATURE_NAMES↔clip_features length guard; `_LAMBDA` not configurable. No TODO, no commented blocks, no `print()`, every public signature typed. `fit` and `build_and_save` are both ~30 lines doing one thing each |
| 7 Error handling / API | n/a (not a router; internal errors raised as `ValueError` / `pickle.UnpicklingError` and caught with safe fallback by callers) |
| 8 Config & paths | ok — all four settings (`PERSONALIZATION_THRESHOLD_LABELS`, `PREFERENCE_WEIGHT_CAP`, `PREFERENCE_SCORER_CACHE_SIZE`, `PREFERENCE_MAX_TRAINING_LABELS`) registered in `config.py:136–150` and documented in `.env.example:10,75–77`; no filesystem paths used |

## Module verdict

clean — the Issue 102 mitigations (`asyncio.to_thread` around `fit` and
`from_bytes`, newest-first `LIMIT PREFERENCE_MAX_TRAINING_LABELS` on the
training pull) are in place and correct. The unpickler allowlist is properly
serialized by `_UNPICKLER_LOCK`, so the previous assessment's "swap is not
atomic" concern is a false positive. The single remaining SEV2 is a
wasted-compute window when two retrains race for the same creator — the
correctness path (version assignment under advisory lock) is intact, but a
task-level idempotency key would eliminate the duplicate fit. Everything
else is cleanup.
