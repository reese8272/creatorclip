# Research Brief — QA, Test-Suite Hardening & Release Engineering (Prompt 15 / Issue 180)

> Read-only research brief. No product code changed. Industry-standard-first per the One Rule
> (`CLAUDE.md`). Repo claims cite `file_path:line`; external claims cite a source link.
> Eval split: this brief owns **test reliability, visual-regression, CI gating, safe
> deploy/rollback, and how the clip-quality eval gates `clip_engine/` changes**. It hands the
> **clip-quality/adversarial-eval methodology** to prompt `08` (Issue 173) and cross-references it
> rather than duplicating.

---

## 1. Executive summary — conclusions first

The project already has an unusually mature gate harness for a solo build: a consolidated
single-status CI (`.github/workflows/ci.yml`), a deterministic Layer-0 floor
(`run_layer0.py`), a real-Postgres integration lane that now runs on PRs (Issue 144), a YAML
clip-quality eval (`tests/eval/scenarios/`), and a Playwright SPA harness with an axe a11y gate.
The remaining risk is **not** "we don't test" — it is concentrated in five places:

1. **Flake detection is reactive, not systemic.** Every reliability incident in
   `docs/OFF_COURSE_BUGS.md` was found by a human noticing red after the fact — one sat red **9+
   days** (Issue 143). There is no order-randomization, no flake-rerun signal, and no quarantine
   lane. The three root-cause classes the repo has already hit (shared-engine/event-loop leakage,
   per-IP rate-limit bucket sharing, advisory-lock leakage) are all **test-isolation** bugs that
   `pytest-randomly` is specifically designed to surface — and it is not installed.
2. **The clip-quality eval does not actually gate `clip_engine/` changes in CI.** It runs as
   ordinary pytest inside the unit lane (`tests/test_clip_engine.py:190`+), so a `clip_engine/`
   edit that ships with a weakened scenario still goes green; nothing enforces "eval must run, and
   must run *un-skipped*, before a `clip_engine/` change merges." This is the single highest-value
   gating gap given the engine is product truth.
3. **The Playwright harness is real but not wired into CI, and has no pixel baselines.** `ci.yml`
   has no Playwright job — the smoke (`frontend/e2e/smoke.spec.ts`) and a11y
   (`frontend/e2e/a11y.spec.ts`) specs only run locally, so the a11y regression gate the team
   *believes* protects prod (it locked the Issue 165 contrast fix) is **not enforced on PRs**.
   Visual-regression baselines (`toHaveScreenshot`) remain explicitly deferred
   (`docs/PROJECT_STATE.md:52`).
4. **Tests assert at the line level, not the behavior level, in the load-bearing core.** `mutmut`
   is a dev dependency (`requirements-dev.txt:11`) but has never been run. For the modules where a
   silent logic flip is a product/security failure (`clip_engine/`, `preference/`, `crypto.py`,
   `limiter.py`, the per-creator isolation in `models.py`/routers), mutation testing is the only
   thing that proves the tests would catch a mutated comparison.
5. **The deploy path can reach prod with an unsafe or irreversible migration.** `deploy.yml:51`
   runs `alembic upgrade head` with no migration linting, no `lock_timeout`, and no rollback
   step; a single bad migration (a blocking `ALTER`, a non-forward-compatible drop) would take
   prod down with only a manual recovery path. Branch protection that would prevent a red PR from
   merging is **convention only** — not enforced (`docs/BRANCHING.md`, "Not yet enforced").

Highest-value work, in order: **(a)** eval-gates-clip_engine + Playwright-in-CI (close the two
"green but unprotected" holes), **(b)** `pytest-randomly` + a flake-rerun signal (attack the
incident root-cause class directly), **(c)** migration safety lint + rollback runbook, **(d)**
visual-regression baselines, **(e)** a scoped mutation-testing cadence on the load-bearing core.

---

## 2. Reliability findings — incident class → root cause → systemic fix

Evidence base: `docs/OFF_COURSE_BUGS.md` (the incident log) + `tests/conftest.py` +
`.github/workflows/ci.yml`.

