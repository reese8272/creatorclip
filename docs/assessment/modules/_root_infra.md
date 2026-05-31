# _root_infra — assessed 2026-05-31

Slice: db.py, crypto.py, config.py, auth.py, limiter.py, models.py, main.py,
observability.py, Dockerfile. (alembic/ owned by its own slice; migrations
referenced where load-bearing for findings here.)

WAVE-2 re-verification against current `main` head (baseline commit
`f5d44df`). WAVE-2 delta in this slice was Issue 84 bumping
`ANTHROPIC_WEB_SEARCH_TOOL` from `web_search_20250305` to
`web_search_20260209` (GA, dynamic filtering). Both `config.py:56` and
`.env.example:12` carry the new default with the documented rationale
referenced inline (lines 47-55). Same tool API shape; no call-site change
required. Every prior finding was traced line-by-line against the files as
they stand today. All three carry-forward SEV2s remain unchanged; the
Cat-8 cleanup logged last cycle is still unresolved.

## Findings

### Verified-present hardening (traced, no defect)

- db.py:33 — `prepare_threshold=None` in `_CONNECT_ARGS`, applied to BOTH
  app and admin engines via `connect_args` at db.py:51,61. The
  PgBouncer/psycopg3 prepared-statement BLOCKER (Issue 58) remains fixed.
- db.py:44-62 — `pool_pre_ping=True`, app pool `15 + 5`, admin pool
  `5 + 10`. Per-pod ceiling 35 against the 25-conn PgBouncer sidecar —
  see docs/DEPLOYMENT.md for the inequality (verify admin engine is in
  the sidecar budget; deployment-slice concern, flagged not fixed here).
- db.py:80-103 — `recreate_engine()` disposes BOTH pools with
  `close=False` before rebinding fresh engines and session factories
  (Issue 39 fork safety). Single-shot per fork from
  `worker/celery_app.py:80` (`worker_process_init`) — no concurrent
  rebind hazard. Workers reference `db.AsyncSessionLocal` /
  `db.AdminSessionLocal` via attribute lookup, so rebind is visible to
  every subsequent task. Confirmed across worker/tasks.py callers.
- db.py:106-109 — `dispose_engine()` awaits both engines on shutdown.
- db.py:119-148 — RLS `set_config('app.creator_id', :cid, true)`
  listener uses a parameterized function call (not `SET LOCAL` which
  rejects bind params on the wire). Fires only when
  `session.info["creator_id"]` is set, so the bootstrap auth lookup
  against the RLS-exempt `creators` table runs cleanly. The structural
  per-creator-isolation invariant (scale-checklist D) is enforced at
  the DB layer — a missing `WHERE creator_id` no longer leaks; RLS
  refuses the row.
- db.py:154-156 — `get_session()` async generator inside
  `async with AsyncSessionLocal()` — guaranteed close on every path.
- crypto.py:13-24 — MultiFernet built primary-first with optional
  previous-key fallback; rotation window honored.
- crypto.py:32-43 — `decrypt()` maps `InvalidToken` →
  `TokenDecryptError` with a message that carries no ciphertext or key
  material. Safe for logs and error responses.
- config.py:50-56 — Anthropic model + web_search tool versions live in
  one place. WAVE-2: default tool is now `web_search_20260209` (GA,
  dynamic filtering — Issue 84). Comment block at 51-55 cites the
  rationale and notes the same `name: "web_search"` call shape, so no
  call-site migration required. `.env.example:12` mirrors the new
  default with the explanatory comment.
- config.py:136-158 — `_require_prod_secrets` fail-fast on
  `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` when `ENV=="production"`,
  AND fail-SAFE on the `/metrics` scrape surface: if `METRICS_TOKEN` is
  unset in prod the endpoint is automatically disabled with a warning
  instead of crashing the app. The `ValidationError` handler
  (161-172) prints field NAMES only → no value leakage on
  missing-required config.
- auth.py:31-55 — identity is JWT-derived (`sub` → UUID → DB lookup on
  `Creator.id`); all failure modes → 401 with a safe `detail`.
  `session.info["creator_id"]` is stamped AFTER the bootstrap lookup, so
  the RLS GUC fires on every subsequent transaction in the request.
- limiter.py:31-34 — slowapi Limiter keyed per-creator off the JWT
  `sub` with Redis storage. `verify_exp=False` is intentional and
  acceptable for a rate-limit key (an expired-but-valid token still
  keys to its own creator).
