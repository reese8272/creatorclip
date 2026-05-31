# youtube — assessed 2026-05-31

Post-Wave-4 re-assessment at baseline commit `67fddc9`. **Wave 4 did NOT touch
`youtube/` directly**, but Wave 4 Fix 3 (Issue 75b) closes the largest
carry-forward compliance SEV2 against this module by adding
`worker/tasks.py::_purge_stale_youtube_analytics_async` (daily Beat,
`YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS=30`) which prunes stale rows from the
four tables this module populates (`VideoMetrics`, `RetentionCurve`,
`AudienceActivity`, `Demographics`), and by rewriting `docs/COMPLIANCE.md` §2
to document the 30-day cadence + concrete source URL
(https://developers.google.com/youtube/terms/developer-policies §III.E.4.b).
Scope: every Python file in `youtube/` (`__init__.py`, `_http.py`,
`_redis.py`, `errors.py`, `quota.py`, `categories.py`, `oauth.py`,
`analytics.py`, `data_api.py`, `ingest.py`). Callers in `worker/` and
`routers/` are other agents' slices and referenced only to verify how this
module's functions are invoked.

## Findings

- [SEV2] youtube/oauth.py:290 — `get_valid_access_token` still has no
  Redis-down degradation. `acquired: bool = await redis_client.set(lock_key,
  lock_token, nx=True, ex=_LOCK_TTL_S)` raises `redis.RedisError`
  (`ConnectionError` / `TimeoutError`) when the broker is unreachable, and
  the exception propagates uncaught through every API path and every Celery
  task that calls `get_valid_access_token` (`sync_channel_catalog`,
  `refresh_youtube_analytics`, `sync_video_analytics`, every router that
  needs a fresh token). The module's graceful-degrade posture (quota →
  "try tomorrow", auth-error → drop row, ffmpeg → bounded timeout) is
  defeated by a single Redis blip — every near-expiry token path 500s
  during a broker outage. Wave 3 added fail-open posture to the *settings*
  path (`aset_owner` callers) and Wave 4 added the analytics purge, but
  neither wave touched this file. Carry-forward from 2026-05-29 /
  2026-05-30 / 2026-05-31 (none of the earlier waves landed a fix; Wave 4
  did not touch this file). | fix: wrap the `set()` in
  `try: acquired = ... except redis.RedisError as exc:` and either (a)
  fall back to lockless refresh (`acquired = True`; Google tolerates rare
  double-refresh and the DB write is idempotent on `creator_id`) with a
  warning log, or (b) raise `HTTPException(503, "Token refresh temporarily
  unavailable")`. Add a regression test that mocks `redis_client.set` to
  raise `redis.ConnectionError` and asserts no 500 surfaces.
  (needs-runtime-confirmation under real Redis failover.)

- [cleanup] youtube/analytics.py:178 — `fetch_audience_activity` hardcodes
  `"hour": 12` because hour-level data isn't in the public Analytics API.
  The magic 12 is a silent placeholder; a reader of upload-timing logic
  could mistake it for a real "noon" hour. Carry-forward from 2026-05-29 /
  2026-05-30 / 2026-05-31 (still present; Wave 4 did not touch this file).
  | fix: name it (`_HOUR_UNAVAILABLE_SENTINEL = 12`) and reference the
  model docstring, or document on the `AudienceActivity.hour` column that
  it is a fixed sentinel until hour-level data is available.

## Closed since last assessment

- **[SEV2 → CLOSED] docs/COMPLIANCE.md analytics retention max-staleness
  purge (Issue 75b / Wave 4 Fix 3).** Verified at this baseline:
  `config.py:95` defines `YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS: int = 30`;
  `.env.example:62` documents it with the §III.E.4.b citation;
  `worker/tasks.py:170-182` registers
  `worker.tasks.purge_stale_youtube_analytics` as a Celery task; the async
  body at `worker/tasks.py:1064-1140` performs a single-transaction
  `DELETE FROM video_metrics`, `retention_curves` (cascaded via the
  collected `stale_video_ids`), `audience_activity`, and `demographics`
  WHERE `fetched_at < now() - 30 days`. The implementation is idempotent
  (DELETE is the same query each run) and respects the cascade boundary
  (does NOT touch the creator-owned `videos` table). `docs/COMPLIANCE.md`
  §2 was rewritten 2026-05-31 with the concrete cadence + the policy URL
  + the §III.D.2.3.a 7-day explicit-revoke window pointer to the
  account-deletion endpoint. The §130 launch-gate checkbox is now `[x]`.
  The largest carry-forward compliance gap on this module is fixed.

