# preference — assessed 2026-06-24

Slice: `preference/` (7 files: `__init__.py` [empty], `_scorer_cache.py`,
`decay.py`, `features.py`, `model.py`, `style_learn.py`, `train.py`). No BLOCKER,
no SEV1 — per-creator isolation, log hygiene, Celery idempotency, the RCE
allowlist, and the honest-threshold + recency-decay clip-quality contract all
re-verified clean against current code. Open items are bounded defects + cleanups.

## Findings
- [SEV2] preference/model.py:26 + requirements.txt:71-73 — `joblib.numpy_pickle`
  is imported and its **private** `NumpyUnpickler` is monkey-swapped at
  model.py:127-132 as the enforcement point of the RCE allowlist, yet `joblib`
  is NOT pinned in requirements.txt (pulled transitively via
  `scikit-learn==1.5.2`; installed today as **1.5.3**, confirmed at runtime). A
  future joblib release can rename/move `NumpyUnpickler` or change the
  `_unpickle` path and silently break the security control. Bounded because
  `tests/test_preference.py` asserts a disallowed-class blob raises
  `UnpicklingError` (drift fails in CI), and it violates the CLAUDE.md standard
  "requirements.txt pinned with ==". | fix: add `joblib==1.5.3` to
  requirements.txt with a comment that preference/model.py swaps its internals
  (DECISIONS 2026-05-31). Still open despite being filed under Issue 109.
- [SEV2] preference/style_learn.py:55-58 — `dominant_style` returns the **first**
  value to cross the threshold by **dict insertion order** (first-seen in
  `history`), not the value with the **highest** count. The docstring promises
  "the field value that appears >= threshold times" and the module advertises
  "mode detection", but if two values of one field both clear the threshold the
  less-frequent first-seen value can win → a wrong smart-default suggestion shown
  to the creator. | fix: pick the max among qualifying values —
  `qualifying = {v: c for v, c in counts.items() if c >= threshold}; return max(qualifying, key=qualifying.get) if qualifying else None`.
  Add a test: value A (count 5, first-seen) vs value B (count 9), threshold 5 →
  must return B.
- [SEV2] preference/style_learn.py:76-88 — `style_suggestion` re-implements the
  exact mode-counting loop already in `dominant_style` (lines 48-58) verbatim
  (DRY) and inherits the same first-over-threshold-not-max bug, so the fix has to
  be made twice. | fix: have `style_suggestion` call
  `dominant_style(history, field, threshold)` per `_KIT_FIELDS`, return the first
  field with a non-None dominant (recompute its count once for the message). One
  source of truth for counting + the fix above lands in one place.
- [SEV2] preference/train.py:61-70 — the dict→`clip_features(**)` adapter (pull
  `signals_jsonb["features"]` keys + `clip.dna_match` into the feature kwargs) is
  duplicated by `clip_engine/ranking.py:53-62` (out of slice). Two copies of the
  feature-assembly contract means a feature added to `features.py` can be wired at
  train time but missed at inference (or vice-versa) → silent train/serve skew in
  the exact module whose job is ranking fidelity. | fix: extract one
  `features_from_clip(clip) -> list[float]` (in `preference/features.py`) and call
  it from both `build_and_save` and `rerank_with_preference`.
- [cleanup] preference/model.py:88 — `model: Any` on `PreferenceScorer.__init__`
  (and `self._model: Any`) | fix: define a `_ProbaModel` Protocol exposing
  `predict_proba(self, X) -> np.ndarray` and `n_features_in_: int`, and type the
  param to it; tightens the only `Any` in the hot-path class.
- [cleanup] preference/train.py:79-86 — the cold-start log line collapses two
  distinct outcomes (n < 2 samples vs single-class feedback) into one message,
  hindering "why is no model training?" diagnosis | fix: split the branch so the
  log states which condition fired.
