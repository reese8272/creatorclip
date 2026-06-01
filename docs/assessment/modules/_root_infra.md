# _root_infra — assessed 2026-05-31 (Wave 9 + Issue 112 re-verification)

## Findings

### Issue 112 Fix Verification — `/health` Connection Pooling

The Issue 112 refactor (db.py pool sizing + Postgres/Redis health probes) is **correct and complete**:

- **main.py:146-158** — `_check_postgres()` routes via `engine.connect()` instead of opening a fresh `psycopg.AsyncConnection`. Stays inside the pre-warmed SQLAlchemy pool (15+5 ceiling under the 25-conn PgBouncer sidecar). Wrapped in `asyncio.timeout(2.0)`.
- **main.py:40-43, 161-172** — `_health_redis` module-level singleton initialized once in lifespan (line 50: `aioredis.from_url(...)`), reused on every `_check_redis()` call. No per-probe connection churn. Properly closed at shutdown (line 68).
- **db.py:33** — `prepare_threshold=None` applied to BOTH engines via `_CONNECT_ARGS`. PgBouncer prepared-statement safety preserved.
- **db.py:39-40** — Pool ceiling: `pool_size=15 + max_overflow=5 = 20` stays under the 25-conn PgBouncer sidecar (documented).
- **tests/test_health.py:4-15** — Regression test asserts `psycopg` not imported at module scope (structural proof the fix holds).
- **tests/test_health.py:18-26** — Regression test asserts `_health_redis` is initialized (no per-probe None-check crash).

**Status: CLEAN — Issue 112 fix is production-ready.**

---

## Remaining Findings from Wave-9 Assessment (Still Present)

### [SEV2] db.py:80-103 — `recreate_engine()` Re-entry Guard

Function is module-public with no guard against multiple calls with in-flight sessions. Documented as "after fork only" but nothing stops accidental re-entry.

**Fix**: Add `_recreate_engine_called: bool = False` module-level flag; raise `RuntimeError("recreate_engine() already called — must not be called with in-flight sessions")` on re-entry. Document the precondition in the docstring.

---

### [SEV2] auth.py:46-58 / api_key.py — Bootstrap Query Before GUC

Bootstrap Creator/ApiKey lookup runs BEFORE `session.info["creator_id"]` is set, so no `app.creator_id` GUC is emitted on the bootstrap transaction. Intentional today (creators/creator_api_keys are RLS-exempt, Issue 56), but this is a by-convention invariant: a future migration flipping either table under RLS would silently lose those rows and break ALL authentication with 401-everywhere outage.

**Fix**: Add a CI test enumerating RLS-exempt tables from `pg_policies` system catalog and asserting `creators` and `creator_api_keys` remain exempt. OR: refactor bootstrap to use `AdminSessionLocal` (BYPASSRLS) for the lookup only, then hand the Creator/ApiKey to the request.

---

### [SEV2] config.py:228-237 — `print()` on Fatal Startup

Fatal config error calls `print(..., file=sys.stderr)`. Correct in spirit (logger not yet configured at import time) but CLAUDE.md production standard mandates "logging module only — no print()". Container log aggregators parsing JSON lines (LOG_JSON=True) miss this fatal startup message entirely.

**Fix**: Call `logging.basicConfig(stream=sys.stderr, level=logging.ERROR, format="[CreatorClip] %(message)s")` in the `except ValidationError` block, then emit `logging.error(...)`. Same user-visible output, single log path aggregators can ingest.

---

### [SEV2] observability.py:43-44, 224-241 — Celery ContextVar Correlation (Pool-Specific)

Module-level ContextVars (`request_id_ctx`, `_task_start_ctx`) documented as "safe because each worker runs one task at a time" — true ONLY under `--pool=prefork`. Any future migration to gevent/eventlet/threads would cause concurrent tasks in one process to overwrite each other's correlation id and start time → mislabelled durations and broken log correlation.

**Fix**: Assert `app.conf.worker_pool == "prefork"` at worker startup and refuse to boot otherwise. OR: key task start off `task.request.id` in a per-task dict guarded by `task_postrun` cleanup.

