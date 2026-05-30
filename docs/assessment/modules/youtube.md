# youtube — assessed 2026-05-30

Re-assessment after the Issue-A hardening pass. Scope: every file in `youtube/`
(`_http.py`, `_redis.py`, `errors.py`, `quota.py`, `categories.py` (NEW, Issue 83),
`oauth.py`, `analytics.py`, `data_api.py`, `ingest.py`). Callers in `worker/` and
`routers/` are other agents' slices and are referenced only to verify how this
module's functions are invoked / consumed.

## Findings

- [SEV2] youtube/oauth.py:285-326 — `get_valid_access_token` has no Redis-down
  degradation. If Redis is unreachable, `get_redis_client().set(...)` at line 290
  raises a `redis.RedisError`, which propagates as an unhandled 500 to the API
  caller and breaks every analytics fetch in the Beat task. The whole module's
  graceful-degrade posture (quota → "try tomorrow", auth-error → drop row) is
  defeated by a Redis blip. | fix: wrap the lock acquisition in
  `try: acquired = await redis_client.set(...)` / `except redis.RedisError`, then
  either fall back to lockless refresh (accept rare double-refresh — Google
  tolerates it and the DB write is idempotent on `creator_id`) or raise
  `HTTPException(503, "Token refresh temporarily unavailable")`. Add a test
  asserting a `RedisError` from `set()` does not surface as a 500.
  (needs-runtime-confirmation under real Redis failover)

- [SEV2] docs/COMPLIANCE.md:21,47-50,111 — analytics data-retention **refresh
  cadence is still TBD / Issue 75b is still open** (`docs/issues.md:1452,1610`).
  The daily refresh exists (`worker/tasks.py:refresh_youtube_analytics` overwrites
  `fetched_at`), but ToS §2 requires a *documented max-staleness / deletion*
  policy and there is no purge of stale rows whose `fetched_at` exceeds the
  policy window (e.g. when a creator revokes the grant — their last
  `VideoMetrics/RetentionCurve/AudienceActivity/Demographics` rows persist
  forever). This is the largest remaining compliance gap in this module.
  | fix: confirm Google's required staleness window, record it in
  COMPLIANCE.md, add a `YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS` setting, and add a
  daily Beat sweep that deletes the four analytics tables' rows whose
  `fetched_at` is older than the window. Carry-forward from 2026-05-29
  assessment (still not closed).

- [cleanup] youtube/analytics.py:159 — `fetch_audience_activity` hardcodes
  `"hour": 12` with the comment that hour-level data is not in the public API.
  The magic 12 is a silent placeholder; a reader of downstream upload-timing
  logic could mistake it for a real hour. | fix: name the constant
  (`_HOUR_UNAVAILABLE_SENTINEL = 12`) and reference the model docstring, or
  document on the `AudienceActivity.hour` column that it is a fixed sentinel
  until hour-level data is available. Carry-forward from 2026-05-29 (still
  present).

## Verified clean (load-bearing traces re-walked)

- **Prior SEV2 (oauth.py lock-wait stale identity-map read) FIXED.** Line 309
  now appends `.execution_options(populate_existing=True)` on the re-read so the
  waiter sees the row the lock-holder committed. Regression test:
  `tests/test_oauth_lifecycle.py::test_concurrent_refresh_only_calls_google_once`.

- **Prior SEV2 (quota.py UTC vs PT) FIXED.** `_quota_key()` (line 56-57) now
  keys by `datetime.now(ZoneInfo("America/Los_Angeles"))`, matching Google's PT
  reset. Regression test:
  `tests/test_quota.py::test_quota_key_uses_pacific_not_utc` pins a UTC instant
  that is still "yesterday" in PT and asserts the PT date.

- **Prior SEV2 (ingest.py no ffmpeg timeout) FIXED.** `extract_audio_wav`
  (lines 62-69) now passes `timeout=settings.FFMPEG_EXTRACT_TIMEOUT_S` (default
  1800s, configured in config.py:72 and `.env.example:50`) and converts
  `TimeoutExpired` to `RuntimeError` so the Celery task retries.

