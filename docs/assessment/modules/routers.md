# routers — assessed 2026-06-01

## Findings

- **SEV1** routers/insights.py:386-395 — Anthropic SDK client created per-request; no prompt caching, no rate limit decorator on LLM endpoint. `analyze_performer` hits Claude Haiku every time (even cache-miss on video + DNA version). Missing: prompt caching via `system:` blocks or model `cache_control` parameter, and `@limiter.limit()` on the endpoint to bound API spend. | Fix: (a) move client to module-level singleton, (b) add `cache_control={"type": "ephemeral"}` on system prompt in the `/analyze-performer` messages, (c) wrap messages.create in retry logic with exponential backoff, (d) add `@limiter.limit("10/hour", key_func=creator_key)` decorator matching other LLM-heavy endpoints.

- **SEV2** routers/activity.py:32-57 — `POST /api/activity` endpoint missing rate limiter. High-volume telemetry endpoint with no auth required can be flood-attacked by unauthenticated clients; no bound on fire-and-forget log volume. Even though `safe_extra` caps individual strings and key count, log injection via \n injection is prevented by `json.dumps()` in observability.py but a determined attacker can still DOS the disk/log pipeline. | Fix: Add `@limiter.limit("200/minute", key_func=get_remote_address)` to the endpoint, keyed by IP not creator (unauthenticated). For authenticated users, use 500/minute via `creator_key` fallback.

- **cleanup** routers/insights.py:290 — String constant `_HAIKU_MODEL = "claude-haiku-4-5-20251001"` hardcoded. No config entry in `.env.example` for model selection. If model rotates (e.g., to Haiku 4.6), the constant must be hunted across the file. | Fix: Move to `config.settings` with a default in `.env.example`, e.g., `ANTHROPIC_INSIGHTS_MODEL=claude-haiku-4-5-20251001`.

- **cleanup** routers/insights.py:388 — `anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)` instantiated on every request. No timeout, no retry policy, no connection pooling. The SDK defaults to 600s timeout and 3 retries (reasonable), but no local visibility into these params. | Fix: Create a module-level client singleton at import time: `_anthropic_client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=120, max_retries=3)` and reuse it.

- **cleanup** routers/ — Across all endpoints, per-creator isolation is enforced. Every Video/Clip/Insight query includes `creator_id == creator.id` or explicit lookup-then-ownership-check (see videos.py:308, clips.py:371, insights.py:332). All RLS predicates are present. No BLOCKER-level cross-creator leaks found.

- **cleanup** routers/ — All temp files wrapped in try/finally (videos.py:191, clips.py:283). DB sessions are dependency-injected via FastAPI `Depends(get_session)` and committed explicitly — no dangling transactions. OAuth tokens encrypted (decrypt() at auth.py:205); only raw refresh token passed to revoke endpoint once per logout flow.

- **cleanup** routers/activity.py:45-48 — `safe_extra` sanitization caps strings and keys but does not validate dict key names. A key like `user\x00injection` will serialize safely in JSON but could confuse downstream log parsers expecting alphanumeric keys. | Fix: Add `re.match(r"^[a-zA-Z0-9_]+$", k)` validation on keys before including in safe_extra.

- **info** routers/improvement.py:86-132 — Improvement brief endpoint uses `SELECT ... FOR UPDATE SKIP LOCKED` to prevent concurrent build re-queuing. Rare pattern in routers; correctly prevents double-charging on concurrent POSTs. Good example of pessimistic locking for idempotency.

## Rubric coverage

| Category | Status |
|---|---|
| Resource lifecycle | CLEAN — DB sessions injected via Depends, temp files in try/finally, no leaks |
| Concurrency & scale | NEEDS-WORK — Anthropic client per-request (should be singleton), no prompt caching, activity endpoint unratelimited |
| Security & compliance | CLEAN — Per-creator isolation on every query, tokens encrypted, no PII logged, status codes correct |
| Clip-quality | N/A |
| Anthropic SDK | NEEDS-WORK — No prompt caching, no rate limit decorator on analyze_performer, client per-request |
| Code cleanliness & typing | CLEAN — All functions typed, no TODO/print, good separation of concerns |
| Error handling & API surface | CLEAN — Pydantic on all requests/responses, correct HTTP codes, safe error messages |
| Config & paths | NEEDS-WORK — ANTHROPIC_INSIGHTS_MODEL hardcoded string, not in config |

## Module verdict
NEEDS-WORK — One SEV1 (Anthropic SDK: no caching/rate-limit on LLM endpoint), one SEV2 (activity endpoint unrated, DoS risk), and cleanup issues around config + activity key validation.

