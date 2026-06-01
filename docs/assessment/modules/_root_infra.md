# _root_infra — assessed 2026-05-31 (Wave 9)

Slice: db.py, crypto.py, config.py, auth.py, limiter.py, models.py, main.py,
api_key.py, observability.py. `clients.py` was listed in an earlier slice
but DOES NOT exist in the repo (verified `ls
/home/reese/workspace/Youtube-Video-AI-Editor/*.py`); external clients live
with their owning modules (`youtube/_http.py`, `worker/progress.py`) and
are correctly closed in main.py's lifespan.

## Wave-9 re-verification (closed since last /assess)

- **Issue 104 — closed.** `auth.py:58` stashes `request.state.creator_id =
  creator.id` as the last step of `get_current_creator`; `api_key.py:119`
  mirrors it in `get_current_creator_via_api_key`. `limiter.py:74-77`
  `creator_key()` reads `request.state.creator_id` first; falls back to
  `get_remote_address` only when unset. The bearer-authenticated routes
  (`/clips/ingest`) now bucket per-creator instead of leaking onto a NAT IP.
- **Issue 105 — closed.** `config.py:137` adds `CELERY_SOFT_TIME_LIMIT_S:
  int = 3000`; `worker/celery_app.py:48-49` binds it to
  `task_soft_time_limit` (single source of truth, no drift possible).
  `config.py:167-185` `_validate_transcription_timeout` model_validator
  asserts `TRANSCRIPTION_TIMEOUT_S < CELERY_SOFT_TIME_LIMIT_S - 30`
  (30 s breathing room). `config.py:213-216` `LOCAL_MEDIA_DIR` validator
  rejects relative paths when `ENV=="production"`.
- **Issue 106 — closed.** `limiter.py:43-51` verifies `exp` with
  `leeway=_JWT_LEEWAY_S` (60s constant at line 37). Except narrowed to
  `jwt.InvalidTokenError`. Log line `"jwt_decode_failed exc=%s"
  type(exc).__name__` (line 57) emits the exception CLASS only — no claim
  body, no token. The 60s leeway (vs. /assess-recommended 300s) is
  documented in `docs/DECISIONS.md` 2026-05-31 entry as a defensible
  override (RFC 7519 §4.1.4 says "a few minutes"; 60s for a security-
  relevant decoder vs. UX path).
- **Issue 108 — closed.** `auth.py:28` `decode_session_token(...) -> dict[
  str, Any]`; `limiter.py:40` `_creator_key(request: Request) -> str`;
  `models.py` `Optional["X"]` → `"X | None"` sweep verified — `grep -n
  "Optional\[" models.py` returns nothing, and `from typing import
  Optional` is no longer present in the file.

## Findings

- [SEV2] main.py:135-153 — `/health` opens a fresh
  `psycopg.AsyncConnection` AND constructs a fresh `aioredis.from_url()`
  client per probe. Under aggressive k8s readiness/liveness probing
  (every few seconds × N replicas) this is sustained connect/disconnect
  churn against Postgres OUTSIDE the SQLAlchemy pool, competing with app
  traffic for backend connections and defeating the PgBouncer pool math
  in db.py. The redis client spins up a fresh pool each call; an
  exception path that misses `aclose()` leaks the pool. (rubric cat 1 +
  cat 2) | fix: reuse the existing SQLAlchemy `engine` for the PG check
  (`async with engine.connect() as c: await c.execute(text("SELECT 1"))`
  — note this also exercises the actual prod path), bind a module-level
  singleton `redis.asyncio.Redis.from_url(REDIS_URL)` at import time,
  `aclose()` it in the lifespan shutdown alongside `youtube._http` and
  `worker.progress`. Wrap both checks in `asyncio.wait_for(..., timeout=
  2.0)` so a slow dep can't queue probes.

- [SEV2] api_key.py:113-114 — every API-key-authenticated request issues
  `UPDATE creator_api_keys SET last_used_at = now()` and `await
  session.commit()` inside the auth dependency, BEFORE the handler runs.
  A single OBS app uploading every few seconds is fine, but the
  docstring ("any future non-browser client") invites high-frequency
  callers; each request synchronously fsyncs a hot row. (rubric cat 1 /
  scale-checklist cat 2) | fix: coarse-grain `last_used_at` — only
  UPDATE if `last_used_at IS NULL OR last_used_at < now() - interval
  '60 seconds'`; skip the commit when no row changed. Keeps the
  management-UI freshness signal without per-request write
  amplification.

