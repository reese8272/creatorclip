# youtube — assessed 2026-07-20 (post-fix)

Scope: youtube/_http.py, _redis.py, analytics.py, categories.py, data_api.py, errors.py,
ingest.py, oauth.py, publish.py, quota.py, __init__.py. Re-assessment of the
2026-07-20 (morning) findings after the two fix waves (`git diff ca3305c..e92b93a --
youtube/`: oauth.py +27/−8, publish.py +31/−2 in commits 2279720 + f29a2be). Each prior
finding verified FIXED or STILL OPEN by reading current code, the new tests
(tests/test_publish.py, tests/test_youtube_edges.py), the Celery caller
(worker/tasks.py:604-799, celery_app.py acks_late config), and the DECISIONS 2026-07-20
fix-batch entry.

## Resolved since the morning assessment

- **[SEV2 → FIXED] publish.py residual duplicate-upload window (resume probe transport
  error escaping untyped)** — new `_resume_offset` (publish.py:112-134) wraps
  `_query_offset` in `except httpx.HTTPError` with exponential backoff (1→16s cap,
  `_MAX_RESUME_ATTEMPTS=5`) against the SAME session URI; on exhaustion it raises a
  terminal `YouTubeUploadError(0, ...)`, which the task wrapper (worker/tasks.py:622-625)
  treats as permanent — never the generic `self.retry` that would open a new resumable
  session. Both call sites converted (publish.py:182 chunk-transport path, :206 5xx
  path). Exception-path audit of `upload_video`: no `httpx.HTTPError` can now escape
  with a live session — `_initiate` (publish.py:60) can still leak a raw transport
  error, but at that point zero bytes have been PUT, so a task-level retry's new session
  cannot create a duplicate video (the orphaned empty session just expires). Regression
  tests: `test_offset_query_transport_error_retries_same_session` (asserts
  `post.assert_called_once()` — never a second session) and
  `test_offset_query_transport_errors_exhausted_raise_typed_terminal`.
- **[SEV2 → FIXED] oauth.py scope union could never narrow** — union replaced with
  replace-on-grant (oauth.py:263-271): the token response `scope` is treated as Google's
  authoritative CURRENT grant (valid because `include_granted_scopes=true` is on every
  auth URL, so a re-auth response carries the combined authorization); a scope unchecked
  on the granular-consent screen now genuinely narrows the stored grant, and the
  narrowing is logged (scope names + creator_id only — no token/PII). The `if scope:`
  guard keeps the stored grant when the field is empty/omitted (routers/auth passes ""
  as default), so a scope-less response cannot wipe the record. Refresh flow verified:
  `_do_token_refresh` passes `scope=new_tokens.get("scope", row.scope)` (oauth.py:324) —
  a refresh response without a scope field replaces the grant with itself (no-op), and a
  refresh response WITH scope reflects the current grant, so refresh-path narrowing is
  also correct and never wipes. Tests:
  `test_store_or_update_tokens_narrows_scope_on_downgraded_regrant` +
  `test_store_or_update_tokens_empty_scope_keeps_stored_grant`. Residual runtime
  uncertainty (base re-login `scope` field actually carrying the combined grant under
  granular consent) now fails CLOSED — worst case is a re-consent prompt via
  `has_publish_scope()=False`, not the old permanent-403 fail-open.
  (needs-runtime-confirmation, but the failure direction is now safe.)
