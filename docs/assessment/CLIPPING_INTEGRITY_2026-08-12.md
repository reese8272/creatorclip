# Clipping-Integrity Audit — 2026-08-12

**Date:** 2026-08-12 · **Commit:** `41012fc7a8ced375fde0a4ac8a6fbf37f8b77672` (branch `docs/close-out-455`, clean tree)
**Tests at audit time:** default lane **2975 passed / 0 failed / 64 skipped** (60.6 s) · eval harness **61 passed** (24 scenarios, 100 %) · Layer 0 all runnable gates green (clip_engine 93.03 % vs 91.0 floor, preference 90.24 vs 88.0)
**Method:** three-agent exploration dossier → deterministic evidence capture → 12-agent workflow (1 known-issues ledger, 4 confirmers over ~49 pre-flagged items, 4 hunters for new defects, 3 adversarial verifiers) → main-loop adjudication of every SEV1 (all five repros/traces re-run or re-read first-hand). Scope: the four user-selected dimensions below. Assessment only — no production code changed by this audit.

---

## ⏰ TIME-CRITICAL (independent of this audit)

- **Issue 448's live re-render verification (ranks 3/13 with `OVERLAY_BAND_DETECT_ENABLED=true`) becomes permanently unverifiable at 2026-08-13 19:23 UTC** when the source video purges. Note: Issue 466 (filed below) shows the overlay-band sampler is broken for any source > ~500 s, so that drill would likely mis-mask anyway — but the deadline stands for whatever drill is attempted.
- After that purge, `tests/fixtures/reframe_seats/` (12 real frames, referenced by **zero** tests) is the only surviving reproduction of Issue 450. Wiring them into a test is Issue 478.

---

## Per-dimension integrity verdicts

| Dimension | Verdict | Basis |
|---|---|---|
| 1. Clip selection core | **SOUND-WITH-CAVEATS** | The geometry invariant chain (setup ≤ peak−0.1, 30 s min, 90 s clamp, duration clamp) is real, coded consistently in three places, and holds under repro. But one SEV1 (456) lets a run-on utterance drag the start so far back that the **peak leaves the clip** — and the Deepgram no-utterance fallback collapses an entire video to one [0, 90] clip. The signal composite is biased toward the loud **aftermath** (457: laughter+energy double-count on the same samples), and the scorer accepts model output unvalidated (461). |
| 2. Mechanical pipeline | **SOUND-WITH-CAVEATS** | Boundary-time handling from transcript to `-ss`/`-t` is careful and mostly verified by repro. But the overlay-band sampler is broken on **every source > ~500 s** — lexicographically scrambled frames past index 999 plus truncation time-dilation (466, SEV1) — and the shipped Punch-in toggle produces an ffmpeg filter that cannot initialize, bricking every render while enabled (467, SEV1, repro rc=234). Dual duration authorities (469) can permanently fail clips; trim/clean leaves stale geometry (470). |
| 3. Learning loop | **SOUND-WITH-CAVEATS** | One-label-per-clip (Issue 444) is coherent, deliberately designed, and integration-proven on PRs. But the always-visible Skip button after "Save trim" **silently erases the creator's keep label** from the training set while the pile keeps the verdict (472, SEV1) — violating the invariant the code's own docstring calls structurally impossible. Debounce watermark misses retractions (473); the personalization threshold reports `active=true` at weight 0.0 (474). |
| 4. Eval/test integrity | **COMPROMISED** | The green dashboard materially overstates what is proven. The LLM scorer that decides which clips ship is evaluated **nowhere** (476, SEV1). Two of the 24 public "adversarial scenarios" assert nothing (477). No test anywhere verifies a rendered clip's boundaries against the requested window; the declared real-media lane (`render-env`) contains zero tests (478). The per-module coverage floors and the changed-line patch gate have **never run in CI** — confirmed in today's live CI log (479). |

**Overall:** the deterministic engine is in substantially better shape than its verification story. Most pre-flagged geometry hazards turned out to be guarded (see refutations in the disposition table) — but the guards are proven by hand-authored synthetic fixtures, the LLM that makes the final call is untested, and two of the audit's five SEV1s live in code paths the suite exercises only through mocks.

---

## Deterministic baseline (Phase 1, captured before any agent ran)