- [SEV2] observability.py:43-44, 224-241 — Celery `_bind_request_id` /
  `_record_task_and_clear` use module-level ContextVars
  (`request_id_ctx`, `_task_start_ctx`). Comment at line 43 documents
  this is "safe because each worker process runs one task at a time" —
  true ONLY under the prefork pool. If a future migration switches to
  `--pool=gevent/eventlet/threads`, concurrent tasks in one process
  overwrite each other's correlation id and start time → mislabelled
  durations and broken log correlation. (rubric cat 2) | fix: at worker
  startup (`worker/celery_app.py`), assert `app.conf.worker_pool ==
  "prefork"` and refuse to boot otherwise; OR key task start off
  `task.request.id` in a per-task dict guarded by `task_postrun`
  removal.

- [SEV2] db.py:80-103 — `recreate_engine()` rebinds the module-global
  engine and BOTH session factories. Documented for "after fork (Issue
  39)" use only (single-shot in `worker_process_init`); but the
  function is module-public and has no guard against being called with
  in-flight sessions, which would leave outstanding `AsyncSession`
  instances holding refs to the disposed pool. (rubric cat 1) | fix:
  add an explicit `_already_called: bool` flag and raise on re-entry,
  or prefix the function with `_` and assert the caller is
  `worker_process_init` via stack inspection; document the precondition
  in the docstring with the words "MUST NOT be called with in-flight
  sessions".

- [SEV2] auth.py:46-58 / api_key.py:96-119 — both auth dependencies
  issue their bootstrap SELECT (Creator / CreatorApiKey lookup) on the
  session BEFORE `session.info["creator_id"]` is set. SQLAlchemy
  autobegin happens on first query, so the bootstrap transaction has no
  `app.creator_id` GUC — this is intentional today because `creators`
  and `creator_api_keys` are RLS-exempt (Issue 56 / 95), but it is a
  by-convention invariant: a future migration that flips either table
  under RLS would silently lose the bootstrap query's rows and break
  ALL authentication with a 401-everywhere outage. (rubric cat 3) |
  fix: add a CI test that enumerates RLS-exempt tables from the
  `pg_policies` system catalog and asserts `creators` and
  `creator_api_keys` are still exempt; OR refactor the bootstrap to
  use `AdminSessionLocal` (BYPASSRLS) for the lookup ONLY, then hand
  the Creator off to the rest of the request via the standard
  `get_session` dependency.

- [SEV2] config.py:222-231 — `print(..., file=sys.stderr)` on fatal
  startup config failure. Correct in spirit (logger not configured at
  import time of `config`) but CLAUDE.md production standard says
  "`logging` module only — no `print()`" and the gap means container
  log aggregators that parse JSON lines (the default `LOG_JSON=True`)
  miss the fatal startup message entirely. (rubric cat 8) | fix: call
  `logging.basicConfig(stream=sys.stderr, level=logging.ERROR,
  format="[CreatorClip] %(message)s")` immediately inside the `except
  ValidationError` block, then `logging.error(...)`. Same user-visible
  output, single log path Sentry/Loki can ingest.

- [cleanup] crypto.py:13-24 — `_fernet()` constructs a fresh
  `MultiFernet` on EVERY encrypt/decrypt call. Cheap but non-zero
  (HMAC + AES context init); a token-refresh-heavy endpoint pays
  repeatedly. (rubric cat 6) | fix: `@functools.lru_cache(maxsize=1)`
  keyed on `(settings.TOKEN_ENCRYPTION_KEY,
  settings.TOKEN_ENCRYPTION_KEY_PREVIOUS)` so rotation gets a fresh
  instance and steady state hits the cache.

- [cleanup] main.py:43-52 — lifespan reaches into TWO modules' private
  internals (`youtube._http`, `worker.progress`) via function-local
  imports. Each new shared async resource adds another such block.
  (rubric cat 6) | fix: define a
  `shared_resources.register_aclose(coro_fn)` registry that modules
  call at import time; lifespan iterates the registry and awaits each.
  Makes shutdown order inspectable and removes the coupling.