- [cleanup] preference/features.py:20-29 — nothing enforces that `clip_features()`
  output length equals `len(FEATURE_NAMES)`, though the docstring says order/length
  must stay stable (and a mismatch is what the model.py:100-104 drift guard exists
  to catch downstream) | fix: module-level
  `assert len(clip_features()) == len(FEATURE_NAMES)`.
- [cleanup] preference/decay.py:11 — the 30-day half-life is hardcoded in `_LAMBDA`
  and the 3× outcome multiplier is a hardcoded param default (decay.py:29), while
  every sibling tunable lives in `config.py` | fix: optional
  `RECENCY_HALF_LIFE_DAYS` (default 30) and `PREFERENCE_OUTCOME_MULTIPLIER`
  (default 3.0) settings + `.env.example` entries. (Half-life already tracked under
  Issue 200.)

## Notes on verified-clean load-bearing items
- **Per-creator isolation (BLOCKER class) — CLEAN.** All four creator-scoped
  queries carry `WHERE creator_id`: train.py:48-49 (ClipFeedback feed),
  train.py:108-109 (PreferenceModel max-version), train.py:140-141 (load version),
  train.py:165-169 (load blob, `creator_id AND version`). The advisory-lock and
  debounce queries in the worker caller are also creator-scoped. The `style_preset`
  read lives in the router (`routers/creators.py:273`, scoped to `creator.id`).
- **Token / PII in logs — CLEAN.** No `decrypt()`/token surface in this module.
  Every `logger.*` emits only `creator_id` (UUID), counts, and version ints — the
  id-only dimension COMPLIANCE.md permits. No email, channel_id, or blob bytes.
- **Concurrency — CLEAN within slice.** Only CPU-bound call (`fit`) offloaded via
  `asyncio.to_thread` (train.py:97); joblib deserialize offloaded too
  (train.py:181) so `_UNPICKLER_LOCK` serializes threads, not coroutines.
  `_scorer_cache` guards its `OrderedDict` with a `threading.Lock` on every
  read/write/evict; LRU bounded by `PREFERENCE_SCORER_CACHE_SIZE=128`. No
  `requests.`/`time.sleep`/`subprocess`/sync DB driver in any `async def`.
- **Celery idempotency — CLEAN.** `build_and_save` takes
  `pg_advisory_xact_lock(hashtext(creator_id))` (train.py:102-104) before the
  max-version read → no `UNIQUE(creator_id, version)` collision; caller
  `worker/tasks.py:_retrain_preference_async` additionally guards with non-blocking
  `pg_try_advisory_lock` released in `finally`, debounces on new-feedback count,
  and catches `IntegrityError` with rollback. The two locks use distinct keys
  (`str(cid)` vs `f"retrain:{cid}"`) — no deadlock.
- **Cache staleness — CLEAN.** Keyed by `(creator_id, version)`; train.py assigns a
  fresh monotonic version per retrain, so a new model is a new key and the stale
  entry falls out by LRU. No manual invalidation needed.
- **Clip-quality (cat 4) — honest fallback + recency decay PRESENT.**
  `preference_weight` returns exactly `0.0` below `PERSONALIZATION_THRESHOLD_LABELS`
  and ramps to `PREFERENCE_WEIGHT_CAP` (model.py:149-154); `rerank_with_preference`
  short-circuits to unchanged DNA ranking at weight 0 (ranking.py:48-49).
  Exponential recency decay real (decay.py:11,16, 30-day half-life); 3× outcome
  multiplier only when `performed_well is True`. Blends against THIS creator's own
  model — no generic virality score; no virality promise in any slice string.
  Feature-schema drift returns None+warning (train.py:152-158); `predict_score`
  raises on feature-count drift instead of a fake 0.5 (model.py:100-104).
