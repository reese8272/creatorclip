# preference — assessed 2026-05-29

Slice: `preference/__init__.py`, `preference/decay.py`, `preference/features.py`,
`preference/model.py`, `preference/train.py`. Consumer `clip_engine/ranking.py` and
`models.py` read for trace only (owned by other agents — not scored here).

## Findings

- [SEV1] preference/train.py:28 — `build_and_save()` has **no caller anywhere in the
  codebase** (grep across `routers/`, `worker/`, `clip_engine/`, beat schedule turns up
  nothing; `worker/tasks.py:124` only mentions it in a docstring). The preference model is
  therefore **never trained or retrained in production** — `load_latest()` always returns
  `None` and `rerank_with_preference` silently no-ops forever. The North-Star
  "learns your style / adapts as you evolve" loop is not wired. | fix: add an idempotent
  Celery task `worker.tasks.retrain_preference(creator_id)` enqueued after each feedback
  write (debounced) and/or on a Beat cadence, calling `build_and_save`; add an integration
  test that posts feedback, runs the task, and asserts `load_latest` returns a scorer whose
  ranking differs from DNA-only. Until wired, treat personalization as unshipped.

- [SEV1] preference/model.py:113-118 — `from_bytes` mutates the module global
  `_jnp.NumpyUnpickler` for the duration of `joblib.load`, then restores it in `finally`.
  This is **not thread- or task-safe**: `load_latest` runs on the async API loop and under
  Celery; two concurrent deserialisations interleave such that thread B can restore the
  original (unrestricted) `NumpyUnpickler` while thread A is still inside `joblib.load`,
  defeating the RCE allowlist exactly when a tampered blob is being read. The security
  guarantee the module advertises (model.py:8-15) does not hold under concurrency. | fix:
  do not monkeypatch a global. Drive a private unpickler directly — open the joblib stream
  with an explicit unpickler instance (subclass `_jnp.NumpyUnpickler`, call its `.load()`
  on the `BytesIO`), or guard the swap with a module-level `threading.Lock`. Add a test
  that runs `from_bytes` concurrently from N threads, one feeding a malicious blob, and
  asserts the malicious one always raises `UnpicklingError`. (needs-runtime-confirmation
  for the exact interleave, but the shared-global pattern is unsafe by construction.)

- [SEV1] clip_engine/ranking.py:38 + preference/train.py:107 — on the hot rerank path,
  `load_latest` runs `select(PreferenceModel).where(creator_id==...).order_by(version
  desc)` against an **unindexed `creator_id`** (see below) and then calls
  `PreferenceScorer.from_bytes` which runs `joblib.load` (CPU-bound unpickle + numpy/LGBM
  reconstruction) **synchronously on the async event loop**. Under concurrency this blocks
  the loop per request. | fix (preference side): make `from_bytes`/`predict_score` safe to
  offload — have the caller `await asyncio.to_thread(PreferenceScorer.from_bytes, blob)`,
  and cache the deserialised scorer per (creator_id, version) in a module-level bounded
  LRU so the unpickle happens once, not per rerank. Add the index (next finding).

- [SEV1] models.py:418 / preference/train.py:84-89 — `build_and_save` does
  `select(max version)` then writes `version+1` into a row guarded by
  `UniqueConstraint(creator_id, version)`. Two concurrent retrains for one creator both
  read version N and both insert N+1 → `IntegrityError` on commit, uncaught, task fails. |
  fix: make the task idempotent/serialised — either `SELECT ... FOR UPDATE` on the
  creator's latest model row, or retry-on-IntegrityError with a fresh max-version read, or
  switch to a monotonic version via `INSERT ... ON CONFLICT` / a per-creator advisory lock
  (`pg_advisory_xact_lock(hashtext(creator_id))`). Add a test firing two retrains
  concurrently asserting exactly one new version row.

- [SEV2] models.py:378 — `ClipFeedback.creator_id` is a foreign key with **no index**
  (Postgres does not auto-index FKs; `grep index models.py` finds only the `activity_index`
  column name). The training query (train.py:34-42) filters and joins on this column and
  will table-scan as feedback grows. `PreferenceModel.creator_id` (models.py:418) is
  likewise unindexed though `load_latest` queries it on the rerank path. | fix: add
  `index=True` to both `creator_id` columns (or composite `(creator_id, version)` for
  PreferenceModel) via an Alembic migration using `CREATE INDEX CONCURRENTLY` (online-safe
  per scale-checklist H).