- [cleanup] main.py:130-132 — `_pg_dsn()` is a one-line dialect munge
  living in `main.py`; any future caller (script, healthcheck sidecar,
  worker probe) will reinvent it. (rubric cat 6) | fix: add `@property
  def psycopg_dsn(self)` to `Settings` returning the
  `postgresql://`-form URL, then use `settings.psycopg_dsn` here.

## Verified-present hardening (traced, no defect)

- **db.py:33** — `prepare_threshold=None` applied to BOTH engines via
  `connect_args` (db.py:51, 61). PgBouncer/psycopg3 prepared-statement
  hazard remains fixed.
- **db.py:44-62** — `pool_pre_ping=True`, app `15+5`, admin `5+10`,
  `pool_recycle=1800`. Documented against the 25-conn PgBouncer
  sidecar in docs/DEPLOYMENT.md. **Scale-checklist A (pool math) ✅
  by inspection** (still wants Locust evidence at target replicas).
- **db.py:119-148** — RLS `set_config('app.creator_id', :cid, true)` is
  parameterized (not raw `SET LOCAL`, which rejects bind params on the
  wire). Fires per-transaction via the `after_begin` listener whenever
  `session.info["creator_id"]` is set. **Scale-checklist D
  (per-tenant isolation) is structural at the DB layer** — a forgotten
  `WHERE creator_id` no longer leaks; the database itself refuses.
- **db.py:106-109, 154-156** — `dispose_engine()` awaits both engines
  on shutdown; `get_session()` uses `async with` for guaranteed close.
- **crypto.py:13-43** — MultiFernet built primary-first with optional
  previous-key fallback (zero-downtime rotation window honored).
  `decrypt()` maps `InvalidToken → TokenDecryptError` with a message
  carrying no ciphertext or key material — safe to log.
  **Scale-checklist I (secrets/key rotation) ✅ by inspection**;
  rotation runbook still owed per CLAUDE.md pre-launch.
- **config.py:187-217** — `_require_prod_secrets` fail-fast on
  `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` when
  `ENV=="production"`; fail-SAFE on `/metrics` when `METRICS_TOKEN`
  unset in prod (auto-disable with warning instead of crash-loop);
  Issue 105 `LOCAL_MEDIA_DIR` absolute-path enforcement live.
- **config.py:167-185** — Issue 105 `_validate_transcription_timeout`
  asserts the 30s cleanup-breathing-room invariant against
  `CELERY_SOFT_TIME_LIMIT_S` at import time (fails fast before a
  worker boot can drift into the dangerous zone).
- **config.py:51-57** — Anthropic model + web_search tool versions
  live in one place. Default tool `web_search_20260209` (GA, dynamic
  filtering).
- **auth.py:32-59** — identity is JWT-derived (`sub` → UUID → DB
  lookup); every failure path → 401 with safe `detail`.
  `request.state.creator_id` stashed AFTER successful DB resolution
  (Issue 104 contract honored).
- **api_key.py:48-66, 92-119** — raw key generated via
  `secrets.token_urlsafe`, hashed via SHA-256, lookup by indexed
  `key_hash` column with `revoked_at IS NULL` filter. Revoked keys
  deterministically fail authentication. Threat model documented at
  api_key.py:13-16 (no salting needed: 192-bit entropy raw key
  already defeats brute force). `request.state.creator_id` stashed
  alongside `session.info["creator_id"]` (Issue 104).
- **limiter.py:37-58** — Issue 106 corrections live: `verify_exp=True`
  with bounded leeway, narrowed exception, exception-class-only log.
  Quota-leak vector closed.
- **limiter.py:61-77** — Issue 104 `creator_key` reads
  `request.state.creator_id` rather than re-decoding the JWT — the
  slowapi-canonical pattern.
- **main.py:54-69, 96-103** — `/docs` disabled outside development;
  `redoc_url=None`; honesty constraint ("does not promise virality")
  in OpenAPI description; CORS uses explicit origin list with
  `allow_credentials=True` (no wildcard-with-credentials misconfig).
