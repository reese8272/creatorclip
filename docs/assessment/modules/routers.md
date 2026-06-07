# routers/ — assessed 2026-06-07

## Findings

- [SEV1] routers/insights.py:542 — anthropic.Anthropic() instantiated inside route handler, creating new client per request (vs module-level singleton) | fix: move client to module level: `_client = None` + `_get_client()` function (same pattern as thumbnails.py:_get_redis).
- [SEV1] routers/insights.py:541-549 — no prompt caching on Claude API call; `max_tokens=256` but no structured output or token logging | fix: add cache_control, log token usage after `msg.usage`.
- [SEV2] routers/activity.py:39 — bare `except Exception` swallowing auth errors; logs nothing when `get_current_creator` fails | fix: catch only `HTTPException`, let others propagate so 401 is visible in logs.
- [cleanup] routers/insights.py:614, 528, 588 — _insight_to_dict duplicates same dict shape (creator_id, video_id, content, created_at serialization) across 3 callsites | fix: no change needed — dict builder is <10 lines and varies per context; DRY not applicable here.
- [cleanup] routers/creators.py:117-128 — _identity_to_dict duplicates isoformat() boilerplate across response models | fix: extract to shared helper in _schemas.py (3 lines, reused 3 times).

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — all DB sessions acquired via `Depends(get_session)`, guaranteed close on context manager exit |
| 2 Concurrency & scale | SEV1 — anthropic client per-request instead of singleton; Stripe call correctly offloaded to thread |
| 3 Security & compliance | ok — per-creator isolation on every `WHERE creator_id` query; auth guards on all protected routes; no PII in logs |
| 4 Clip-quality | n/a (no clip-scoring logic in routers) |
| 5 Anthropic SDK | SEV1 — missing prompt caching + token logging on performer-analysis call |
| 6 Cleanliness & typing | cleanup — minor dict duplication, all function signatures typed |
| 7 Error handling / API | ok — correct status codes (202 for async, 4xx for validation, 402 for balance), Pydantic models on all endpoints |
| 8 Config & paths | ok — all temp files cleaned up in finally blocks, settings.UPLOAD_MAX_MB/REDIS_URL/ANTHROPIC_API_KEY used correctly |

## Module verdict

NEEDS-WORK — SEV1 on Anthropic SDK usage (no caching, no token logging) and client lifecycle (per-request instantiation). Per-creator isolation is solid; auth gates all protected routes. Fix SDK pattern before launch.
