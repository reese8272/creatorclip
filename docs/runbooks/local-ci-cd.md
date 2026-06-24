# Runbook — Hybrid CI/CD (local pre-push gate + self-hosted CI)

**Decision:** `docs/DECISIONS.md` — 2026-06-23, "Hybrid self-hosted + local CI/CD".
**Goal:** stop burning GitHub-hosted Actions minutes and get CI feedback *right here* in the
terminal, without standing up a separate CI service.

The model has two layers:

| Layer | Where | Runs | Cost |
|-------|-------|------|------|
| **1 — pre-push hook** | your dev machine | fast Docker-free gates, before a push leaves the box | free, instant |
| **2 — `ci.yml`** | self-hosted runner on the prod VM | full suite incl. Docker-only gates | 0 GH-hosted minutes |

Deploy is unchanged: push `main` → `docker-publish.yml` (self-hosted) → `deploy.yml` (self-hosted,
smoke + auto-rollback). CI does **not** gate deploy.

---

## Layer 1 — local pre-push gate (already active on this machine)

One-time per clone:

```bash
bash scripts/setup_hooks.sh      # sets core.hooksPath → .githooks
```

What runs on `git push` (the `--fast` profile in `scripts/ci_local.sh`):

- `ruff format --check` + `run_layer0.py --gates ruff,mypy,bandit` (lint, types, SAST)
- `pytest -m "not integration"` (needs local Redis on :6379)
- frontend: `eslint`, `vitest run`, `tsc -b && vite build`

Run it by hand any time:

```bash
scripts/ci_local.sh            # fast profile (same as the hook)
scripts/ci_local.sh --full     # adds pip-audit (network) + coverage gate
```

Behaviour notes:

- It auto-activates `.venv`. A gate whose tool/service is genuinely absent (Redis down,
  `frontend/node_modules` missing) is **skipped with a warning, not failed** — a missing local
  daemon must never block every push. Layer 2 always runs those with the service present.
- Bypass for one push: `git push --no-verify` **or** `CI_LOCAL_SKIP=1 git push`.

The Docker-only gates (integration, eval/ffmpeg, playwright, migration-lint, docker-build) are
**not** reproduced locally — this box has no Docker/Postgres. They run in Layer 2.

---

## Layer 2 — self-hosted CI on the prod VM

`ci.yml` jobs are `runs-on: self-hosted` (2026-06-23). They run on the same runner that
`docker-publish.yml` / `deploy.yml` already use (installed by `scripts/setup-runner.sh`).

### Why this is safe on the prod box
- Prod Postgres/Redis publish **no host ports** (`docker-compose.prod.yml`), so CI's `:5432`/`:6379`
  service containers do **not** collide with production.
- `actions/cache` and Docker `type=gha` cache hit GitHub's cache backend — free, and they do **not**
  consume Actions *minutes* (only GH-hosted compute does).

### One-time VM prerequisites
The CI jobs `sudo apt-get install ffmpeg libpq-dev gcc` and use Node 22 + Python 3.12. To avoid a
job-time passwordless-sudo dependency, install them once on the runner host. `setup-runner.sh` now
does this; to add CI support to an **already-installed** runner without re-registering it:

```bash
ssh root@147.182.136.107
apt-get update -q
apt-get install -y --no-install-recommends ffmpeg libpq-dev gcc python3.12 python3.12-venv
# Node 22 (if not present): use your existing nodesource/nvm setup.
```

### Single runner = serial (important)
With one runner, a push to `main` would queue `ci.yml`'s ~12 jobs **and** `docker-publish` on the
same runner, so a deploy could wait behind CI. **Interim mitigation (2026-06-24):** `ci.yml`'s
`push` trigger is removed — CI runs on **PRs + `workflow_dispatch` only**, so a main-push deploy
never competes with CI on the shared runner. The local pre-push hook gates direct-to-main pushes.
To restore per-push CI (and `concurrency: cancel-in-progress` keeping only the latest commit
queued), register a **second runner**, then add `push: branches: [main, staging]` back to `ci.yml`:

```bash
ssh root@147.182.136.107
# Second runner in its own dir + systemd unit; same registration token flow as
# scripts/setup-runner.sh, but --name autoclip-prod-vm-2 and a fresh _work dir.
sudo -u github-runner mkdir -p /opt/github-runner-2 && cd /opt/github-runner-2
# ... download + ./config.sh --name autoclip-prod-vm-2 --labels self-hosted,linux,x64,prod ...
./svc.sh install github-runner && ./svc.sh start
```

GitHub then schedules CI and the deploy path across both runners concurrently.

### Watch a run right here (no browser)
```bash
gh run list --limit 5
gh run watch <run-id> --exit-status
```

---

## Upgrade trigger — move CI off the prod VM

Reusing the prod VM is the right call **now** (pre-launch, no users, low PR volume). Move CI to a
dedicated small box (~$6–12/mo) when **either**:
1. real users are served from that VM (don't run test code next to live traffic), **or**
2. PR/CI volume makes the single-runner serial execution or VM load painful.

Point a new runner at that box with the same `setup-runner.sh` flow and remove the `prod` label
from the CI runner.
