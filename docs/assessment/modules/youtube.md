# youtube — assessed 2026-07-20

Scope: youtube/_http.py, _redis.py, analytics.py, categories.py, data_api.py, errors.py,
ingest.py, oauth.py, publish.py, quota.py, __init__.py. Re-assessment against the
2026-07-01 findings: each prior finding verified FIXED or carried forward by reading the
current code and the `git diff f70a857..HEAD -- youtube/` changes (oauth.py, publish.py,
quota.py, data_api.py, analytics.py, _http.py), plus call-site verification in
worker/tasks.py (`_publish_to_youtube_async`) and config.py/.env.example.

## Resolved since 2026-07-01

- **[SEV2 → FIXED] videos.insert quota mischarge** — `COST_DATA_VIDEOS_INSERT = 100`
  against the shared read pool is gone. quota.py:159-188 adds `consume_insert()` with a
  dedicated PT-anchored key (`_insert_quota_key()`, quota.py:92-96) limited by
  `YOUTUBE_QUOTA_INSERT_DAILY_CALLS` (config.py:436 = 100; documented in
  .env.example:170), reusing `_LUA_CONSUME` atomically. Call-site verified:
  worker/tasks.py:673 calls `consume_insert()`; no remaining `COST_DATA_VIDEOS_INSERT`
  references outside stale worktrees. Uploads no longer debit the shared budget.
- **[SEV2 → FIXED] completed resumable upload misreported as failed** —
  `_query_offset` (publish.py:86-107) now returns `(offset, video_id)`; a 200/201 on the
  status query surfaces the created resource id, and both resume paths in
  `upload_video` (publish.py:155-161 exception path, 179-186 5xx path) return
  `completed_id` instead of falling through to the terminal raise. The residual
  all-bytes-received-but-no-id state raises a permanent `YouTubeUploadError(0, ...)`
  (publish.py:191-194), which the Celery wrapper treats as terminal (never retried) —
  correct anti-duplicate posture.
- **[SEV2 → FIXED] base re-login silently stripping publish scope** —
  `build_authorization_url` now sets `include_granted_scopes=true` on the BASE flow too
  (oauth.py:96-102), and `store_or_update_tokens` unions the incoming scope with the
  stored grant (oauth.py:253-260; `scope` is `nullable=False` in models.py:292, so
  `row.scope.split()` is safe). See new finding below on the union's inability to ever
  narrow.

## Findings

- [SEV2] youtube/publish.py:126-162 — residual duplicate-upload window: a raw
  `httpx.HTTPError` raised INSIDE `_query_offset` (line 95, network still down when the
  resume probe fires) or inside `_initiate` escapes `upload_video` untyped. The Celery
  caller's failure handler only catches `(YouTubeAuthError, YouTubeUploadError)`
  (worker/tasks.py:686), so the raw error falls to the generic `except Exception:
  self.retry` in the sync wrapper — the retry re-runs `upload_video` from scratch with a
  NEW resumable session. If the original session had in fact completed at Google, a
  duplicate video is created; the 2026-07-01 fix only covers the case where
  `_query_offset` is reachable. | fix: wrap the `_query_offset` call in
  `try/except httpx.HTTPError`, count it as a resume attempt with backoff instead of
  escaping; longer-term, persist `session_uri` on `ClipPublication` so a task-level
  retry resumes the SAME session (the resumable protocol's intended crash-recovery
  shape) rather than initiating a new one. (needs-runtime-confirmation)
- [SEV2] youtube/oauth.py:253-260 — the scope union can never NARROW, so a creator who
  unchecks `youtube.upload` on Google's granular-consent screen during a re-consent gets
  a token response without that scope, but the union re-adds it: `has_publish_scope()`
  stays `True` forever and every publish 403s with no self-heal — the only reset path is
  row deletion on refresh `invalid_grant` (oauth.py:291-300), which partial scope
  removal does not trigger. The inline comment "the reconnect path re-syncs the record"
  is untrue under union semantics (reconnect also unions). | fix: evidence-based
  narrowing — when the publish path gets a 403 insufficient-permission
  `YouTubeAuthError`, strip `PUBLISH_SCOPE` from `row.scope`; or validate the actual
  grant via Google's tokeninfo endpoint at exchange time and store that authoritative
  scope set. (needs-runtime-confirmation of granular-consent × include_granted_scopes
  interplay.)
