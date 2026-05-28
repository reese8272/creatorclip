# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a source of
> truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-05-28
**Branch:** `main` — HEAD `e942de3`
**Working tree:** clean (one untracked `.claude/` dir, ignored)
**Ahead/behind origin/main:** **+8 / -0** — 8 commits unpushed
**Production:** green on `origin/main` (`287c291`); unpushed work has not deployed yet

---

## 1. CURRENT FOCUS

**Push the 8-commit Phase 2 hardening batch to `origin/main` and confirm the deploy.**

Phase 2 hardening Issues 32–35, 40–42, and 44 are merged on local `main` and the full test
suite is green (326 passed, 1 skipped, 16 deselected). Nothing else is in-flight.

### → NEXT ACTIONS (in order)

**1. Verify suite + push.**

```bash
.venv/bin/python -m pytest -q          # confirm 326 passed
git push origin main                    # triggers CI + Deploy to production
```

**2. Watch the deploy.**

```bash
gh run watch                            # or:
gh run list --workflow="Deploy to production" --limit 3
```

If CI passes but the deploy step fails: check `gh run view <id> --log-failed`. The
most recent deploy iteration was about the smoke test using `curl` inside the app
container (resolved by switching to `python urllib` — see commit `287c291`).

**3. Delete the stale `issue-31-operability` branch.**

It's the unsquashed history of PR #2, already squash-merged as `d7c1f20` on main.
23 unique commits, 8 behind main, all superseded.

```bash
git branch -D issue-31-operability
git push origin :issue-31-operability   # if the branch exists on remote too
```

**4. Pick the next Phase 2 hardening issue.**

Per `docs/issues.md` and `docs/PROJECT_STATE.md`, **Issue 36 (OAuth token lifecycle
hardening)** is next — three SEV-1 fixes in `routers/auth.py`, `youtube/oauth.py`,
`youtube/data_api.py`, `youtube/analytics.py`. After 36, Issue 37 (SDK timeouts) and
38 (sync-in-async + held sessions) unblock the rest of the worker/Celery chain
(39, 43, 45–47).

---

## 2. WHAT WORKS NOW (do not re-investigate)

- ✅ **Phase 2 hardening Issues 32, 33, 34, 35, 40, 41, 42, 44** — closed; full test
  suite green (326 passed, 1 skipped, 16 deselected integration tests).
- ✅ **Beta deploy pipeline** — last `Deploy to production` workflow on `287c291`
  succeeded at 17:04 UTC. Production health check on `https://autoclip.studio/health`
  passing.
- ✅ **Smoke test fixed** — using `python urllib` inside the app container instead of
  `curl` (curl isn't in the image; port 80 isn't exposed to host; Cloudflare bypassed).
- ✅ **Cloudflare Tunnel `autoclip-prod`** stable — `autoclip.studio → tunnel → app:8000`.
- ✅ **Migration `0001_initial_schema`** idempotent — SA 2.0 async + Postgres ENUM
  collision fixed; live VM has all tables.
- ✅ **OAuth callback** — `upsert_creator` signature mismatch closed (`Bug 1` from
  prior session).
- ✅ **GHCR auth on VM** — deploy workflow logs in cleanly before pull.
- ✅ **5 parallel agents (Issues 35, 40, 41, 42, 44) merged + worktrees cleaned**.

---

## 3. THE ARC THAT LED HERE

1. **Issue 31 (operability kit)** merged as PR #2 — secrets registry, doctor script,
   deploy hardening, auto-heal.
2. **VM provisioned + beta live** on `autoclip.studio`.
3. **Three "bring-up" bugs** (OAuth callback crash, CI migration, GHCR 403) all fixed
   between earlier sessions and the smoke-test iteration that ended with `287c291`.
4. **Project audit (2026-05-28 AM)** surfaced 24 hardening + coverage findings, filed
   as Phase 2 Issues 32–55 in `docs/issues.md`.
5. **Issues 32–34 closed** in the prior session — starlette pin, cross-creator leak in
   improvement brief, idempotent minute deduction (`MinuteDeduction` ledger).
6. **5 parallel agents (this session)** closed Issues 35, 40, 41, 42, 44 in isolated
   worktrees. Three (35, 40, 44) merged via `git merge --no-ff`; two (41, 42) leaked
   directly to main's working tree and were bundled into the catch-up commit.
