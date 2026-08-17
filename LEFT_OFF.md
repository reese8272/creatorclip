# LEFT_OFF.md — CreatorClip / AutoClip Session Handoff

**Last updated:** 2026-08-17 · **Branch:** `main` @ `1def133` · **Working tree:** ⚠️ **DIRTY — the
2026-08-17 audit's output is uncommitted.** Changed: `docs/issues.md` (Lane L30 added),
`docs/DECISIONS.md` (7 entries), `docs/PROJECT_STATE.md`, `LEFT_OFF.md`, plus the untracked
directory `docs/assessment/DEEP_AUDIT_2026-08-17/`. **No source, test or config file was touched** —
verify with `git status --porcelain | grep -vE '^(\?\? docs/assessment/| M (docs/|LEFT_OFF\.md))'`,
which should print nothing. ⚠️ **Commit before doing anything destructive** — the audit directory is
**untracked**, so a `git clean` would delete ~11,000 lines of it. Use a `feature/*` branch and a PR;
`main` is protected with no bypass.
**Suite at handoff:** `.venv/bin/python -m pytest -q` → **3170 passed, 64 skipped**.
**Prod:** `https://autoclip.studio`. **Running commit NOT verified this session** — it was `c260689`
on 2026-08-15, and `1def133` (PR #118) merged after that, which should have deployed. Confirm before
trusting it:
`docker inspect autoclip-app-1 --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'`

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

**Durability first, then the two SEV1s the audit found.** 2026-08-17 was a read-only deep standards
and process audit — **no product code changed.** It confirmed the friend-beta track below is still
the right shape, and found one product defect serious enough to jump the queue. The owner's chosen
order for the next session is **backups → personalization → the Week-1 conversions.**

### → NEXT ACTION

1. **Arm backups — operator track §A, starting with Issue 255 (secrets escrow).** Unchanged as the
   #1 item, and now with a recorded target behind it: `docs/DECISIONS.md` 2026-08-17 commits to
   **RPO 24 h / RTO 4 h with a quarterly drill**, which gives this work a definition of done.
   Walked today, losing the droplet means **total loss** of the billing ledgers, `preference_models`
   (the trained taste — irreplaceable, it *is* the product), `creator_dna`, `clip_outcomes` and the
   consent records. R2 media survives; the database that indexes it does not. One genuinely missing
   config key the audit found: **`BACKUP_HEALTHCHECK_URL`** (the dead-man's switch, consumed by
   `scripts/backup_pg.sh` and `scripts/backup_redis.sh`) is absent from `.env.example`.
2. **Issue 521, then Issue 520 — in that order.** `docs/issues.md` Lane L30 Batch G.
   **520:** LightGBM's untouched `min_child_samples=20` makes the preference model a **constant
   predictor for label counts 21–40** — exactly the band the maturity ramp covers — so
   `blended_score` is a monotone transform of `score`, the persisted order is byte-identical to
   DNA-only, and the API still returns `personalization={active: true}`. Against the north star that
   is an honesty defect, not just a correctness one.
   **521 first, because the gate lies.** The rerank eval written on 2026-08-13 to guard exactly this
   passes only because its fixture is the single 40-row shape LightGBM can split. Fix the gate first,
   so the fix to 520 has something that can actually see it.
3. **Issue 498 — the six Week-1 conversions (~1.5 h total).** Batch A. Start with the ten-minute one:
   `"test:ci": "vitest run --reporter=verbose"`. The flake it closes has cost three sessions, each of
   which ended by writing down the same advice and not applying it.

**Then** resume the beta track: **#29 (Google OAuth verification)** is still the only item with a
clock you cannot compress (1–4 weeks external review) — read the scope warning in §D before
submitting — and **Issue 445** (three-pile triage UI) still needs a real CHECK phase; re-read
`docs/issues.md` Lane L27 first, four design questions are open and the AC numbering has drifted.

*Nothing is blocked. §B lists deferred code items if you want a quick win instead.*

### → BEFORE YOU START: read these three, in this order

1. `docs/assessment/DEEP_AUDIT_2026-08-17/REPORT.md` — the verdict and the reading rules below.
2. `docs/assessment/DEEP_AUDIT_2026-08-17/SYNTHESIS_process.md` — the mechanism map + 90-day plan.
3. `docs/issues.md` Lane **L30** (Issues 498–527), eight batches.

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

## WHAT WORKS NOW (verified — do not re-investigate)

### Repo hygiene + branch protection (2026-08-15, this session)

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
5. **2026-08-17 (this session)** — owner asked *why the project keeps hitting one small snag after
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
| Deployed commit | `c260689` (confirmed from the container's image revision label) |
| Image | `ghcr.io/reese8272/creatorclip:latest` (deploys resolve `sha-<7char>` for the staging gate) |
| Branch model | **trunk-based**: `feature/* → PR → main`. No `staging` branch. Rebase/squash only. |
| Deploy chain | push to `main` → **Docker publish** → (`workflow_run`) → **Deploy to production** → data-bearing staging gate → prod smoke + auto-rollback |
| Local test env | **Use `.venv/bin/python`** or `scripts/ci_local.sh` — see gotcha 1 |
| Node | 22.17.1 (`.nvmrc`); node 26 breaks jsdom |
| Secrets (names only) | `TOKEN_ENCRYPTION_KEY`, `JWT_SECRET_KEY`, `ANTHROPIC_API_KEY`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `BACKUP_ENCRYPTION_KEY` — in VM `/opt/autoclip/.env` + GitHub secrets. **Never read or print values.** |

---

## CONSTRAINTS & GOTCHAS

1. **⚠️ RUN TESTS FROM `.venv`, NOT SYSTEM PYTHON.** This has now burned two consecutive sessions.
   System `python3.12` has **fastapi 0.115.4** against the pinned **0.137.1**, and its `mypy` cannot
   import the `pydantic` plugin — so mypy aborts before checking anything and Layer 0 reports a
   **vacuous `ok 0`**. Under system python the suite once showed 4 phantom failures and pip-audit 77
   phantom CVEs; under `.venv` it is **3170 passed / 0 failed**, all gates 0. Use
   `scripts/ci_local.sh --fast` (it prefixes `PATH` with `.venv/bin`). *This bit again on 2026-08-15 —
   results happened to hold, but they were not trustworthy as first reported.*
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
13. **`--require` on `run_layer0.py` does NOT make its gates honest** (audit 2026-08-17, reproduced).
    Four of the eight gates infer their result purely from stdout and never check `proc.returncode`,
    so a tool that runs and *fails* scores `{"status":"ok","value":0}` — a perfect score against the
    strict baseline of 0. `--require` only escalates a `skipped` status, so it cannot see this at all.
    **Fix the returncode check (Issue 499) before adding `--require` (Issue 500)** — doing it in the
    other order buys a false sense of coverage. Most plausible live consequence today: pip-audit
    reporting "0 vulnerabilities" during a PyPI/OSV outage, on a required check.
14. **A gate whose scope is a list literal is a vacuous-green generator by construction.** The audit
    censused 101 module-level literals in `tests/` + `scripts/`, diffed the ~20 that define a gate's
    scope, and **11 had drifted** — two covering live defects. The house rule is now **"scan, don't
    list"**; the model to copy is `tests/test_usage_coverage.py` (real AST discovery with a
    bidirectional staleness check). Applies to RLS tables, LLM route registries, ffmpeg task routing,
    and `run_layer0.py`'s own source list.
15. **The limiter posture is decided and the code does not implement it yet.** `docs/DECISIONS.md`
    2026-08-17: the **limiter degrades to a local in-memory bucket**, the **spend guard fails
    CLOSED**, and every Redis client gets bounded socket timeouts. Today the limiter fails *closed*
    with an unhandled **HTTP 500** on every rate-limited route including `GET /auth/me` — so a Redis
    latency spike is a silent total sign-in outage that `/health` reports as `200 ok`. Issue 522.
    Do not "fix" this from the three stale documents that still describe the old fail-open claim.

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
