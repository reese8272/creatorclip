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
  - **psycopg3 + transaction pooling (Issue 58):** the SQLAlchemy engine sets
    `connect_args={"prepare_threshold": None}` (`db.py`) — psycopg3 server-side
    prepared statements are incompatible with transaction-mode pooling and would
    raise `prepared statement "_pg3_…" does not exist` in production.
  - **Connection budget (must hold before raising replica counts):**
    `app_pool_per_pod (pool_size 15 + max_overflow 5 = 20) ≤ PgBouncer sidecar (25)`.
    Across the fleet, the PgBouncer→Postgres server pool must satisfy:
    `Σ(PgBouncer default_pool_size) + Σ(celery_pool × worker_concurrency × worker_replicas)
    ≤ Postgres max_connections − superuser_reserved`. Re-check this inequality and
    record the chosen numbers whenever API/worker replica counts change.

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

## RLS one-time setup (Issue 79)

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

## TOKEN_ENCRYPTION_KEY rotation runbook

OAuth access/refresh tokens are stored Fernet-encrypted (`crypto.py`). Rotation is
**zero-downtime** because `crypto._fernet()` builds a `MultiFernet([primary, previous])`:
`encrypt()` always uses the primary key, while `decrypt()` tries the primary then the previous
key — so tokens still readable while we re-encrypt the table. Pre-launch checklist item; rotate
on a fixed cadence (e.g. every 90 days) and immediately on any suspected key exposure.

**Keys live only in the VM `.env` (`/opt/autoclip/.env`, chmod 600) — never in git.**

1. **Generate a new key:**
   ```bash
   docker compose -f docker-compose.prod.yml exec -T app \
     python -c "from crypto import generate_key; print(generate_key())"
   ```
2. **Enter the decrypt-both window.** In `/opt/autoclip/.env`, set
   `TOKEN_ENCRYPTION_KEY_PREVIOUS` to the *current* key (the one still in
   `TOKEN_ENCRYPTION_KEY`), then restart so the app can decrypt under either key:
   ```bash
   # .env: TOKEN_ENCRYPTION_KEY=<current>   TOKEN_ENCRYPTION_KEY_PREVIOUS=<current>
   docker compose -f docker-compose.prod.yml up -d app worker beat
   ```
3. **Re-encrypt every stored token** old→new in one atomic transaction (auto-rolls back on any
   failure — if it errors, do NOT change the key and investigate):
   ```bash
   docker compose -f docker-compose.prod.yml exec -T app \
     python3 scripts/rotate_token_key.py --old-key "<current>" --new-key "<new>"
   ```
4. **Promote the new key.** In `.env`, set `TOKEN_ENCRYPTION_KEY=<new>` and **clear**
   `TOKEN_ENCRYPTION_KEY_PREVIOUS=` (so a leaked old key no longer decrypts anything), then
   restart:
   ```bash
   docker compose -f docker-compose.prod.yml up -d app worker beat
   ```
5. **Verify:** sign in / trigger a YouTube refresh for one creator and confirm a clean
   `decrypt()` (no `TokenDecryptError` in logs). The old key can now be destroyed.

**Rollback (steps 1–3 only):** if the re-encrypt fails it rolled itself back; leave the key
unchanged and keep `TOKEN_ENCRYPTION_KEY_PREVIOUS` as-is. After step 4, rollback means setting
`TOKEN_ENCRYPTION_KEY` back to the old key and re-running step 3 in reverse.

---

## Transcription Compute Decision

**Must decide before Issue 5:**

| Option | Pros | Cons |
|--------|------|------|
| Self-hosted WhisperX (GPU) | Cheapest at scale, offline, no data-sharing | GPU node management, cold-start latency |
| Deepgram API | Fast, managed, no GPU | Per-minute cost, external data dependency |
| AssemblyAI API | Reliable, word-level timestamps | Per-minute cost, external data dependency |

Decision: log in `docs/DECISIONS.md` when made.
