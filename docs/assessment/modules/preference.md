# preference — assessed 2026-07-20 (post-fix)

Slice: `preference/__init__.py`, `preference/_scorer_cache.py`, `preference/decay.py`,
`preference/efficacy.py`, `preference/features.py`, `preference/model.py`,
`preference/style_learn.py`, `preference/train.py`.

Re-assessment after the two fix waves (diff ca3305c..e92b93a; module commits 2279720 +
f29a2be touching efficacy.py, model.py, train.py). Every finding from the 2026-07-20
morning pass re-verified against HEAD. Unit lane green at assessment time:
`tests/test_preference*.py` + `tests/eval/test_efficacy.py` + `tests/preference/` —
86 passed (the new LGBM round-trip test ran, not skipped, on this host).

## Findings

- [cleanup] preference/efficacy.py:482 + 263 — (carried) `creator_id` still smuggled onto
  `LabeledClip` as a dynamic attribute (`lc.creator_id = creator_id  # type: ignore[attr-defined]`)
  and read back via `getattr(..., "creator_id", m.creator_id)` after seeding
  `CreatorMetrics.creator_id` with a *clip* UUID fallback — direct `LabeledClip` builders
  (the sweep, tests) get metrics attributed to a clip id. | fix: real
  `creator_id: uuid.UUID | None = None` dataclass field, or pass `creator_id` into
  `compute_creator_metrics`.

- [cleanup] preference/efficacy.py:243 — (carried) imports the private `_signal_score` from
  `clip_engine.scoring`; a rename inside clip_engine silently breaks the harness's
  generic-signal baseline. | fix: export a public `signal_score` alias and import that.

- [cleanup] preference/efficacy.py:443-449 — (new, residual of the LIMIT fix) the harness
  query has no SQL-side action/outcome filter, so non-trainable rows (skip/format without
  a positive outcome — dropped later at :456) consume the `PREFERENCE_MAX_TRAINING_LABELS`
  budget; a skip-heavy creator's harness window is smaller/older than the trainer's
  (train.py:56 filters `action.in_(TRAINABLE_ACTIONS)` before its LIMIT). Bounded either
  way — an eval-window skew, not a correctness or scale bug. | fix: add
  `.where(ClipFeedback.action.in_([a.value for a in TRAINABLE_ACTIONS]) |
  ClipOutcome.performed_well.is_(True))` (the outcome arm keeps the rel-2.0 eval-only
  rows) so both queries budget over the same population.

## Resolved since the 2026-07-20 morning pass (verified by reading + running, not assumed)

- **LGBM allowlist round-trip — FIXED.** `model.py:49-58` adds
  `("sklearn.preprocessing._label", "LabelEncoder")` to `_ALLOWED_CLASSES` (LGBMClassifier
  stores its target `LabelEncoder`; the missing entry meant `from_bytes` on ANY mature
  LightGBM model raised and `load_latest`'s broad catch (train.py:194-198) silently
  disabled personalization fleet-wide — the prior pass's exact predicted failure, real,
  now caught). New test `tests/test_preference.py:194`
  (`test_lgbm_scorer_round_trips_through_allowlist`) fits n=30 ≥ threshold=20 (forcing
  the LGBMClassifier branch, `OSError → pytest.skip` libgomp guard kept), asserts
  `type(scorer._model).__name__ == "LGBMClassifier"`, round-trips through
  `_RestrictedUnpickler`, and asserts identical `predict_score` + `label_count`. Ran and
  passed here — allowlist/dep drift now fails CI loudly.
- **`load_labeled_clips` unbounded — FIXED.** efficacy.py:443-449 now mirrors train.py:
  `.order_by(created_at.desc()).limit(settings.PREFERENCE_MAX_TRAINING_LABELS)` (=5000,
  config.py:415 / .env.example:167), then re-sorts ascending (:484) for the chronological
  split. The O(n²) `kendall_tau` is thereby bounded (~1500-row eval split worst case)
  AND no longer on the event loop (next item).
- **Sync fit inside the async efficacy chain — FIXED.** `evaluate_creator` (efficacy.py:504)
  now runs `await asyncio.to_thread(compute_creator_metrics, train, eval_clips, k)` —
  the CPU-bound scorer fit + tau never block the caller's loop, mirroring train.py:103.
  `sweep_half_life` remains sync, but its only callers are the offline CLI
  `scripts/eval_efficacy.py` and tests — no async caller exists; acceptable.
