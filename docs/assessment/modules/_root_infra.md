# _root_infra — assessed 2026-06-24

Slice: `auth.py`, `config.py`, `crypto.py`, `db.py`, `limiter.py`, `main.py`,
`models.py`, `api_key.py`, `event_log.py`, `observability.py`, `redact.py`.

Line numbers are current as of this date. Several SEV2/cleanup items below were
first raised 2026-06-09 and ticketed (Issues 82/109/228/229/233) but are **still
live in the code** — re-verified against current line numbers, not assumed fixed.

## Findings

- [SEV1] limiter.py:80-83 + main.py:123 — `Limiter(storage_uri=settings.REDIS_URL)`
  resolves to the **synchronous** `limits.storage.redis.RedisStorage` (confirmed:
  `storage_from_string('redis://…')` → sync class; slowapi 0.1.9
  `extension.__evaluate_limits` calls `self.limiter.hit(...)` with no
  `to_thread`/`run_in_threadpool` — grep count 0 in both middleware.py and
  extension.py). The check runs in the async dispatch path via `SlowAPIMiddleware`
  globally **and** on 69 `@limiter.limit(...)` async routes, so every limited
  request makes a **blocking Redis round-trip on the event-loop thread**. At the
  hundreds-of-concurrent-users target this serializes request handling on Redis
  latency and head-of-line-blocks the whole loop whenever Redis stalls (failover /
  SLOWLOG / blip). One *systemic* finding, not 69. | fix: near-term, append a
  socket timeout to the limiter storage URI (`redis://…?socket_timeout=0.1`) so a
  Redis stall degrades one request not the loop; proper fix is the async storage
  path — construct the Limiter with `async+redis://…` (slowapi 0.1.9 has the
  async-strategy refs; `limits.aio.storage.RedisStorage` is present) and confirm
  the moving-window hit is awaited (needs-runtime-confirmation). Record the choice
  in `docs/DECISIONS.md` (tracked: Issue 82).

- [SEV2] config.py:263 — every production fail-fast in `_require_prod_secrets`
  (Stripe-secrets-required, `/metrics` auto-disable, absolute `LOCAL_MEDIA_DIR`)
  is gated on the free-string compare `self.ENV == "production"`, but
  `ENV: str = "development"` accepts ANY value. `ENV=prod`, `ENV=Production`, or a
  typo silently switches off **all** prod safeguards at once — the app boots
  "healthy" with unauthenticated `/metrics` and unset Stripe secrets. Same hazard
  at main.py:116 (`docs_url` exposes `/docs`) and the several `ENV == "production"`
  middleware gates (main.py:296). Highest-leverage defect in the slice. | fix:
  `ENV: Literal["development", "test", "staging", "production"]` so pydantic
  rejects anything else at boot (tracked: Issue 228).

- [SEV2] config.py:48 — `JWT_SECRET_KEY: str` has no minimum-length validation;
  an 8-char secret yields a brute-forceable HS256 session signer (hashcat cracks
  short HS256 secrets in minutes). Fernet key strength is enforced by the library;
  the JWT secret has no equivalent guard. | fix: add a `model_validator` requiring
  `len(JWT_SECRET_KEY) >= 32` (256-bit, per RFC 7518 §3.2); fail fast at boot
  (tracked: Issue 229).

- [SEV2] crypto.py:13-29 — `_fernet()` builds a fresh `MultiFernet` (+1-2 `Fernet`)
  on **every** `encrypt()`/`decrypt()` call, including the per-request OAuth-token
  decrypt on the YouTube refresh path (violates the module-singleton rule, rubric
  §1). Side effect: a malformed `TOKEN_ENCRYPTION_KEY` is not detected until the
  first crypto call instead of at boot. | fix: `@functools.lru_cache(maxsize=1)`
  on `_fernet` and call it once at import for fail-fast; expose
  `_fernet.cache_clear()` for `scripts/rotate_token_key.py` (tracked: Issue 233).

- [SEV2] db.py:87-123 — `recreate_engine` guards re-entry with a plain bool
  (`_recreate_in_progress`), a check-then-set race where the **loser returns
  immediately while the winner is mid-rebuild** — a second concurrent caller can
  proceed against a just-`dispose()`d engine. Safe in the documented single-
  threaded prefork-hook use, but nothing enforces that contract. | fix:
  `threading.Lock()` with a blocking acquire around the rebuild so the loser waits
  for the rebuilt engine rather than racing past; add a docstring restricting
  callers to fork hooks (tracked: Issue 82).

- [SEV2] main.py:233 — `headers = dict(response.headers)` in
  `StaticCacheBustMiddleware.dispatch` collapses duplicate header keys: a
  `text/html` response carrying two `Set-Cookie` headers silently drops one.
  Latent today (cookie-setting responses are redirects/JSON, not text/html) but a
  correctness trap on a middleware that buffers and rebuilds every HTML response. |
  fix: build the new response from `response.headers.raw` (or copy via
  `MutableHeaders`) to preserve multi-value headers (tracked: Issue 109).

