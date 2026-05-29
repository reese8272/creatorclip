# _root_infra — assessed 2026-05-29

Slice: db.py, crypto.py, config.py, auth.py, limiter.py, models.py, main.py.
Driver in use is `postgresql+psycopg` (psycopg3 async), not asyncpg — this changes
which axis-A pitfalls apply (prepared-statement handling differs from asyncpg).

## Findings

- [BLOCKER] db.py:14-20 — `create_async_engine` has no `connect_args` disabling
  psycopg3 server-side prepared statements, but `docs/DEPLOYMENT.md:46` puts
  PgBouncer in **transaction-pooling mode** in front of Postgres. psycopg3
  auto-prepares a statement after the 5th execution; under transaction pooling
  the prepared statement is created on one server connection and re-used on
  another, producing `prepared statement "_pg3_…" does not exist` errors that
  unit tests (which hit Postgres directly, no PgBouncer) will never surface |
  fix: pass `connect_args={"prepare_threshold": None}` to `_make_engine()` so
  psycopg3 never server-prepares; add this to `db.py` and document the PgBouncer
  coupling in `docs/DEPLOYMENT.md`. Verify with a Locust run behind PgBouncer
  (needs-runtime-confirmation for the exact failure, but the misconfiguration is
  certain by inspection).

- [SEV1] db.py:18-19 — pool ceiling per process is `pool_size=10 + max_overflow=20
  = 30` connections, but `docs/DEPLOYMENT.md:46` provisions the PgBouncer sidecar
  at **25 conns/pod**. At saturation each FastAPI pod can demand 30 > 25, so the
  6th–30th overflow checkout blocks/timelimits against PgBouncer even though the
  app pool thinks it has headroom → `QueuePool`/PgBouncer checkout timeouts at
  p99 under load (scale-checklist axis A) | fix: pin `pool_size`+`max_overflow`
  so the per-pod ceiling is ≤ the PgBouncer `default_pool_size` (e.g. pool_size=15,
  max_overflow=5 → 20, leaving sidecar headroom), and reconcile the number in one
  place in `docs/DEPLOYMENT.md`. Recompute the total-connections inequality
  (api_pool×replicas + celery_pool×concurrency×worker_replicas ≤ Postgres
  max_connections) and record it.

- [SEV1] alembic/versions/0001_initial_schema.py:233 (schema owned here:
  models.py:310-321) — `dna_embeddings.embedding Vector(1024)` has **no HNSW or
  IVFFlat index**; the only index on the table is the B-tree
  `ix_dna_embeddings_creator_id`. A `<=>` cosine similarity query
  (`tests/test_db.py:57` proves the access pattern exists) is therefore an O(rows)
  sequential scan that degrades as the per-creator corpus grows (scale-checklist
  axis H) | fix: add an Alembic migration creating an HNSW index with the matching
  op class, e.g. `CREATE INDEX CONCURRENTLY ix_dna_embeddings_hnsw ON
  dna_embeddings USING hnsw (embedding vector_cosine_ops);` run outside a
  transaction block (`op.execute` with autocommit) so it is online-safe. Confirm
  the index distance op matches the query distance op (`<=>` = cosine).

- [SEV1] db.py:14-20 — multi-tenant isolation is enforced only by application-level
  `WHERE creator_id` clauses (auth.py:43 derives the identity correctly from the
  JWT `sub`, good); there is **no Postgres RLS policy** and no query-construction
  helper that makes the filter structural (scale-checklist axis D). One forgotten
  clause in any router is a cross-tenant leak. RLS is still an open issue
  (`docs/issues.md:948`) | fix: track to closure — add RLS with a `creator_id`
  policy + per-request `SET LOCAL app.current_creator` (note the `SET LOCAL` /
  transaction-pooling interaction already flagged in issues.md), or at minimum a
  standing test that introspects every creator-scoped endpoint asserting creator
  B gets 404 on creator A's resource. Until then this stays a SEV1 latent-leak
  risk, not a clean bill.

- [SEV2] db.py:14-20 — no `pool_recycle` set; long-lived connections are not
  cycled, so a Postgres/PgBouncer restart or an idle-timeout reaper can leave
  stale connections that fail the next checkout despite `pool_pre_ping=True`
  catching most of it (scale-checklist axis A backed design) | fix: add
  `pool_recycle=1800` to `_make_engine()`.

- [SEV2] main.py:93-100 — `_check_postgres` opens a brand-new
  `psycopg.AsyncConnection.connect()` on **every** `/health` call, bypassing the
  pool. Under a tight k8s liveness/readiness probe interval across many replicas
  this is steady connection churn against Postgres/PgBouncer that competes with
  real traffic for the 25-conn sidecar budget | fix: either run the health probe
  through the existing SQLAlchemy engine (`async with engine.connect()`), or give
  health checks a tiny dedicated pool, and ensure probe failures only mark the pod
  unready (drain) rather than thrashing connections.

