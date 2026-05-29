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
- [x] **Connection pooling**: PgBouncer sidecar in the app pod, transaction mode, 25 conns/pod.

### Preliminary Production Architecture (subject to research)

```
Cloudflare (CDN + DDoS) → Cloudflare Tunnel / Load Balancer
  → K8s nginx-ingress
    → FastAPI pods (HPA on CPU/request count)
    → Celery worker pods (KEDA on Redis queue depth)
    → Celery beat pod (1 replica)

Managed PostgreSQL (pgvector enabled) — PgBouncer for connection pooling
Redis 7 (managed or self-hosted in K8s)
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

## RLS one-time setup (Issue 60)

Alembic migration `0005_rls_policies` introduces Postgres Row-Level Security
on 12 tenant-owned tables. The application connects as `creatorclip_app`
(no `BYPASSRLS`); migrations and Celery worker tasks connect as
`creatorclip_migrate` (`BYPASSRLS`). The migration itself only creates the
roles, grants, and policies — the `BYPASSRLS` attribute, role passwords, and
table ownership transfer must be performed once by an operator with
`SUPERUSER`.

**One-time prod ops (run BEFORE the first alembic upgrade that includes
revision `e5f6a7b8c9d0`):**

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

## Transcription Compute Decision

**Must decide before Issue 5:**

| Option | Pros | Cons |
|--------|------|------|
| Self-hosted WhisperX (GPU) | Cheapest at scale, offline, no data-sharing | GPU node management, cold-start latency |
| Deepgram API | Fast, managed, no GPU | Per-minute cost, external data dependency |
| AssemblyAI API | Reliable, word-level timestamps | Per-minute cost, external data dependency |

Decision: log in `docs/DECISIONS.md` when made.
