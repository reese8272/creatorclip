# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-08-13 · **Branch:** `main` @ `592893f` · working tree **clean**, 0 open PRs.
**Prod:** `https://autoclip.studio`, alembic **`0062 (head)`** — deploy for `592893f` succeeded 17:56 UTC.
**Remote branches: `main` + `staging` only, and `main == staging == 592893f`.**
**⚠️ `main` and `staging` are now BRANCH-PROTECTED** (8 required checks, linear history, no
force-push/deletion). Direct pushes to `main` are rejected — **open a PR for everything.**

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source
> of truth. Numbers here are copied from those docs; if they disagree, the docs win.

---

## CURRENT FOCUS

**Nothing is in flight.** This checkpoint was a *CI-integrity and branch-reconciliation* session, and
it is fully closed: every PR merged, deployed, and verified. The next session starts from a clean
board and picks the next product lane.

### → NEXT ACTION

1. **Decide the repo visibility question** (30 seconds, no code). The repo is **public**. Going
   private on the free plan **silently removes the branch protection just applied** — that is the
   literal 403 recorded in Issue 145: *"Upgrade to GitHub Pro or make this repository public."*
   Private also re-meters Actions minutes (public = unlimited; free private = 2,000/mo, and ~12 CI
   jobs per PR is 25–35 billed minutes). Verified safe either way: no secret was ever committed,
   `.gitignore` covers `.env`/`*.pem`/`*.key`, `docs/SECRETS.md` holds only format placeholders, and
   no self-hosted-runner workflow triggers on `pull_request`. **Recommendation: if the clip engine
   being publicly readable bothers you, go private AND buy Pro (~$4/mo); otherwise stay public.**
2. **Clear the two beta blockers, in this order** — see *BETA LOOSE ENDS* below:
   - **Billing RED** — buy the smallest minute pack on prod for real; minutes must credit.
     Everything else in Stage A is downstream of this.
   - **#28 friend smoke** — the Stage-A capstone; only worth running once billing settles.
3. **Then pick the next lane from `docs/issues.md`** (next free issue number: **484**). The audit
   lane L29 is closed; what remains is mostly *live-verification* checkboxes, not code — see
   *WHAT'S LEFT IN issues.md*.

---

## WHAT THIS CHECKPOINT DID (2026-08-13, PRs #99–#110)

**In relation to `docs/issues.md`: nothing here maps to a numbered issue.** This work was CI/tooling
integrity, discovered while reconciling branches. It is deliberately **not** filed as issues — every
item is already fixed and merged; the PRs are the record. Do not go looking for issue numbers.

