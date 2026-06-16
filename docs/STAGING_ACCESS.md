# Staging Access — LLM-drivable harness hook-in

**Purpose:** give an LLM/agent (Claude Code) a consistent, SSH-reachable way to drive
the real CreatorClip app end-to-end, since the OAuth-gated browser UI can't be clicked
by an agent. Pairs with `scripts/llm_harness.py`.

**Last verified:** 2026-06-16 (Issue 142).

---

## What's already in place (verified)

- **SSH works.** `~/.ssh/config` has a `creatorclip-vm` alias → `root@147.182.136.107`
  (DigitalOcean droplet `ubuntu-s-4vcpu-8gb-nyc1`), using `~/.ssh/id_ed25519`. A non-interactive
  `ssh creatorclip-vm '…'` succeeds today.
- **Docker 29.5** on the box. **Prod** runs as the `autoclip-*` compose project (app on
  `:8000`, behind cloudflared → `autoclip.studio`).
- **A staging stack already exists** as the `root-*` compose project: `root-app-1`
  (`0.0.0.0:8001->8000`), `root-pgbouncer-1`, `root-postgres_staging-1`, `root-redis_staging-1`.
  Container env has `ENV=staging`, `JWT_SECRET_KEY` set, and `httpx`+`jwt` deps.
- **Deploy trigger is `main`-only** (`.github/workflows/deploy.yml` → on Docker-publish for
  branch `main`, or manual dispatch). **Pushing a feature branch does NOT deploy** — safe.

## ⚠️ Current blocker (must fix before the harness can drive staging)

`GET /health` inside `root-app-1` returns `{"status":"degraded","postgres":"error","redis":"ok"}`
— the staging app can't authenticate to its Postgres (`FATAL: server login failed: wrong
password type`). This is the RLS role-split (`creatorclip_app` / `creatorclip_migrate`) password
setup from `docs/DEPLOYMENT.md` ("RLS one-time setup") never completed on staging. Until this is
fixed, every authenticated endpoint 500s, so the harness only gets past `/health` (which itself
reports degraded). The staging image is also ~2 weeks old — it predates Issue 139.

---

## Runbook — stand up a correct staging on `:8001` with the latest branch

> Run from `creatorclip-vm`. This is isolated from prod (separate DB `creatorclip_staging`,
> Redis db index 1, port 8001). It never touches prod data.

1. **Get the code onto the box** (feature branch — does NOT auto-deploy):
   ```bash
   # locally: push the branch
   git push -u origin <branch>
   # on the VM, into an isolated staging checkout:
   ssh creatorclip-vm 'git -C /opt/autoclip/staging-src pull || \
     git clone -b <branch> https://github.com/reese8272/<repo> /opt/autoclip/staging-src'
   ```
2. **Bring up the staging stack** (compose file lives in the repo as
   `docker-compose.staging.yml`):
   ```bash
   ssh creatorclip-vm 'cd /opt/autoclip/staging-src && \
     docker compose -f docker-compose.staging.yml up -d --build'
   ```
3. **Fix the RLS role passwords once** (the documented one-time op — see
   `docs/DEPLOYMENT.md` → "RLS one-time setup"), then `alembic upgrade head` (applies
   migration **0024 video_origin_enum**):
   ```bash
   ssh creatorclip-vm 'cd /opt/autoclip/staging-src && \
     docker compose -f docker-compose.staging.yml exec -T app alembic upgrade head'
   ```
4. **Seed a creator** (prints `CC_CREATOR_ID`):
   ```bash
   ssh creatorclip-vm 'cd /opt/autoclip/staging-src && \
     docker compose -f docker-compose.staging.yml exec -T app python tests/perf/seed_staging.py'
   ```
5. **Confirm health is green:**
   ```bash
   ssh creatorclip-vm 'curl -s http://localhost:8001/health'   # → {"status":"ok",...}
   ```

## Running the harness against staging

The staging app listens on the VM's `localhost:8001` (not public). Two equivalent ways:

**A — inside the container** (it already has `httpx`+`jwt` and can read its own secret):
```bash
ssh creatorclip-vm 'cd /opt/autoclip/staging-src && \
  docker compose -f docker-compose.staging.yml cp scripts/llm_harness.py app:/tmp/h.py && \
  docker compose -f docker-compose.staging.yml exec -T app sh -c \
    "CC_BASE_URL=http://localhost:8000 CC_JWT_SECRET=\$JWT_SECRET_KEY \
     CC_CREATOR_ID=00000000-1111-2222-3333-444444444444 python /tmp/h.py --flow all"'
```

**B — port-forward + run the local harness:**
```bash
ssh -fN -L 18001:localhost:8001 creatorclip-vm
CC_JWT_SECRET=<staging JWT_SECRET_KEY> CC_CREATOR_ID=00000000-1111-2222-3333-444444444444 \
  python3 scripts/llm_harness.py --base-url http://localhost:18001 --flow all
```

The harness exits non-zero if any REQUIRED step fails; the `issue139` flow is a live regression
for the linked-video fix (links a fixed test video, asserts it appears with `clippable:false`,
and that queuing a source-less video 409s).

## Teardown / logs

```bash
ssh creatorclip-vm 'cd /opt/autoclip/staging-src && docker compose -f docker-compose.staging.yml logs --tail 100 app'
ssh creatorclip-vm 'cd /opt/autoclip/staging-src && docker compose -f docker-compose.staging.yml down'   # leaves prod untouched
```
