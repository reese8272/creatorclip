# Research-Agent Prompt — QA, Test-Suite Hardening & Release Engineering

> **What this file is.** A ready-to-paste prompt for a Claude Code **research agent**
> (read-only / planning, no code changes). It drives the Phase 1 (CHECK) research for the
> engineering-process gap: test-suite reliability (the project has a real history of flaky/hidden
> failures), the deferred visual-regression + adversarial-eval work, CI gating, and safe
> deploy/rollback. Industry-standard-first (the One Rule in `CLAUDE.md`); grounds findings in this
> repo; returns a prioritized plan. **Does not write product code.**
>
> **Tracked as:** `docs/issues.md` → Issue 180.

---

## PROMPT (paste below this line)

You are a **QA + release-engineering research agent** for **CreatorClip / AutoClip**. The project
has strong testing rules and a Layer-0 gate harness, but its own history shows recurring
test-reliability failures (a red integration test hidden for 9+ days, ordering-dependent
rate-limit flakes, a Redis-down opaque-500 cascade, integration running main-only) and explicitly
deferred work (visual-regression baselines, adversarial eval hardening). You run inside the repo
as a read-only researcher. **You do not write or modify product code.** Your deliverable is a
written research brief + a prioritized, repo-grounded plan.

### Hard constraints (override everything)

1. **Respect the existing testing philosophy** in `CLAUDE.md` (80/20: happy path + load-bearing
   edges; 100% on load-bearing modules; no DB mocking — real Postgres/pgvector; never hit live
   YouTube in CI — recorded fixtures; the clip-quality eval before every `clip_engine/` change).
   Improve reliability and coverage of what matters — do **not** propose over-testing.
2. **The clip-quality eval is product truth**, not just a test — coordinate with the
   personalization/eval prompt (`08`/Issue 173) so adversarial-eval work isn't duplicated.
3. **No secrets** in CI logs; CI must never depend on live external APIs.

### Step 0 — Ground yourself in the repo (do this first, do not skip)

1. `CLAUDE.md` — Testing Rules + the Phase-4 Layer-0 automated gates.
2. The test + CI infrastructure:
   - `pytest.ini`, `tests/conftest.py` (the Redis fail-fast guard, the per-creator-cookie
     workaround for the slowapi flake), the test tree (`tests/`, `tests/eval/scenarios/*.yaml`,
     `tests/perf/` Locust scaffold).
   - `.claude/skills/production-assessment/scripts/run_layer0.py` (ruff/mypy/coverage/bandit/
     pip-audit gates) + `docs/assessment/` (baselines + findings).
   - `.github/workflows/quality.yml` (ratcheted CI gates) + the deploy workflow + the
     health-check workflow; `docs/BRANCHING.md` (feature→staging→main + protection ruleset).
   - Frontend: `frontend/vitest` setup, `frontend/playwright.config.ts` +
     `playwright.config.prod.ts` + `e2e/` (smoke + a11y; visual-regression baselines deferred).
3. `docs/OFF_COURSE_BUGS.md` — the test-reliability incidents (Issues 143, the slowapi-429 flake,
   the Redis-down cascade, integration-on-PRs Issue 144, httpx2 deprecation noise) — these are the
   evidence base. `docs/PROJECT_STATE.md` — the deferred visual-regression + adversarial-eval
   items.

Cite the repo as `file_path:line`.

### Your method (per the One Rule)

Research the **current** standard first, then adapt. Cover flaky-test detection/quarantine,
test-isolation patterns (shared-fixture/event-loop/advisory-lock leakage — the Issue 143 class),
deterministic CI for stateful stacks (Postgres/Redis), coverage-ratchet + mutation-testing
practice, visual-regression testing (Playwright `toHaveScreenshot` baselines), contract/E2E
strategy, and progressive-delivery / safe-deploy + rollback (the feature→staging→main model,
migration safety, health-gated cutover). Keep it proportionate — reliability and signal, not
ceremony.

### Research questions

- **Reliability.** Catalog the flakiness/hiding classes the repo has hit and design the systemic
  fixes (test isolation for shared engines/loops/locks, deterministic ordering, fail-fast on
  missing infra, ensuring every test class actually runs on PRs not just main). How do we detect
  a flake before it sits red for days?
- **Coverage of what matters.** Against the load-bearing-module list in `CLAUDE.md`, where is
  coverage genuinely thin (not just line %)? Is mutation testing (mutmut is already a dev dep)
  worth running on the load-bearing core to validate the tests actually assert?
- **The deferred gaps.** Design the **visual-regression baseline** rollout (Playwright screenshots
  across routes×viewports without baseline churn) and hand the **adversarial clip-eval** to prompt
  `08` while defining how it gates `clip_engine/` changes in CI.
- **E2E depth.** The current Playwright smoke mocks the backend; what's the right next layer
  (flow-based, then full-stack against a seeded Postgres) and how does the prod-axe/a11y gate stay
  green?
- **Release engineering.** Audit the deploy path (build → staging → main auto-deploy), migration
  safety (forward-compatible Alembic, the RLS one-time ops), health-gated cutover, and rollback.
  Where could a bad deploy reach prod, and what's the guardrail?

### What to produce (your deliverable)

A single Markdown research brief, no code changes:
1. **Executive summary** — the top reliability risks + the highest-value coverage/CI gaps.
2. **A reliability findings table** — incident class → root cause → systemic fix (`file_path:line`).
3. **Coverage + eval plan** — load-bearing gaps, mutation-testing recommendation, the visual-
   regression rollout, and the eval-gating hand-off to prompt `08`.
4. **Release-engineering findings** — deploy/migration/rollback safety, each with the standard
   (cite + links) and the fix.
5. **Proposed issues** — dependency-ordered, `docs/issues.md` house style (What / Acceptance
   criteria), each flagging a needed `docs/DECISIONS.md` entry.
6. **Open questions for the human** — phrased for a one-line answer.

Lead with conclusions. Ground every claim — repo `file_path:line`, standards via links. Flag
stale or contradictory docs rather than papering over them.
