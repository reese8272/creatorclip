# _root_infra — assessed 2026-05-31

Slice: db.py, crypto.py, config.py, auth.py, limiter.py, models.py, main.py,
api_key.py, observability.py. `clients.py` was listed in the slice but DOES
NOT exist in the repo (verified `ls /home/reese/workspace/Youtube-Video-AI-Editor/*.py`); no
module-level client-singleton hub exists — external clients live with their
modules (`youtube/_http.py`, `worker/progress.py`) and are correctly closed
in main.py's lifespan. Re-assessment supersedes the prior Wave-4 report;
new findings on api_key.py (Issue 95 OBS auth path) and limiter.py
`verify_exp=False` are first time logged.

## Findings

- [SEV2] limiter.py:19-27 — `jwt.decode(..., options={"verify_exp": False})`
  PLUS bare `except Exception: pass` means an EXPIRED or FORGED-but-malformed
  token still keys the rate limit to the token's `sub`. An attacker who
  exfiltrates an expired session token can continue consuming a victim's
  per-creator quota indefinitely; conversely a JWT_SECRET_KEY misconfig is
  silently swallowed and every legit user collapses onto their NAT IP
  bucket. (rubric cat 3) |
  fix: verify exp with bounded leeway
  (`options={"verify_exp": True}, leeway=300`); narrow the except to
  `(jwt.InvalidTokenError, KeyError, ValueError)` and log decode failures at
  WARNING through a rate-limited counter (e.g. once per minute per key
  class) so misconfig is visible in Sentry/log search, never the token body.

- [SEV2] main.py:135-153 — `/health` opens a fresh `psycopg.AsyncConnection`
  AND constructs a fresh `aioredis.from_url()` client per probe. Under
  aggressive k8s readiness/liveness probing (every few seconds × N
  replicas) this is sustained connect/disconnect churn against Postgres
  OUTSIDE the SQLAlchemy pool, competing with app traffic for backend
  connections and defeating the PgBouncer pool math in db.py. The redis
  client spins up a fresh pool each call; an exception path that misses
  `aclose()` leaks the pool. (rubric cat 1 + cat 2) |
  fix: reuse the existing SQLAlchemy `engine` for the PG check
  (`async with engine.connect() as c: await c.execute(text("SELECT 1"))`);
  bind a module-level singleton `redis.asyncio.Redis.from_url(REDIS_URL)`
  at import time, `aclose()` it in the lifespan shutdown alongside
  `youtube._http` and `worker.progress`. Wrap both checks in
  `asyncio.wait_for(..., timeout=2.0)` so a slow dep can't queue probes.

- [SEV2] api_key.py:113-114 — every API-key-authenticated request issues
  `UPDATE creator_api_keys SET last_used_at = now()` and `await
  session.commit()` inside the auth dependency, BEFORE the handler runs.
  A single OBS app uploading every few seconds is fine, but the docstring
  ("any future non-browser client") invites high-frequency callers; each
  request synchronously fsyncs a hot row. (rubric cat 1 / scale-checklist
  cat 2) |
  fix: coarse-grain `last_used_at` — only UPDATE if
  `last_used_at IS NULL OR last_used_at < now() - interval '60 seconds'`;
  skip the commit when no row changed. Keeps the management-UI freshness
  signal without per-request write amplification.

- [SEV2] observability.py:43-44, 224-241 — Celery `_bind_request_id` /
  `_record_task_and_clear` use module-level ContextVars
  (`request_id_ctx`, `_task_start_ctx`). Comment at line 43 documents
  this is "safe because each worker process runs one task at a time" —
  true ONLY under the prefork pool. If a future migration switches to
  `--pool=gevent/eventlet/threads`, concurrent tasks in one process
  overwrite each other's correlation id and start time → mislabelled
  durations and broken log correlation. (rubric cat 2) |
  fix: at worker startup (`worker/celery_app.py`), assert
  `app.conf.worker_pool == "prefork"` and refuse to boot otherwise; OR
  key task start off `task.request.id` in a per-task dict guarded by
  `task_postrun` removal.

- [SEV2] db.py:80-103 — `recreate_engine()` rebinds the module-global
  engine and BOTH session factories. Documented for "after fork (Issue
  39)" use only (single-shot in `worker_process_init`); but the function
  is module-public and has no guard against being called with in-flight
  sessions, which would leave outstanding `AsyncSession` instances
  holding refs to the disposed pool. (rubric cat 1) |
  fix: add an explicit `_already_called: bool` flag and raise on
  re-entry, or prefix the function with `_` and assert the caller is
  `worker_process_init` via stack inspection; document the precondition
  in the docstring with the words "MUST NOT be called with in-flight
  sessions".

