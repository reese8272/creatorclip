# Render Deploy — CreatorClip Beta

Beta-only deployment runbook for hosting CreatorClip on [Render](https://render.com)
via the `render.yaml` Blueprint at the repo root.

> **Scope.** Render is the **beta** host. The full-scale production target remains
> GKE Autopilot + Cloud SQL PG16 + KEDA (Helm chart at `deploy/charts/creatorclip/`,
> Issue 275 — the deploy-track linchpin). Render gets us a real internet-reachable
> beta with an always-on Celery worker without standing up Kubernetes first. The
> Render/GKE split is a beta hosting decision; see `docs/DECISIONS.md` (2026-06-24).

## 1. Topology

All five resources pin `region: oregon` so Render's private networking + low
latency hold across services.

```
                  ┌──────────────────────────────┐
   internet  ───▶ │ creatorclip-web  (web, $PORT) │  FastAPI + SPA, /health, /docs(off)
                  │   preDeploy: alembic upgrade  │
                  └───────────┬──────────────────┘
                              │ private network
        ┌─────────────────────┼─────────────────────────┐
        ▼                     ▼                           ▼
┌────────────────┐  ┌──────────────────┐      ┌────────────────────────┐
│ creatorclip-db │  │ creatorclip-     │      │ creatorclip-worker     │  Celery worker (always-on)
│ Postgres 16    │  │ keyvalue (Redis) │◀────▶│ creatorclip-beat       │  Celery beat (1 instance)
│ + pgvector     │  │ noeviction broker│      └────────────────────────┘
└────────────────┘  └──────────────────┘
```

- **creatorclip-web** — `web`, runtime `docker`, binds `0.0.0.0:$PORT`, healthcheck `/health`.
- **creatorclip-worker** — `worker`, always-on Celery worker (`--concurrency=2`).
- **creatorclip-beat** — `worker`, single-instance Celery beat (RedBeat).
- **creatorclip-keyvalue** — managed Key Value (Redis) broker/backend, `noeviction`.
- **creatorclip-db** — managed Postgres 16 with pgvector.

Each service builds its own image from the same `./Dockerfile` (a Blueprint has no
shared-image option). The web image is the one that needs the compiled SPA; the
worker/beat reuse the same Dockerfile for parity. A slimmer worker image is a later
optimization, not a beta blocker.

## 2. Prerequisites

- A Render account with this repo connected (GitHub/GitLab).
- External API keys provisioned in their provider dashboards (Issue 25):
  Anthropic, Voyage, Deepgram, Cloudflare R2 (account id + access keys + bucket),
  Stripe (secret + webhook signing secret + publishable key).
- A Google OAuth 2.0 client (Issue 26): client id + secret.
- Fresh `TOKEN_ENCRYPTION_KEY` (Fernet) generated **just for Render** — do NOT reuse
  the VM's. Generate with:
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

Cross-reference `docs/SECRETS.md` for the canonical origin of every key.

## 3. Provisioning via Blueprint

1. Commit `render.yaml` to the default branch and push.
2. In the Render dashboard: **New > Blueprint**, select this repo.
3. Render parses `render.yaml` and creates:
   `creatorclip-db` (Postgres 16), `creatorclip-keyvalue` (Key Value, `noeviction`),
   `creatorclip-web`, `creatorclip-worker`, `creatorclip-beat`, and the
   `creatorclip-beta-env` env group.

## 4. Secret setup

At Blueprint creation Render prompts for every `sync: false` variable in
`creatorclip-beta-env`. Paste each (origins in `docs/SECRETS.md`):

| Var | Notes |
|-----|-------|
| `ANTHROPIC_API_KEY` | Anthropic console |
| `GOOGLE_OAUTH_CLIENT_ID` / `_SECRET` | Google Cloud console OAuth client |
| `TOKEN_ENCRYPTION_KEY` | **Fresh Fernet key for Render only** |
| `DEEPGRAM_API_KEY` | required (`TRANSCRIPTION_BACKEND=deepgram`) |
| `VOYAGE_API_KEY` | embeddings |
| `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` | **required to boot** in production (config validator); use test-mode keys if billing isn't live |
| `STRIPE_PUBLISHABLE_KEY` | frontend Stripe.js |
| `R2_ACCOUNT_ID` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` / `R2_BUCKET` | Cloudflare R2 |
| `METRICS_TOKEN` | else `/metrics` auto-disables in production (fail-safe) |
| `SENTRY_DSN` | optional |
| `ALLOWED_ORIGINS` / `APP_BASE_URL` / `OAUTH_REDIRECT_URI` | set after step 7 (the live URL is unknown until first deploy) |

`JWT_SECRET_KEY` uses `generateValue: true` — Render mints it; no manual entry.
`DATABASE_URL`, `DATABASE_MIGRATION_URL`, and `REDIS_URL` are injected from the
managed resources (`fromDatabase` / `fromService`) — never entered by hand.

> The app expects a `postgresql+psycopg://` DSN; Render injects a bare
> `postgresql://`. `config._normalize_async_pg_dsn` rewrites the scheme at load,
> so the injected `connectionString` works unchanged.

## 5. pgvector

The `CREATE EXTENSION vector` migration is part of the Alembic history. Render's
managed PG16 supports pgvector and grants the default role extension privileges,
so the web service's `preDeployCommand: alembic upgrade head` enables it on first
deploy. No manual `CREATE EXTENSION` step is required.

## 6. First deploy

1. Render builds the Docker image per service (web/worker/beat).
2. The **web** service runs `preDeployCommand: alembic upgrade head` after build,
   before start — a failed migration blocks the deploy (zero-downtime gate).
3. Web starts `uvicorn main:app --host 0.0.0.0 --port $PORT --workers 2`.
4. Worker starts `celery -A worker.celery_app worker --concurrency=2`.
5. Beat starts `celery -A worker.celery_app beat -S redbeat.RedBeatScheduler`.
6. Watch each service's deploy logs.

> **Migrations run on the web service only.** Worker and beat have no
> `preDeployCommand` so two Alembic runs cannot race the same DB.

## 7. OAuth wiring

1. Copy the live web URL (e.g. `https://creatorclip-web.onrender.com`).
2. In the `creatorclip-beta-env` group set:
   - `ALLOWED_ORIGINS=https://creatorclip-web.onrender.com` (no wildcard / no localhost)
   - `APP_BASE_URL=https://creatorclip-web.onrender.com`
   - `OAUTH_REDIRECT_URI=https://creatorclip-web.onrender.com/auth/callback`
3. Add that exact redirect URI to the Google Cloud console OAuth client (Issue 26).
4. Redeploy so the new env values take effect.

## 8. Smoke check (Issue 28)

```bash
BASE=https://creatorclip-web.onrender.com

# Liveness — expect 200, "status":"ok" (PG + Redis probes green)
curl -s $BASE/health

# /docs disabled in production (ENV=production) — expect 404
curl -s -o /dev/null -w '%{http_code}\n' $BASE/docs
```

- Complete one OAuth login end-to-end → confirm a `creators` row is created.
- Enqueue one clip job from the UI and confirm `creatorclip-worker` picks it up
  in its logs (proves the always-on worker + Key Value broker).
- Confirm a scheduled (beat) task fires — proves RedBeat holds its lock in Key Value.

## 9. Custom domain (optional)

Attach `autoclip.studio` in the web service's **Settings > Custom Domains**, point
DNS per Render's instructions, then re-point `ALLOWED_ORIGINS`, `APP_BASE_URL`,
`OAUTH_REDIRECT_URI`, and the Google console redirect URI to the new origin.

## 10. Rollback

- **App:** Render dashboard > service > **Deploys > Rollback** to a previous deploy
  (per service). Because migrations are forward-tested in `preDeployCommand`, a
  failed migration blocks the deploy rather than shipping a broken image.
- **DB:** `alembic downgrade <rev>` run manually via a one-off shell on the web
  service. Treat schema rollbacks as deliberate, data-loss-aware operations.

## 11. Cost & limits

- **Plans:** `starter` web/worker/beat; `basic-256mb` Postgres; `starter` Key Value.
  Bump web/worker to `standard` if uvicorn workers or ffmpeg renders need more headroom.
- **Ephemeral FS:** container disk is wiped on every deploy/restart. Media lives in
  **R2** (`STORAGE_BACKEND=r2`), and logs go to **stdout** (`LOG_DIR=""`), never to disk.
- **Key Value persistence:** the broker must use `maxmemoryPolicy: noeviction` so
  queued Celery jobs are never evicted; the free tier has no persistence, so use
  `starter`+.
- **Scale** is *not* Render's job — the GKE/Helm path (Issue 275) owns 10k+ scale.
  Render hosts the beta only.