### Part 1 — Branch reconciliation (the original ask)
- **Six `fix/issue-*` branches were already fully absorbed into `main`**, squash-merged via the
  integration branches (PRs #92, #93). Squash-merge rewrites history, so they never became ancestors
  and survived the auto-delete. Verified **line-by-line** that every addition had landed; every
  residual difference was `main` being *ahead* (render-env floor raised 3→7, `origin_s` refactored
  into `clip_origin_s()`, PR #94's harness fix). **All six deleted. Do not resurrect them.**
- `main`'s tip was **gated for the first time** — full 12-job CI dispatch, all green.
- `staging` fast-forwarded to `main`; they are now equal and expected to stay that way.

### Part 2 — Five CI defects, all the same shape
Every one was **a gate reporting green while not testing what it claimed.**

| PR | Defect | Why it mattered |
|---|---|---|
| #99 | `ci_local.sh` ran `pytest -m "not integration"`; a CLI `-m` **replaces** `pytest.ini`'s `addopts` instead of intersecting | Pre-push hook re-selected `render_env`/`llm_live`/`transcription_live` and failed on every dev box; `--no-verify` hid it |
| #100 | The drills' `-m scripts.drills` invocation was unpinned and `drills.py` had no `sys.path` guard | `scripts/flags.py` shadows root `flags.py`; the only defence was a docstring |
| #101 | `eval/clip-quality` posted to `context.sha` — the **merge** commit, not the PR head | Issue 265 built it *specifically* so protection could require it, and it never could. **Requiring it before this fix would have deadlocked every PR** |
| #102 | flags-flip drill raced the app's 30 s flag TTL cache | GO_LIVE Stage-A evidence for #284; could only ever have passed by luck |
| #105–#109 | rate-limit drill asserted `all([])` → `True` | See Part 4 — it was hiding a real bug |

### Part 3 — Branch protection enabled
`main` + `staging`: 8 required checks, `strict`, linear history, no force-push/deletion,
`enforce_admins: false`. The Issue-145 blocker ("needs GitHub Pro on a private repo") evaporated when
the repo went public. **#101 had to land first** or every PR would have hung on *"Waiting for status
to be reported."*

### Part 4 — The rate-limit drill, and what it was hiding
The vacuous assertion wasn't just a weak test — it was **masking a real cross-drill bug**.
`drill_spend_trip` deliberately breaches the spend cap; `record_spend` sets a cool-down key with a
**1-hour TTL**; `require_budget` then 429s **every budget-guarded route** — but the cleanup deleted
four keys and *not* that one. That 429 is indistinguishable from a rate-limit trip at the HTTP layer,
which is why it read as a rate-limit problem for three rounds. It also made an earlier run log
`flags-flip: re-enabled -> 429. PASS`, because #102's predicate accepted "not 503" as success.

Took five PRs: #105 (the vacuous assertion) → #106 (per-drill `asyncio.run` vs the loop-bound Redis
singleton) → #107 (**my error** — asserted the daily 60 instead of the binding `20/hour` burst, which
would have failed a *healthy* stack) → #108 (the actual cool-down leak) → #109 (suite clean-start).

### Part 5 — Docs + scorecard brought in line
- `GO_LIVE.md`: **#284 and #290 CODE-GREEN → GREEN**, and the W1/W2 staging-verify residuals row
  **OPEN → GREEN**, all on drills run `31727428785`. Stage-A totals recounted from the rows
  (the stated tally had drifted).
- `BRANCHING.md` / `PROJECT_STATE.md`: the "needs GitHub Pro" claim corrected.
- `OFF_COURSE_BUGS.md`: rate-limit entry OPEN → ✅ FIXED.
- Root causes logged as **ISSUE-2026-08-13-01/-02** in `~/.claude/ISSUES_LOG.md`.

---

## WHAT WORKS NOW (verified — do not re-investigate)

- **`main` @ `592893f` is green on all 12 CI jobs and deployed to prod.** `main == staging`.
- **The staging drills genuinely pass** (run `31727428785`), with substantive output on every leg:
  ```
  flags-flip: re-enabled -> 202. PASS
  spend-trip: manual reset restored the flag. PASS
  rate-limit: 20 cheap 404 probes then 429 at request #21 (binding limit=20). PASS
  ```
  That third line is the one that used to be a lie.
- **The pre-push hook works and is installed** — `.githooks/pre-push` with `core.hooksPath` set (look
  there, **not** `.git/hooks/`, which is why an older note said it was missing). It runs ruff, mypy,
  bandit, vitest, the frontend build and the unit lane; it cannot run the Docker-only gates.
