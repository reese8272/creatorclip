# CreatorClip — Branching & Promotion Model

Established in Issue 145 (2026-06-17) as a two-tier `feature → staging → main` model.
The `staging` **branch** was retired on 2026-08-15 in favour of trunk-based development
against `main`; see `docs/DECISIONS.md`. The staging **environment** (the data-bearing
`ccstage` compose stack on the VM) is unchanged and still gates every prod deploy.

> **`staging` branch vs staging environment — they are different things.** Retiring the
> branch removed a merge hop that verified nothing. It did NOT remove the pre-prod gate.

---

## Branches

| Branch | Role | Who writes to it |
|--------|------|------------------|
| `main` | **Trunk / live production.** Every commit here is deployable; `docker-publish.yml` → `deploy.yml` ship it to `autoclip.studio`. | PRs from `feature/*` only — no direct pushes, no admin bypass. |
| `feature/*` | **Work.** Short-lived, one issue/topic each. | You + Claude. |

## Promotion flow

```
feature/<issue>  ──PR──►  main  ──auto──►  staging stack gate  ──►  deploy → autoclip.studio
     (8 required CI gates)          (docker-publish)   (data-bearing DB,       (prod)
                                                    in-container migrations,
                                                     core smoke — BLOCKING)
```

1. Branch `feature/<issue>` off `main`.
2. Open a PR into `main`. The `CI` workflow runs (lint, unit, integration, coverage,
   static-gates, docker, Playwright, eval). Merge when the 8 required checks are green.
   Merge with **Rebase** or **Squash** — `required_linear_history` disables merge commits.
3. Merging pushes `main`, which triggers `docker-publish.yml` → `deploy.yml`. The
   `deploy-staging` job (Issue 298) deploys the exact `sha-` image under test to the
   persistent, data-bearing staging DB, runs in-container migrations + the core smoke,
   and **blocks the prod job on failure**. Break-glass: `workflow_dispatch` with
   `skip_staging=true`.
4. For manual pre-merge verification against the staging stack, see
   `docs/STAGING_ACCESS.md` — that runbook is branch-independent and still applies.

### Why the `staging` branch went away (2026-08-15)

Three findings, all verified rather than assumed:

- **It verified nothing extra.** `ci.yml` ran the *identical* 8 required checks on PRs
  into `main` and into `staging`. A second hop through the same gates adds latency, not
  signal.
- **Nothing deployed it.** `docker-publish.yml` builds on `push: [main]` only. No
  workflow ever shipped the `staging` branch anywhere, so "verify on staging before
  promoting" was never actually wired to the branch.
- **It made real protection impossible.** `enforce_admins: true` +
  `required_linear_history: true` + a long-lived `staging` branch **deadlock** on the
  second promotion: linear history forces `staging → main` to squash/rebase-merge, which
  rewrites SHAs; syncing `staging` back to `main` is then a non-fast-forward, and
  `allow_force_pushes: false` with admins enforced leaves no way to land it. GitHub
  offers no true fast-forward merge button, so one of the three had to go. The branch was
  the one carrying no value.

If a genuine pre-merge test environment is wanted later (a deployed staging URL fed from
a branch), that is tracked as its own issue — it needs a deploy path and auth gating, not
a branch.

---

## Branch protection — ENFORCED on `main`, admins included, since 2026-08-15

> ✅ **Live on `main`.** The blocker recorded here (Issue 145: the API
> returned 403 "Upgrade to GitHub Pro or make this repository public" on the free
> tier) no longer applies — the repo is **public**, where branch protection is free.
> Protection was first applied 2026-08-13; on **2026-08-15** `enforce_admins` was
> flipped to `true` and the `staging` branch (and its protection) was retired.
>
> ⚠️ **`enforce_admins: true` means the maintainer is gated too.** Before this, the
> rules were advisory for the only person pushing: `main`'s HEAD `1221fb8` was a
> **two-parent merge commit** despite `required_linear_history: true`, and the
> `git push origin origin/main:staging` sync landed while the remote itself printed
> `8 of 8 required status checks are expected`. Both went through purely on admin
> bypass. There is now no bypass — every change to `main` goes through a PR.
>
> ⚠️ **Verify before trusting `eval/clip-quality`.** That context was posted to
> `context.sha` — the ephemeral `refs/pull/N/merge` commit — while protection
> evaluates the PR **head**. It had therefore never satisfied a rule, and requiring
> it before the fix would have hung every PR on "Expected — Waiting for status to be
> reported". Fixed in PR #101 (`ci.yml` now posts to
> `context.payload.pull_request?.head?.sha ?? context.sha`) and pinned by
> `tests/test_ci_config.py::test_eval_commit_status_targets_the_pr_head_sha`.
> **Do not re-enable that requirement against any ci.yml predating #101.**

