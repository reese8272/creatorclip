# _root_infra — assessed 2026-06-01

## Findings

- [SEV2] api_key.py:113-114 — `last_used_at` write on every API-key request causes write amplification. Every auth path commits a transaction just to update a timestamp. | fix: batch updates into a periodic job (e.g., Beat task every 5 min) or use an async fire-and-forget PATCH instead of synchronous session.commit().

- [SEV1] models.py:728-757 — `CreatorInsight` model missing `__table_args__` with composite index on `(creator_id, video_id)`. Docstring says "cached per (video_id, dna_version)" but queries will likely scan the full creator_insights table by creator_id. Without the index, cross-tenant list operations (e.g., GET /insights for a creator) will N+1 or full-table-scan. | fix: add `__table_args__ = (sa.Index("ix_creator_video_insight", "creator_id", "video_id"),)` after line 757.

- [cleanup] config.py:231-236 — fatal startup errors use `print()` instead of `logger.error()`. Breaks JSON log aggregation and loses the request_id context. | fix: replace with `logging.getLogger(__name__).critical()` so logs stay structured and searchable.

- [SEV2] db.py:80-103 — `recreate_engine()` is public with no re-entry guard or validation. If called twice concurrently (race in Celery prefork handler), the second caller inherits a disposed pool while the first is mid-rebind. | fix: add `_engine_rebind_lock: asyncio.Lock` at module level and acquire it inside `recreate_engine()`, or guard with a boolean flag `_engine_recreated: bool`.

- [SEV1] auth.py:47 + api_key.py:95-102 — both paths issue a SELECT on `creators` table BEFORE `session.info["creator_id"]` is set, so RLS policies cannot gate the query. The `creators` table is documented as exempt from RLS (Issue 56), but this is a non-obvious exception that must be enforced in tests. | fix: add an integration test that verifies SELECT on creators without creator_id GUC set returns rows (non-RLS), then confirm RLS gates on subsequent queries.

- [cleanup] observability.py:43-44 — `_task_start_ctx` ContextVar is safe only under Celery prefork (one task per process). If Celery switches to threaded/gevent pool in production, multiple tasks per process will collide on the same ContextVar, causing duration metrics to be incorrect. | fix: document the prefork-only assumption in a comment or add a startup check that asserts `CELERYD_POOL == "prefork"` in production.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | **PASS** — sessions via context manager; engines module-level singletons; no leaks. SEV2: recreate_engine re-entry risk. |
| 2 Concurrency & scale | **WARN** — api_key write-on-every-auth is bounded (per-creator rate limit caps writes), but SEV2 recreate_engine race + observability prefork assumption flagged. |
| 3 Security & compliance | **PASS** — encrypted tokens via crypto.decrypt(), no PII in logs, RLS exemptions documented (creators table). SEV1: SELECT creators before RLS context set is spec'd but not tested. |
| 4 Clip-quality correctness | n/a — no clip scoring logic in root infra. |
| 5 Anthropic SDK usage | n/a — no LLM calls in these modules. |
| 6 Code cleanliness & typing | **PASS** — no TODO/commented code, full typing. cleanup: config.py print() instead of logging. |
| 7 Error handling & API surface | **PASS** — auth/api_key raise HTTP 401/403 with safe messages, no stack traces. |
| 8 Config & paths | **PASS** — all config present in Settings, fail-fast on required vars. All media paths checked for absolute when STORAGE_BACKEND=local. |

## New code: Models (Issues 113-119)

- **InsightType enum** (models.py:77-80): well-defined, no issues.
- **CreatorInsight model** (models.py:728-757): **MISSING composite index** on (creator_id, video_id). Creator insight lists will scan full table. Fix: add __table_args__ with (creator_id, video_id) index.
- **ClipFeedback.feedback_tags** (models.py:511): JSONB column correctly typed as `list | None`. No constraint violations. ✓
- **ClipFeedback.feedback_note** (models.py:513): Text column correctly typed. ✓
- **Clip.style_preset** (models.py:477): JSONB column correctly typed as `dict | None`. Schema documented in comment. ✓

## Module verdict

NEEDS-WORK — New CreatorInsight model missing critical index for creator_id + video_id queries; recreate_engine lacks re-entry guard (SEV2 race); API-key last_used_at write amplification (SEV2); auth/api_key SELECT creators before RLS context requires integration test (SEV1); config.py prints on fatal startup instead of logging (cleanup).

Five concrete fixes required; all are below-threshold for blocking but must land in a sweep before scale testing begins.