- [SEV2] clip_engine/ranking.py:56 + preference/model.py:81-83 — the rerank blend is a
  hardcoded `0.5*dna + 0.5*pref` regardless of how many labels trained the model
  (`scorer.label_count` is carried but never consulted). A LogisticRegression cold-start
  model fit on the 2-label minimum (train.py:67) is given the **same weight** as a mature
  LightGBM model. This contradicts the threshold-honesty rule (CLAUDE.md Clip-Engine Rules:
  "below it, ranking falls back to DNA + signals") — there is no honest below-threshold
  fallback; any trained model fully participates at 50%. | fix: gate or ramp the preference
  weight on `label_count` vs `PERSONALIZATION_THRESHOLD_LABELS` (e.g. weight 0 below
  threshold, linear ramp toward a cap above it), and surface the personalization state to
  the creator honestly. Primary fix belongs in ranking.py, but the contract is the
  preference module's to define — expose a `preference_weight(label_count)` helper here.

- [SEV2] preference/model.py:88-93 — `predict_score` wraps `predict_proba` in a bare
  `except Exception` and returns `0.5` on any failure (shape mismatch, model corruption,
  feature-count drift). At 0.5 a broken model still moves rankings (blended at 50%) while
  looking healthy, and feature-schema drift between `feature_schema_jsonb` and the live
  `FEATURE_NAMES` is silently swallowed. | fix: validate the input length against the
  model's expected `n_features_in_` and raise/skip (let the caller fall back to DNA) rather
  than returning a misleading neutral score; log at `error` with creator/version context.
  Add a feature-schema version check in `load_latest` comparing
  `row.feature_schema_jsonb["features"]` to `FEATURE_NAMES` before trusting the blob.

- [cleanup] preference/decay.py:23 / train.py:11 — `datetime.now(UTC)` is called inline in
  `feedback_age_days`; fine for prod but makes recency-decay tests time-dependent and not
  injectable. | fix: accept an optional `now: datetime | None = None` param defaulting to
  `datetime.now(UTC)` so decay math is deterministically testable.

- [cleanup] preference/model.py:141,147 — `LogisticRegression`/`lightgbm` imported inside
  `fit()` (lazy import). Acceptable to defer the heavy LightGBM import, but the sklearn
  import adds per-call overhead on the cold-start path. | fix: leave LightGBM lazy; hoist
  the sklearn import to module top, or document the lazy-import intent in a one-line WHY
  comment.

- [cleanup] preference/train.py:91-97 — `PreferenceModel(...)` sets `updated_at` but the
  table has no `created_at`; "version N created at" is unrecoverable for audit. | fix: add a
  `created_at` column (server default `now()`) in the model + migration, or rename the field
  to reflect that it is creation time.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | 1 finding — version-race IntegrityError on commit (SEV1); sessions are caller-managed (ranking/worker own them) |
| 2 Concurrency & scale | 3 findings — global-monkeypatch unpickler not thread-safe (SEV1), sync joblib.load on event loop (SEV1), unindexed creator_id (SEV2) |
| 3 Security & compliance | 1 finding — allowlist defeated under concurrent from_bytes (SEV1); per-creator isolation on training query is correct (creator_id derived, WHERE present); no PII/token logged |
| 4 Clip-quality | 2 findings — personalization never trained (SEV1, no caller); no honest below-threshold fallback, fixed 50/50 blend (SEV2). Recency decay math itself is correct (half-life 30d, λ=ln2/30, 3× outcome multiplier, well-tested) |
| 5 Anthropic SDK | n/a (no LLM calls) |
| 6 Cleanliness & typing | 3 cleanups — injectable clock, lazy imports, missing created_at; all functions typed |
| 7 Error handling / API | 1 finding — predict_score swallows all errors into 0.5 (SEV2); module has no router surface |
| 8 Config & paths | ok — PERSONALIZATION_THRESHOLD_LABELS in config.py with default; no paths; no missing .env keys |

## Module verdict
NEEDS-WORK — the recency-decay math and RCE allowlist intent are sound and well-tested, but
the personalization loop is never wired (`build_and_save` has no caller), the version
increment races, the unpickler allowlist is defeated under concurrent loads, and there is no
honest below-threshold fallback — so the module does not yet deliver or safely ship the
channel-learning behavior it claims.
