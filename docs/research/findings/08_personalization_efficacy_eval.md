# Research Brief 08 — Personalization-Model Efficacy & Clip-Quality Eval (Issue 173)

**Author:** read-only ML-efficacy + evaluation research agent · **Date:** 2026-06-22
**Drives:** Issue 173 (Phase 1 CHECK) → proposed sub-issues below
**Scope:** Can we *prove* the DNA + recency-decayed preference reranker picks good clips for *this*
creator? · offline/online eval design · adversarial eval-set expansion (the open pre-launch gate) ·
recency-decay validation · cold-start honesty · feedback→outcome loop integrity · continuous eval.
**Method:** current industry standard researched first (links inline); every repo claim cited
`file_path:line`. Where I could not verify a claim, I say so.

> **Honesty/ToS guardrails respected throughout.** Nothing here proposes a virality metric — every
> quality measure is a *fit-with-this-creator* or *agreement-with-this-creator's-own-labels*
> measure (CLAUDE.md "Honesty Constraint"; principle #11 `docs/CLIPPING_PRINCIPLES.md:33`).
> **Eval scope split (do not duplicate prompt 15 / Issue 180):** this brief owns the *clip-quality
> and model-efficacy methodology* — what to measure and the threshold definitions. Prompt 15 owns
> *CI reliability, flaky-test handling, and the mechanics of gating `clip_engine/` changes on the
> eval*. Cross-references are marked **[→15]**.

---

## 1. Executive summary — can we currently prove the model is good?

**No. Today we can prove the engine is *correct* (clips start at the setup, invariants hold), but
we cannot prove it is *good* for a real creator. There is zero offline ranking metric and zero
baseline comparison anywhere in the repo.** A `grep` for `ndcg|precision_at|map_at|kendall|spearman|
holdout` across all `.py` returns nothing. The moat — "the only AI editor that truly knows your
channel" — is asserted by architecture, not measured.

The top gaps, highest-leverage first:

1. **No efficacy metric exists.** The preference model is trained (`preference/train.py:34`) and
   blended (`clip_engine/ranking.py:27`), but nothing ever asks "does the reranked order agree with
   the creator's held-out upvotes/outcomes better than (a) random or (b) DNA-only / a generic
   signal baseline?" The industry-standard offline answer is a **chronological held-out split +
   NDCG@k / MAP / a rank-correlation (Kendall τ)** computed *per creator* and aggregated
   ([Shaped.ai offline-vs-online](https://www.shaped.ai/blog/evaluating-recommender-models-offline-vs-online-evaluation);
   [Evidently 10 ranking metrics](https://www.evidentlyai.com/ranking-metrics/evaluating-recommender-systems)).
   We have the data to build it (`clip_feedback`, `clip_outcomes`, `clips.signals_jsonb`) but no
   harness. **This is the single most important deliverable** and is what turns "tests pass" into
   "model is good."

2. **The "eval harness hardened with adversarial/edge cases" gate is marked done in one doc and
   open in another — a live contradiction.** `docs/PROJECT_STATE.md:1176` checks it off
   ("3 new fixtures; fixed early-peak MIN_CLIP_S bug") while `CLAUDE.md:273` and the Pre-Public-
   Launch list still carry it as **open**. The truth is in between: 6 fixtures exist
   (`tests/eval/scenarios/*.yaml`) and they are real, but they cover a **narrow slice** of the
   adversarial space (Section 3 enumerates what's missing) and they assert only *geometry*
   (peak/setup bounds), never *ranking quality*. Flag and reconcile.

3. **The eval is a unit test, not a quality gate.** The scenarios run inside ordinary `pytest`
   (`tests/test_clip_engine.py:203` `test_eval_scenario`), not a dedicated, separately-reportable
   gate that must be green before any `clip_engine/` change as CLAUDE.md Testing Rules require
   (`CLAUDE.md:209`). It is *de facto* gated because it's in the suite, but there is no explicit
   "clip-eval" job, no pass-rate threshold, and no protection against a future `pytest -k`
   accidentally excluding it. The **enforcement mechanics belong to prompt 15 [→15]**; the
   **scenario design and thresholds belong here**.

4. **Recency decay's half-life is asserted, not validated.** `_LAMBDA = ln(2)/30`
   (`preference/decay.py:11`) hard-codes a 30-day half-life with the justification "feedback adapts
   faster than channel identity (DNA uses 90 days)" (`preference/decay.py:5`). The literature is
   explicit that the *correct* half-life is **data-dependent and must be tuned** — e.g. matrix-
   factorization work found a ~150-day half-life optimal for ratings
   ([Half-Life Decaying Model, CEUR Vol-2038](https://ceur-ws.org/Vol-2038/paper1.pdf);
   [Ding & Li, Time-Weight CF](https://cseweb.ucsd.edu/classes/fa17/cse291-b/reading/p485-ding.pdf)).
   30 days is a *defensible default*, but we have never measured whether it actually beats 15/60/90
   on our own data, nor proven the "content pivot" claim (an old preference is genuinely
   down-weighted). It *is* unit-tested for math correctness (`tests/test_preference.py:23` — 30d→0.5)
   but never for efficacy.

5. **Cold-start honesty is half-built: the *virality* honesty constraint is everywhere; the
   *personalization* honesty constraint is nowhere.** Below `PERSONALIZATION_THRESHOLD_LABELS=20`
   (`config.py:162`) the reranker correctly gets weight 0 and ranking falls back to DNA+signals
   (`preference/model.py:139`, `clip_engine/ranking.py:42`) — the *mechanics* are honest and
   well-tested (`tests/test_preference_rerank.py:22`). **But the creator is never told this.** The
   `ClipOut` schema (`routers/clips.py:29`) has no field for personalization status; the
   `FitBadge`/`WhyThisClip` UI shows a channel-fit tier and the "not a guarantee" disclaimer
   (`frontend/src/components/ui/fit-badge.tsx:11`, `frontend/src/components/review/WhyThisClip.tsx:21`)
   but **never distinguishes "this ranking is personalized to your 40 ratings" from "we're still
   learning — this is DNA-only."** The North Star promises learning *from your own feedback*; a
   creator below threshold is being shown generic DNA ranking with no signal that personalization
   isn't active yet. This is the clearest honesty gap and it is a small additive change.

6. **The outcome signal is a 3× *weight multiplier*, not "the highest weight" — and its label is
   coarse.** Issue 13's intent ("published-clip outcome is the strongest positive label") is
   implemented as `recency_weight × 3.0` when `performed_well is True` (`preference/decay.py:26`).
   That is *not* guaranteed to dominate: a fresh downvote (weight ≈ 1.0) outweighs a 31-day-old
   outcome-positive (0.5 × 3 = 1.5 — OK) but is comparable to a 47-day-old one (0.35 × 3 ≈ 1.05).
   More importantly, `performed_well` is a **binary `views ≥ channel_median`** where the median is
   taken over **full-video `VideoMetrics.views`, not over published Shorts/clip outcomes**
   (`worker/tasks.py:1338-1363`) — a category mismatch that can make almost every Short look like
   it "underperformed" the channel's long-form view counts. The signal is real but its calibration
   is questionable and unmeasured.

**Bottom line for the moat:** the plumbing is solid and honest; the *proof* is absent. Issue 173
should land an offline-eval harness (the proof), a hardened adversarial scenario set with
ranking-aware thresholds (the gate), a personalization-status surface (the honesty fix), and a
decay/outcome calibration study (the correctness fix).

---

## 2. Evaluation plan — offline + online metrics, data, thresholds

### 2.1 Offline efficacy (the core deliverable) — "does it beat the baselines?"

**Standard.** Offline ranking evaluation uses a **chronological held-out split** (never random —
random leaks future labels; chronological mirrors deployment and avoids time-based leakage,
[Revisiting Offline Eval for Top-N, ACM TOIS](https://dl.acm.org/doi/full/10.1145/3545796);
[Shaped.ai](https://www.shaped.ai/blog/evaluating-recommender-models-offline-vs-online-evaluation)).
Report **rank-aware** metrics, not just precision/recall: **NDCG@k** (handles graded relevance),
**MAP@k** (binary relevance, top-weighted), and a **rank correlation** (Kendall τ / Spearman ρ),
which video-summarization research adopted precisely because it is more stable than F1 against
noisy human labels ([Mr.HiSum, NeurIPS 2023 D&B](https://proceedings.neurips.cc/paper_files/paper/2023/file/7f880e3a325b06e3601af1384a653038-Paper-Datasets_and_Benchmarks.pdf)).
Best practice: cover different metric *groups* rather than many from one group ([ACM TOIS](https://dl.acm.org/doi/full/10.1145/3545796)).

**Labels (relevance), strongest → weakest, per `models.py`):**
- `clip_outcomes.performed_well = True` (published + beat baseline) — strongest positive
- `ClipFeedback.action ∈ {upvote, trim}` — explicit keep (`preference/train.py:28`)
- `ClipFeedback.action = downvote` — explicit reject (negative)
- `skip` — excluded (matches training, `preference/train.py:30`); optionally a weak negative with
  IPS correction (Section 2.3) but **out of scope for v1** — log as a DECISIONS deferral.

**Three rankings to compare, on each creator's held-out feedback/outcomes:**
1. **Random** (sanity floor — any real model must beat this).
2. **Generic-signal baseline** = the cold-start `_signal_score` (`clip_engine/scoring.py:127`),
   i.e. density/hook/spike with *no DNA and no preference*. This is our honest stand-in for a
   "generic virality" ranker and is the baseline the North Star claims to beat.
3. **DNA + preference** (the production blend, `clip_engine/ranking.py:73`).

**Data we have:** `clips.signals_jsonb["features"]` (the exact feature vector, written at
`clip_engine/ranking.py:149`), `clips.dna_match`, `clips.score`, `clip_feedback`, `clip_outcomes`.
**Data we're missing:** (a) **a logged-impression / position record** — we store the final rank but
not "what order did the creator actually see, and which did they act on" with timestamps, which
counterfactual/IPS methods need ([Counterfactual eval, arXiv:2007.12719](https://arxiv.org/pdf/2007.12719));
(b) **enough labeled creators** — offline metrics per creator need ≥ ~30–50 labeled clips to be
non-noisy; most creators will be below that, so report metrics **pooled across creators** *and*
per-creator-above-N, and treat single-creator numbers as directional only.

**Pass thresholds (proposed, to confirm in Phase 2):**
- DNA+preference **NDCG@5 ≥ generic-signal NDCG@5** on the pooled held-out set, by a margin that
  clears a bootstrap 95% CI (not a point estimate — small samples are noisy).
- DNA+preference **strictly beats random** on every metric (hard floor; failure = ship-blocker).
- **No regression gate:** a new model version must not drop pooled NDCG@5 by more than a small
  ratchet vs. the last released version (mirrors the Layer-0 coverage ratchet; **mechanics [→15]**).

### 2.2 Online efficacy — when there's traffic

**Standard.** Offline metrics are necessary but "often lack accuracy and are inadequate for
selecting candidates for A/B test" ([Offline A/B for RecSys, arXiv:1801.07030](https://arxiv.org/abs/1801.07030)).
The current best-practice for *fast, small-sample* ranking comparison is **interleaving** (fewer
interactions than A/B, consistent comparisons in a shorter timespan) optionally combined with
**counterfactual/IPS** evaluation ([Airbnb 2025, arXiv:2508.00751](https://arxiv.org/abs/2508.00751)).
- **Now (pre-scale):** the offline harness + the adversarial set are the gate. Online is **not**
  actionable until there is meaningful traffic — say so honestly rather than building an A/B
  framework no one can fill.
- **Later:** **team-draft interleaving** of DNA-only vs DNA+preference, success = the creator's
  approve/keep action. Log impressions+positions now (it's the missing data above) so counterfactual
  eval is *possible* later — this is cheap insurance, propose as a small issue.

### 2.3 Calibration (honesty-adjacent)

`PreferenceScorer.predict_score` returns a probability (`preference/model.py:92`) that is blended
linearly into `score` (`clip_engine/ranking.py:73`) and ultimately mapped to a fit *tier* shown to
the user. If the probability is miscalibrated the tier is misleading. **Standard:** reliability
curve + **Brier score**, fixable with Platt/temperature scaling (search corpus: calibration via
temperature/Platt scaling for recsys). Low priority for v1 (we show tiers, not raw probabilities to
the user) but should be a metric in the dashboard so we know whether `fitTier(clip.score)` bins are
meaningful.

---

## 3. Adversarial eval-set design (the open pre-launch gate)

**Current state.** 6 fixtures (`tests/eval/scenarios/*.yaml`): `basic_retention_peak`,
`loud_aftermath`, `multi_peak_ordering`, `no_silence_boundary`, `overlapping_peaks`,
`peak_very_early`. The harness (`tests/test_clip_engine.py:204-270`) asserts `min_candidates`,
`all_setup_before_peak`, per-candidate `peak_s`/`setup_start_s` bounds, and a window-overlap cap.
**Two structural limitations:**
- It asserts **geometry only** — never that the *right* candidate ranks first, and never anything
  about scoring (Claude is not called in the harness). "Setup-not-aftermath at scale" needs more
  geometry cases; "the model is good" needs the §2 ranking harness.
- Thresholds are **spot bounds**, not an aggregate **pass-rate**. Standard video-highlight practice
  reports an aggregate (F1/mAP) over a *set*, not per-fixture asserts only
  ([SumMe/TVSum protocol](https://proceedings.neurips.cc/paper_files/paper/2023/file/7f880e3a325b06e3601af1384a653038-Paper-Datasets_and_Benchmarks.pdf)).
  Propose an aggregate "≥ X% of scenarios pass" alongside the existing hard per-fixture asserts.

**New scenarios to add (mirroring the existing YAML schema — `input.timeline.events` +
`expected`).** Each guards a named failure mode; the column "guards" maps to a principle/known bug.

| New scenario | Construction | Guards against |
|---|---|---|
| `false_peak_single_spike` | One isolated 1-sample `energy_spike`, no retention/laughter, no preceding silence | Promoting a noise spike to a clip (prominence floor at `candidates.py:167` must reject it → `min_candidates: 0`) |
| `cold_open_no_silence_lead` | Strong retention peak at 20s with **no** silence/energy before it (video opens hot) | `_find_setup_start` falling back to `peak − window` going negative / clip starting at 0 incorrectly (principle #1 hook) — assert `setup_start_s == 0` and clip still ≥ MIN_CLIP_S |
| `interrupted_setup` | silence→energy→**short silence (talk-over)**→energy→peak | Anchoring to the *inner* silence and clipping only half the setup — assert `setup_start_s` ≤ the *first* silence end |
| `very_long_setup` | Slow 90s build (energy from peak−95s) exceeding `WINDOW_S=75` (`candidates.py:18`) | Lookback cap silently truncating a legitimate long setup — assert `setup_start_s == peak − 75` (documents the cap as intentional) |
| `laughter_then_second_joke` | laugh aftermath at 60s, **second** setup+peak at 110s | NMS (`candidates.py:210`) merging two distinct beats; assert 2 candidates, both setup<peak |
| `aftermath_louder_than_setup` | extends `loud_aftermath`: retention spike *and* laughter *and* energy all post-peak, setup is quiet speech | The core differentiator under maximum adversarial pressure (principle #2) — assert `setup_start_s` ≤ setup-silence end |
| `dead_air_midclip` | long silence (>5s) inside the [setup,end] window | `silence_ratio` feature (`scoring.py:117`) correctly high; dead-air-elimination principle #5 — ranking/feature assertion in the §2 harness |
| `boundary_no_transcript` | a peak where `words=None` (snapping skipped) | Graceful degradation of principle #12 snapping (`candidates.py:235`) — invariants still hold |

**Ranking-aware scenarios (require §2 harness, mocked/fixed Claude scores):** at least one fixture
that asserts *ordering* — e.g. given two candidates where the creator's DNA clearly prefers one,
the preferred one ranks #1. Without Claude in CI this needs a **recorded/stubbed score fixture**
(never hit the live Anthropic API in CI — CLAUDE.md Testing Rules; **wiring [→15]**).

**Pass thresholds:** keep every existing per-fixture hard assert; **add** an aggregate
`scenario_pass_rate ≥ 100%` for geometry fixtures (they are deterministic, so 100% is correct) and a
separate ranking-fixture suite whose pass-rate becomes the new pre-launch gate.

---

## 4. Model-correctness findings

### 4.1 Recency decay — does it *measurably* re-weight recent feedback?

- **Standard.** Exponential time-decay is the right family; the **half-life must be tuned to the
  data**, validated by showing the decayed model beats the undecayed (and beats other half-lives) on
  a held-out chronological split, with the canonical test being a *concept-drift* scenario where an
  old preference must be overridden ([CEUR Vol-2038](https://ceur-ws.org/Vol-2038/paper1.pdf);
  [ACM TORS concept drift, 2025](https://dl.acm.org/doi/10.1145/3707693);
  [Ding & Li](https://cseweb.ucsd.edu/classes/fa17/cse291-b/reading/p485-ding.pdf)).
- **Repo reality.** Half-life is a hard-coded constant `ln(2)/30` (`preference/decay.py:11`); the
  math is unit-tested (`tests/test_preference.py:23,27,39`) but **efficacy is never measured**. The
  weight enters training as a `sample_weight` (`preference/train.py:73,97`). There is no test or
  experiment proving the "content pivot shouldn't stay anchored to 18 months ago" claim, and no
  experiment that 30d beats 60/90d on our data. It is currently **principled-by-analogy, not
  validated**.
- **Fix.** (1) Add a **decay-efficacy experiment** to the §2 harness: a synthetic/real creator with
  a labeled style pivot; assert the decayed model ranks post-pivot-aligned clips above pre-pivot
  ones, and the undecayed model does not. (2) Make the half-life a **config setting**
  (`DECAY_HALF_LIFE_DAYS`, default 30) so it is tunable from the eval, not a code edit — this is a
  **DECISIONS entry** (deviation: parameterizing a previously-hardcoded constant; record that 30d
  is the *default* pending the tuning study, and reconcile with the DNA builder's 90d half-life).

### 4.2 Cold start + honest threshold

- **Standard.** Hybrid cold-start = content-based for cold, collaborative for warm, with a *smooth*
  handoff — and **transparency about which regime is active** is part of doing it honestly
  ([cold-start practitioner guide](https://medium.com/data-scientists-handbook/cracking-the-cold-start-problem-in-recommender-systems-a-practitioners-guide-069bfda2b800);
  [Vinija's cold-start notes](https://vinija.ai/recsys/cold-start/)).
- **Repo reality — mechanics: correct & honest.** Weight 0 below threshold, linear ramp to
  `PREFERENCE_WEIGHT_CAP=0.5` at 2× threshold (`preference/model.py:139-154`, `config.py:162-166`);
  `rerank_with_preference` returns clips unchanged when weight is 0 or no model exists
  (`clip_engine/ranking.py:42-47`); well-tested (`tests/test_preference_rerank.py:22-38,58-97`).
  Broken/feature-drifted models fall back to DNA rather than scoring with garbage
  (`preference/train.py:152-158`, `preference/model.py:100`) — exemplary honest degradation.
- **Repo reality — surfacing: missing.** **The creator is never told which regime they're in.**
  `ClipOut` (`routers/clips.py:29-38`) has no personalization-status field; the UI shows the
  virality-honesty disclaimer (`frontend/src/pages/Review.tsx:45`) and a channel-fit tier
  (`fit-badge.tsx`) but nothing that says "personalization is still learning — N of 20 ratings" vs
  "ranking is personalized to your feedback." A below-threshold creator sees DNA-only ranking
  presented identically to a fully-personalized one. **This silently over-claims personalization**,
  contradicting the Honesty Constraint and the North Star.
- **Fix.** Add a personalization-status field to the clips response (e.g.
  `personalization: {active: bool, labels: int, threshold: int, weight: float}`) sourced from
  `scorer.label_count` + `preference_weight()`, and a one-line honest UI surface ("Learning your
  style — 12 / 20 ratings. Ranking is based on your channel DNA until then."). Additive,
  small, high-honesty-leverage. **DECISIONS entry** (new honesty surface + new API field).

### 4.3 Feedback → model loop integrity

- **Trace.** Vote/trim/skip → `POST` review (`routers/review.py`) → `clip_feedback` row →
  `retrain_preference.delay` (`routers/review.py:88`) → `build_and_save` reads newest-first
  `PREFERENCE_MAX_TRAINING_LABELS` rows, joins `clips` + `clip_outcomes`, builds features, fits with
  recency×outcome weights (`preference/train.py:34-128`). Published outcomes →
  `poll_clip_outcomes` Beat task sets `performed_well = views ≥ channel_median`
  (`worker/tasks.py:1363`). Per-creator isolation is enforced (`ClipFeedback.creator_id == creator_id`
  filter `preference/train.py:50`; admin session + RLS note `worker/tasks.py:362`). ✅
- **Is the outcome really the highest weight (Issue 13)?** **Partially.** It is a **3× multiplier**
  on the recency weight (`preference/decay.py:29`), not a guaranteed dominance. Math: a
  same-day downvote (≈1.0) vs a 47-day-old outcome-positive (0.35×3≈1.05) are ~equal — so "strongest
  label" holds only while the outcome is recent. Whether that's *intended* is a **DECISIONS
  question** (the CTR-signal precedent at `docs/DECISIONS.md:1833` explicitly chose *not* to use a
  signal as a training target; the 3× choice here is undocumented in DECISIONS).
- **Label-quality risk — `performed_well` calibration.** `channel_median` is computed over
  **full-video `VideoMetrics.views`** (`worker/tasks.py:1338-1344`), but the outcome being judged is
  a **published Short**. Shorts and long-form have wildly different view scales; comparing a Short's
  views to the *long-form* median can mark nearly all Shorts as `performed_well = False`, injecting a
  systematic negative bias into the strongest-weighted label. **This is a real, unmeasured
  correctness risk** and likely an **OFF_COURSE_BUGS / DECISIONS item** — the median should be over
  comparable units (published Shorts, or format-matched).
- **Leakage / imbalance / staleness:**
  - **Leakage:** addressed for `dna_match` (the composite-score collinearity fix, Issue 103 #5,
    `docs/DECISIONS.md:2114`, `scoring.py:282`). But the §2 *offline* harness must be careful not to
    train and evaluate on the same clips — enforce the chronological split there.
  - **Imbalance:** `LogisticRegression(class_weight="balanced")` handles cold-start imbalance
    (`preference/model.py:175`); LightGBM (warm path) does **not** set `class_weight`/`is_unbalance`
    (`preference/model.py:181`) — if a creator's feedback is heavily positive-skewed the warm model
    can degrade. Worth a metric in the dashboard and possibly `is_unbalance=True`.
  - **Staleness:** retrain is event-driven on feedback (`routers/review.py:88`) with a self-debounce
    (`worker/tasks.py:395`), but a *new outcome* arriving via the Beat task does **not** trigger a
    retrain — so the strongest label can sit unincorporated until the next vote. Worth confirming
    intended; if not, trigger retrain on outcome finalization too.

### 4.4 Setup-not-aftermath robustness (geometry)

The core mechanic is well-implemented (`candidates.py:103` `_find_setup_start` backward scan;
principle #2/#12) and the `loud_aftermath` fixture proves the headline case. The Section-3 expansion
is what hardens it "at scale." No correctness bug found in the backward-scan logic on read; the
`WINDOW_S=75` cap (`candidates.py:18`) is the one behavior that silently truncates long setups and
deserves an explicit fixture documenting it as intentional.

---

## 5. Proposed issues (dependency-ordered, `docs/issues.md` house style)

> Numbering continues the queue (latest is in the 170s). Confirm exact numbers at filing.

### Issue 173a — Offline clip-ranking efficacy harness (NDCG/MAP/Kendall vs random + generic baseline)
**What.** A read-only, DB-backed offline eval harness (`tests/eval/` + a runnable script) that, per
creator with ≥ N labeled clips and pooled across creators, computes NDCG@5, MAP@5, and Kendall τ for
three rankings — random, generic-signal (`_signal_score`), DNA+preference — on a **chronological**
held-out split of `clip_feedback` + `clip_outcomes`. Outputs a metrics table; no product-code change.
**Acceptance criteria.**
- [ ] Chronological split (no random split); no clip appears in both train and eval.
- [ ] Reports NDCG@5, MAP@5, Kendall τ for all three rankings, pooled + per-creator-above-N, with
      bootstrap 95% CIs.
- [ ] DNA+preference strictly beats random on every metric (asserted); beats generic-signal on
      pooled NDCG@5 by a CI-clearing margin (reported, gate threshold confirmed in Phase 2).
- [ ] Uses real Postgres fixtures (no DB mocking); never calls live Anthropic/YouTube.
- [ ] **DECISIONS entry:** metric set, k, the held-out split definition, and `skip`-label exclusion.

### Issue 173b — Adversarial clip-quality scenario expansion + aggregate pass-rate
**What.** Add the 8 geometry scenarios in Section 3 (plus ≥1 ranking-aware fixture using recorded
scores) to `tests/eval/scenarios/`; extend the harness with an aggregate `scenario_pass_rate` and
the `very_long_setup` cap-documentation assert. Closes the real content of the "eval harness
hardened" pre-launch gate. **Depends on 173a** for the ranking-fixture scoring path.
**Acceptance criteria.**
- [ ] 8 new geometry fixtures added; each asserts its named failure mode (Section 3 table).
- [ ] ≥1 ranking-aware fixture asserts the DNA-preferred candidate ranks #1 (recorded scores).
- [ ] Aggregate geometry pass-rate asserted at 100%; ranking suite pass-rate becomes the gate.
- [ ] `CLAUDE.md:273` / Pre-Public-Launch list reconciled with `PROJECT_STATE.md:1176` (flag both).
- [ ] **No DECISIONS entry needed** unless a fixture reveals an intended behavior change.

### Issue 173c — Honest personalization-status surface (API field + UI line)
**What.** Add `personalization: {active, labels, threshold, weight}` to the clips response
(`routers/clips.py` `ClipOut`), sourced from `scorer.label_count` + `preference_weight()`, and a
one-line UI surface in Review distinguishing "still learning (N/threshold)" from "personalized."
**Acceptance criteria.**
- [ ] `ClipOut` carries personalization status; below threshold → `active: false` + honest copy.
- [ ] UI shows learning progress below threshold; "personalized to your feedback" above it.
- [ ] No virality language; structural no-virality test stays green.
- [ ] Test: below-threshold response says not-yet-personalized; above-threshold says personalized.
- [ ] **DECISIONS entry:** new honesty surface + new API field (extends the Honesty Constraint).

### Issue 173d — Recency-decay calibration study + parameterize half-life
**What.** Use the 173a harness to compare half-lives {15, 30, 60, 90} on held-out data + a
concept-pivot scenario; move the constant to `DECAY_HALF_LIFE_DAYS` config (default 30).
**Depends on 173a.**
**Acceptance criteria.**
- [ ] Decayed model beats undecayed on the pivot scenario (post-pivot clips rank higher); reported.
- [ ] Best half-life on our data reported with CIs; default updated only if it clears the incumbent.
- [ ] `_LAMBDA` derived from `DECAY_HALF_LIFE_DAYS` in `config.py` (+ `.env.example`).
- [ ] **DECISIONS entry:** parameterizing the constant + the chosen default + DNA-vs-feedback
      half-life rationale (90d vs 30d).

### Issue 173e — `performed_well` baseline-unit fix (Shorts vs long-form median)
**What.** Compute the outcome baseline over **comparable units** (published Shorts / format-matched),
not the full-video `VideoMetrics.views` median, so the strongest-weighted label isn't systematically
negative. Re-examine the 3× outcome multiplier vs "strongest label" (Issue 13). **Depends on 173a**
to measure the before/after impact.
**Acceptance criteria.**
- [ ] Baseline median computed over comparable-format published outcomes (define the unit).
- [ ] 173a shows the label-bias before/after (fraction `performed_well=True` becomes plausible).
- [ ] Decision on whether outcome must *dominate* (vs 3× multiplier) recorded.
- [ ] **DECISIONS entry:** the baseline-unit change + the multiplier-vs-dominance resolution.
- [ ] **Log in `OFF_COURSE_BUGS.md`** first if discovered outside an active issue.

### Issue 173f — Continuous eval: impression/position logging + standing metric report
**What.** Log each clip *impression with rank+timestamp* (the missing data for counterfactual eval),
and emit the 173a pooled metrics on each retrain so regressions surface. **CI gating mechanics and
dashboard plumbing coordinate with prompt 15/Issue 180 [→15].** **Depends on 173a.**
**Acceptance criteria.**
- [ ] Impression log captures (clip_id, rank, shown_at) per creator, isolation-safe.
- [ ] Pooled NDCG@5 recomputed + recorded per release; ratchet defined (mechanics [→15]).
- [ ] No PII/token in any logged line; per-creator isolation on every query.
- [ ] **DECISIONS entry:** the new impression-log schema + retention posture (ToS/privacy).

---

## 6. Open questions for the human (one-line answers)

1. **Baseline unit for `performed_well`:** compare a Short's views to other *Shorts'* median, or to
   format-matched (Shorts-vs-Shorts, longs-vs-longs)? (Affects 173e.)
2. **Outcome dominance:** should a published-clip outcome *always* outweigh any explicit vote (true
   "highest weight"), or is the recency-aware 3× multiplier the intended behavior? (Issue 13 intent.)
3. **Half-life default:** OK to parameterize `DECAY_HALF_LIFE_DAYS` (default 30, tunable), or keep it
   a fixed constant pending the study? (Affects 173d.)
4. **`min N` for per-creator offline metrics:** what labeled-clip count makes a per-creator number
   trustworthy enough to show vs. pool-only — 30? 50? (Affects 173a thresholds.)
5. **Pre-launch gate strictness:** is "DNA+preference beats generic-signal on pooled NDCG@5 with a
   CI-clearing margin" the right ship-blocker, or beat-random-only for v1 with the stronger gate
   deferred? (Affects 173a/173b gate.)
6. **`skip` as a weak negative:** include skips (with IPS correction) or keep excluding them as
   training does today? (Affects 173a label set.)

---

### Doc-staleness / contradiction flags (raised, not papered over)

- `docs/PROJECT_STATE.md:1176` marks "Eval harness hardened with adversarial/edge cases" **done**;
  `CLAUDE.md:273` + Pre-Public-Launch list mark it **open**. Reconcile (Issue 173b).
- The **3× outcome multiplier** (`preference/decay.py:29`) implements Issue 13's "strongest label"
  but is **not recorded in `docs/DECISIONS.md`** (unlike the adjacent CTR-signal decision at
  `docs/DECISIONS.md:1833`). Document it.
- No `docs/DECISIONS.md` entry justifies the **30-day half-life** as validated — it is asserted in a
  code docstring only (`preference/decay.py:5`). Either validate (173d) or record as a default.