- [SEV2] main.py:334-359 — `_log_request_events` `await`s
  `event_log.record_event(...)` on **every** non-skipped request, which opens a
  transaction and `COMMIT`s a row on the logs engine inline (event_log.py:108-125,
  pool_size=5/overflow=10). This adds a full DB write-transaction + logs-pool
  checkout to the request hot path at every RPS; the code comment itself names "a
  high-throughput async queue is the documented scale path." | fix: enqueue
  telemetry to an in-process `asyncio.Queue` drained by a background task
  (fire-and-forget), keeping the best-effort swallow; or split event_logs onto a
  separate DB and batch inserts.

- [SEV2] api_key.py:113-114 — `get_current_creator_via_api_key` issues
  `UPDATE creator_api_keys SET last_used_at = now()` + `COMMIT` on **every**
  bearer-authenticated request. The OBS companion / folder-watcher client is
  chatty (per-clip `/clips/ingest`), so this is per-request write amplification +
  single-row contention on one key row. | fix: throttle the touch — update only
  when `last_used_at IS NULL OR last_used_at < now() - interval '5 minutes'`, so
  steady-state auth is read-only.

- [SEV2] observability.py:397-417 + redact.py:46-53 — `scrub_dict` (the
  formatter / Sentry / event-log "structural backstop") is **shallow**: it masks
  only sensitive *top-level* keys. A token nested under a non-sensitive key
  (`extra={"context": {"access_token": "…"}}`) is NOT redacted, and Sentry
  `breadcrumbs`/`exception` values aren't scrubbed by `_sentry_before_send` at
  all. With `send_default_pii=False` + SDK integrations this is defense-in-depth,
  not a proven leak, but it is a gap in the claimed backstop. | fix: make
  `scrub_dict` recurse into nested dicts/lists (bounded depth ≤4); add a Sentry
  `event_scrubber`/denylist; unit-test that a nested `access_token` is masked
  (tracked: Issue 233).

- [cleanup] limiter.py:30 — `SESSION_COOKIE = "cc_session"` duplicates auth.py:59
  (DRY; can silently diverge and break per-creator bucketing). | fix:
  `from auth import SESSION_COOKIE` (no import cycle — auth.py does not import
  limiter) (tracked: Issue 109).

- [cleanup] db.py:140 — `_set_app_creator_id(session, transaction, connection)`
  untyped (CLAUDE.md mandates typed signatures). | fix: annotate
  `(session: Session, transaction: SessionTransaction, connection: Connection)`
  (tracked: Issue 109).

- [cleanup] auth.py:123-124 — the `except (...)` → `raise … from None` drops the
  exception class from logs entirely; limiter.py:57 logs the class name for the
  identical decode failure. | fix: `logger.info("session_decode_failed exc=%s",
  type(exc).__name__)` before the raise (class name only — PII-safe).

- [cleanup] event_log.py:47-49 — `_is_sensitive` is a one-line passthrough around
  `redact.is_sensitive` with no added behavior (DRY/KISS). | fix: call
  `is_sensitive` directly at event_log.py:58 and delete the wrapper.

- [cleanup] config.py:485,490 — `print(..., file=sys.stderr)` on the startup
  config-error path. Defensible (runs before `configure_logging`) and ends in
  `sys.exit(1)`, so not a real "no print()" violation; flagging only so it isn't
  re-raised. | fix: none required; optional one-line "pre-logging bootstrap"
  comment.

## Verified OK (load-bearing checks that passed)

- **Token handling → decrypt()**: `crypto.decrypt` uses `MultiFernet`
  (primary+previous) and raises a *safe* `TokenDecryptError` (no ciphertext/key in
  the message). `models.py` declares the encrypted columns with an explicit
  "always use crypto.encrypt()/decrypt()" contract; no raw-token access in slice.
- **No PII/token in any log line**: grepped every `logger.*` in all 11 files —
  none log token/email/secret/cookie/scope/raw_key/ciphertext. limiter.py:57 logs
  the JWT exception **class name only**; auth.py logs nothing sensitive.
