# youtube — assessed 2026-06-09

## Findings

- [SEV2] youtube/data_api.py:84 + youtube/analytics.py:42 — `await consume(cost)`
  runs ONCE at the top of `_get_json` / `_fetch_report`, but the retry loop issues
  up to `_MAX_RETRIES=4` real HTTP requests against Google (429/5xx/RequestError
  arms all `continue` without re-consuming). Google bills the project for every
  accepted request; our Redis counter increments once. Under sustained 429/5xx
  churn the real quota drains while the local counter shows budget — the daily
  Beat refresh then 403s creators we believed had quota (ToS §4 quota-management
  obligation). Carried over from 2026-06-08, unfixed. | fix: move
  `await consume(cost)` inside the `for attempt` loop immediately before
  `_http.client().get(...)`; unit test: two 429s + one 200 ⇒ counter += 3.

- [SEV2] youtube/oauth.py:312-332 — fail-open is broken by its own `finally`.
  When Redis is unreachable, `redis_client.set(...)` raises, the except arm sets
  `acquired = True` ("proceed without lock"), `_do_token_refresh` succeeds — and
  then the `finally` at line 332 calls `redis_client.eval(_LUA_RELEASE_LOCK, ...)`
  against the same dead Redis. The RedisError raised in `finally` REPLACES the
  successful return, so the request 500s anyway and the documented circuit-breaker
  intent (lines 314-325) never works. (The DB commit does land, so the *next*
  request takes the fast path — blast radius is one 500 per creator per refresh
  window during a Redis outage.) | fix: track `lock_held: bool` set only when the
  Redis SET actually succeeded; release only if `lock_held`, and wrap the release
  in `try/except aioredis.RedisError: logger.warning(...)`. Regression test: mock
  redis `set` and `eval` to raise ConnectionError, assert the refreshed token is
  returned.

- [SEV2] youtube/analytics.py:252-286 — `sync_video_analytics(session, video,
  creator, access_token)` trusts the caller that `video.creator_id == creator.id`.
  It writes `VideoMetrics(video_id=video.id)` (line 276) and `RetentionCurve`
  rows (line 285) with no ownership check, so a future caller bug that crosses
  creators silently attaches creator-A analytics to creator-B's video — the same
  failure mode as the 2026-05-28 SEV-0 in `routers/improvement.py`
  (COMPLIANCE.md §Findings). Carried over, unfixed. | fix: early guard
  `if video.creator_id != creator.id: raise ValueError("creator/video mismatch")`
  + regression test asserting the raise.

- [SEV2] youtube/data_api.py:202 — `get_videos_metadata` silently truncates
  `",".join(video_ids[:50])`. Today's callers chunk to 50 already
  (analytics.py:222-223 slices in 50s), so it is latent — but the signature
  accepts an unbounded `list[str]` and drops ids 51+ with no error or log.
  Carried over, unfixed. | fix: either raise
  `ValueError("get_videos_metadata accepts ≤50 ids; chunk first")` when
  `len(video_ids) > 50`, or move the 50-chunk loop inside the function. Silent
  truncation is the worst of both.

- [SEV2] youtube/oauth.py:144-156 — `fetch_creator_identity` looks parallel
  (tuple unpacking) but the two awaits run sequentially (Python evaluates the
  tuple left to right). `_call_userinfo` and `_call_youtube_channels` hit
  independent Google endpoints; this doubles first-connect latency for nothing.
  Carried over, unfixed. | fix: `user_info, channels = await asyncio.gather(
  _call_userinfo(access_token), _call_youtube_channels(access_token))` —
  `asyncio` is already imported (line 9).

- [SEV2] youtube/data_api.py:158-192 — `list_channel_videos` paginates the
  ENTIRE uploads playlist with no cap (while-True until `nextPageToken` runs
  out) and accumulates every item in `results`. A whale channel (10k-20k
  videos) burns ~2 quota units per 50 videos in catalog sync alone, and one
  such onboarding can starve the shared `YOUTUBE_QUOTA_DAILY_UNITS=8000` budget
  for every other creator that day (rubric 2: bounded work / fan-out). The
  quota gate makes it fail gracefully, but fairness across tenants is not
  bounded. (needs-runtime-confirmation on typical catalog sizes) | fix: add
  `settings.MAX_CATALOG_VIDEOS` (e.g. 1000 → 20 pages, newest-first since the
  uploads playlist is reverse-chronological) and stop paginating at the cap;
  document in `.env.example`.

- [cleanup] youtube/analytics.py:41-101 duplicates youtube/data_api.py:81-142 —
  the retry/backoff/Retry-After/RequestError loops in `_fetch_report` and
  `_get_json` are ~60 lines of near-identical code (DRY; flagged in the two
  prior assessments, still unrefactored — and the Issue-88 RequestError fix
  indeed had to be applied twice, lines 53-66 vs 92-106). | fix: extract
  `async def _retry_get(url, headers, params, *, cost, log_prefix) -> dict`
  into youtube/_http.py next to the client singleton; both modules call it.

- [cleanup] youtube/oauth.py:17,206,263-265,299,349-351,361 — domain-layer
  module raises `fastapi.HTTPException` (400/401/503) directly. Router callers
  work, but Celery/worker callers receive HTTP-coupled exceptions they must
  special-case, and the module already has a typed-error home
  (youtube/errors.py). | fix: raise `YouTubeAuthError` (and a small
  `TokenRefreshInProgress`) here; translate to HTTP status in the routers.