## Verified clean (load-bearing traces re-walked)

- **Wave 4 purge interaction with this module's writers.** The purge keys
  on `fetched_at`, which this module sets on every successful write
  (`analytics.py:262, 286, 295, 302, 304`). When a creator's daily Beat
  refresh stops succeeding (token revoked, quota exhausted, transient
  outage >30d), `fetched_at` stops advancing and the row falls past the
  cutoff — exactly the ToS intent. No code path in this module mutates
  `fetched_at` without also writing the corresponding payload, so the
  staleness signal cannot drift.

- **Issue 87 wiring (sync_video_catalog).** `youtube/analytics.py:198-235`
  exists and is invoked from `worker/tasks.py` (OAuth callback + Beat
  refresh). Per-creator scope enforced on the existing-row read at
  `analytics.py:213` (`Video.creator_id == creator.id`) and the inserted
  `Video` row at `analytics.py:227` carries `creator_id=creator.id`.
  Tested: `tests/test_catalog_sync.py`.

- **Issue 87 Shorts threshold.** `data_api.py:53` reads
  `settings.SHORTS_MAX_DURATION_S` (config.py default 180, `.env.example`
  documented "YouTube's official Shorts max (raised from 60s in Oct 2024)").
  Matches YouTube's current spec; no hardcoded 60.

- **Issue 88 phase-2 metrics chain.** `sync_video_analytics`
  (`analytics.py:238-271`) is invoked by the worker per video missing
  `VideoMetrics` rows; per-creator isolation enforced by the caller on the
  Video query (`Video.creator_id == creator.id`). `YouTubeAuthError`
  re-raises terminally so the token-revoke path runs.

- **Issue 88 `httpx.RequestError` retry.** Both `analytics.py:53-65` and
  `data_api.py:93-106` catch `httpx.RequestError` (parent of `ReadTimeout`,
  `ConnectError`, etc.) as transient → backoff + retry, raising after
  `_MAX_RETRIES`. The shared client's read timeout is 60s (`_http.py:20`)
  with documentation pointing to Issue 88. Tested:
  `tests/test_issue_88_filter_parity.py`.

- **Issue 88 `check_data_gate` parity.** `analytics.py:307-352` joins
  `VideoMetrics` and filters `engagement_rate is not None` — matches
  `dna/builder.py:rank_videos`'s metrics-only predicate. `ready` uses OR
  semantics (longs >= min OR shorts >= min) — matches the builder's raise
  condition. Per-creator scope enforced on both subqueries (lines 320, 331).

- **Prior SEV2 (oauth.py lock-wait stale identity-map) FIXED + REGRESSED-AGAINST.**
  Line 309 `.execution_options(populate_existing=True)` present. Test:
  `tests/test_oauth_lifecycle.py::test_concurrent_refresh_only_calls_google_once`.

- **Prior SEV2 (quota.py UTC vs PT) FIXED.** `_quota_key()` keys by
  `datetime.now(ZoneInfo("America/Los_Angeles"))` (quota.py:56-57).

- **Prior SEV2 (ingest.py no ffmpeg timeout) FIXED.** `extract_audio_wav`
  (ingest.py:62-71) passes `timeout=settings.FFMPEG_EXTRACT_TIMEOUT_S`
  (default 1800s) and converts `TimeoutExpired` to `RuntimeError` so Celery
  retries.

- **Prior SEV2 (analytics.py & data_api.py ignore Retry-After) FIXED.**
  Both call `retry_after_seconds(resp)` (errors.py:17) on 429 and sleep
  `max(retry_after, base)` (analytics.py:78-80, data_api.py:119-121).

- **Tokens: only via decrypt()/encrypt().** `decrypt()` at oauth.py:229,
  281, 318; `encrypt()` at oauth.py:204, 211, 213. No plaintext token in
  any `logger.*` line — every log line audited (oauth.py:237-247, 283,
  319-323; analytics.py:60,83,246; data_api.py:100,124) carries
  `creator_id` + `repr(exc)` only. Tokens travel in the `Authorization`
  header (not URL/body), so an exception repr cannot leak a token. No
  email/PII in any `logger.*` call.

- **Shared httpx singleton (`_http.client()`).** Lazy (`_http.py:24-29`)
  — connection pool binds to first-use loop (correct for post-fork worker
  loop per Issue 39). Reused at every Google call (oauth.py:88,94,103;
  analytics.py:53; data_api.py:93). Bounded timeout (connect=5s,
  read/write/pool=60s). `aclose()` wired in FastAPI lifespan + Celery
  `worker_process_shutdown` (cross-checked in worker slice).

