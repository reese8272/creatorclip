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
3. Verify on the staging stack (see `docs/STAGING_ACCESS.md`).
4. Open a PR `staging → main`. Merge when green → auto-deploys.

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
      "Docker build (smoke test)"
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
