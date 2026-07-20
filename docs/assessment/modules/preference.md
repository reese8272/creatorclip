# preference — assessed 2026-07-20

Slice: `preference/__init__.py`, `preference/_scorer_cache.py`, `preference/decay.py`,
`preference/efficacy.py` (new since prior pass), `preference/features.py`, `preference/model.py`,
`preference/style_learn.py`, `preference/train.py`.

Changed since f70a857 (extra scrutiny): `decay.py`, `efficacy.py`, `features.py`, `style_learn.py`.
Unit lane green at assessment time: `tests/test_preference.py` + `tests/test_preference_metrics.py`
(32 passed).

## Findings

- [SEV2] preference/model.py:46-65 — (carry-forward from 2026-07-01) the LightGBM serialization
  branch of `_ALLOWED_CLASSES` still has NO round-trip test. Verified today: every round-trip test
  (`tests/test_preference.py:176,186`) fits `_training_data()` (n=10) with `threshold=20`, so only
  the LogisticRegression path is exercised; `tests/test_preference_edges.py` and
  `tests/test_preference_scorer_cache.py` also never serialize an LGBM scorer. The LGBM-only
  allowlist entries (`lightgbm.basic.Booster`, `collections.defaultdict`, `OrderedDict`,
  `NumpyArrayWrapper`) and the pinned numpy-2.x internal path (`numpy._core.multiarray`) are
  unasserted; a dep bump or incomplete allowlist makes `from_bytes` raise, `load_latest` catches
  broadly (train.py:180-184) and silently falls back to DNA for ALL mature/personalized models,
  logged only at `warning`. | fix: add one round-trip test that fits n ≥ threshold (forcing the
  LGBM branch; keep the existing `OSError → pytest.skip` libgomp guard from
  test_fit_lgbm_at_threshold), serializes, and asserts `from_bytes` predictions match — so
  allowlist/dep drift fails CI loudly instead of disabling personalization fleet-wide.

- [SEV2] preference/efficacy.py:414-431 — `load_labeled_clips` is unbounded: no `LIMIT`, and it
  fetches ALL feedback rows (including skip/format, filtered only afterwards in Python at :438).
  train.py:53 caps the very same join at `PREFERENCE_MAX_TRAINING_LABELS` precisely to avoid this
  (Issue 102), but the harness runs on EVERY retrain via `worker/tasks.py:_emit_preference_metrics`
  → `evaluate_creator`. Compounding: `kendall_tau` (efficacy.py:88-113) is O(n²) pure Python and
  runs 3× (one per ranking) over the 30% eval split — a power creator with thousands of labels
  costs millions of pair iterations per retrain, inside the session that still holds the
  `retrain:{cid}` advisory lock. | fix: mirror train.py — add
  `.where(ClipFeedback.action.in_(TRAINABLE_ACTIONS) | <outcome-positive>)` semantics in SQL and
  `.order_by(created_at.desc()).limit(settings.PREFERENCE_MAX_TRAINING_LABELS)` (then re-sort asc
  for the chronological split).

- [SEV2] preference/efficacy.py:267-302 — `_train_scorer` docstring claims it "reproduc[es]
  preference.train.build_and_save's label/weight construction", but the label rule diverges:
  `y = 1 if relevance >= _REL_KEEP`, and `_relevance_for` (:200-208) returns 2.0 whenever
  `performed_well is True` REGARDLESS of action — so a downvoted clip whose outcome performed well
  trains as a positive (and gets the 3× weight at :295), whereas production (train.py:71-73) labels
  it negative with 3× weight. The "dna_preference" arm therefore isn't the production trainer,
  biasing the moat metric the harness exists to measure. Bounded (downvote+performed_well is rare).
  | fix: carry the raw action on `LabeledClip` and derive the train label from action only
  (downvote → 0), keeping graded relevance for eval; or document the deviation in DECISIONS.

- [SEV2] preference/efficacy.py:302 (`fit` inside `_train_scorer`) — CPU-bound
  LightGBM/LogisticRegression fit runs synchronously inside the `async` chain
  `evaluate_creator → compute_creator_metrics → _blend_scores → _train_scorer`; train.py:97
  deliberately offloads the identical fit via `asyncio.to_thread` for this reason. Today's only
  caller is the worker's private `run_async` loop (bounded harm: it just serializes the task while
  holding the session + advisory lock), but any future API-loop caller — the module docstring
  invites reuse — would stall the server for seconds. `sweep_half_life` (:341-384) multiplies this
  by the 4-point grid per creator. | fix: make `_train_scorer` async-aware or wrap the fit at the
  call sites with `await asyncio.to_thread(fit, ...)` like train.py.

