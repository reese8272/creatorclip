# youtube — assessed 2026-06-24

## Findings

- [SEV2] youtube/oauth.py:347-354 — the documented Redis fail-open is defeated by
  its own `finally`. When `redis_client.set(...)` raises `RedisError` (line 335),
  the except arm sets `acquired = True` ("proceed without lock", lines 336-347)
  and `_do_token_refresh` succeeds — but the `finally` at line 352 then calls
  `redis_client.eval(_LUA_RELEASE_LOCK, ...)` against the same dead Redis, with no
  try/except. That `RedisError` propagates out of `finally` and REPLACES the
  successful return, so the request 500s during a Redis outage — exactly the
  availability failure the fail-open block was written to prevent. The DB commit
  does land, so the *next* request takes the fast path; blast radius is one 500
  per creator per refresh window during a Redis outage, self-healing. Carried over
  from the 2026-06-09 assessment, still unfixed. | fix: set a
  `lock_held = False`, flip it `True` only when the SET actually returns; in the
  `finally` release only `if lock_held`, and wrap the `eval` in
  `try/except aioredis.RedisError: logger.warning(...)`. Regression test: patch
  redis `set` and `eval` to raise ConnectionError, assert the refreshed token is
  returned (no 500).

- [SEV2] youtube/data_api.py:117 + youtube/analytics.py:43 — `await consume(cost)`
  runs ONCE at the top of `_get_json` / `_fetch_report`, but the retry loop issues
  up to `_MAX_RETRIES=4` real HTTP requests to Google (the 429/5xx/`RequestError`
  arms all `continue` without re-consuming). Google bills the project quota for
  every accepted request; our Redis counter increments once, so under sustained
  429/5xx churn the real project quota drains while the local counter still shows
  budget. The daily Beat refresh then 403s creators we believed had quota,
  violating the ToS §4 quota-management obligation (docs/COMPLIANCE.md). Carried
  over, still unfixed. | fix: move `await consume(cost)` inside the `for attempt`
  loop, immediately before `_http.client().get(...)`. Unit test: two 429s + one
  200 ⇒ counter incremented by 3.

- [SEV2] youtube/publish.py:144,162,167 — false-failure on a final-chunk transient
  error causes a DUPLICATE upload via Celery retry. If the LAST chunk PUT raises
  (httpx error, line 139) or 5xx (line 158), `_query_offset` is called; when the
  server already holds every byte it returns `total` (line 92-93), the
  `while offset < total` loop exits WITHOUT returning a video id, and line 167
  raises `YouTubeUploadError(0, "upload loop exited without completing")` — despite
  the upload having succeeded. The caller (worker/tasks.py:414) marks the
  ClipPublication failed and re-raises; with `max_retries=3` Celery re-runs the
  task, which opens a FRESH resumable session and re-uploads the whole file → a
  second (private) video on the creator's channel and ~100 wasted quota units. The
  ClipPublication UNIQUE constraint guards duplicate *scheduling*, not task-retry
  re-upload. Bounded to the rare final-chunk-failure window; publish path is
  pre-audit / `privacyStatus=private` / opt-in scope only. | fix: after the loop,
  before raising, query the session once more (`_query_offset`/GET the session) and
  if the server reports complete, fetch and return the created resource id instead
  of raising — or treat a `_query_offset` result of `total` inside the loop as
  "finished" and re-issue a zero-length finalize to read back the `id`.

- [SEV2] youtube/publish.py:140-145,158-163 — the resume budget is global, not
  per-stall, so a long multi-chunk upload fails on cumulative blips. `attempts` is
  reset to 0 ONLY on a server-acknowledged 308 that advanced the offset (line 154);
  an httpx error (line 140) and a 5xx (line 159) each do `attempts += 1` with no
  reset even when `_query_offset` successfully recovers and the next chunk lands.
  Over a multi-GB upload (hundreds of 8 MiB chunks) any 6 independent transient
  failures spread across the whole transfer exhaust `_MAX_RESUME_ATTEMPTS=5` and
  abort a recoverable upload. Bounded to large uploads on the publish path.
  (needs-runtime-confirmation on real chunk-failure rates) | fix: reset
  `attempts = 0` after a successful chunk write of any kind (i.e. whenever forward
  progress is made), keeping the counter a measure of *consecutive* failed resume
  attempts rather than lifetime failures.

- [SEV2] youtube/analytics.py:254-287 — `sync_video_analytics(session, video,
  creator, access_token)` trusts the caller that `video.creator_id == creator.id`.
  It writes `VideoMetrics(video_id=video.id, ...)` (line 278) and `RetentionCurve`
  rows (line 287) with no ownership assertion, so a future caller bug that crosses
  creators would silently attach creator-A analytics to creator-B's video — the
  same failure mode as the 2026-05-28 SEV-0 in routers/improvement.py
  (docs/COMPLIANCE.md §Findings). Today's callers (worker/tasks.py:2444,
  worker/tasks.py:2564) DO scope their video selects by `Video.creator_id`, so this
  is defense-in-depth, not a live leak. Carried over, still unfixed. | fix: add an
  early guard `if video.creator_id != creator.id: raise ValueError("creator/video
  mismatch")` + a regression test asserting the raise.

