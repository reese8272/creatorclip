# LEFT_OFF — Session Handoff Contract

> **Read this first.** This is the living "where we are right now" file. It is NOT a source-of-truth
> doc — those live in `docs/`. Update this at the end of every session.

**Last updated:** 2026-05-27
**Branch:** `issue-31-operability` — HEAD `84a02c1` ("fix: add python-multipart to requirements")
**Working tree:** clean (screenshot PNG untracked — safe to ignore)
**PR:** [#2 open](https://github.com/reese8272/creatorclip/pull/2) — CI was red (missing dep, now fixed),
re-running now; lint ✅ docker build ✅ unit tests ⏳ (should go green after the python-multipart push)

---

## 1. CURRENT FOCUS

**Stand up the beta: provision the DigitalOcean droplet, deploy the app, verify
`https://agenticlip.studio/health` returns `{"status":"ok"}`.**

The VM exists and is reachable (`ssh creatorclip-vm` works), but it is a completely bare Ubuntu
24.04 box — Docker is not installed, `/opt/autoclip` doesn't exist, nothing is running.

### → NEXT ACTIONS (in order)

**Step 0 — Confirm CI goes green and merge PR #2**
```bash
gh pr checks 2 --watch   # wait for Unit tests to turn green
gh pr merge 2 --squash   # or merge via GitHub UI
```
Must merge before provisioning — the current `ghcr.io/reese8272/creatorclip:latest` image was
built from the broken `main` (missing `billing.tiers` + missing `python-multipart`). Merging
builds a clean image that actually boots.

**Step 1 — Provision the VM** (I can do this via `ssh creatorclip-vm`)
```bash
# Install Docker Engine + Compose plugin
curl -fsSL https://get.docker.com | sh
apt-get install -y docker-compose-plugin

# Create deploy dir
mkdir -p /opt/autoclip
cd /opt/autoclip

# Pull docker-compose.prod.yml from the repo
curl -fsSL https://raw.githubusercontent.com/reese8272/creatorclip/main/docker-compose.prod.yml \
  -o docker-compose.prod.yml
```

**Step 2 — Build `/opt/autoclip/.env`** — I generate the crypto keys; **you provide**:

| Secret | Where to get it |
|---|---|
| `GOOGLE_OAUTH_CLIENT_ID` + `GOOGLE_OAUTH_CLIENT_SECRET` | console.cloud.google.com → APIs & Services → Credentials |
| `CLOUDFLARE_TUNNEL_TOKEN` | one.dash.cloudflare.com → Zero Trust → Networks → Tunnels → your tunnel → Configure → token |
| `R2_ACCOUNT_ID` + `R2_ACCESS_KEY_ID` + `R2_SECRET_ACCESS_KEY` + `R2_BUCKET` | Cloudflare → R2 → Manage R2 API Tokens |
| `STRIPE_SECRET_KEY` + `STRIPE_PUBLISHABLE_KEY` + `STRIPE_WEBHOOK_SECRET` | dashboard.stripe.com → Developers → API keys / Webhooks |
| `ANTHROPIC_API_KEY` ✅ / `VOYAGE_API_KEY` ✅ / `DEEPGRAM_API_KEY` ✅ | already confirmed valid |

**Step 3 — Fix tunnel ingress rule** (DigitalOcean is already Cloudflare — just edit the
public-hostname ingress for `agenticlip.studio` to point at `app:8000`, not `localhost:*`)

**Step 4 — Deploy + smoke test**
```bash
cd /opt/autoclip
docker compose -f docker-compose.prod.yml up -d
# doctor runs automatically in deploy.yml preflight, but also:
docker compose -f docker-compose.prod.yml exec app python scripts/doctor.py
curl -s https://agenticlip.studio/health
```

---

## 2. WHAT WORKS NOW (do not re-investigate)

- ✅ **`ssh creatorclip-vm` connects** — `id_ed25519` is now authorized on the droplet (`root@ubuntu-s-4vcpu-8gb-nyc1`). The `creatorclip-vm` alias is in `~/.ssh/config`.
- ✅ **PR #2 on `issue-31-operability`** — carries the full operability kit + 3 boot-crash bug fixes (see §3); CI going green after the python-multipart push.
- ✅ **API keys confirmed live** — Anthropic, Voyage, Deepgram all authenticate (run `python3.12 scripts/doctor.py --full` to reverify anytime).
- ✅ **`ruff check .` + `ruff format --check .`** — both clean across all 87 files.
- ✅ **`313 tests pass** locally (against real Redis); 7 integration tests deselected (need live Postgres).
- ✅ **Redis service wired into CI** — `ci.yml` now runs a `redis:7-alpine` service so the rate-limiter tests don't fail in CI.
- ✅ **`scripts/doctor.py`** — offline/full/json modes, redacted output, deploy gate.
- ✅ **`docs/SECRETS.md`** — canonical registry of every secret + the creatorclip/autoclip/agenticlip naming map.
- ✅ **`docs/ACCESS.md`** — click-by-click SSH + CI deploy key + Cloudflare Tunnel runbook.
- ✅ **`docker-compose.prod.yml`** — cloudflared service + auto-heal + healthchecks + no host port + no `--reload`.

---

## 3. THREE BOOT-CRASH BUGS FIXED THIS SESSION

All three were in the unpushed `main` commit (`41016e6`). The app could not start on a clean install.

| Bug | Symptom | Fix |
|---|---|---|
| `billing.tiers` deleted but still imported in `routers/clips.py` | `ModuleNotFoundError` on `import main` | Replaced `require_render` with `check_positive_balance` from `billing.ledger` |
| `python-multipart` missing from `requirements.txt` | `RuntimeError: Form data requires python-multipart` on `import main` | Added `python-multipart==0.0.20` to `requirements.txt` |
| arm64 image built for x86 droplet | Wasted ~2× CI build time | `docker-publish.yml` changed to `platforms: linux/amd64` only |

---

## 4. KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| **Public domain** | `agenticlip.studio` |
| **VM (DigitalOcean Droplet)** | `147.182.136.107` — Ubuntu 24.04, 4 vCPU / 8 GB, NYC1 |
| **SSH alias** | `ssh creatorclip-vm` (uses `~/.ssh/id_ed25519`, user `root`) |
| **Deploy dir on VM** | `/opt/autoclip` (doesn't exist yet — created in Step 1) |
| **Docker image** | `ghcr.io/reese8272/creatorclip:latest` |
| **GitHub repo** | `github.com/reese8272/creatorclip` |
| **PR #2** | `issue-31-operability` → `main` |
| **GitHub Actions secrets needed** | `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, `VPS_PORT`, `GHCR_TOKEN`, `PRODUCTION_URL` (var) |
| **Tunnel ingress** | must be `app:8000` — NOT `localhost:80` or `localhost:8000` |
| **Storage backend** | `r2` (decided for beta) |
| **Billing** | Stripe wired (decided for beta) |
| **Transcription** | `deepgram` (default, no GPU needed) |
| **Free trial** | 60 min granted on first login (`FREE_TRIAL_MINUTES=60`) |

---

## 5. CONSTRAINTS & GOTCHAS

- **Merge PR #2 before pulling the image.** The current `:latest` on GHCR was built from broken `main`. The new image builds when PR #2 merges (triggered by `docker-publish.yml` on push to `main`).
- **Tunnel ingress MUST be `app:8000`** (Docker Compose network hostname), not `localhost`. The `cloudflared` service runs inside the same Compose network — `localhost` inside it points at itself, not the app.
- **`config.py` exits on startup if any required var is missing.** Run `python scripts/doctor.py` first, before `docker compose up`, to see exactly what's wrong without wasting a deploy cycle.
- **GitHub secrets are write-only** — you can never read them back. The only way to check what's set is to run a deploy and let the doctor preflight catch a bad/missing secret.
- **`ssh creatorclip-vm` uses `id_ed25519`.** If you work from a different machine, that key won't be authorized. Add the new machine's public key via the DigitalOcean recovery console (see `docs/ACCESS.md` §1b).
- **`dump.rdb` is gitignored** — redis creates it in the CWD if started locally. It's already in `.gitignore`.
- **The Screenshot PNG in repo root** — `Screenshot 2026-05-27 124552.png` is untracked; safe to delete once you don't need it.

---

## 6. POINTERS

| Doc | Purpose |
|---|---|
| `docs/SECRETS.md` | Every secret: what it is, which of 5 locations it lives in, how to obtain/rotate |
| `docs/ACCESS.md` | SSH access, CI deploy key, Cloudflare Tunnel (click-by-click, tailored to this infra) |
| `docs/DEPLOYMENT.md` | Dev setup, K8s production target, pre-deploy checklists |
| `docs/RUNBOOKS.md` | `TOKEN_ENCRYPTION_KEY` + `JWT_SECRET_KEY` rotation procedures |
| `docs/DECISIONS.md` | All architectural decisions, including Issue 31 rationale (2026-05-27) |
| `docs/PROJECT_STATE.md` | Issue table — Issue 31 ✅ Done; Issues 23–28 (BETA) all 🔲 Not started |
| `docs/issues.md` | Full issue backlog with acceptance criteria |
| `.claude/settings.local.json` | Local Claude Code permissions (gitignored) |
| `~/.ssh/config` | `creatorclip-vm` alias definition |
| `~/.claude/projects/.../memory/MEMORY.md` | Auto-memory index for this project |