- [SEV2] preference/train.py:106-123 — every retrain inserts a new `PreferenceModel` row
  (weights_blob up to ~100s of KB for LGBM) and no code path ever deletes old versions; retrain is
  feedback-debounced (worker/tasks.py:1010-1027) but still fires on every new-feedback batch, so
  rows/blob storage grow without bound per creator over the product's lifetime. Only the newest 2
  versions are ever read (load_latest:139-144; the worker's NDCG warn-ratchet reads `.limit(2)`).
  | fix: after the commit in `build_and_save`, delete rows with
  `version < new_version - 4` for this creator (keep last 5) — safe under the same advisory lock.

- [cleanup] preference/efficacy.py:254-255 + 463 — `creator_id` is smuggled onto `LabeledClip` as a
  dynamic attribute (`lc.creator_id = creator_id  # type: ignore[attr-defined]`) and read back with
  `getattr(..., "creator_id", m.creator_id)` after seeding `CreatorMetrics.creator_id` with a
  *clip* UUID as fallback — any caller that builds `LabeledClip`s directly (the sweep, tests) gets
  metrics attributed to a clip id. | fix: add `creator_id: uuid.UUID | None = None` as a real
  dataclass field on `LabeledClip` (or pass `creator_id` into `compute_creator_metrics`).

- [cleanup] preference/efficacy.py:235 — imports the private `_signal_score` from
  `clip_engine.scoring`; a rename inside clip_engine silently breaks the harness's
  generic-signal baseline. | fix: export a public `signal_score` alias from clip_engine.scoring
  and import that.

## Resolved since 2026-07-01

- features.py NaN guard covering only `dna_match` — **FIXED** (Issue 352): `_finite()`
  (features.py:8-16) now clamps every float feature; regression tests
  `tests/test_preference_edges.py:64,78` cover NaN in both `dna_match` and a non-dna feature.
- style_learn returning first-over-threshold instead of most-frequent — **FIXED**
  (Issue 352 Batch J): `_dominant` (style_learn.py:38-58) takes the argmax by count, ties to
  first-seen; tests `test_*_returns_argmax_not_first_over_threshold`
  (tests/preference/test_style_learn.py:58,122) pin it.
- style_learn DRY (duplicated counting loop) — **FIXED**: both `dominant_style` and
  `style_suggestion` now delegate to the shared `_dominant` helper.

## Verified OK (checked, not assumed)

- Recency decay (decay.py:29-35): `w = e^(-ln2/H · age)` with the Issue-200 per-call
  `half_life_days` override validated `> 0` (:24-25); production default `_LAMBDA` derives from the
  config-validated `DECAY_HALF_LIFE_DAYS`. Rubric-4 recency-decay requirement satisfied.
- Personalization-threshold honesty (model.py:147-162): weight 0 below
  `PERSONALIZATION_THRESHOLD_LABELS`, linear ramp to `PREFERENCE_WEIGHT_CAP` at 2×; the efficacy
  blend (`_blend_scores`:305-324) reproduces the same honest fallback (weight 0 → pure DNA
  composite). `preference_weight` gating unchanged since the prior pass.
- Per-creator isolation on every query: train.py:49, 109, 141, 166-169
  (`creator_id` in every WHERE); efficacy.py:429 (`ClipFeedback.creator_id == creator_id`); the
  worker retrain additionally runs under `db.tenant_session(cid)` RLS (worker/tasks.py:985).
  Parameterized SQL only (`text(...)` with bound params). Logs contain only creator UUIDs — no
  PII, no tokens, no virality promises.
- Restricted-unpickler posture unchanged from the 2026-07-01 verification (full `(module, name)`
  tuple allowlist; `_UNPICKLER_LOCK` serializes the module-global swap; `from_bytes` offloaded via
  `to_thread` in load_latest).
- Metric math in efficacy.py spot-checked: DCG/NDCG discount `log2(i+2)` matches Järvelin &
  Kekäläinen; `kendall_tau` tie counting is correct tau-b (pairs tied in x counted toward `ties_x`
  regardless of y and vice versa); `bootstrap_ci` quantile indices are correct for ci=0.95 and
  deterministic per seed; `chronological_split` rejects out-of-range `train_frac` and never
  shuffles.
- Config: all module tunables (`PREFERENCE_WEIGHT_CAP`, `PERSONALIZATION_THRESHOLD_LABELS`,
  `DECAY_HALF_LIFE_DAYS`, `STYLE_LEARN_THRESHOLD`, `PREFERENCE_SCORER_CACHE_SIZE`,
  `PREFERENCE_MAX_TRAINING_LABELS`) present in `.env.example` with descriptions.
- `_scorer_cache` LRU is lock-guarded, bounded by `PREFERENCE_SCORER_CACHE_SIZE`, keyed by
  immutable `(creator_id, version)` — no stale-model risk.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | 1 finding (unbounded PreferenceModel version accumulation); sessions injected + committed; fit offloaded in train.py |
| 2 Concurrency & scale | 2 findings (unbounded load_labeled_clips + O(n²) tau per retrain; sync fit inside async efficacy chain) |
| 3 Security & compliance | ok — per-creator isolation on all queries incl. new efficacy loader, RLS tenant_session on retrain, parameterized SQL, allowlist unpickler, UUID-only logs |
| 4 Clip-quality | 1 finding (harness train-label divergence vs production); recency decay + honest threshold fallback verified OK |
| 5 Anthropic SDK | n/a (no LLM calls) |
| 6 Cleanliness & typing | 2 cleanup (dynamic creator_id attr + type:ignore; private `_signal_score` import) |
| 7 Error handling / API | n/a (not a router) |
| 8 Config & paths | ok — all tunables in `.env.example` with descriptions; no filesystem paths |

## Module verdict
NEEDS-WORK — three of four prior findings fixed (NaN guard, style argmax, DRY) and isolation /
decay / threshold-honesty are sound, but the LightGBM serialization branch remains untested
(carry-forward — silent fleet-wide personalization loss on dep drift) and the new efficacy harness
adds four defects: unbounded per-retrain load + O(n²) tau, sync fit on the async path, a
train-label divergence that biases the moat metric, and unbounded model-version accumulation.
