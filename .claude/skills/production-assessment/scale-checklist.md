# Scale Checklist — "Is this TRULY production ready?"

General code quality ≠ production readiness at concurrency. These are the failure
modes that only appear with CreatorClip's stack (FastAPI async + SQLAlchemy
asyncpg + Celery/Redis + pgvector + Cloudflare R2 + Anthropic/Voyage) under real
load — hundreds of concurrent creators. Each axis has a concrete, backed design,
not just a question. The Layer-2 verdict marks each ✅ / ⚠️ / ❌ with evidence.

Most of these cannot be settled by reading — they need the Locust run in
`tests/perf/`. Mark those `(needs load evidence)` until you have it.

---

## A. Connection-pool math (the #1 scale killer)

**Failure mode:** request timeouts and `TimeoutError: QueuePool limit` under
concurrency, even though every unit test passes.

**The math you must verify:**
```
total_db_connections = (api_pool_size + api_max_overflow) × api_replicas
                     + celery_pool_connections × worker_concurrency × worker_replicas
must be ≤ Postgres max_connections − superuser_reserved (default 3)
```
SQLAlchemy async default pool is `pool_size=5, max_overflow=10` = up to 15 per
process. Postgres default `max_connections=100`. So **7 API replicas alone**
(7×15=105) exhaust the DB before workers get a single connection.

**Backed design:**
- Put **PgBouncer in transaction-pooling mode** between the app and Postgres.
  This is the standard fix at hundreds+ of clients — it multiplexes thousands of
  client connections onto a small server pool. With asyncpg, disable server-side
  statement caching (`prepared_statement_cache_size=0` / `statement_cache_size=0`)
  because transaction pooling breaks prepared statements.
- Set `pool_pre_ping=True` and a sane `pool_recycle` (e.g. 1800s) to survive
  Postgres/PgBouncer restarts.
- Pin pool sizes explicitly in config; never rely on defaults. Document the math
  above in `docs/DEPLOYMENT.md` with your chosen replica counts.

**Evidence to capture:** Locust at target concurrency with pool metrics; no
checkout timeouts at p99.

---

## B. Async event loop hygiene (no sync calls on the loop)

**Failure mode:** p99 latency explodes under load because one blocking call
stalls the single-threaded event loop for every concurrent request on that worker.

**What to hunt (mechanizable — feed to a subagent or grep):** inside any
`async def`: `requests.`, `time.sleep`, `subprocess.run`/`check_output`,
`open(...).read()` of large files, a sync DB driver, ffmpeg invoked synchronously,
or a CPU-heavy loop.

**Backed design:**
- Network I/O → `httpx.AsyncClient` (module-level singleton, not per-call).
- ffmpeg / WhisperX / any CPU or blocking work → Celery task, never in the
  request path. If it must be in-process, `await asyncio.to_thread(...)`.
- The clip render pipeline (ffmpeg cut + 9:16 reframe) is correctly already a
  Celery job — verify nothing pulls it back onto the API loop.

---

## C. Celery idempotency under at-least-once delivery

**Failure mode:** Redis broker redelivers a task (visibility timeout, worker
restart, `acks_late`) and a non-idempotent task double-charges billing,
double-purges media, or corrupts DNA state.

**Backed design:**
- Every task that mutates state must be idempotent on a stable key. Use a
  `processed_jobs(job_key UNIQUE)` row or an `INSERT ... ON CONFLICT DO NOTHING`
  guard, or a `state` column transition guarded by `WHERE state = 'pending'`.
  (Issues 39/43/47 already model this discipline — confirm it for all 11 task
  bodies, not just the three that were hardened.)
- Set `acks_late=True` + `task_reject_on_worker_lost=True` so crashes redeliver;
  this REQUIRES idempotency to be safe.
- Set `task_acks_on_failure_or_timeout` and explicit `max_retries` +
  exponential backoff; ensure retries don't re-stamp one-time markers (the
  `if ... is None:` guard pattern from Issue 43).
- Bound `worker_prefetch_multiplier` (e.g. 1 for long media jobs) so one worker
  doesn't hoard the queue.

**Evidence:** a test that fires the same task twice concurrently and asserts a
single effect.

---

## D. Per-tenant isolation as an enforced invariant

**Failure mode:** one missing `WHERE creator_id = ?` leaks creator A's videos,
analytics, or clips to creator B. At scale this is the highest-severity class of
bug and it is a single forgotten clause away.