- [SEV2] youtube/data_api.py:251 — `get_videos_metadata` silently truncates with
  `",".join(video_ids[:50])`. The signature accepts an unbounded `list[str]` but
  drops ids 51+ with no error or log. The only caller chunks to 50 first
  (analytics.py:223-224), so it is latent today, but silent truncation is the worst
  of both worlds — a future caller passing 80 ids loses 30 videos' metadata
  invisibly. Carried over, still unfixed. | fix: raise
  `ValueError("get_videos_metadata accepts <=50 ids; chunk first")` when
  `len(video_ids) > 50`, OR move the 50-chunk loop inside the function.

- [SEV2] youtube/data_api.py:191-241 — `list_channel_videos` paginates the ENTIRE
  uploads playlist (while-True until `nextPageToken` is exhausted) and accumulates
  every item in `results` with no cap. A whale channel (10-20k videos) burns ~2
  quota units per 50 videos during catalog sync, and one such onboarding can starve
  the shared `YOUTUBE_QUOTA_DAILY_UNITS` (default 8000) budget for every other
  creator that day (rubric 2: bounded work / fan-out). The quota gate makes it fail
  gracefully, but per-tenant fairness is unbounded. (needs-runtime-confirmation on
  typical catalog sizes) | fix: add `settings.MAX_CATALOG_VIDEOS` (e.g. 1000 → 20
  pages; the uploads playlist is reverse-chronological so newest-first is the right
  truncation), stop paginating at the cap, and document it in `.env.example`.

- [cleanup] youtube/oauth.py:166-178 — `fetch_creator_identity` reads as parallel
  (tuple unpacking, lines 168-171) but the two awaits run sequentially (Python
  evaluates the tuple left-to-right). `_call_userinfo` and `_call_youtube_channels`
  hit independent Google endpoints; serializing them doubles first-connect latency
  of the OAuth callback for no reason. | fix: `user_info, channels = await
  asyncio.gather(_call_userinfo(access_token), _call_youtube_channels(access_token))`
  — `asyncio` is already imported (line 9).

- [cleanup] youtube/analytics.py:42-102 duplicates youtube/data_api.py:114-175 —
  the retry/backoff/Retry-After/`RequestError` loop in `_fetch_report` and
  `_get_json` is ~60 lines of near-identical code (DRY). It has been flagged in
  prior assessments and remains unrefactored; the Issue-88 `RequestError` fix had
  to be applied in both copies, which is exactly the maintenance hazard DRY guards
  against. | fix: extract `async def _retry_get(url, *, headers, params, cost,
  log_prefix) -> dict` into youtube/_http.py next to the client singleton; both
  modules call it.

- [cleanup] youtube/oauth.py:228,285,321,371,383 — this domain-layer module raises
  `fastapi.HTTPException` (400/401/503) directly. Router callers work, but the
  Celery/worker callers (worker/tasks.py) receive HTTP-coupled exceptions they must
  special-case, and the module already has a typed-error home (youtube/errors.py).
  | fix: raise a domain exception (e.g. `YouTubeAuthError` / a new
  `TokenRefreshError`) here and translate to HTTPException in the router boundary.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — shared httpx + redis are lazy module-level singletons (`_http.py`, `_redis.py`), closed on shutdown (main.py:92, celery_app.py:113); `_do_token_refresh` writes on an internal `AdminSessionLocal` ctx-mgr; upload file handle in a `with`; ffmpeg/ffprobe bounded by `timeout=` |
| 2 Concurrency & scale | 3 findings — fail-open finally (oauth), duplicate-upload + global resume budget (publish), unbounded catalog pagination (data_api). No sync/blocking call inside any `async def` (subprocess only in sync `def` in ingest.py); per-creator Redis advisory lock is correct (SET NX EX + Lua compare-and-delete) |
| 3 Security & compliance | ok — tokens always via `decrypt()` (oauth.py:266,325,375); no token/PII in any `logger.*` line (only creator UUID / exc / path); every creator-scoped query filters `creator_id` (`sync_video_catalog` 229, `check_data_gate` 335/349, worker callers); `invalid_grant` deletes the token row; quota backoff honors Retry-After; `yt-dlp` off by default behind `YTDLP_ENABLED`; no virality promise in any string |
| 4 Clip-quality | n/a — data/auth/transport module, no scoring |
| 5 Anthropic SDK | n/a — no LLM calls in this module |
| 6 Cleanliness & typing | 3 cleanups — sequential awaits, DRY retry-loop duplication, HTTPException in domain layer. No TODO/print/debug; all signatures typed |
| 7 Error handling / API | n/a — no FastAPI routes here (routers own the API surface); typed exceptions (`YouTubeAuthError`, `YouTubeUploadError`, `QuotaExhaustedError`) are well-formed |
| 8 Config & paths | ok — every setting (`MAX_INGESTED_*`, `YOUTUBE_QUOTA_DAILY_UNITS`, `YTDLP_ENABLED`, `FFMPEG_EXTRACT_TIMEOUT_S`, `SHORTS_MAX_DURATION_S`, `YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS`, OAuth client/redirect) present in both config.py and .env.example; paths via `pathlib.Path` |

## Module verdict
NEEDS-WORK — no cross-tenant leak and no blocking-in-async (isolation, decrypt, and
singletons all hold), but a cluster of carried-over SEV2s persists (Redis fail-open
defeated by its own `finally`, quota under-counted on retries) plus two newly-found
publish-path SEV2s (final-chunk false-failure → duplicate upload, and a global rather
than per-stall resume budget) that bite under load or infra degradation.
