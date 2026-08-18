# LEFT_OFF.md — CreatorClip / AutoClip Session Handoff

**Last updated:** 2026-08-18 · **Branch:** `main` @ `70ee4fa` · **Working tree:** ✅ **CLEAN**, in
sync with `origin/main` (0 ahead / 0 behind). All CI green; the nightly LLM E2E passed at 03:48 UTC
**after** these merges, so the live-LLM path survived them.

**Four PRs shipped 2026-08-17, all merged, deployed and live-verified:** **#119** (the audit itself +
Issue 498 items 1–3), **#120** (Issue 521 — the lying gate), **#121** (Issue 520 — the
personalization SEV1), **#122** (this handoff). History on `main` is linear.

**Suite at handoff:** `.venv/bin/python -m pytest -q` → **3225 passed, 64 skipped, 0 xfailed**
(3170 before the 2026-08-17 work; 3199 → 3212 → 3225 across the three 2026-08-18 PRs).

**Prod:** `https://autoclip.studio`. **Running commit VERIFIED** — the container's image revision
label reads `70ee4fa7663b94e018cd8fdf3f9f5a3ab2b26b5e`, and `/health` returns 200 over the public
URL with all containers healthy. Re-confirm the same way after any deploy:
`docker inspect autoclip-app-1 --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'`
*(Note: `curl localhost:8000/health` from inside the VM returns `000` — the app is behind
cloudflared, so check the public URL, not localhost. That is not an outage.)*

**Live-verified beyond the label** (per gotcha 4 — a green pipeline is not a working feature). Against
the prod DB under a real `tenant_session`, the owner's scorer now reports:
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

**Durability is now the only thing left on the critical path.** The owner's chosen order was
**backups → personalization → the Week-1 conversions**; the second and third have shipped, so
backups stand alone at the front with nothing ahead of them and no code excuse to defer behind.

The 2026-08-17 audit itself was read-only. Everything it found that jumped the queue —
**Issue 521** (the gate that certified the wrong property) and **Issue 520** (personalization was a
measured no-op across its entire ramp while the API reported it active) — is fixed, merged, deployed
and live-verified on the running container. Lane L30's process track (Batches B–H) is untouched and
is the natural next code work; **Issue 499** is its highest-value entry.

### → NEXT ACTION

1. **Arm backups — operator track §A, starting with Issue 255 (secrets escrow).** Now the **#1 item
   with nothing ahead of it**, and the only thing on the critical path that nobody but the owner can
   do. It has a recorded definition of done: `docs/DECISIONS.md` 2026-08-17 commits to **RPO 24 h /
   RTO 4 h with a quarterly drill**. Losing the droplet today means **total loss** of the billing
   ledgers, `preference_models` (the trained taste — irreplaceable, it *is* the product),
   `creator_dna`, `clip_outcomes` and the consent records. R2 media survives; the database that
   indexes it does not. ✅ The missing **`BACKUP_HEALTHCHECK_URL`** key (the dead-man's switch, read
   by `scripts/backup_pg.sh` + `scripts/backup_redis.sh`) is now in `.env.example` — set it when you
   arm the cron, or a cron that stops firing is invisible.
2. ~~**Issue 521, then Issue 520.**~~ ✅ **BOTH DONE 2026-08-17** (PRs #120, #121 — merged, deployed,
   live-verified). The rerank eval now covers eight creator shapes instead of the one knife-edge it
   could pass; the LightGBM switchover is a separately measured constant (`PREFERENCE_LGBM_MIN_LABELS
   = 60`, floor 41, validated at boot); and `effective_weight()` is the single producer of the blend
   weight, so `active` cannot claim personalization the reranker did not apply. Details in
   `docs/DECISIONS.md` and `docs/issues.md` §520/§521.
   ⚠️ **Expect one warn-only NDCG-ratchet event per affected creator** on their first retrain after
   this deploy — the served model genuinely changes shape. That is the ratchet working; do not chase
   it as a regression.
3. **Issue 498 — items 4 and 5 remain** (items 1–3 and 6 are done; PR #119). Deliberately deferred,
   not forgotten: **item 4** (`ci_local.sh` seed echo + the `failed: 0` vs `LOCAL CI FAILED`
   contradiction) needs the two numbers *reconciled* rather than patched — a diagnosis, not a
   one-liner. **Item 5** (promoting `Migration lint (Squawk)` + `Visual regression` to required)
   mutates branch protection and wants its own change with a readback.
4. ~~**Issues 499, 500 and 522**~~ ✅ **ALL DONE 2026-08-18.** Layer-0 gates verify their tool ran and
   every invocation now carries `--require`; a Redis blip is no longer a total sign-in outage.
   See gotchas 13 and 15 for the facts worth carrying forward from each.
   **The recommended next CODE work is Batch C** ("scan, don't list", Issues 505–507) — the
   next-densest value, since 11 of ~20 hand-maintained gate-scope literals had already drifted, two
   of them covering live defects. Note **505** also carries two tenant tables with no RLS policy and
   no recorded exemption, and **506** four LLM routes missing a burst or daily cap.
   Batch B's remainder (**501–504**) is the cheaper alternative: 503 in particular is a weekly
   mutation gate that has never executed a single mutant across 8 green runs.

**Then** resume the beta track: **#29 (Google OAuth verification)** is still the only item with a
clock you cannot compress (1–4 weeks external review) — read the scope warning in §D before
submitting — and **Issue 445** (three-pile triage UI) still needs a real CHECK phase; re-read
`docs/issues.md` Lane L27 first, four design questions are open and the AC numbering has drifted.

**One loose end from the 520 work:** the personalization-*active* path has never been exercised on
prod, because the owner has 10 labels and it needs ≥21. If you rate ~12 more clips, that closes the
last unverified claim in this fix — and it is also the cheapest way to see the new behaviour live.

*Nothing is blocked. §B lists deferred code items if you want a quick win instead.*

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

### A. Operator track — the real critical path (nobody but the owner can do these)

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
| Deployed commit | `70ee4fa` (confirmed 2026-08-17 from the container's image revision label; `/health` 200 over the public URL) |
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
18. **CI jobs occasionally hang in `Install system deps` (apt), not in your code.** Seen 2026-08-17:
    the coverage job sat ~35 min on an apt step that normally takes seconds, while the identical job
    had passed in 3m15s minutes earlier on the same branch. Cancel the run and
    `gh run rerun <id> --failed`. Check the job's *step* states before assuming your change broke it.
19. **`ci_local.sh`'s verdict and its own summary sometimes disagree.** On 2026-08-15 it printed
    `LOCAL CI FAILED` alongside `failed: 0`; on 2026-08-17 the two agreed (`Local CI passed` /
    `failed: 0`). So the contradiction is **intermittent**, which is worse than consistent — it means
    a green local run is not self-evidently trustworthy. Reconciling the two numbers is Issue 498
    item 4, still open.

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
