# D05 — Clip engine, ML boundary, and eval methodology

**Auditor:** domain researcher, deep standards audit 2026-08-17.
**Scope:** `clip_engine/`, `preference/`, `tests/eval/`, the LLM scoring boundary, and how clip
quality is measured. Read-only pass. All measurements below were run with `.venv/bin/python`
(lightgbm 4.6.0, scikit-learn 1.5.2 — the pinned versions).

---

## Verdict

The *deterministic geometry* half of this engine is measured better than almost any solo project
I have seen, and the Issue-476/480 lane closed on 2026-08-13 genuinely moved the LLM scorer from
"evaluated nowhere" to "evaluated thinly". The *learning* half is broken in a way none of the
gates can see: **LightGBM's untouched `min_child_samples=20` makes the preference model a constant
predictor for every label count from 20 to ~43, which is exactly the band the maturity ramp
covers — so personalization is a measured no-op over its entire ramp while the API tells the
creator it is active.** The test written four days ago to guard precisely this passes only because
its fixture is the one 40-row configuration where LightGBM can split.

Everything else in this domain is a matter of degree: the behavioral clip-quality eval is n=2,
runs after deploy, and nothing watches it; and the human label stream that could answer "did Opus 5
make clips better?" is being written to a table nothing reads.

---

## What the current standard is, with sources

### 1. How the field measures highlight detection in 2026

The academic frame is **video moment retrieval + highlight detection (MR-HD)**, and it is
well-standardised:

- **Datasets:** QVHighlights (10,148 YouTube videos with query-anchored highlight spans),
  TVSum, ActivityNet-Captions, Charades-STA, TaCoS.