- main.py:54-67 — `/docs` disabled outside development; `redoc_url=None`
  always; honesty constraint string is present in the OpenAPI
  description ("does not promise virality").
- main.py:93-99 — `ALLOWED_ORIGINS` parsed from config (no wildcard);
  CORS `allow_credentials=True` paired with an explicit origin list —
  correct pairing, no `*` + credentials misconfiguration.
- main.py:38-51 — lifespan calls `await _http.aclose()` AND
  `await progress.aclose()` (Issue 86) on shutdown.
- main.py:73-82 — every router (including `tasks_router` for SSE
  progress) is mounted exactly once.
- main.py:110-123 — `/metrics` is gated behind
  `secrets.compare_digest` bearer-token comparison when `METRICS_TOKEN`
  is set. Combined with the config.py prod fail-safe, the prior
  "unauthenticated /metrics scrape surface" stays closed for production.
- observability.py:37,165-210 — RequestIDMiddleware is pure ASGI, bounds
  id length/printability at `_valid_request_id` (157-161) against log
  injection, echoes the header, labels golden-signal latency by route
  TEMPLATE (203-208) to keep cardinality bounded. JsonLogFormatter
  (86-102) never special-cases token/PII; emits the `request_id` field
  on every line.
- observability.py:105-132 — `log_event` (Issue 88) emits structured
  business events through the dedicated `event` logger. Docstring at
  120-125 reiterates the "never raw bodies, tokens, or PII" rule —
  enforcement is by-convention at the call site, not structural.
- Dockerfile:26 — `ENV PYTHONPATH=/app` correctly present after
  `ENV PATH=` (commit c2a76d4). Load-bearing fix for the 2026-05-30 prod
  incident where forked Celery pool workers had `sys.path[0]` pointing
  at `/root/.local/bin` and could not import first-party packages
  (`dna`, `worker`, …). Verified intact at HEAD.

### Defects

- [SEV2] observability.py:224-241 — Celery `_bind_request_id` /
  `_record_task_and_clear` use the single module-level `request_id_ctx`
  and `_task_start_ctx` ContextVars. Comment at line 44 documents this
  is "safe because each worker process runs one task at a time" — true
  ONLY under the prefork pool. If the worker is ever started with
  `--pool=gevent/eventlet/threads`, concurrent tasks in one process
  will overwrite each other's request id and start time → mislabelled
  durations and broken log correlation. Carry-forward, unverified
  fix. (rubric cat 2) |
  fix: at worker startup (`worker/celery_app.py`), assert
  `app.conf.worker_pool == "prefork"` and refuse to boot otherwise;
  OR key task start off `task.request.id` via a per-task dict guarded
  by `task_postrun` removal. Low blast radius today; pure footgun for
  any future migration.

- [SEV2] Dockerfile:1-34 — image runs as root (no `USER` directive).
  Combined with `COPY . .` at line 28, every process in the container
  (uvicorn, celery, ffmpeg subprocesses) runs as UID 0 with write
  access to the entire app tree. An RCE via a media-processing
  dependency (ffmpeg, whisperx, ytdlp) would have full container
  privilege. Industry standard is a non-root user even when k8s
  `securityContext: runAsNonRoot` is set externally (defense in depth).
  Carry-forward. (rubric cat 3) |
  fix: after `COPY . .`, add
  `RUN useradd --create-home --uid 1000 app && chown -R app:app /app /root/.local`
  then `USER app`. Verify Celery worker can still write its
  state/temp dirs and that ffmpeg subprocesses run as `app`. Update
  docs/DEPLOYMENT.md with the chosen UID/GID.

- [SEV2] Dockerfile:34 — default `CMD` ships `uvicorn … --reload`. The
  line-33 comment ("override in docker-compose or production deploy")
  relies on every deployer remembering to override. A forgotten
  override in a prod manifest starts uvicorn in dev mode: filesystem
  watcher running, single worker, no graceful shutdown guarantees.
  Carry-forward. (rubric cat 3 / cat 8) |
  fix: make the default safe-for-prod
  (`gunicorn -k uvicorn.workers.UvicornWorker -w 4 main:app
  --bind 0.0.0.0:8000`) and move the `--reload` invocation into
  `docker-compose.yml` where dev belongs.

- [cleanup] main.py:43-50 — lifespan reaches into TWO modules' private
  internals (`youtube._http`, `worker.progress`) via function-local
  imports. Each new shared async resource adds another such block.
  (rubric cat 6) |
  fix: define a `shared_resources.register_aclose(coro_fn)` registry
  that modules call at import time; lifespan iterates the registry
  and awaits each. Removes the coupling and makes shutdown order
  inspectable.

