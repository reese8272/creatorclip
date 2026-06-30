# CreatorClip — Deployment

**Last updated**: 2026-05-25
**Target scale**: 10,000+ concurrent creators

---

## Development (Docker Compose)

```bash
cp .env.example .env
# Fill in required vars in .env

docker compose up --build
# App at http://localhost:8000
# Docs at http://localhost:8000/docs (development only)

# Health check
curl http://localhost:8000/health
```

To run tests against the running stack:
```bash
pytest
# Integration tests (requires live postgres + redis):
pytest -m integration
```

---

## Production Target: Kubernetes

Docker Compose is **dev/test only**. Production at 10k+ scale requires Kubernetes.

> **Status (2026-06-22): the architecture is decided and the Helm chart is written, but it has NEVER
> run on a real cluster.** "Staging" today is a Docker-Compose project on the prod VM
> (`docs/STAGING_ACCESS.md`), so the connection-budget and load `[DEC]`s below are written against the
> GKE/Cloud SQL topology but verified only by construction. Closing this is **Issue 275** (GKE staging +
> first Helm deploy) — the linchpin — with the remaining chart gaps tracked as **Issues 276–280, 287**
> (Lane L12_K8S_DEPLOY) and the transcription-compute decision as **Issue 293**. See `docs/issues.md`.

### Decisions Made (see `docs/DECISIONS.md` for full rationale)

- [x] **Managed K8s provider**: GKE Autopilot — lowest-ops, no node management, Cloud SQL
  supports pgvector, Secret Manager integrates cleanly.
- [x] **Ingress + TLS**: nginx-ingress + Cloudflare Tunnel; TLS terminated at Cloudflare.
- [x] **Celery worker autoscaling**: KEDA with Redis `listLength` trigger on the `celery` queue.
- [x] **GPU / transcription**: Deepgram (hosted) for MVP; WhisperX selectable via config for
  self-hosted GPU cost optimization later. No GPU nodes needed at launch.
- [x] **Database**: Cloud SQL for PostgreSQL 16; pgvector extension enabled post-provision.
- [x] **Helm charts**: Written in `deploy/charts/creatorclip/`. See `deploy/README.md`.
- [x] **Secrets management**: GCP Secret Manager + External Secrets Operator syncs to K8s.
- [x] **Beat HA (Issue 263)**: RedBeat (`celery-redbeat==2.3.3`) replaces the file-backed
  `PersistentScheduler`. Beat stores its schedule and a distributed lock (key `redbeat::lock`,
  TTL 1500s) in Redis, preventing duplicate task scheduling across restarts. Beat liveness
  probe checks the heartbeat file mtime (300s threshold) — a crashed beat pod is detected
  and restarted by the kubelet within 60–180s. Beat still runs as 1 replica + Recreate
  strategy; RedBeat makes the restart safe. The 30-day YouTube ToS purge
  (`purge_stale_youtube_analytics`) cannot silently halt without the liveness probe.
  - **Redis HA (Issue 263):** beat HA requires Redis to survive pod-level restarts (the
    distributed lock TTL must remain live). Production must use a managed HA Redis endpoint
    (GCP Memorystore for Redis with failover, or Upstash Redis). Set `REDBEAT_REDIS_URL` in
    the K8s Secret to the HA endpoint (falls back to `REDIS_URL` in dev). Set
    `redis.haUrl` in `values.prod.yaml` to the prod managed endpoint.
  - **Chaos-test verification (deferred to staging):** `kill -9 <beat-pod>` → confirm no
    duplicate scheduled tasks + pod restarts within liveness threshold (Issue 263 verify path).
