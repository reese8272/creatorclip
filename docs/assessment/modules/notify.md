# notify — assessed 2026-07-20 (post-fix); delta re-assessed 2026-07-29

## Delta 2026-07-29 (ready-pass, branch w3/ready-pass-closeout)

Only in-slice change since e92b93a: `notify/copy.py` (16-line diff, landed in W1/W2 merge
a50b332 lineage) — the `trial_ending` and `balance_low` bodies became f-strings that bind
`settings.APP_BASE_URL` at import time (`from config import settings`, copy.py:28;
`{settings.APP_BASE_URL}/pricing` at copy.py:68-69, 75).

- **"Only imported by tests" claim — VERIFIED TRUE.** Repo-wide grep: the only importers of
  `notify.copy` are tests/test_mailer.py:611, tests/test_compliance_no_virality.py:224,
  tests/test_notifications_triggers.py:573,599, tests/test_lifecycle_email.py:265.
  `notify/__init__.py` exports nothing, `notify/mailer.py` and `worker/tasks.py` never import
  it. The change is inert in production.
- **Import-time binding hazard — none in practice.** `config.settings` is a module-level
  pydantic-settings singleton instantiated on first import; `notify/mailer.py:51` already
  binds `settings.APP_BASE_URL` into the Jinja `app_url` global at import time (the cited
  precedent is real). Settings are frozen at process start, so import-time == call-time in
  prod. Test-env leakage is bounded: the unit lane renders
  `http://localhost:8000/pricing` (APP_BASE_URL default, config.py:573), and no test asserts
  exact body text — the COPY-importing tests scan for virality phrases
  (test_compliance_no_virality.py, test_lifecycle_email.py:265-271) or check template-file
  existence (test_mailer.py:611-625), so no brittleness. One latent note: if COPY is ever
  wired into a production render path, a test that monkeypatches `settings.APP_BASE_URL`
  after import would see the stale bound value — same caveat mailer.py's `app_url` global
  already carries.
- **No production gap behind the fix**: the real trial_ending/balance_low email links were
  already absolute via `{{ app_url }}/pricing` in the Jinja templates
  (notify/templates/trial_ending.{txt,html}:7/16, balance_low.{txt,html}:7/15); the in-app
  `_COPY` fallback bodies (worker/tasks.py:5510-5519) carry no URL at all and use a relative
  `link_url` of `/app/dashboard`, which is correct in-app. So the wave's effort landed
  entirely in dead code — the first carry-forward cleanup below gets sharper, not resolved:
  copy.py's docstring still falsely claims Jinja templates do `from notify.copy import COPY`
  (copy.py:10-14 — templates cannot execute Python imports), and the NEW paragraph
  (copy.py:20-23) claims render-safety for a render path that does not exist.
- No other in-slice change (`git diff e92b93a..HEAD -- notify/` touches only copy.py);
  mailer, dedupe, templates, and the worker caller are byte-identical to the 2026-07-20 run.

**Delta verdict: clean (unchanged)** — the only change is a correct-but-inert edit to dead
test-only code; all four 2026-07-20 cleanups stand, with the copy.py one now reading: delete
copy.py and repoint the four test files at the templates, or actually wire it in as the
single copy source — polishing it while dead just deepens the triplication.

---

# Assessment of record — 2026-07-20 (post-fix)

**Post-fix status:** clean, unchanged in-slice — `git diff ca3305c..e92b93a -- notify/` is
empty; the only wave touch was `tests/test_mailer.py` (Issue 358 dummy-Fernet-key fixture
fix, test-only). Verdict and all cleanups stand as written below.

Slice: `notify/__init__.py`, `notify/copy.py`, `notify/dedupe.py`, `notify/mailer.py`,
`notify/templates/*` (10 `.txt` + 10 `.html`). Per this run's method additions, also traced the
real caller `worker.tasks.send_notification` → `_send_notification_async`
(worker/tasks.py:4875-5118) and the Issue 246 sunset-cap code it feeds
(worker/tasks.py:2930-3082). `notify/` itself is byte-identical to the 2026-07-01 run
(`git diff f70a857..HEAD -- notify/` is empty); all changes landed in the caller + config.

## Resolved since 2026-07-01
- **SEV2 — no-timeout blocking Resend send (shared with worker SEV1) — FIXED** (Issue 349).
  Exactly the prescribed fix shape landed in the caller: worker/tasks.py:5089-5098 wraps the
  sync `mailer_send` in `asyncio.wait_for(asyncio.to_thread(...), timeout=_settings.RESEND_TIMEOUT_S)`,
  and the DB session is committed and closed BEFORE the send (worker/tasks.py:5077-5078) so no
  connection is held during the HTTP call; the failure path reopens a fresh session to mark the
  delivery `failed` (worker/tasks.py:5113-5118). `RESEND_TIMEOUT_S: int = 10` added at
  config.py:723 and .env.example:222. Additionally verified in the pinned SDK itself
  (resend==2.32.2, requirements.txt:125, installed): the default `RequestsClient` has a
  built-in HTTP timeout — `def __init__(self, timeout: int = 30)` in
  `resend/http_client_requests.py`, passed as `requests.request(..., timeout=self._timeout)` —
  so a `wait_for`-cancelled send leaves its offload thread hung at most ~30 s, not
  indefinitely. No executor-exhaustion residue; the worker-loop wedge is gone.
- **SEV2 — `EMAIL_FROM` not fail-fast validated — FIXED.** `_validate_notify_backend`
  (config.py:784-805) now raises at startup when `NOTIFY_BACKEND='resend'` and `EMAIL_FROM`
  is empty (config.py:801-805), alongside the existing `RESEND_API_KEY` check (config.py:792).