- **Shared Redis singleton (`_redis.get_redis_client()`).** redis-py 4.2+
  pool per client instance — correct production pattern
  (`_redis.py:20-29`). Module-level singleton shared by `quota.py` +
  `oauth.py`.

- **5xx backoff + classify-error.** Both retry loops back off on 5xx
  (analytics.py:89-94, data_api.py:131-136); permanent vs transient 401/403
  correctly split (`_classify_error` + `PERMANENT_403_REASONS` /
  `TRANSIENT_403_REASONS` in errors.py) so revoked grants raise
  `YouTubeAuthError` (permanent) while `quotaExceeded` / `rateLimitExceeded`
  / `userRateLimitExceeded` get retried.

- **Quota Lua is atomic check-then-INCRBY-then-EXPIRE** (quota.py:39-51).
  TTL=90,000s auto-rolls the day after. `QuotaExhaustedError` honored:
  `_fetch_report` / `_get_json` call `consume()` before each request; the
  daily refresh catches it, rolls back, and breaks (verified in worker
  slice).

- **Per-creator isolation across the module.** Every creator-scoped query
  filters `creator_id`/`video_id`: analytics.py:213 (existing video IDs),
  254/262/269/281/299 (VideoMetrics/RetentionCurve/AudienceActivity/
  Demographics single-row gets), 320/331 (data-gate counts);
  oauth.py:164,194,241,273. SQL is ORM-parameterized throughout; no
  f-string/`%` query building (the `f"channel=={channel_id}"` strings at
  analytics.py:110,134,155,185 are YouTube API filter expressions on a
  validated `creator.channel_id`, not SQL — sanitized at the OAuth-callback
  boundary by Google's own channel id format).

- **Revocation handling.** `invalid_grant` deletes the YoutubeToken row
  (oauth.py:236-242); auth-error paths surface as `YouTubeAuthError` so the
  caller can drop the token row and stop calling Google. The Wave 4 purge
  belt-and-braces this: even if a token deletion races with an analytics
  write, the affected rows age out at the 30-day cutoff.

- **`yt-dlp` off by default**, guarded by `YTDLP_ENABLED`
  (ingest.py:82-85), documented own-content-only in module docstring
  (ingest.py:1-9); present in `.env.example` with the correct warning.

- **`categories.py`**: pure-static, typed, no I/O. Frozenset `NICHE_IDS`
  for O(1) validation; `label_for` linear scan is irrelevant at n=15. No
  secrets, no I/O, no virality language.

- **No virality promise** in any string in the module (grep clean).

- **No `TODO` / `FIXME` / `print()` / debug statements** anywhere in the
  slice (grep clean).

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — singleton httpx + redis clients; aclose wired in app + worker; ffmpeg bounded |
| 2 Concurrency & scale | ok inspection; 1 SEV2 carry-forward (Redis-down degradation in oauth.py:290) (needs-runtime-confirmation) |
| 3 Security & compliance | ok — tokens / isolation / revocation / Retry-After clean; analytics retention purge + 30-day staleness cutoff now in place (Wave 4 Fix 3 / Issue 75b); §130 launch-gate checkbox closed |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | n/a (no LLM calls in this module) |
| 6 Cleanliness & typing | ok — signatures typed; 1 cleanup (magic `hour=12` sentinel — carry-forward) |
| 7 Error handling / API | n/a (no routers here; `HTTPException` raised from oauth is consumed by routers) |
| 8 Config & paths | ok — `FFMPEG_EXTRACT_TIMEOUT_S`, `YOUTUBE_QUOTA_DAILY_UNITS`, `YTDLP_ENABLED`, `SHORTS_MAX_DURATION_S`, `MIN_VIDEOS_FOR_DNA`, `MIN_SHORTS_FOR_DNA`, `YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS` all present in `.env.example` with descriptions |

## Module verdict
NEEDS-WORK — Wave 4 closed the module's biggest open compliance gap (analytics
retention purge + 30-day cutoff + COMPLIANCE.md §2 documentation) without
touching any `youtube/` file. One SEV2 carries forward: the unhandled
Redis-down path in `get_valid_access_token` (oauth.py:290) that turns a broker
blip into 500s across every token-refresh path. One cleanup (magic `hour=12`
sentinel in `fetch_audience_activity`) also persists. Both are bounded fixes;
nothing in this module rises to BLOCKER or SEV1.
