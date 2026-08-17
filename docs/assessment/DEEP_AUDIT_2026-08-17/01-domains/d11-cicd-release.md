# D11 — CI/CD and release process

**Audit:** DEEP_AUDIT_2026-08-17 · Phase 1 domain report
**Scope:** `.github/workflows/*`, `.githooks/pre-push`, `scripts/ci_local.sh`, branch protection,
deploy pipeline, release/versioning.
**Ground truth consumed:** `00-groundtruth/process-map.md` §§1, 2, 5, 8; `snag-taxonomy.md` Classes 1
and 9; `architecture-map.md` §A1. Decisions checked before filing: `DECISIONS.md:8` (trunk-based,
08-15), `:5226` (Issue 145 two-tier model), `:3163` (hybrid local+self-hosted CI), `:11659`
(Issue 297 CalVer), `:11482` (Issue 271 auto-rollback), `:12558` (Issue 360 runner split).
Also read `AUDIT_BRIEF.md` §§5, 9 and `AUDIT_KNOWN_ISSUES.md` §§C, E.

---

## Verdict

The *pipeline architecture* here is better than the project's own reputation suggests: the required
set is well chosen, the runner trust boundary (Issue 360) is correct, the data-bearing staging gate
is genuinely above standard for this scale, and the `strict: true` + linear-history + no-push-trigger
combination is sound rather than the hole it looks like. The problems are not in the shape of the
pipeline — they are in **three gates that report success without doing their job**, which is this
repo's named #1 failure class showing up in the delivery tooling itself. The single most valuable
change available is also the cheapest: three of the four advisory checks already run on every PR,
already finish 1.5–3.5 minutes *before* the required set closes, and cost **zero** additional PR
latency to promote to required.

---

## What the current (2026) standard is, with sources