- [cleanup] youtube/publish.py:165 — `resp.json().get("id")` on the final-chunk 200 can
  raise an uncaught `ValueError` on a non-JSON body; `_query_offset` (lines 100-103)
  guards the identical parse. | fix: apply the same `try/except ValueError` and raise
  `YouTubeUploadError(resp.status_code, "upload finished without a video id")` so the
  typed-error contract holds.
- [cleanup] (carry-forward) youtube/analytics.py:47 vs data_api.py:226 — Analytics
  reports still charge `consume(COST_ANALYTICS_REPORT)` against the same shared
  `_quota_key()` counter as the Data API, though the YouTube Analytics API is a separate
  product with its own quota. Conservative (throttles early, never leaks), but the
  insert-bucket split (Issue 352 Batch D) shows the intended per-product shape. | fix:
  give Analytics its own daily counter, or document the deliberate single-budget choice
  in a comment/DECISIONS entry.
- [cleanup] (carry-forward) youtube/analytics.py:47 — `consume()` is still charged
  up-front before the HTTP GET and its retry loop, so a report that ultimately fails on
  a network error debits a unit; data_api.py:226 defers `consume` until a real non-304
  200. Minor over-count (1 unit), noted for consistency.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — `_http` singleton now registered with `shared_resources.register_aclose` for app-shutdown close (_http.py:42, new since 07-01); `_redis` lazy singleton; publish.py file handle in `with`; ffprobe/ffmpeg/yt-dlp subprocesses time-bounded (ingest.py:36,71); token refresh commits on an internal `AdminSessionLocal` (oauth.py:296-316), never the caller's session |
| 2 Concurrency & scale | 1 finding (SEV2 duplicate-upload window) — otherwise ok: no sync/blocking calls inside `async def` in-slice (subprocess helpers are sync `def`); quota check-then-incr atomic via `_LUA_CONSUME` for both the shared and the new insert bucket; refresh lock is SET NX EX + Lua compare-and-delete with fail-open on Redis outage (oauth.py:352-365); backoff with jitter + Retry-After honored (data_api.py:233-258, analytics.py:76-100); catalog fan-out paginated (in-memory maps fine at ≤100-user beta) |
| 3 Security & compliance | 1 finding (SEV2 scope-union never narrows — a stale capability record, not a token leak) — otherwise ok: tokens read via `decrypt()` (oauth.py:284,343,395) and never logged (log lines carry creator_id/video_id only); per-creator isolation on every creator-scoped query (analytics.py:264,340,403-423; ETag cache key folds creator_id, data_api.py:53-66); parameterized SQLAlchemy throughout; yt-dlp gated off by default and own-content-only (ingest.py:89, COMPLIANCE §ingest); 30-day staleness purge posture unchanged and honored outside slice; no virality strings |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | n/a (no LLM calls in-slice) |
| 6 Cleanliness & typing | ok — signatures typed; no TODO/print/debug; the stale videos.insert cost comment from 07-01 was removed with the fix; one untrue comment folded into the scope-union finding |
| 7 Error handling / API | 1 finding (cleanup, publish.py:165 untyped ValueError) — no FastAPI routers in-slice; typed `YouTubeAuthError`/`YouTubeUploadError`/`QuotaExhaustedError`/`QuotaSubBudgetExhaustedError` otherwise used correctly |
| 8 Config & paths | ok — new `YOUTUBE_QUOTA_INSERT_DAILY_CALLS` in config.py:436 AND .env.example:170 with description; all quota keys PT-anchored (America/Los_Angeles) matching Google's reset; no relative paths |

## Module verdict
NEEDS-WORK — all three 2026-07-01 SEV2s (insert-quota mischarge, completed-upload
misreport, publish-scope stripping) are verified fixed end-to-end, but a residual
duplicate-upload window remains when the resume probe itself dies on the network (task
retry opens a new session), and the new scope union can never narrow a genuinely
reduced grant, leaving publish permanently 403ing for that creator.
