# notify — assessed 2026-06-24

Slice: `notify/__init__.py`, `notify/copy.py`, `notify/dedupe.py`, `notify/mailer.py`,
`notify/templates/*` (clips_ready, dna_built, refund_issued, reauth_required,
trial_ending, balance_low — each `.txt` + `.html`).

Verification done by reading the sole production caller
(`worker/tasks.py::_send_notification_async`, lines 3942-4084) and the worker event-loop
runtime (`worker/celery_app.py:77 run_async`), and by rendering the templates with the
*exact* context the caller supplies. ruff + mypy pass clean on the slice; `tests/test_mailer.py`
is 11/11 green — but the tests hand-feed template variables the real caller never passes, so
they give false confidence (see SEV1 below).

## Findings

- [SEV1] notify/templates/*.txt+*.html (all 6, esp. clips_ready.txt:1,5,7 and every
  `{{ app_url }}` ref) — templates reference variables the production caller does not supply.
  The only live caller is `worker/tasks.py:4056` which renders with
  `context={"creator": creator, **payload}`; for `clips_ready` the payload is just
  `{"clip_count": N}` (worker/tasks.py:1930). `clips_ready` templates use `{{ creator_name }}`,
  `{{ video_title }}`, `{{ review_url }}` (none supplied → empty), and *every* lifecycle
  template uses `{{ app_url }}` (never supplied → empty). Because the Jinja2 Environment at
  `notify/mailer.py:37` uses the default silent `Undefined`, these render to empty strings
  instead of raising. Rendered with the real context, `clips_ready.txt` produces subject
  "Your clips are ready — " (trailing dash, no title), greeting "Hi ," and link
  "Review your clips: " (empty); `trial_ending.txt` produces "Add minutes: /pricing" — a
  host-less relative URL that is unclickable in an email client. Every transactional email
  ships broken under load. | fix: (a) set `undefined=StrictUndefined` on the Environment
  (mailer.py:37) so a missing variable fails the render loudly instead of silently shipping a
  blank; (b) align the caller — pass `app_url=settings.APP_URL` (or the canonical base-URL
  setting) plus `creator_name`/`video_title`/`review_url` into the `clips_ready` context, and
  switch the lifecycle templates that say `{{ creator_name }}` vs `{{ creator.channel_title }}`
  onto one convention; (c) add a render test that uses the *production* context shape
  (`{"creator": <obj>, "clip_count": N}`), not the hand-fed test context, and asserts the
  subject and links are non-empty.

- [SEV2] notify/mailer.py:182 (with :197-209 `_extract_subject`) — the `Subject:` line is
  parsed out for the header but never removed from the body, so `params["text"]` (and the
  console preview) ship with a literal `Subject: Your clips are ready — V` as the first line
  of the message the recipient reads. Confirmed by rendering: first body line is
  `'Subject: Your clips are ready — V'`. | fix: have `_render` (or `_extract_subject`) return
  `(subject, body_without_subject_line)` and pass the stripped body to Resend `text`/console;
  drop the `Subject:` line from the rendered body before send.

- [SEV2] notify/mailer.py:189 — the Resend (production) path logs the recipient email at INFO
  (`"... to=%s ..."` with `to`). Email address is PII; the rubric requires no PII in any log
  line, and this fires in prod (unlike `_send_console`, which is a dev-only sink). | fix: drop
  `to` from the resend log line, or hash/redact it (log `resend_id` + `idempotency_key` only,
  which already correlate the send). Console-path `to`+`body_preview` (line 151) is acceptable
  as a dev sink but consider gating it behind a debug flag.

- [SEV2] notify/mailer.py:160-188 `_send_resend` — `resend.Emails.send()` is a synchronous,
  `requests`-backed blocking HTTP call, and it is invoked from inside the worker's coroutine
  (`worker/tasks.py:4053`, run on the per-worker event loop via
  `worker/celery_app.py:87 run_until_complete`) with no `asyncio.to_thread` offload. This is
  the "sync/blocking call inside an async def" anti-pattern the scale rubric flags. With the
  default prefork-one-task-per-loop model the blast radius is bounded (one task owns the loop
  for its duration), but it blocks the loop for the full network round-trip and would stall
  siblings the moment notifications are ever fanned out concurrently on one loop.
  (loop-contention impact is needs-runtime-confirmation; the blocking call itself is certain.)
  | fix: at the call site wrap as `await asyncio.to_thread(mailer_send, ...)`, or expose an
  async `send` that uses an `httpx.AsyncClient` module-level singleton against the Resend REST
  API instead of the sync SDK.

- [SEV2] notify/copy.py:69-80 vs notify/templates/ — `welcome` and `catalog_sync_done` are
  defined in `COPY` (and have in-app copy in `worker/tasks.py::_build_inapp_notification`) but
  have NO paired `.txt`/`.html` template files. If `send_notification` is ever called for
  those event types with `email_transactional=True`, `_render` raises `jinja2.TemplateNotFound`
  → caught at worker/tasks.py:4065 → delivery marked `failed`, email silently never sent. No
  live call site emails them today, so it is latent, not active. | fix: either add
  `welcome.{txt,html}` + `catalog_sync_done.{txt,html}` templates, or have `send()` fall back
  to a generic template (and assert in a test that every `COPY` key and every emailable
  `event_type` has a matching template pair).

- [cleanup] notify/copy.py:28-81 — the `COPY` dict (per-event `subject`+`body`) is not consumed
  by the mailer at runtime: subjects come from each template's `Subject:` line via
  `_extract_subject`, and bodies come from the templates. `COPY` is read only by tests
  (`tests/test_compliance_no_virality.py`, `tests/test_notifications_triggers.py`). It is thus
  a third, parallel copy source that can drift from the templates and from the in-app `_COPY`
  in `worker/tasks.py:4108` (the bodies already differ). DRY risk for the honesty constraint,
  bounded only because the no-virality test scans COPY. | fix: make `COPY` the single source —
  either render templates from `COPY` (inject `subject`/`body` into the Jinja context) or
  delete `COPY` and derive the test's no-virality scan from the rendered templates directly.

- [cleanup] notify/mailer.py:90,108 — `_render(template: str, context: dict)` and the public
  `send(..., context: dict, ...)` use bare `dict` for the template context crossing the public
  boundary; value type is unparameterized. mypy passes but the rubric asks to flag loose dicts
  at the surface. | fix: annotate `context: dict[str, Any]` (add `from typing import Any`).

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — no DB sessions / file handles / subprocesses in slice; Resend SDK init is a module-level api_key assignment guarded by an idempotent `_init_resend` flag (mailer.py:50-69), correct singleton shape |
| 2 Concurrency & scale | 1 finding (SEV2) — sync blocking `resend.Emails.send` on the worker loop with no `to_thread`; loop singleton itself is fine |
| 3 Security & compliance | 1 finding (SEV2 PII: recipient email in prod log). No creator-scoped DB query in this slice (isolation lives in worker/tasks.py, another slice). No OAuth/token handling here — n/a for decrypt. dedupe key correctly contains no PII (dedupe.py). No virality promise: all 6 templates + COPY carry the disclaimer and pass the honesty scan |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | n/a (no LLM call in slice) |
| 6 Cleanliness & typing | 2 cleanups — unused/parallel `COPY` copy source (DRY); bare `dict` context typing. No TODO/print/debug; ruff + mypy clean |
| 7 Error handling / API | n/a (no FastAPI router in slice). `send()` correctly validates the idempotency key (mailer.py:77-84) and raises ValueError on unknown backend rather than silently dropping |
| 8 Config & paths | ok — `_TEMPLATES_DIR` is absolute via `Path(__file__).parent` (mailer.py:36); NOTIFY_BACKEND / RESEND_API_KEY / EMAIL_FROM all present in config.py:397-403 and .env.example:159-165 with descriptions; config fails fast when resend is selected without a key (config.py:405-419) |

## Module verdict
NEEDS-WORK — no BLOCKER and no cross-tenant leak (the slice does no DB I/O), but a SEV1
template/caller contract mismatch ships every transactional email with empty subjects and
host-less links in production (masked by tests that hand-feed the variables), plus a Subject
line leaking into the body, recipient-email PII in prod logs, a blocking SDK call on the
worker loop, and two events with no template.
