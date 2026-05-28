# LEFT_OFF — Session Handoff Contract

> **Read this first.** This is the living "where we are right now" file. It is NOT a source-of-truth
> doc — those live in `docs/`. Update this at the end of every session.

**Last updated:** 2026-05-28
**Branch:** `issue-31-operability` (ahead of main with migration fix)
**Working tree:** clean
**PR:** merged — `issue-31-operability` → `main` (squash commit `d7c1f20`)

---

## 1. CURRENT STATE — BETA IS LIVE ✅

**`https://autoclip.studio/health` returns `{"status":"ok","postgres":"ok","redis":"ok"}`**

All 7 containers running and healthy on the DigitalOcean droplet (`147.182.136.107`).

---

## 2. WHAT WAS DONE THIS SESSION

| Step | Status |
|---|---|
| Merged PR #2 (`issue-31-operability` → `main`) | ✅ |
| Installed Docker 29.5.2 on VM | ✅ |
| Created `/opt/autoclip/` deploy dir | ✅ |
| Wrote `/opt/autoclip/.env` with all secrets | ✅ |
| Built Docker image on VM (GHCR auth workaround) | ✅ |
| `docker compose up -d` — all 7 containers started | ✅ |
| Fixed Alembic migration bug (SA 2.0 + psycopg async) | ✅ |
| Ran `alembic upgrade head` — 19 tables created | ✅ |
| Cloudflare Tunnel authenticated and connected | ✅ |
| DNS CNAME live: `autoclip.studio → autoclip-prod tunnel` | ✅ |
| Smoke test: `https://autoclip.studio/health` → `{"status":"ok"}` | ✅ |

---

## 3. DOMAIN CHANGE

**`agenticlip.studio` was replaced with `autoclip.studio`** — `agenticlip.studio` is not in Cloudflare.
`autoclip.studio` is active and pointing at the new `autoclip-prod` tunnel.

Update any references to `agenticlip.studio` in docs, OAuth redirect URIs, etc.

---

## 4. KEY COORDINATES

| Thing | Value |
|---|---|
| **Public URL** | `https://autoclip.studio` |
| **Health endpoint** | `https://autoclip.studio/health` |
| **VM** | `147.182.136.107` — Ubuntu 24.04, 4 vCPU / 8 GB, NYC1 |
| **SSH alias** | `ssh creatorclip-vm` |
| **Deploy dir** | `/opt/autoclip/` |
| **Source on VM** | `/opt/autoclip/src/` (cloned for local build) |
| **Active tunnel** | `autoclip-prod` (`db79b904-9cbf-4a79-b336-3b8195e6d37b`) |
| **Cloudflare zone** | `autoclip.studio` (zone `764913b08938704d661e6613f0926ac9`) |
| **Docker image** | Built locally on VM from `/opt/autoclip/src` |
| **GitHub repo** | `github.com/reese8272/creatorclip` (private) |

---

## 5. OPEN ITEMS / NEXT ACTIONS

### Immediate (before real users)
- [ ] **Push migration fix to main** — `alembic/versions/0001_initial_schema.py` fix is on `issue-31-operability`, not yet on `main`. Create a PR or push directly.
- [ ] **Wire CI/CD deploy** — GitHub Actions `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, `VPS_PORT`, `GHCR_TOKEN`, `PRODUCTION_URL` secrets need setting so future pushes auto-deploy.
- [ ] **Fix GHCR auth** — run `gh auth refresh -h github.com -s read:packages` in a real terminal to get `read:packages` scope. Then `docker login ghcr.io` on VM so future deploys pull from GHCR instead of building locally.
- [ ] **Google OAuth redirect URI** — add `https://autoclip.studio/auth/callback` to the OAuth client in Google Cloud Console (may already be done).
- [ ] **Update `docs/SECRETS.md`** — reflects `agenticlip.studio`; needs update to `autoclip.studio` + new tunnel/R2 token names.

### Beta issues (Issues 23–28 in `docs/PROJECT_STATE.md`)
All 6 beta issues are still 🔲 Not started. These are the next engineering sprint.

---

## 6. CONSTRAINTS & GOTCHAS

- **Image is built locally on VM** (not pulled from GHCR) — until GHCR auth is fixed, re-deploys must `cd /opt/autoclip/src && git pull && docker build ...`
- **Migration fix not on main yet** — the `alembic/versions/0001_initial_schema.py` fix is committed on `issue-31-operability`. Merge or cherry-pick to main before next deploy.
- **`cloudflared` container must be recreated (not just restarted)** to pick up a new `CLOUDFLARE_TUNNEL_TOKEN` from `.env`. Use `docker compose up -d --force-recreate cloudflared`.
- **`tunnel_secret` API format** — Cloudflare's API-created tunnels had "Invalid tunnel secret" errors; workaround was creating via MCP with `autoclip-prod` tunnel. Do not delete this tunnel.
- **`.env` on VM is authoritative** — not in git. If VM is rebuilt, all secrets must be re-entered. See `docs/SECRETS.md` for the registry.

---

## 7. POINTERS

| Doc | Purpose |
|---|---|
| `docs/SECRETS.md` | Every secret — update `agenticlip.studio` → `autoclip.studio` |
| `docs/ACCESS.md` | SSH access, CI deploy key, Cloudflare Tunnel runbook |
| `docs/DEPLOYMENT.md` | Dev setup, pre-deploy checklists |
| `docs/PROJECT_STATE.md` | Issue table — Issue 31 ✅ Done; Issues 23–28 (BETA) all 🔲 Not started |
| `~/.ssh/config` | `creatorclip-vm` alias |
