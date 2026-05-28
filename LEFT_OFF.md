# LEFT_OFF — Session Handoff Contract

> **Read this first.** This is the living "where we are right now" file. Not a changelog, not a
> source of truth — those live in `docs/`. Update this at the end of every session.

**Last updated:** 2026-05-28
**Branch:** `main` — HEAD `4f86fca`
**Working tree:** clean (one untracked PNG screenshot, safe to delete)
**Ahead/behind origin/main:** 0 / 0 (fully in sync)

---

## 1. CURRENT FOCUS

**Fix the three remaining blockers so a real user can complete the OAuth login flow end to end.**

The beta is live and the health endpoint is green, but the first real login attempt crashes the
app. Three bugs need to be fixed before handing the beta to anyone.

### → NEXT ACTIONS (in order)

**Bug 1 — OAuth callback crashes (highest priority, breaks login)**

`routers/auth.py:66` unpacks `creator, is_new = await upsert_creator(...)` but
`youtube/oauth.py:136` declares `-> Creator` and returns a single object, not a tuple.

Fix options (pick one):
- Change `upsert_creator` to return `tuple[Creator, bool]` (was_new flag)
- OR change the caller to `creator = await upsert_creator(...)` and remove the `is_new` branch

Check what `is_new` is used for at `routers/auth.py:75` before deciding.

```bash
sed -n '60,90p' routers/auth.py    # see full callback context
sed -n '129,155p' youtube/oauth.py # see full upsert_creator
```

**Bug 2 — CI integration tests failing (alembic migration)**

The migration fix landed on main (`47c665f`) but integration tests in CI are still failing at
`alembic upgrade head`. Likely cause: CI uses a fresh postgres — check if the fix is actually
running in CI or if there is a separate issue.

```bash
gh run view 26580687999 --log-failed   # full failure log
```

**Bug 3 — CD deploy fails (GHCR 403 on VM)**

The deploy workflow SSHs into the VM and runs `docker compose pull`, but GHCR returns 403.
The `GHCR_TOKEN` secret is set in GitHub Actions but the deploy step on the VM may not be using
it to log in before pulling. Check `.github/workflows/deploy.yml` — look for the docker login step.

```bash
cat .github/workflows/deploy.yml
gh run view 26580745746 --log-failed
```

---

## 2. WHAT WORKS NOW (do not re-investigate)

- ✅ **`https://autoclip.studio/health`** → `{"status":"ok","postgres":"ok","redis":"ok"}`
- ✅ **`/auth/login`** redirects correctly to Google OAuth (302, correct scopes + redirect_uri)
- ✅ **All 7 Docker containers** running and healthy on the VM
- ✅ **19 DB tables** created — `alembic upgrade head` succeeded on the live VM
- ✅ **Cloudflare Tunnel** (`autoclip-prod`) connected — DNS `autoclip.studio → tunnel`
- ✅ **GHCR login on VM** — `docker login ghcr.io` with `read:packages`-scoped token works manually
- ✅ **GitHub Actions secrets** — all 6 set: `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, `VPS_PORT`, `GHCR_TOKEN`, `PRODUCTION_URL`
- ✅ **Google OAuth consent screen** — changed to External; `reesepludwick@gmail.com` added as test user
- ✅ **Migration fix on main** — SQLAlchemy 2.0.36 + psycopg async ignores `create_type=False`; fix removes manual `op.execute("CREATE TYPE...")` calls
- ✅ **MCP servers connected** — Cloudflare (`mcp.cloudflare.com`), Deepgram (needs `DEEPGRAM_API_KEY` in env to connect), Stripe, Google Workspace (Gmail/Drive/Calendar)

---

## 3. THE ARC THAT LED HERE

1. **Issue 31 (operability kit)** merged as PR #2 — secrets registry, doctor script, deploy hardening, auto-heal
2. **VM provisioned** — Docker installed, `/opt/autoclip/` created, `docker-compose.prod.yml` deployed
3. **All secrets collected** — Google OAuth, Stripe, R2, Cloudflare Tunnel, AI keys — `.env` written to VM
4. **Cloudflare wired via MCP** — R2 API token created, tunnel `autoclip-prod` created, DNS CNAME set, ingress configured
5. **Domain switched** — `agenticlip.studio` (not in Cloudflare) → `autoclip.studio` (active zone)
6. **Image built on VM** — GHCR auth lacked `read:packages`; worked around by git-cloning and building locally on VM
7. **Migration bug fixed** — SA 2.0 `ENUM._on_table_create` ignores `create_type=False`; removed manual enum DDL
8. **Beta went live** — health endpoint green, OAuth redirect working, but login callback crashes (Bug 1)

---

## 4. KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| **Public URL** | `https://autoclip.studio` |
| **VM** | `147.182.136.107` — Ubuntu 24.04, 4 vCPU / 8 GB, NYC1 |
| **SSH alias** | `ssh creatorclip-vm` (key: `~/.ssh/id_ed25519`, user: `root`) |
| **Deploy dir on VM** | `/opt/autoclip/` |
| **Source clone on VM** | `/opt/autoclip/src/` (used for local image build) |
| **Active tunnel** | `autoclip-prod` (`db79b904-9cbf-4a79-b336-3b8195e6d37b`) |
| **Cloudflare zone** | `autoclip.studio` (zone `764913b08938704d661e6613f0926ac9`) |
| **R2 bucket** | `creatorclip-beta` |
| **Docker image** | `ghcr.io/reese8272/creatorclip:latest` |
| **GitHub repo** | `github.com/reese8272/creatorclip` (private) |
| **Google OAuth client ID** | `742666675967-1kru9898ec3gvm8f0lt89mj54drc4ft0.apps.googleusercontent.com` |
| **App secrets on VM** | `/opt/autoclip/.env` (600 perms, not in git — see `docs/SECRETS.md`) |