| # | Incident class (evidence) | Root cause | Why current fix is partial | Systemic fix (standard) |
|---|---|---|---|---|
| R1 | **Red test hidden 9+ days** — `poll_clip_outcomes` advisory-lock flake (`OFF_COURSE_BUGS.md` 2026-06-08, Issue 143) | Session-level `pg_advisory_lock` leaked across pytest-asyncio per-test event loops on a shared module `admin_engine` pool | Fixed point-wise (rollback-before-unlock + autouse dispose fixture in `tests/test_worker_pipeline.py`); integration moved onto PRs (Issue 144, `ci.yml:7`). But the *class* — shared engine/loop/lock state — has no guardrail; the next shared-fixture leak is invisible again. | Add `pytest-randomly` (shuffles order + reseeds every run) so order-coupled state surfaces locally and in CI; it is the named tool for exactly this class ([Advanced Python](https://advancedpython.dev/articles/pytest-randomisation/), [Mergify](https://mergify.com/learn/flaky-tests/pytest)). Make event-loop/engine scope explicit (`scope="function"` unless required) ([Mergify](https://mergify.com/learn/flaky-tests/pytest)). |
| R2 | **Order-dependent rate-limit flake** — TestClient `429`s in the full lane, pass in isolation (`OFF_COURSE_BUGS.md` 2026-05-31) | slowapi keys unauthenticated requests by IP; every `TestClient` call is IP `testclient`, so tests share one rate-limit bucket | Workaround is a per-test per-creator session cookie applied **by hand** in two tests; the bug log itself flags it is "⚠️ not surfaced in conftest" — the next dev hits the same trap | Provide a conftest fixture that auto-assigns a fresh per-creator session cookie (or resets the slowapi Redis bucket between tests), so isolation is the default not a manual ritual. This is the "fixture isolation by default" pattern ([Mindful Chase](https://www.mindfulchase.com/explore/troubleshooting-tips/testing-frameworks/troubleshooting-fixture-leakage-and-state-contamination-in-pytest.html)). |
| R3 | **Infra-down → opaque 500 cascade** — mid-session Redis death looked like a code regression (`OFF_COURSE_BUGS.md` 2026-05-29) | slowapi limiter has no in-memory fallback by design; nothing asserted Redis was up before the suite | **Already well fixed** — `tests/conftest.py:31` `pytest_configure` fails fast with one legible message. Good model to extend: there is no equivalent fail-fast for Postgres in the integration lane. | Keep the Redis guard; add the same socket fail-fast for Postgres in the integration path so a missing DB also fails legibly, not as a wall of 500s. (Pattern proven in this repo.) |
| R4 | **Integration ran main-only** — the R1 flake could hide because integration never ran on PRs | CI topology: integration was a separate main-only workflow | **Fixed** — consolidated `ci.yml` runs integration on PRs and `staging` too (`ci.yml:82`, comment at `ci.yml:6`). | Lock it in: make the integration job a **required** status check once branch protection is enforceable (see R8). |
| R5 | **First-ever CI run of new tests fails** — event-log tests failed the first time they executed; earlier runs aborted at Alembic before reaching them (`OFF_COURSE_BUGS.md` 2026-06-18, migration 0026 enum double-create) | A migration error short-circuited the integration lane, so whole test classes silently never ran | The migration bug was fixed, but "a class that never executes looks identical to a passing class" remains undetected | Add `pytest --collect-only` count assertion or `--ignore-glob`-free run + fail the lane if collected-test count drops vs a committed floor; `ci.yml:77` already collects for import-smoke — extend it to a count ratchet so "tests stopped running" is itself a failure. |
| R6 | **httpx2 TestClient deprecation noise** (`OFF_COURSE_BUGS.md` 2026-06-17) | starlette 1.3.1 bump deprecates httpx-1 TestClient | Logged, non-blocking | Migrate to httpx2 when the test stack is next bumped; treat the warning as a tracked migration signal, not noise to ignore. |
| R7 | **No flake-rerun signal anywhere** | `pytest-rerunfailures` / flaky-detection not installed; no "ran red then green" telemetry | A genuinely intermittent failure is indistinguishable from a hard failure or is dismissed as "just rerun it" | Add an opt-in **detection** rerun (CI-only, reports a test as flaky when it passes on rerun) — **not** a blanket auto-retry, which the standard explicitly warns hides real bugs ([pytest docs](https://docs.pytest.org/en/stable/explanation/flaky.html), [Mergify](https://mergify.com/learn/flaky-tests/pytest)). Pair with a quarantine marker so a known flake is visibly tracked, never silently disabled ([DEV/quarantine](https://dev.to/alex_aslam/taming-flaky-tests-in-monorepos-quarantine-pipelines-and-stability-hacks-279g)). |
| R8 | **Red PR can still merge** | Branch protection requires GitHub Pro on a private repo; currently convention-only (`docs/BRANCHING.md`, "Not yet enforced") | The CI workflow runs, but nothing *blocks* a merge on red | Enforce the ruleset in `docs/BRANCHING.md` the moment Pro is enabled (the exact `gh api` payload is already written there). Until then this is an accepted, documented risk — flag it as such, do not pretend it is enforced. |

**Flaky-detection answer (research question):** the standard layered approach is
(1) `pytest-randomly` to *prevent* the order-coupling class at its source, (2) a CI-only
detection rerun to *flag* intermittency the moment it appears (so nothing sits red for days
unnoticed), and (3) a quarantine marker so a flake under repair is tracked, not deleted or
silently `@skip`-ped. Crucially, do **not** adopt blanket `pytest-rerunfailures` as policy — it
converts a real intermittent bug into a green run, which is precisely how R1 stayed hidden
([pytest docs](https://docs.pytest.org/en/stable/explanation/flaky.html)).

---

## 3. Coverage + eval plan

### 3.1 Where coverage is genuinely thin (not just line %)

The Layer-0 coverage gate (`run_layer0.py:117`) is a **line-rate floor across all sources** —
it cannot tell you whether the *load-bearing* modules are well-asserted, only that the aggregate
line rate didn't drop. Two structural blind spots:

- **No diff/patch coverage.** A PR can add untested lines to a load-bearing module and still pass
  if the aggregate floor holds. The current standard is to gate on **patch coverage** (coverage
  of changed lines) with `target: auto` + a small threshold, which forces new code to be tested
  without red-walling legacy ([Codecov](https://docs.codecov.com/docs/common-recipe-list),
  [Codacy](https://blog.codacy.com/diff-coverage)). This is the single biggest coverage-signal
  upgrade and needs no new infra (`diff-cover` runs on the existing `coverage.xml`).
- **Per-module floors don't exist.** `CLAUDE.md` names load-bearing modules (clip engine,
  preference, crypto, per-creator isolation) but the gate treats them identically to glue code.
  Recommend per-package coverage components/floors for `clip_engine/`, `preference/`, `crypto.py`,
  `limiter.py`, `auth.py` so a regression *there* is caught even if the aggregate is fine.

### 3.2 Mutation testing — recommendation: **yes, scoped, cadence-only**

`mutmut==3.2.0` is already a dev dep (`requirements-dev.txt:11`, annotated "cadence-only (slow)")
but has never been run. The standard is unambiguous: apply mutation testing to the **10–20% of
the codebase that must be correct** — money, security, core business rules — not the whole tree
([eferro](https://www.eferro.net/2025/11/mutation-testing-when-good-enough-tests.html),
[johal.in](https://johal.in/mutation-testing-with-mutmut-python-for-code-reliability-2026/)). A
2024 IEEE finding cited across sources: ~40% of high-coverage codebases still harbor undetected
logic errors — i.e. line coverage lies about assertion quality, which is exactly the risk for an
engine whose correctness is the product. Recommended target set (the load-bearing core, mirrors
`CLAUDE.md`): `clip_engine/` (the setup-vs-aftermath comparisons), `preference/` (recency-decay
reweighting math), `crypto.py` (`decrypt()`), `limiter.py` (`_creator_key`), and the per-creator
isolation predicates. Run it **on a cadence (manual/scheduled), not per-PR** — it is slow and the
standard warns against per-PR mutation gates; treat a mutation-score drop as a finding to fix
tests, with a target of >80% on these modules ([RONY BARUA](https://anandarony.com/2025/11/24/mutant-testing-in-software-quality-assurance/)).
**Decision needed** (see §5/§6): whether to gate (block PRs) or merely report.

### 3.3 Visual-regression baseline rollout (Playwright `toHaveScreenshot`)

Currently `frontend/e2e/smoke.spec.ts:65` captures `page.screenshot(...)` as **audit artifacts**
— it asserts only on console/JS errors, never pixel-diffs. The deferred work
(`docs/PROJECT_STATE.md:52`, Issue 162 follow-up) is to promote select stable pages to
`toHaveScreenshot()`. Standard-aligned rollout that avoids baseline churn:

1. **Generate baselines in CI, not locally** — font/anti-aliasing rendering differs per OS, so a
   locally-generated baseline flakes against the Linux CI runner. Generate (and store) baselines
   from the same container/runner that runs the diff
   ([Playwright docs](https://playwright.dev/docs/test-snapshots),
   [TestDino](https://testdino.com/blog/playwright-visual-testing)).
2. **Start with a small set of stable routes**, not all 9×2. Login, pricing, empty dashboard —
   pages with no live/random data. Defer high-churn pages (insights with real numbers, review
   with media) until they're masked.
3. **Tune tolerance + determinism:** `animations: 'disabled'` (already used at
   `smoke.spec.ts:68`), `maxDiffPixelRatio` ≈ 0.01 for full-page shots, `mask` dynamic regions
   (thumbnails, balances, timestamps), wait for fonts/network-idle
   ([TestDino](https://testdino.com/blog/playwright-visual-testing),
   [Playwright docs](https://playwright.dev/docs/test-snapshots)).
4. **Run on PRs as a separate, non-required-at-first job** so a baseline update is a deliberate
   reviewed step (`--update-snapshots` committed in its own PR), and visual flake never blocks an
   unrelated change while the baselines settle.
5. **Reuse the existing mocked-backend fixture** (`frontend/e2e/fixtures/mock-api.ts`) so renders
   are deterministic without Postgres/Redis — the harness is already built for this.

### 3.4 Eval gating — the `clip_engine/` hand-off to prompt 08

The clip-quality eval is product truth (`tests/eval/scenarios/*.yaml`, loaded by
`tests/test_clip_engine.py:190`+; the core invariant `setup_start_s < peak_s` is asserted at
`test_clip_engine.py:178`). **Today it gates nothing specifically** — it runs as a normal unit
test, so it executes on every PR but there is no rule that a `clip_engine/` change *must* run a
green, un-skipped eval, and no failure if someone `@skip`s a scenario.

**This brief owns the CI gating mechanism; prompt 08 owns the eval content** (adversarial/edge
scenarios, the labeled-window methodology, scoring). The clean seam:

- **Prompt 08 / Issue 173 delivers**: the adversarial scenario corpus (the existing
  `loud_aftermath.yaml`, `overlapping_peaks.yaml`, etc. are the seed), the labeling standard, and
  the pass/fail assertions per scenario.
- **This brief / Issue 180 delivers the gate**: a CI job that, **when files under `clip_engine/`
  (or `tests/eval/`) change**, runs the eval as a *required, un-skippable* check. Implement with
  `dorny/paths-filter` to detect the change and a commit-status that is required, with the known
  caveat that a skipped GitHub job reports "success" not "skipped" — so the standard pattern is to
  mark a **commit status** required rather than the job itself
  ([dorny/paths-filter](https://github.com/dorny/paths-filter),
  [community discussion](https://github.com/orgs/community/discussions/164673)). Add a guard that
  fails if the collected eval-scenario count drops (the R5 "tests stopped running" class applied
  to scenarios) and that no scenario is `xfail`/`skip`-marked without an explicit allowlist.

This keeps 08 and 15 non-overlapping: 08 = *what to assert*, 15 = *how CI enforces it can't
regress*.

### 3.5 E2E depth (research question)

Current state: Playwright smoke + a11y mock the backend at the network boundary
(`fixtures/mock-api.ts`); a separate prod config (`playwright.config.prod.ts`) hits the live site
with a captured session (Issue 164). The right next layers, cheapest-first:

1. **Flow-based E2E, still mocked** — promote per-page smoke to real journeys
   (login→onboard→sync→build-DNA→review→feedback). Already a named follow-up (Issue 162). Highest
   value/lowest cost; deterministic.
2. **Full-stack E2E against seeded Postgres** — the layer mocks can't cover (real OAuth/session
   handling, real per-creator isolation, real DB writes). Needs the integration CI services
   (`ci.yml:82` already stands up pgvector+redis) plus a seed step (`tests/perf/seed_staging.py`
   is a starting point). Run a small smoke subset, not the whole journey matrix, to stay
   proportionate.
3. **a11y gate stays green** by running `e2e/a11y.spec.ts` in the *same* CI job as the new
   Playwright lane (it currently runs only locally — see §1.3). The prod-axe audit
   (`e2e/prod/audit.spec.ts`) stays manual/scheduled, not per-PR, because it costs a real session
   and is subject to Cloudflare challenges (documented in `health-check.yml:7`).

---

## 4. Release-engineering findings

Deploy path: `docker-publish.yml` (on merge to `main`) → `deploy.yml` (self-hosted runner, pull
image → `doctor.py` preflight → `alembic upgrade head` → `up -d` → 5×retry `/health` smoke).
Promotion model: `feature/* → staging → main → auto-deploy` (`docs/BRANCHING.md`).

| # | Finding (evidence) | Standard (cite) | Fix |
|---|---|---|---|
| D1 | **Migrations run unguarded in the deploy** — `deploy.yml:51` runs `alembic upgrade head` with no migration lint; a blocking `ALTER`/unsafe `ADD COLUMN ... NOT NULL DEFAULT`/drop would lock or break prod. No `lock_timeout`/`statement_timeout` set anywhere (grep of `alembic/` returns none). | Lint Postgres migrations in CI with **Squawk** before they reach prod; set a short `lock_timeout` + `statement_timeout` so a bad migration aborts instead of hanging ([Squawk safe-migrations](https://squawkhq.com/docs/safe_migrations), [Squawk](https://squawkhq.com/)). | Add a Squawk CI step on changed migration files; set `lock_timeout`/`statement_timeout` in the Alembic run env. **DECISIONS entry needed** (new tool + migration policy). |
| D2 | **No expand/contract policy** — nothing enforces that a migration is forward-compatible with the still-running old container during rollout. | Expand/contract: additive (nullable add + backfill) ships first; the destructive contract ships a release later, so old and new code coexist — the standard for zero-downtime ([reliablepenguin](https://blogs.reliablepenguin.com/2025/11/16/database-migrations-without-drama-expand-contract-in-practice), [Jasmin Fluri/Medium](https://medium.com/@jasminfluri/expand-and-contract-method-for-database-changes-414d236f236f)). | Document an expand/contract rule in a migration PR checklist; the RLS rollout (`activate-rls.yml`) already demonstrates the team can do staged, idempotent ops — generalize it. **DECISIONS entry** for the policy. |
| D3 | **No rollback path for a bad migration.** `deploy.yml` rolls the image forward and smoke-tests, but if `alembic upgrade` half-applies or the new image is bad post-migration, recovery is manual. Alembic has no built-in deploy-rollback ([alembic#1298](https://github.com/sqlalchemy/alembic/issues/1298)). | Industry split: many teams adopt **roll-forward** (write a new migration to fix) as the default, reserving `downgrade()` for migrations proven safe at 0% traffic; either way the decision must be explicit ([botmonster](https://botmonster.com/coding/automate-database-migrations-alembic-sqlalchemy/), [getdefacto](https://www.getdefacto.com/article/database-schema-migrations)). | Write a rollback runbook in `docs/DEPLOYMENT.md`: image rollback (re-tag previous GHCR image + `up -d`) + the migration policy (roll-forward default; reversible `downgrade()` only where expand/contract makes it safe). **DECISIONS entry** for roll-forward-vs-downgrade. |
| D4 | **Health-gated cutover is shallow.** `deploy.yml:59` already retries `/health` 5× and fails the job on non-ok — good. But `docker compose up -d` has **already replaced the running container** by the time the smoke test runs, so a failed smoke test fails the *job* but prod is already on the new (broken) image. | Progressive delivery gates the cutover on health *before* sending traffic (canary/blue-green) ([Harness](https://www.harness.io/blog/beyond-the-big-bang-de-risking-cloud-migrations-with-progressive-delivery), [freecodecamp blue-green](https://www.freecodecamp.org/news/how-to-manage-blue-green-deployments-on-aws-ecs-with-database-migrations/)). | Single-VM Compose can't do true blue-green cheaply; the proportionate fix is an **auto-rollback on smoke failure** (re-pull/`up -d` the previous image tag) so a red smoke test self-heals rather than leaving prod broken. Full canary is a K8s-era item (`docs/DEPLOYMENT.md` notes K8s is the 10k-scale target). **DECISIONS entry** if auto-rollback is adopted. |
| D5 | **Red PR can merge → can reach prod.** Branch protection is convention-only (`docs/BRANCHING.md`); on a solo free-tier repo nothing blocks a merge on a failing required check, and `staging→main` auto-deploys. | GitHub required status checks / Rulesets ([the exact payload is already written in `docs/BRANCHING.md`]). | Enforce the ruleset when GitHub Pro is enabled (already documented). Until then: accepted, documented risk — the mitigation is that `deploy.yml`'s preflight (`doctor.py`) + smoke catch gross breakage post-merge. Flag, don't paper over. |
| D6 | **CI workflow name drift in docs.** Prompt/older docs reference `.github/workflows/quality.yml`; the repo consolidated everything into `ci.yml` (`ci.yml:3` header). `docs/BRANCHING.md` required-checks list is correct (matches `ci.yml` job names). | — | Minor doc hygiene: ensure any remaining reference to `quality.yml`/`integration.yml` is updated to `ci.yml`. (Stale-doc flag, not a defect.) |

---

## 5. Proposed issues (dependency-ordered, `docs/issues.md` house style)

> House style observed in `docs/issues.md`: `## Issue N — Title`, a **Problem** paragraph, then a
> checkbox **Acceptance criteria** list, optional **Follow-ups**. Latest issue in the repo is
> **165**; this brief is tracked as **Issue 180**. Numbers below are proposed; renumber on filing.

### Issue 180a — Eval gates `clip_engine/` changes as a required, un-skippable CI check
**Problem.** The clip-quality eval (`tests/eval/scenarios/*.yaml`, run by
`tests/test_clip_engine.py:190`+) is product truth but gates nothing specifically: it runs as an
ordinary unit test, so a `clip_engine/` change can ship with a weakened or skipped scenario and
still go green. `CLAUDE.md` requires the eval before every `clip_engine/` change — CI doesn't
enforce it.
**Acceptance criteria.**
- [ ] CI runs the eval as a dedicated step; when files under `clip_engine/` or `tests/eval/`
      change (via `dorny/paths-filter`), the eval result is a **required** commit status.
- [ ] Build fails if the collected eval-scenario count drops below a committed floor (R5 class).
- [ ] Build fails if any scenario is `skip`/`xfail`-marked outside an explicit, reviewed allowlist.
- [ ] No live external APIs; runs on existing CI services only.
- [ ] Hand-off boundary documented: scenario *content* is owned by Issue 173 (prompt 08).
- [ ] **DECISIONS entry:** required-check-via-commit-status pattern + the 08/15 eval-ownership seam.

### Issue 180b — Wire the Playwright SPA harness (smoke + a11y) into CI
**Problem.** `frontend/e2e/smoke.spec.ts` and `e2e/a11y.spec.ts` exist and pass locally, but
`ci.yml` has no Playwright job — so the a11y regression gate that locked the Issue 165 contrast
fix is **not actually enforced on PRs**.
**Acceptance criteria.**
- [ ] New `ci.yml` job installs Chromium + runs `smoke.spec.ts` + `a11y.spec.ts` against the Vite
      dev server with the mocked backend (no Docker), mirroring `playwright.config.ts`.
- [ ] a11y job fails on any serious/critical axe violation (current local behavior).
- [ ] Job is a required check (or documented convention until branch protection is on).
- [ ] Prod-axe/`e2e/prod/*` stays manual/scheduled (Cloudflare-challenge constraint, `health-check.yml:7`).
- [ ] No DECISIONS entry needed (implements existing intent).

### Issue 180c — Test-isolation hardening: `pytest-randomly` + per-creator-cookie conftest fixture + Postgres fail-fast
**Problem.** Three reliability incidents (Issues 143, the slowapi-429 flake, the Redis cascade)
are all test-isolation bugs; the repo has no order randomization, the rate-limit workaround is
applied by hand in two tests (`OFF_COURSE_BUGS.md` flags it as not surfaced in conftest), and
there is a Redis fail-fast (`conftest.py:31`) but no Postgres equivalent.
**Acceptance criteria.**
- [ ] `pytest-randomly` added to `requirements-dev.txt`; suite passes under randomized order in CI.
- [ ] conftest fixture auto-assigns a fresh per-creator session cookie (or resets the slowapi
      Redis bucket) so the R2 flake can't recur; the two manual workarounds removed.
- [ ] Postgres socket fail-fast added to the integration path, mirroring the Redis guard.
- [ ] Shared engine/event-loop fixtures audited; scope made explicit per the standard.
- [ ] **DECISIONS entry:** adopting randomized test order (changes default `pytest` behavior).

### Issue 180d — Flake detection + quarantine signal (NOT blanket auto-retry)
**Problem.** A genuinely intermittent failure is indistinguishable from a hard failure; the
worst incident sat red 9+ days. There is no signal that flags "this passed on rerun" and no
tracked quarantine for a flake under repair.
**Acceptance criteria.**
- [ ] CI-only detection rerun reports (does not silently green) tests that pass only on rerun.
- [ ] A `quarantine` marker keeps a known flake visible + non-blocking while it's being fixed
      (never `@skip`/delete).
- [ ] Documented policy: blanket `pytest-rerunfailures` as a merge gate is prohibited (it hides
      real bugs — the R1 mechanism).
- [ ] **DECISIONS entry:** flake policy (detection-rerun yes, auto-retry-as-gate no).

### Issue 180e — Diff/patch-coverage gate + per-module floors for load-bearing modules
**Problem.** The Layer-0 gate is an aggregate line floor (`run_layer0.py:117`); a PR can add
untested lines to `clip_engine/`/`preference/`/`crypto.py` and pass. No diff coverage; no
per-module floor.
**Acceptance criteria.**
- [ ] Patch-coverage check on changed lines (`diff-cover` over the existing `coverage.xml`),
      `target: auto` style, gating new code without red-walling legacy.
- [ ] Per-package coverage floors for `clip_engine/`, `preference/`, `crypto.py`, `limiter.py`,
      `auth.py`.
- [ ] Integrated into `run_layer0.py` / `ci.yml` so CI and local `/assess` measure identically.
- [ ] **DECISIONS entry:** adding patch-coverage + per-module floors to the gate model.

### Issue 180f — Migration safety: Squawk lint + lock/statement timeouts + rollback runbook
**Problem.** `deploy.yml:51` runs `alembic upgrade head` with no lint, no `lock_timeout`, and no
rollback path; a bad migration can lock or break prod with only manual recovery.
**Acceptance criteria.**
- [ ] Squawk lints changed migration SQL in CI; unsafe ops fail the check.
- [ ] `lock_timeout` + `statement_timeout` set for the Alembic run so a bad migration aborts.
- [ ] `docs/DEPLOYMENT.md` rollback runbook: image rollback (previous GHCR tag + `up -d`) +
      migration policy (roll-forward default; reversible `downgrade()` only where expand/contract
      makes it safe) + an expand/contract PR checklist.
- [ ] **DECISIONS entry:** Squawk adoption + roll-forward-vs-downgrade policy + expand/contract rule.

### Issue 180g — Auto-rollback on failed deploy smoke test
**Problem.** `deploy.yml:53` replaces the running container *before* the smoke test
(`deploy.yml:59`); a failed smoke fails the job but leaves prod on the broken image.
**Acceptance criteria.**
- [ ] On smoke failure, deploy re-pulls/`up -d` the previously-running image tag (capture it
      before pull).
- [ ] Job still exits non-zero so the failure is visible/alerted.
- [ ] Documented as a stopgap until K8s-era progressive delivery (`docs/DEPLOYMENT.md`).
- [ ] **DECISIONS entry:** single-VM auto-rollback over full canary (the proportionate choice).

### Issue 180h — Visual-regression baselines (`toHaveScreenshot`) on stable routes
**Problem.** Deferred (`docs/PROJECT_STATE.md:52`, Issue 162 follow-up). The smoke harness
captures screenshots as audit artifacts but never pixel-diffs.
**Acceptance criteria.**
- [ ] `toHaveScreenshot()` on a small set of stable, data-free routes (login, pricing, empty
      dashboard) first; high-churn pages deferred/masked.
- [ ] Baselines generated **in CI/the same container**, committed to git; `maxDiffPixelRatio`
      tuned; `animations: 'disabled'` + dynamic-region masks; mocked backend reused.
- [ ] Runs on PRs as a separate, initially non-blocking job; baseline updates land in their own
      reviewed PR via `--update-snapshots`.
- [ ] **DECISIONS entry:** visual-regression scope + baseline-in-CI policy.

### Issue 180i — Scoped mutation-testing cadence on the load-bearing core
**Problem.** `mutmut` is a dev dep (`requirements-dev.txt:11`) never run; line coverage doesn't
prove the tests *assert* on the engine/security core.
**Acceptance criteria.**
- [ ] `mutmut` configured to target only `clip_engine/`, `preference/`, `crypto.py`,
      `limiter.py`, and the per-creator isolation predicates (the 10–20% that must be correct).
- [ ] Run on a manual/scheduled cadence (not per-PR — it's slow), with a documented >80%
      mutation-score target on these modules; surviving mutants triaged into test gaps.
- [ ] **DECISIONS entry:** mutation-testing scope + gate-vs-report decision (open question Q3).

---

## 6. Open questions for the human (one-line answers)

1. **Eval gate hardness:** should a `clip_engine/` change with a failing eval **block merge**
   (required commit status), or only warn until prompt 08's adversarial corpus lands? (180a)
2. **Branch protection:** is GitHub Pro available now, so the documented ruleset can be enforced,
   or do we stay convention-only and accept the red-PR-can-merge risk? (R8/D5)
3. **Mutation testing:** report-only finding, or a scheduled gate that must be cleared before a
   `clip_engine/`/`preference/` change ships? (180i)
4. **Migration rollback stance:** adopt **roll-forward as default** (write a new fix migration),
   or require reversible `downgrade()` on every migration? (D3/180f)
5. **Auto-rollback:** acceptable for the deploy to self-heal by re-pulling the previous image on a
   failed smoke test, or do you prefer a human-in-the-loop rollback? (D4/180g)
6. **Visual baselines:** start with login/pricing/empty-dashboard only, or do you want a specific
   route set baselined first? (180h)

---

## Appendix — Cross-references & stale-doc flags

- **Cross-ref to prompt 08 / Issue 173:** adversarial clip-eval *content* (scenario corpus,
  labeling, scoring) is owned there; this brief owns only the *CI gating mechanism* (§3.4, Issue
  180a). The existing `tests/eval/scenarios/*.yaml` are the shared seed corpus.
- **Stale-doc flag (D6):** references to `quality.yml` / `integration.yml` are stale — the repo
  consolidated into `.github/workflows/ci.yml` (`ci.yml:3`). `docs/BRANCHING.md`'s required-checks
  list is correct and matches the current job names.
- **Already-good, keep:** the Redis fail-fast guard (`conftest.py:31`), integration-on-PRs
  (`ci.yml:82`, Issue 144), the deterministic Layer-0 harness (`run_layer0.py`), and the deploy
  preflight + `/health` retry smoke (`deploy.yml:47`,`:59`) are sound — the gaps above are
  additive, not rewrites.

### Sources
- [pytest — Flaky tests](https://docs.pytest.org/en/stable/explanation/flaky.html)
- [Mergify — flaky tests in pytest](https://mergify.com/learn/flaky-tests/pytest)
- [Advanced Python — finding test isolation issues with pytest](https://advancedpython.dev/articles/pytest-randomisation/)
- [Mindful Chase — fixture leakage / state contamination](https://www.mindfulchase.com/explore/troubleshooting-tips/testing-frameworks/troubleshooting-fixture-leakage-and-state-contamination-in-pytest.html)
- [DEV — quarantine pipelines for flaky tests](https://dev.to/alex_aslam/taming-flaky-tests-in-monorepos-quarantine-pipelines-and-stability-hacks-279g)
- [Playwright — visual comparisons / test snapshots](https://playwright.dev/docs/test-snapshots)
- [TestDino — Playwright visual testing (baselines, maxDiffPixelRatio)](https://testdino.com/blog/playwright-visual-testing)
- [eferro — mutation testing: when "good enough" tests weren't](https://www.eferro.net/2025/11/mutation-testing-when-good-enough-tests.html)
- [johal.in — mutation testing with mutmut](https://johal.in/mutation-testing-with-mutmut-python-for-code-reliability-2026/)
- [RONY BARUA — mutation testing experience](https://anandarony.com/2025/11/24/mutant-testing-in-software-quality-assurance/)
- [Codecov — common configurations (patch coverage)](https://docs.codecov.com/docs/common-recipe-list)
- [Codacy — diff coverage quality gate](https://blog.codacy.com/diff-coverage)
- [dorny/paths-filter](https://github.com/dorny/paths-filter)
- [GitHub community — trigger on file paths + required-check caveat](https://github.com/orgs/community/discussions/164673)
- [Squawk — safe migrations](https://squawkhq.com/docs/safe_migrations)
- [Squawk — Postgres migration linter](https://squawkhq.com/)
- [reliablepenguin — expand/contract in practice](https://blogs.reliablepenguin.com/2025/11/16/database-migrations-without-drama-expand-contract-in-practice)
- [Jasmin Fluri — expand and contract](https://medium.com/@jasminfluri/expand-and-contract-method-for-database-changes-414d236f236f)
- [botmonster — Alembic dev→prod rolling deploys](https://botmonster.com/coding/automate-database-migrations-alembic-sqlalchemy/)
- [getdefacto — safe + robust schema migrations](https://www.getdefacto.com/article/database-schema-migrations)
- [alembic#1298 — deployment rollback feature request](https://github.com/sqlalchemy/alembic/issues/1298)
- [Harness — de-risking with progressive delivery](https://www.harness.io/blog/beyond-the-big-bang-de-risking-cloud-migrations-with-progressive-delivery)
- [freecodecamp — blue-green deploys with DB migrations](https://www.freecodecamp.org/news/how-to-manage-blue-green-deployments-on-aws-ecs-with-database-migrations/)
