# youtube — assessed 2026-06-01

## Findings
- [SEV2] youtube/analytics.py:320 — missing type hint on `creator_id` parameter | fix: change signature to `async def check_data_gate(session: AsyncSession, creator_id: uuid.UUID) -> dict:`
- [cleanup] youtube/analytics.py:40 and youtube/data_api.py:81 — duplicate retry/backoff logic in `_fetch_report` and `_get_json` | fix: extract common exponential backoff + retry-after parsing into a shared helper function in youtube/_http.py (or _retry.py), call from both locations to avoid divergence and reduce maintenance burden

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | PASS — AsyncSession passed as parameter (caller owns commit); httpx client is module-level singleton; no per-call client construction; subprocess calls have timeouts |
| 2 Concurrency & scale | PASS — no blocking calls in async functions; shared httpx client with configured timeout (60s); quota tracking via Lua script prevents N+1 and unbounded fan-out |
| 3 Security & compliance | PASS — OAuth tokens always decrypted via `decrypt()` and never logged; creator_id logged not tokens; per-creator WHERE filters on all queries (Video.creator_id, Analytics rows, Audience data); Fernet encryption enforced; invalid_grant triggers token deletion per RFC 6749 |
| 4 Clip-quality correctness | N/A — youtube module is analytics/oauth infrastructure; no clip-scoring logic present |
| 5 Anthropic SDK usage | N/A — no LLM calls in youtube module |
| 6 Code cleanliness & typing | NEEDS-WORK — missing type hint on `creator_id` in `check_data_gate`; duplicate retry logic in two functions; no TODOs, no commented code, no print() statements |
| 7 Error handling & API surface | N/A — youtube module has no HTTP routers; error handling is internal (raises YouTubeAuthError, QuotaExhaustedError for caller to handle) |
| 8 Config & paths | PASS — all paths absolute (ffprobe/ffmpeg commands); config read via settings (REDIS_URL, GOOGLE_OAUTH_*, YOUTUBE_QUOTA_DAILY_UNITS, etc.); fail-fast on missing tokens (403/401 handled) |

## Module verdict
NEEDS-WORK — one missing type hint (mechanical, no behavior risk) and one DRY violation (retry logic duplication that will diverge over time). Compliance posture is correct (per-creator isolation, token encryption, invalid_grant handling). No blockers.

