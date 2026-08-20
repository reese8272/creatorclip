# LEFT_OFF.md — CreatorClip / AutoClip Session Handoff

**Last updated:** 2026-08-20 · **Branch:** `chore/closure-integrity-audit` @ `5e2c495` ·
**Working tree:** ✅ **CLEAN**, **2 commits ahead of `origin/main`, 0 behind.**

> ⚠️ **YOU ARE NOT ON `main`.** One PR is open and green: **#128** (the closure-integrity audit —
> docs only). Merging it is the first action below. `main` is at `fd8126e`, which is what production
> is running.

**Shipped 2026-08-17:** **#119** (the audit itself + Issue 498 items 1–3), **#120** (Issue 521 — the
lying gate), **#121** (Issue 520 — the personalization SEV1), **#122** (handoff).

**Shipped 2026-08-18:** **#124** (Issue 499 — Layer-0 gates that scored work they never did; + the
tracker-number drift), **#125** (Issue 522 — a Redis blip was a total sign-in outage; + Issue 500
`--require`; + every CI apt call bounded), **#126** (close-out).

**Shipped 2026-08-19:** **#127** (Issue 524 — the render never verified its own output; Issue 525 —
"Your 0 clips are ready"). All merged, deployed and live-verified. History on `main` is linear.

**Suite at handoff:** `.venv/bin/python -m pytest -q` → **3259 passed, 64 skipped, 0 xfailed**
(3170 before the 2026-08-17 work; 3199 → 3212 → 3225 → 3229 → 3259 across the 08-18/08-19 PRs).

**Prod:** `https://autoclip.studio`. **Running commit VERIFIED** — the container's image revision
label reads `fd8126ec09257ba3a5f185ae42c6e0bb4933004b`, and `/health` returns 200 over the public
URL with all containers healthy. Re-confirm the same way after any deploy:
`docker inspect autoclip-app-1 --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'`
*(Note: `curl localhost:8000/health` from inside the VM returns `000` — the app is behind
cloudflared, so check the public URL, not localhost. That is not an outage.)*

**Live-verified beyond the label** (per gotcha 4 — a green pipeline is not a working feature).

*2026-08-18 (Issue 522), read out of the RUNNING container, not the image:*
`in_memory_fallback_enabled=True · swallow_errors=False · fallback_limiter built ·
youtube redis timeouts 2.0/2.0 · BudgetStatus fields ('blocked','retry_after_s','reason')`.
Plus `GET /auth/me` returning **401, not 500** — the request passed *through* the limiter to auth,
which is the exact route that was the outage. The in-memory fallback itself has not been exercised on
prod, because that needs a real Redis outage; it is proven by test and by config readback, not live.

*2026-08-17 (Issue 520):* against the prod DB under a real `tenant_session`, the owner's scorer
reports:
`LogisticRegression · label_count=10 · is_degenerate=False · effective weight 0.0 ·
API active=False labels=10 threshold=20`. Two things that proves: `is_degenerate` computed correctly
on a **pre-520 blob** (the lazy-recompute path is what makes the fix migration-free — it ran in
production), and the honest sub-threshold fallback is intact. The owner has only 10 labels, so the
personalization-**active** path could not be exercised live — that needs ≥21 clips rated, and is the
one loose end from this work.

> ⚠️ **THE BRANCHING MODEL CHANGED ON 2026-08-15.** The `staging` **branch is deleted**. The model is
> trunk-based: `feature/* → PR → main`. `main` protection is now **`enforce_admins: true`** — there
> is no bypass, including for the owner. Direct pushes to `main` are **rejected** (verified live, not
> just read back from the API). PRs must merge via **Rebase** or **Squash**; linear history disables
> GitHub's merge-commit button. **Merging to `main` triggers a production deploy.**
>
> The staging **ENVIRONMENT** is untouched and still gates every prod deploy. Branch ≠ environment.

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source of
> truth. If a number here disagrees with `docs/GO_LIVE.md`, **the doc wins**.

---

## CURRENT FOCUS