- [SEV2] auth.py:46-54 / api_key.py:96-117 — both auth dependencies issue
  their bootstrap SELECT (Creator / CreatorApiKey lookup) on the session
  BEFORE `session.info["creator_id"]` is set. SQLAlchemy autobegin
  happens on first query, so the bootstrap transaction has no
  `app.creator_id` GUC — this is intentional today because `creators`
  and `creator_api_keys` are RLS-exempt (Issue 56 / 95), but it is a
  by-convention invariant: a future migration that flips either table
  under RLS would silently lose the bootstrap query's rows and break
  ALL authentication with a 401-everywhere outage. (rubric cat 3) |
  fix: add a CI test that enumerates RLS-exempt tables from the
  `pg_policies` system catalog and asserts `creators` and
  `creator_api_keys` are still exempt; OR refactor the bootstrap to use
  `AdminSessionLocal` (BYPASSRLS) for the lookup ONLY, then hand the
  Creator off to the rest of the request via the standard
  `get_session` dependency.

- [SEV2] config.py:174-184 — `print(..., file=sys.stderr)` on fatal
  startup config failure. Correct in spirit (logger not configured at
  import time of `config`) but CLAUDE.md production standard says
  "`logging` module only — no `print()`" and the gap means container
  log aggregators that parse JSON lines (the default `LOG_JSON=True`)
  miss the fatal startup message entirely. (rubric cat 8) |
  fix: call `logging.basicConfig(stream=sys.stderr, level=logging.ERROR,
  format="[CreatorClip] %(message)s")` immediately inside the
  `except ValidationError` block, then `logging.error(...)`. Same
  user-visible output, single log path Sentry/Loki can ingest.

- [cleanup] crypto.py:13-24 — `_fernet()` constructs a fresh
  `MultiFernet` on EVERY encrypt/decrypt call. Cheap but non-zero
  (HMAC + AES context init); a token-refresh-heavy endpoint pays
  repeatedly. (rubric cat 6) |
  fix: `@functools.lru_cache(maxsize=1)` keyed on
  `(settings.TOKEN_ENCRYPTION_KEY, settings.TOKEN_ENCRYPTION_KEY_PREVIOUS)`
  so rotation gets a fresh instance and steady state hits the cache.

- [cleanup] main.py:43-50 — lifespan reaches into TWO modules' private
  internals (`youtube._http`, `worker.progress`) via function-local
  imports. Each new shared async resource adds another such block.
  (rubric cat 6) |
  fix: define a `shared_resources.register_aclose(coro_fn)` registry
  that modules call at import time; lifespan iterates the registry
  and awaits each. Makes shutdown order inspectable and removes the
  coupling.

- [cleanup] main.py:130-132 — `_pg_dsn()` is a one-line dialect munge
  living in `main.py`; any future caller (script, healthcheck sidecar,
  worker probe) will reinvent it. (rubric cat 6) |
  fix: add `@property def psycopg_dsn(self)` to `Settings` returning the
  `postgresql://`-form URL, then use `settings.psycopg_dsn` here.

- [cleanup] auth.py:27 — `decode_session_token` returns bare `dict`;
  `payload["sub"]` is load-bearing. (rubric cat 6) |
  fix: `def decode_session_token(token: str) -> dict[str, Any]:` and
  `from typing import Any`.

- [cleanup] limiter.py:15 — `_creator_key(request)` parameter is
  untyped. (rubric cat 6) |
  fix: `def _creator_key(request: Request) -> str:` (import `Request`
  from `starlette.requests` or `fastapi`).

- [cleanup] models.py:106,229,235-238,479 — mix of `Optional["X"]` and
  `X | None` for forward-ref relationships. PEP 604 works with string
  forward refs in SQLAlchemy 2.0 since 2.0.0; pick `| None` to match
  the rest of the file. (rubric cat 6) |
  fix: `s/Optional\["X"\]/"X" | None/g` in models.py and drop
  `from typing import Optional`.

- [cleanup] config.py:23 + `.env.example` — `DATABASE_MIGRATION_URL`
  (Issue 79 RLS admin role) is declared in `Settings` with a documented
  fallback to `DATABASE_URL`, but it is NOT listed in `.env.example`.
  Anyone copying `.env.example` to `.env` for a production setup silently
  ends up with `database_migration_url == DATABASE_URL` (single role, no
  BYPASSRLS split). Carry-forward from prior assessment. (rubric cat 8) |
  fix: add to `.env.example` under the `DATABASE_URL` stanza:
  `DATABASE_MIGRATION_URL=  # REQUIRED in production — BYPASSRLS role for Alembic + worker cross-tenant sweeps. Leave blank in dev.`

## Verified-present hardening (traced, no defect)

- db.py:33 — `prepare_threshold=None` applied to BOTH engines via
  `connect_args` (db.py:51, 61). PgBouncer/psycopg3 prepared-statement
  hazard remains fixed.
- db.py:44-62 — `pool_pre_ping=True`, app `15+5`, admin `5+10`,
  `pool_recycle=1800`. Documented against the 25-conn PgBouncer sidecar
  in docs/DEPLOYMENT.md.