- **PreferenceModel version accumulation — FIXED.** train.py:129-136 prunes
  `version <= new_version - _KEEP_MODEL_VERSIONS` (keep last 5; only newest 2 ever read)
  in the same transaction, still under the `pg_advisory_xact_lock` (:108-110), so a
  concurrent retrain cannot race the delete. Creator-scoped WHERE on the delete.
- **Efficacy train-label divergence — FIXED, exact parity with train.py verified line by
  line.** `LabeledClip` gains a real `action` field (:189) set from the raw feedback
  action (:463). `_train_scorer` (:292-313) now (a) filters to
  `_TRAINABLE_ACTIONS = {upvote, trim, downvote}` — string-identical to train.py:28-31's
  enum set; (b) derives `y = 1 iff action in _POSITIVE_ACTIONS` — matches train.py:77,
  so a downvoted clip whose outcome performed well trains NEGATIVE with the 3× weight,
  exactly like production; (c) weight parity holds: `performed_well=(relevance >= 2.0)`
  ⇔ `performed_well is True` for loader-built rows (`_relevance_for` returns 2.0 only
  then), and `sample_weight` (decay.py:57) applies the multiplier only on `is True`, so
  the False-vs-None coercion is behaviorally identical to train.py:79; (d) `_blend_scores`
  (:331) now feeds `preference_weight(scorer.label_count)` — `label_count` = fitted
  trainable rows (model.py:198), the same count production uses
  (clip_engine/ranking.py:65) — instead of the old `len(train)` that counted eval-only
  rows. Pinned by two new tests (`tests/eval/test_efficacy.py:105`
  `test_downvote_with_good_outcome_trains_negative_like_production`, :128
  `test_non_trainable_actions_are_excluded_from_the_fit`), both passing.

## Verified OK (rubric sweep, re-checked at HEAD)

- Recency decay (decay.py:29-35): `w = e^(-ln2/H · age)`, per-call `half_life_days`
  override validated `> 0`; default λ from config-validated `DECAY_HALF_LIFE_DAYS`.
- Personalization-threshold honesty (model.py:152-166): weight 0 below
  `PERSONALIZATION_THRESHOLD_LABELS`, linear ramp to `PREFERENCE_WEIGHT_CAP` at 2×;
  the efficacy blend reproduces the same honest fallback (weight 0 → pure DNA
  composite, efficacy.py:332-337). No virality promise anywhere in the module.
- Per-creator isolation: creator_id in every WHERE — train.py:55, :115, :132-134 (new
  pruning delete), :154-156, :180-183; efficacy.py:447. Parameterized SQL only.
  Logs carry creator UUIDs only — no PII, no tokens.
- Restricted-unpickler posture unchanged (full `(module, name)` allowlist,
  `_UNPICKLER_LOCK`, `from_bytes` offloaded via `to_thread` in load_latest) — now with
  the LGBM branch actually asserted by CI.
- New-regression scan of `git diff ca3305c..HEAD -- preference/`: nothing beyond the
  three intended fixes; the loader's ascending re-sort (:484) is redundant with
  `chronological_split`'s internal sort but harmless.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — version pruning added under the advisory lock; sessions injected + committed; fits offloaded |
| 2 Concurrency & scale | ok — harness load bounded at 5000, metrics offloaded via to_thread; sweep sync path is CLI-only |
| 3 Security & compliance | ok — isolation on all queries incl. the new pruning delete, allowlist unpickler now CI-asserted for LGBM, UUID-only logs |
| 4 Clip-quality | ok — recency decay + honest threshold fallback verified; harness now trains with production's exact label/weight/count rule |
| 5 Anthropic SDK | n/a (no LLM calls) |
| 6 Cleanliness & typing | 3 cleanup (dynamic creator_id attr; private `_signal_score` import; harness LIMIT budget counts non-trainable rows) |
| 7 Error handling / API | n/a (not a router) |
| 8 Config & paths | ok — all tunables in `.env.example` with descriptions; no filesystem paths |

## Module verdict
clean — all five targeted findings from the morning pass are genuinely fixed and
test-pinned (LGBM round-trip ran green here; efficacy trainer parity verified
symbol-by-symbol against train.py including the 3×-weight downvote edge and the
`label_count` feed); only three cleanup-grade items remain.