> 🚦 **SCOPE DECISION, 2026-08-20 — CODE ONLY. The operator track is PARKED.**
> The owner has deferred every owner-only action until the L30 code queue is finished. **Do not
> propose operator work as "the next action", and do not re-raise it each session** — it is recorded
> in `docs/DECISIONS.md` (2026-08-20) with the accepted risk written down. Parked: Issues 255,
> 256/257/258, 288, 286, 246, **529**, 29, 28, plus rating clips and driving an upload.
>
> **Two consequences you will otherwise rediscover the hard way:**
> **(a) Issue 528 is parked by transitivity** — it is gated on 529, and landing it without Resend
> provisioned would fail the next deploy's boot. It is a code issue this decision nonetheless blocks.
> **(b) "Code complete" will still leave four fixes unproven in production** (520's active path,
> 522's fallback, 524's render guard, 525's delivery). Each needs a live trigger only the owner can
> pull. That is known and accepted, not an oversight.

**The single active goal: work Lane L30's remaining code queue to empty.**

Everything the 2026-08-17 audit found that jumped the queue is fixed, merged, deployed, and — as of
PR #128 — **independently verified**: Issues 521, 520, 499, 500, 522, 524, 525 and 498 (items 1–3, 6).
Read `docs/assessment/CLOSURE_INTEGRITY_2026-08-19.md` before re-checking any of them.

**The remaining code queue**, cheapest-first within each batch:

| Batch | Issues | Note |
|---|---|---|
| **C** — scan, don't list | **505, 506, 507** | **Recommended next.** 505: 2 of 29 tenant tables have no RLS policy (defence-in-depth gap, not an open door — every query filters explicitly). 506: the LLM-route literal lists 9 against ~15 live; two chat routes have a daily cap and no burst limit. |
| **B** tail | 501, 502, 503, 504 | 503 is the standout: a weekly mutation gate that has never executed a single mutant across 8 green runs. |
| **G** remainder | 523 | Stripe async-payment: take-money-grant-nothing, latent behind a Dashboard toggle. |
| **H** | 526, 527 | Dead output — the GDPR export has no UI while the Privacy Policy says it does. |
| **D / E / F** | 508–510 / 511–515 / 516–519 | Docs-as-schema, production truthfulness, process artifacts. |
| *also open* | 498 items 4–5, 445, 484, 495 | 484 is the highest-impact known clip-quality defect (a clip opening on a clause that inverts the speaker's meaning). |

**Blocked and not workable under this decision:** 528 (needs 529).

### → NEXT ACTION

1. **Merge PR #128** — open, green (13/13), `CLEAN`, and the only thing keeping this checkout off
   `main`:
   `gh pr merge 128 --repo reese8272/creatorclip --rebase --delete-branch`
   then `git checkout main && git pull --ff-only origin main`.
   Docs-only, but **merging to `main` deploys** and `docker-publish.yml` has no `paths-ignore`, so it
   still rebuilds and redeploys (gotcha 8). Expected, not a problem.

2. **Start Lane L30 Batch C — Issue 505 first.** Run the project's issue workflow (CHECK → APPROVE →
   BUILD → REVIEW). 505 is `M (~2 h)`: derive the RLS sweep from `pg_policies` at runtime instead of
   two hand-written tuples, and resolve the two tenant tables that carry `creator_id` with no policy
   and no recorded exemption. **Already confirmed independently, do not re-derive:** exactly **2 of
   29** tenant tables (`creator_api_keys`, `creator_identity`) appear in no RLS migration, and every
   current query filters by `creator_id` explicitly — so this is a missing defence-in-depth layer,
   **not** an open door. It needs the integration lane, which runs in CI only.

3. **Then 506, then 507** — same batch. 506's fix includes four LLM route decorators missing a burst
   or daily cap (Celery-flood risk, not a spend hole: all four carry `Depends(require_budget)`).

4. **Then pick from the queue table above.** If you want product impact over process hygiene, jump to
   **484** (clips opening on a clause that inverts the speaker's meaning) or **523** (Stripe
   take-money-grant-nothing) instead of finishing Batch B.

**Do NOT do:** anything on the parked operator list, or Issue 528. See the scope decision above.

### → BEFORE YOU START: read these, in this order

1. **§🔬 THE RESEARCH BEHIND THIS WORK, below** — the measurements and the paths taken/rejected.
   Cheapest thing in this file to read and the most expensive to re-derive.
2. `docs/assessment/DEEP_AUDIT_2026-08-17/REPORT.md` — the verdict and the reading rules below.
3. `docs/assessment/DEEP_AUDIT_2026-08-17/SYNTHESIS_process.md` — the mechanism map + 90-day plan.
4. `docs/issues.md` Lane **L30** (Issues 498–527), eight batches. Note **498 items 1–3, 520 and 521
   are now ticked** — read their build notes, not just their titles.

> **Two rules for anything sourced from that audit.**
> **(1) Use the verifier's CORRECTED statement, never the original claim.** Of 141 findings, 72 were
> adversarially verified: **8 CONFIRMED, 57 CORRECTED, 7 REFUTED**. The 7 refuted are listed in
> `SYNTHESIS_technical.md` §3 "Excluded" and in L30's "deliberately NOT filed" section — **do not
> re-file them**, and do not re-open the process changes rejected there either.
> **(2) Discount any unverified severity by about one level.** Measured: of the 21 findings that
> faced a skeptic, **21 were downgraded and 0 upgraded**. 69 findings were never contested at all.
>
> **Coverage was not uniform.** 67% of the frontend, `ingestion/transcribe.py` (the live ASR switch),
> the four `knowledge/clip_*` modules, `upload_intel/`, `improvement/` and 59 of 62 migrations were
> **never opened**. Absence of findings there is not a clean bill of health.
>
> **Do not "improve" three things the audit explicitly ratified:** the missing service layer, the
> single VM, and `worker/tasks.py`'s line count. All three now have decision entries dated
> 2026-08-17 — argue against the recorded position if you disagree, don't re-litigate a vacuum.

---

## 🔬 THE RESEARCH BEHIND THIS WORK — what we measured, and where it lives

Read this before touching `preference/` or any Layer-0 gate. It is the *why* behind the last two
commits, and none of it is re-derivable cheaply.

### Where the research is saved

| Artifact | What it holds |
|---|---|
| `docs/assessment/CLOSURE_INTEGRITY_2026-08-19.md` | **Independent verification of all 10 closures since the audit** — four layers each (ledger / code / non-vacuity / live). Read this BEFORE re-verifying any of 498, 499, 500, 520, 521, 522, 524, 525. |
| `docs/assessment/DEEP_AUDIT_2026-08-17/` | The full read-only audit — 56 agents, three phases, **12,546 lines across 30 files**. `REPORT.md` first, then `SYNTHESIS_process.md`. This is the corpus Lane L30 was filed from. |
| `docs/DECISIONS.md` (2026-08-17 entries) | **Eight** entries carry this date. Six are the audit's own approved decisions; **two were written on 2026-08-17 evening** — the strict-xfail rule and the two-numbers preference split. Each carries its measurements and its rejected alternatives. |
| `docs/issues.md` §498/§520/§521 | Per-issue build notes: what was done, what departed from the filed plan, and the verification evidence. |

### What was measured on 2026-08-17 (empirically, by running the repo's own code)

The audit *claimed* the preference model was degenerate. We re-derived it rather than trusting the
claim — and the measurement **corrected the audit's stated band**. The dead zone is **n = 20–39 with
certainty**, plus ~43% of n=40 — not the "21–40" the finding said. Running `preference.model.fit` on
lightgbm 4.6.0, 40 trials per label count:

| n (labels) | trained booster | probability spread |
|---|---|---|
| 20–39 (every n, 40/40 trials) | 1 tree, 0 splits | **0.000000** |
| 40 | degenerate in 17/40 trials | 0.0 or 0.9999 |
| ≥41 | 92+ trees | 0.9999 |

**41 is the measured non-degeneracy floor.** `PREFERENCE_LGBM_MIN_LABELS = 60` is that floor with a
~1.5× margin, and `config.py` now refuses to boot below 41. If you ever change
`PREFERENCE_LGBM_MIN_CHILD_SAMPLES`, **re-measure the floor** — the two are coupled by construction.

The eval fixture was measured the same way. Its old single 20/20 split gave **92 trees, spread
0.9999**; moving **one label** to 21/19 collapsed it to **1 tree, spread 0.000000**. That is the whole
Issue-521 defect in two numbers.

### The paths we took, and the ones we rejected

1. **Issue 520 — raise the switchover (chosen) vs. tune `min_child_samples` down (rejected).**
   Chosen because it makes `preference/model.py`'s own "LogisticRegression cold-start → LightGBM
   warm-start" docstring *true* rather than rewriting it; because a linear model is the standard
   choice on 20–60 rows where a shallow GBDT fits noise; and — decisively — because the rejected path
   forces `PERSONALIZATION_THRESHOLD_LABELS` to ≥41, which moves the **creator-facing** number on the
   Review page from 20 to 45. That is a product regression adopted to fix an implementation defect.
2. **Landing Issue 521 as `xfail(strict=True)` (chosen) vs. narrowing the assertion until green
   (rejected).** `main` has no bypass, so a red test cannot merge. Narrowing is the exact failure
   class 521 exists to fix. Strict xfail kept CI green, printed the known-red reasons into the build
   log, and — because a strict xfail that starts passing is a hard failure — made **"521 blocks 520"
   enforced by pytest instead of by a doc bullet.** It worked exactly as designed: when 520 landed,
   all four markers XPASSed and *failed the run*, which is what forced them to be cleared in the same
   change. **Before committing any strict-xfail marker, simulate the intended fix and confirm the row
   can actually XPASS** — otherwise you have written a permanent blocker. We did.
3. **Adding a mid-ramp n=30 split the acceptance criteria did not ask for.** The issue is about the
   *ramp*; n=30 is where a real creator sits (weight 0.25, `active: true`, model returning 0.5 for
   every clip). Testing only n=40 would have repeated the original mistake at a different point.
4. **Leaving the precondition test deliberately un-xfailed.** It asserts label count and blend weight
   — things a degenerate model satisfies perfectly. It passes on all eight splits while the two
   property tests failed on four. That gap, in one test run, *is* the defect stated as a test result.
5. **Deferring Issue 498 items 4 and 5 rather than batching them in.** Item 4 needs the `failed: 0`
   vs `LOCAL CI FAILED` contradiction *reconciled* — a diagnosis, not a one-liner, and burying it in
   a docs PR would hide it. Item 5 mutates branch protection and wants its own change with a readback.

### Two facts a future session cannot re-derive

- **The migration-free claim was proven in production, not just in tests.** `is_degenerate` computed
  correctly on a real pre-520 model blob on the prod box. That is what makes the lazy-recompute path
  trustworthy; do not "tidy it up".
- **Expect one warn-only NDCG-ratchet event per affected creator** on their first retrain after this
  deploy. The served model genuinely changes shape. That is the ratchet working — **do not chase it
  as a regression.**

---

## WHAT WORKS NOW (verified — do not re-investigate)

### Every closure since the audit has been independently verified (2026-08-19, PR #128)

`docs/assessment/CLOSURE_INTEGRITY_2026-08-19.md` audits all ten closures in `a57749f..fd8126e`
against four layers: ledger, code, **non-vacuity** (revert the fix, does a named test go red), and
live production readback. **Verdict: 10 of 10 real.** Do not re-verify these — read the report.

Three results worth carrying:

1. **No gate was loosened to make a fix pass.** `baselines.json` has **zero commits** touching it in
   the range; `SCENARIO_FLOOR` unchanged; no coverage floor moved. This was the likeliest place for a
   silent integrity failure and it is clean.
2. **Issue 520's measurement reproduces exactly.** Re-run independently at 40 trials per label count
   on a fresh seed: degenerate 40/40 at *every* n in 20–39, **first clean n = 41** — the recorded
   floor. The boot validator refuses 20 and 40, boots at 41 and 60. (The build note's "17/40
   degenerate at n=40" measured 11/40 here — different seed, same conclusion, left uncorrected.)
3. **All seven testable mechanisms are non-vacuous** — each was surgically reverted and a specific
   named test went red. The test names are in the report's summary table.

**Four tails remain, none of them defects.** They need a trigger, not a fix: 520's
personalization-*active* path (owner has **11** labels, needs 21), 522's in-memory fallback (needs a
real Redis outage), 524's render guard (**0 renders** since deploy), 525's delivery (Issue 529).
**One upload would collapse three of the four** — it exercises the render guard, produces a
notification, and adds clips to rate.

### The personalization loop is honest now (2026-08-17, PRs #120 + #121)

`PERSONALIZATION_THRESHOLD_LABELS` (20) and `PREFERENCE_LGBM_MIN_LABELS` (60) are now **two separate
numbers answering two different questions** — "enough feedback to be honest about?" and "enough rows
for this estimator to split?". One constant answering both was the root cause of the SEV1.
`effective_weight()` is the **single** producer of the blend weight, read by the reranker, the API
and the offline efficacy harness, so `active ⟺ weight > 0 ⟺ the blend was applied` holds
structurally rather than by three call sites remembering the same two conditions. The
LogisticRegression branch is reachable at serve time **for the first time in the project's history**.
Three vacuous-green tests were fixed in the same change (two could not even compile past the
parameter rename).

### Repo hygiene + branch protection (2026-08-15)

Three PRs shipped, all rebase-merged, `main` history now genuinely linear:

- **#115** — privacy-policy brand fix (Issue 488 / LLC rebrand). Two stale "CreatorClip" sentences in
  `static/privacy.html` → "AutoClip", plus a regression test asserting the retired name is **absent**
  from all three legal pages. The pre-existing tests only asserted "AutoClip" was *present*, which is
  exactly why the stale copy survived. **Live-verified on prod:** `privacy.html` = 0 "CreatorClip" /
  16 "AutoClip"; `tos.html` and `accessibility.html` = 0. Google's OAuth review checks app-name
  consistency across the ToS and privacy policy (`docs/GO_LIVE.md:50`), so this was a real #29 gate.
- **#116** — retired the `staging` branch; `enforce_admins: true` on `main`. Rationale in
  `docs/DECISIONS.md` (2026-08-15) and a rewritten `docs/BRANCHING.md`.
- **#117** — off-course log entry for a race-test flake (below).

**Why the staging branch went away** (all three verified, not assumed):
1. It verified nothing — `ci.yml` ran the *identical* 8 required checks on PRs into `main` **and**
   `staging`. A second hop through the same gates added latency, not signal.
2. Nothing ever deployed it — `docker-publish.yml` builds on `push: [main]` only. Every other
   `staging` string in `.github/workflows/` and `tests/test_ci_config.py` refers to the `ccstage`
   compose stack.
3. It made real protection impossible — `enforce_admins` + `required_linear_history` + a long-lived
   second branch **deadlock** on the second promotion. See the gotcha in §7 below.

**Evidence protection was previously advisory-only:** `main`'s old HEAD `1221fb8` was a *two-parent
merge commit* despite `required_linear_history: true`, and the documented
`git push origin origin/main:staging` sync landed while the remote itself printed
`8 of 8 required status checks are expected`. Both rode admin bypass. That is now closed — a direct
push to `main` returns `[remote rejected] ... protected branch hook declined`.

**Current `main` protection (readback-verified):** `enforce_admins=true`, `linear=true`,
`strict=true`, 8 required checks, `force_push=false`, `deletions=false`. `delete_branch_on_merge` is
now **on**, so merged branches clean themselves up.

**Issue 496 filed** — a *real* pre-merge staging environment (deployed, **auth-gated** URL). The auth
gating is load-bearing: the repo is public and the stack holds seeded creator fixtures.

### Everything from 2026-08-14 still stands

The catalog-sync outage (`_FIELDS_PLAYLIST_ITEMS` requested `snippet(resourceId/videoId,…)` while
`list_channel_videos` filtered on `resource_id["kind"]`, which the `fields` spec stripped — so every
item was dropped, silently, for **seven weeks**) is fixed and **live-proven on prod**: creator
`eb9af967-…` went `0 → 21` videos (10 long / 15 Shorts), 21 `video_metrics` rows, 2100 retention
points, and `/creators/me/data-gate` `0/0 ready:false` → `6 long / 15 shorts, ready:true`.

Also green in that release: **#490** (beat-refresh commit granularity + stalest-first ordering),
**#491** (grounding honesty — DNA block omitted rather than placeholder-injected), **#492** (clip LLM
calls were describing the whole video's opening), **#493** (in-app YouTube reconnect), **#494**
(kill switch + spend gate on two billed routes).

Full detail: `docs/issues.md` §489–496, `docs/DECISIONS.md`, `docs/PROJECT_STATE.md`.

---

## ⛳ WHAT IS LEFT TO COMPLETE THE BETA

`docs/GO_LIVE.md` is the canonical scorecard — **read it, don't trust this summary**. As of
2026-08-14 it records **Stage A = 35 gates · 20 GREEN · 5 CODE-GREEN · 10 OPEN · 0 RED**. Nothing on
2026-08-15 changed a gate's status; the privacy fix strengthened the *evidence* under the already-
GREEN "ToS + Privacy Policy live, linked, and accurate" row (`GO_LIVE.md:46`) — arguably that row was
not fully accurate before, since two pages carried a retired brand name.

### A. Operator track — ⏸️ **PARKED 2026-08-20 until the code queue is empty**

> Deferred by owner decision (`docs/DECISIONS.md`, 2026-08-20). Everything in this section is real and
> still required for the beta — it is **sequenced after** the code track, not cancelled. Do not pull
> items from here into a session's next-action list.
>
> **The one item whose cost grows while parked:** backups. Nothing is backed up today, and the
> 2026-08-19 deploy logged *"Migrating WITHOUT a safety dump"*. Losing the droplet during the code
> track is permanent loss of `preference_models` — the trained taste, which is the product. Every
> other row here merely waits. This is recorded as accepted risk; it does not need re-raising.

Ordered by consequence, per `docs/runbooks/255-258-dr-durability.md`:

| # | What | Why it's ordered here | Runbook |
|---|---|---|---|
| **255** | **Secrets escrow — DO THIS FIRST.** Copy `TOKEN_ENCRYPTION_KEY`, `JWT_SECRET_KEY` and a snapshot of `/opt/autoclip/.env` to **two independent** legs (password manager **and** GCP Secret Manager). | Without it a perfect Postgres restore yields useless ciphertext — every user's OAuth tokens unrecoverable. **Never store these inside the backup they protect.** | `docs/RUNBOOKS.md:578-589` |
| — | **Rotate the exposed Anthropic key.** New key in console → VM `.env` → `doctor.py --full` green → **then** revoke the old one. | A known-exposed credential on a billed API. | `docs/SECRETS.md:219-222` |
| **256/257** | **Nightly encrypted PG backups.** Create `creatorclip-backups`; set `BACKUP_R2_BUCKET` + `BACKUP_ENCRYPTION_KEY`; install cron `7 3 * * * cd /opt/autoclip && ./scripts/backup_pg.sh`. | No backups exist today. | `docs/RUNBOOKS.md:590-606` |
| — | **Run the restore drill.** `scripts/reapply_erasures.py` afterwards is **mandatory**, not optional. | It is what stops a restore resurrecting data a user asked to have erased. Record the RTO. | `docs/RUNBOOKS.md:641-648` |
| **258** | **R2 Object Lock**, Compliance mode ≥14d — **not** Governance (admin-overridable, therefore not tamper-proof) — plus per-prefix lifecycle. | Reconcile windows against right-to-erasure (#254); also closes that CODE-GREEN row. | `docs/RUNBOOKS.md:596-601` |
| **288** | **Redis durability.** cron `27 3 * * * ./scripts/backup_redis.sh`, then the restart drill. *Staging Redis is intentionally ephemeral — do not "fix" it.* | The Celery broker currently does not survive a restart. | `docs/RUNBOOKS.md:711-714` |
| **286** | **Cloudflare edge rate limit** on `/auth/*` — `preauth-rate-limit`, 10 req/min per IP, Managed Challenge. Keep `/health` **out** of the expression. | The verify loop must show the challenge **and** `docker compose logs app` showing no request flood — that is what proves the block happened at the edge, not in the app. | `docs/EDGE_SECURITY.md:26-79` |
| **246** | **`MAILING_ADDRESS`** — a real physical postal address (PO box or CMRA mailbox; printed publicly in every lifecycle email footer). | Until set, **all lifecycle email is intentionally SKIPPED** (`config.py:1007-1010`, enforced at `worker/tasks.py:4556`). That is the correct fail-safe, not a bug. **Not required for a friend beta.** | — |

### B. Code track

- **Issue 445 — the three-pile triage UI.** `docs/issues.md` Lane L27. Strangers hit the review queue
  on their **first** upload, and reviewed state does not survive a reload today. Four design
  questions open — **run a real CHECK phase first**; AC numbering has drifted.
- **Issue 484** — the meaning-inverting cold open (a clip opening on `"feel like Percy Butler is…"`
  when the speaker said *"don't feel like"*). Highest-impact known clip-quality defect.
- **Issue 495** — the deferred 2026-08-14 audit list: brand-kit fields that cannot be cleared; the
  `captions_enabled` toggle no renderer reads; `Profile.tsx` hardcoded `"—"` stats; the fully-built
  **data export with no UI** (GDPR Art. 15/20); `is_rewatch_spike` + `captions_available` never
  written; `push_enabled`; unvalidated `_upsert_style_field`; the `AskSurfaceTabs → /analysis` dead
  end.
- **Issue 496** — a real pre-merge staging environment (see above). Not beta-blocking.

### C. The #28 friend smoke itself

Unblocked. The criterion is *the full pipeline exercised on prod over 48 h* — the invite is not the
gate. Warn the friend about two expected things so they don't read as breakage:
1. The **"Google hasn't verified this app"** interstitial at consent (expected while #29 is
   unsubmitted).
2. **Weekly reconnect** — Google expires Testing-mode refresh tokens after 7 days. There is a real
   reconnect card on Profile (Issue 493), and the app warns 2 days out.

### D. Stage B (public launch) — start the clock now

- **Issue #29 — Google OAuth verification.** Submit the READ-ONLY scope set only (`openid`,
  `userinfo.email`, `userinfo.profile`, `youtube.readonly`, `yt-analytics.readonly` —
  `youtube/oauth.py`). **Do NOT include `youtube.upload`**: it drags in the heavier YouTube API
  compliance audit (#194 keeps it a separate, later submission). The 100-user cap is **not** why this
  matters — the 7-day token expiry is, and that is independent of user count.
- Remaining Stage-B gates (8 OPEN): #261 load profile, #236 SLOs, #282 status page, #326
  Grafana/Sentry activation → unblocks #291 cost alerts, key-rotation dry run, final security review,
  pricing beyond minute packs, and the #30 sign-off.

---

## THE ARC THAT LED HERE

1. **2026-08-13** — CI-integrity checkpoint closed; `main`/`staging` branch-protected (but
   `enforce_admins: false`, so the rules bound everyone except the only person pushing).
2. **2026-08-14 (morning)** — billing went RED → GREEN after four stacked defects. The ledger then
   claimed #28 was the sole remaining blocker.
3. **2026-08-14** — owner's three symptoms (*"sync says 0 shorts and 0 videos"*, *"why reconnect every
   7 days"*, *"why can't I generate titles and hooks"*) resolved to **one root cause** (the
   `fields`/`kind` contract drift) plus a class of related defects. Shipped, deployed, live-verified.
   The ledger claim in (2) was **wrong and has been corrected** — #28 could never have passed while a
   friend's first sync returned nothing.
4. **2026-08-15** — owner asked for repo cleanup and for `main`/`staging` to "genuinely
   hold their protective values". Investigation found the `staging` branch verified nothing, was never
   deployed, and structurally *prevented* `enforce_admins: true`. Retired it; trunk-based now.
   The owner's in-flight privacy-policy edits were finished, tested, merged and live-verified in the
   same pass.
5. **2026-08-17 (morning)** — owner asked *why the project keeps hitting one small snag after
   another*, and commissioned a deep standards + process audit (56 agents, three phases, read-only).
   **Answer: not the architecture and not the stack** — the missing service layer, the single VM and
   `worker/tasks.py`'s size were each independently ratified as correct. The diagnosis is that **this
   project diagnoses better than it defends**: it finds the right root cause almost every time,
   writes the fix into the row that reported it, and does not build it. Nine recurring gotchas each
   have a correct structural fix already written down; none was built; the classes recurred.
   Bounded honestly by the audit's own completeness critic — that explains **~40%** of logged snags;
   the rest (especially third-party SDK surprise, which escapes to production *every* time) needs a
   **habit**, not a gate: one real transaction against each live integration on a schedule.
   Counter-evidence worth holding: **August has the lowest fix-commit ratio in the project's history
   (36%)**, so defect *arrival* is not rising — with 52 of 138 off-course rows still open, the
   "snag after snag" feeling is at least partly backlog visibility.
   Seven decisions were approved and recorded; Lane L30 (Issues 498–527) filed.
6. **2026-08-17 (late) — the audit's own top finding got built.** Owner asked what to tackle first.
   The answer was not on the list: the audit's 12,546 lines were **untracked**, one `git clean` from
   gone, so committing them preceded everything. Then the SEV1, in the order the audit itself
   demanded — **the gate before the fix**, because the gate written four days earlier to catch this
   defect *passed*, and a fix landing against a lying gate proves nothing.
   The shape of the defect is worth remembering, because it is the audit's thesis in miniature: one
   config constant was quietly answering two unrelated questions, and the gap between the right
   answers was the bug. Nobody wrote a wrong number; nothing related the number to what the model
   could actually do. That is "diagnoses better than it defends" expressed in code rather than in
   process — and it is why the fix ends in a boot-time validator rather than a comment.
   Batched alongside: three Issue-498 conversions, each turning a rule someone had to *remember*
   into a mechanism that cannot be forgotten.
7. **2026-08-18 — the gate track, then the outage nobody had noticed.** Issue 499 first, because a
   session that cannot trust its own gate re-runs mypy by hand; then Issue 500 on top of it. The
   tracker's next-free-issue number had drifted the day after being made the sole authority — fixed
   by deriving it. Then **Issue 522**, which turned out to be the day's real finding: the limiter
   passed neither fallback kwarg, so a Redis blip was a **total sign-in outage** that `/health`
   reported as 200. Fixing it surfaced a latent event-loop defect that fail-open had been hiding for
   months, and CI caught the half of that fix a local green run could not.
8. **2026-08-19 — two defects where the product claimed success it had not earned.** Issue 524: the
   render shipped a silently truncated clip and announced "Clip ready." Issue 525: a zero-clip video
   was told its 0 clips were ready. Building 525 meant reading the live container rather than the
   repo, which produced the worst finding of the week — **notifications have never worked in
   production**: 17 delivery rows saying `sent`, none delivered, no creator ever emailed.
9. **2026-08-19 (late) — the owner asked whether any of it was actually done.** Ten closures had
   landed since the audit; the obvious question was whether they were closed or only said so. The
   answer, verified four ways per closure: **all ten real, no gate loosened, Issue 520's measurement
   reproduces exactly.** The audit found one bookkeeping error of its own author's making and one
   near-miss (an RLS-filtered empty result that read as an empty table). That check is now
   `docs/assessment/CLOSURE_INTEGRITY_2026-08-19.md`, and it is why the next session should read
   rather than re-derive.

---

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Prod URL | `https://autoclip.studio` |
| GitHub repo | `reese8272/creatorclip` (**public**) — note the dir name `Youtube-Video-AI-Editor` differs from the repo slug; `gh api repos/...` calls must use the slug |
| VM (DigitalOcean) | `147.182.136.107` (`ssh creatorclip-vm`), deploy dir `/opt/autoclip`, compose at `/opt/autoclip/src/docker-compose.prod.yml` |
| Containers | `autoclip-app-1`, `autoclip-worker-1`, `autoclip-render-worker-1`, `autoclip-beat-1`, `autoclip-postgres-1`, `autoclip-redis-1`, `autoclip-cloudflared-1` |
| Prod DB | `docker exec autoclip-postgres-1 psql -U creatorclip -d creatorclip` |
| Owner creator id | `eb9af967-5d2f-4063-a05e-9f4f070ce840` ("Backboard Media", channel `UCNU5Tnt0xp7YtHNPgxDrSIw`) |
| Deployed commit | `fd8126e` (confirmed 2026-08-19 from the container's image revision label; `/health` 200 over the public URL). **Note `main` may be ahead once PR #128 merges — re-confirm after any deploy.** |
| Health check | Use `https://autoclip.studio/health`. `curl localhost:8000/health` **on the VM returns `000`** — the app sits behind cloudflared. Not an outage. |
| Image | `ghcr.io/reese8272/creatorclip:latest` (deploys resolve `sha-<7char>` for the staging gate) |
| Branch model | **trunk-based**: `feature/* → PR → main`. No `staging` branch. Rebase/squash only. |
| Deploy chain | push to `main` → **Docker publish** → (`workflow_run`) → **Deploy to production** → data-bearing staging gate → prod smoke + auto-rollback |
| Local test env | **Use `.venv/bin/python`** or `scripts/ci_local.sh` — see gotcha 1 |
| Node | 22.17.1 (`.nvmrc`); node 26 breaks jsdom |
| Secrets (names only) | `TOKEN_ENCRYPTION_KEY`, `JWT_SECRET_KEY`, `ANTHROPIC_API_KEY`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `BACKUP_ENCRYPTION_KEY` — in VM `/opt/autoclip/.env` + GitHub secrets. **Never read or print values.** |

---

## CONSTRAINTS & GOTCHAS

1. **⚠️ RUN TESTS FROM `.venv`, NOT SYSTEM PYTHON.** This burned three consecutive sessions.
   System `python3.12` has **fastapi 0.115.4** against the pinned **0.137.1**, and its `mypy` cannot
   import the `pydantic` plugin — so mypy aborts before checking anything and Layer 0 reports a
   **vacuous `ok 0`**. Under system python the suite once showed 4 phantom failures and pip-audit 77
   phantom CVEs; under `.venv` it is **3199 passed / 0 failed**, all gates 0. Use
   `scripts/ci_local.sh --fast` (it prefixes `PATH` with `.venv/bin`).
   ✅ **Partly mechanised 2026-08-17 (Issue 498 item 2):** `CLAUDE.md` no longer *instructs* the bad
   interpreter — both places that invoke one now say `.venv/bin/python`. The structural half (a gate
   that cannot run must report `fail`, not `ok 0`) is **Issue 499, still open** — see gotcha 13.
2. **FastAPI 0.137.1 defers `include_router`** into `_IncludedRouter` objects, so `app.routes` holds
   almost no `APIRoute`. Any test that walks routes naively iterates **zero** routes and passes
   vacuously. Resolve via `effective_route_contexts`; see `tests/test_response_models.py`.
3. **`deploy.yml` triggers on `workflow_run`, not `push`.** After merging there is a gap while
   **Docker publish** builds. A naive "latest deploy run" check matches the *previous* deploy and
   reports a stale success. **Pin any deploy check to your SHA**, or read the revision label off the
   running container (`docker inspect autoclip-app-1 --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'`).
4. **A green intermediate layer is not a working feature.** The standing lesson of the billing and
   catalog outages. Neither a passing suite, nor a success log line, nor a green pipeline is evidence
   a feature does its job — only the feature's own output is.
5. **Counters must count writes, not attempts.** `"Synced 4 new video(s)"` over a dead feature hid the
   catalog outage for seven weeks.
6. **A `fields=`/projection string and the code that parses the response must change together.**
   `tests/test_data_api.py` applies the real spec to the fixture before parsing, so drift fails CI.
7. **Do NOT recreate a long-lived branch that merges into `main`** without first setting
   `required_linear_history: false`. The three settings deadlock: linear history disables the
   merge-commit button, forcing squash/rebase, which rewrites SHAs; syncing the branch back is then a
   non-fast-forward, and `allow_force_pushes: false` with admins enforced leaves no way to land it.
   GitHub exposes no true fast-forward merge button. Mechanism documented in `docs/BRANCHING.md`.
   *(This gotcha replaces the old "`enforce_admins: false` is load-bearing" note, which is now wrong.)*
8. **Every change to `main` now costs a PR + a full CI cycle**, including one-line doc edits. That is
   the accepted price of closing the bypass. Batch doc changes where you can. **Known inefficiency:**
   `docker-publish.yml` has no `paths-ignore`, so a docs-only merge still rebuilds the image and
   redeploys prod. A `paths-ignore: ['docs/**', '**.md']` would fix it — **not done**, since it needs
   a check that no doc is load-bearing at build time.
9. **Never revert Stripe to `HTTPXClient`** — two stacked defects, 10-week total checkout outage
   (`billing/stripe_client.py`, DECISIONS 2026-08-12). `RequestsClient` only.
10. **Before any fresh-upload drill**: run `.venv/bin/python scripts/r2_set_cors.py https://autoclip.studio`.
    The `ExposeHeaders ETag` is load-bearing — without it multipart completes stall at 100%.
11. **Known flake, do not panic:** `tests/test_ranking_persist_race.py::test_persist_integrity_error_returns_winner_set`
    failed once in the gating lane on 2026-08-15 and would not reproduce (8/8 isolated, clean full
    re-run). Ordering-dependent (`pytest-randomly`). Logged in `docs/OFF_COURSE_BUGS.md`. **Per the
    Flake Policy: never add `--reruns`;** quarantine only on recurrence. Note `ci_local.sh` printed
    `LOCAL CI FAILED` while its own summary said `failed: 0` — those disagree, and one is lying.
12. **Before debugging anything non-trivial**, grep the cross-project log first:
    `grep -i "<symptom>" ~/.claude/ISSUES_LOG.md`.
13. ✅ **RESOLVED 2026-08-18 (Issue 499).** Layer-0 static gates now check that their tool actually
    ran, so a Layer-0 `ok 0` is trustworthy again — the re-run-by-hand tax is gone.
    **Two things worth carrying forward, because re-deriving them costs a measurement pass:**
    (a) the check is an **allow-list `{0, 1}`**, never `returncode != 0` — exit 1 is the normal
    "ran, found things" state the baselines exist to count, and rejecting it red-walls the gate on
    the first ruff violation; (b) **bandit is invisible to any returncode check** — a scan of a
    missing path or an unparseable file exits **0** with `results: []` and a populated `errors[]`,
    so that array is its only signal. Anyone "simplifying" this to a non-zero check reopens both.
    Table + rationale: `docs/DECISIONS.md` 2026-08-18; build note at `docs/issues.md` §499.
    **Issue 500** (`--require` on every invocation) is now **unblocked** — the ordering constraint it
    carried was this issue.
14. **A gate whose scope is a list literal is a vacuous-green generator by construction.** The audit
    censused 101 module-level literals in `tests/` + `scripts/`, diffed the ~20 that define a gate's
    scope, and **11 had drifted** — two covering live defects. The house rule is now **"scan, don't
    list"**; the model to copy is `tests/test_usage_coverage.py` (real AST discovery with a
    bidirectional staleness check). Applies to RLS tables, LLM route registries, ffmpeg task routing,
    and `run_layer0.py`'s own source list.
    **Worked example, 2026-08-18:** `docs/issues.md`'s `Next free issue number` was a hand-incremented
    literal and read 498 while 498–527 were filed above it — the next issue would have collided with
    #520. It is now derived (`tests/test_tracker_hygiene.py` scans every `### Issue N` heading under
    `docs/` and asserts the declaration is `max + 1`, and that it exists in exactly one file). Note
    the shape: Issue 498 item 6 had *diagnosed* this the day before and deleted the two competing
    copies. Making one number authoritative did not make it correct — only the scan did.