- [x] **Connection pooling**: PgBouncer sidecar in **both** the app pod and the worker pod
  (Issue 259), transaction mode.
  - **psycopg3 + transaction pooling (Issue 58):** the SQLAlchemy engine sets
    `connect_args={"prepare_threshold": None}` (`db.py`) — psycopg3 server-side
    prepared statements are incompatible with transaction-mode pooling and would
    raise `prepared statement "_pg3_…" does not exist` in production.
  - **App pool per pod**: `pool_size 15 + max_overflow 5 = 20` client conns ≤ PgBouncer
    sidecar `defaultPoolSize` 25. The prod override raises `defaultPoolSize` to 50
    (`values.prod.yaml`), which is safely below the sidecar's `max_client_conn` 1000.
  - **Worker pool per pod**: `admin_engine pool_size 2 + max_overflow 2 = 4` client conns
    ≤ PgBouncer sidecar `defaultPoolSize` 5 (sized for `--concurrency=2`). Worker's
    `DATABASE_URL` and `DATABASE_MIGRATION_URL` both point to `localhost:5432` (the sidecar).
  - **Fleet connection budget inequality (Issue 259 — must hold before raising replica counts):**

    ```
    Σ(app_pods × app_defaultPoolSize) + Σ(worker_pods × worker_defaultPoolSize)
      ≤ Cloud SQL max_connections − superuser_reserved(10)
    ```

    **Prod ceiling (values.prod.yaml):**

    | Tier | Max replicas | defaultPoolSize | Server conns |
    |------|-------------|-----------------|-------------|
    | App (HPA max) | 20 | 50 | 1,000 |
    | Worker (KEDA max) | 50 | 5 | 250 |
    | **Total** | | | **1,250** |

    Cloud SQL `max_connections` is set automatically from instance RAM. A
    `db-custom-2-8192` (2 vCPU / 8 GB) instance yields ~1,000 connections — **the
    prod ceiling of 1,250 exceeds this**. Either (a) use a larger instance tier
    (4 vCPU / 16 GB → ~2,500 max_connections), or (b) reduce `defaultPoolSize` values.
    **⚠️ OPEN QUESTION (Issue 259):** confirm the Cloud SQL instance tier and its actual
    `max_connections` before scaling to prod maxReplicas. Record the confirmed numbers in
    `docs/DECISIONS.md`.

### Preliminary Production Architecture (subject to research)

```
Cloudflare (CDN + DDoS) → Cloudflare Tunnel / Load Balancer
  → K8s nginx-ingress
    → FastAPI pods (HPA on CPU/request count)
    → Celery worker pods (KEDA on Redis queue depth)
    → Celery beat pod (1 replica)

Managed PostgreSQL (pgvector enabled) — PgBouncer sidecars (app + worker pods)
Redis 7 (managed HA — Memorystore or Upstash, Issue 263)
Cloudflare R2 (object storage, zero egress)
```

### Pre-Deploy Checklist (production)

**Gate 1 — Automated**
```bash
pytest  # zero failures, including tests/eval/
```

**Gate 2 — Manual smoke test**
```bash
docker compose up
```
Drive the change at http://localhost:8000:
- [ ] Connect YouTube → ingest a video → candidates render
- [ ] Review flow captures feedback; ranking responds
- [ ] Edge cases: no-cam, captions-only, small catalog
- [ ] Honesty text visible; no virality promise anywhere
- [ ] No regression in adjacent flows (auth, DNA, insights)
- [ ] Browser console clean

**Gate 3 — Production gates (before public launch)**
See `docs/PROJECT_STATE.md` Pre-Public-Launch Gates section.

---

## Production health monitoring (Issue 144)

Continuous uptime monitoring is owned by **Cloudflare Health Checks**, not a GitHub
Actions cron. Rationale: prod sits behind Cloudflare **Bot Fight Mode**, which serves a
403 JS-challenge to GitHub-hosted datacenter IPs even when the origin is healthy — so a
scheduled GH probe false-reds every run (confirmed Issue 144). Cloudflare Health Checks
probe from Cloudflare's own edge, so they are not bot-challenged, and they alert natively.

**One-time setup (Cloudflare dashboard):**
1. **Traffic → Health Checks → Create**.
2. Name `autoclip-health`; **Monitor type** `HTTPS`; **Path** `/health`.
3. **Expected codes** `200`; enable **Response body** match on `"status":"ok"`.
4. **Check regions**: a spread (e.g. WEU + ENAM); **interval** 60s; **consecutive
   fails to alert** 2.
5. **Notifications**: add an email/webhook (Slack/PagerDuty) destination.

This watches `status`/`postgres`/`redis` exactly as the old probe did — the `/health`
endpoint already returns `{"status":"ok","postgres":"ok","redis":"ok"}` (degrades to
non-`ok` + 503 when a backing service is down).

**What's left in CI:**
- `deploy.yml` runs an **internal** localhost `/health` smoke test on every deploy
  (bypasses Cloudflare) — the deploy-time gate.
- `.github/workflows/health-check.yml` is now **manual-dispatch only** — a smoke test
  you can point at a non-Cloudflare target via the `url` input (origin IP / staging).
  Running it against the Cloudflare-fronted prod URL returns 403 by design.

---

## RLS one-time setup (Issue 79)

> ✅ **ACTIVATED in prod 2026-06-30 (Issue 343).** The app now connects as
> `creatorclip_app` (no BYPASSRLS); `DATABASE_MIGRATION_URL` points at the superuser
> `creatorclip` (BYPASSRLS) for Alembic + worker cross-tenant sweeps. We used the
> **simplified path below** for an already-migrated DB: set the `creatorclip_app`
> password + re-grant, repoint `DATABASE_URL`, recreate app+worker — and **kept the
> superuser as the migrate role** (did NOT transfer table ownership to
> `creatorclip_migrate`; that remains optional future hardening). Verified via the
> Issue 341 smoke harness `isolation` check. Rollback: `.env.bak-pre-rls*` on the VM.
> The full pre-0010 runbook below is retained for reference / a clean rebuild.