- db.py:119-148 — RLS `set_config('app.creator_id', :cid, true)` is
  parameterized (not raw `SET LOCAL`, which rejects bind params on the
  wire). Fires per-transaction via the `after_begin` listener whenever
  `session.info["creator_id"]` is set. **The per-creator-isolation
  invariant (scale-checklist D) is structural at the DB layer** —
  forgotten `WHERE creator_id` no longer leaks.
- db.py:106-109, 154-156 — `dispose_engine()` awaits both engines on
  shutdown; `get_session()` uses `async with` for guaranteed close.
- crypto.py:13-43 — MultiFernet built primary-first with optional
  previous-key fallback (zero-downtime rotation window honored).
  `decrypt()` maps `InvalidToken → TokenDecryptError` with a message
  carrying no ciphertext or key material — safe to log.
- config.py:148-170 — `_require_prod_secrets` fail-fast on
  `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` when `ENV=="production"`;
  fail-SAFE on `/metrics` when `METRICS_TOKEN` unset in prod (auto-disable
  with warning instead of crash-loop).
- config.py:50-56 — Anthropic model + web_search tool versions live in
  one place. Default tool `web_search_20260209` (GA, dynamic filtering).
- auth.py:31-55 — identity is JWT-derived (`sub` → UUID → DB lookup);
  every failure path → 401 with safe `detail`.
- api_key.py:48-66, 92-104 — raw key generated via
  `secrets.token_urlsafe`, hashed via SHA-256, lookup by indexed
  `key_hash` column with `revoked_at IS NULL` filter. Revoked keys
  deterministically fail authentication. Threat model documented at
  api_key.py:13-16 (no salting needed: 192-bit entropy raw key already
  defeats brute force).
- main.py:54-69, 93-99 — `/docs` disabled outside development;
  `redoc_url=None`; honesty constraint ("does not promise virality") in
  OpenAPI description; CORS uses explicit origin list with
  `allow_credentials=True` (no wildcard-with-credentials misconfig).
- main.py:38-51 — lifespan awaits `_http.aclose()` AND
  `progress.aclose()` (Issue 86) on shutdown.
- main.py:114-127 — `/metrics` gated behind `secrets.compare_digest`
  bearer-token comparison when token set; combined with config fail-safe,
  the prior "unauthenticated /metrics scrape surface" stays closed in prod.
- models.py — Fernet-encrypted token columns documented at the class
  level (lines 6-7, 175-177); `MinuteDeduction.video_id UNIQUE` is the
  Celery at-least-once idempotency key (line 587); `CreatorDna`
  `uq_creator_dna_build_job_id` partial-unique index is the structural
  backstop for the advisory-lock guard (lines 412-417); `ClipFeedback`
  `creator_id` is indexed via migration `0006_vector_and_fk_indexes`
  (verified).
- observability.py:37, 165-210 — RequestIDMiddleware is pure ASGI,
  bounds id length/printability against log injection
  (`_valid_request_id`, 157-161), echoes the header, labels golden-signal
  latency by route TEMPLATE (203-208) to bound cardinality.
  JsonLogFormatter never special-cases token/PII; emits `request_id` on
  every line.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | 3 findings (api_key write-amp, /health builds clients per call, recreate_engine guard); core pool/session/lifespan plumbing correct |
| 2 Concurrency & scale | 2 findings (Celery ContextVar correlation safe only under prefork; /health connection churn vs. probe rate); db.py pool math + PgBouncer + parameterized RLS GUC all correct |
| 3 Security & compliance | 3 findings (limiter verify_exp=False + silent swallow, bootstrap-query-before-GUC structural invariant); MultiFernet rotation correct, API-key threat model documented, parameterized SQL throughout, /metrics + /docs gated, CORS locked, no PII/token in any log line in slice |
| 4 Clip-quality | n/a (infra) |
| 5 Anthropic SDK | n/a (no LLM call in slice; model + tool config declared in config.py only) |
| 6 Cleanliness & typing | 5 cleanups (MultiFernet not cached, lifespan coupling, _pg_dsn placement, typing gaps in auth/limiter, Optional/`\| None` mix in models) |
| 7 Error handling / API | ok — main.py is app shell; /health returns safe statuses; /metrics 401 detail is safe; no stack traces leaked |
| 8 Config & paths | 2 findings (print() in config startup fallback; DATABASE_MIGRATION_URL missing from .env.example); pydantic-settings fail-fast correct, `_STATIC` absolute via `Path(__file__).parent` |

## Module verdict

**NEEDS-WORK** — no BLOCKER and no cross-tenant leak in this slice (Postgres
RLS makes per-creator isolation structural via the `set_config` GUC
listener). The hot-list: limiter.py silently accepting expired tokens for
per-creator rate-limit keying is a per-creator-quota leak vector; /health
building fresh connections per probe is a self-inflicted scale ceiling
trivial to fix; api_key.py writing `last_used_at` per request is
write-amplification waiting for a high-frequency caller; the
bootstrap-query-before-GUC pattern is correct today but a load-bearing
by-convention invariant that needs a CI test to keep honest.