**Backed design — make it structural, not vigilant:**
- Best: **Postgres Row-Level Security (RLS)** with a `creator_id` policy and a
  per-request `SET app.current_creator`. The database refuses cross-tenant rows
  even if application code forgets the filter. This is the industry standard for
  hard multi-tenancy on shared Postgres.
- Cheaper interim: a query-construction helper that requires `creator_id` and a
  test that introspects each creator-scoped endpoint asserting creator B gets 404
  on creator A's resource. Add this as a standing test, not a one-time review.
- Never trust a `creator_id` from the request body — derive it from the session
  JWT.

---

## E. Backpressure & graceful degradation

**Failure mode:** a dependency slows or fails (R2 latency, YouTube quota
exhausted mid-job, Anthropic 529, Redis blip) and failures cascade into outage.

**Backed design:**
- **Timeouts on every external call** — httpx, R2/boto, Anthropic, Voyage. A
  call with no timeout is an outage waiting for a slow dependency.
- **Circuit breaker / retry-with-jitter** on idempotent external calls; fail
  fast and shed load rather than pile up.
- YouTube `QuotaExhaustedError` must degrade to "try tomorrow" with fair
  ordering (already done, Issue 47) — confirm the same posture for Anthropic/
  Voyage rate limits.
- R2 writes must be retried and verified; a half-written render must not be
  surfaced as a finished clip.
- Health endpoint already reports `degraded` (good) — ensure the load balancer /
  k8s readiness probe actually uses it to drain unhealthy pods.

---

## F. Rate limiting & quota under contention

**Failure mode:** 200 creators hit an LLM/render endpoint simultaneously and
either the limiter fails open (cost blowout) or fails closed (everyone 429'd).

**Backed design:**
- slowapi already uses real Redis (good — no in-memory fallback that fails open
  per-replica). Confirm limits are **per-creator**, not per-IP, for authenticated
  routes.
- Add a **per-creator usage quota** check before each LLM/render job (a
  pre-launch requirement in CLAUDE.md) — separate from rate limiting; this is
  cost control, not abuse control.
- Load test the limiter path itself; a Redis round-trip per request is a
  throughput ceiling worth measuring.

---

## G. Observability (you can't operate at scale blind)

**Backed design:**
- Structured JSON logs with a request/correlation id and `creator_id` (never the
  token, never PII) so a single creator's failing job is traceable.
- The four golden signals (latency, traffic, errors, saturation) exported —
  Prometheus/OpenTelemetry. Celery queue depth + task latency are first-class:
  a growing queue is the earliest sign of under-provisioned workers.
- Error tracking (Sentry or equivalent) with PII scrubbing on.
- p50/p95/p99 per endpoint, not just averages — averages hide the tail that
  actually pages you.

---

## H. Data & migration safety at scale

**Backed design:**
- Alembic migrations must be **online-safe**: no `ALTER TABLE` that takes a long
  exclusive lock on a large table during deploy. Use `CREATE INDEX CONCURRENTLY`
  (outside a transaction), add columns nullable-then-backfill, expand-then-
  contract for renames. (Issue 43/47 already split schema + backfill — good.)
- pgvector: confirm an **HNSW or IVFFlat index** exists on the embedding column
  used for similarity search; an unindexed `<->` scan is O(rows) and dies as the
  corpus grows. Verify the index's distance op matches the query.
- Backups + point-in-time recovery configured and *restore-tested*; an untested
  backup is not a backup.

---

## I. Secrets, keys, and deletion (compliance-load-bearing)

**Backed design:**
- `TOKEN_ENCRYPTION_KEY` rotation runbook exists and is exercised (CLAUDE.md
  pre-launch item). Fernet supports MultiFernet for zero-downtime rotation —
  confirm the code can decrypt with the old key while encrypting with the new.
- Account-deletion endpoint performs token revocation + media purge (right to
  erasure) and is itself idempotent.
- No secret in image layers, logs, or error responses; `/docs` disabled in prod
  (already conditional on `ENV` — confirm `ENV` is set in the prod manifest).

---

## How to read this in the verdict

A project is **PRODUCTION-READY: YES** only when A–F are ✅ with load evidence
and G–I are ✅ by inspection. **CONDITIONAL** = no BLOCKERs but one or more axes
lack load evidence or have a documented, scheduled fix. **NO** = any open
BLOCKER (cross-tenant leak, non-idempotent money/media task, pool math that
exhausts the DB at target replicas, a sync call on the request loop on a hot path).