15. ✅ **BUILT 2026-08-18 (Issue 522).** The limiter degrades to a per-process in-memory bucket, the
    spend guard fails CLOSED on its pre-execution check, and all four Redis clients now have socket
    timeouts. **Three things to carry forward:**
    (a) fail-closed covers the CHECK arm only — `record_spend` stays fail-open on purpose, because
    post-call accounting cannot un-spend money;
    (b) the can't-verify case returns **503 with its own copy**, never the 429 "budget reached"
    message, which would be false during an outage — if you touch that copy, keep the honesty
    assertions in `tests/test_spend_guard.py` passing;
    (c) **expect a 2× effective rate limit while Redis is down** (`--workers 2`, per-process bucket).
    That is the accepted cost, not a bug.
    The three stale documents that described the old fail-open claim are amended. Note one of the
    three citations (`DECISIONS.md:2633-2634`) had itself drifted to an unrelated entry — a live
    instance of what **Issue 508** exists to catch.
16. **Stacked PRs need a rebase + force-push after each merge.** Rebase-merging rewrites the SHA, so
    the moment PR A lands, PR B (which contained A's old commit) goes `CONFLICTING`. This is normal
    here, not a mistake: `git rebase origin/main` on B's branch, then
    `git push --force-with-lease`. Git will say "skipped previously applied commit" — that is correct.
    Budget a full CI cycle per rebase; three stacked PRs cost three cycles.
17. **The integration lane cannot run on this box.** No local Postgres running and the Docker daemon
    is down, so `-m integration` aborts at the conftest reachability check. CI is the only
    verification — and **say so plainly** rather than implying you ran it. To confirm a *new*
    integration test actually executed rather than silently skipping, diff the `N passed` count
    against the previous run's (on 2026-08-17: 190 → 191). The lane runs `-q`, so names aren't printed.
18. ✅ **RESOLVED 2026-08-18 — apt hangs are now bounded.** All 14 apt invocations in `ci.yml`
    carry `timeout 300`, pinned by `test_every_apt_invocation_is_time_bounded`, so a stalled mirror
    fails fast into the existing warning path instead of hanging to the 6-hour job timeout. It had
    recurred three times with only a manual cancel-and-rerun as mitigation, and it blocked a merge
    the day it was fixed (`Integration tests` is a required context with no admin bypass).
    *Historical, for context:* Seen 2026-08-17:
    the coverage job sat ~35 min on an apt step that normally takes seconds, while the identical job
    had passed in 3m15s minutes earlier on the same branch. Cancel the run and
    `gh run rerun <id> --failed`. Check the job's *step* states before assuming your change broke it.
19. **`ci_local.sh`'s verdict and its own summary sometimes disagree.** On 2026-08-15 it printed
    `LOCAL CI FAILED` alongside `failed: 0`; on 2026-08-17 the two agreed (`Local CI passed` /
    `failed: 0`). So the contradiction is **intermittent**, which is worse than consistent — it means
    a green local run is not self-evidently trustworthy. Reconciling the two numbers is Issue 498
    item 4, still open.

20. **An empty psql result is NOT an empty table — RLS is `FORCE`d on tenant tables.** Querying
    `preference_models` on the prod box as `creatorclip` returned **zero rows**, which reads as "no
    trained model exists". The table has **5**. RLS is `ENABLE`d *and* `FORCE`d and the query ran
    without `app.creator_id` set, so the policy correctly returned nothing. This nearly became a
    false finding in the 2026-08-19 closure audit. When reading a tenant table by hand, either set
    the GUC or query through `db.tenant_session` — and treat an empty result as "unproven", never as
    "empty". (Incidentally the cleanest live confirmation that tenant isolation works.)

21. **`git add -A` at commit time will silently merge two issues into one commit.** Happened twice in
    one day — the 522 commit swallowed the 500 work, the 524 commit swallowed 525 — and both needed
    `git reset --soft HEAD~1` and a re-split. If a change covers two issues, stage explicitly by
    path. The commit messages are the durable record of *why*; one commit describing two unrelated
    fixes destroys that.
---

## POINTERS

| Doc | What it owns |
|---|---|
| `docs/GO_LIVE.md` | **The canonical launch scorecard** — Stage A/B gates, owners, evidence. Start here. |
| `docs/BRANCHING.md` | Branch model + the exact protection JSON (re-run to restore) + Flake Policy. |
| `docs/PROJECT_STATE.md` | Session log + what's done/in-flight. |
| `docs/issues.md` | The work queue (Wave × Lane × Batch). **Sole authority for the next free issue number** — it is stated once, at the end of Lane L30. This file and `PROJECT_STATE.md` used to carry stale copies (497 and 443 against the real 498); both are removed. Do not reintroduce one here. |
| `docs/DECISIONS.md` | Every deviation + why. Branch-model rationale is the 2026-08-15 entry; the seven audit decisions are dated 2026-08-17. |
| `docs/assessment/DEEP_AUDIT_2026-08-17/` | The 2026-08-17 standards + process audit. `REPORT.md` first. `DECISIONS_DRAFTS.md` holds **16 still-unapproved drafts** — approve each at its paired L30 issue's CHECK phase, never in bulk. |
| `docs/OFF_COURSE_BUGS.md` | Incidental defects found while doing something else. |
| `docs/RUNBOOKS.md` · `docs/SECRETS.md` · `docs/EDGE_SECURITY.md` · `docs/ACCESS.md` | Operator procedures for §A. |
| `docs/SOT.md` · `docs/COMPLIANCE.md` · `docs/CLIPPING_PRINCIPLES.md` | Architecture, ToS/retention posture, named principles. |
| `docs/STAGING_ACCESS.md` | The staging **stack** runbook (branch-independent — still valid). |
| `~/.claude/ISSUES_LOG.md` | Cross-project root causes (search before debugging). |
| `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` | Session memory index. |
