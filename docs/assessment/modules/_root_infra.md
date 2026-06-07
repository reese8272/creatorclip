# _root_infra — assessed 2026-06-07

## Findings

- **SEV1** api_key.py:113–114 — Every API-key auth (bearer token on `/clips/ingest` from OBS uploader) unconditionally UPDATEs `last_used_at = now()` | fix: Add conditional check `if row.last_used_at is None or row.last_used_at < datetime.now(UTC) - timedelta(minutes=1):` before commit; avoids write amplification at scale (OBS sends uploads every few seconds)
- **SEV2** models.py:735–753 — `CreatorInsight` has separate `index=True` on `creator_id` and `video_id` but no composite index on `(creator_id, video_id)` — queries filtering both will scan the single-column index and fetch many rows | fix: Add `__table_args__ = (sa.Index("ix_creator_insights_creator_video", "creator_id", "video_id"),)` to CreatorInsight class
- **SEV2** db.py:80–103 — `recreate_engine()` is public (no underscore prefix) with no re-entry guard; concurrent Celery worker prefork calls could race on module-global reassignment and corrupt state | fix: Add `_engine_lock: asyncio.Lock = None` module-global + guard in `recreate_engine()`, or rename to `_recreate_engine()` (underscore signals internal-only use from celery_app)
- **cleanup** db.py:120 — `_set_app_creator_id()` event listener missing type hints on `session`, `transaction`, `connection` parameters | fix: Annotate as `session: Session, transaction: Any, connection: Any` (event payload types are not fully typed in SQLAlchemy stubs, `Any` is standard practice per Issue 78c)
- **cleanup** config.py:247–252 — Startup validation errors printed to stderr via `print()` instead of logger; JSON log aggregators miss fatal configuration errors | fix: Replace with `logging.getLogger(__name__).critical(...)` before `sys.exit(1)` so errors appear in JSON logs

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ✅ CLEAN — DB sessions via async context manager (get_session) guaranteed close on all paths including exceptions; module singletons (engine, admin_engine, limiter) initialized in lifespan + properly disposed on shutdown; no handle leaks on error paths |
| 2 Concurrency & scale | ⚠️ SEV2 — `recreate_engine()` unguarded race present; pool config correct (15+5=20 ≤ 25 PgBouncer limit per docs/DEPLOYMENT.md); no blocking calls hidden in async code; api_key `last_used_at` update on every request is write amplification |
| 3 Security & compliance | ✅ CLEAN — OAuth tokens always decrypted via `crypto.decrypt()` never logged/returned raw; no PII in log lines (verified logger calls); JWT validation with 60s leeway RFC 7519 compliant (Issue 106); MultiFernet key rotation correct per Issue 43 pattern |
| 4 Clip-quality | N/A (not a clip module) |
| 5 Anthropic SDK | N/A (not used in _root_infra; deferred to routers assessment) |
| 6 Code cleanliness & typing | ⚠️ CLEANUP — `_set_app_creator_id` untyped parameters; stderr `print()` on fatal startup; one justified `type: ignore` in main.py (Issue 78c); no commented code or debug statements |
| 7 Error handling / API | ✅ CLEAN — Config validators gate missing secrets in production (fail-fast); startup errors clear and unambiguous; /health endpoint guarded with asyncio.timeout(2.0) to prevent k8s probe hangs |
| 8 Config & paths | ✅ CLEAN — All paths validated absolute in production when load-bearing (LOCAL_MEDIA_DIR checked only when STORAGE_BACKEND=local per Issue 110); .env.example maintained; all new config via `settings` singleton |

## Module verdict

**NEEDS-WORK** — SEV2 race in `recreate_engine()` must be fixed before Celery workers scale; SEV2 CreatorInsight query performance will degrade under load without composite index; SEV1 api_key write amplification on frequent uploads needs backoff logic.

