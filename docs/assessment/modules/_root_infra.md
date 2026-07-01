# _root_infra — assessed 2026-07-01

Slice: `auth.py`, `config.py`, `crypto.py`, `db.py`, `limiter.py`, `main.py`,
`models.py`, `api_key.py`, `event_log.py`, `observability.py`, `redact.py`, `verbose.py`.

All load-bearing claims below were verified against current official docs (cited inline)
per the "documentation-not-memory" constraint. Package versions read from
`requirements.txt`: `slowapi==0.1.9`, `sqlalchemy[asyncio]==2.0.36`,
`psycopg[binary,pool]==3.2.3`, `redis[hiredis]==5.2.0`, `cryptography==48.0.1`,
`PyJWT==2.13.0`.

## Findings

- [SEV1] event_log.py:70-78 — the dedicated event-log engine opens its own pool
  (`pool_size=5, max_overflow=10` = **15 connections per API replica**) to
  `settings.logs_database_url`, which **defaults to `DATABASE_URL`** (config.py:63-65),
  i.e. the SAME Postgres/PgBouncer as the primary engine. The documented per-pod
  connection budget in db.py:35-38 only accounts for `engine` (15+5=20) and
  `admin_engine`; this second 15-connection pool is **absent from the inequality**.
  It is not background load: main.py:374 `await event_log.record_event(...)` runs on
  **every** non-skipped request, so the event-log pool takes request-rate concurrency.
  This is exactly scale-checklist axis-A (`QueuePool limit`/PgBouncer exhaustion) hiding
  in an uncounted engine. | fix: pin the event-log pool small — telemetry is best-effort,
  one short INSERT/commit — e.g. `pool_size=2, max_overflow=3`, and add it to the
  `total_db_connections` inequality in docs/DEPLOYMENT.md. (needs-load-confirmation of
  the total against the 25-conn PgBouncer sidecar sizing).

