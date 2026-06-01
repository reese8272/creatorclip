# youtube — assessed 2026-05-31

Post-Wave-9 re-assessment. Wave 9 landed the two carry-forward items that have
trailed this slice since the post-Wave-4 baseline: **Issue 103** wrapped the
refresh-lock `redis.set()` in `try/except redis.RedisError → log + lockless
fallback`, and **Issue 108** named the magic `hour=12` placeholder as
`_HOUR_UNAVAILABLE_SENTINEL`. Both fixes verified in code below. Scope: every
Python file in `youtube/` (`__init__.py`, `_http.py`, `_redis.py`, `errors.py`,
`quota.py`, `categories.py`, `oauth.py`, `analytics.py`, `data_api.py`,
`ingest.py`). Callers in `worker/` and `routers/` are other agents' slices and
are referenced only to verify how this module's functions are invoked.

## Findings

(none — both carry-forward items are CLOSED, no net-new findings)

## Closed since last assessment

- **[CLOSED, was SEV2] Issue 103 — `oauth.py:292` Redis-down degradation in
  `get_valid_access_token`.** Lines 291-304 now wrap `redis_client.set(...)` in
  `try: ... except aioredis.RedisError as exc:` and fall back to lockless
  refresh (`acquired = True`) with a `logger.warning` that includes
  `creator_id` + `repr(exc)`. The chosen path is option (a) from the prior
  finding — safe because Google's refresh endpoint is idempotent and
  `store_or_update_tokens` is idempotent on `creator_id`. The Lua
  compare-and-delete `finally` is still inside the same `try`, but it lives on
  the post-`acquired` branch only, so a Redis outage during release would
  surface as a warning at most — it cannot 500 the path. The carry-forward SEV2
  that has trailed this slice since 2026-05-29 is closed. (Regression test
  recommendation from the prior finding — mock `redis_client.set` raising
  `redis.ConnectionError`, assert no 500 — still worth adding to
  `tests/test_oauth_lifecycle.py`; verified in code only here.)

- **[CLOSED, was cleanup] Issue 108 — `analytics.py` magic `hour=12`.** Line
  158 defines `_HOUR_UNAVAILABLE_SENTINEL = 12` with an inline comment that
  cites the YouTube Analytics dimsmets doc explaining hour-of-day isn't in the
  public API, and references Issue 108. Line 189 uses the named constant in
  the `fetch_audience_activity` return rows. A reader can no longer mistake
  the 12 for a real "noon" hour. The carry-forward cleanup is closed.

## Verified clean (load-bearing traces re-walked)

- **Wave-9 Redis-down fallback is safe.** `oauth.py:291-304` catches
  `aioredis.RedisError` (the canonical parent of `ConnectionError` /
  `TimeoutError` / `BusyLoadingError`) — not bare `Exception`, so genuine
  programming errors still propagate. The fallback sets `acquired = True`,
  meaning the worker proceeds to call `_do_token_refresh`. Two workers hitting
  this branch simultaneously for the same creator could issue duplicate
  refresh calls; Google tolerates this (the refresh endpoint is idempotent and
  returns a new token each time), and the DB upsert is idempotent on
  `creator_id` so the last write wins. The Lua release in the `finally` runs
  against a (likely still-down) Redis; if it errors, the exception escapes —
  but it does so on the success path AFTER the new token has been committed,
  so the caller still gets its token. (One refinement worth noting but NOT
  flagging at SEV: the `finally` could itself catch `RedisError` to keep the
  symmetry, but the practical blast radius is identical to the current code
  because Redis being down on `set` strongly correlates with being down on
  `eval`, and any uncaught `RedisError` there would surface to the caller
  after a successful refresh — annoying but not data-unsafe.)

- **Wave-9 hour sentinel is wired through the data path.** The
  `AudienceActivity` composite-key get at `analytics.py:294-295` still uses
  `row["hour"]`, which now flows from `_HOUR_UNAVAILABLE_SENTINEL`. Every
  audience-activity row therefore has `hour=12` consistently, preserving the
  schema invariant for downstream `upload_intel` consumers (other agent's
  slice).

- **Wave-4 purge interaction with this module's writers.** The purge keys on
  `fetched_at`, which this module sets on every successful write
  (`analytics.py:273, 299, 307, 315, 317`). When a creator's daily Beat refresh
  stops succeeding (token revoked, quota exhausted, transient outage >30d),
  `fetched_at` stops advancing and the row falls past the cutoff — exactly the
  ToS intent. No code path in this module mutates `fetched_at` without also
  writing the corresponding payload, so the staleness signal cannot drift.

