# _root_infra — assessed 2026-06-01

## Findings

- **SEV1** models.py:724–757 — `CreatorInsight` model missing composite `(creator_id, video_id)` index | fix: Add `__table_args__ = (sa.Index("ix_creator_insight_creator_video", "creator_id", "video_id"),)` to `CreatorInsight` class; add migration `0020_creator_insight_index`
- **SEV1** db.py:80–103 — `recreate_engine()` public with no re-entry guard; concurrent Celery prefork calls could race and corrupt module globals | fix: Add `_engine_recreating: bool = False` flag + guard before reassigning globals, or rename to `_recreate_engine` (underscore prefix signals internal-only)
- **SEV2** api_key.py:113–114 — `UPDATE creator_api_keys SET last_used_at = now()` on **every** API-key request (OBS uploader frequency) causes write amplification at scale | fix: Skip UPDATE when `last_used_at IS NOT NULL AND last_used_at > now() - interval '60 seconds'`
- **cleanup** db.py:120 — `_set_app_creator_id` listener function missing type hints on `session`, `transaction`, `connection` parameters | fix: Add type hints from SQLAlchemy event types (Session, Transaction, AsyncConnection)
- **cleanup** config.py:238–243 — Startup validation errors printed to stderr via `print()` instead of logger; JSON log aggregators miss fatal configuration errors | fix: Replace with `logging.getLogger(__name__).critical(...)` before `sys.exit(1)`
- **CLEAN** observability.py:164–175 — `configure_logging` idempotently removes handlers before re-adding; `mkdir(parents=True, exist_ok=True)` guards against permission errors gracefully; `RotatingFileHandler` added only once per handler list | confirmed compliant with Issue 122 requirements

## Rubric coverage

| Category | Status |
|---|---|
| Resource lifecycle | ✅ CLEAN — DB sessions via context manager + guaranteed close; module singletons (engine, admin_engine, limiter, _health_redis) initialized in lifespan; all external clients properly disposed |
| Concurrency & scale | ⚠️ SEV1 — `recreate_engine()` race present; pool config correct (15+5=20 ≤ 25 PgBouncer); no blocking in async code verified |
| Security & compliance | ✅ CLEAN — OAuth tokens always via `decrypt()` never logged; no PII in logs; JWT validation with 60s leeway RFC-compliant; MultiFernet key rotation correct |
| Clip-quality | N/A |
| Anthropic SDK | ✅ CLEAN — Not used in _root_infra; defer to routers module assessment |
| Code cleanliness & typing | ⚠️ CLEANUP — `_set_app_creator_id` untyped; stderr `print()` on fatal startup; one `type: ignore` in main.py justified (Issue 78c) |
| Error handling | ✅ CLEAN — Config fail-fast with clear error messages; startup validators properly gate missing secrets in production |
| Config & paths | ✅ CLEAN — All paths absolute in production (validated); `.env.example` maintained; all new config gated by `settings` singleton |

## Module verdict

**NEEDS-WORK** — One SEV1 race condition in `recreate_engine()` must be fixed before Celery workers deploy; CreatorInsight missing index will degrade under load; api_key write amplification on OBS uploads needs backoff.