- **Coverage gates really do run** (Issue 479's fix holds): `coverage ok 84.8`, `module_coverage ok`
  with real per-module rates vs floors, `diff_cover ok`.
- **Lane L29 (Issues 456–482) is built and merged** — PRs #85–#98, including all five SEV1s.
- **Security posture of the public repo is verified** — see NEXT ACTION 1.

---

## BETA LOOSE ENDS (Stage A — `docs/GO_LIVE.md` is canonical)

**Stage A: 33 gates — 18 GREEN · 4 CODE-GREEN · 10 OPEN · 1 RED.** The beta is in good shape; the
honest framing is that what remains is almost entirely **operator actions and live proof, not code.**

**The two that actually gate inviting friends:**
1. 🔴 **Billing — a real purchase has never settled on prod.** Code shipped (Issue 21 packs, 206
   webhook verify, 205 ledger reconcile, 290 spend guard) and the 10-week `HTTPXClient` outage is
   fixed, but the gate stays RED until a real pack purchase credits minutes. **Everything else is
   downstream of this.**
2. ⬜ **#28 friend smoke** — the Stage-A capstone: full pipeline on prod with real friends for 48 h.
   Only worth running after billing settles.

**Operator chores, no code (each ~minutes):** rotate the exposed Anthropic key; Cloudflare edge rate
limit on `/auth/*` (#286); escrow `TOKEN_ENCRYPTION_KEY`/`JWT_SECRET_KEY`/`.env` off-box (#255); set
`MAILING_ADDRESS` (#246 — lifecycle email is intentionally **skipped** until then, which is the
correct fail-safe, not a bug); nightly PG backup bucket + restore drill (#256/#257); R2 Object Lock
(#258); Redis durability drill (#288); status page/uptime (#282 — **deferred by owner call**, not a
blocker); billing + LLM-cost alerts.

**External, not ours to schedule:** Google OAuth verification (#29) — the one true long pole for
*public* launch, irrelevant to a private beta.

**Stage B (public launch): 9 gates, all OPEN** — including load profile (#261), reversible-migration
CI (#296), SLOs (#236), key-rotation dry run (#30 AC), final security review, `ALLOWED_ORIGINS`/`/docs`
re-verify (#24), and pricing beyond minute packs. **None of these gate the beta.**

---

## WHAT'S LEFT IN `docs/issues.md`

**417 checked · 115 unchecked · next free number 484.** The unchecked count overstates the work:
most are **acceptance-criteria checkboxes inside already-shipped issues**, and the majority need a
*live upload* rather than code.

- **L29 (456–482)** — built and merged. 5 stray unchecked ACs are Issue-442 leftovers (background
  style removal) that the code already satisfies; tidy on next pass.
- **L28 (448–449)** — the billing live-proof ACs (same blocker as above) and one overlay-band
  re-render check.
- **L27 (444–447)** — triage UI shipped; remaining ACs are live-verification on a real upload.
- **L26 (414–426)** — camera-region + overlay ACs pending a frame-extraction spot-check; **363**
  (caption text editing) and **376(b)** (no-auth demo) are **deliberately parked/descoped**,
  reversible. **381** (chat-density) is genuinely open, size L, needs external verification.
- **Pattern:** almost everything left is *"upload one real video and check N things at once."* One
  good fresh-upload session would clear a large fraction of it.

---

## THE ARC THAT LED HERE

1. Prior sessions: full clipping-integrity audit → Lane L29 filed (456–482) → **built and merged**
   across PRs #85–#98.
2. This session opened with "merge the slew of branches, sync staging, fix any CI issues."
3. The branches turned out to be **already merged** (squash-merge artifacts) — verified, deleted.
4. Gating `main`'s tip for the first time exposed that the **local** gate was broken, and pulling
   that thread found four more gates that reported green without testing anything.
5. Branch protection was enabled once `eval/clip-quality` was fixed enough to be requirable.
6. The last thread — the rate-limit drill — turned out to be masking a genuine cross-drill
   contamination bug (the leaked spend cool-down), which took five PRs to unwind.

---

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Repo | `github.com/reese8272/creatorclip` — **PUBLIC**, `main` + `staging` both protected |
| Prod | `https://autoclip.studio` · VM `ssh creatorclip-vm` (standing permission), `/opt/autoclip`, docker-compose, Cloudflare tunnel |
| Prod containers | `autoclip-app-1`, `autoclip-worker-1`, `autoclip-beat-1`, `autoclip-render-worker-1` |
| Staging | compose project **`ccstage`** on the same VM — *not* the `staging` branch, which drives nothing |
| Deploy chain | PR → merge to `main` → Docker publish → staging gate → Deploy to production |
| Alembic head | `0062_delete_masking_skip_feedback` |
| Drills evidence | staging-drills run **31727428785** (all three legs green, 2026-08-13) |
| Seeded staging creator | `00000000-1111-2222-3333-444444444444` (`_STAGING_CREATOR_ID` in `scripts/drills.py`) |
| Eval floor | `SCENARIO_FLOOR=31`, 32 fixtures (`tests/test_clip_engine.py`) |
| Secrets | by name only — `TOKEN_ENCRYPTION_KEY`, `JWT_SECRET_KEY`, `ANTHROPIC_API_KEY`, `DEEPGRAM_API_KEY`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`. Live in VM `.env` + GitHub Secrets. **Never committed; never printed.** |

### Local gate incantations
```bash
export PATH=/home/reese/.nvm/versions/node/v22.17.1/bin:$PATH && hash -r   # node 26 = 35 phantom jsdom fails
redis-cli ping || redis-server --daemonize yes --save '' --appendonly no
# backend:  .venv/bin/python -m pytest -p no:langsmith -q     # BARE — never pass -m
# frontend: from frontend/ — npx vitest run AND npm run build (tsc -b type-checks TESTS)
# Layer 0:  PATH="$PWD/.venv/bin:$PATH" .venv/bin/python .claude/skills/production-assessment/scripts/run_layer0.py
# NOTE: .githooks/pre-push runs most of the above automatically on every push.
```

---

## CONSTRAINTS & GOTCHAS

- **`main` is protected — open a PR.** A direct push is rejected. `strict: true` means a PR goes
  `BEHIND` the moment anything else merges; fix with `gh pr update-branch --rebase <N>`.
- **`enforce_admins: false` is load-bearing, not laziness.** A `main` commit carries only the four
  push-triggered deploy checks and **none** of the 8 required contexts, so
  `git push origin origin/main:staging` lands *solely* via admin bypass. Flipping it to `true`
  silently breaks the staging sync.
- **Never pass `-m` to pytest** in a script, hook, or by hand. It *replaces* `pytest.ini`'s `addopts`
  and drags in lanes needing real ffmpeg/mediapipe/API keys. (ISSUE-2026-08-13-01.)
- **On a `pull_request` event, `context.sha` is the merge commit, not the head.** Anything written
  keyed by commit must use `context.payload.pull_request?.head?.sha`. (ISSUE-2026-08-13-02.)
- **Drills mutate live staging state.** They now clean up after themselves, but a run killed
  mid-flight can still leave a 1 h spend cool-down; `_reset_creator_throttles()` clears it at suite
  start. The drills clear the whole `LIMITS:*` namespace — safe on single-tenant `ccstage`, **never
  acceptable against prod**.
- **`health-check.yml` is red and that is expected** — dispatch-only, and probing the
  Cloudflare-fronted prod URL from a GitHub runner returns a 403 challenge by design. Uptime is owned
  by Cloudflare Health Checks. Don't "fix" it.
- **A failed image build silently SKIPS deploy** — check `gh run list` after every merge.
- Stripe transport must stay `RequestsClient`; **never** revert to `HTTPXClient` (two stacked
  defects, 10-week outage — `billing/stripe_client.py`, DECISIONS 2026-08-12).

---

## POINTERS

| Doc | What it owns |
|---|---|
| `docs/GO_LIVE.md` | **The go/no-go scorecard — canonical for beta readiness** |
| `docs/issues.md` | Work queue, lanes L26–L29, next free **484** |
| `docs/SOT.md` | Stack, architecture, file structure |
| `docs/PROJECT_STATE.md` | Progress log |
| `docs/DECISIONS.md` | Deviations + rationale |
| `docs/BRANCHING.md` | Branch model + the applied protection ruleset |
| `docs/OFF_COURSE_BUGS.md` | Incidental-defect log |
| `docs/COMPLIANCE.md` · `docs/CLIPPING_PRINCIPLES.md` | ToS/retention · named principles registry |
| `docs/RUNBOOKS.md` · `docs/DEPLOYMENT.md` · `docs/STAGING_ACCESS.md` | Ops |
| `~/.claude/ISSUES_LOG.md` | Cross-project root causes — **grep before debugging** |
| `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` | Session memory index |