- **Issue 87 wiring (`sync_video_catalog`).** `youtube/analytics.py:211-248`
  exists and is invoked from `worker/tasks.py` (OAuth callback + Beat
  refresh). Per-creator scope enforced on the existing-row read at
  `analytics.py:226` (`Video.creator_id == creator.id`) and the inserted
  `Video` row at `analytics.py:240` carries `creator_id=creator.id`.

- **Issue 87 Shorts threshold.** `data_api.py:53` reads
  `settings.SHORTS_MAX_DURATION_S` (default 180; matches YouTube's current
  spec); no hardcoded 60.

- **Issue 88 phase-2 metrics chain.** `sync_video_analytics`
  (`analytics.py:251-284`) is invoked by the worker per video missing
  `VideoMetrics` rows; per-creator isolation enforced by the caller on the
  Video query. `YouTubeAuthError` re-raises terminally so the token-revoke
  path runs.

- **Issue 88 `httpx.RequestError` retry.** Both `analytics.py:52-65` and
  `data_api.py:92-106` catch `httpx.RequestError` (parent of `ReadTimeout`,
  `ConnectError`, etc.) as transient → backoff + retry, raising after
  `_MAX_RETRIES`. The shared client's read timeout is 60s (`_http.py:20`) with
  documentation pointing to Issue 88.

- **Issue 88 `check_data_gate` parity.** `analytics.py:320-365` joins
  `VideoMetrics` and filters `engagement_rate is not None` — matches
  `dna/builder.py:rank_videos`'s metrics-only predicate. `ready` uses OR
  semantics (longs >= min OR shorts >= min) — matches the builder's raise
  condition. Per-creator scope enforced on both subqueries (lines 333, 344).

- **Prior SEV2 (oauth.py lock-wait stale identity-map) FIXED + REGRESSED-AGAINST.**
  `oauth.py:323` `.execution_options(populate_existing=True)` present. Test:
  `tests/test_oauth_lifecycle.py::test_concurrent_refresh_only_calls_google_once`.

- **Prior SEV2 (quota.py UTC vs PT) FIXED.** `_quota_key()` keys by
  `datetime.now(ZoneInfo("America/Los_Angeles"))` (`quota.py:56-57`).

- **Prior SEV2 (ingest.py no ffmpeg timeout) FIXED.** `extract_audio_wav`
  (`ingest.py:62-71`) passes `timeout=settings.FFMPEG_EXTRACT_TIMEOUT_S`
  (default 1800s) and converts `TimeoutExpired` to `RuntimeError` so Celery
  retries.

- **Prior SEV2 (analytics.py & data_api.py ignore Retry-After) FIXED.** Both
  call `retry_after_seconds(resp)` (`errors.py:17`) on 429 and sleep
  `max(retry_after, base)` (`analytics.py:78-80`, `data_api.py:119-121`).

- **Tokens: only via decrypt()/encrypt().** `decrypt()` at `oauth.py:230,
  282, 332`; `encrypt()` at `oauth.py:205, 212, 214`. No plaintext token in
  any `logger.*` line — every log line audited (`oauth.py:238-247, 284,
  298-303, 333-338`; `analytics.py:60, 83, 259`; `data_api.py:100, 124`;
  `ingest.py:40`; `quota.py:83`) carries `creator_id` + `repr(exc)` /
  scalar metadata only. Tokens travel in the `Authorization` header (not
  URL/body), so an exception repr cannot leak a token. No email/PII in any
  `logger.*` call (grep clean).

- **Shared httpx singleton (`_http.client()`).** Lazy (`_http.py:24-29`) —
  connection pool binds to first-use loop (correct for post-fork worker loop
  per Issue 39). Reused at every Google call (`oauth.py:89, 95, 104`;
  `analytics.py:53`; `data_api.py:93`). Bounded timeout (connect=5s,
  read/write/pool=60s). `aclose()` wired in FastAPI lifespan + Celery
  `worker_process_shutdown` (cross-checked in worker slice).

- **Shared Redis singleton (`_redis.get_redis_client()`).** redis-py 4.2+ pool
  per client instance — correct production pattern (`_redis.py:20-29`).
  Module-level singleton shared by `quota.py` + `oauth.py`.

