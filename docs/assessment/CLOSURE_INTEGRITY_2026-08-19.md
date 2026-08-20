# Closure integrity audit — everything closed since the 2026-08-17 report

**Date:** 2026-08-19 · **Range:** `a57749f`..`fd8126e` (14 commits) · **HEAD == deployed revision**

The 2026-08-17 deep audit's finding was that this project **diagnoses better than it defends** —
it finds the right root cause, writes the fix into the row that reported it, and does not build it.
Ten closures have landed since. This audit asks the obvious follow-up: **are they closed, or do they
just say they are?**

**Verdict: 10 of 10 closures are real.** Every mechanism is implemented, live in production, and
proven non-vacuous. No baseline, ratchet, or coverage floor was loosened anywhere in the range. Four
issues carry honestly-recorded tails that need a trigger nobody has pulled yet; one bookkeeping error
was found and corrected.

---

## Method

Four layers per closure. A failure at any layer is recorded, not fixed inline.

| Layer | Question |
|---|---|
| **L1 Ledger** | Does every checked AC correspond to a real change in the diff? |
| **L2 Code** | Is the AC satisfied *as written*, and are the build note's **numbers** true? |
| **L3 Non-vacuity** | Revert the fix surgically — does a **named test go red**? |
| **L4 Live** | Is the effect observable in the running production container? |

L4 classifies as **VERIFIED LIVE** (observable now), **VERIFIED IN CODE** (deployed, awaiting a
trigger), or **NEEDS OWNER ACTION**.

---

## Summary