- **Per-creator isolation**: this slice is schema (`models.py`) + infra. Queries
  exist only in auth.py (lookup `creators` by id — RLS-exempt bootstrap, correct),
  api_key.py (lookup by globally-unique `key_hash` on `creator_api_keys`, a
  non-RLS table — no tenant column to leak), and event_log.py (`event_logs`,
  intentionally no RLS; purge filters `creator_id`). DB-level RLS is injected by
  db.py's `after_begin` listener (`SELECT set_config('app.creator_id', :cid,
  true)`) against the 12 tenant tables in migration 0010 (+ chat 0026 +
  notifications). Unset GUC ⇒ `current_setting(...,true)` NULL ⇒ `creator_id =
  NULL` ⇒ 0 rows (fail-closed). No missing `WHERE creator_id` in this slice.
- **api_key dependency ordering**: the dependency COMMIT (api_key.py:114) ends the
  txn and wipes the `SET LOCAL` GUC, then `session.info["creator_id"]` is set
  (line 116) so the route's *next* transaction re-injects it. The `last_used_at`
  UPDATE hits a non-RLS table, so the pre-info commit is correct (not silently
  filtered). No isolation defect.
- **Parameterized SQL**: db.py uses `text(... :cid ...)` bound params; no
  f-string/`%`-built SQL anywhere in the slice.
- **Async singletons / lifecycle**: `engine`/`admin_engine` + sessionmakers are
  module-level singletons; `dispose_engine` closes both pools; `_health_redis` is
  a lifespan singleton reused by `/health` and the metrics saturation scrape;
  `get_session` yields inside `async with AsyncSessionLocal()` so close/rollback
  is guaranteed on every path. Pool math (15+5 app / 2+2 admin / 5+10 logs)
  documented against the PgBouncer budget; `prepare_threshold=None` correct for
  PgBouncer txn-pooling; `pool_pre_ping` + `pool_recycle=1800` set.
- **No blocking call in the db/auth/health async paths**: `_check_postgres`/
  `_check_redis` use `asyncio.timeout` + async drivers; event_log engine is
  created lazily precisely to bind the correct loop in a Celery worker. The only
  loop-blocking call is the slowapi sync storage (SEV1 above).
- **Config fail-fast & .env.example**: pydantic-settings with required fields +
  `model_validator`s (prod secrets, metrics-token fail-safe, transcription-timeout
  invariant `TRANSCRIPTION_TIMEOUT_S < CELERY_SOFT_TIME_LIMIT_S − 30`, absolute
  LOCAL_MEDIA_DIR in prod). Every sampled newer key (SENTRY_DSN, RESEND_API_KEY,
  NOTIFY_BACKEND, REDBEAT_REDIS_URL, METRICS_TOKEN, CSRF/CSP, STRIPE_TAX, …) is in
  `.env.example` with a description.
- **Paths**: `_STATIC`/`_SPA_DIST` use `Path(__file__).parent`; the SPA catch-all
  confines candidates to `_SPA_DIST` via `is_relative_to` (traversal-blocked).
- **/metrics hardening**: bearer-gated with `secrets.compare_digest`; in
  production an empty `METRICS_TOKEN` fail-SAFE disables the endpoint.
- **No virality promise**: app description carries the honesty line; no virality
  string anywhere in the slice.
- **No Anthropic SDK calls in slice**: `observability.record_llm_tokens` is the
  metrics sink LLM call sites feed — low-cardinality labels, creator_id
  deliberately excluded to avoid 10k-creator cardinality blowup.
- **No TODO/FIXME/debug/breakpoint** in the slice (only the two startup prints).

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | 2 findings — crypto per-call MultiFernet rebuild; recreate_engine bool race. Singletons/get_session-close/lazy logs engine/lifespan disposal all correct. |
| 2 Concurrency & scale | 1 SEV1 (sync slowapi Redis storage on the loop) + 2 SEV2 (per-request telemetry commit; per-request last_used_at write) + 1 SEV2 latent (Set-Cookie collapse). Pool math verified. |
| 3 Security & compliance | 2 findings — ENV free-string gate (disables prod fail-fasts); no JWT_SECRET_KEY length floor; + 1 SEV2 shallow redaction backstop. decrypt() safe, no PII/token in logs, RLS fail-closed, parameterized SQL, honesty line present. |
| 4 Clip-quality | n/a (infrastructure module) |
| 5 Anthropic SDK | n/a (no LLM call here; metrics sink only) |
| 6 Cleanliness & typing | fully typed except db.py:140; 4 cleanup (SESSION_COOKIE dup, untyped listener, silent auth decode, redundant `_is_sensitive`, startup print). |
| 7 Error handling / API surface | ok — /health and /metrics return safe payloads; 401/403/404 carry no internal detail; rate-limit 429 via slowapi handler. (Request/response Pydantic models live in routers — out of slice.) |
| 8 Config & paths | 1 finding (ENV gate, also under §3) — required-var fail-fast works, paths absolute where load-bearing, .env.example complete. |

## Module verdict
NEEDS-WORK — no cross-tenant leak and no BLOCKER (RLS is fail-closed, token
handling is safe), but one systemic SEV1 (synchronous slowapi Redis storage
blocking the event loop on every rate-limited request) plus the `ENV ==
"production"` free-string gate, whose single-char typo silently disables every
production fail-fast safeguard at once.
