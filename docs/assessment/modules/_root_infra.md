# _root_infra — assessed 2026-05-30

Slice: db.py, crypto.py, config.py, auth.py, limiter.py, models.py, main.py,
observability.py, Dockerfile. (alembic/ is owned by its own slice; index
migrations referenced where load-bearing for findings here.)

Re-assessed after the 2026-05-30 Dockerfile hotfix (PYTHONPATH=/app) and the
Issue-86 wiring in main.py (tasks router mounted, progress Redis client closed
in lifespan). All claims below verified by reading current code.

## Findings

### Verified-present hardening (traced, no defect)

- db.py:33 — `prepare_threshold=None` in `_CONNECT_ARGS`, applied via
  `connect_args` at db.py:51,61. The PgBouncer/psycopg3 prepared-statement
  BLOCKER (Issue 58) is fixed for BOTH the app and admin engines. Confirmed.
- db.py:47-50,58-62 — `pool_pre_ping=True`, `pool_size=15`, `max_overflow=5`,
  `pool_recycle=1800` on the app engine; admin engine `pool_size=5,
  max_overflow=10`. Per-pod ceiling 20 + 15 = 35; documented in
  docs/DEPLOYMENT.md against the 25-conn PgBouncer sidecar — verify the
  sidecar budget covers the admin engine too (slice-out of scope to fix,
  flag to deployment owner).
- db.py:80-103 — `recreate_engine()` disposes BOTH pools with `close=False`
  before rebinding fresh engines and session factories (Issue 39 fork
  safety). The class-level `after_begin` listener on `Session` survives
  rebind — comment at db.py:102-103 is correct.
- db.py:106-109 — `dispose_engine()` awaits both engines' `dispose()`.
- db.py:119-148 — RLS `set_config('app.creator_id', :cid, true)` listener
  uses a parameterized function call (not `SET LOCAL` which would reject
  bind params). Fires only when `session.info["creator_id"]` is set, so
  the bootstrap auth lookup against the RLS-exempt `creators` table runs
  cleanly. The structural per-creator-isolation invariant (scale-checklist D)
  is now enforced at the DB layer — a missing `WHERE creator_id` no longer
  leaks data, RLS refuses the row.
- db.py:154-156 — `get_session()` is an async generator inside an
  `async with AsyncSessionLocal()` block — guaranteed close on every path,
  including exceptions raised in the dependency consumer.
- crypto.py:13-24 — MultiFernet built primary-first with optional previous-
  key fallback; rotation window honored. `decrypt()` (32-43) maps
  `InvalidToken` → `TokenDecryptError` with a message that carries no
  ciphertext or key material. Safe for logs and error responses.
- config.py:126-148 — `_require_prod_secrets` fail-fast on `STRIPE_SECRET_KEY`
  / `STRIPE_WEBHOOK_SECRET` when `ENV=="production"`, AND fail-SAFE on the
  `/metrics` scrape surface: if `METRICS_TOKEN` is unset in prod the endpoint
  is automatically disabled with a warning instead of crashing the app. Plus
  the field-name-only `ValidationError` handler (151-162) → `sys.exit(1)`
  with no value leakage on missing required config.
- auth.py:31-55 — identity is JWT-derived (`sub` → UUID → DB lookup on
  `Creator.id`), all failure modes → 401 with safe message. `session.info`
  is stamped with `creator_id` AFTER the bootstrap lookup, so the RLS GUC
  fires on every subsequent transaction in the request (db.py:119 listener).
- limiter.py:31-34 — slowapi Limiter keyed per-creator off the JWT `sub`
  with Redis storage. `verify_exp=False` is intentional and acceptable for
  a rate-limit key (an expired-but-valid token still keys to its own creator).
- main.py:54-71 — `/docs` disabled outside development; `redoc_url=None`
  always; ALLOWED_ORIGINS parsed from config (no wildcard); CORS
  `allow_credentials=True` paired with an explicit origin list — correct
  pairing.
- main.py:38-51 — lifespan calls `await _http.aclose()` AND
  `await progress.aclose()` (Issue 86) on shutdown. Verified
  `worker/progress.py:246` exposes `async def aclose()` that closes the
  module-singleton redis client.
- main.py:73-82 — every router (including the new `tasks_router` for SSE
  progress) is mounted exactly once; no duplicate registrations.
- main.py:110-123 — `/metrics` is gated behind `secrets.compare_digest`
  bearer-token comparison when `METRICS_TOKEN` is set. Combined with the
  config.py:142-147 prod fail-safe, the prior SEV2 "unauthenticated
  /metrics scrape surface" is now closed for production. CLOSING the
  prior SEV2.
- observability.py:135-180 — RequestIDMiddleware is pure ASGI, bounds id
  length/printability against log injection (127-131), echoes the header,
  labels golden-signal latency by route TEMPLATE (172-174) to keep
  cardinality bounded. JsonLogFormatter (86-102) never special-cases
  token/PII; emits the request_id field on every line.
- Dockerfile:26 — `ENV PYTHONPATH=/app` correctly added after `ENV PATH=`.
  This is the load-bearing fix for the 2026-05-30 prod incident where
  forked Celery pool workers had `sys.path[0]` pointing at
  `/root/.local/bin` and could not import first-party packages (`dna`,
  `worker`, …). The subprocess guard at
  `tests/test_worker_imports_integration.py` will catch any regression.

### Defects