| Check | Result |
|---|---|
| `pytest -q -rs` (default lane) | 2975 passed, 0 failed, 64 skipped, 178 deselected, 60.6 s |
| Skip census | Zero cv2 / libgomp / opencv skips — render, camera-region, and efficacy suites genuinely executed locally. Skips are Issue-226 legacy-page retirements + Issue-275 staging-pending + 1 RSS-bound test |
| Eval harness (`tests/test_clip_engine.py -v`) | 61 passed, 1.78 s. All 24 scenarios green — including the two shown below to pass vacuously |
| Scenario census | 24 files + 1 in `scenarios/ranking/` (excluded by non-recursive glob). `SCENARIO_FLOOR = 23` in code — CLAUDE.md's "21" is stale (Issue 482) |
| `render-env` lane | Marker registered in pytest.ini, **zero tests carry it** — 3217 deselected, 0 selected |
| Layer 0 (no `--update-baseline`) | ruff 0 · mypy 0 · coverage 84.18 % · clip_engine 93.03 (floor 91) · preference 90.24 (floor 88) · bandit 0/0 · pip-audit 0 · diff-cover skipped (not installed locally) |
| `-m integration` lane | **Not runnable here** (no Docker/Postgres). Runs in PR CI — that lane is where the triage-SQL dedup proof lives |

---

## Findings register (severity-ranked, filed as Lane L29, Issues 456–482)

### SEV1 — all five personally adjudicated (repro re-run or full code trace by the orchestrator)

| # | Finding | Adjudication evidence |
|---|---|---|
| **456** | `snap_start`'s absolute never-mid-sentence rule moves a start backward **without distance bound**; the 90 s-clamp payoff guard assumes ≤10 s of motion, so a ≥15 s run-on utterance ships a clip whose **own peak lies outside the window**. Degenerate case: Deepgram no-utterance fallback → whole video becomes one unterminated sentence → every candidate collapses to [0, 90], containment leaves ONE clip per video. | Repro re-run: `[setup 225, peak 300, end 320]` → shipped `[204.7, 294.7]`, peak excluded; repro 7 → all candidates `[0, 90]`, one survivor |
| **466** | Overlay-band (superchat) detection is broken on every source > ~500 s: `_sample_by_seeking` names frames `f{i:03d}.png` and reads them back `sorted(glob)` — lexicographic, so f1000 sorts between f100 and f101, scrambling temporal order past 999 samples; `step = duration/len(stack)` assumes a complete stack while the sampler silently drops failed frames and truncates at the 240 s budget, dilating every span time and never scanning the tail. The 1617 s drill video needs 3234 samples in a 240 s budget. | Repro re-run: `sorted()` puts f1000 at index 101; code read of `camera_region.py:568,619` + `overlay_bands.py:174,214` confirms all three legs |
| **467** | `_punch_in_filter` emits a time-dependent expression in crop `w`/`h`, which ffmpeg evaluates **once at filter-config time where t = NaN** → filter init error. Enabling the shipped "Punch-in at peak" toggle (UI + brand kit, merged onto every render) makes **every engine-clip render fail** with a generic "Render failed." Feature has never been able to work; only argv-string tests exist. | Repro re-run: production-shaped chain through ffmpeg 8.1.2 → rc=234, "Conversion failed!" |
| **472** | `POST /feedback action=skip` inserts a feedback row that **wins the training partition and erases the prior label** (trim/upvote), while `clip.triage` stays `kept` — the exact pile/model divergence the Issue-444 docstring declares structurally impossible. The shipped UI makes it the natural flow: "Save trim" deliberately does not advance; the always-visible Skip button is the obvious next click. | Full code trace re-read: `models.py:185-191` (skip absent from map) + `review.py:242-244` (triage unchanged, row still inserted) + `train.py:41,126` (skip wins partition, drops at filter) + `YourCall.tsx:132,204` |
| **476** | `score_candidates` — the LLM call that decides which clips ship and what principle they cite — is evaluated **end-to-end nowhere**: every test patches `_ANTHROPIC`, no eval scenario invokes it, the nightly live-LLM lane excludes it, mutmut (the only thing that touches it) is weekly and non-gating. A prompt/model change that systematically prefers aftermath windows would pass 100 % of every gate. | All four legs verified independently by confirmer + verifier (grep for recorded-HTTP fixtures: zero; nightly workflow test list read; mutation.yml `\|\| true`) |

### SEV2 (filed individually; full bodies in `docs/issues.md` Lane L29)

