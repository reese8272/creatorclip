# routers — assessed 2026-06-01

## Findings
- [SEV1] routers/insights.py:376 — Anthropic client created per-request instead of module-level singleton | fix: move `client = anthropic.Anthropic(...)` to module-level initialization and reuse across calls
- [SEV1] routers/insights.py:378 — `__import__("asyncio").to_thread(...)` inline pattern is unconventional and fragile | fix: import asyncio at module top and use standard `await asyncio.to_thread(...)`
- [SEV2] routers/insights.py:312-402 — no token usage logging after `client.messages.create()` call | fix: log `msg.usage.input_tokens` and `msg.usage.output_tokens` after successful LLM call (Rubric §5 mandate)
- [SEV1] routers/insights.py:312 — no rate limiter on `analyze_performer` POST endpoint | fix: add `@limiter.limit("X/hour", key_func=creator_key)` (all new endpoints require rate limiting per rubric)
- [SEV2] routers/clips.py:173-183 — session commit after style update but before task enqueue creates race: if task runs before refresh, it reads old style | fix: refresh clip after commit before enqueuing task
- [SEV2] routers/review.py:66 — `body.feedback_tags or None` coerces empty list to None; unclear if DB stores [] or None | fix: be explicit: store `body.feedback_tags if body.feedback_tags else None` with schema comment documenting semantics
- [cleanup] routers/creators.py:285,373 — `str(exc)` exposes internal error text in 422/409 responses | fix: log full exception with `logger.exception(...)`, return safe message in HTTPException
- [cleanup] routers/improvement.py:285 — same `str(exc)` pattern in error response | fix: log exception internally, return safe message

## Rubric coverage
| Category | Status |
|---|---|
| 1. Resource lifecycle | PASS — sessions via context manager, external clients (except Anthropic in new code) are module-level singletons, temp files cleaned in finally blocks |
| 2. Concurrency & scale | NEEDS-WORK — asyncio.to_thread used correctly elsewhere (auth, videos, billing) but insights.py's inline __import__ is non-idiomatic and Anthropic per-request construction scales poorly |
| 3. Security & compliance | PASS — per-creator isolation enforced on all new queries (video.creator_id, CreatorInsight.creator_id checks present); parameterized SQL throughout |
| 4. Clip-quality correctness | n/a — no clip-scoring logic in routers |
| 5. Anthropic SDK usage | NEEDS-WORK — no prompt caching (mandatory); no token usage logging; per-request client construction (SEV1) |
| 6. Code cleanliness & typing | PASS — no TODOs, commented code, or print statements; all functions typed; DRY; no functions >30 lines that do >1 thing |
| 7. Error handling & API surface | PASS — Pydantic models on all requests/responses; correct HTTP status codes (202 for async, 404 for not-found, 422 for validation); error messages safe (except str(exc) carry-forward) |
| 8. Config & paths | PASS — no new config needed; all paths absolute |

## Module verdict
NEEDS-WORK — Anthropic client construction per-call (SEV1), missing rate limit on analyze_performer (SEV1), no token logging (SEV2), inline asyncio.__import__ non-idiomatic (SEV2), render_clip session commit race with enqueue (SEV2), and str(exc) in error responses (carry-forward cleanup).