| Issue | L1 | L2 | L3 — test that goes red on revert | L4 |
|---|:--:|:--:|---|---|
| **498** (items 1–3, 6) | ✅ | ✅ | n/a (config/prose) — mechanism verified live instead | ✅ VERIFIED LIVE |
| **520** personalization no-op | ✅ | ✅ **re-measured** | `test_rerank_declines_to_blend_a_degenerate_model` + `test_personalization_degenerate_model_is_never_reported_active` | ⚠️ CODE — active path untriggered |
| **521** lying eval gate | ✅ | ✅ | `_assert_non_degenerate` raises on a forced degenerate booster | ✅ VERIFIED LIVE |
| **499** Layer-0 returncode | ✅ | ✅ | `test_gate_fails_when_the_tool_did_not_complete[ruff, bandit]` | ✅ CI-observable |
| **500** `--require` | ✅ | ✅ | `test_every_layer0_invocation_requires_its_gates` | ✅ CI-observable |
| **522** Redis posture | ✅ | ✅ | `test_sign_in_probe_survives_a_dead_limiter_backend` + `test_app_limiter_declares_the_in_memory_fallback` | ⚠️ CODE — fallback untriggered |
| **524** render verification | ✅ | ✅ | all three `test_render_*_verifies_its_output` | ⚠️ CODE — no render since deploy |
| **525** honest notifications | ✅ | ✅ | `test_zero_clips_does_not_claim_clips_are_ready` | ⚠️ **NEEDS OWNER ACTION** (#529) |
| *tracker number* | ✅ | ✅ | `test_next_free_issue_number_is_one_past_the_highest_filed` | ✅ n/a |
| *CI apt bounded* | ✅ | ✅ | `test_every_apt_invocation_is_time_bounded` | ✅ observed — the run after it stopped stalling |

**Audit self-check:** working tree clean after every surgical revert; full suite
**3259 passed / 64 skipped / 0 failed**, identical to the pre-audit baseline.

---

## The cross-cutting check: was any gate loosened to make a fix pass?

**No.** This was the most likely place for a quiet integrity failure and it is clean.

- `docs/assessment/baselines.json` — **zero commits touched it** in the entire range. Still
  `ruff 0 · mypy 0 · bandit_high 0 · bandit_medium 0 · pip_audit 0 · coverage 83.0`.
- `SCENARIO_FLOOR = 31` — unchanged.
- `MODULE_COVERAGE_FLOORS` — the file was edited by Issue 499, but the diff touches no floor value
  (`clip_engine 91.0 · preference 88.0 · crypto/limiter/auth 99.0` all intact).

---

## Issue 520 — the one worth re-deriving, and it holds

520's correctness rests on a **measurement**, so trusting the write-up would have defeated the point.
Re-measured independently (40 trials per label count, fresh seed, via the repo's own
`preference.model.fit` with the switchover forced to 0):

| n | degenerate trials | trees (median) | probability spread |
|---|---|---|---|
| 20–39 | **40/40 at every n** | 1 | 0.000000 |
| 40 | 11/40 | 100 | 0.199995 |
| **41+** | **0/40** | 100 | 0.53–0.66 |

**First fully clean n = 41, exactly the recorded floor.** The boot validator was then tested at the
boundary: `PREFERENCE_LGBM_MIN_LABELS` of 20 and 40 are **refused**; 41 and 60 **boot**.

**One discrepancy, immaterial:** the build note records 17/40 degenerate at n=40; I measured 11/40.
Different seed, same conclusion (40 is unsafe, 41 is the floor). Not corrected — the recorded figure
is a valid observation from a different sample, and the load-bearing number is 41, which reproduces.

**The structural honesty claim is true.** `effective_weight` has exactly three production callers —
the reranker, the API, the efficacy harness — and `preference_weight` has exactly **one** production
caller, `effective_weight` itself. Nothing computes a blend weight independently, so
`active ⟺ weight > 0` holds by construction rather than by three call sites remembering a rule.

---

## Issue 521 — the gate no longer certifies the wrong property

The fixture carries **eight creator shapes** (four at n=40 including the original 20/20 knife-edge,
one mid-ramp at n=30, three at n=80). **Zero strict-xfail markers remain** — the only surviving
mentions are prose explaining the history, consistent with all four XPASSing when 520 landed and
being cleared in that change.

Proven able to fail: feeding `_assert_non_degenerate` a LightGBM model forced at n=30 raises
*"the booster is degenerate at label_count=30: 1 tree(s), 1 leaf/leaves"*; at n=80 it passes.

---

## Issue 498 — four done, two honestly still open

| Item | Verified |
|---|---|
| 1 — `test:ci` verbose | `package.json:14` + invoked at `ci.yml:492` |
| 2 — `CLAUDE.md` names `.venv/bin/python` | 3 occurrences, both invoking lines converted |
| 3 — node engine pin | **live, both directions**: node 26.5.1 → `EBADENGINE … Required {"node":">=22 <23"}`; node 22.17.1 → clean install, exit 0 |
| 6 — single-source issue number | the only declaration is `docs/issues.md`; prose mentions elsewhere are history |

Items **4 and 5 remain correctly marked open** with their original deferral reasons intact. Nothing
was silently dropped.

---

## The four tails — what is genuinely not yet proven

These are **not** defects in the closures. Each fix is correct and deployed; each awaits a trigger.

| Issue | What is unproven | What would close it |
|---|---|---|
| **520** | The personalization-**active** path has never run in production | Owner rates ≥21 clips. **Currently 11** (was 10 when first recorded — it moved, still short) |
| **522** | The in-memory limiter fallback has never absorbed a real Redis outage | A genuine outage, or a deliberate one in a window. Not worth manufacturing |
| **524** | The render guard has never judged a real render — **0 renders in the last 3 h** | One upload |
| **525** | Notifications still do not deliver | **Issue 529** — provision Resend. Confirmed live: `NOTIFY_BACKEND=console`, and all **17** delivery rows now read `handled_by = NULL, status = sent`, exactly the console-era signature the new column was added to expose |

---

## Live production readback (2026-08-19)

Every fix confirmed present in the running container, not merely in the image:

```
520  threshold 20 · lgbm switchover 60 · min_child 20 · degeneracy gate present
522  in_memory_fallback True · swallow_errors False · redis timeouts 2.0/2.0
     BudgetStatus ('blocked','retry_after_s','reason')
524  verifier present, uses max()
525  no_clips_found copy present · NOTIFY_BACKEND=console
```

The Issue-525 startup warning **fired during this readback**, verbatim and unprompted — the most
direct possible evidence that it is doing its job.

---

## Findings

**1 — Bookkeeping error (corrected in this change).** Issues 524 and 525 were dated
`DONE 2026-08-18` in `docs/issues.md` but were built, merged and deployed on **2026-08-19**. Cause:
the date was carried from the plan rather than the landing. Corrected to 2026-08-19.

**2 — A near-miss worth recording, because it is the audit's own failure mode.** A first query of
`preference_models` returned **no rows**, which reads as "no trained model exists". The table has
**5 rows**; RLS is `ENABLE`d and `FORCE`d and the query ran without the tenant GUC set. An empty
result is not evidence of an empty table. Incidentally a clean live confirmation of tenant isolation.

**3 — No integrity failures found.** Nothing was ticked without evidence, silently descoped, or
propped up by a loosened gate.

---

## Scope

**Audited:** the 8 numbered issues and 2 un-numbered closures listed above.
**Not audited:** anything closed before `a57749f`; the 16 unapproved drafts in `DECISIONS_DRAFTS.md`;
Issues 528/529 (filed today, not closed).
**Not attempted, by decision:** live actions that are risky or owner-only — a deliberate production
Redis outage, rating clips, driving an upload. The audit's job is an honest ledger, not manufactured
evidence.
