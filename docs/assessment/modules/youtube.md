# youtube — assessed 2026-07-01

Scope: youtube/_http.py, _redis.py, analytics.py, categories.py, data_api.py, errors.py,
ingest.py, oauth.py, publish.py, quota.py, __init__.py. Every YouTube Data/Analytics API,
OAuth, quota, and ToS claim below is verified against current official Google documentation
(URLs + fetch date inline); nothing is asserted from memory per the run constraint.

## Findings

- [SEV2] youtube/quota.py:37-41 (constant) + accounting model — `COST_DATA_VIDEOS_INSERT = 100`
  is charged against the **shared** daily counter (`_quota_key()`; the debit lives at
  worker/tasks.py:502 `consume(COST_DATA_VIDEOS_INSERT)` with no separate bucket). This is
  wrong per current official docs: `videos.insert` bills to its **own dedicated 100-calls/day
  bucket at cost 1 unit/call**, NOT 100 units of the shared 10k pool. So every publish
  over-debits the read/analytics budget by ~100×, and under a publish burst the shared budget
  (`YOUTUBE_QUOTA_DAILY_UNITS`, config.py:393 = 8000) trips `QuotaExhaustedError` for
  interactive reads/onboarding far earlier than Google would. The inline comment ("the default
  10k/day quota now allows ~100 uploads/day rather than ~6") describes a superseded model.
  | fix: track uploads against a **separate** daily key (e.g.
  `creatorclip:yt_upload_quota:{pt-date}`, limit 100 *calls*, cost 1/call) and stop debiting
  the shared `_quota_key()` counter for inserts; rewrite the comment. Verified:
  https://developers.google.com/youtube/v3/determine_quota_cost ("videos.insert: 100 quota per
  day. Each call costs 1 quota.") and
  https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits ("100
  `videos.insert` calls, and 10,000 units per day combined for all other endpoints") — both
  fetched 2026-07-01, page last-updated 2026-06-24.

- [SEV2] youtube/publish.py:86-96 (`_query_offset`) + 126-167 (`upload_video`) — when a chunk
  PUT fails mid-flight but the upload had actually completed, `_query_offset` gets 200/201 from
  the session-status query and returns `total_bytes`, **discarding the created video resource
  id** in that response. The main loop then falls out of `while offset < total` and raises
  `YouTubeUploadError(0, "upload loop exited without completing")` (line 167). A *successful*
  upload is thus reported as failed, so the Celery caller (worker/tasks.py) may retry and
  create a **duplicate YouTube upload**. | fix: make `_query_offset` return the resource id
  (or a distinguishable sentinel) on 200/201 and have `upload_video` return that id instead of
  raising. Protocol basis: https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol
  (a completed session-status query returns 200/201 with the resource), fetched 2026-07-01.
  (needs-runtime-confirmation of the exact 201 body on a status-only PUT.)

- [SEV2] youtube/oauth.py:78-100 (`build_authorization_url`, base flow omits
  `include_granted_scopes`) + 217-250 (`store_or_update_tokens` overwrites `row.scope`) — a
  base re-login builds the auth URL **without** `include_granted_scopes=true` and with
  `prompt=consent`, so Google issues a token scoped to only the base read scopes;
  `store_or_update_tokens` then unconditionally sets `row.scope = scope` (line 248), narrowing
  the stored grant. A creator who had opted into publishing (`youtube.upload`) silently loses
  it in our records — `has_publish_scope()` (oauth.py:63-67) flips to `False` — until they
  re-opt-in. | fix: set `include_granted_scopes=true` on the base flow too (Google re-presents
  prior scopes and returns the combined scope), or merge/preserve an already-granted publish
  scope in `store_or_update_tokens` rather than overwriting. Source:
  https://developers.google.com/identity/protocols/oauth2/web-server (incremental
  authorization — "an access token that represents the combined authorization"), fetched
  2026-07-01. (needs-runtime-confirmation of Google's exact narrowing behavior on re-consent.)

- [cleanup] youtube/analytics.py:47 vs youtube/data_api.py:226 — `_fetch_report` charges
  `consume(COST_ANALYTICS_REPORT)` against the **same** global `_quota_key()` counter used for
  the Data API, but the YouTube **Analytics** API is a separate product with its own quota.
  Conflating them is conservative (over-counts the shared pool → can only throttle earlier, not
  a leak), but it needlessly couples two independent budgets and compounds finding #1. | fix:
  give Analytics its own daily counter (or document the deliberate single-budget choice). The
  code conflation is real; the exact separate Analytics quota number is (unverified — needs the
  official YouTube Analytics API quota doc).

- [cleanup] youtube/analytics.py:47 — `consume()` is charged up-front before the HTTP GET (and
  before the retry loop), so a report that ultimately fails with a network error still debits a
  unit; data_api.py:226 correctly defers `consume` until a real (non-304) 200. Minor over-count
  only (1 unit, and analytics has no ETag/304 path so deferral buys nothing) — noted for
  consistency, not correctness.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — `_http`/`_redis` are lazy singletons bound to the using loop; publish.py file handle in `with`; ffmpeg/ffprobe/yt-dlp subprocesses time-bounded (ingest.py:36,71); token refresh writes on an internal `AdminSessionLocal` (oauth.py:270-308) so it never commits caller work |
| 2 Concurrency & scale | ok — no sync/blocking call inside an `async def` in-slice (subprocess calls are sync `def` helpers); shared httpx client + Redis reused across calls; quota check-then-incr is atomic via `_LUA_CONSUME`; catalog sync fan-out is bounded by pagination (large-channel in-memory maps acceptable for ≤100-user beta) |
| 3 Security & compliance | ok — tokens read via `decrypt()` (oauth.py:272,331,381) and never logged (logs carry `creator_id` only); per-creator isolation on every creator-scoped query (analytics.py:264,403-423; ETag key folds creator_id, data_api.py:53-66); parameterized SQLAlchemy; yt-dlp gated off by default (config.py:392); 30-day staleness + insert scope opt-in honored; no virality strings |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | n/a (no LLM calls in-slice) |
| 6 Cleanliness & typing | ok — signatures typed; no TODO/print/debug; one stale comment folded into finding #1 |
| 7 Error handling / API | n/a (no FastAPI routers in-slice; typed `YouTubeAuthError`/`YouTubeUploadError`/`QuotaExhaustedError` used correctly) |
| 8 Config & paths | ok — new config (`YOUTUBE_QUOTA_*`, `YOUTUBE_ETAG_CACHE_TTL_S`, `MAX_INGESTED_*`, `FFMPEG_EXTRACT_TIMEOUT_S`) present in config.py with defaults; quota key PT-anchored (America/Los_Angeles) matching Google's reset |

## Module verdict
NEEDS-WORK — no cross-tenant leak or blocker, but the videos.insert quota accounting is wrong
per current Google docs (over-debits the shared read budget), a completed-then-failed resumable
upload is misreported as failed (duplicate-upload risk), and a base re-login can silently strip
a creator's publish scope.