---

### [SEV2] api_key.py:113-114 — Write Amplification on `last_used_at`

Every API-key-authenticated request (e.g., OBS companion app uploading every few seconds) issues `UPDATE creator_api_keys SET last_used_at = now()` inside the auth dependency BEFORE the handler runs. No rate-limiting on the update; high-frequency clients cause per-request writes and fsync thrashing on a hot row.

**Fix**: Coarse-grain `last_used_at` — only UPDATE if `last_used_at IS NULL OR last_used_at < now() - interval '60 seconds'`. Skip the commit when no row changed. Keeps management-UI freshness signal without per-request write amplification.

---

### [cleanup] db.py:120 — Missing Type Hints on `_set_app_creator_id`

Event listener callback is missing type hints on parameters: `def _set_app_creator_id(session, transaction, connection):`

**Fix**: Add type hints: `def _set_app_creator_id(session: Session, transaction, connection: Connection) -> None:`. (Note: `transaction` type is internal SQLAlchemy; could be left untyped if that's opaque, but `Session` and `Connection` must be explicit per CLAUDE.md rubric 6.)

---

### [cleanup] crypto.py:13-24 — MultiFernet Not Cached

`_fernet()` constructs a fresh `MultiFernet` on EVERY encrypt/decrypt call (cheap but non-zero HMAC+AES context init). Token-refresh-heavy endpoints pay repeatedly.

**Fix**: Add `@functools.lru_cache(maxsize=1)` keyed on `(settings.TOKEN_ENCRYPTION_KEY, settings.TOKEN_ENCRYPTION_KEY_PREVIOUS)`. Cache busts on key rotation; steady state hits the cache.

---

### [cleanup] main.py:43-52, 57-65 — Lifespan Coupling to Private Module Internals

Lifespan reaches into TWO modules' private internals via function-local imports (`youtube._http`, `worker.progress`). Each new shared async resource adds another coupling block.

**Fix**: Define a `shared_resources.register_aclose(coro_fn)` registry that modules call at import time; lifespan iterates the registry and awaits each. Decouples and makes shutdown order inspectable.

---

### [cleanup] main.py:130-132 Removed (Issue 112)

Prior assessment flagged a `_pg_dsn()` function here, but that function no longer exists (was removed during the Issue 112 refactor). The old SQLAlchemy.create_async_engine path is gone. **CLEAN.**

---

## Rubric Coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | SEV2 on `recreate_engine()` re-entry guard; core pool/session/lifespan plumbing + Issue 112 fix all correct |
| 2 Concurrency & scale | SEV2 on observability ContextVar pool-specificity; `/health` connection churn FIXED (Issue 112); pool math + PgBouncer + RLS GUC all correct; scale-checklist A ✅ by inspection |
| 3 Security & compliance | SEV2 on bootstrap-query-before-GUC by-convention invariant; parameterized RLS + quota-leak vector (Issue 106) + API-key threat model all correct; scale-checklist D ✅ structural, I ✅ by inspection |
| 4 Clip-quality | n/a (infra) |
| 5 Anthropic SDK | n/a (no LLM call in slice) |
| 6 Cleanliness & typing | cleanup on `_set_app_creator_id` type hints, MultiFernet caching, lifespan coupling; Issue 108 typing sweep complete (no `Optional[` in slice) |
| 7 Error handling / API | ok — safe error details, no stack traces |
| 8 Config & paths | cleanup on config.py `print()` fallback; all required vars in .env.example |

## Module Verdict

**NEEDS-WORK** — Issue 112 `/health` fix is production-ready. Remaining hot-list: (1) bootstrap-query-before-GUC by-convention invariant (needs CI test to prevent regress); (2) recreate_engine() re-entry guard (structural guard missing); (3) observability ContextVar pool-scoping (silent break risk under pool change); (4) config print() single log path; (5) api_key write-amplification on high-frequency clients. No BLOCKER; no cross-tenant leak (Postgres RLS is structural). Issue 112 closed the highest-impact SEV2 (axis E connection churn under load).
