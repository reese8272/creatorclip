# notify — assessed 2026-07-01

Slice: `notify/copy.py`, `notify/dedupe.py`, `notify/mailer.py`, `notify/__init__.py`,
`notify/templates/*` (10 `.txt` + 10 `.html`). Traced the real caller
`worker.tasks.send_notification` → `_send_notification_async` (worker/tasks.py:4441-4660)
and every `send_notification.delay(...)` call site.

## Prior findings — RE-VERIFIED against current code
The 2026-06-24 assessment listed a SEV1 + four SEV2s. Re-checked each against the live code;
**all five are fixed**:
- SEV1 (silent `Undefined` → blank subject / "Hi ,") — FIXED. Env now uses
  `undefined=StrictUndefined` (mailer.py:46) AND the `clips_ready` caller supplies the vars
  (see below).
- SEV2 `Subject:` line leaking into body — FIXED. `_strip_subject_line` (mailer.py:122-146)
  removes it in `_render` (mailer.py:117).
- SEV2 recipient email logged in prod — FIXED. `_send_resend` logs only `resend_id` +
  `idempotency_key`, address explicitly omitted (mailer.py:261-266).
- SEV2 missing `welcome`/`catalog_sync_done` templates — FIXED. Both `.txt`+`.html` now exist.

### clips_ready SEV1 specifically — verified fixed
The prior blank-subject/host-less-link defect was that `clips_ready`/lifecycle templates
referenced `creator_name`/`video_title`/`review_url`/`app_url` the caller never passed. Now:
the real caller passes all four for clips_ready — worker/tasks.py:2304-2315 sends
`{"clip_count": len(clips), "creator_name": ..., "video_title": ...,
"review_url": f"{settings.APP_BASE_URL}/app/review"}`, merged at worker/tasks.py:4629
(`{"creator": creator, **payload}`). `app_url`/`mailing_address` are Jinja globals
(mailer.py:51/54); `unsubscribe_url` is passed on lifecycle sends (worker/tasks.py:4629);
`creator` (ORM obj) is always passed. Cross-checked every template's `{{ }}` vars against the
supplied context — all satisfied. With `StrictUndefined`, any regression would now raise
`UndefinedError` at render instead of silently shipping a blank. Verified against Jinja2 docs:
StrictUndefined "barks on print and iteration… you can do nothing with it except checking if
it's defined", and `str(foo)` raises `jinja2.exceptions.UndefinedError`
(https://jinja.palletsprojects.com/en/stable/api/#jinja2.StrictUndefined, fetched 2026-07-01).

### Resend SDK call shape — verified correct
`_send_resend` uses `resend.Emails.send(params, options)` with
`options = {"idempotency_key": key}` (mailer.py:258-260). Confirmed as the current official
Python-SDK signature: `options: resend.Emails.SendOptions = {"idempotency_key": ...};
resend.Emails.send(params, options)`, and custom `headers` go in the SendParams dict — both
match the code. Sources: https://resend.com/docs/dashboard/emails/idempotency-keys and
https://pypi.org/project/resend/ (fetched 2026-07-01).

## Findings

- [SEV2] notify/mailer.py:246-260 — the Resend send has **no HTTP timeout** and is a
  synchronous, `requests`-backed blocking call executed from inside `async def
  _send_notification_async` via `run_until_complete` on the worker's singleton event loop
  (worker/celery_app.py:94-96), with no `asyncio.to_thread` offload. A stalled Resend
  connection blocks the whole worker process. It is bounded — not infinite — only by Celery's
  hard `task_time_limit` (~3300s = `CELERY_SOFT_TIME_LIMIT_S` 3000 + 300, config.py:441 +
  celery_app.py:63), i.e. one hung send can wedge a worker for up to ~55 min. The Resend
  Python SDK sets timeouts only at the HTTP-client level, never per call. | fix: (a) set an
  explicit client timeout once in `_init_resend()` (e.g.
  `resend.default_async_http_client = resend.HTTPXClient(timeout=10)` or the sync-client
  equivalent), and (b) offload the blocking call: `await asyncio.to_thread(mailer_send, ...)`
  at worker/tasks.py:4626 so a slow send never holds the loop. Source: timeout is client-level
  per https://pypi.org/project/resend/ (fetched 2026-07-01).
  (needs-runtime-confirmation on exact worst-case hang.)

- [SEV2] config.py:687-691 — `_validate_notify_backend` fails fast on a missing
  `RESEND_API_KEY` when `NOTIFY_BACKEND='resend'` but does **not** validate `EMAIL_FROM`
  (default `""`, config.py:638). With the resend backend and empty `EMAIL_FROM`, `_send_resend`
  posts `"from": ""` (mailer.py:250) → Resend 422 → swallowed by the broad `except Exception`
  at worker/tasks.py:4641, so every transactional email is silently marked `failed` with none
  delivered and no startup signal. | fix: extend the validator — `if self.NOTIFY_BACKEND ==
  "resend" and not self.EMAIL_FROM: raise ValueError("NOTIFY_BACKEND='resend' requires
  EMAIL_FROM")`. (config.py is outside the notify/ dir but is the fail-fast guard this module
  depends on.)

- [cleanup] notify/copy.py:28-99 — the `COPY` dict is **dead production code**: it is imported
  only by tests (test_mailer.py, test_lifecycle_email.py, test_compliance_no_virality.py),
  never by the mailer or task. The email path renders Jinja templates; the in-app path uses a
  *separate* inline `_COPY` dict at worker/tasks.py:4684+. Copy now lives in three
  unsynchronised sources, and copy.py's docstring (lines 10-18) falsely claims templates do
  `from notify.copy import COPY; subject = COPY[...]` — they don't. Not a compliance gap:
  `test_no_virality_in_notification_templates` (tests/test_compliance_no_virality.py:190) does
  scan the real templates, so the sent artifact is gated. | fix: delete copy.py and repoint
  those tests at the templates, or make copy.py the single source both render paths consume; at
  minimum fix the misleading docstring.

- [cleanup] notify/mailer.py:102,152,249,258-259 — bare `dict` annotations on `context` (in
  `_render` and public `send`) and on `params_dict`/`options` drop key/value typing mypy could
  otherwise check at the public boundary. | fix: `context: dict[str, object]`,
  `params_dict: dict[str, object]`.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — no DB/file handles here; Resend `api_key` set once via idempotent `_init_resend()` guard (mailer.py:62-81); Jinja env is a module-level singleton |
| 2 Concurrency & scale | 1 SEV2 — no-timeout, sync-blocking Resend send on the worker loop |
| 3 Security & compliance | ok — recipient email omitted from prod logs (verified mailer.py:261-266); dedupe key carries no PII (dedupe.py); no virality (templates gated by structural test); no creator-scoped SQL / token handling in this slice |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | n/a (no LLM calls) |
| 6 Cleanliness & typing | 2 cleanup — dead/triplicated `COPY` dict + false docstring; bare `dict` hints. No TODO/print/debug |
| 7 Error handling / API | n/a (not a router). `send()` validates the idempotency key (mailer.py:89-96) and raises `ValueError` on unknown backend |
| 8 Config & paths | 1 SEV2 — `EMAIL_FROM` not fail-fast validated; paths absolute (`Path(__file__).parent`, mailer.py:36); NOTIFY_BACKEND/RESEND_API_KEY/EMAIL_FROM/MAILING_ADDRESS all in config.py + .env.example with descriptions |

## Module verdict
NEEDS-WORK — no BLOCKER, and every prior finding (incl. the clips_ready SEV1) is genuinely
fixed; two SEV2s remain: the blocking Resend send has no HTTP timeout so a network stall can
wedge a worker up to the ~55 min hard limit, and `EMAIL_FROM` is not validated at startup so a
resend-backend misconfig silently drops all transactional mail.