| # | Finding |
|---|---|
| 457 | Laughter/energy double-count: one loud reaction can satisfy both detectors and stack 3.5× weight on the same samples — biasing peak detection toward the aftermath the engine exists to avoid |
| 458 | A silence-only timeline fabricates candidates: the flat region between two silences is a scipy peak (repro: 2 phantom clips from silence + nothing) |
| 459 | `find_peaks` cannot see a peak in the first/last signal sample — a retention spike at video start/end (weight 3.0, the ground-truth signal) is invisible |
| 460 | Setup fallback takes the **earliest** energy spike in the 75 s window (docstring says "nearest") — systematically over-long setups when silence is absent |
| 461 | `score_candidates` response hardening: model-emitted `principle` written with no registry check; unguarded `float()` can fail the whole generation task; a string `"index"` value silently discards every LLM score |
| 462 | `[BEFORE]` transcript context truncates from the wrong end — keeps text ~60 s away, drops the sentence adjacent to the cut (the one that matters for setup judgment) |
| 463 | `video_context` prompt-security: creator-authored identity text in a SYSTEM block (dna/brief.py documents the opposite rule for the same data, Issue 224); model-authored summary re-used content-unwrapped downstream (second-order injection surface) |
| 464 | Principle 10 "Native length": `optimal_clip_len_s` is computed, stored, and surfaced — and read by **no** clip-geometry code; every creator gets the fixed 30/90 band |
| 465 | `rerank_with_preference` overwrites the persisted `score` with the blended value — downstream consumers (generate-more recap) read it as the DNA/LLM fit score; appended clips skip the blend, mixing blended and raw ranks in one video |
| 468 | `POST /clips/{id}/render` lacks the pending-clean/edit 409 guard its three sibling endpoints have — re-rendering mid-edit races the artifact swap |
| 469 | Two duration authorities: sentence-snap clamps to librosa 16 kHz audio duration (capped 14400 s); render hard-rejects against ffprobe container duration with a TERMINAL no-retry ValueError — any mismatch = permanently failed clip |
| 470 | Trim/clean never updates `clip.start_s/end_s/setup_start_s` — after `/clean/confirm`, captions/crops/duration/transcript all compute against pre-trim geometry |
| 471 | Right-to-erasure completeness beyond the known `clips/` gap (Issue 446): GDPR exports, extracted-audio WAVs, and recap artifacts survive account deletion |
| 473 | Retrain debounce watermark counts only TRAINABLE rows — pure skip retractions and `performed_well` outcome arrivals never trigger a retrain |
| 474 | Personalization honesty at the threshold: `active=true` reported with weight 0.0 at exactly T labels; label count is computed on a different basis (pre-dedup) than training uses (post-dedup) |
| 475 | Efficacy harness diverges from production: eval-set `performed_well=True` overrides skip/format retractions the trainer honors; Proof-of-Lift computes durations from `start_s`, not the `setup_start_s` origin every other surface uses |
| 477 | Eval-runner assertion integrity: `injection_in_transcript.yaml` expectations read by no code (passes vacuously); `false_peak_single_spike.yaml` asserts nothing (`min_candidates: 0`); no upper-bound-on-count assertion exists; expected-candidate matching is by nearest peak (two expectations can match one candidate; spurious extras never rejected); the core setup-before-peak invariant is opt-in and 10 of 15 geometry scenarios don't opt in; `SCENARIO_FLOOR` is enforced only for deletions, not lowering |
| 478 | Real-media verification lane: `render-env` marker registered, zero tests carry it; no test ffprobes a rendered clip's duration/PTS vs the requested window; `reframe_seats` real-frame fixtures orphaned (only surviving Issue-450 repro after the purge); nothing guarantees the ffmpeg-gated tests execute in CI |
| 479 | Gates that never ran: `run_layer0.py` deletes `_coverage.xml` at the end of **every** invocation while ci.yml runs coverage and module-floors as **two** invocations — per-module floors and diff-cover have been permanently "skipped" in CI (confirmed in today's live CI log) while printing "All runnable gates passed"; `ci_local.sh` pre-push gate additionally requires Postgres unnecessarily and the hook isn't installed |
| 480 | Preference-eval gap: the "ranking reflects DNA" fixture proves only sort-by-score (`rank_candidates` never reads `dna_match`); `rerank_with_preference` has no eval with a trained model |
| 481 | Transcription timing fidelity untested: normalizer-shape tests only; every eval supplies hand-typed timings — a systematic Deepgram offset shifts every clip boundary invisibly |

### CLEANUP roll-up (Issue 482) + report-register-only items

**Issue 482 (doc↔code accuracy sweep):** "rolling 60–90 s window" language vs fixed `WINDOW_S=75.0`; `docs/PIPELINE.md` omits `analyze_video_context` and line refs drift 65–855 lines (measured — dossier's "1500+" was wrong); CLAUDE.md `SCENARIO_FLOOR=21` vs code 23; `models.py` Clip.triage comment contradicts shipped 444 behavior; no test parses `docs/CLIPPING_PRINCIPLES.md` against the four code copies of the registry.

**Register only (verified real, below filing threshold — fix opportunistically):**
silence events contribute unscaled −0.5 while other events value-scale (C1-04) · retention spikes have a 2-sample footprint vs 8+ for energy runs (C1-05, corrected from "single-sample") · NMS threshold + IoU math duplicated across candidates/merge (C1-08) · invariant-repair tail coded 3× (C1-09) · `silence_ratio` computed but unused in cold-start score — dead-air enters only via the −0.5 composite weight (C2-03, "blind" refuted) · hook principle says 3 s, `hook_energy` measures 5 s (C2-05) · recap segments can carry `principle: ""` while the eval asserts registry names (C2-09) · stale Sonnet-floor comments in scoring.py (C2-11) · cold-start weights/normalizers are magic numbers (C2-12) · efficacy re-implements the blend formula by hand (C2-13) · Deepgram no-utterance fallback degrades silently but diarization does NOT collapse (C3-04, corrected) · camera-region version-bump invalidates failure markers but not stored rects — read-time version check covers it (C3-07, corrected) · permanent render failures DO emit a structured log + metric (C3-08, corrected) · eval CI job posts a success-shaped "Skipped" status when paths don't match (C4-06) · cv2/libgomp silent-skip risk is CI-robustness only — everything ran locally, and the concept-pivot gate mechanically cannot OSError-skip (C4-12, corrected) · eval exercises pool size 8, production uses 12 (C4-15) · equal-prominence tie-break is reversed — later peaks win the pool cut · the 90 s ceiling doesn't exist on the no-transcript path (95 s clips possible) · the 3× `performed_well` multiplier also amplifies **negative** labels on well-performing clips · re-ingest can silently replace an existing camera region · quarantine/`not_llm_live` markers are registered but no lane runs them · `analysis/` package: bandit never scans it and it has no coverage floor (mypy leg refuted — follow_imports covers it) · **recap selection ignores triage — clips the creator explicitly skipped are stitched into their recap (OWNER DECISION needed: filed nowhere, needs a product ruling before it's a bug)**

---

## Pre-flag disposition summary

Full 88-row table below. Headline splits:

- **Filed as new issues:** 27 (5 SEV1, 21 SEV2, 1 CLEANUP roll-up) — Lane L29, Issues 456–482
- **Already tracked:** 6 — clean/confirm R2 orphan (OFF_COURSE 2026-08-10), overlay flag inert (Issue 448's own open box), sendcmd-format SEV2 (assessment carry-forward, line refs now stale + flag-gated framing wrong since reframe is LIVE), floor-doc staleness, CI no-push-trigger (OFF_COURSE + memory)
- **Deliberate (DECISIONS-accepted, not re-litigated):** 6 — word-level snap tripwire (2026-08-05 r.1), NMS-before-snap ordering (2026-08-07), containment threshold inertness (2026-08-07 D.1), skip-reason taxonomy (Issue 217), mutmut 3-module scope (Issue 273), style-notes third-block placement (Issue 371)
- **Refuted:** 2 fully (C1-10 wrongful-drop is unreachable defensive code; C1-11 float noise is mathematically impossible — a 0.5-grid peak −0.1 is already 2 dp-exact), plus material corrections on 10 PARTIALs (diarization does not collapse; PIPELINE drift 65–855 lines not 1500+; pool-8-vs-12 does not affect skip reasons; permanent render failures are logged; the concept-pivot gate cannot silently skip; mypy does cover analysis/ via follow_imports)

The refutations matter: they are what filing 27 issues instead of 49 buys. Every filed issue survived an adversarial verifier whose charter was to kill it, plus main-loop adjudication of all five SEV1s.

### Full disposition table (audit trail)

| Item | Verdict | Class | Sev | Outcome |
|---|---|---|---|---|
| C1-01 | CONFIRMED | new | CLEANUP | **filed as Issue 482** |
| C1-02 | CONFIRMED | new | SEV2 | **filed as Issue 460** |
| C1-03 | CONFIRMED | new | SEV2 | **filed as Issue 457** |
| C1-04 | CONFIRMED | new | CLEANUP | report register only |
| C1-05 | CONFIRMED | new | CLEANUP | report register only |
| C1-06 | CONFIRMED | deliberate | CLEANUP | DECISIONS-accepted — docs/DECISIONS.md 2026-08-07 Issue 441 entry ('Root cause worth record |
| C1-07 | CONFIRMED | deliberate | NONE | DECISIONS-accepted — docs/DECISIONS.md:712-720 (2026-08-05 wave ruling 1): 'extract_candida |
| C1-08 | CONFIRMED | new | CLEANUP | report register only |
| C1-09 | CONFIRMED | new | CLEANUP | report register only |
| C1-10 | REFUTED | n/a | NONE | refuted — not filed |
| C1-12 | CONFIRMED | n/a | NONE | report register only |
| C1-13 | CONFIRMED | deliberate | NONE | DECISIONS-accepted — docs/DECISIONS.md 2026-08-07 Issue 441 Decision 1 ('_MAX_OVERLAP_S = 3 |
| C2-01 | CONFIRMED | new | SEV2 | **filed as Issue 461** |
| C2-02 | CONFIRMED | new | SEV2 | **filed as Issue 461** |
| C2-03 | CONFIRMED | new | CLEANUP | report register only |
| C2-04 | CONFIRMED | new | SEV2 | **filed as Issue 464** |
| C2-05 | CONFIRMED | new | CLEANUP | report register only |
| C2-06 | CONFIRMED | new | CLEANUP | **filed as Issue 482** |
| C2-07 | CONFIRMED | new | SEV2 | **filed as Issue 465** |
| C2-08 | CONFIRMED | new | CLEANUP | report register only |
| C2-09 | CONFIRMED | new | CLEANUP | report register only |
| C2-10 | CONFIRMED | new | SEV2 | **filed as Issue 463** |
| C2-11 | CONFIRMED | new | CLEANUP | report register only |
| C2-12 | CONFIRMED | new | CLEANUP | report register only |
| C2-13 | CONFIRMED | new | CLEANUP | report register only |
| C3-01 | CONFIRMED | new | SEV2 | **filed as Issue 469** |
| C3-02 | CONFIRMED | new | SEV2 | **filed as Issue 470** |
| C3-03 | CONFIRMED | tracked | SEV2 | already tracked — docs/OFF_COURSE_BUGS.md:143 (2026-08-10 row, status Open) — clean/conf |
| C3-04 | CONFIRMED | new | CLEANUP | report register only |
| C3-05 | CONFIRMED | deliberate | CLEANUP | DECISIONS-accepted — docs/DECISIONS.md:3423-3436 (2026-06-23 Issue 217): 'all_candidates_su |
| C3-06 | CONFIRMED | tracked | SEV2 | already tracked — docs/issues.md:2802 (flag default false) + :2819-2820 (owed live re-re |
| C3-07 | CONFIRMED | new | CLEANUP | report register only |
| C3-08 | CONFIRMED | new | CLEANUP | report register only |
| C3-09 | CONFIRMED | new | CLEANUP | **filed as Issue 482** |
| C3-10 | CONFIRMED | tracked | CLEANUP | already tracked — docs/OFF_COURSE_BUGS.md:141 (2026-08-10 ci-no-push-trigger row, SEV3); |
| C4-01 | CONFIRMED | new | SEV1 | **filed as Issue 476** |
| C4-02 | CONFIRMED | new | SEV2 | **filed as Issue 477** |
| C4-03 | CONFIRMED | new | SEV2 | **filed as Issue 477** |
| C4-04 | CONFIRMED | new | SEV2 | **filed as Issue 478** |
| C4-05 | CONFIRMED | new | SEV2 | **filed as Issue 478** |
| C4-06 | CONFIRMED | new | CLEANUP | report register only |
| C4-07 | CONFIRMED | new | SEV2 | **filed as Issue 477** |
| C4-08 | CONFIRMED | new | SEV2 | **filed as Issue 480** |
| C4-09 | CONFIRMED | new | SEV2 | **filed as Issue 481** |
| C4-10 | CONFIRMED | new | SEV2 | **filed as Issue 480** |
| C4-11 | CONFIRMED | tracked | SEV2 | already tracked — docs/assessment/modules/clip_engine.md:67-73 (SEV2 carry-forward); led |
| C4-12 | CONFIRMED | new | CLEANUP | report register only |
| C4-13 | CONFIRMED | deliberate | CLEANUP | DECISIONS-accepted — docs/DECISIONS.md:2458-2515 (Issue 273): mutation scope is EXACTLY thr |
| C4-14 | CONFIRMED | tracked | CLEANUP | already tracked — Ledger entry [ASSESS clip_engine doc-scenario-floor-stale] (stale-trac |
| C4-15 | CONFIRMED | new | CLEANUP | report register only |
| CI per-module coverage floors (clip_engine 91%) and diff-cover patch gate have NEVER run i | CONFIRMED | new | SEV2 | **filed as Issue 479** |
| Distilled style notes (LLM output derived from creator free-text feedback) are injected in | CONFIRMED | deliberate | SEV2 | DECISIONS-accepted — docs/DECISIONS.md:12574 — Issue 371 ruling (1), dated 2026-07-30: 'Inj |
| Efficacy harness eval set diverges from the training set it claims to mirror: performed_we | CONFIRMED | new | SEV2 | **filed as Issue 475** |
| Equal-prominence tie-break is reversed: np.argsort(...)[::-1] treats LATER peaks as strong | CONFIRMED | new | CLEANUP | report register only |
| Geometry eval branch: the core setup-before-peak invariant is opt-in per scenario and 10 o | CONFIRMED | new | CLEANUP | **filed as Issue 477** |
| INCIDENTAL: Dead OSError skip-guards in efficacy tests mask which test can actually skip | CONFIRMED | new | CLEANUP | dup of C4-12 |
| INCIDENTAL: Model-emitted string index silently discards all LLM scores | CONFIRMED | new | SEV2 | **filed as Issue 461** |
| INCIDENTAL: Re-ingest can silently REPLACE an existing camera region | CONFIRMED | new | CLEANUP | report register only |
| INCIDENTAL: SCENARIO_FLOOR ratchet comment omits the 21→23 raise | CONFIRMED | new | CLEANUP | **filed as Issue 482** |
| INCIDENTAL: clean_confirm docstring asserts an R2 lifecycle that does not exist | CONFIRMED | tracked | CLEANUP | dup of C3-03 |
| INCIDENTAL: injection_in_transcript.yaml description affirmatively false, not just aspirat | CONFIRMED | new | CLEANUP | dup of C4-02 |
| INCIDENTAL: sentence_snap 90s-clamp guard can end a clip BEFORE its peak after a long run- | CONFIRMED | new | SEV2 | **filed as Issue 456** |
| INCIDENTAL: test_signals.py has a real-ffmpeg test outside the render-env marker too | CONFIRMED | n/a | NONE | dup of [25] No gate guarantees the real-ffmpeg  |
| Issue 448 overlay-band code: real-frame tests cover only the pure functions — flag-gated i | CONFIRMED | new | SEV2 | **filed as Issue 466** |
| No gate guarantees the real-ffmpeg tests ever execute: render-env lane is empty, CI's ffmp | CONFIRMED | new | SEV2 | **filed as Issue 478** |
| No test enforces doc<->code consistency for the 12-principle registry — 5 code copies, onl | CONFIRMED | new | CLEANUP | **filed as Issue 482** |
| Overlay-band (superchat) span timing is broken on every long source: >999 samples are read | CONFIRMED | new | SEV1 | **filed as Issue 466** |
| POST /clips/{id}/render lacks the pending-clean/edit 409 guard its three sibling endpoints | CONFIRMED | new | SEV2 | **filed as Issue 468** |
| POST /feedback action=skip silently erases the creator's keep/drop training label while th | CONFIRMED | new | SEV1 | **filed as Issue 472** |
| Personalization honesty broken at and above the threshold: active=true is reported with we | CONFIRMED | new | SEV2 | **filed as Issue 474** |
| Proof-of-Lift panel computes duration_s and setup_lead_s from start_s, not the setup_start | CONFIRMED | new | SEV2 | **filed as Issue 475** |
| Punch-in (zoom_on_peak) filter is invalid ffmpeg — enabling the shipped 'Punch-in at peak' | CONFIRMED | new | SEV1 | **filed as Issue 467** |
| Quarantine marker policy is not implemented (no lane runs quarantined tests) and the not_l | CONFIRMED | new | CLEANUP | report register only |
| Recap selection ignores triage: clips the creator explicitly rejected (triage=skip) are st | CONFIRMED | new | CLEANUP | report register only |
| Retrain debounce watermark only counts TRAINABLE feedback rows — pure skip retractions and | CONFIRMED | new | SEV2 | **filed as Issue 473** |
| Right-to-erasure and lifecycle gaps beyond the known clips/ one: full GDPR data exports, e | CONFIRMED | new | SEV2 | **filed as Issue 471** |
| SCENARIO_FLOOR ratchet is comment-enforced only — nothing would catch it being lowered | CONFIRMED | new | CLEANUP | **filed as Issue 477** |
| Scoring transcript context truncates from the WRONG end: [BEFORE] keeps text ~60s away and | CONFIRMED | new | SEV2 | **filed as Issue 462** |
| Second-order prompt injection: video_context's model-authored summary (derived from the un | CONFIRMED | new | SEV2 | **filed as Issue 463** |
| Silence events alone fabricate clip candidates: the flat region between any two silences i | CONFIRMED | new | SEV2 | **filed as Issue 458** |
| The 3x performed_well outcome multiplier amplifies NEGATIVE labels too: a downvoted clip t | CONFIRMED | new | CLEANUP | report register only |
| The 90s 'hard ceiling for ALL candidates' does not exist on the no-transcript path: 95s cl | CONFIRMED | new | CLEANUP | report register only |
| Unbounded backward sentence-snap defeats the 90s-clamp peak guard: clips ship with their o | CONFIRMED | new | SEV1 | **filed as Issue 456** |
| analysis/ package (video_context.py — the LLM moment proposer in the clip chain) is invisi | CONFIRMED | new | CLEANUP | report register only |
| ci_local.sh Layer-1 pre-push gate cannot run any tests on the dev box: unnecessary Postgre | CONFIRMED | new | SEV2 | **filed as Issue 479** |
| find_peaks boundary blindness: a retention spike (weight 3.0, the ground-truth signal) in  | CONFIRMED | new | SEV2 | **filed as Issue 459** |
| models.py Clip.triage comment contradicts shipped Issue-444 behavior: claims 'setting tria | CONFIRMED | new | CLEANUP | **filed as Issue 482** |
| C1-11 | REFUTED (Stage A) | n/a | NONE | refuted — The mechanism exists in code (min after round, no re-round) but produces no obse |


---

## Limitations of this audit

1. **The `-m integration` lane did not run here** (no Docker/Postgres on this box). It runs on every PR in CI; the triage-SQL dedup semantics (G9) are proven there and were not re-proven locally.
2. **No live-production verification.** The owed drills remain owed: 448 re-render (expires 2026-08-13 19:23 UTC), 444 idempotency drill, 437 failure-path drill, 427 frame check, 424–426 Playwright baselines. Prod-VM `.env` claims are doc-sourced (`docs/LEFT_OFF.md`), not read from the box.
3. **The LLM scorer's judgment was not evaluated by this audit either** — Issue 476 is simultaneously a finding and a limitation: no harness exists to run it against, and building one was out of scope.
4. Severities are the adversarial verifiers' calls, spot-checked (SEV1s fully re-adjudicated; two verifier downgrades accepted: coverage-floors SEV1→SEV2 because the global floor IS enforced, analysis/-scope SEV2→CLEANUP after the mypy leg was refuted).

## Out of scope (deliberately not done)

Live-prod drills and output review; any fix, refactor, test addition, or flag flip (including the tempting `reframe_seats` wiring — filed as part of Issue 478 instead); integration-lane execution; mutation-scope changes; re-litigating the six DECISIONS-accepted behaviors.

## Method note

Exploration dossier: `/home/reese/.claude/plans/i-need-an-assessment-fluffy-rocket.md` (session-local). Deterministic outputs: session scratchpad (`pytest_default_lane.txt`, `eval_harness.txt`, `layer0.txt`, `phase1_facts.md`). Workflow: 12 agents / 687 tool uses / ~2.28 M subagent tokens; every candidate finding passed one confirmer or hunter AND one adversarial verifier; "new" classification required a fresh grep of `docs/issues.md`, `docs/DECISIONS.md`, and `docs/OFF_COURSE_BUGS.md` beyond the ledger index. Repro scripts live in the session scratchpad, referenced from each filed issue.