- **RCE surface on `weights_blob` — hardened.** `_RestrictedUnpickler.find_class`
  (model.py:78-82) rejects any class outside the explicit allowlist before
  construction; the global swap is serialized by `_UNPICKLER_LOCK`; the
  `numpy._core.multiarray.scalar` path matches the pinned numpy==2.1.3 (verified at
  runtime). The single residual risk is the unpinned joblib dep (finding above).

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — AsyncSession owned/injected by callers via `async with`; `commit()` ends the advisory-xact-lock transaction; scorer LRU bounded by `PREFERENCE_SCORER_CACHE_SIZE=128`; no files/subprocesses constructed here |
| 2 Concurrency & scale | ok — `fit` + joblib deserialize offloaded via `asyncio.to_thread`; training query capped newest-first at `PREFERENCE_MAX_TRAINING_LABELS=5000`; advisory locks prevent the version race; cache-hit skips the blob fetch. (Global-unpickler-lock is a documented per-process deserialize chokepoint but bounded — needs-runtime-confirmation it bites at target concurrency) |
| 3 Security & compliance | 1 SEV2 — `find_class` allowlist correct for the pinned stack, but the monkeypatched dep (`joblib`) is unpinned. Per-creator isolation on all 4 queries verified; no PII/token in logs; params-bound `text()` advisory-lock keys; no virality language |
| 4 Clip-quality | ok — honest below-threshold fallback (weight 0) + 30-day exponential recency decay + 3×-on-`performed_well` verified; reranks creator-specific model; drift guards fall back to DNA rather than fabricate scores |
| 5 Anthropic SDK | n/a — module makes no LLM calls |
| 6 Cleanliness & typing | 5 findings (style_learn mode-bug SEV2 + DRY SEV2; train↔ranking feature-adapter DRY SEV2; `model: Any` cleanup; collapsed cold-start log cleanup; missing FEATURE_NAMES length assert cleanup) — no TODO / commented code / `print()`; signatures otherwise typed |
| 7 Error handling / API | n/a — no router in slice; errors surface as `ValueError`/`UnpicklingError`, caught with DNA fallback by callers |
| 8 Config & paths | ok — all 5 settings (`STYLE_LEARN_THRESHOLD`, `PERSONALIZATION_THRESHOLD_LABELS`, `PREFERENCE_WEIGHT_CAP`, `PREFERENCE_SCORER_CACHE_SIZE`, `PREFERENCE_MAX_TRAINING_LABELS`) present in `config.py` with defaults; no filesystem paths; one cleanup (half-life + 3× multiplier could be config) |

## Module verdict
NEEDS-WORK — no BLOCKER and no SEV1: isolation, log hygiene, Celery idempotency,
the RCE allowlist, and the honest-threshold/recency-decay clip-quality contract
are all verified clean. Open: pin `joblib==1.5.3` (its private internals are the
RCE-allowlist enforcement point and currently float), fix the smart-default
mode bug (first-over-threshold instead of max, duplicated across two functions),
de-dup the train↔serve feature adapter to prevent feature skew, plus four
non-blocking cleanups.

## Issue 75 Reconciliation (2026-06-23, carried forward)
| Finding | Disposition |
|---|---|
| [SEV2] joblib not pinned (model.py:26) | tracked in Issue 109 — STILL OPEN (joblib 1.5.3 floating, re-confirmed 2026-06-24) |
| [cleanup] `model: Any` on PreferenceScorer (model.py:88) | tracked in Issue 109 |
| [cleanup] cold-start log collapse (train.py:79-86) | tracked in Issue 109 |
| [cleanup] no FEATURE_NAMES length assert (features.py:20-29) | tracked in Issue 109 |
| [cleanup] `_LAMBDA` 30-day half-life hardcoded (decay.py:11) | tracked in Issue 200 |
| [SEV2] style_learn mode bug (style_learn.py:55-58) | NEW 2026-06-24 — untracked |
| [SEV2] style_learn DRY (style_learn.py:76-88) | NEW 2026-06-24 — untracked |
| [SEV2] train↔ranking feature-adapter DRY (train.py:61-70) | NEW 2026-06-24 — untracked |
