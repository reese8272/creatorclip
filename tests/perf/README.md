# tests/perf/ — load testing (concurrency evidence)

Unit tests prove correctness; they cannot prove behavior under hundreds of
concurrent creators. The scale-checklist axes A (pool math), E (health churn),
and F (rate-limit under contention) can only be *settled* with a real load run.
This is that run.

> **Tool choice:** Locust (Python), so it reuses the project's auth scheme and
> is maintained in the same language as the codebase. If you later need
> >10k RPS from one box or native Grafana/Prometheus streaming, k6 is the
> documented alternative (see `docs/DECISIONS.md`).

---

## Prerequisites

1. A **staging** stack (never run against production). `docker-compose.staging.yml`
   in the repo root brings up an isolated copy with PgBouncer transaction-pooling,
   a separate Postgres DB (`creatorclip_staging`), and a separate Redis DB index.
2. Migrations applied to the staging DB.
3. A seeded test creator (use `tests/perf/seed_staging.py` — see below).
4. `locust` installed: `pip install locust` (or it's in `requirements-dev.txt`).

---

## Step-by-step (run on the prod VM)

### 1. Start the staging stack

```bash
# From the repo root on the prod VM.
# .env is reused for secrets (POSTGRES_PASSWORD, JWT_SECRET_KEY, etc.).
# POSTGRES_PASSWORD must be set so PgBouncer can authenticate.
docker compose -f docker-compose.staging.yml pull
docker compose -f docker-compose.staging.yml up -d

# Wait for healthy (30–60 s for first pull):
docker compose -f docker-compose.staging.yml ps
```

The app is exposed on **port 8001** (`http://localhost:8001`) so it does not
touch the production stack on port 8000.

### 2. Apply migrations

```bash
docker compose -f docker-compose.staging.yml exec app \
    alembic upgrade head
```

### 3. Seed the staging database

```bash
# The seed script connects directly to the staging Postgres (port 5434 mapped
# below so it doesn't collide with the prod Postgres on 5432).
export POSTGRES_PASSWORD="$(grep POSTGRES_PASSWORD .env | cut -d= -f2)"
export DATABASE_URL="postgresql://creatorclip:${POSTGRES_PASSWORD}@localhost:5434/creatorclip_staging"
python3 tests/perf/seed_staging.py
```

The script prints:
```
export CC_BASE_URL=http://localhost:8001
export CC_JWT_SECRET=<value of JWT_SECRET_KEY from .env>
export CC_CREATOR_ID=00000000-1111-2222-3333-444444444444
```

Copy and run those exports.

### 4. Verify staging is healthy

```bash
curl -s http://localhost:8001/health | python3 -m json.tool
# Expected: {"status": "ok", "postgres": "ok", "redis": "ok"}
```

### 5. Run Locust

```bash
export CC_BASE_URL=http://localhost:8001
export CC_JWT_SECRET="$(grep JWT_SECRET_KEY .env | cut -d= -f2)"
export CC_CREATOR_ID=00000000-1111-2222-3333-444444444444

locust -f tests/perf/locustfile.py \
       --host "$CC_BASE_URL" \
       --users 300 --spawn-rate 20 --run-time 5m \
       --headless \
       --csv docs/assessment/loadtest
```

The `--csv` flag writes `docs/assessment/loadtest_stats.csv` and
`loadtest_stats_history.csv` so the assessment REPORT.md can cite real numbers.

### 6. Read the results

```bash
column -t -s, docs/assessment/loadtest_stats.csv | head -20
```

### 7. Tear down the staging stack

```bash
docker compose -f docker-compose.staging.yml down -v
# -v removes the staging_postgres_data and staging_redis_data volumes.
# Do NOT run -v on the production stack.
```

---

## What to look for (feeds scale-checklist.md)

| Symptom in results | Likely cause | Checklist axis |
|---|---|---|
| p99 latency climbs while p50 stays flat | event-loop stall — sync call on hot async path | B |
| Errors spike to `QueuePool limit` / `TimeoutError` | DB pool exhausted vs replicas × pool size | A |
| 429s appear earlier than expected | rate limiter bucket collision (per-IP not per-creator) | F |
| `/health` flips to `degraded` under load | DB or Redis saturation | E |
| Throughput plateau then drop | downstream (R2/YouTube) without a timeout backing up | E |
| `prepared statement does not exist` in app logs | PgBouncer transaction mode + psycopg prepare (Issue 58) | A |

**Pass criteria (axes A + E — needed to move verdict from CONDITIONAL → YES):**
- p99 latency on `GET /videos` and `GET /creators/me` < 500 ms at 300 users
- Error rate < 1% across the 5-minute run
- No `QueuePool limit` or `prepared statement` errors in app logs
- `/health` stays `ok` throughout

---

## Recording results in the assessment

After a passing run, add a section to `docs/assessment/REPORT.md`:

```markdown
### Locust run — <date> — axes A + E

| Endpoint | p50 | p95 | p99 | Error % |
|---|---|---|---|---|
| GET /videos | X ms | X ms | X ms | 0% |
| GET /creators/me | ... | ... | ... | ... |
| GET /health | ... | ... | ... | ... |

**Verdict**: Pool math holds at 300 concurrent users. No prepared-statement
errors logged. Axes A and E closed. Scale-checklist verdict: ✅.
```

Then flip axes A and E in `docs/assessment/scale-checklist.md` from ⚠️ to ✅.

---

## Next steps after a run

- Record the numbers in the next `/assess` REPORT.md (axes A/E/F).
- If pool exhaustion appears: verify `pool_size + max_overflow ≤ PgBouncer DEFAULT_POOL_SIZE` in `docker-compose.staging.yml`. Current sizing: 15+5=20 ≤ 25.
- If `prepared statement` errors appear: confirm `prepare_threshold=None` in `db.py` — see `_CONNECT_ARGS`.
- Separately load-test the Celery path (queue depth + task latency) — the async render/generate jobs are not exercised here by design.