- **Prior SEV2 (analytics.py & data_api.py ignore Retry-After) FIXED.** Both
  `analytics.py:58-60` and `data_api.py:101-103` now call
  `retry_after_seconds(resp)` (errors.py:17) on 429 and sleep
  `max(retry_after, base)`. Helper supports both delta-seconds and HTTP-date
  forms (RFC 9110 §10.2.3). Tests: `test_youtube_errors.py` (helper) and
  `test_analytics.py::test_fetch_report_honors_retry_after_on_429`.

- OAuth tokens are read only via `decrypt()` (oauth.py:229, 281, 318);
  `encrypt()` on every write (oauth.py:204-216). No token, refresh token,
  email, or secret appears in any `logger.*` line — the only token-context logs
  (oauth.py:237-240, 244, 283, 319-323) log `creator_id` + the httpx exception
  repr, and the token is sent in an `Authorization` header (not URL), so the
  exception carries no secret.

- Shared per-process httpx singleton present and lazy (`_http.py:19-24`); reused
  across every Google/YouTube/Analytics call and across retries
  (oauth.py:88,94,103; analytics.py:46; data_api.py:89). Bound to first-use loop
  (correct for post-fork worker loop). `aclose()` is wired into both FastAPI
  lifespan and Celery `worker_process_shutdown`. Every httpx call carries the
  singleton's timeout (connect=5s, read/write/pool=15s) — no unbounded Google
  call.

- 5xx backoff with jitter on idempotent GETs (analytics.py:70-75,
  data_api.py:114-119); permanent vs transient 401/403 correctly split
  (`_classify_error` + `PERMANENT_403_REASONS`/`TRANSIENT_403_REASONS` in
  errors.py) so revoked grants raise `YouTubeAuthError` (permanent) and quota /
  rateLimit / userRateLimit get retried.

- `QuotaExhaustedError` degrade-to-tomorrow honored: `consume()` is atomic Lua
  check-then-incr (quota.py:39-51); the daily refresh catches it, rolls back,
  and breaks (deferring remaining creators), with starvation-fair ordering by
  `last_analytics_refreshed_at NULLS FIRST` (per prior trace, worker slice).

- Per-creator isolation: every creator-scoped query filters
  `creator_id`/`video_id` (analytics.py:194, 250, 262, 281, 290-300;
  oauth.py:164, 194, 241, 273). No unscoped reads. SQL is ORM-parameterized
  throughout; no f-string/`%` query building.

- Revocation: `invalid_grant` deletes the token row (oauth.py:236-242);
  auth-error paths drop the row so the creator is skipped thereafter.

- `yt-dlp` off by default, guarded by `YTDLP_ENABLED` (ingest.py:82-85),
  documented own-content-only (ingest.py:1-9); present in `.env.example` with
  the correct warning.

- New `youtube/categories.py` (Issue 83): pure-static, typed, no I/O. Used by
  `routers/creators.py` (NICHE_OPTIONS listing), `dna/identity.py`
  (NICHE_IDS validation + `labels_for`), and `dna/conflict.py` (`label_for`).
  Frozenset `NICHE_IDS` gives O(1) validation; the O(n) `label_for` linear scan
  is irrelevant at n=15. No secrets, no I/O, no virality language.

- No virality promise in any string in the module.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — singleton httpx + redis clients, aclose wired in both processes, ffmpeg now bounded |
| 2 Concurrency & scale | ok inspection; 1 SEV2 (Redis-down degradation in oauth.py:285) (needs-runtime-confirmation) |
| 3 Security & compliance | tokens/isolation/revocation/Retry-After clean; analytics retention cadence still open (SEV2, Issue 75b) |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | n/a (no LLM calls in this module) |
| 6 Cleanliness & typing | ok — signatures typed; 1 cleanup (magic `hour=12` sentinel — carry-forward) |
| 7 Error handling / API | n/a (no routers here; `HTTPException` raised from oauth is consumed by routers) |
| 8 Config & paths | ok — `FFMPEG_EXTRACT_TIMEOUT_S` / `YOUTUBE_QUOTA_DAILY_UNITS` / `YTDLP_ENABLED` all present in `.env.example` with descriptions |

## Module verdict
NEEDS-WORK — all four prior SEV2s (lock-wait stale read, UTC/PT quota TZ,
missing ffmpeg timeout, ignored Retry-After) are fixed and tested. Two SEV2s
remain: the still-open analytics-retention purge (Issue 75b, compliance gap)
and an unhandled Redis-down path in `get_valid_access_token` that turns a
broker blip into 500s on every token refresh.