- [SEV2] config.py:391 — `ENV: str = "development"` is a free string, yet every
  production security boundary branches on the exact literal `self.ENV == "production"`:
  prod-secret fail-fast (config.py:719), `/docs` disable (main.py:127), HSTS header
  (main.py:322), R2-required + metrics-token gate (config.py:730/758), and the
  verbose-content-logging prod guard (config.py:510). A deploy-time typo (`prod`,
  `Production`, trailing space) silently runs a PRODUCTION container in dev-mode
  hardening — no prod-secret enforcement, `/docs` exposed, no HSTS, and verbose raw
  content logging enabled by `VERBOSE_LOGGING` alone. | fix: constrain to
  `ENV: Literal["development","staging","production"] = "development"`. pydantic-settings
  coerces string Literals from env correctly (the known Literal-from-env gap is only for
  non-string literals like `Literal[False]` — pydantic/pydantic-settings#435), so a typo
  fails fast at boot.

- [SEV2] config.py:83 — `JWT_SECRET_KEY: str` has no minimum-length validation. HS256
  requires a key at least as long as the hash output (256 bits / 32 bytes) per RFC 7518
  §3.2, and weak HS256 secrets are practically brute-forceable
  (auth0.com/blog/brute-forcing-hs256-is-possible). PyJWT 2.13.0 does **not** enforce
  this on `encode()` by default. A short/guessable secret lets an attacker forge session
  cookies (auth.create_session_token) for any `creator_id` → full cross-tenant
  impersonation. | fix: add a `field_validator("JWT_SECRET_KEY")` asserting
  `len(v.encode()) >= 32`; fail fast at boot.

- [SEV2] config.py:82,91 + crypto.py:13-24 — `TOKEN_ENCRYPTION_KEY` (and
  `_PREVIOUS` during rotation) format is never validated at startup. `_fernet()` builds
  `Fernet(key.encode())` lazily on the first `encrypt()`/`decrypt()`, and Fernet raises
  `ValueError: Fernet key must be 32 url-safe base64-encoded bytes` only at that first
  call — i.e. a malformed key surfaces as a 500 on a creator's OAuth callback in
  production instead of a boot failure, and a bad `_PREVIOUS` key silently breaks a
  live rotation window. | fix: validate both in a `field_validator` by attempting
  `Fernet(v.encode())` at load (fail-fast, CLAUDE.md Production Standards). (Verified:
  cryptography 48 Fernet requires exactly 32 url-safe base64 bytes.)

- [SEV2] requirements.txt — `slowapi==0.1.9` is pinned but its transitive dependency
  **`limits` is not pinned at all**. The entire Issue-312 event-loop mitigation
  (limiter.py:83-86, 129-133) depends on two `limits`-internal behaviours: (a)
  `storage_options` being forwarded verbatim to `redis.from_url()`
  (confirmed in limits 5.x docs, limits.readthedocs.io/en/stable/storage.html), and
  (b) sync `RedisStorage.hit()` returning a bool. `limits` restructured storage and
  added the async `limits.aio` path across the 3.x→5.x line; a silent bump could change
  the passthrough or `hit()`'s truthiness and **silently disable rate limiting**.
  CLAUDE.md mandates `==` pins. | fix: pin `limits==<resolved-version>` explicitly and
  add a smoke test asserting `limiter._storage` is a sync RedisStorage and
  `pool.connection_kwargs["socket_timeout"] == 0.1`.

- [SEV2] observability.py:516-518 — HTTP latency metric labels by route template
  (good, bounded) but falls back to the **raw path** when `scope.get("route")` is None
  — which is exactly the case for every **unmatched (404) request**. A scanner or broken
  client hitting `/random/<uuid>` paths mints a new `http_request_duration_seconds`
  time-series per unique path → unbounded Prometheus cardinality / memory growth
  (the failure this line was trying to avoid, but it leaks on the 404 path). | fix: when
  `route` is None, use a constant label such as `"__unmatched__"` instead of
  `scope["path"]`.

- [SEV2] limiter.py:129-133 (residual, already mitigated — noted, not re-opened) —
  Verified against slowapi v0.1.9 `extension.py`: `_check_request_limit` is a plain
  `def` invoked in middleware and calls `self.limiter.hit(lim.limit, *args, cost=cost)`
  **synchronously, no await** (~line 468). With the sync `RedisStorage` the Redis
  round-trip therefore executes on the event-loop thread for every rate-limited request.
  The shipped interim fix (bounded `socket_timeout=0.1`, `socket_connect_timeout=0.25`)
  correctly caps worst-case blocking at ~100 ms/request rather than eliminating it, and
  the async-upgrade trigger is documented. Acceptable for the ≤100-user beta; remains a
  per-request serialized Redis hop on the loop and a throughput ceiling. | fix: none for
  beta; execute the documented `async+redis://` + `limits.aio` switch when slowapi ships
  a version that awaits `hit()`. (needs-load-evidence for the ceiling.)

- [SEV2] main.py:360-385 — `_log_request_events` `await`s a full DB INSERT+COMMIT
  (event_log.record_event) inline before returning the response on every non-static
  request, coupling every request's p99 latency to the telemetry DB's health. The module
  docstring itself names "a high-throughput async queue" as the scale path and the code
  comment states it is awaited deliberately for read-after-write on `/api/logs/me`.
  Combined with the SEV1 pool finding, a slow logs DB both adds latency to and can starve
  connections from every request. | fix: if the read-after-write guarantee can be
  relaxed, dispatch via `asyncio.create_task(...)` (fire-and-forget, already best-effort);
  otherwise keep the await but MUST shrink the event-log pool (SEV1) so it cannot exhaust
  the shared bouncer. (needs-load-evidence.)

- [cleanup] config.py:477 — `log_level_int` does `import logging as _logging` inside the
  property though `logging` is already imported at module top (config.py:1). | fix: drop
  the local import and use the module-level `logging`.

- [cleanup] config.py:787,792 — `print(..., file=sys.stderr)` on the boot config-error
  path. This is the one defensible `print` (logging isn't configured yet and the process
  is about to `sys.exit(1)`), but CLAUDE.md's "no print()" rule technically flags it.
  | fix: leave as-is; it is the correct pre-logging bootstrap channel — noted for
  completeness only.

## Verified-correct (no finding)

- **Pool math / prepared statements (db.py):** `prepare_threshold=None` in `connect_args`
  is the documented psycopg3 way to disable server-side prepared statements under
  PgBouncer transaction pooling (psycopg.org/psycopg3/docs/advanced/prepare.html). The
  scale-checklist's asyncpg `statement_cache_size=0` note is N/A here — this stack uses
  `postgresql+psycopg` (psycopg3), and the psycopg3 equivalent is correctly applied.
  `pool_pre_ping=True` + `pool_recycle=1800` present on both engines.
- **Per-tenant isolation (auth.py / api_key.py / db.py):** the `after_begin` listener
  emits `SELECT set_config('app.creator_id', :cid, true)` (parameterized — correct, since
  `SET LOCAL` rejects binds) for RLS. Confirmed against migration 0010 `_TENANT_TABLES`:
  `creators`, `creator_api_keys`, and `event_logs` are **deliberately** not RLS-gated, so
  the auth-bootstrap `Creator` lookup (auth.py:141) and the API-key `key_hash` lookup
  (api_key.py:96, cross-tenant by design — the key IS the credential) resolve correctly
  before any GUC is set. No missing `WHERE creator_id` in the slice.
- **Fernet MultiFernet rotation (crypto.py):** `_fernet()` builds
  `MultiFernet([primary, previous])` so decrypt tries the primary then the previous key —
  correct zero-downtime rotation semantics. Rebuilding per call is cheap (base64 parse);
  not worth caching.
- **Log/PII hygiene:** redact.py blocklist is broad; JsonLogFormatter and
  `_sentry_before_send` apply `scrub_dict` as a structural backstop; verbose raw-content
  sink is triple-gated and prod-locked. limiter.py logs only the exception class. No token
  or PII in any `logger.*`/`log_event` call in the slice.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | 1 SEV1 (uncounted event-log pool) + 1 SEV2 (inline await) |
| 2 Concurrency & scale | 2 SEV2 (slowapi sync hop residual, request-path DB write) |
| 3 Security & compliance | 3 SEV2 (ENV gate, JWT min-len, TOKEN key not boot-validated); isolation verified clean |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | n/a (no LLM calls; verbose/observability only shape usage dicts) |
| 6 Cleanliness & typing | 2 cleanup |
| 7 Error handling / API | ok (main.py health/metrics status codes correct; no stack traces to client) |
| 8 Config & paths | 1 SEV2 (limits unpinned) + config validators otherwise strong; paths absolute |

## Module verdict
NEEDS-WORK — no cross-tenant leak or open BLOCKER, but an uncounted 15-connection
event-log pool taking request-rate load (SEV1, scale axis A) plus three fail-fast config
gaps (ENV free-string, JWT/Fernet key validation) and an unpinned `limits` dependency
should be closed before a load-tested launch.
