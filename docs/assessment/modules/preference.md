# preference — assessed 2026-07-01

Slice: `preference/_scorer_cache.py`, `preference/decay.py`, `preference/features.py`,
`preference/model.py`, `preference/style_learn.py`, `preference/train.py`, `preference/__init__.py`.

All best-practice / library-behaviour claims below were verified against current official
docs / security research (URLs + dates inline), not from memory, per the hard constraint.

## Findings

- [SEV2] preference/features.py:22-34 — the non-finite guard covers ONLY `dna_match`; the other
  six numeric features (`signal_density`, `hook_energy`, `silence_ratio`, `clip_duration_s`,
  `setup_length_s`) are passed through unsanitised. train.py:61-69 pulls them straight from
  `clip.signals_jsonb["features"]`, so a single NaN/inf stored there poisons the vector: at train
  time `sklearn` rejects non-finite input by default (`check_array(..., ensure_all_finite=True)` —
  scikit-learn 1.5 `LogisticRegression.fit` docs,
  https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.LogisticRegression.html,
  accessed 2026-07-01) so `asyncio.to_thread(fit, ...)` (train.py:97) raises and the whole retrain
  fails; at predict time a NaN feature makes `predict_proba` return NaN → poisons the rerank sort —
  the exact failure the Issue-338 `dna_match` guard exists to prevent. | fix: route every feature
  through one finite-clamp helper (`_finite(x, 0.0)`) inside `clip_features`, not just `dna_match`;
  add a test passing NaN in `hook_energy` and asserting a finite vector.
  (needs-runtime-confirmation that `signals_jsonb` can hold NaN — depends on the ingestion writer.)

- [SEV2] preference/model.py:46-65 + tests/test_preference.py:173,183 — the LightGBM serialization
  branch of `_ALLOWED_CLASSES` has NO round-trip test. Both round-trip tests fit
  `_training_data()` (n=10) with `threshold=20`, so they only exercise the LogisticRegression path;
  the LightGBM-only allowlist entries (`lightgbm.basic.Booster`, `collections.defaultdict`,
  `collections.OrderedDict`, `joblib.numpy_pickle.NumpyArrayWrapper`) are never asserted to
  round-trip through `_RestrictedUnpickler`. The allowlist also pins exact *internal* module paths
  of pinned deps (`sklearn.linear_model._logistic`, `numpy._core.multiarray` — the numpy-2.x path,
  which differs from numpy-1.x `numpy.core.multiarray`; NumPy 2.0 release notes,
  https://numpy.org/devdocs/release/2.0.0-notes.html, accessed 2026-07-01). A dep bump or an
  incomplete LGBM allowlist makes `from_bytes` raise `UnpicklingError`; `load_latest` catches it
  broadly (train.py:181-184) and silently falls back to DNA for ALL mature/personalized models,
  logged only at `warning`. | fix: add a round-trip test that fits with n ≥
  `PERSONALIZATION_THRESHOLD_LABELS` (forces the LightGBM branch), serializes, and asserts
  `from_bytes` returns a scorer with matching predictions — run in CI so allowlist/dep drift fails
  loudly instead of silently disabling personalization fleet-wide.

- [SEV2] preference/style_learn.py:36-58 & 61-88 — `dominant_style` / `style_suggestion` return
  the FIRST value whose count ≥ threshold in dict insertion (first-seen) order, not the most
  frequent. If value B occurs 12× and value A 6× but A is seen first in `history`, the "make this
  your default?" suggestion surfaces A — a wrong, confusing default that contradicts the
  smart-default UX the module cites (NNG default-effect literature per the docstring / DECISIONS
  Issue 187). | fix: among values meeting the threshold pick the arg-max by count deterministically
  (`max((v for v,c in counts.items() if c>=threshold), key=counts.get, default=None)`), and add a
  test with two qualifying values asserting the higher-count one wins.

- [cleanup] preference/style_learn.py:76-88 — `style_suggestion` re-implements the exact per-field
  counting loop of `dominant_style` (48-53) (DRY, rubric 6). | fix: have `dominant_style` return
  `(value, count)` or extract a shared `_dominant(history, field, threshold)` helper and call it
  from `style_suggestion`.

## Verified OK (no finding — checked, not assumed)

- Security posture of the restricted unpickler is *defensible*, not the common bypassable pattern:
  `find_class` (model.py:78-82) allowlists the full `(module, name)` TUPLE, which is exactly the
  mitigation the research recommends — the documented bypasses ("RestrictedUnpickler is Bypassable",
  https://github.com/maurosoria/dirsearch/issues/1073; "Pain Pickle", ResearchGate 369410624,
  accessed 2026-07-01) rely on restricting only module OR only name, or on `collections` gadget
  names like `_itemgetter` / `__builtins__` that are NOT in this allowlist. `defaultdict`'s
  `__reduce__` factory is itself a separate GLOBAL that re-enters `find_class`. joblib's own docs
  confirm plain `joblib.load` executes arbitrary code and "should never be used to load objects from
  an untrusted source" (https://joblib.readthedocs.io/en/stable/persistence.html, accessed
  2026-07-01) — so the allowlist is correct defense-in-depth; the blob's only writer is the app's own
  `to_bytes` into a per-creator DB row. The module-global `NumpyUnpickler` swap is serialized by
  `_UNPICKLER_LOCK` (model.py:37,134) and effective because joblib resolves `NumpyUnpickler` via the
  `numpy_pickle` module global. Residual risk is acceptable for a DB-write threat model.
- Exponential recency decay (decay.py:20-22, `w=e^(-ln2/H·age)`) and honest below-threshold fallback
  (model.py:147-162, weight 0 below `PERSONALIZATION_THRESHOLD_LABELS`) both satisfy rubric-4 and the
  CLAUDE.md Clip-Engine rules. `DECAY_HALF_LIFE_DAYS` / `PERSONALIZATION_THRESHOLD_LABELS` are guarded
  `> 0` by a config validator (config.py:648-663), so the import-time `_LAMBDA` division cannot divide
  by zero.
- Per-creator isolation present on EVERY query: train.py:49 (`ClipFeedback.creator_id`), 108-110,
  166-169 (blob fetch by creator_id+version), load_latest 140-142. Advisory lock + version max+1
  (train.py:102-113) guards the `UNIQUE(creator_id, version)` race. SQL is parameterized
  (`text(...)`, `{"k": ...}`). Only `creator_id` UUIDs are logged — no PII, no tokens.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — session injected + committed (train.py:123); fit offloaded via `to_thread`; LRU bounded by `PREFERENCE_SCORER_CACHE_SIZE` |
| 2 Concurrency & scale | ok — advisory xact lock, per-worker LRU cache, `to_thread` offload; global unpickler lock serializes cold-cache `from_bytes` but bounded by the cache |
| 3 Security & compliance | ok — per-creator isolation on all queries, parameterized SQL, full-tuple pickle allowlist (verified vs research), only UUIDs logged |
| 4 Clip-quality | 3 findings (partial NaN guard; style-learn returns first-not-max); recency decay + honest fallback verified OK |
| 5 Anthropic SDK | n/a (no LLM calls in this module) |
| 6 Cleanliness & typing | 1 cleanup (style_learn DRY); signatures typed |
| 7 Error handling / API | n/a (not a router) |
| 8 Config & paths | ok — all tunables in `.env.example` w/ descriptions + validators; no filesystem paths |

## Module verdict
NEEDS-WORK — no blockers and per-creator isolation + the pickle allowlist are sound, but three
SEV2s (partial NaN guard, untested LightGBM serialization branch that silently disables
personalization, first-not-most-frequent style default) should be fixed.