- [cleanup] auth.py:27 — `decode_session_token` returns bare `dict`.
  Annotate `-> dict[str, Any]` — `payload["sub"]` is load-bearing for
  callers. Carry-forward. (rubric cat 6) |
  fix: `def decode_session_token(token: str) -> dict[str, Any]:` and
  import `Any` from `typing`.

- [cleanup] limiter.py:15 — `_creator_key(request)` parameter is
  untyped. Carry-forward. (rubric cat 6) |
  fix: `def _creator_key(request: Request) -> str:` (import from
  `starlette.requests` or `fastapi`).

- [cleanup] limiter.py:26 — bare `except Exception: pass` on JWT
  decode. Behavior is intentional (fail-open to IP keying so an
  unauthenticated / malformed-cookie request still gets a rate-limit
  key) but the WHY is undocumented. Carry-forward. (rubric cat 6) |
  fix: add a one-line comment per CLAUDE.md code-style — "any decode
  failure (expired, malformed, bad signature) falls back to IP keying
  so we never 500 the rate-limit middleware".

- [cleanup] config.py:23 + .env.example — `DATABASE_MIGRATION_URL`
  (Issue 79 RLS admin role) is declared in `Settings` with a
  documented fallback to `DATABASE_URL`, but it is STILL NOT listed
  in `.env.example` (verified at .env.example:15-17 — the only
  database stanza is `DATABASE_URL`). Anyone copying `.env.example`
  to `.env` for a production setup will silently end up with
  `database_migration_url == DATABASE_URL` (single role; no BYPASSRLS
  split). Carried forward from 2026-05-30; new this WAVE-2 cycle
  remains the absence of any fix. (rubric cat 8) |
  fix: add a stanza to `.env.example` immediately under `DATABASE_URL`:
  ```
  DATABASE_MIGRATION_URL=                  # REQUIRED in production — BYPASSRLS role for Alembic + worker cross-tenant sweeps. Leave blank in dev (falls back to DATABASE_URL).
  ```

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — engine dispose/recreate correct; `get_session` async-context manager guarantees close; lifespan drains BOTH `youtube._http` and `worker.progress` Redis client |
| 2 Concurrency & scale | ok — pool math correct, RLS via parameterized `set_config` (not SET LOCAL); no blocking call in async paths; 1 SEV2 (ContextVar correlation safe only under prefork) |
| 3 Security & compliance | ok — `/metrics` SEV2 closed (prod fail-safe + bearer-token gate); tokens via decrypt()/MultiFernet, no PII in logs, CORS locked, fail-fast prod secrets verified; RLS makes per-creator isolation structural; 2 SEV2 Docker hardening gaps remain (root user, --reload default) |
| 4 Clip-quality | n/a — infra module |
| 5 Anthropic SDK | n/a — no LLM call in slice (model + web_search tool versions declared in config.py:50-56 only; WAVE-2 Issue 84 bump to `web_search_20260209` verified) |
| 6 Cleanliness & typing | 4 cleanups (lifespan coupling, typing gaps in auth/limiter, silent except) |
| 7 Error handling / API | ok — main.py is app wiring not a router; /health returns safe statuses, no stack traces; /metrics 401 detail is safe |
| 8 Config & paths | ok — pydantic-settings fail-fast, `_STATIC` absolute via `Path(__file__).parent`, `PYTHONPATH=/app` present, WAVE-2 web-search default updated in both Settings and `.env.example`; 1 carry-forward cleanup (DATABASE_MIGRATION_URL still missing from `.env.example`) |

## Module verdict
NEEDS-WORK — no BLOCKER and no cross-tenant leak in this slice (RLS
enforces creator isolation structurally at the DB layer). Three SEV2s
remain, all carried forward unchanged from 2026-05-30: the Celery
ContextVar correlation that is safe only under prefork, and the two
Dockerfile production-hardening gaps (container runs as root; default
CMD ships uvicorn --reload). WAVE-2 Issue 84 (`ANTHROPIC_WEB_SEARCH_TOOL`
→ `web_search_20260209`) is correctly propagated to both `config.py:56`
and `.env.example:12`. The Cat-8 cleanup logged last cycle —
`DATABASE_MIGRATION_URL` declared in Settings but absent from
`.env.example` — is still unresolved.