- [SEV2] main.py:79-85 vs 56-58 — `CORSMiddleware` is added AFTER the routers,
  static mount, and AFTER `SlowAPIMiddleware`. Starlette runs middleware in
  reverse-add order; adding CORS last means it wraps outermost (which is what you
  want), but mixing `add_middleware` calls before and after `include_router`
  obscures the order and is fragile to reorder. More concretely: a request
  rejected by the rate limiter can return a 429 without CORS headers because the
  ordering relationship between SlowAPI and CORS is not pinned deliberately | fix:
  group all `add_middleware` calls together immediately after `app = FastAPI(...)`,
  with CORS added last (outermost) so error responses (429/500) still carry CORS
  headers; add a test asserting a 429 response includes `access-control-allow-origin`.

- [SEV2] main.py:34-38 — `lifespan` does not initialize or dispose the shared
  async engine / a module-level redis client, and `_check_redis` (main.py:103-108)
  builds a fresh `aioredis.from_url(...)` per call instead of reusing a singleton
  (rubric §1 external clients must be module-level singletons; scale axis G) | fix:
  create the redis client once at module/lifespan scope and reuse it in the health
  check; dispose the SQLAlchemy engine in the lifespan shutdown
  (`await dispose_engine()`), mirroring the worker's shutdown path.

- [cleanup] config.py:66-71 — fail-fast path uses `print(...)` to stderr; CLAUDE.md
  Production Standards mandate the `logging` module only and ban `print()`. This
  runs before logging is configured, so it is borderline, but it is still a raw
  print | fix: acceptable as a bootstrap exception, but add a one-line comment
  noting WHY print is used here (logging not yet configured at settings-load time)
  to satisfy the no-print rule's intent, or route through
  `logging.getLogger(...).critical(...)` after a minimal `basicConfig`.

- [cleanup] limiter.py:15 / auth.py:13 / limiter.py:12 — `SESSION_COOKIE =
  "cc_session"` is duplicated in auth.py:13 and limiter.py:12, and the HS256
  JWT-decode logic is duplicated between auth.py:27-28 and limiter.py:19-26 (DRY) |
  fix: define `SESSION_COOKIE` and a `decode_session_token(..., verify_exp: bool)`
  helper once (in auth.py or a small `session.py`) and import it in limiter.py.

- [cleanup] limiter.py:26 — bare `except Exception: pass` swallows all JWT errors
  to fall back to IP keying. Functionally fine (the limiter must not 500), but the
  bare except hides genuine misconfiguration | fix: narrow to
  `except jwt.PyJWTError` / `KeyError`, matching auth.py's specific catch.

- [cleanup] main.py:115 / 117 / 121, db.py:57, models.py:524-543 — several public
  functions return loosely-typed `dict` (`health() -> dict`, `get_session ->
  AsyncGenerator[AsyncSession, None]` is fine; `append_audit(session, ...)` has an
  untyped `session` param) | fix: annotate `append_audit`'s `session:
  AsyncSession`; consider a small Pydantic/TypedDict for the health payload so the
  readiness contract is typed.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | 2 findings (redis client per-call SEV2; engine not disposed in lifespan SEV2) |
| 2 Concurrency & scale | 4 findings (psycopg3 prepared-stmt BLOCKER; pool ceiling vs sidecar SEV1; pgvector index SEV1; no pool_recycle SEV2; health-check connection churn SEV2) |
| 3 Security & compliance | 1 finding (no RLS/structural tenant isolation SEV1). Positive: tokens via crypto.decrypt with MultiFernet rotation (crypto.py) correct; identity derived from JWT sub not body (auth.py:43); cookie httponly+samesite+secure-in-prod (routers/auth.py); no token/PII in any slice log line |
| 4 Clip-quality | n/a (infra module) |
| 5 Anthropic SDK | n/a (no LLM calls in slice) |
| 6 Cleanliness & typing | 4 cleanups (print bootstrap; SESSION_COOKIE+JWT decode DRY; bare except; typing gaps) |
| 7 Error handling / API | n/a (no routers in slice; main.py middleware/CORS finding filed under §2) |
| 8 Config & paths | ok — pydantic-settings fail-fast (config.py:61-72); all new config present in .env.example with descriptions (verified); static path absolute via `Path(__file__).parent` (main.py:70). No findings. |

## Module verdict
NEEDS-WORK (1 BLOCKER) — crypto rotation, JWT auth, config fail-fast, and cookie
security are solid, but the psycopg3-prepared-statements-under-PgBouncer
misconfiguration will break the app behind the planned transaction-mode pooler,
and the pool-vs-sidecar math, missing pgvector index, and absent structural tenant
isolation must close before launch.