- [cleanup] youtube/ingest.py:36,63-65 — `subprocess.run(capture_output=True)`
  buffers full ffmpeg/ffprobe stderr in memory; a misbehaving ffmpeg can emit
  megabytes of progress noise before the timeout fires, yet only 500 chars are
  ever used (line 71). | fix: `stderr=subprocess.PIPE, stdout=subprocess.DEVNULL`
  for `extract_audio_wav` and truncate on read, or `-loglevel error` on the
  ffmpeg command line. Low priority.

### Closed / invalidated since 2026-06-08

- [SEV1-CLOSED] oauth.py `_do_token_refresh` double-commit on the caller's
  session — re-verified fixed: writes go through an internal
  `AdminSessionLocal()` (oauth.py:242, 256-260, 267-276); caller session is
  only `refresh`ed (line 279).
- [cleanup-INVALID] "stale `type: ignore[misc]` for redis `eval`" — re-tested:
  `requirements.txt` pins `redis[hiredis]==5.2.0` and
  `.venv/bin/python -m mypy --warn-unused-ignores youtube/quota.py
  youtube/oauth.py` returns "Success: no issues found" — mypy would flag an
  unused ignore, so the ignores at oauth.py:332 and quota.py:70-76 are still
  required. Dropped.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — httpx AsyncClient lazy loop-bound singleton (_http.py:24-29) with shutdown `aclose()`; Redis singleton (_redis.py:20-29); token writes on a context-managed internal session (oauth.py:256,267); subprocesses bounded by timeout (ingest.py:36,63). Prior SEV1 confirmed closed. |
| 2 Concurrency & scale | 4 findings — quota undercount per retry (SEV2), broken Redis fail-open in `finally` (SEV2), sequential awaits in `fetch_creator_identity` (SEV2), uncapped catalog pagination (SEV2). No blocking calls inside async paths (ingest.py's `subprocess.run` is sync-only, called from Celery); quota Lua check-and-incr is atomic; refresh lock is SET NX EX + Lua compare-and-delete with waiter `populate_existing` re-read. |
| 3 Security & compliance | 1 finding (SEV2 ownership guard in `sync_video_analytics`). Otherwise PASS — every token read via `decrypt()` (oauth.py:244,303,353); no token/email/channel in any `logger.*` call (grep clean; only creator_id UUIDs); per-creator WHERE on every token/analytics query (oauth.py:200,295,342; analytics.py:227,331-338,341-349); invalid_grant ⇒ token-row delete (oauth.py:251-260) per RFC 6749 §5.2 + ToS revocation; `fetched_at` stamped on all analytics writes for the 30-day purge; yt-dlp gated off by default (ingest.py:82-85) per COMPLIANCE.md §5; ORM-parameterized SQL only; no virality language. |
| 4 Clip-quality | n/a (API/infrastructure module, no scoring). |
| 5 Anthropic SDK | n/a (no LLM calls). |
| 6 Cleanliness & typing | 3 findings — duplicated retry loop (cleanup, 3rd assessment running), HTTPException from domain layer (cleanup), ffmpeg stderr buffering (cleanup). All signatures typed; no TODO/print/commented-out blocks; prior "stale type:ignore" finding invalidated by mypy --warn-unused-ignores. |
| 7 Error handling / API | n/a (no routers). Typed `YouTubeAuthError` / `QuotaExhaustedError` for callers; Google error JSON never forwarded. |
| 8 Config & paths | ok — all config via `settings` and present in `.env.example` (REDIS_URL:21, FFMPEG_EXTRACT_TIMEOUT_S:51, YTDLP_ENABLED:64, MIN_VIDEOS_FOR_DNA:70, MIN_SHORTS_FOR_DNA:71, SHORTS_MAX_DURATION_S:72, YOUTUBE_QUOTA_DAILY_UNITS:79); ffmpeg/ffprobe PATH-resolved by name (standard); fail-fast at pydantic-settings layer. |

## Module verdict

NEEDS-WORK — 0 blockers, 0 SEV1, 6 SEV2, 3 cleanups. Security/compliance core is
solid (decrypt-only token reads, no PII in logs, per-creator isolation verified
line-by-line, ToS retention + revocation plumbing correct). But four SEV2s carried
over from 2026-06-08 remain untouched (quota undercount per retry, missing
creator/video ownership guard, silent 50-id truncation, sequential identity
fetch), and two new SEV2s landed this pass: the Redis fail-open path self-destructs
in its own `finally`, and catalog pagination is uncapped against the shared daily
quota. The quota undercount and fail-open fixes should ship before the Beat
refresh runs at production scale.

## Issue 75 Reconciliation (2026-06-23)

| Finding | Disposition |
|---|---|
| [SEV2] quota undercount per retry (youtube/data_api.py:84, analytics.py:42) | → tracked in Issue 260 (YouTube Data API quota at scale — extension + fairness + caching) |
| [SEV2] Redis fail-open broken in finally (youtube/oauth.py:312-332) | → tracked in Issue 82 (async migration wave 2 — OAuth correctness) |
| [SEV2] sync_video_analytics ownership guard missing (youtube/analytics.py:252-286) | → tracked in Issue 231 (worker tenant tasks under RLS) |
| [SEV2] get_videos_metadata silent 50-id truncation (youtube/data_api.py:202) | → tracked in Issue 260 |
| [SEV2] sequential awaits in fetch_creator_identity (youtube/oauth.py:144-156) | → tracked in Issue 82 |
| [SEV2] uncapped catalog pagination (youtube/data_api.py:158-192) | → tracked in Issue 260 |
| [cleanup] duplicated retry loop (youtube/analytics.py, data_api.py) | → tracked in Issue 82 |
| [cleanup] HTTPException raised from domain layer (youtube/oauth.py) | → tracked in Issue 82 |
| [cleanup] ffmpeg stderr buffering (youtube/ingest.py:36,63-65) | → tracked in Issue 109 (deferred design cleanups) |