7. **Post-merge test fixes (this session)** — Issue 40 test was patching the wrong
   import path; Issue 44 test used `importlib.reload` that orphaned every other
   module's `from config import settings` reference. Both rewritten.

---

## 4. KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| **Public URL** | `https://autoclip.studio` |
| **VM** | `147.182.136.107` — Ubuntu 24.04, 4 vCPU / 8 GB, NYC1 |
| **SSH alias** | `ssh creatorclip-vm` (key: `~/.ssh/id_ed25519`, user: `root`) |
| **Deploy dir on VM** | `/opt/autoclip/` |
| **Active tunnel** | `autoclip-prod` (`db79b904-9cbf-4a79-b336-3b8195e6d37b`) |
| **Cloudflare zone** | `autoclip.studio` (zone `764913b08938704d661e6613f0926ac9`) |
| **R2 bucket** | `creatorclip-beta` |
| **Docker image** | `ghcr.io/reese8272/creatorclip:latest` |
| **GitHub repo** | `github.com/reese8272/creatorclip` (private) |
| **App secrets on VM** | `/opt/autoclip/.env` (chmod 600; see `docs/SECRETS.md`) |
| **GitHub Actions secrets** | `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, `VPS_PORT`, `GHCR_TOKEN`, `PRODUCTION_URL` |
| **Test runner** | **`.venv/bin/python -m pytest -q`** (system `python3.12` is broken — see Gotchas) |
| **Active issue** | **Issue 36 — OAuth token lifecycle hardening (SEV-1)** |
| **Last completed** | Issue 44 — Auth boundary hardening (SEV-1, three sub-fixes) |

---

## 5. CONSTRAINTS & GOTCHAS

- **System `python3.12` cannot run pytest** — langsmith installed a newer pydantic
  (2.46.4) into the uv-managed Python while user-site has pydantic-core 2.27.2.
  `python3.12 -m pytest` fails at plugin-load with `SystemError: pydantic-core
  incompatible`. **Always use `.venv/bin/python` for tests.** Fix (deferred):
  `python3.12 -m pip install --user --break-system-packages "pydantic-core>=2.46.4"`.
- **Pushing to `main` triggers CI + production deploy.** Don't push speculative
  hardening work without running the suite first.
- **5 worktrees existed during this session at `.claude/worktrees/agent-*`** — all
  cleaned up. If you see leftover worktrees in a future session, check
  `git worktree list` and `git worktree remove --force <path>`.
- **`importlib.reload(config)` is poison in tests.** It orphans every other module's
  `from config import settings` reference (those still point to the pre-reload
  object). Mutate `settings` attributes via `monkeypatch.setattr(settings, "X", v)`
  instead. Lesson learned via Issue 44 agent's test.
- **`from billing.ledger import check_positive_balance`** in `routers/videos.py`
  binds the function in the router namespace — patching the source path
  (`billing.ledger.*`) won't intercept the call. Patch
  `routers.videos.check_positive_balance` instead.
- **`docker compose up -d --force-recreate cloudflared`** is required to pick up
  a new tunnel token — `docker restart` reuses the token baked at creation.
- **Google OAuth app is in Testing mode** — only test users can sign in. Google
  verification required for YouTube scopes before opening to real users.
- **Branch `issue-31-operability` is stale** — 23 ahead / 8 behind main, ex-PR-#2
  history already squash-merged. Delete on next push.

---

## 6. POINTERS

| Doc | Purpose |
|---|---|
| `docs/PROJECT_STATE.md` | Issue table + current status — Phase 2 hardening in progress |
| `docs/issues.md` | Full issue backlog with acceptance criteria — Issue 36 is next |
| `docs/DECISIONS.md` | Architectural decisions — has fresh 2026-05-28 entries for Issues 32–35, 40–42, 44 |
| `docs/SOT.md` | Architecture + data model — `MinuteDeduction` ledger row added |
| `docs/COMPLIANCE.md` | YouTube ToS + Findings & Fixes Log (Issue 33 cross-creator leak entry) |
| `docs/SECRETS.md` | Every secret — needs update: `agenticlip.studio` → `autoclip.studio` |
| `docs/ACCESS.md` | SSH access, CI deploy key, Cloudflare Tunnel runbook |
| `docs/DEPLOYMENT.md` | Dev setup, pre-deploy checklists |
| `.github/workflows/deploy.yml` | CD pipeline — currently green after smoke-test iteration |
| `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/MEMORY.md` | Auto-memory index for this project |
