# preference — assessed 2026-06-09

## Findings

No code changes in `preference/` since the 2026-06-08 assessment (latest commit
touching the slice is c82f624, Issue 102). All prior "clean" claims re-verified
against current code. One new SEV2 found that earlier sweeps missed; the four
prior cleanups remain open.

- [SEV2] preference/model.py:25–26 + requirements.txt — `joblib` is directly
  imported and its **private** internals (`joblib.numpy_pickle.NumpyUnpickler`,
  swapped at model.py:127–132) are the enforcement point for the RCE allowlist,
  but joblib is NOT pinned in requirements.txt (only pulled transitively via
  `scikit-learn==1.5.2`; installed today as 1.5.3). A future joblib release can
  rename/move `NumpyUnpickler` or change the `_unpickle` path and break the
  security control. Mitigation already in place: tests/test_preference.py:198–228
  assert a disallowed-class blob raises `UnpicklingError`, so drift fails loudly
  in CI rather than silently in prod — bounded blast radius, hence SEV2 not SEV1.
  Violates the CLAUDE.md production standard "requirements.txt pinned with ==".
  | fix: add `joblib==1.5.3` to requirements.txt with a comment that
  preference/model.py monkeypatches its internals (DECISIONS 2026-05-31).
- [cleanup] preference/model.py:88 — `model: Any` on `PreferenceScorer.__init__`
  | fix: define a `_ProbaModel` Protocol with `predict_proba` and
  `n_features_in_`.
- [cleanup] preference/train.py:79–86 — cold-start log collapses two distinct
  failure modes (n < 2 vs single-class feedback) | fix: split the branch so the
  log says which one occurred.
- [cleanup] preference/features.py:20–29 — no guard that `clip_features()`
  output length matches `FEATURE_NAMES` (the docstring says order/length must
  stay stable, but nothing enforces it) | fix: module-level
  `assert len(clip_features()) == len(FEATURE_NAMES)`.
- [cleanup] preference/decay.py:11 — 30-day half-life hardcoded in `_LAMBDA`,
  not exposed via config | fix: optional `RECENCY_HALF_LIFE_DAYS` setting
  (default 30) in config.py + `.env.example`.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — AsyncSession owned/injected by caller (correct); `commit()` on success path ends the advisory-xact-lock transaction; scorer LRU bounded by `PREFERENCE_SCORER_CACHE_SIZE=128` (_scorer_cache.py:40–41); no files/subprocesses |
| 2 Concurrency & scale | ok — CPU-bound `fit` offloaded via `asyncio.to_thread` (train.py:97); joblib deserialize offloaded (train.py:181) so `_UNPICKLER_LOCK` serializes threads, not coroutines; training query capped newest-first at `PREFERENCE_MAX_TRAINING_LABELS=5000` (train.py:52–53); `pg_advisory_xact_lock(hashtext(creator_id))` (train.py:102–104) prevents the UNIQUE(creator_id, version) race; cache-hit path skips the blob fetch entirely (train.py:160–163) |
| 3 Security & compliance | 1 SEV2 — `_RestrictedUnpickler.find_class` (model.py:78–82) rejects unknown classes before construction and the allowlist matches the pinned stack (`numpy._core.multiarray` is correct for numpy==2.1.3), but the dep it monkeypatches is unpinned (see finding). No PII/token in any log line; every query creator-scoped (`WHERE creator_id` at train.py:49, 141, 167); SQL parameterized including the advisory-lock key; no virality language |
| 4 Clip-quality | ok — exponential recency decay `e^(-ln(2)/30·age)` (decay.py:11–16) with 3× outcome multiplier only when `performed_well is True`; `preference_weight` returns 0 below `PERSONALIZATION_THRESHOLD_LABELS` and ramps linearly to `PREFERENCE_WEIGHT_CAP` at 2× threshold (model.py:149–154) — honest fallback to DNA; feature-schema drift returns None with a warning instead of scoring (train.py:152–158); `predict_score` raises on feature-count drift rather than emitting a fake 0.5 (model.py:100–104) |
| 5 Anthropic SDK | n/a (no LLM calls) |
| 6 Cleanliness & typing | 4 cleanup — `Any` on `_model`; collapsed cold-start log; no FEATURE_NAMES length guard; `_LAMBDA` not configurable. No TODO, no commented-out code, no `print()`; all public signatures typed |
| 7 Error handling / API | n/a (not a router; errors surface as `ValueError`/`UnpicklingError`, caught with DNA fallback by callers) |
| 8 Config & paths | ok — all four settings in config.py with defaults and documented in `.env.example` (lines 10, 75–77); no filesystem paths |

## Module verdict

NEEDS-WORK — one one-line SEV2 (pin `joblib==1.5.3`; its private internals are
the RCE-allowlist enforcement point and are currently version-floating), plus
four pre-existing non-blocking cleanups. All Issue 60/71/78a/102 mitigations
re-verified in place and correct; per-creator isolation holds on every query.

## Issue 75 Reconciliation (2026-06-23)

| Finding | Disposition |
|---|---|
| [SEV2] joblib not pinned (preference/model.py:25-26) | → tracked in Issue 109 (deferred design cleanups — requirements.txt hygiene) |
| [cleanup] model: Any on PreferenceScorer (preference/model.py:88) | → tracked in Issue 109 |
| [cleanup] cold-start log collapse (preference/train.py:79-86) | → tracked in Issue 109 |
| [cleanup] no FEATURE_NAMES length assert (preference/features.py:20-29) | → tracked in Issue 109 |
| [cleanup] _LAMBDA 30-day half-life hardcoded (preference/decay.py:11) | → tracked in Issue 200 (recency-decay half-life calibration + parameterize) |