- [SEV2] observability.py:189-211 — Celery `_bind_request_id` /
  `_record_task_and_clear` use the single module-level `request_id_ctx`
  and `_task_start_ctx` ContextVars. This is correct ONLY under the
  prefork pool (the assumption documented at observability.py:43-44).
  If the worker is ever started with `--pool=gevent/eventlet/threads`,
  concurrent tasks in one process will overwrite each other's request
  id and start time → mislabelled durations and broken log
  correlation. Carry-forward from prior cycle, unchanged. |
  fix: at worker startup (worker/celery_app.py), assert
  `app.conf.worker_pool == "prefork"` and refuse to boot otherwise;
  OR key task start off `task.request.id` via a per-task dict guarded
  by `task_postrun` removal. Low blast radius today (prefork is
  configured), but it is a footgun for any future migration.

- [SEV2] Dockerfile:1-34 — image runs as root (no `USER` directive).
  Combined with `COPY . .` at line 28, every process in the container
  (uvicorn, celery, ffmpeg subprocesses) has UID 0 and write access to
  the entire app tree. An RCE via a media-processing dependency
  (ffmpeg, whisperx, ytdlp) would have full container privilege.
  Production-hardening industry standard is a non-root user in the
  image, even when k8s `securityContext: runAsNonRoot` is set
  externally (defense in depth). |
  fix: after the `COPY . .` line, add
  `RUN useradd --create-home --uid 1000 app && chown -R app:app /app /root/.local`
  then `USER app`. Verify the Celery worker can still write its
  state/temp dirs and that ffmpeg subprocesses run as `app`. Update
  docs/DEPLOYMENT.md with the chosen UID/GID.

- [SEV2] Dockerfile:34 — default `CMD` ships `uvicorn … --reload`.
  The line 33 comment ("override in docker-compose or production
  deploy") relies on every deployer remembering to override. A
  forgotten override in a prod manifest starts uvicorn in dev mode:
  filesystem watcher running, single worker, no graceful shutdown
  guarantees. |
  fix: make the default safe-for-prod
  (`gunicorn -k uvicorn.workers.UvicornWorker -w 4 main:app
  --bind 0.0.0.0:8000`) and move the `--reload` invocation to
  `docker-compose.yml` where dev belongs. Keeps the
  least-surprise-on-prod-launch invariant.

- [cleanup] main.py:43-50 — lifespan reaches into TWO modules'
  private internals (`youtube._http`, `worker.progress` module-state)
  via function-local imports. Each new shared async resource will
  add another such block. |
  fix: define a `shared_resources.register_aclose(coro_fn)` registry
  that modules call at import; lifespan iterates the registry and
  awaits each. Removes the coupling and makes the shutdown order
  inspectable. Carry-forward from prior cycle, now applies to two
  callsites instead of one.

- [cleanup] auth.py:27 — `decode_session_token` returns bare `dict`.
  Annotate `-> dict[str, Any]` — the shape (`payload["sub"]`) is
  load-bearing for callers. Carry-forward; mypy gate likely already
  flags.

- [cleanup] limiter.py:15 — `_creator_key(request)` parameter is
  untyped; annotate `request: Request` (starlette). Carry-forward.

- [cleanup] limiter.py:26 — bare `except Exception: pass` on JWT
  decode. Behavior is intentional (fail-open to IP keying so an
  unauthenticated or malformed-cookie request still gets a rate-limit
  key) but the WHY is undocumented. Add a one-line comment per
  CLAUDE.md code-style. Carry-forward.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — engine dispose/recreate correct; `get_session` async context manager guarantees close; lifespan now drains BOTH `youtube._http` and `worker.progress` Redis client |
| 2 Concurrency & scale | ok — pool math unchanged and correct, RLS via parameterized `set_config` (not SET LOCAL); no blocking call in async paths; 1 SEV2 (ContextVar correlation safe only under prefork, observability.py:189) |
| 3 Security & compliance | ok — `/metrics` SEV2 closed (prod fail-safe in config.py + bearer-token gate in main.py); tokens via decrypt()/MultiFernet, no PII in logs, docs/CORS locked, fail-fast prod secrets all verified; RLS makes per-creator isolation structural; 2 SEV2 hardening gaps in Dockerfile (root user, --reload default) |
| 4 Clip-quality | n/a — infra module, no scoring |
| 5 Anthropic SDK | n/a — no LLM call in slice (model/tool versions only declared in config.py:50-51) |
| 6 Cleanliness & typing | 4 cleanups (lifespan coupling now touches 2 modules, typing gaps in limiter/auth, silent except) |
| 7 Error handling / API | ok — main.py is app wiring, not a router; /health returns safe statuses, no stack traces to client; /metrics 401 is safe |
| 8 Config & paths | ok — pydantic-settings fail-fast, `_STATIC` absolute via `Path(__file__).parent`, `PYTHONPATH=/app` makes /app structurally importable for every process in the image (Dockerfile hotfix verified) |

## Module verdict
NEEDS-WORK — prior `/metrics` SEV2 is now closed by the prod fail-safe in
config.py and the bearer-token gate in main.py; the Dockerfile PYTHONPATH
hotfix correctly resolves the prod ModuleNotFoundError incident. Three
SEV2s remain: the Celery ContextVar correlation that is safe only under
prefork (carry-forward), and two Dockerfile production-hardening gaps
(container runs as root; default CMD ships uvicorn --reload). No BLOCKER
and no cross-tenant leak in this slice — RLS now enforces creator
isolation structurally at the DB layer.
