# Staging Access — LLM-drivable harness hook-in

**Purpose:** give an LLM/agent (Claude Code) a consistent, SSH-reachable way to drive
the real CreatorClip app end-to-end, since the OAuth-gated browser UI can't be clicked
by an agent. Pairs with `scripts/llm_harness.py`.

**Last verified:** 2026-06-16 (Issue 142) — **staging is LIVE and the harness passes 10/10
against it.** Running as compose project `cc139` on the VM (`cc139-app-1` on `:8001`,
healthy), built from branch `issue-139-142-sweep` under the `creatorclip:staging` tag. Prod
(`autoclip-*`) untouched. The harness's Issue-139 flow confirmed live:
`linked_video_visible_non_clippable — origin=link clippable=False` and
`queue_source_less_409 — 409`.

---

## Automated staging-parity gate (Issue 298)

The manual runbook below is no longer the only consumer of this stack. On every prod
deploy, `.github/workflows/deploy.yml` runs a **`deploy-staging` gate job** on the
self-hosted runner BEFORE the prod `deploy` job:

1. Resolves the **exact GHCR image under test** from the triggering Docker-publish run
   (`ghcr.io/reese8272/creatorclip:sha-<7-char SHA>` — the `type=sha,prefix=sha-` tag
   format; never `:latest`, which can race a concurrent push).
2. Brings this file up as compose project **`ccstage`** with
   `STAGING_IMAGE=<sha- image> … up -d --no-build --pull never`.
3. Runs `alembic upgrade head` **in-container** against the **persistent** staging DB
   and asserts `alembic current` == `alembic heads` (silent-no-op guard).
4. Seeds fixtures (idempotent upsert) and runs the verified smoke below
   (`llm_harness --flow core`).
5. **Stops** `app`/`worker` but keeps `staging_postgres_data` — the volume's persistence
   is the point: the next run migrates a **data-bearing** database, not a fresh one.
   (Motivating incident 2026-07-02: CI's fresh-DB bootstrap passed while prod's
   data-bearing DB failed the same migration.)

The prod job declares `needs: deploy-staging`; break-glass is the `workflow_dispatch`
input `skip_staging=true` (staging infra broken — never to bypass a failing migration).
See `docs/DEPLOYMENT.md` → "Staging-parity gate".

### Parity matrix (prod ⇄ staging)

Enforced by `tests/test_ci_config.py::test_staging_prod_compose_parity` — update the
test and this matrix together.

| Component | prod (`docker-compose.prod.yml`) | staging (`docker-compose.staging.yml`) | Parity |
|-----------|----------------------------------|----------------------------------------|--------|
| app/worker image | `ghcr.io/reese8272/creatorclip:${IMAGE_TAG:-latest}` | `${STAGING_IMAGE:-creatorclip:staging}` | Gate injects the exact `sha-` image prod will run |
| postgres | `pgvector/pgvector:pg16` | `pgvector/pgvector:pg16` | **MATCH** (test-enforced) |
| redis | `redis:7-alpine` | `redis:7-alpine` | **MATCH** (test-enforced) |
| pgbouncer | — (app connects direct) | `edoburu/pgbouncer:v1.25.2-p0` (transaction mode) | **Allowlisted staging-only inversion** — staging exercises the production-K8s topology (Issue 112) |
| cloudflared / autoheal | present | absent | Prod-only edge/ops plumbing, not under test by the gate |

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

## Resolved blocker (history)

The old staging (`root-*` project) was permanently degraded:
`{"status":"degraded","postgres":"error","redis":"ok"}` with `FATAL: server login failed:
wrong password type`. Root cause was **not** the RLS roles — it was PgBouncer `AUTH_TYPE=md5`
against Postgres 16's `scram-sha-256` password encryption. Fixed in
`docker-compose.staging.yml` (`AUTH_TYPE: scram-sha-256`). The old `root-*` stack (2-week-old
image, pre-Issue-139) was torn down and replaced by the `cc139` project built from this branch.
(The pinned `edoburu/pgbouncer:1.23.1-p3` tag was also removed from Docker Hub; staging uses
the cached `edoburu/pgbouncer:latest` — re-pin to a valid tag when convenient.)

---

## Runbook — the exact, verified steps (project `cc139`, checkout `/opt/autoclip/src`)

> Run from `creatorclip-vm`. Isolated from prod: separate DB `creatorclip_staging`, Redis db
> index 1, port 8001, distinct compose project `cc139`, and image tag `creatorclip:staging`
> (NEVER `:latest` — prod shares that tag). Never touches prod data.

1. **Update the checkout to the branch** (feature branch does NOT auto-deploy; deploy.yml is
   `main`-only):
   ```bash
   git push origin <branch>                              # locally
   ssh creatorclip-vm 'git config --global --add safe.directory /opt/autoclip/src; \
     cd /opt/autoclip/src && git fetch origin <branch> && git reset --hard origin/<branch>'
   ```
2. **Wire the env** (reuse the staging secrets) and **stop the old staging** to free `:8001`:
   ```bash
   ssh creatorclip-vm 'cp /root/.env /opt/autoclip/src/.env; \
     docker compose -p root -f /root/docker-compose.staging.yml down'   # old broken stack
   ```
3. **Build the branch image + bring up the stack** (build explicitly, then up WITHOUT pulling —
   the buildable image must not be pulled, and the cached third-party images stay cached):
   ```bash
   ssh creatorclip-vm 'cd /opt/autoclip/src && \
     docker compose -p cc139 -f docker-compose.staging.yml build app && \
     docker compose -p cc139 -f docker-compose.staging.yml up -d --no-build --pull never'
   ```
4. **Migrate + seed** (applies migration **0024 video_origin_enum**; seed prints `CC_CREATOR_ID`):
   ```bash
   ssh creatorclip-vm 'cd /opt/autoclip/src && \
     docker compose -p cc139 -f docker-compose.staging.yml exec -T app alembic upgrade head && \
     docker compose -p cc139 -f docker-compose.staging.yml exec -T app python tests/perf/seed_staging.py'
   ```
5. **Confirm health:**
   ```bash
   ssh creatorclip-vm 'curl -s http://localhost:8001/health'   # → {"status":"ok",...}
   ```

## Running the harness against staging (verified)

The app is baked into the image, so just exec it (the container has `httpx`+`jwt` and its own
`JWT_SECRET_KEY`):
```bash
ssh creatorclip-vm 'cd /opt/autoclip/src && \
  docker compose -p cc139 -f docker-compose.staging.yml exec -T app sh -c \
   "CC_BASE_URL=http://localhost:8000 CC_JWT_SECRET=\$JWT_SECRET_KEY \
    CC_CREATOR_ID=00000000-1111-2222-3333-444444444444 python scripts/llm_harness.py --flow all"'
```
The harness exits non-zero if any REQUIRED step fails. The `issue139` flow is a live regression
for the linked-video fix (links a fixed test video, asserts it appears with `clippable:false`,
and that queuing a source-less video 409s). Add `--flow core` for read-only smoke.

## Teardown / logs

```bash
ssh creatorclip-vm 'cd /opt/autoclip/src && docker compose -p cc139 -f docker-compose.staging.yml logs --tail 100 app'
ssh creatorclip-vm 'cd /opt/autoclip/src && docker compose -p cc139 -f docker-compose.staging.yml down'   # leaves prod untouched
```
