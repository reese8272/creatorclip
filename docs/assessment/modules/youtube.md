# youtube — assessed 2026-06-08

## Findings

- [SEV1-CLOSED] youtube/oauth.py:220-276 — `_do_token_refresh` was calling
  `session.commit()` on a caller-owned `AsyncSession`, flushing unrelated pending
  writes. FIXED in commit 030f987: now opens an internal `AdminSessionLocal()`
  scoped to token writes only, and caller's session is refreshed via
  `session.refresh(row)` so subsequent reads see the new token. ✓

- [SEV2] youtube/quota.py:64 + youtube/data_api.py:84 + youtube/analytics.py:42 —
  `consume(cost)` runs ONCE at the top of `_get_json` / `_fetch_report` but the
  retry loop issues up to 4 real HTTP requests against Google. Google bills our
  project for every accepted request, but our Redis counter only ever decrements
  once. Under sustained 429/5xx churn the worker exhausts the real Google quota
  while our local counter still shows budget remaining — the daily Beat refresh
  will start 403'ing creators we believed we had quota for. | fix: call
  `await consume(cost)` inside the retry loop just before each `_http.client().get`,
  so every actual HTTP call costs one quota unit. Add a unit test that issues two
  429s + a 200 and asserts the counter incremented by 3.

- [SEV2] youtube/analytics.py:252-286 — `sync_video_analytics(video, creator, ...)`
  trusts the caller that `video.creator_id == creator.id`. It writes
  `VideoMetrics(video_id=video.id)` and `RetentionCurve(video_id=video.id)`
  without verifying ownership, so a future caller bug that crosses creators
  would silently attach creator-A's analytics to creator-B's video row.
  The 2026-05-28 cross-creator leak in `routers/improvement.py` (see
  COMPLIANCE.md §Findings) is the exact failure mode this module should
  refuse to enable. | fix: add an early `if video.creator_id != creator.id:
  raise ValueError("creator/video mismatch")` guard; add a regression test
  asserting the function raises when given a video owned by a different creator.

- [SEV2] youtube/data_api.py:195-214 — `get_videos_metadata` silently truncates
  `video_ids[:50]` (line 202). Today's two callers happen to chunk to 50 already
  (`analytics.py:223` slices in 50s; `routers/videos.py` passes one id at a time),
  so the bug is latent — but the function's signature accepts an unbounded
  `list[str]` and gives no error on overflow. A future caller will pass 100 ids
  and lose half the catalog with no log line. | fix: either `raise ValueError
  ("get_videos_metadata accepts ≤50 ids per call; chunk first")` on
  `len(video_ids) > 50`, OR move the chunking loop inside this function so the
  caller contract is "pass any number, get them all". Choose one; the silent
  truncation is the worst of both worlds.

- [SEV2] youtube/oauth.py:139-151 — `fetch_creator_identity` looks parallel via
  tuple unpacking but the awaits run **sequentially** (lines 141-143: Python
  evaluates left to right, awaiting each before the next). Both calls hit
  independent Google endpoints with no ordering dependency. Doubles
  first-connect latency for no reason — and the structure implies the author
  thought they were parallel. | fix: `user_info, channels = await asyncio.gather(
  _call_userinfo(access_token), _call_youtube_channels(access_token))`.

- [cleanup] youtube/analytics.py:41-101 and youtube/data_api.py:81-142 — the
  retry/backoff/Retry-After/RequestError loops in `_fetch_report` and `_get_json`
  are now ~60 lines of near-identical code. Already flagged in the prior
  assessment; still unrefactored. Risk: any future fix (e.g. honoring
  Retry-After on 503) has to be made in both places or they diverge. | fix:
  extract `async def _retry_get(url, headers, params, *, cost, error_log_prefix)`
  into `youtube/_http.py` next to the client singleton. Both modules call it.

- [cleanup] youtube/oauth.py:327 + youtube/quota.py:70,74-76 — `# type: ignore
  [misc]  # SDK/stub typing lag (Issue 78c)` is repeated 4× for the redis-py
  `eval` signature. The asyncio redis stubs have been correct since redis-py 5.2
  (Aug 2025); these ignores may be stale. | fix: verify redis-py version in
  requirements.txt is ≥5.2; if so, drop the ignores and let mypy verify. If
  they're still needed, update the issue ID reference.

- [cleanup] youtube/ingest.py:36,63 — `subprocess.run` with `capture_output=True`
  on potentially-large ffmpeg stderr loads the whole stream into memory. Bounded
  by the 30s/configured timeout, but a misbehaving ffmpeg can still emit
  megabytes of progress noise. | fix: pipe stderr to `subprocess.DEVNULL` for
  `extract_audio_wav` (we only need the return code; we already truncate to 500
  chars on failure at line 71) — or use `stderr=subprocess.PIPE` with a small
  capped read. Low priority; only matters under repeated failure.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | PASS (was 1 finding; SEV1 fixed in 030f987). httpx + redis singletons correct; subprocess bounded by timeout. |
| 2 Concurrency & scale | 2 findings — sequential awaits in `fetch_creator_identity` (SEV2); quota-per-retry undercount (SEV2). No blocking calls hidden in async; httpx client is loop-bound singleton; quota Lua is atomic. |
| 3 Security & compliance | PASS — every token read flows through `decrypt()` (oauth.py:298,348,374); no token, refresh_token, email, or channel_id appears in any `logger.*` call (only `creator_id` UUID); per-creator `WHERE` on every analytics/audience/token query verified; invalid_grant → row delete matches §III.D.2.3 revocation handling; `fetched_at` stamped on every analytics write so the 30-day staleness purge (COMPLIANCE.md) has the field it needs; `yt_dlp` gated behind `YTDLP_ENABLED=false` default per ToS source-acquisition rule. One SEV2 trust-boundary gap: `sync_video_analytics` doesn't enforce video↔creator ownership it accepts as parameters. |
| 4 Clip-quality correctness | n/a (infrastructure module, no scoring). |
| 5 Anthropic SDK usage | n/a (no LLM calls). |
| 6 Cleanliness & typing | 2 findings — duplicate retry loop (cleanup, repeat from prior assessment); stale `type: ignore` annotations (cleanup). All function signatures typed; no TODO/print/commented blocks; functions stay ≤30 lines except `_get_json`/`_fetch_report` which the DRY fix collapses. |
| 7 Error handling / API | n/a (no routers in module). Raises typed `YouTubeAuthError` / `QuotaExhaustedError` for callers; never leaks Google error JSON. |
| 8 Config & paths | PASS — `ffprobe`/`ffmpeg` invoked by name (PATH-resolved, standard for ops tooling); all config via `settings` (`REDIS_URL`, `YOUTUBE_QUOTA_DAILY_UNITS`, `FFMPEG_EXTRACT_TIMEOUT_S`, `YTDLP_ENABLED`, `SHORTS_MAX_DURATION_S`, `GOOGLE_OAUTH_*`); fail-fast happens at pydantic-settings layer. |

## Module verdict

NEEDS-WORK — SEV1 double-commit FIXED in 030f987. Remaining: 4 SEV2 (quota undercount per-retry, missing creator/video ownership check, silent 50-id truncation, sequential awaits) + 3 cleanups (duplicate retry loop, stale type:ignore, ffmpeg stderr buffering). Security & compliance posture is solid: tokens always decrypted, never logged; per-creator WHERE enforced on every query; revocation + retention plumbing correct. The 4 SEV2s should ship before the Beat refresh runs at production scale; 3 cleanups are optional but beneficial for maintainability.
