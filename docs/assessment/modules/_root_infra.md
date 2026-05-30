# _root_infra — assessed 2026-05-29

Slice: db.py, crypto.py, config.py, auth.py, limiter.py, models.py, main.py,
observability.py. (alembic/ lives in its own package, not a root entrypoint →
out of slice per contract.)

This is a re-assessment after the Issues 58–75 hardening session. All claims below
verified by reading current code.

## Findings

### Verified-present hardening (traced, no defect)
- db.py:18 — psycopg3 `prepare_threshold=None` in `_CONNECT_ARGS`, applied via
  `connect_args` at db.py:36. The Issue-58 BLOCKER (PgBouncer transaction-pooling
  + server-side prepared statements) is correctly fixed. Confirmed present.
- db.py:32-35 — `pool_pre_ping=True`, `pool_size=15`, `max_overflow=5`,
  `pool_recycle=1800`. Per-pod ceiling 20 < 25-conn sidecar budget. Correct.
- db.py:65-67 — `dispose_engine()` awaits `engine.dispose()`; db.py:48-62
  `recreate_engine()` rebinds the pool after fork with `dispose(close=False)` so
  inherited FDs aren't closed under the parent. Correct.
- crypto.py:13-24 — MultiFernet built primary-first, previous-key fallback;
  rotation window honored. decrypt() (crypto.py:32-43) maps InvalidToken →
  `TokenDecryptError` with a message that carries no ciphertext/key material. Safe.
- config.py:85-98 — `_require_prod_secrets` fail-fast on STRIPE_SECRET_KEY /
  STRIPE_WEBHOOK_SECRET when `ENV == "production"`. config.py:101-112 fail-fast on
  any missing required field via pydantic ValidationError → `sys.exit(1)` with a
  field-name-only message (no value leakage). Correct.
- auth.py:39-46 — identity is JWT-derived: `sub` decoded, coerced to UUID, then a
  creator-scoped `WHERE Creator.id == creator_id` lookup. 401 on any failure, no
  internal detail leaked.
- limiter.py:31-34 — slowapi Limiter keyed per-creator off the JWT `sub`, storage
  on real Redis (`settings.REDIS_URL`). Signature IS verified (only `verify_exp`
  disabled — acceptable for a rate-limit key, an expired-but-valid token still keys
  to its own creator).
- main.py:57 — `/docs` disabled outside development (`docs_url=None` when
  `ENV != "development"`); redoc always off. main.py:87 — ALLOWED_ORIGINS parsed
  from config, no wildcard. CORS `allow_credentials=True` paired with an explicit
  origin list (not `*`) — correct.
- observability.py — RequestIDMiddleware is pure ASGI (135-180), binds a
  ContextVar (37), bounds id length/printability against log injection (127-131),
  echoes header, records golden-signal latency labelled by route template
  (172-174) to keep cardinality bounded. JSON formatter (86-102) emits request_id
  and never special-cases token/PII. Celery propagation via before_task_publish /
  task_prerun / task_postrun with `weak=False` (214-225); wired in
  worker/celery_app.py:16. configure_logging wired in both main.py:32 and the
  worker. Correct and complete.
- All new observability config (LOG_JSON, REQUEST_ID_HEADER, METRICS_ENABLED) and
  rotation/Stripe keys present in `.env.example` with descriptions.

### Defects
- [SEV2] main.py:102-107 — `/metrics` is exposed with no authentication or network
  scoping when `METRICS_ENABLED=true`. The endpoint reveals route templates,
  traffic volume, error rates, and Celery task names to any unauthenticated caller.
  The module docstring/config implies disabling the flag is the only control. |
  fix: bind `/metrics` to an internal-only listener (separate port/interface
  scraped by Prometheus inside the cluster), or gate it behind a bearer token /
  network policy. At minimum document in docs/DEPLOYMENT.md that ingress must not
  route `/metrics` publicly. (needs-runtime-confirmation on ingress config.)
- [SEV2] observability.py:189-211 — Celery `_bind_request_id` /
  `_record_task_and_clear` use a single module-level `request_id_ctx` and
  `_task_start_ctx` ContextVar. This is safe ONLY under the prefork pool (one task
  per process at a time), which the comment at :44 assumes. If the worker is ever
  run with `--pool=gevent/eventlet/threads`, concurrent tasks in one process will
  clobber each other's request id and start time → mislabelled durations and
  cross-task log correlation. | fix: assert/document the prefork assumption at
  worker startup, or key task start off `task.request` rather than a shared
  ContextVar. Low blast radius today (prefork is the configured pool).
- [cleanup] main.py:41-43 — lifespan reaches into `youtube._http` via a function-
  local import and closes a private attribute. Tight coupling to another module's
  internals. | fix: expose a public `youtube.aclose_http()` and call that.
- [cleanup] auth.py:27 — `decode_session_token` returns bare `dict`; annotate
  `-> dict[str, Any]` for parity with the typing mandate (the value shape is
  load-bearing for callers reading `payload["sub"]`).
- [cleanup] limiter.py:15 — `_creator_key(request)` parameter is untyped; annotate
  `request: Request` (starlette). The mypy gate likely flags this already.
- [cleanup] limiter.py:26 — bare `except Exception: pass` on JWT decode. Behavior
  is intentional (fail-open to IP keying) but undocumented; add a one-line WHY
  comment per the code-style rule.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — engine dispose/recreate correct; get_session via async context manager (db.py:74-76); health-check psycopg/redis conns closed (main.py:117,129) |
| 2 Concurrency & scale | ok — pool sizing correct, no blocking call in async paths; 1 SEV2 (ContextVar assumes prefork, observability.py:189) |
| 3 Security & compliance | 1 SEV2 — /metrics unauthenticated (main.py:102); tokens via decrypt()/MultiFernet, no PII in logs, docs/CORS locked, fail-fast prod secrets all verified |
| 4 Clip-quality | n/a — infra module, no scoring |
| 5 Anthropic SDK | n/a — no LLM call in slice (model/tool versions only declared in config.py:35-36) |
| 6 Cleanliness & typing | 4 cleanups (typing gaps in limiter/auth, youtube internals coupling, silent except) |
| 7 Error handling / API | ok — main.py is app wiring, not a router; health returns safe statuses, no stack traces to client |
| 8 Config & paths | ok — pydantic-settings fail-fast, all new keys in .env.example; _STATIC absolute via Path(__file__).parent (main.py:76) |

## Module verdict
NEEDS-WORK — the Issue 58/75/75f hardening is correctly in place and verified; two
SEV2s remain (unauthenticated /metrics scrape surface, and the Celery ContextVar
correlation that is only safe under the prefork pool), plus minor typing cleanups.
No BLOCKER and no cross-tenant leak in this slice.
