# CreatorClip — Branching & Promotion Model

Established in Issue 145 (2026-06-17). Replaces the ad-hoc single-feature-branch flow
(`issue-139-142-sweep` style) with a two-tier promotion model.

---

## Branches

| Branch | Role | Who writes to it |
|--------|------|------------------|
| `main` | **Live / production.** Every commit here is deployable; `docker-publish.yml` → `deploy.yml` ship it to `autoclip.studio`. | PRs from `staging` only (one-time exception: the 143–147 sweep merges via PR #20 directly). |
| `staging` | **Pre-prod verification.** Mirrors what's about to go live; used to validate against the staging stack before promotion. | PRs from `feature/*`. |
| `feature/*` | **Work.** Short-lived, one issue/topic each. | You + Claude. |

## Promotion flow

```
feature/<issue>  ──PR──►  staging  ──PR──►  main  ──auto──►  deploy → autoclip.studio
     (CI gates)            (verify on            (CI gates)
                           staging stack)
```

1. Branch `feature/<issue>` off `staging`.
2. Open a PR into `staging`. The `CI` workflow runs (lint, unit, integration, coverage,
   static-gates, docker). Merge when green.
3. Verify on the staging stack (see `docs/STAGING_ACCESS.md`). For feature branches
   this is the **manual** runbook. Since Issue 298 the same stack is ALSO exercised
   **automatically** on every prod deploy: `deploy.yml`'s `deploy-staging` gate deploys
   the exact `sha-` image under test to the persistent (data-bearing) staging DB, runs
   in-container migrations + the core smoke, and blocks the prod job on failure — so
   promotion no longer rests on the manual step alone.
4. Open a PR `staging → main`. Merge when green → auto-deploys (staging gate first,
   then prod; break-glass via `workflow_dispatch` with `skip_staging=true`).

Keep `staging` fast-forward-able from `main`: after any direct hotfix to `main`, sync
`staging` (`git push origin origin/main:staging`).

---

## Branch protection (apply when GitHub Pro is enabled)

> ⚠️ **Not yet enforced.** Branch protection / rulesets require **GitHub Pro** on a
> private repo (the API returns 403 "Upgrade to GitHub Pro or make this repository
> public" on the free tier — confirmed Issue 145). Until then the model below is
> **convention**, with the real gate being the `CI` workflow that runs on every PR.
> Apply the ruleset the moment Pro is enabled.

**Required status checks** (exact job names from `.github/workflows/ci.yml`):
- `Lint (ruff)`
- `Unit tests (pytest)`
- `Integration tests (postgres + redis)`
- `Coverage floor (pytest-cov ratchet)`
- `Types + SAST + deps (mypy, bandit, pip-audit)`
- `Docker build (smoke test)`
- `Playwright (smoke + a11y)` — Issue 266: a11y regression gate (axe violations on serious/critical)
- `eval/clip-quality` (commit status, not job) — Issue 265: required on clip_engine/ and tests/eval/ changes; posted via GitHub commit-status API because a skipped required job reports 'success' (GitHub quirk — a commit status always reflects real outcome)

**Apply via `gh` (run once Pro is active), for each of `main` and `staging`:**

```bash
for BR in main staging; do
  gh api -X PUT "repos/reese8272/creatorclip/branches/$BR/protection" \
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
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
done
```

Notes:
- `required_pull_request_reviews: null` — **solo maintainer can't approve their own PR**,
  so a required-review rule would deadlock merges. Add it (`required_approving_review_count: 1`)
  once the team is ≥2.
- `strict: true` — branch must be up to date with base before merge.
- `required_linear_history: true` + `allow_force_pushes: false` — clean, non-rewritable history.
- GitHub's modern equivalent is **Rulesets** (Settings → Rules → Rulesets); the same
  contexts/linear-history/force-push settings apply.

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