**1. Trunk-based delivery for very small teams.** The 2025/2026 consensus is unchanged in shape and
sharpened by AI: short-lived branches into a protected trunk, small batches, and automated gates that
fail closed. DORA's 2025 *State of AI-assisted Software Development* found AI raises throughput while
**also raising instability where the delivery foundation is weak**, and named code review as the
bottleneck that AI makes *more* acute rather than less — teams author 10–100× more code without any
matching increase in review capacity. Its explicit countermeasures are (a) working in small batches
and (b) **shifting automation to the author phase** so agents enforce standards before a human looks.
([DORA 2025 year in review](https://dora.dev/insights/dora-2025-year-in-review/);
[DORA — Balancing AI tensions](https://dora.dev/insights/balancing-ai-tensions/);
[Faros summary](https://www.faros.ai/blog/key-takeaways-from-the-dora-report-2025);
[Jellyfish interview with lead author](https://jellyfish.co/blog/2025-dora-report/))

**2. Review substitutes when there is no second human.** GitHub itself now treats one-person approval
as *not a control*: the "prevent self-review" setting for deployment environments exists precisely
because an approver who is also the initiator is not a review
([GitHub changelog, prevent self-reviews](https://github.blog/changelog/2023-10-16-actions-prevent-self-reviews-for-secure-deployments-across-actions-environments/);
[Reviewing deployments](https://docs.github.com/actions/managing-workflow-runs/reviewing-deployments)).
The 2026 substitute that has actually been adopted is an automated reviewer wired as a PR check —
over 1.3M repos now run at least one AI code-review integration, and both
[`anthropics/claude-code-action`](https://github.com/anthropics/claude-code-action) and
Copilot code review support running on every PR. The current guidance is that AI review is *advisory
until you put it in the required-status list*, and that a solo maintainer should be careful about
making the human-approval slot agent-on-agent
([kunalganglani.com — AI code review in CI/CD, 2026](https://www.kunalganglani.com/blog/ai-code-review-github-actions);
[aibuilderclub — reviewing AI-generated PRs, 2026](https://www.aibuilderclub.com/blog/reviewing-ai-generated-pull-requests)).

**3. Merge queues.** Standard advice in 2026 is that a merge queue is a throughput tool, not a safety
tool, and that most teams reaching for one actually needed *"code tested on top of `main` before it
merges — which rebase-before-merge does with less overhead than a queue."* GitHub's queue also has no
concept of a flaky test: a flaky failure is indistinguishable from a real one and jams the queue
([Mergify — when to outgrow GitHub's merge queue](https://mergify.com/blog/when-to-outgrow-github-merge-queue);
[Tenki — merge queue in 2026 and flaky required checks](https://tenki.cloud/blog/github-merge-queue-setup)).

**4. Hook placement.** The current rule of thumb is explicit: **pre-commit under ~10 s, pre-push under
~2 minutes, everything slower belongs in CI**; and pre-push is the accepted compromise for developers
who commit frequently as a save-point habit
([iotools.cloud — pre-commit, pre-push and stopping bad code at the door](https://iotools.cloud/journal/git-hooks-pre-commit-pre-push-and-stopping-bad-code-at-the-door/);
[Pi Stack — pre-commit vs Lefthook vs Husky, 2026](https://www.pistack.xyz/posts/2026-04-26-pre-commit-vs-lefthook-vs-husky-git-hooks-management-guide-2026/);
[BigGo — the pre-commit hook debate](https://biggo.com/news/202510210735_Git-Pre-Commit-Hook-Debate)).

**5. Local-gate failure semantics.** The standard for a script that may not be able to run a check is
to **decide the failure mode explicitly and encode skip as a distinct outcome** — fail closed for
security/verification gates, fail open only for advisory ones, and never let "could not run" share an
exit code with "ran and passed." The kselftest convention (exit 4 = skipped, distinct from 0 = passed)
is the canonical precedent
([Dojo Five — CI scripts and exit codes](https://dojofive.com/blog/how-ci-pipeline-scripts-and-exit-codes-interact/);
[dev.to — your bash CI scripts are a ticking time bomb](https://dev.to/ericwoooo_kr/your-bash-ci-scripts-are-a-ticking-time-bomb-heres-what-to-use-instead-3b7c);
[kselftest skip exit code](https://lkml.iu.edu/2601.3/01147.html)).

**6. Deployment strategy at single-VM scale.** The explicit 2026 guidance is *not* to reach for canary:
"most teams overshoot — they reach for Kubernetes and canary when an atomic symlink and a five-line
health check would have solved the problem"; rolling + solid health checks is described as already
sufficient for a five-person SaaS monolith, with canary introduced only as cadence and team size grow
([Koyeb — blue-green, rolling and canary explained](https://www.koyeb.com/blog/blue-green-rolling-and-canary-continuous-deployments-explained);
[Statsig — rolling vs canary](https://www.statsig.com/perspectives/canary-vs-rolling-continuous-deployment-strategies)).

**7. Release automation.** Two accepted shapes: fully automatic tag-on-every-qualifying-push
(semantic-release) or a Release-PR that batches (release-please), with release-please now the more
commonly recommended default because it gives a draft state instead of releasing on every push
([semantic-release FAQ](https://semantic-release.org/support/faq/);
[Why I swapped semantic-release for release-please](https://blog.hazya.dev/why-i-swapped-semantic-release-for-release-please);
[DevOpsil — automated semantic versioning, 2026-03](https://devopsil.com/articles/2026-03-21-semantic-versioning-automated-releases)).
Both share one non-negotiable: **the released artifact can state its own identity**, and every push to
main that ships produces a distinct one.

---

## Findings

### F1 — CalVer release automation has been a silent no-op for 636 commits, and the running app cannot state its own version *(HIGH)*

Issue 297 (`DECISIONS.md:11659`) decided three things: CalVer `YYYY.MM.patch` in `pyproject.toml`;
`/health` exposes it via `importlib.metadata`; `docker-publish.yml` auto-creates a Git tag + GitHub
Release on every push to main so the `type=semver` metadata rule tags the image. **All three are
dead.**

Evidence, all verified today:

| Claim | Reality |
|---|---|
| CalVer bumps and tags each release | `git ls-remote --tags origin` → **exactly one tag**, `v2026.6.0`, pointing at `01afa46` (2026-06-23). `git rev-list --count v2026.6.0..origin/main` = **636**. |
| A GitHub Release fires the `type=semver` image tag | `gh release list` → **empty**. The `release: published` trigger in `docker-publish.yml:5-6` has never fired; no image has ever carried a CalVer tag. |
| `/health` reports the live version | `curl https://autoclip.studio/health` → `{"status":"ok",…,"version":"dev"}` |
| `pyproject.toml [project].version` self-bumps on month roll | still `2026.6.0` (`pyproject.toml:6`) in mid-August |

Two independent root causes, both of the house failure shape:

1. **`Dockerfile` never installs the project as a package** — it `COPY . .` (Dockerfile, runtime
   stage) with `PYTHONPATH=/app`. So `importlib.metadata.version("creatorclip")` (`main.py:83`) always
   raises `PackageNotFoundError` and falls through to `"dev"` (`main.py:85`). The `/health` version
   field has never reported a real version **in any environment**, including local.
2. **Every failure in the tag/release step is swallowed by `|| echo`** (`docker-publish.yml:96`, `:99`,
   `:106`). The tag step prints `Tag v2026.6.0 already exists — skipping tag creation.` on every one of
   ~600 pushes and exits 0. `gh release create` has evidently never succeeded even once (zero releases
   exist despite the tag existing), and its failure is likewise swallowed. The decision entry
   anticipated the tag collision and called it *"a documented constraint, not a silent failure"* — it
   is in fact a silent failure, because nothing bumps the patch and nothing reads the log.

There is also a **third** version string: `main.py:128` hardcodes `version="0.1.0"` on the FastAPI app,
so `/openapi.json` and `/docs` advertise 0.1.0.

**Failure scenario.** A deploy at 02:00 degrades the app. The operator opens `autoclip.studio/health`
— the documented *"what version is live"* touchpoint (`main.py:564-566`) — and gets `"dev"`. The
CalVer tag the decision promised would make *"rollback targets human-readable alongside the digest"*
resolves to a June build 636 commits stale. The only true identity is the GHCR RepoDigest captured
transiently in `PREV_IMAGE` inside a workflow run, plus `IMAGE_SHA` in the untracked `/opt/autoclip/.env`
(which is fed to Sentry as the release marker — and per `process-map.md` §6, Sentry is unverified as
live). Release identity is recoverable only by SSH-ing to the box and running `docker inspect`.

**Verdict:** deviation-unjustified — the decision was correct and the implementation reports success
while doing nothing. **Zero tests reference `__version__`, `importlib.metadata`, or the tag step**
(`grep -rl "importlib.metadata\|__version__" tests/` → no hits), including in
`tests/test_ci_config.py`, which asserts properties of every other part of this pipeline.

**Fix shape (cheapest first):** stop deriving version from package metadata — read
`pyproject.toml` at build time into a `ARG APP_VERSION` / `ENV APP_VERSION` in the Dockerfile alongside
the already-working `GIT_SHA` pattern, and report `{"version": APP_VERSION, "sha": IMAGE_SHA}` from
`/health`. Then either drop the tag/release step or make it real: remove `|| echo` on `gh release
create`, and derive the CalVer patch from the commit count since the last tag so it cannot collide.
Pin whichever you choose with a `tests/test_ci_config.py` assertion — that file is the right home and
already exists.

---

### F2 — The prod deploy's critical-journey smoke silently downgrades to a warning; the staging gate 230 lines above it cannot *(HIGH)*

`deploy.yml:365-370`:

```
if [ -z "${CC_JWT_SECRET:-}" ]; then
  echo "WARNING: CC_JWT_SECRET not set — skipping critical journey smoke."
  echo "Set the CC_JWT_SECRET secret and CC_CREATOR_ID variable in GitHub Actions."
else
  … llm_harness.py --flow core … || _rollback_and_fail
fi
```

`CC_JWT_SECRET` comes from `secrets.CC_JWT_SECRET` (`deploy.yml:318`). Nothing in the repo establishes
that it is set, and `docs/assessment/modules/deploy_ci.md:156` lists `CC_JWT_SECRET` as an
*"`.env.example` cleanup"* — i.e. it is not documented as required anywhere an operator would see.

The correct pattern is **already implemented in the same file** for the staging gate
(`deploy.yml:136`), which sources the secret from inside the container and therefore cannot be
skipped:

```
app sh -c 'CC_BASE_URL=http://localhost:8000 CC_JWT_SECRET="$JWT_SECRET_KEY" python scripts/llm_harness.py --flow core'
```

**Failure scenario.** `CC_JWT_SECRET` is unset (or rotated and not re-synced). Every prod deploy now
validates exactly one thing: `/health` returns `status: ok` five times. `/health` checks postgres,
redis and storage connectivity — it is precisely the check that stayed green through the 10-week
Stripe outage, the 7-week catalog-sync outage and the 9-day silent-prod incident (`snag-taxonomy.md`
§D). A deploy that breaks `auth_me`, `videos_list`, `dna`, `insights`, `billing_balance` or the videos
envelope shape now passes, the auto-rollback (Issue 271/295) is never armed on anything but liveness,
`docker image prune -f` runs and **deletes the rollback target**, and the run reports green.

This is the same defect class as `OFF_COURSE_BUGS:148` (*"a pre-flight doctor that green-lights a
subsystem it does not actually exercise is worse than no check"*) — sitting on the last gate before
production declares itself healthy.

**Fix:** one line. Mirror the staging invocation. If the secret genuinely cannot be sourced
in-container, `exit 1` instead of `echo WARNING` — a deploy-verification gate must fail closed.

---

### F3 — Three of the four advisory gates cost zero PR latency to make required; one of them already claims in-file to be required *(HIGH)*

Measured from four consecutive real CI runs (`gh run view … --json jobs`, runs 31908591494 /
31895047970 / 31894386424 / 31893721420):

| Job | Required? | Duration (4 runs) | Margin vs. critical path |
|---|---|---|---|
| **Integration tests (postgres + redis)** | ✅ | 3m34s–4m29s | *this is the critical path* |
| Unit tests (pytest) | ✅ | 3m10s–3m29s | — |
| Coverage floor | ✅ | 3m03s–3m11s | — |
| **Frontend (lint, test, build)** | ❌ | **1m47s–1m49s** | finishes **~2m earlier** |
| **Visual regression** | ❌ | **43s–57s** | finishes **~3m earlier** |
| **Migration lint (Squawk)** | ❌ | **20s–25s** | finishes **~3.5m earlier** |
| Flake detection | ❌ (`continue-on-error`) | 1m36s–1m48s | — |

All twelve jobs start within 3 seconds of each other and run in parallel. **Promoting `Frontend`,
`Visual regression` and `Migration lint (Squawk)` to required adds exactly 0 seconds to time-to-merge**
— they already run on every PR and already finish before the gate closes. The only real cost is that a
red or flaky one now blocks; that is the point.

Per-gate recommendation:

- **`Migration lint (Squawk)` → REQUIRE NOW, unconditionally.** 20–25 s. It carries Squawk unsafe-op
  linting, the online downgrade round-trip with byte-identical `pg_dump` diff, and
  `scripts/check_downgrades.py`. This is the *only* automated protection on the path that has already
  produced two of the five most expensive incidents in the corpus (the silent-no-op migration,
  `ISSUES_LOG.md:556`; the invalid `UPDATE … LIMIT` backfill snippet, `OFF_COURSE_BUGS:142`). Failure
  scenario today: a migration adding `ALTER TABLE clips … SET NOT NULL` merges with Squawk red, and
  takes an `ACCESS EXCLUSIVE` lock on the largest table during a prod deploy — the exact class Squawk
  exists to catch. There is no reason this is advisory; the gate was made real in
  `DECISIONS.md:12312` and then never wired to protection.
- **`Visual regression` → REQUIRE NOW.** 43–57 s, green on all four sampled runs. Its own in-file
  comment says **"GATING since 2026-07-29 (ready-pass W2)"** (`ci.yml:606-609`) while it is absent from
  the required-contexts list in `docs/BRANCHING.md:100-127`. That is a live contradiction between two
  documents that both claim to describe the gate, and it means the belief "visual regressions are
  gated" is false. Note that `OFF_COURSE_BUGS:42` ("Playwright + visual jobs always fail on the
  self-hosted runner", still marked Open) is **stale** — Issue 360 moved PR CI to `ubuntu-latest` on
  2026-07-20 and both jobs pass there. Either promote it or delete the comment; the current state is
  worse than either.
- **`Frontend (lint, test, build)` → REQUIRE, but capture the flake first.** 1m48s. 92 vitest files
  against 260 source files currently gate nothing, in a product whose two most recent honesty-inversion
  SEV-class defects (`OFF_COURSE_BUGS:22` `text-success` on an error string, `:40` fabricated waveform)
  are frontend defects. The honest caveat: `:133` → `:147` → `:157` document the same cold-first-run
  vitest flake three times, and **the failing test name was never captured on any occasion** because
  nobody remembered `--reporter=verbose`. Per the 2026 merge-queue guidance ("fix test determinism
  before tightening rules"), do the structural fix first — `frontend/package.json:10` is
  `"test": "vitest run"`; add `"test:ci": "vitest run --reporter=verbose"` and call it from
  `ci.yml:456`. That makes the fourth recurrence self-documenting, which is exactly the "convert a root
  cause into a mechanism" step `snag-taxonomy.md` §E4 says this project keeps skipping. Then require it.
- **`Mutation testing` → KEEP NON-REQUIRED. This one is right as-is.** `mutation.yml:5-9` argues the
  case correctly and the recent weekly run took 86 s, so cost is not the objection — *semantics* are.
  A mutation score is a trend metric over 3 files; gating it either red-walls unrelated PRs on
  surviving mutants or gets ratcheted down to meaninglessness. Leave it weekly and report-only.
- **`Flake detection` → KEEP `continue-on-error`.** `docs/BRANCHING.md` "Flake Policy" is correct and
  well-reasoned; rerun-as-gate is properly prohibited.

---

### F4 — `ci_local.sh` prints "Local CI passed." after skipping every gate it was asked to run *(MEDIUM-HIGH)*

Six of the script's gates degrade to `skip` + continue: `ruff` missing (`:73`, `:82`), `python3`
missing (`:93`), `pytest` missing (`:112`), **Redis down** (`:113`, `:121`), **`node_modules` absent**
(`:130`). The summary block does print a `skipped:` count (`:156`), but the terminal verdict is
`Local CI passed.` in green and `exit 0` (`:162`), and the pre-push hook `exec`s the script and
inherits that code (`.githooks/pre-push:25`).

**Failure scenario.** A fresh clone or a box where Redis is not running (the developer's own
documented situation — `MEMORY.md`: *"Docker unavailable here; run tests with python3.12 + brew
redis"*): `pytest` skipped, `coverage` skipped, the entire frontend block skipped because
`frontend/node_modules` was never installed. Two gates ran (ruff, mypy/bandit). The push proceeds and
the terminal says **"Local CI passed."** The developer's mental model — "the pre-push hook checked
this" — is now false in exactly the way `ci.yml:28-31`'s premise was false when the hook was not
installed at all (`snag-taxonomy.md` §E1). Layer 1 of the documented two-layer model reports green
having verified ~15% of what it names.

This is not a hypothetical class: `run_layer0.py`'s identical skip-is-not-failure shape is what made
per-module coverage and diff-cover no-ops for seven weeks (Issue 479) while printing *"All runnable
gates passed."* The coverage job was hardened with `--require`; `ci_local.sh:95` still invokes
`run_layer0.py --gates mypy,bandit` **with no `--require`**, so a stale `.venv` that breaks mypy
produces a green local gate too.

**Standard:** pass and could-not-run must not share an exit code (kselftest exit 4; "decide your
failure mode explicitly — fail closed for security gates"). **Fix, in order of value:**
1. Change the final banner to `Local CI: N passed, M NOT RUN (…)` — never the word "passed" when
   `${#SKIPPED[@]} > 0`.
2. Classify gates as `core` (pytest, static) vs `best-effort` (frontend build, ruff-format ratchet); a
   skipped **core** gate exits non-zero with the remedy printed (`brew services start redis`).
3. Add `--require mypy,bandit` to line 95 so a broken toolchain fails rather than passes.

The escape hatches (`--no-verify`, `CI_LOCAL_SKIP=1`) should stay — a bypass a developer must *choose*
is honest; a bypass that happens by itself and reports success is not.

---

### F5 — There is no review of any kind, and no substitute for one, in a repo where every SEV1 was caught by production or a manual audit *(MEDIUM — judgement call)*

`required_pull_request_reviews: null` is the **right call and I am not arguing against it**: GitHub's
own "prevent self-review" semantics say a lone approver is not a control, and `docs/BRANCHING.md:129`
correctly notes a required-review rule deadlocks a solo maintainer. But the recorded decision stops
there — it treats "no required review" as the end state rather than as a gap to substitute for.

The corpus is unambiguous about the cost: **every SEV1/BLOCKER was caught at production or by a
deliberate audit, none by CI** (`snag-taxonomy.md` §B), and the two failure classes that dominate —
vacuous green (Class 1) and honesty inversion (Class 5) — are explicitly ones no gate can catch
(`OFF_COURSE_BUGS:22`: *"No gate could have caught this"* — the token was valid, just wrong).
Findings arrive in bursts from paid audits (27 issues in one day), not from the pipeline. DORA 2025
names this exact pattern: AI raises authoring throughput without raising review capacity, and the
countermeasure is to move automated judgement into the author/PR phase.

**Failure scenario.** A generated PR renders a failure string in `text-success`, or adds a test whose
assertion is `all([])`. Ruff, mypy, bandit, coverage, integration and Playwright all pass — none of
them read for *intent*. It merges, auto-deploys, and is found 3 weeks later by the owner looking at a
screenshot. That is the modal history of this repo.

**Recommendation (judgement call, and I want to be careful here).** Add
[`anthropics/claude-code-action`](https://github.com/anthropics/claude-code-action) as a PR job scoped
narrowly to the two classes gates cannot catch — *"does any assertion in this diff pass vacuously?"*
and *"does any user-facing string's styling contradict its content?"* — and keep it
**non-blocking**. Making an LLM reviewer a *required* check in **this** repo would create a new
Class-1 gate that can pass by not looking, which is the precise failure the project is trying to
escape. The value is the comment, not the veto. Pair it with a 3-line self-review checklist in a PR
template (there is none today — `.github/` contains only `workflows/`), which is the cheapest
mechanism that reliably slows an author down.

**Explicitly NOT recommended:** a merge queue. At one developer and ~2–4 merges/day, `strict: true`
already provides the only property a queue buys — tested-on-top-of-`main` — with none of the flake
jamming. This is a "you do not need this at 100 users" call.

---

### F6 — `pip-audit` is a required check pinned at a zero baseline, with no automated dependency-update path *(LOW-MEDIUM)*

`run_layer0.py:281` returns `pip_audit_vulns` compared `max` against `baselines.json`'s `0`, inside the
required `Types + SAST + deps` check (`ci.yml:419-435`). There is **no `.github/dependabot.yml`, no
`renovate.json`, and no CODEOWNERS** — `requirements.txt` is hand-pinned with `==`.

**Failure scenario.** A CVE is published overnight against a transitive dep. The next morning *every*
open PR — including an unrelated production hotfix — fails a required check that has nothing to do
with the change. There is no bot PR waiting with the bump; the maintainer must diagnose,
find a compatible version, and re-pin by hand before anything can merge. The existing coping mechanism
is `PIP_AUDIT_IGNORES` (`run_layer0.py:227-265`), a hand-maintained suppression list with **no expiry
dates** — five pip CVEs are currently suppressed with a "re-evaluate when the venv is rebuilt" note
that nothing schedules.

**Fix:** a `dependabot.yml` with weekly **grouped** updates for pip + npm + github-actions. Grouped
mode is the specific thing that makes this tolerable for a solo maintainer — one PR a week, not
fifteen. Cost: ~10 minutes to add.

---

## What is genuinely right here

These are specific, and several of them are things I expected to find broken and did not.

1. **The no-`push`-trigger design is sound, not a hole — I checked.** With `strict: true` (branch must
   be up to date with base) plus `required_linear_history` forcing squash/rebase, the tree that lands
   on `main` is byte-identical to the PR head that the 8 checks ran against. Re-running ~12 jobs
   post-merge would burn minutes for no new signal. `ci.yml:28-31`'s reasoning was only wrong in the
   world where direct pushes were possible; with `enforce_admins: true` closing that, the premise is
   now actually true. Do not add `push: [main]`.
2. **The runner trust boundary (Issue 360, `DECISIONS.md:12558`) is exactly right** and better than
   most small teams manage: PR-triggered code never touches the self-hosted runner that has docker-group
   membership and read access to `/opt/autoclip/.env`. `ci.yml:11-25` states the threat model
   (a malicious transitive dep pulled by `npm ci` reading every prod secret) explicitly, and closes with
   *"if the spending limit fast-fails PR CI, fix billing; do NOT move these jobs back."* That is the
   correct instruction to leave for your future self.
3. **`permissions: contents: read` at workflow level** in `ci.yml:53-54`, escalated per-job only where
   needed. This is current GitHub-hardening standard and most repos this age do not have it.
4. **`tests/test_ci_config.py` — 22 tests asserting properties of the CI/CD YAML itself.** This is
   above standard. It is the mechanism that turns "we learned X about our pipeline" into "X cannot
   regress", and it already pins the eval commit-status SHA target, the pre-migration dump ordering,
   the `needs: deploy-staging` chain, and the never-`:latest` staging pin. The right home for the fixes
   in F1 and F2 is this file.
5. **The data-bearing staging gate (`deploy.yml:31-147`) is the strongest thing in the pipeline** and is
   *above* standard for this scale. Deploying the exact `sha-` image under test against a persistent
   `staging_postgres_data` volume, then asserting `alembic current == heads` in-container, is the only
   mechanism that can catch data-dependent migration failures — CI's fresh-DB bootstrap structurally
   cannot. The `!cancelled()` guard on the prod job and the `skip_staging` break-glass are both
   correctly reasoned.
6. **Auto-rollback that still `exit 1`s** (`deploy.yml:339`). A rollback is a safety net, not a success
   signal, and reporting the deploy as failed after rolling back is the right call — most homegrown
   rollbacks report green. `docker image prune` correctly moved to *after* a green smoke so a failed
   deploy cannot delete its own rollback target.
7. **The 2026-08-15 trunk-based decision (`DECISIONS.md:8`) is well-argued and correctly evidenced.**
   The `enforce_admins` + `required_linear_history` + long-lived-second-branch deadlock is real, the
   analysis verified it against the live repo rather than assuming, and it explicitly records that the
   previous rules *"bound everyone except the only person pushing."* It arrived on day 82, which is
   late — but the reasoning is better than most teams produce, and the "do not re-create a long-lived
   branch" warning is the right artifact to leave behind.
8. **No pre-commit hook is the correct answer, and it should stay that way** (answer to Q4 below).

---

## Answers to the specific questions

**Q1 — standard for solo trunk-based delivery in 2026.** Short-lived branches → PR → protected trunk
with `strict` + linear history + `enforce_admins`; required checks that fail closed; no required human
review (it deadlocks and GitHub's own semantics say one person approving themselves is not a control);
an **automated reviewer in the author/PR phase** as the substitute, per DORA 2025's "shift automation
left" guidance; small batches; **no merge queue** below roughly 10 PRs/day. This project now matches
that shape on every axis except the automated reviewer (F5) and the advisory gates (F3).

**Q2 — which advisory gates to promote, and the latency cost.** Measured above: Migration lint (+0 s,
promote now), Visual regression (+0 s, promote now — and resolve the `ci.yml:606-609` contradiction
either way), Frontend (+0 s, promote after adding `--reporter=verbose` so the three-times-unnamed
vitest flake is captured), Mutation (leave non-required — the objection is semantic, not temporal).

**Q3 — local-gate shape.** Fail on a missing dependency for *core* gates; report explicitly and
distinctly for best-effort ones. Never emit the word "passed" when anything was skipped. See F4.

**Q4 — pre-commit or pre-push?** **Pre-push is the right boundary and no pre-commit hook should be
added.** The 2026 rule of thumb is pre-commit ≤10 s / pre-push ≤2 min; nothing in this repo's gate set
fits in 10 s except `ruff check` on changed files, which `ruff format`/`ruff check` already cover at
push time. The dominant reported reason developers abandon pre-commit hooks is that they break the
frequent-save-point commit habit — and this maintainer commits *very* frequently (967 commits in 82
days). Adding one would guarantee `--no-verify` becomes muscle memory, which would then also bypass
the pre-push gate. The correct improvement to Layer 1 is making the existing pre-push hook honest
(F4), not adding a second hook. If hook management ever becomes a burden, Lefthook is the current
recommendation for its parallel execution — but a 25-line bash `pre-push` at this scale does not need
a framework.

**Q5 — deploy approval / canary.** **Do not add a GitHub environment required-reviewer gate.** GitHub
ships "prevent self-review" precisely because a self-approval is not a review; for a solo maintainer
it is a click, and its only real effect would be to insert an unbounded wait between merge and deploy
during which `main` and production diverge — which is worse, not better, for a trunk-based repo.
Similarly, **canary is over-engineering at ≤100 users on one VM** — the explicit current guidance is
that most teams reaching for canary needed a health check and an atomic swap, which this pipeline
already has. The ~50 s broken-image window is a real but correctly-sized cost at this scale.

What *would* pay for itself, in strict priority order, is: (1) fix F2 so the smoke actually runs;
(2) an alert when a deploy run fails or `/health` goes non-ok — `snag-taxonomy.md` §9 records prod
being **silently down for up to 9 days** and `process-map.md` §6 records **zero alert rules anywhere in
the repo**; (3) shorten the 50 s window with a compose health-check-gated start rather than a
strategy change. An approval button would have prevented none of the nine incidents in the corpus. An
alert would have caught at least three.

**Q6 — release/versioning.** Tag-on-every-push-to-main is a legitimate standard shape (it is what
semantic-release does) and CalVer was a defensible choice for a continuously-deployed single-product
SaaS — the reasoning at `DECISIONS.md:11659` holds up. But the implementation is inert (F1), and the
specific mechanism that broke it is the one the decision entry waved away: the patch component must be
bumped by a human and never is. **Either** derive the patch mechanically (commit count since the last
tag, or `YYYY.MM.DD`, or drop CalVer and tag `sha-<short>`), **or** adopt release-please so that
merging a Release PR is what tags — the 2026 default precisely because it removes the manual-bump
step that killed this one. Whichever is chosen, the non-negotiable half is that the deployed artifact
can state its own identity at `/health`, which today it cannot.

---

## Decisions this domain needs but does not have

1. **What "release identity" means for this product.** Nothing records whether a version string is for
   humans (support/rollback), machines (Sentry release grouping), or customers. F1 exists because
   Issue 297 answered "which scheme" without answering "who reads it." Pick one consumer and make the
   artifact serve it.
2. **A stated policy on what makes a check required.** The 8-vs-4 split is not written down anywhere as
   a rule — it is an accretion. Without a stated principle ("a check is required if a failure would
   reach production undetected"), `Migration lint` sits advisory while `Docker build (smoke test)`
   blocks, and no one can tell whether that is a decision or an oversight.
3. **The review substitute.** `DECISIONS.md` records *why* there is no required review; it does not
   record what replaces it. That is a live gap in a repo where CI catches <10% of defects and 0 of the
   SEV1s.
4. **Local-gate failure semantics.** Is `ci_local.sh` an advisory convenience or a gate? It is
   currently documented as a gate (`ci.yml:11-25` calls it "Layer 1") and behaves as an advisory. Pick.
5. **Dependency-update cadence.** No decision exists on how a CVE gets fixed. The current implicit
   answer — "the required pip-audit check red-walls everything until you fix it by hand" — is a policy
   nobody chose.
6. **An expiry convention for `PIP_AUDIT_IGNORES`.** Five suppressions carry a "re-evaluate when…"
   comment with no owner and no date. A suppression without an expiry is a permanent hole.
7. **What the four `📋 Open` CI/deploy rows in `OFF_COURSE_BUGS.md` are for.** `:42` is factually stale
   (fixed by Issue 360, still marked Open) and `:81` (LLM E2E Nightly red on main) has been open since
   2026-07-02. A backlog that does not close is a backlog whose entries stop being read — the
   visual-regression contradiction in F3 survived a month partly because of it.
