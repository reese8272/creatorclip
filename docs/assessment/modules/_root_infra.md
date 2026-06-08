# _root_infra — assessed 2026-06-07

Slice: `db.py`, `crypto.py`, `config.py`, `auth.py`, `limiter.py`, `models.py`,
`main.py`, `api_key.py`. HEAD `7af18b2`.

## Findings

- [SEV2] api_key.py:107 — `get_current_creator_via_api_key` does
  `select(CreatorApiKey…)` and then a separate `session.get(Creator, …)`
  on every bearer-authenticated request. Two round-trips on the request
  hot path for what is one row | fix: replace with a single
  `select(CreatorApiKey, Creator).join(Creator,
  Creator.id == CreatorApiKey.creator_id).where(...)` and unpack both rows,
  or load with `selectinload(CreatorApiKey.creator)`. Saves one round-trip
  per /clips/ingest call from OBS/folder-watcher (the highest-frequency
  bearer route).

- [SEV2] api_key.py:113 — every successful bearer call issues `UPDATE
  creator_api_keys SET last_used_at = now()` and `await session.commit()`
  on the request hot path. Under a folder-watcher uploading 10-100 clips
  in a minute this is 10-100 write transactions per minute per creator
  just to update a display field | fix: throttle the write -- e.g. only
  update when `now() - row.last_used_at > 60s`, or push to a redis-backed
  lazy flush. Cite axis-A in DEPLOYMENT (pool sizing) -- every avoided
  write frees a transaction in the PgBouncer math.

- [SEV2] crypto.py:13 — `_fernet()` constructs a fresh `MultiFernet`
  (and 1-2 `Fernet` objects) on EVERY encrypt/decrypt call, including
  the per-request OAuth-token decrypt on the YouTube refresh path.
  Fernet itself is cheap, but this is the "module-level singleton"
  rule from rubric section 1 being missed | fix: cache the `MultiFernet` at
  module import (or via `functools.lru_cache(maxsize=1)` on `_fernet`)
  and invalidate on a deliberate key-rotation hook. Keep the rebuild
  path callable so `scripts/rotate_token_key.py` can force a refresh.

- [SEV2] db.py:80 — `_recreate_in_progress` is a plain bool, no lock.
  Documented as a guard against "concurrent Celery prefork signals"
  but Celery prefork is single-threaded per fork so this is fine in
  the documented use; however nothing prevents an in-process caller
  from racing it. Today no caller does, so this is a latent fragility |
  fix: either add a `threading.Lock()` around the check+set, or add a
  comment narrowing the guarantee to "single-threaded fork hook only,
  do not call from request handlers" so a future maintainer doesn't
  reuse it elsewhere.

- [SEV2] config.py:262,267 — `print(..., file=sys.stderr)` on startup
  config error. The CLAUDE.md production-standard says "`logging`
  module only -- no `print()`". This is the one defensible exception
  (bootstrap before logging is configured), but the file already
  imports `logging` and uses it at 235 | fix: either switch to
  `logging.basicConfig(level=logging.ERROR); logging.error(...)` for
  consistency, or add a one-line comment justifying the print as
  "pre-logging-config bootstrap error" so the standard isn't silently
  violated.

- [SEV2] db.py:167 — `get_session()` yields the session but does NOT
  commit or rollback on exit. The `async with AsyncSessionLocal()`
  context manager will close, but on the success path the implicit
  transaction is rolled back, not committed, because SQLAlchemy 2.0
  async sessions don't auto-commit on `__aexit__`. Every router that
  writes therefore has to remember to call `await session.commit()`,
  which is the established pattern here -- but a router that forgets
  silently loses writes (the audit_log append, the api_key
  last_used_at update, etc. all rely on the router/dep committing).
  | fix: leave the dep as-is (commit-by-caller is idiomatic FastAPI)
  but add a one-line docstring on `get_session` stating the contract,
  so the gap is documented rather than implicit. (needs-runtime-
  confirmation that every writer actually commits -- that's a
  routers-slice job.)

- [cleanup] auth.py:42 — `from None` suppresses the underlying jwt
  exception in the re-raise. Fine for the response (we don't leak
  jwt internals), but the original exception class is also lost from
  logs. Limiter.py at limiter.py:57 logs the class name for the same
  case -- auth.py should mirror | fix: add
  `logger.info("session_decode_failed exc=%s", type(exc).__name__)`
  before the raise. Useful operationally; PII-safe (class name only).

- [cleanup] db.py:133 — `_set_app_creator_id` signature is untyped
  (`session, transaction, connection`). Mypy gate will let listener
  callbacks slide but CLAUDE.md mandates type hints on every signature
  | fix: annotate as
  `(session: Session, transaction: SessionTransaction, connection: Connection)`
  imported from `sqlalchemy.orm` / `sqlalchemy.engine`.

- [cleanup] models.py:655 — `append_audit` is `async def` but never
  awaits anything. `session.add(...)` is sync | fix: drop the `async`
  (callers already `await append_audit(...)` so removing means a
  caller diff). Or, defensible alternative: leave it for future-proofing
  and add a comment explaining the gap.

- [cleanup] main.py:62,67 — late imports of `youtube._http` and
  `worker.progress` inside `lifespan` for shutdown. Comment justifies
  avoiding circular import; works, but the imports also paper over a
  layering smell (main.py shouldn't know about worker internals
  directly). Acceptable today | fix: when worker/observability
  consolidates, move `aclose()` registration to a small
  `lifecycle.py` that main.py imports cleanly.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | 2 SEV2 (crypto MultiFernet rebuilt per call; api_key write per request) -- pool/engine/lifespan are clean |
| 2 Concurrency & scale | ok -- pool math matches DEPLOYMENT.md (15+5 <= 25), `prepare_threshold=None` correct for PgBouncer transaction mode, no hidden blocking calls in async paths, `pool_pre_ping=True`, `pool_recycle=1800` set; admin engine sized 5+10 |
| 3 Security & compliance | ok -- `decrypt()` only used via models contract; JWT decode in limiter has explicit `verify_exp=True` + 60s leeway with class-only logging; api_key constant-time-comparison reasoning defensible (192-bit entropy); RLS `set_config(..., is_local=true)` is parameterized; no PII in logged lines |
| 4 Clip-quality | n/a (infrastructure module) |
| 5 Anthropic SDK | n/a (no LLM calls in this slice) |
| 6 Cleanliness & typing | 3 cleanup -- `_set_app_creator_id` untyped, `append_audit` spuriously async, auth.py drops exception class from logs |
| 7 Error handling / API | n/a (no router endpoints in this slice; main.py only wires routers) |
| 8 Config & paths | ok -- `.env.example` has all 4 Issue-134 settings; production-only `LOCAL_MEDIA_DIR` absolute-path validator; METRICS fail-safe disable when token unset in prod; TRANSCRIPTION_TIMEOUT_S < CELERY_SOFT_TIME_LIMIT_S - 30 invariant enforced |

## Module verdict

**clean** -- no blockers, no SEV1. The infrastructure surface is one of
the most carefully-reasoned parts of the codebase: pool math is cited
to DEPLOYMENT.md, RLS context injection is documented end-to-end,
PgBouncer transaction-mode prepared-statement gotcha is handled, JWT
leeway / exp-verification has a DECISIONS.md trail, and METRICS auth
fails safe in production. The SEV2s are real defects on the request
hot path (per-call `MultiFernet` rebuild; per-call api_key write +
two-query lookup) that will compound under the 10k-creator scale
target but won't bite at single-user load. Cleanups are minor.