**Required status checks** (exact job names from `.github/workflows/ci.yml`):
- `Lint (ruff)`
- `Unit tests (pytest)`
- `Integration tests (postgres + redis)`
- `Coverage floor (pytest-cov ratchet)`
- `Types + SAST + deps (mypy, bandit, pip-audit)`
- `Docker build (smoke test)`
- `Playwright (smoke + a11y)` — Issue 266: a11y regression gate (axe violations on serious/critical)
- `eval/clip-quality` (commit status, not job) — Issue 265: required on clip_engine/ and tests/eval/ changes; posted via GitHub commit-status API because a skipped required job reports 'success' (GitHub quirk — a commit status always reflects real outcome)

**Applied via `gh` (2026-08-15) to `main` — re-run verbatim to restore:**

```bash
gh api -X PUT "repos/reese8272/creatorclip/branches/main/protection" \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "Lint (ruff)",
      "Unit tests (pytest)",
      "Integration tests (postgres + redis)",
      "Coverage floor (pytest-cov ratchet)",
      "Types + SAST + deps (mypy, bandit, pip-audit)",
      "Docker build (smoke test)",
      "Playwright (smoke + a11y)",
      "eval/clip-quality"
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
```

Notes:
- `required_pull_request_reviews: null` — **solo maintainer can't approve their own PR**,
  so a required-review rule would deadlock merges. Add it (`required_approving_review_count: 1`)
  once the team is ≥2.
- `strict: true` — branch must be up to date with base before merge.
- `required_linear_history: true` + `allow_force_pushes: false` — clean, non-rewritable history.
- GitHub's modern equivalent is **Rulesets** (Settings → Rules → Rulesets); the same
  contexts/linear-history/force-push settings apply.
- `enforce_admins: true` — the maintainer is gated like everyone else. This is only
  survivable because there is no longer a branch to sync: a merge commit on `main`
  carries **none of the 8 required contexts** (`ci.yml` has no `push` trigger, so those
  run only on the PR head), which is precisely why the old
  `git push origin origin/main:staging` sync could never satisfy the rule on its own and
  landed on admin bypass alone. With `staging` retired, every path to `main` is a PR,
  and a PR always carries the contexts.
- **Do not re-create a long-lived branch that merges into `main`** without first flipping
  `required_linear_history` to `false`. The three settings deadlock — see "Why the
  `staging` branch went away" above for the exact mechanism.
- The four deploy-track checks that fire on a `main` push — `Build & push to GHCR`,
  `Staging gate (data-bearing DB)`, `Deploy → autoclip.studio`, `Run staging drills (all)`
  (measured on `6137992`) — are **not** required contexts and are not merge gates. They
  run after the merge; `deploy.yml`'s staging gate is what blocks a bad image from prod.

---

## Flake Policy (Issue 268)

A flaky test is an intermittent failure — it passes on re-run but fails on the first attempt.
Mishandling flakes caused the Issue 143 9-day red where nobody could distinguish flake from
real regression.

### Detection vs. gating

| Job | Purpose | Gating? |
|-----|---------|---------|
| `Flake detection (non-gating)` | Runs unit suite with `--reruns 1`; summarises candidates | **No** (`continue-on-error: true`) |
| `Unit tests (pytest)` | Single-pass honest gate | **Yes** |

**Blanket `--reruns N` as a merge gate is explicitly prohibited.** It converts a real
intermittent regression into a false green — the exact mechanism that hid the 9-day red.

### Quarantine lifecycle

When a flake is detected:

1. Add `@pytest.mark.quarantine` to the test (excluding it from the gating lane).
2. Open an issue tracking the root cause.
3. Fix the root cause.
4. Remove the `quarantine` marker and verify the test passes consistently.

**Never `@skip` or delete a flaky test** — skipping loses the signal that the flake is
still broken. The `quarantine` marker keeps the test collected and running in a non-blocking
lane, so the fix is verifiable without blocking CI.