---

## 5. CONSTRAINTS & GOTCHAS

- **Image built locally on VM, not from GHCR** — CD deploy is broken (Bug 3). Until fixed, redeploy manually: `cd /opt/autoclip/src && git pull && docker build -t ghcr.io/reese8272/creatorclip:latest . && docker compose -f /opt/autoclip/docker-compose.prod.yml up -d`
- **`cloudflared` must be recreated, not just restarted** — `docker restart` reuses the token baked in at creation. Use `docker compose up -d --force-recreate cloudflared` to pick up a new `CLOUDFLARE_TUNNEL_TOKEN`.
- **Do NOT delete `autoclip-prod` tunnel** — two earlier API-created tunnels had "Invalid tunnel secret" errors that were never resolved. `autoclip-prod` is the only working one.
- **`docs/SECRETS.md` still references `agenticlip.studio`** — needs updating to `autoclip.studio`, new tunnel ID, new R2 token name (`creatorclip-r2-beta`).
- **Pushing to `main` triggers CI + deploy** — both currently failing. Fix Bugs 2 and 3 before merging anything that runs in CI or deploys.
- **Google OAuth app is in Testing mode** — only test users can sign in. Google verification required for YouTube scopes before opening to real users.
- **`GHCR_TOKEN` in GitHub Actions** has `read:packages` + `repo` scope. The deploy workflow likely needs an explicit `docker login ghcr.io -u reese8272 --password-stdin <<< "$GHCR_TOKEN"` step on the VM before `docker compose pull`.

---

## 6. POINTERS

| Doc | Purpose |
|---|---|
| `docs/SECRETS.md` | Every secret — **needs update**: `agenticlip.studio` → `autoclip.studio` |
| `docs/ACCESS.md` | SSH access, CI deploy key, Cloudflare Tunnel runbook |
| `docs/DEPLOYMENT.md` | Dev setup, pre-deploy checklists |
| `docs/PROJECT_STATE.md` | Issue table — Issue 31 ✅ Done; Issues 23–28 (BETA) all 🔲 Not started |
| `docs/issues.md` | Full issue backlog with acceptance criteria |
| `docs/DECISIONS.md` | Architectural decisions — needs entry: domain change + migration fix |
| `.github/workflows/deploy.yml` | CD pipeline — currently broken (Bug 3) |
| `~/.claude/projects/.../memory/MEMORY.md` | Auto-memory index for this project |
