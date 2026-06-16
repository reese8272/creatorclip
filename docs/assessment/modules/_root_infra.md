# _root_infra — assessed 2026-06-09

Slice: `db.py`, `crypto.py`, `config.py`, `auth.py`, `limiter.py`, `models.py`,
`main.py`. (Note: `api_key.py` was in the 2026-06-07 slice but is not in
today's; its two SEV2s are not re-assessed here.)

## Findings

- [SEV2] config.py:237 — every production fail-fast check (`Stripe secrets
  required`, `/metrics` auto-disable, `LOCAL_MEDIA_DIR` absolute) is gated on
  the free-string comparison `self.ENV == "production"`. `ENV: str =
  "development"` (config.py:152) accepts any value, so `ENV=prod`,
  `ENV=Production`, or a typo silently disables ALL prod validators and the
  app boots looking healthy with unauthenticated `/metrics` and unset Stripe
  secrets | fix: `ENV: Literal["development", "test", "staging", "production"]`
  so pydantic rejects anything else at boot; grep call sites for other ENV
  string compares (main.py:89 `docs_url` gate has the same hazard).

- [SEV2] config.py:232-268 — `_require_prod_secrets` covers Stripe but not the
  backend-conditional secrets: `STORAGE_BACKEND=r2` boots with all four `R2_*`
  empty (config.py:80-83), `TRANSCRIPTION_BACKEND=deepgram` (the default,
  config.py:58) boots with `DEEPGRAM_API_KEY=""` (config.py:72), and
  `VOYAGE_API_KEY=""` (config.py:45) despite embeddings being mandatory per
  architecture. Each surfaces only at the first job/render instead of at boot —
  exactly the gap the validator exists to close | fix: extend the validator:
  in production require `R2_*` when `STORAGE_BACKEND == "r2"`,
  `DEEPGRAM_API_KEY` when `TRANSCRIPTION_BACKEND == "deepgram"`
  (`ASSEMBLYAI_API_KEY` analog), and `VOYAGE_API_KEY` unconditionally.

- [SEV2] config.py:34 + auth.py:25 — `JWT_SECRET_KEY: str` has no minimum-
  length validation; an operator setting an 8-char secret gets a silently
  brute-forceable HS256 session signer (hashcat does short HS256 secrets in
  minutes). Fernet key strength is enforced structurally by the library;
  the JWT secret has no equivalent guard | fix: add a validator requiring
  `len(JWT_SECRET_KEY) >= 32` (256 bits, matching the HS256 key-size
  recommendation in RFC 7518 §3.2); fail fast at boot.

- [SEV2] limiter.py:80-83 + main.py:96 — slowapi 0.1.9 with `storage_uri=
  settings.REDIS_URL` uses the **sync** `limits` RedisStorage (limits 5.8.0
  installed); every rate-limit check on a decorated route is a blocking Redis
  round-trip executed inside `SlowAPIMiddleware`'s async dispatch — a sync
  socket call on the event loop per request (rubric §2). Sub-ms with a
  colocated Redis, but it head-of-line-blocks every in-flight request whenever
  Redis stalls (failover, SLOWLOG, network blip) (needs-runtime-confirmation
  for magnitude) | fix: near-term, set a socket timeout on the limiter storage
  URI (`?socket_timeout=0.1`) so a Redis stall degrades one request, not the
  loop; longer-term migrate to an async limiter (fastapi-limiter, or slowapi's
  async storage support when stable) — log a DECISIONS.md entry either way.

- [SEV2] main.py:155 — `headers = dict(response.headers)` in
  `StaticCacheBustMiddleware.dispatch` collapses duplicate header keys: a
  `text/html` response carrying two `Set-Cookie` headers would silently drop
  one. Latent today (cookie-setting responses in routers/auth.py are
  redirects/JSON, not text/html) but it is a correctness trap on a middleware
  that runs on every HTML response | fix: build the new response from
  `headers=response.headers.raw` (or copy via `MutableHeaders`) instead of
  `dict()`, preserving multi-value headers.

- [SEV2] crypto.py:13-29 — UNFIXED from 2026-06-07: `_fernet()` constructs a
  fresh `MultiFernet` (+1-2 `Fernet`) on every `encrypt()`/`decrypt()` call,
  including the per-request OAuth-token decrypt on the YouTube refresh path
  (rubric §1 module-level-singleton rule). Side effect: a malformed
  `TOKEN_ENCRYPTION_KEY` is not detected until the first crypto call instead
  of at boot | fix: `@functools.lru_cache(maxsize=1)` on `_fernet` and invoke
  it once at import for fail-fast; keep `_fernet.cache_clear()` available for
  `scripts/rotate_token_key.py`.

- [SEV2] db.py:80-116 — UNFIXED from 2026-06-07: `_recreate_in_progress` is a
  plain bool with a check-then-set race, and the loser of the race **returns
  immediately while the winner is mid-rebuild** — a second concurrent caller
  can proceed against a disposed engine. Safe in the documented single-
  threaded prefork-hook use, but nothing enforces that contract | fix:
  `threading.Lock()` around the rebuild (blocking acquire so the loser waits
  for the rebuilt engine rather than racing past), plus a docstring line
  restricting callers to fork hooks.

- [cleanup] limiter.py:30 — `SESSION_COOKIE = "cc_session"` duplicates
  auth.py:14 (DRY; the two can silently diverge and break per-creator
  bucketing) | fix: `from auth import SESSION_COOKIE` — no import cycle
  (auth.py does not import limiter).

- [cleanup] config.py:276-281 — `print(..., file=sys.stderr)` on startup
  config error; CLAUDE.md says logging-only. Defensible (pre-logging-config
  bootstrap) but undocumented, and the module already imports `logging`
  (config.py:1, used at :249) | fix: one-line comment justifying the print as
  pre-logging bootstrap, or `logging.basicConfig` + `logging.error`.

- [cleanup] db.py:132-133 — `_set_app_creator_id(session, transaction,
  connection)` untyped (CLAUDE.md mandates typed signatures) | fix: annotate
  `(session: Session, transaction: SessionTransaction, connection: Connection)`.

- [cleanup] db.py:167-169 — `get_session()` has no docstring stating the
  commit-by-caller contract; a router that forgets `await session.commit()`
  silently loses writes on `__aexit__` rollback. (Downgraded from the
  2026-06-07 SEV2: the fix is documentation; actual commit coverage is a
  routers-slice concern) | fix: add a docstring: "caller owns commit;
  uncommitted work is rolled back on exit".

- [cleanup] models.py:680-698 — `append_audit` is `async def` but awaits
  nothing (`session.add` is sync) | fix: drop `async` (mechanical caller
  diff), or comment why it stays.

- [cleanup] main.py:144 — `StaticCacheBustMiddleware.dispatch(self, request:
  Request, call_next)` — `call_next` param and return type unannotated | fix:
  `call_next: RequestResponseEndpoint) -> Response` from
  `starlette.middleware.base`.

- [cleanup] auth.py:42-43 — UNFIXED from 2026-06-07: the `except (...)` →
  `raise ... from None` drops the exception class from logs entirely;
  limiter.py:57 logs the class name for the identical decode failure | fix:
  `logger.info("session_decode_failed exc=%s", type(exc).__name__)` before the
  raise (class name only — PII-safe).

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | 2 findings (crypto per-call MultiFernet rebuild — carried over; recreate_engine race guard) — engines/sessionmakers/health-Redis are proper module singletons, lifespan closes youtube `_http`, worker progress client, and health Redis |
| 2 Concurrency & scale | 2 findings (slowapi sync Redis call on the event loop; latent Set-Cookie collapse in the buffer-everything HTML middleware) — pool math 15+5 app / 5+10 admin verified against DEPLOYMENT.md inequality (celery pools counted separately); `prepare_threshold=None` correct for PgBouncer txn mode; `pool_pre_ping` + `pool_recycle=1800` set; /health probes reuse pooled connections; creator-scoped indexes confirmed in alembic 0001 |
| 3 Security & compliance | 1 finding (no JWT_SECRET_KEY length floor) — tokens stored Fernet-encrypted with rotation via MultiFernet; `decrypt()` raises a safe `TokenDecryptError` (no ciphertext/key in message); RLS GUC injected via parameterized `set_config(..., true)`; limiter logs exception class only; `/metrics` bearer-gated with `secrets.compare_digest`; algorithms pinned to HS256 on both decode paths; no PII in any logger call in slice |
| 4 Clip-quality | n/a (infrastructure module) |
| 5 Anthropic SDK | n/a (no LLM calls in slice) |
| 6 Cleanliness & typing | 6 cleanup (SESSION_COOKIE duplication, bootstrap print, two untyped signatures, spuriously-async append_audit, silent auth decode failure) |
| 7 Error handling / API | ok — /health and /metrics return safe payloads; 401s carry no internal detail; rate-limit 429 via slowapi handler |
| 8 Config & paths | 2 findings (ENV free-string gate; backend-conditional prod secrets unchecked) — required-var fail-fast works, `.env.example` covers all slice settings incl. DATABASE_MIGRATION_URL / TOKEN_ENCRYPTION_KEY_PREVIOUS / METRICS_TOKEN / STATIC_VERSION; TRANSCRIPTION_TIMEOUT_S < CELERY_SOFT_TIME_LIMIT_S − 30 invariant enforced; LOCAL_MEDIA_DIR absolute-path check correctly scoped to STORAGE_BACKEND=local |

## Module verdict

NEEDS-WORK — no blockers or SEV1s and the infrastructure remains carefully
reasoned (pool math, RLS injection, PgBouncer prepared-statement handling all
verified), but 7 SEV2s stand: two carried over unfixed from 2026-06-07
(per-call MultiFernet, recreate_engine race) plus five new — most importantly
the `ENV == "production"` free-string gate, which lets a one-character env
typo silently switch off every production fail-fast safeguard at once.