### Simplified activation on an already-migrated DB (what was actually run)

```sql
-- As the superuser owner (creatorclip):
ALTER ROLE creatorclip_app LOGIN PASSWORD '<app-password>';
GRANT USAGE ON SCHEMA public TO creatorclip_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO creatorclip_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO creatorclip_app;
```
```
# /opt/autoclip/.env  (chmod 600) — keep host/db, swap the role:
DATABASE_URL=postgresql+psycopg://creatorclip_app:<app-password>@postgres:5432/creatorclip
DATABASE_MIGRATION_URL=postgresql+psycopg://creatorclip:<POSTGRES_PASSWORD>@postgres:5432/creatorclip
```
```bash
# Cut over, then verify with the smoke harness (isolation must be green):
ssh creatorclip-vm 'cd /opt/autoclip && docker compose -f docker-compose.prod.yml up -d --force-recreate --no-deps app worker'
ssh creatorclip-vm 'cd /opt/autoclip && docker compose -f docker-compose.prod.yml exec -T app sh -c "RUN_LIVE_SMOKE=1 python3.12 scripts/live_smoke.py --target prod --seed --only isolation"'
# Rollback: restore .env DATABASE_URL -> creatorclip and recreate.
```

---

### Original pre-0010 runbook (reference)

Alembic migration `0010_rls_policies` introduces Postgres Row-Level Security
on 12 tenant-owned tables. The application connects as `creatorclip_app`
(no `BYPASSRLS`); migrations and Celery worker tasks connect as
`creatorclip_migrate` (`BYPASSRLS`). The migration itself only creates the
roles, grants, and policies — the `BYPASSRLS` attribute, role passwords, and
table ownership transfer must be performed once by an operator with
`SUPERUSER`.

**One-time prod ops (run BEFORE the first alembic upgrade that includes
revision `0010_rls_policies`):**

```sql
-- 1. Grant BYPASSRLS to the migration role (created by the migration with
--    LOGIN-only by default).
ALTER ROLE creatorclip_migrate BYPASSRLS;

-- 2. Set passwords. Use the same generator as POSTGRES_PASSWORD
--    (openssl rand -hex 24).
ALTER ROLE creatorclip_app PASSWORD '<app-password>';
ALTER ROLE creatorclip_migrate PASSWORD '<migrate-password>';

-- 3. Transfer table ownership to creatorclip_migrate so it can run future
--    DDL (CREATE POLICY / ALTER TABLE) without superuser:
DO $$
DECLARE
    t text;
BEGIN
    FOR t IN
        SELECT tablename FROM pg_tables WHERE schemaname = 'public'
    LOOP
        EXECUTE format('ALTER TABLE %I OWNER TO creatorclip_migrate', t);
    END LOOP;
END
$$;
```

**Then update `/opt/autoclip/.env` (chmod 600):**

```
DATABASE_URL=postgresql+psycopg://creatorclip_app:<app-password>@localhost:5432/creatorclip
DATABASE_MIGRATION_URL=postgresql+psycopg://creatorclip_migrate:<migrate-password>@localhost:5432/creatorclip
```

**Then run the alembic upgrade as usual** (the entrypoint will use
`DATABASE_MIGRATION_URL`):

```bash
ssh creatorclip-vm 'cd /opt/autoclip && docker compose -f docker-compose.prod.yml restart app'
# Migration runs in the app container's startup hook.
```

**Verify** afterwards:

```sql
-- Connect as creatorclip_app. An unfiltered SELECT must return 0 rows
-- because no app.creator_id GUC is set:
SELECT count(*) FROM videos;  -- expect 0
SET LOCAL app.creator_id = '<a-real-creator-uuid>';
SELECT count(*) FROM videos;  -- expect that creator's video count
```

**pgbouncer note** (when we add it): RLS-safe configurations are *transaction
pooling* mode only. Statement pooling mode can hand off mid-transaction to
a different connection, leaking the `SET LOCAL` GUC across tenants. Do not
deploy pgbouncer in statement pooling mode with this stack.

---

## Auto-Rollback on Failed Smoke Test (Issue 271)

**Mechanism (single-VM stopgap):** `deploy.yml` captures the running image digest
before pulling the new image (`PREV_IMAGE`). The `docker image prune` step runs AFTER
the smoke test. If the smoke test fails:

1. The deploy step re-pulls `PREV_IMAGE` and restarts the previous container.
2. The step still exits non-zero, so GitHub Actions reports the deploy as failed and
   alerting fires — the auto-rollback does NOT hide the failure.
3. First-deploy guard: if `PREV_IMAGE` is empty, rollback is skipped and manual
   recovery is required.

**Limitations:** This is a single-VM image-rollback, not a zero-downtime blue-green
deploy. There is a brief window (smoke test loop, up to ~50s) during which the broken
image is running. Blue-green / canary deployment with zero-downtime failover is the
target state at the Kubernetes tier (Issue 275+).

**Target state:** Progressive delivery via K8s (Issue 275+) — Argo Rollouts or Flux
progressive promotion with automated canary analysis. The single-VM pattern here is a
safe stopgap until the GKE cluster is stood up.

---

## Migration Rollback Runbook (Issue 270)

**Policy: roll-forward-first.** Expand/contract migrations are forward-compatible with
the prior image, so the standard recovery is to roll the image back (not the schema).
True `alembic downgrade` is a break-glass operation and must not be used without an
explicit expand/contract audit.

### Step 1 — Image rollback (safe, always try first)

```bash
# 1. Find the previous image tag from GHCR (or from the prior deploy.yml run log).
PREV_TAG=<previous-sha-or-tag>

# 2. Pull the previous image.
docker pull ghcr.io/reese8272/creatorclip:${PREV_TAG}

# 3. Update the image pin in docker-compose.prod.yml (or set IMAGE_TAG env var).
sed -i "s|ghcr.io/reese8272/creatorclip:.*|ghcr.io/reese8272/creatorclip:${PREV_TAG}|" \
  /opt/autoclip/docker-compose.prod.yml

# 4. Restart on the previous image (schema unchanged).
cd /opt/autoclip && docker compose -f docker-compose.prod.yml up -d

# 5. Verify the smoke test passes.
curl -s http://localhost:8000/health | python3 -c "import sys,json; print(json.load(sys.stdin))"
```

### Step 2 — Schema downgrade (break-glass only)

Only run if the migration added a column/constraint that is **incompatible** with the
prior image AND the prior image is already running (rare; avoid with expand/contract).

```bash
# Run ONLY if Step 1 is insufficient.
docker compose -f docker-compose.prod.yml run --rm app alembic downgrade -1
```

### Expand/contract PR checklist

> **Full policy, copy-paste templates, and the sequencing rule (separate deploys) are in
> [`docs/MIGRATIONS.md`](MIGRATIONS.md).** The checklist below is a quick-reference
> summary; `MIGRATIONS.md` is the authoritative source.

For every migration that adds a NOT NULL column or changes a constraint:

- [ ] Phase 1 (expand): add column nullable with default — backward-compatible.
- [ ] Phase 2 (backfill): populate existing rows in a data migration or job.
- [ ] Phase 3 (contract): add NOT NULL constraint (with NOT VALID to avoid full scan),
  then validate in a **separate migration in a separate deploy**.
- [ ] Any `CREATE INDEX` must use `CONCURRENTLY` inside `autocommit_block()`.
- [ ] Backfills use bounded UPDATE loops (never one giant UPDATE).
- [ ] All migrations pass `squawk` lint (enforced in CI via the `migration-lint` job).
- [ ] See `docs/MIGRATIONS.md` for copy-paste snippet templates.

---

## TOKEN_ENCRYPTION_KEY rotation runbook

→ **Canonical procedure: [`docs/RUNBOOKS.md`](RUNBOOKS.md) → "TOKEN_ENCRYPTION_KEY
Rotation".** (Consolidated in Issue 146 — this section previously held a second, divergent
copy.) In short: it's zero-downtime via `MultiFernet([primary, previous])` —
set `TOKEN_ENCRYPTION_KEY_PREVIOUS` to the current key, re-encrypt with
`scripts/rotate_token_key.py`, then promote the new key and clear `PREVIOUS`. Rotate every
~90 days and immediately on any suspected key exposure. Keys live only in the VM `.env`
(`/opt/autoclip/.env`, chmod 600) — never in git.

---

## Transcription Compute Decision

**Must decide before Issue 5:**

| Option | Pros | Cons |
|--------|------|------|
| Self-hosted WhisperX (GPU) | Cheapest at scale, offline, no data-sharing | GPU node management, cold-start latency |
| Deepgram API | Fast, managed, no GPU | Per-minute cost, external data dependency |
| AssemblyAI API | Reliable, word-level timestamps | Per-minute cost, external data dependency |

Decision: log in `docs/DECISIONS.md` when made.
