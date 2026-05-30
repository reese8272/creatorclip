# youtube — assessed 2026-05-29

Re-assessment after hardening Issues 58–75. Scope: every file under `youtube/`
(`_http.py`, `_redis.py`, `errors.py`, `quota.py`, `oauth.py`, `analytics.py`,
`data_api.py`, `ingest.py`). Callers in `worker/` are another agent's slice and
are referenced only to establish how this module's functions are invoked.

## Findings

- [SEV2] youtube/oauth.py:303-313 — in the lock-contended wait branch, the re-read
  `session.execute(select(YoutubeToken)...)` runs against a session that already
  loaded this PK at line 272-275. With `expire_on_commit=False` (db.py:44) and no
  `.execution_options(populate_existing=True)` / `session.expire(row)`, SQLAlchemy's
  identity map returns the **cached stale instance** — the new `expires_at` the
  lock-holder committed (in its own session) is never seen. The waiter then exhausts
  all 3 retries and raises 503 even though a fresh token exists in the DB. Under
  concurrent refresh (two workers, or worker + API request for the same creator) this
  turns a benign race into spurious 503s and a wasted second refresh on the next call.
  | fix: force a DB round trip in the loop —
  `await session.refresh(fresh_row)` after fetch, or
  `select(YoutubeToken).where(...).execution_options(populate_existing=True)`; add a
  test that seeds an expired row, simulates the lock-holder committing a fresh row in a
  *separate* session, and asserts the waiter returns the new token (not 503).

