# youtube — assessed 2026-05-29

Slice: `youtube/_redis.py`, `youtube/analytics.py`, `youtube/data_api.py`,
`youtube/errors.py`, `youtube/ingest.py`, `youtube/oauth.py`, `youtube/quota.py`.

## Findings

- [SEV1] youtube/oauth.py:84-109 — every OAuth/Google call (`_call_token_endpoint`,
  `_call_userinfo`, `_call_youtube_channels`) constructs a fresh `httpx.AsyncClient()`
  per call and **none sets a timeout** | this is two rubric/scale violations at once:
  (a) rubric 1 + scale axis B — clients should be a module-level singleton, not built
  per request (TLS handshake + pool teardown on every token refresh, and at hundreds of
  creators the refresh storm has no connection reuse); (b) scale axis E — a call with no
  timeout will hang the request/Celery worker indefinitely if Google's token endpoint
  stalls, cascading to outage. fix: introduce one module-level
  `_HTTP = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))` (mirroring the
  data_api/analytics pattern which already passes a timeout), reuse it in all three
  helpers, and close it on app shutdown. Token refresh is on the hot path for every
  authenticated request whose token is within 5 min of expiry, so this is load-bearing.

- [SEV1] youtube/data_api.py:86 & youtube/analytics.py:45 — `async with
  httpx.AsyncClient(timeout=...) as client:` is **inside the retry loop**, so a new
  client (and connection pool) is created and torn down on every attempt and every call |
  rubric 1 (external clients must be singletons) + scale axis B. Timeouts are correctly
  set here (good), but per-call construction defeats connection pooling under the
  analytics fan-out (one `_fetch_report`/`_get_json` per video × hundreds of creators in
  the Beat refresh). fix: hoist a single module-level `httpx.AsyncClient` with the
  configured timeout and reuse it across calls and retries; do not enter the context
  manager per attempt.

- [SEV1] youtube/analytics.py:79-163 (all `_fetch_report` callers) and
  youtube/data_api.py — no enforcement of the YouTube Analytics **data-retention /
  refresh-or-delete cadence** that docs/COMPLIANCE.md §2 marks TBD and lists as a
  pre-launch gate. Analytics rows (`VideoMetrics.fetched_at`, `RetentionCurve`,
  `AudienceActivity`, `Demographics`) are written with `fetched_at` but nothing in this
  module refreshes or purges them on Google's required cadence; cached analytics can
  persist indefinitely, which is a direct ToS exposure | fix: (1) confirm Google's
  required refresh interval and record it in COMPLIANCE.md §2; (2) add a scheduled
  refresh/purge keyed on `fetched_at` older than the cadence (Celery Beat task that
  re-fetches or deletes stale rows). Mark `(needs-runtime-confirmation)` on the exact
  cadence value until the ToS figure is confirmed.

- [SEV2] youtube/data_api.py:129-163 — `list_channel_videos` accumulates **every** video
  in the uploads playlist into one in-memory `list[dict]` with no upper bound, and
  `sync_video_catalog` (analytics.py:171) then builds two full dicts (`video_id_map`,
  `duration_map`) over that list | scale rubric 2 "no unbounded in-memory accumulation
  of per-creator data". A creator with tens of thousands of uploads spikes memory and
  quota in a single task. fix: stream pages — yield/insert per page rather than
  accumulating, or cap with a configurable `MAX_CATALOG_VIDEOS` and document it; combine
  with `worker_prefetch_multiplier=1` so one large channel cannot starve workers.

- [SEV2] youtube/oauth.py:244 — `logger.warning("Token refresh failed for creator %s:
  %s", creator_id, exc)` logs the raw `httpx.HTTPStatusError`. httpx's `str(exc)`
  includes the request URL and status (not the POST body), so the client_secret/
  refresh_token in the request `data` are **not** emitted today — but this is one
  httpx-version/`repr` change away from leaking, and the rubric requires no secret in any
  log line | fix: log only `exc.response.status_code` and a static message, never the
  exception object, on any line in the token-refresh path. (Confirmed by reading: the
  POST body at oauth.py:127-135 carries refresh_token + client_secret.)

- [SEV2] youtube/data_api.py:78-113 / youtube/analytics.py:39-71 — for a non-2xx status
  **outside** {401,403,429} (e.g. 500/503 from Google), the loop falls straight to
  `resp.raise_for_status()` with no retry, even though 5xx is the textbook transient case
  | scale axis E (retry-with-jitter on idempotent external calls). These GETs are
  idempotent, so a Google 503 currently fails the whole ingest instead of backing off.
  fix: treat 5xx like the transient branch (backoff + retry) before `raise_for_status`.

- [cleanup] youtube/data_api.py:198-209 — `get_video_stats` does
  `int(stats.get("viewCount", 0))`; `viewCount` from the API arrives as a string and a
  missing/malformed value would raise `ValueError` rather than returning `{}` | fix:
  wrap the cast in `contextlib.suppress`/`try` and default to 0, consistent with the
  defensive parsing used elsewhere in the file.

- [cleanup] youtube/analytics.py:278 — `check_data_gate(session, creator_id)` has an
  untyped `creator_id` parameter (every other signature in the slice is typed) | fix:
  annotate `creator_id: uuid.UUID` and the return as `-> dict[str, int | bool]`.

- [cleanup] youtube/data_api.py:36-46 — `parse_duration_seconds` regex requires a literal
  `T`; an ISO 8601 duration with only a day component (`P1D`, no `T`) returns 0.0 | fix:
  make the `T` and time groups optional, or note in a comment that YouTube always returns
  a time component (low real-world risk for video durations, hence cleanup not SEV).

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | 2 findings — per-call httpx clients in oauth + data_api/analytics (SEV1) |
| 2 Concurrency & scale | 2 findings — missing timeouts on OAuth calls (SEV1), unbounded catalog accumulation (SEV2), no 5xx backoff (SEV2) |
| 3 Security & compliance | 2 findings — analytics retention/refresh not enforced (SEV1, compliance gate), exc logged in refresh path (SEV2). Tokens correctly go through `decrypt()`/`encrypt()`; per-creator scoping present on every query (`creator_id`/`video_id` filters confirmed); yt-dlp gated off by default; no virality strings. |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | n/a (no LLM calls in this module) |
| 6 Cleanliness & typing | 3 cleanup findings — untyped `creator_id`, fragile `int()` cast, regex `T` edge |
| 7 Error handling / API | n/a (no FastAPI routes here; HTTP error classification reviewed under cat 2/3) |
| 8 Config & paths | ok — all referenced config (`YOUTUBE_QUOTA_DAILY_UNITS`, `OAUTH_REDIRECT_URI`, `GOOGLE_OAUTH_*`, `YTDLP_ENABLED`, `REDIS_URL`, `SOURCE_MEDIA_RETENTION_HOURS`, `TOKEN_ENCRYPTION_KEY*`) present in `.env.example` with descriptions; no relative paths |

## Module verdict
NEEDS-WORK — no cross-tenant leak and token handling is sound, but per-call httpx
clients without timeouts on the OAuth hot path and the unenforced analytics
retention/refresh cadence are SEV1s that will bite under load and against the YouTube ToS.