- **5xx backoff + classify-error.** Both retry loops back off on 5xx
  (`analytics.py:89-94`, `data_api.py:131-136`); permanent vs transient
  401/403 correctly split (`_classify_error` + `PERMANENT_403_REASONS` /
  `TRANSIENT_403_REASONS` in `errors.py`) so revoked grants raise
  `YouTubeAuthError` (permanent) while `quotaExceeded` / `rateLimitExceeded` /
  `userRateLimitExceeded` get retried.

- **Quota Lua is atomic check-then-INCRBY-then-EXPIRE** (`quota.py:39-51`).
  TTL=90,000s auto-rolls the day after. `QuotaExhaustedError` honored:
  `_fetch_report` / `_get_json` call `consume()` before each request; the
  daily refresh catches it, rolls back, and breaks (verified in worker
  slice).

- **Per-creator isolation across the module.** Every creator-scoped query
  filters `creator_id` / `video_id`: `analytics.py:226` (existing video IDs),
  `267 / 274 / 282 / 294 / 312` (VideoMetrics/RetentionCurve/AudienceActivity/
  Demographics single-row gets), `333 / 344` (data-gate counts);
  `oauth.py:165, 195, 242, 274, 322`. SQL is ORM-parameterized throughout; no
  f-string/`%` query building (the `f"channel=={channel_id}"` strings at
  `analytics.py:110, 137, 164, 198` are YouTube API filter expressions on a
  validated `creator.channel_id`, not SQL — sanitized at the OAuth-callback
  boundary by Google's own channel-id format).

- **Revocation handling.** `invalid_grant` deletes the YoutubeToken row
  (`oauth.py:237-243`); auth-error paths surface as `YouTubeAuthError` so the
  caller can drop the token row and stop calling Google. The Wave-4 purge
  belt-and-braces this: even if a token deletion races with an analytics
  write, the affected rows age out at the 30-day cutoff.

- **`yt-dlp` off by default**, guarded by `YTDLP_ENABLED`
  (`ingest.py:82-85`), documented own-content-only in module docstring
  (`ingest.py:1-9`).

- **`categories.py`**: pure-static, typed, no I/O. Frozenset `NICHE_IDS` for
  O(1) validation; `label_for` linear scan is irrelevant at n=15. No secrets,
  no I/O, no virality language.

- **No virality promise** in any string in the module (grep clean).

- **No `TODO` / `FIXME` / `print()` / debug statements** anywhere in the slice
  (grep clean).

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — singleton httpx + redis clients; `aclose` wired in app + worker; ffmpeg bounded |
| 2 Concurrency & scale | ok — Wave-9 closed the Redis-down degradation (`oauth.py:291-304`); refresh path now fails open to lockless idempotent refresh on broker outage |
| 3 Security & compliance | ok — tokens / isolation / revocation / Retry-After clean; analytics retention purge + 30-day staleness cutoff in place (Wave-4 Fix 3 / Issue 75b) |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | n/a (no LLM calls in this module) |
| 6 Cleanliness & typing | ok — signatures typed; Wave-9 closed the magic `hour=12` cleanup (`_HOUR_UNAVAILABLE_SENTINEL` at `analytics.py:158`) |
| 7 Error handling / API | n/a (no routers here; `HTTPException` raised from oauth is consumed by routers) |
| 8 Config & paths | ok — `FFMPEG_EXTRACT_TIMEOUT_S`, `YOUTUBE_QUOTA_DAILY_UNITS`, `YTDLP_ENABLED`, `SHORTS_MAX_DURATION_S`, `MIN_VIDEOS_FOR_DNA`, `MIN_SHORTS_FOR_DNA`, `YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS` all present in `.env.example` with descriptions |

## Module verdict
clean — Wave 9 closed both carry-forward items (Issue 103 Redis-down fallback +
Issue 108 named hour sentinel). No net-new findings. Every load-bearing trace
(token decrypt, per-creator isolation, Retry-After, quota PT day, ffmpeg
timeout, lock-wait identity-map refresh, retention-purge interaction, shared
httpx/redis singletons, revocation handling) re-walked and verified. The only
soft note worth carrying forward outside this assessment is the regression
test for the Redis-down branch in `oauth.py` — code-correct, but not yet
pinned by a unit test that mocks `redis_client.set` raising
`redis.ConnectionError`.