- DECISIONS entry present ("Publish resume probe: in-process retry, not session
  persistence", 2026-07-20) and matches what shipped.

## Findings

- [SEV2] youtube/publish.py + worker/tasks.py — the DECISIONS-accepted residual:
  `session_uri` is still not persisted on `ClipPublication`, so a hard worker death
  (SIGKILL/OOM/soft-time-limit kill — `task_acks_late=True` in celery_app.py:71
  redelivers the task) or a user re-trigger after the terminal probe failure re-runs
  `upload_video` with a NEW session; if the original session had completed at Google, a
  duplicate video results. The idempotency guard (worker/tasks.py:686-705) only
  short-circuits `status == done` rows — a crash mid-upload leaves `running`. Window is
  now narrow (requires session-completed-at-Google AND process death before the
  success-path commit) and explicitly accepted in DECISIONS 2026-07-20 with session
  persistence named the follow-up. | fix (the named follow-up): migration adding
  `clip_publications.session_uri`; write it after `_initiate`, and on re-run resume that
  session (probe first) instead of initiating. (needs-runtime-confirmation)
- [SEV2 → downgraded, tracked as accepted] the terminal `YouTubeUploadError(0, ...)`
  from an exhausted probe marks the publication failed even when the upload may have
  succeeded at Google (success misreported as failure — the safe, non-duplicating
  direction, chosen deliberately). Folded into the session-persistence follow-up above;
  not counted separately.
- [cleanup] (carry-forward, STILL OPEN) youtube/publish.py:192 — final-chunk 200
  `resp.json().get("id")` can raise an uncaught `ValueError` on a non-JSON body;
  `_query_offset` (publish.py:102-105) guards the identical parse. Note the stakes rose
  slightly: this is now the ONLY untyped escape reachable with a completed upload (a
  ValueError here falls to the generic Celery retry → new session → duplicate), though a
  non-JSON 200 from Google's upload endpoint is essentially theoretical. | fix: same
  `try/except ValueError` → `YouTubeUploadError(resp.status_code, "upload finished
  without a video id")`.
- [cleanup] (carry-forward, STILL OPEN) youtube/analytics.py:45 vs data_api.py:226 —
  Analytics reports still charge `consume(COST_ANALYTICS_REPORT)` against the shared
  Data-API daily counter although the Analytics API is a separate quota product.
  Conservative (throttles early, never overspends). | fix: dedicated counter à la
  `consume_insert()`, or a one-line DECISIONS note documenting the single-budget choice.
- [cleanup] (carry-forward, STILL OPEN) youtube/analytics.py:45 — `consume()` charged
  up-front before the HTTP GET + retry loop, so a report that dies on the network debits
  a unit; data_api.py:226 defers until a real non-304 2xx. Minor over-count.

## New-regression check (`git diff ca3305c..HEAD -- youtube/`)

Only oauth.py and publish.py changed; no new defects found. `_resume_offset` uses
`asyncio.sleep` (no loop blocking); worst-case added wall time is bounded (≤5 probes ×
≤15s backoff per outer attempt, inside the existing Celery soft time limit for typical
clips); its warning log carries the httpx exception only (same pattern as the existing
chunk-failure log — no token). The narrowing log line carries creator_id + scope URLs,
no PII/secret. Stray `=======` conflict markers left in docs/DECISIONS.md by the
integration merge are outside this slice — logged in docs/OFF_COURSE_BUGS.md.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — `_http` singleton registered for app-shutdown aclose; publish file handle in `with`; token refresh commits on internal `AdminSessionLocal` (oauth.py:307-327), caller session read-only; subprocesses time-bounded (ingest.py) |
| 2 Concurrency & scale | 1 finding (SEV2 crash-window duplicate — DECISIONS-accepted residual, session persistence is the named follow-up) — otherwise ok: exception-path duplicate window closed by `_resume_offset`; quota atomic via `_LUA_CONSUME` (shared + insert buckets); refresh lock SET NX EX + Lua CAD, fail-open on Redis outage; backoff + Retry-After honored (data_api.py, analytics.py) |
| 3 Security & compliance | ok — prior SEV2 (scope stuck-wide) fixed by replace-on-grant with empty-scope guard; tokens read via `decrypt()` (oauth.py:295,354,406) and never logged (new log lines verified: creator_id + scope names / httpx exc only); per-creator isolation on every creator-scoped query; parameterized SQLAlchemy; yt-dlp own-content-only + flag-gated; no virality strings |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | n/a (no LLM calls in-slice) |
| 6 Cleanliness & typing | ok — new `_resume_offset` fully typed + docstringed; the untrue union comment replaced by an accurate replace-on-grant rationale citing the DECISIONS entry |
| 7 Error handling / API | 1 finding (cleanup, publish.py:192 untyped ValueError on final-chunk parse — carry-forward) — typed error contract otherwise holds end-to-end into the task wrapper |
| 8 Config & paths | ok — no new config this wave; `YOUTUBE_QUOTA_INSERT_DAILY_CALLS` remains in config.py + .env.example; quota keys PT-anchored; no relative paths |

## Module verdict
NEEDS-WORK — both morning SEV2s are verified fixed with regression tests (no transport
error can escape to a session-recreating retry on any exception path; scope handling now
narrows correctly, guards empty scope, and refresh cannot wipe grants); what remains is
the DECISIONS-accepted crash-window duplicate (persist `session_uri` follow-up) plus
three carried-forward cleanups.