- [SEV2] youtube/quota.py:51 — `_quota_key()` buckets the daily counter by
  `datetime.now(UTC)` date, but the YouTube/Google project quota resets at **midnight
  Pacific Time** (per quota.py's own docstring line 4 and COMPLIANCE.md §4). UTC date
  rolls over 7–8h before the real Google reset, so for that window our counter starts
  fresh while Google's has not — we can hand out budget the project no longer has and
  hit hard 403 `quotaExceeded`, defeating the degrade-gracefully design. | fix: key by
  America/Los_Angeles date:
  `datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")`; keep the 25h
  TTL. Add a test pinning a UTC instant that is still "yesterday" in PT and asserting
  the key matches the PT date.

- [SEV2] youtube/ingest.py:44-62 — `extract_audio_wav` runs `subprocess.run(...)` with
  **no `timeout=`**, unlike `probe_duration_s` (line 36, `timeout=30`). A wedged ffmpeg
  on a malformed/huge upload blocks the calling thread indefinitely; this function is
  invoked from `_ingest_async` (worker/tasks.py:244), so a hang ties up that worker
  slot with no recovery. | fix: add a bounded `timeout=` (e.g. proportional to source
  duration, floor ~600s) and convert `TimeoutExpired` into the existing `RuntimeError`
  path so the Celery task fails and retries cleanly.

- [SEV2] youtube/analytics.py:51 / youtube/data_api.py:93 — on a 429 the backoff uses a
  fixed exponential schedule (1s, 2s, 4s…) and ignores the `Retry-After` header Google
  returns on rate-limit/quota responses. COMPLIANCE.md §4 mandates backoff on 429/403;
  honoring `Retry-After` is the documented-correct behavior and avoids retrying before
  the server-stated window. | fix: when `resp.status_code == 429` and a `Retry-After`
  header is present, sleep `max(parsed_retry_after, computed_delay)` before the next
  attempt.

- [SEV2] docs/COMPLIANCE.md:21,47-50,85 — analytics data-retention **refresh cadence is
  still TBD/open** (Issue 75b). The mechanism exists and is sound: `refresh_youtube_analytics`
  beat task re-fetches daily (worker/schedule.py:27, worker/tasks.py:654) and overwrites
  `fetched_at`. But ToS §2 requires a *documented max-staleness / deletion* policy, and
  the table still reads "Refresh cadence TBD — confirm from ToS." There is no upper bound
  that deletes or force-refreshes analytics rows whose `fetched_at` is older than the
  policy window (e.g. if a creator's grant dies, their last metrics persist indefinitely).
  | fix: confirm Google's required staleness window, record it in COMPLIANCE.md, and add a
  purge/staleness sweep (delete VideoMetrics/RetentionCurve/AudienceActivity/Demographics
  rows whose `fetched_at` exceeds the window) so retention is enforced, not just attempted.
  Tracks as Issue 75b — flag as not-closed.

- [cleanup] youtube/analytics.py:155 — `fetch_audience_activity` hardcodes `"hour": 12`
  with the comment "Hour-level data not in public API," yet the AudienceActivity model and
  COMPLIANCE.md describe "day/hour activity windows." The magic 12 is a silent
  placeholder; a reader of downstream upload-timing logic could mistake it for a real
  hour. | fix: name the constant (e.g. `_HOUR_UNAVAILABLE_SENTINEL = 12`) or document on
  the model that `hour` is a fixed sentinel until hour-level data is available.

## Verified clean (load-bearing traces)

- OAuth tokens read via `decrypt()` only (oauth.py:229, 281, 313); `encrypt()` on every
  write (oauth.py:203-216). No token, refresh token, email, or secret appears in any
  `logger.*` line — the only token-context logs (oauth.py:244,283) log `creator_id` +
  the httpx exception, and token auth is sent via header (not URL), so the exception repr
  carries no secret.
- Shared per-process httpx singleton present and lazy (Issue 72, _http.py:19-24); reused
  across every Google/YouTube/Analytics call and across retries (oauth.py:88,94,103;
  analytics.py:46; data_api.py:88). Bound to first-use loop (correct for the post-fork
  worker loop). `aclose()` wired into both FastAPI lifespan (main.py:43) and Celery
  `worker_process_shutdown` (celery_app.py:96).
- Every httpx call carries the singleton's timeout (connect=5s, read/write/pool=15s,
  _http.py:15) — no unbounded Google call.
- 5xx backoff with jitter on the idempotent GETs (analytics.py:66-71, data_api.py:109-114);
  permanent vs transient 401/403 correctly split (errors.py + data_api.py:_classify_error)
  so revoked grants raise `YouTubeAuthError` instead of looping.
- QuotaExhausted degrade-to-tomorrow honored: `consume()` is atomic Lua check-then-incr
  (quota.py:33-45); the daily refresh catches `QuotaExhaustedError`, rolls back, and
  `break`s, deferring remaining creators to the next run (worker/tasks.py:698-704), with
  starvation-fair ordering by `last_analytics_refreshed_at NULLS FIRST` (tasks.py:666).
- Per-creator isolation: every creator-scoped query filters `creator_id`/`video_id`
  (analytics.py:191, 246, 258, 276, 285-297; oauth.py:164, 194, 241, 273). No
  unscoped reads. SQL is ORM-parameterized throughout; no f-string/`%` query building.
- Revocation: invalid_grant deletes the token row (oauth.py:236-242); auth-error in the
  refresh loop drops the row so the creator is skipped thereafter (tasks.py:705-722).
- `yt-dlp` off by default, guarded by `YTDLP_ENABLED` (ingest.py:73), documented
  own-content-only (ingest.py:1-9); present in `.env.example` with the correct warning.
- No virality promise in any string in the module.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — singleton clients, aclose wired both processes; ingest.py:44 missing subprocess timeout (SEV2) |
| 2 Concurrency & scale | 1 SEV2 (oauth.py:303 stale identity-map re-read under lock contention) |
| 3 Security & compliance | tokens/isolation/revocation clean; analytics retention cadence still open (SEV2, Issue 75b); 429 Retry-After unhonored (SEV2) |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | n/a (no LLM calls in this module) |
| 6 Cleanliness & typing | ok — signatures typed; 1 cleanup (magic `hour=12` sentinel) |
| 7 Error handling / API | n/a (no routers here; HTTPException raised from oauth is consumed by routers) |
| 8 Config & paths | ok — all new config in `.env.example` with descriptions; quota key uses wrong TZ (folded into SEV2 above) |

## Module verdict
NEEDS-WORK — no cross-tenant leak and the Issue-72 HTTP/timeout/backoff/quota hardening
holds, but four SEV2s remain: the lock-wait stale-read (spurious 503s), the UTC-vs-PT
quota reset window, the missing ffmpeg timeout, and the still-open analytics retention
cadence (Issue 75b).