## Issue 246 re-engagement sunset cap — verified correct (landed 93d9218, since f70a857)
- **Cap logic**: `_reengagement_sunset` (worker/tasks.py:2963-2986) counts
  `notification_deliveries` rows for the creator with `event_type == "re_engagement"` and
  sunsets at `count >= LIFECYCLE_REENGAGE_MAX_ATTEMPTS` (default 3, config.py:594,
  .env.example:185 with description). Counting *attempted* sends (ledger rows include
  later-failed sends; opt-out/CAN-SPAM skips never reach the ledger) is an explicit,
  documented tradeoff in the docstring — correct.
- **Per-creator scoping**: `WHERE NotificationDelivery.creator_id == creator_id`
  (worker/tasks.py:2983); `creator_id` is indexed (models.py:1359) so the count is cheap.
- **Idempotency**: the sweep is read-only + enqueue-only; the actual send dedupes via the
  UNIQUE `dedupe_key = sha256(creator:event:period_bucket)` INSERT (worker/tasks.py:5022-5043),
  so same-window re-enqueues and Celery redeliveries are no-ops. The 14-day `period_bucket`
  (worker/tasks.py:3055-3056) plus the shared 48 h `_lifecycle_capped` budget bound cadence.
- **Tests**: integration test at-cap-skipped / under-cap-enqueued
  (tests/test_lifecycle_integration.py:215-251) + unit coverage
  (tests/test_lifecycle_email.py:171+).

## Findings
- [cleanup] notify/copy.py:28-99 — (carry-forward) the `COPY` dict is still dead production
  code: imported only by tests (tests/test_mailer.py:611, tests/test_compliance_no_virality.py:218,
  tests/test_notifications_triggers.py:568, tests/test_lifecycle_email.py:265), never by the
  mailer or task; the docstring (copy.py:10-18) still falsely claims templates do
  `from notify.copy import COPY`. Copy remains triplicated (Jinja templates, copy.py, inline
  `_COPY` at worker/tasks.py:5142+). Not a compliance gap — the structural no-virality test
  scans the real templates. | fix: delete copy.py and repoint those tests at the templates, or
  make it the single source both render paths consume; at minimum fix the docstring.
- [cleanup] notify/mailer.py:102,156,249 — (carry-forward) bare `dict` annotations on
  `context` (in `_render` and public `send`) and `params_dict` drop key/value typing at the
  public boundary. | fix: `context: dict[str, object]`, `params_dict: dict[str, object]`.
- [cleanup] notify/mailer.py:260-266 + models.py:1346,1372 — `provider_message_id` is never
  populated anywhere: `_send_resend` logs `resend_id` then discards it and `send()` returns
  `None`, yet the NotificationDelivery docstring claims the column "stores the Resend message
  id returned on success". Diagnosing deliverability requires grepping worker logs instead of
  the ledger. | fix: have `send()` return the provider id (`str | None`) and set
  `delivery.provider_message_id` in step 8 of `_send_notification_async`, or drop the column
  claim.
- [cleanup] worker/tasks.py:3060-3076 + 5002-5008 — the daily sweep re-enqueues a no-op
  `send_notification` task every day, forever, for each dormant creator who opted out of
  lifecycle email: the opt-out path returns BEFORE the ledger insert, so `_lifecycle_capped`
  and `_reengagement_sunset` never see a row and cannot gate them. Bounded (1 task/creator/day,
  no email sent) — waste + log noise only at beta scale. | fix: join
  `NotificationPreference.email_lifecycle.is_(True)` (outer-join, default-row-absent = opted
  in) into the sweep cohort queries. Related: the sweep runs 2 gate queries per candidate
  (N+1); fine for ≤100 creators, batch via a single grouped count query if the beta outgrows
  that.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — Jinja env + Resend api_key are module-level singletons (mailer.py:37, 62-81); no DB/file handles in slice; caller now frees the DB session before the send (worker/tasks.py:5077) |
| 2 Concurrency & scale | ok — prior blocking-send SEV2 fixed (wait_for + to_thread + SDK-level 30 s timeout); sweep N+1 noted under cleanup, acceptable at beta scale |
| 3 Security & compliance | ok — recipient address omitted from both console and resend log paths (mailer.py:214, 261); dedupe key is PII-free sha256 of IDs (dedupe.py:53-54); no virality promise in copy.py, templates, or `_COPY` (gated by tests/test_compliance_no_virality.py); RFC 8058 one-click unsubscribe on lifecycle sends; CAN-SPAM MAILING_ADDRESS gate enforced at sweep and task level |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | n/a (no LLM calls) |
| 6 Cleanliness & typing | 4 cleanup — dead/triplicated COPY + false docstring; bare `dict` hints; never-written provider_message_id; opted-out re-enqueue churn. No TODO/print/debug |
| 7 Error handling / API | n/a (not a router) — `send()` validates the idempotency key (mailer.py:89-96) and raises ValueError on unknown backend (mailer.py:200-203) |
| 8 Config & paths | ok — EMAIL_FROM now fail-fast validated (config.py:801); RESEND_TIMEOUT_S + LIFECYCLE_REENGAGE_MAX_ATTEMPTS in config.py and .env.example with descriptions; paths absolute (`Path(__file__).parent`, mailer.py:36) |

## Module verdict
clean — both 2026-07-01 SEV2s are genuinely fixed (timeout/offload via Issue 349 verified down
to the SDK's 30 s RequestsClient default; EMAIL_FROM fail-fast in config), the Issue 246
sunset cap is correct (per-creator scoped, ledger-counted, dedupe-idempotent, test-covered);
only four cleanups remain, none with behavior risk.