- **Metrics:** for moment retrieval, `R1@0.5` / `R1@0.7` (recall@1 at an IoU threshold) and
  `mAP@0.5 / @0.75 / avg`; for highlight detection, `mAP` and **`HIT@1`** — the hit rate of the
  single highest-scoring clip, counting a segment positive only when its saliency label is "very
  good". ([Lighthouse, arXiv 2408.02901](https://arxiv.org/abs/2408.02901);
  [Moment/Highlight via MLLM frame segmentation, arXiv 2512.12246](https://arxiv.org/html/2512.12246v1);
  [SVHighlights, KDD 2026](https://arxiv.org/html/2606.06926v2))
- **Tooling:** [`line/lighthouse`](https://github.com/line/lighthouse) (EMNLP-2024 demo, ICASSP
  2025 + 2026 follow-ups) is a maintained library covering 6 models × 3 feature sets × 5 datasets
  with YAML-configured runs — a reproducible harness rather than a paper repo.

**The important translation:** every one of those metrics is *IoU against a human-annotated span*.
That is exactly the shape of assertion `tests/eval/scenarios/*.yaml` already makes — except the
spans are hand-invented by the author rather than annotated on real video. The gap is not
methodological sophistication; it is that there is no ground truth.

Commercially, the state of the art is not impressive and is worth knowing. Opus Clip's 0–99
"virality score" is [publicly reported as unreliable — clips rated 40 outperforming clips rated
85, ~60–70% clip-selection accuracy on solo-speaker content dropping to ~40% on multi-speaker
conversations](https://viral.day/en/blog/vizard-vs-opus-clip-features-pricing-and-quality-compared-in-2026).
The most rigorous public 2026 benchmark of the category
([reap.video, April 2026](https://reap.video/reports/state-of-top-ai-video-clipping-tools-2026))
used **nine tools × six source videos (5–90 min)**, scored on 12 dimensions, with only two truly
objective metrics: **time-to-first-clip** and **caption WER against human ground-truth
transcripts**. Clip *selection* quality was judged by hand. Nobody in this market has a
defensible automated clip-quality metric. That is a licence to be modest, not a licence to skip it.

### 2. Golden-set sizing for LLM-judged / LLM-produced output

Converged 2026 guidance: **50–200 hand-labelled examples** for a first golden set;
"minimum viable 50–100"; ~100 is where LLM-judge scores stabilise above the run-to-run noise floor;
200 is where small-but-real quality movements become detectable
([Future AGI golden-set guide, 2026](https://futureagi.com/blog/llm-eval-golden-set-design-2026/);
[MLflow 2026 eval guide](https://mlflow.org/articles/integrating-evaluation-into-ai-workflows-2026-guide/)).
For subjective axes, **pairwise comparison with both orderings** beats absolute rubric scoring,
position bias must be neutralised by evaluating (A,B) and (B,A) and counting only consistent wins,
and the judge must be calibrated against 100–300 human-labelled traces with an inter-annotator
agreement number
([Future AGI, LLM-as-judge 2026](https://futureagi.com/blog/llm-as-a-judge/)). The caveat that
matters most here: [LLM judges agree strongly with each other and only weakly with
humans](https://arxiv.org/pdf/2606.03043) — inter-judge consensus is not evidence.

### 3. Online metrics from implicit/explicit feedback at low traffic

- Position bias is the dominant confound: click/keep data used raw as a training signal yields
  sub-optimal rankers. The standard correction is **IPS** — reweight by the inverse probability of
  observation at that position — with the 2026 refinement that IPS breaks under trust/selection
  bias and needs affine or control-function corrections
  ([arXiv 2008.10242](https://arxiv.org/pdf/2008.10242);
  [arXiv 2506.06989](https://arxiv.org/abs/2506.06989)).
- At ≤100 users, an A/B test of two rankers is statistically hopeless. The standard answer is
  **interleaving**: 10–100× more sensitive than A/B, [>100× fewer users for 95%
  power](https://netflixtechblog.com/using-interleaving-in-online-experiments-to-accelerate-algorithm-innovation-at-netflix-a04ee392ec55),
  [82% agreement with the A/B verdict at Airbnb using 4% of traffic and 0.5% of the
  runtime](https://arxiv.org/html/2508.00751v1). Team-draft is the simplest variant and the
  weakest; balanced/debiased interleaving is stronger
  ([Amazon Search, CIKM 2022](https://dl.acm.org/doi/pdf/10.1145/3511808.3557123)).

### 4. Active-speaker detection, 2026

AutoFlip is still EOL. But the "buy or build a real model" landscape has moved since
`DECISIONS.md:11794` was written: **LR-ASD** (Springer IJCV 2025) is a maintained, deliberately
lightweight ASD network, and Sieve's **fast-asd** is a production-optimised TalkNet with
variable-frame-rate support and parallel face detection — both self-hostable, so neither
introduces a sub-processor.
([LR-ASD](https://github.com/Junhua-Liao/LR-ASD);
[fast-asd](https://github.com/sieve-community/fast-asd);
[TalkNet-ASD, mAP 90.8 on AVA](https://github.com/TaoRuijie/TalkNet-ASD))

---

## Findings

### F1 — [HIGH] LightGBM is a constant predictor for label counts 20–43: the personalization ramp is a measured no-op, and the API says it is active

**Evidence:** `preference/model.py:206` fits `lgb.LGBMClassifier(n_estimators=100,
learning_rate=0.1, verbosity=-1)` with every regularisation default untouched. LightGBM's default
`min_child_samples` is 20, i.e. a split is only legal if **both** children hold ≥20 rows. The
LightGBM branch is selected at `n >= PERSONALIZATION_THRESHOLD_LABELS = 20` (`config.py:602`,
`preference/model.py:196-208`). The blend weight ramps `0 → PREFERENCE_WEIGHT_CAP=0.5` across
`n = 20 → 40` (`preference/model.py:162-167`).

Measured on the pinned lightgbm 4.6.0 over realistic feature draws (200 trials per n):

| n labels | trials producing a constant predictor |
|---|---|
| 20, 25, 30, 35, 40 | **200 / 200 (100%)** |
| 44, 45, 50, 60, 80, 100 | 0 / 200 |

`verbosity=-1` suppresses LightGBM's own `"No further splits with positive gain"` warning, so
nothing is logged.

**Failure scenario:** a creator triages 30 clips (say 18 kept, 12 dropped).
`retrain_preference` → `build_and_save` → `fit(n=30)` → LightGBM branch → booster with 1 tree and
0 splits. `predict_score` returns the same probability `c` for every clip.
`rerank_with_preference` (`clip_engine/ranking.py:220-228`) writes
`blended_score = 0.75*score + 0.25*c`, which is strictly increasing in `score` — **the persisted
rank order is byte-identical to the DNA-only ranking**. Meanwhile
`GET /videos/{id}/clips` returns `personalization = {active: true, labels: 30, threshold: 20,
weight: 0.25}` (`routers/clips.py:786-792`), and per `Clip.blended_score IS NOT NULL` the
Issue-465 contract asserts "personalization *was* applied to that clip". The creator is told their
own feedback is now shaping the ranking; it provably is not.

**Why this is a standards deviation rather than a tuning nit:** the recorded decision
(`DECISIONS.md:9066`, Issue 60) chose the maturity gate specifically as *"the standard hybrid
cold-start strategy — start content-based, grow personalization as the creator's own feedback
accumulates."* The gate as built does not grow anything: it delivers exactly zero effect from 20
to ~43 labels and then jumps to the full `PREFERENCE_WEIGHT_CAP` the instant the model becomes
non-degenerate (the ramp has already saturated at n=40). The recorded intent is a ramp; the
implementation is a step function at n≈44 with a 24-label dead zone in front of it. Standard
practice for GBDT below a few hundred rows is to shrink `min_data_in_leaf`/`num_leaves`
explicitly, or to not use GBDT at that volume at all.

**Corollary — the LogisticRegression branch never influences a single served ranking.** `fit()`
selects LR for `n < 20` (`preference/model.py:196-202`), and `preference_weight` returns exactly
`0.0` for `n < 20` (`:164-165`), which makes `rerank_with_preference` early-return at
`ranking.py:184-185` before scoring anything. So an LR-backed `PreferenceScorer` is trained,
versioned, blob-persisted, pruned, LRU-cached and joblib-deserialised — and is unreachable at
serve time by construction. It exists only for `preference/efficacy.py`.

Judgement call in the *fix*, not the diagnosis: lower `min_child_samples`/`num_leaves` for the
small-n regime, or raise the LightGBM switch-over to ~60–100 labels and let LR carry 20–60 with a
non-zero weight. The diagnosis itself is measured, not argued.

### F2 — [HIGH] The Issue-480 rerank eval is green only because its fixture is the knife-edge case (new vacuous-green instance)

**Evidence:** `tests/preference/test_rerank_eval.py:105-131` and
`tests/eval/scenarios/ranking/rerank_preference_flips_order.yaml`. The fixture is
`rows_per_class: 20` — exactly 20 positive and 20 negative rows, with every continuous feature
perfectly separated between the two classes (`signal_density` 2.0 vs 0.5, `silence_ratio` 0.05 vs
0.40, …) and only a `0.01 * (i % 20)` deterministic jitter within each class.

That is the single 40-row shape where the unique best split partitions the data **exactly 20/20**,
satisfying `min_child_samples=20` on both children. Running the repo's own `_train_scorer` on it
yields **92 trees** and `p_favorite − p_leader = 0.9999`. Running the same `fit()` on 200
realistic 40-row label sets yields **1 tree and spread `0.000000` every time** (F1 table).

**Failure scenario:** the test whose docstring says *"a REAL trained preference model … must FLIP
the DNA ordering"* is permanently green, while every real creator at that label count gets no flip
at all. Its `test_control_weight_zero_preserves_dna_order` control proves the harness *can* fail —
but only by patching `preference_weight` to 0, which does not probe the degeneracy at all. The
fixture was constructed to satisfy `label_count == 2 × threshold` (asserted at `:115`) and the
20/20 balance came along for free; nobody chose the knife edge deliberately.

This is `AUDIT_BRIEF.md §6`'s failure family — *an intermediate layer reports success without
exercising the thing it claims to verify* — landing on the newest gate in the domain, four days
after that gate was written to close a SEV1. It is not re-filing Issue 480; Issue 480's closure is
the defect.

**Cheapest structural fix:** parametrise the fixture over an *unbalanced* row split (e.g. 27/13)
and assert `scorer._model.booster_.num_trees() > 1` — one assertion that makes the whole class
impossible.

### F3 — [MEDIUM] The efficacy harness has no DNA-only arm, so it cannot measure the preference model's marginal contribution — and structurally cannot see F1

**Evidence:** `preference/efficacy.py:210` — `RANKINGS = ("random", "generic_signal",
"dna_preference")`. The `generic_signal` arm is `clip_engine.scoring._signal_score`
(`efficacy.py:262`), a *different ranker entirely* — cold-start signal features with no DNA. There
is no arm that is "the DNA composite alone".

**Failure scenario:** with a degenerate scorer (F1), `_blend_scores` returns a strictly monotone
transform of `c.dna_composite`, so `ndcg["dna_preference"]` equals the DNA-only NDCG to full float
precision — but nothing computes the DNA-only NDCG, so the equality is unobservable. The
per-retrain warn-ratchet (`worker/tasks.py:1877-1898`) only fires on a *drop of >0.05 versus the
previous model version*; a model that has contributed exactly zero lift since inception never
drops and never warns. The single question the harness exists to answer — *"is personalization
actually helping this creator?"* — needs an ablation control it does not have.

This is otherwise the strongest piece of methodology in the repo (chronological split, graded
relevance, paired bootstrap CIs, Kendall tau, a shared training-set select so the offline set is
byte-identical to production's — Issue 475). The fix is one dictionary entry:
`"dna_only": [c.dna_composite for c in eval_clips]`.

### F4 — [MEDIUM] The behavioral clip-quality eval is n=2, runs strictly post-deploy, and nothing watches it

**Evidence:** `tests/test_llm_live_scoring.py` (Issue 476, closed 2026-08-13). Class A is **one**
hand-written synthetic transcript with **two** hand-placed candidate windows; class C is one more
synthetic transcript with two windows; class B is a shape/contract check. Seven live calls a
night, ~$1. No real creator transcript, no real audio, no human label. Against 2026 golden-set
guidance (50–200 examples; ~100 before scores stabilise above noise) this is an n=2 eval, and it is
graded by fixed orderings on material the author wrote to have an obvious answer.

Worse than its size is its position in the pipeline:

- `llm-e2e-nightly.yml` is `schedule` + `workflow_dispatch` only, and is **not** one of the 8
  required checks (`process-map.md §1`).
- The required `eval/clip-quality` context posts `state: 'success'` with *"Skipped — no
  clip_engine/ or tests/eval/ changes"* on any PR that does not touch those paths
  (`ci.yml:553-565`) — and a `config.py` model-id swap or a `knowledge/util.py` prompt-block change
  does not touch them.
- The workflow's terminal step (`llm-e2e-nightly.yml`, "Post result summary") writes only to
  `$GITHUB_STEP_SUMMARY`. There is **no notification, no issue creation, no Slack/email**, and per
  `process-map.md §6` there are *zero alert rules anywhere in the repo* and `health-check.yml`'s
  schedule already died silently for six weeks without anyone noticing.

**Failure scenario:** someone changes `ANTHROPIC_MODEL_SCORING` or edits the scoring rubric in
`clip_engine/scoring.py`'s `_SYSTEM_STATIC`. All 8 required checks pass (the goldens re-record
requirement only bites on `_OUTPUT_SCHEMA` sha changes, not prompt text). Push-to-main auto-deploys
within minutes. The only behavioral evidence runs ~9 hours later, into a step summary, and a
majority-of-3 failure on class A produces a red run in a workflow list nobody opens. The repo's own
history says this exact gap costs 6–10 weeks.

Not re-filing Issue 476 — it is closed and the closure is real work. The finding is that it is a
floor presented as a ceiling, and that it is unwatched.

### F5 — [MEDIUM] `clip_impressions` is a write-only table and creator keep-rate is computed nowhere

**Evidence:** `models.py:1079-1104` — the table exists explicitly because *"this is the position
record that counterfactual/IPS evaluation needs; capturing it now is cheap insurance — it cannot be
reconstructed later"*. It is written at `routers/clips.py:858-876`. `grep -rn ClipImpression`
across the tree returns **no reader** outside `tests/test_clip_impressions_integration.py` (an RLS
test). `grep -rn "keep_rate\|keep-rate"` across all Python and TypeScript returns **zero hits**.

Meanwhile `ClipTriage` (`models.py:164-180`, Issue 444) has been producing exactly one
keep/dropped/pending verdict per clip since 2026-08-10 — a clean, deduped, human label stream, the
thing the field would kill for.

**Failure scenario:** the question the Opus 5 upgrade was bought to answer — "are the clips
better?" — has a data substrate that has been accumulating for weeks and no query, metric,
dashboard, or Prometheus gauge reads it. A quality regression shipped to production is invisible
by construction; the first signal would be a creator complaining.

**A second defect in the data being collected:** the impression rows are written on *every*
`list_clips` call. A creator who reloads the review page five times logs five impressions per clip
at the same rank. Any future IPS estimate built on this table without a dedup or session key will
have an inflated propensity denominator and will systematically under-weight the top ranks — the
exact bias the table was created to correct.

### F6 — [MEDIUM, judgement call] The reframe BUILD decision holds; the decision to skip audio-visual ASD is what the churn is buying

I **endorse** `DECISIONS.md:11794` (Issue 189, build-not-buy). The cost and latency arguments have
weakened — hosted reframe APIs are now commodity (Reap, Vmaker, Choppity, Klypse all ship speaker-
tracking reframe, several with APIs) — but the argument that does not expire is the third one:
a hosted reframe API is a **new video-data sub-processor** under GDPR Art. 28 and YouTube API
Services ToS §VII, receiving source video containing creator PII and third-party faces. For a
solo maintainer running a private beta, adding a sub-processor to a feature that can be
self-hosted is the wrong trade, and no 2026 vendor I found removes that constraint. The AutoFlip
trap is correctly documented and still true.

What I **argue against** is `DECISIONS.md:12938` (2026-08-04, decision 6), which rejects a
TalkNet-class audio-visual model as *"overkill for the ≤100-user beta (new ML surface, real
latency/cost)"* and substitutes co-occurrence + largest-during-turn + mouth-motion-energy voting
(`clip_engine/speaker_map.py:14-17`). Three things have changed or were mis-weighted:

1. **The premise is now weaker.** LR-ASD (IJCV 2025) is explicitly a *lightweight* ASD network,
   and fast-asd is a production-optimised TalkNet with variable-FPS support. Both are
   self-hosted — they add no sub-processor, so the Issue-189 argument that justified BUILD
   *also* justifies building on a real ASD model rather than a heuristic.
2. **The observed maintenance cost contradicts "overkill".** Per `architecture-map.md §A4`,
   **10 of the last 15 clip-engine decisions are reframe-geometry fixes** — Issues 433, 439, 440,
   441, 443, 448, 450 are all, restated, *"we picked the wrong face/seat/region."* That is not
   incidental polish; that is a heuristic repeatedly failing at the one thing an ASD model exists
   to do, and each fix is a new hand-tuned threshold (`MIN_DIARIZATION_COVERAGE = 0.6`,
   `MIN_MAPPING_CONFIDENCE = 0.3`, `TRACK_MATCH_MAX_FRAC = 0.15`, `TURN_GAP_MERGE_S = 0.4`,
   `BACKCHANNEL_MAX_S = 0.8`, `_W_COOCCURRENCE = 1.0`, …) with no held-out set behind it.
3. **The failure mode is undefended.** Two-shot podcast, both faces similar size; the *listener*
   chews, nods, and laughs while the speaker talks steadily → mouth-motion energy (weighted 2×,
   `speaker_map.py:~60`) votes the wrong seat → `speaker_cut` frames the listener for the whole
   turn. Issue 478's own scope caveat records that `test_render_env_reframe.py` **cannot** prove
   seat ordering at the available fixture resolution. So the next instance of this class has no
   gate in front of it either.

This is a judgement call and I flag it as one. The honest framing is: the project has taken on
~3,200 lines and ~38% of the clip engine — the full maintenance cost of BUILD — while deliberately
declining the component that would make the build good. That is the worst square of the
build-vs-buy matrix, and the reversal that put it there is recorded in one clause of a ten-decision
batch entry rather than as a decision of its own.

---

## The smallest credible clip-quality eval that closes the real gap

Costed honestly for one maintainer with ≤100 beta users. This is the deliverable I would argue for
over any of the fixes above except F1.

**Corpus — 40 clips, one afternoon.** Take 5 real ingested videos already in prod spanning the
real distribution (one long podcast, one stream VOD, one talking-head, one multi-speaker, one with
no retention data). For each, keep the engine's top 8 candidates as *rendered* clips. 40 clips
total. Store the transcript window, the signals, the score, the principle, and the render, under
`tests/eval/corpus/` with a provenance README (the Issue-481 LibriSpeech fixture already
establishes this pattern in-repo).

**Labels — the maintainer, twice, two weeks apart.** For each clip, one binary keep/drop plus one
free-text reason. Re-label the same 40 after a two-week gap and report **intra-rater Cohen's
κ**. This is the honest version of inter-rater agreement for a one-person team, it costs ~90
minutes total, and it establishes the noise floor below which no engine change is interpretable.
If κ < 0.6, the rubric is the problem and no eval built on it will mean anything. Where a friend
beta-tester is available (Issues #26/#28/#282), a second rater on the same 40 upgrades this to
real inter-rater κ at no extra engineering cost.

**Metric — HIT@1 and nDCG@8 against the labels.** Both are already implemented in
`preference/efficacy.py` (`ndcg_at_k`, `reciprocal_rank`) and both are the field-standard highlight
metrics. Report `HIT@1` (did the top-ranked clip get a keep?) and `nDCG@8`, with a paired bootstrap
CI — the harness already has `paired bootstrap` machinery. Do **not** add an LLM judge for the
subjective axis; the 2026 evidence that LLM judges agree with each other and not with humans is
strong, and you have a human. Use the LLM only where it is already used well: fixed orderings on
constructed adversarial pairs.

**Cadence — reporting, not gating, for one release cycle.** Run it manually before any change to
`clip_engine/scoring.py`'s prompt, `_OUTPUT_SCHEMA`, or `ANTHROPIC_MODEL_SCORING`, and record the
number in `DECISIONS.md` alongside the change. After three runs establish variance, ratchet it into
the `eval/clip-quality` commit status with a floor, exactly as the geometry scenarios were.

**Cost:** ~40 rendered clips of storage; ~2 hours of labelling in total; ~$3–5 of Opus 5 tokens per
full re-score. That is the entire price of being able to answer "did the Opus 5 upgrade make clips
better?" — a question currently unanswerable, on an upgrade that roughly doubled the cost of the
three highest-volume calls in the system.

**Online, in parallel, essentially free:** the keep-rate metric from F5. Compute
`kept / (kept + dropped)` bucketed by served `rank`, from `clip_triage` joined to deduped
`clip_impressions`. Deduplicate impressions to one row per (clip, day) before using them as a
propensity denominator. This is not an unbiased quality measure — it carries position bias (rank-1
clips get judged first and most favourably) and presentation bias (the auto-render top-8 cut means
ranks 9+ are never rendered and therefore never fairly judged). Treat the raw number as a *trend
line*, and if you ever want to compare two engine versions on live traffic, use **interleaving**,
not A/B: at 100 users an A/B test of two rankers will never reach power, and interleaving is the
documented answer at 100× fewer users.

---

## What is genuinely right here

Not padding — these are specific and load-bearing.

1. **The geometry eval is the best-defended gate in the repo.** 32 scenarios, a 100% pass-rate
   assertion, a ratcheted `SCENARIO_FLOOR = 31` (`tests/test_clip_engine.py:269`), a regex scan
   forbidding unapproved `skip`/`xfail` in the YAML with an empty allowlist, and — the detail that
   shows real understanding — the CI enforcement is a **commit status** rather than a required job,
   because GitHub reports a skipped required job as success (Issue 265). One invariant, defended
   properly, is worth more than five defended badly.
2. **`tests/test_scoring_goldens.py` is current-standard.** Real recorded Anthropic response
   bodies replayed through `anthropic.types.Message.model_validate` into the *real* parse path, a
   genuine `stop_reason="max_tokens"` truncation golden, and a sha256 pin on `_OUTPUT_SCHEMA` that
   reds CI until a ~$0.20 re-record. Most teams' "LLM tests" are a `MagicMock` returning a
   hand-typed dict.
3. **The Issue-465 `score` / `blended_score` split** (`ranking.py:167-171`, migration 0059) is the
   right shape: the fit composite is immutable, the personalized value is separate, and
   `blended_score IS NULL` is a meaningful signal. Adding this late, correctly, is unusual.
4. **Issue 475's shared `training_rows_select`** (`preference/train.py:91-137`) makes the offline
   harness train on byte-identically the same dataset production does. The docstring explaining why
   the VERDICT partition must include `skip` and the TRAINABLE filter must run *after* it is the
   best piece of prose in the codebase, and the rule it encodes (a creator revising their own
   judgement is a label correction, not annotator disagreement) is correct labelling theory.
5. **Honest failure everywhere in the rerank path.** `predict_score` raises on feature-count drift
   rather than returning a misleading 0.5; `load_latest` refuses a feature-schema-drifted model;
   `rerank_with_preference` scores everything before mutating anything and falls back to DNA on
   exception *or* on a non-finite prediction; `_safe_score` sorts NaN deterministically to the
   bottom. Every one of these degrades toward the honest answer.
6. **The `clip_impressions` table existing at all.** Writing the position log two months before
   anyone needs it, with the correct reason (it cannot be reconstructed), and correctly excluding
   creator-made selections so a NULL rank does not poison the top slot, is a genuinely good
   instinct. F5 is a criticism of the reader, not the writer.

---

## Decisions this domain needs but does not have

1. **What "the clip engine is working" means, numerically.** There is no recorded definition of an
   acceptable HIT@1 / keep-rate / nDCG, no drift monitor, and no re-eval cadence. `DECISIONS.md`
   has 259 entries and none of them says what good looks like for the product's core output.
2. **A minimum-viable-`n` position for the preference model.** F1 exists because nobody wrote down
   the number of labels below which a learned reranker is not worth serving. That number should be
   measured (it is ~44 today, and it is an artefact of an unset LightGBM default rather than a
   choice) and it should drive `PERSONALIZATION_THRESHOLD_LABELS`, not the other way round.
   Relatedly, `PREFERENCE_MAX_TRAINING_LABELS = 5000` and `PREFERENCE_FEEDBACK_SCAN_LIMIT = 20_000`
   defend against a power creator who cannot exist in a ≤100-user beta, while the binding
   constraint at n<44 is undefended — the caps are the wrong end of the distribution.
3. **A model-upgrade validation policy.** Twenty pinned `ANTHROPIC_MODEL_*` ids, a goldens set that
   must be re-recorded on schema change, and no written rule about who validates a model swap,
   against what, before it auto-deploys. The Opus 5 upgrade is the precedent and it shipped
   unvalidated on quality.
4. **Whether the nightly eval is allowed to fail silently.** Either it gates (move the scoring
   classes into a pre-merge lane with recorded fixtures) or it alerts (create an issue / notify on
   red). "Runs nightly into a step summary" is the shape of a gate that will be discovered dead in
   week 7.
5. **A position on the reframe endgame.** Either write down "the heuristic ladder is final for v1
   and we accept the wrong-seat failure class", or schedule LR-ASD/fast-asd. The current state is
   neither — an open-ended sequence of geometry fixes with no stated stopping condition and no
   held-out set to say whether any of them helped.
6. **Whether keep-rate is a product metric.** The triage stream and impression log are the two most
   valuable assets this project owns and neither has an owner, a query, or a dashboard.