- **main.py:38-53** — lifespan awaits `_http.aclose()` AND
  `progress.aclose()` (Issue 86) on shutdown.
- **main.py:114-127** — `/metrics` gated behind
  `secrets.compare_digest` bearer-token comparison when token set;
  combined with config fail-safe, the prior "unauthenticated /metrics
  scrape surface" stays closed in prod.
- **models.py** — Fernet-encrypted token columns documented at the
  class level (lines 6-7, 174-177); `MinuteDeduction.video_id UNIQUE`
  is the Celery at-least-once idempotency key (line 587);
  `CreatorDna.uq_creator_dna_build_job_id` partial-unique index is
  the structural backstop for the advisory-lock guard (lines 411-417);
  `ClipFeedback.creator_id` is indexed via migration
  `0006_vector_and_fk_indexes` (verified); Issue 108 `Optional["X"]`
  → `"X | None"` sweep complete (no remaining `Optional[` matches).
- **observability.py:37, 165-210** — RequestIDMiddleware is pure ASGI,
  bounds id length/printability against log injection
  (`_valid_request_id`, 157-161), echoes the header, labels
  golden-signal latency by route TEMPLATE (203-208) to bound
  cardinality. JsonLogFormatter never special-cases token/PII; emits
  `request_id` on every line.
- **.env.example:18, 66** — `DATABASE_MIGRATION_URL` and
  `LOCAL_MEDIA_DIR` both present with production-vs-dev guidance.
  The prior Wave-4 finding "DATABASE_MIGRATION_URL missing from
  .env.example" is closed.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | 3 findings (api_key write-amp, /health builds clients per call, recreate_engine guard); core pool/session/lifespan plumbing correct |
| 2 Concurrency & scale | 2 findings (Celery ContextVar correlation safe only under prefork; /health connection churn vs. probe rate); db.py pool math + PgBouncer + parameterized RLS GUC all correct (scale-checklist A ✅ by inspection) |
| 3 Security & compliance | 1 finding (bootstrap-query-before-GUC by-convention invariant). Issue 106 closed the limiter quota-leak vector; MultiFernet rotation correct; API-key threat model documented; parameterized SQL throughout; /metrics + /docs gated; CORS locked; no PII/token in any log line in slice (scale-checklist D ✅ structural, I ✅ by inspection) |
| 4 Clip-quality | n/a (infra) |
| 5 Anthropic SDK | n/a (no LLM call in slice; model + tool config declared in config.py only) |
| 6 Cleanliness & typing | 3 cleanups (MultiFernet not cached, lifespan coupling, _pg_dsn placement). Issue 108 closed the prior typing-gap findings in auth/limiter/models |
| 7 Error handling / API | ok — main.py is app shell; /health returns safe statuses; /metrics 401 detail is safe; no stack traces leaked |
| 8 Config & paths | 1 finding (print() in config startup fallback). DATABASE_MIGRATION_URL finding closed; LOCAL_MEDIA_DIR absolute-in-prod validator live |

## Wave-9 delta vs. prior /assess

Prior assessment flagged 7 SEV2 + 5 cleanup in this slice. Closed this
wave: limiter.py verify_exp + quota leak (Issue 106), three typing
cleanups in auth/limiter/models (Issue 108), DATABASE_MIGRATION_URL
documentation gap (already fixed in `.env.example` Issue 79
follow-up). Remaining: 5 SEV2 + 3 cleanup. No net-new findings on
re-read.

## Module verdict

**NEEDS-WORK** — no BLOCKER and no cross-tenant leak in this slice
(Postgres RLS makes per-creator isolation structural via the
`set_config` GUC listener). Wave-9 closed the highest-priority Wave-4
SEV2 (the limiter quota-leak vector) and the entire typing-cleanup
backlog. The hot-list shrinks to: /health building fresh connections
per probe (self-inflicted scale ceiling, trivial to fix); api_key.py
writing `last_used_at` per request (write-amplification waiting for a
high-frequency caller); observability ContextVar correctness scoped to
the prefork pool (will silently break under any future pool change);
bootstrap-query-before-GUC pattern (correct today, load-bearing
by-convention invariant that needs a CI test to keep honest);
config.py `print()` fallback (single log path missed by JSON
aggregators).
