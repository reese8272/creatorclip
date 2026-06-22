# Research Brief 11 — Notifications & Lifecycle Communications (Issue 176)

**Author:** read-only research agent · **Date:** 2026-06-22
**Drives:** Issue 176 (Phase 1 CHECK) → sub-issues below
**Scope:** transactional email + in-app notifications + (optional) web push + a minimal honest
lifecycle sequence. Telemetry split honored: this brief owns **creator-facing comms** ("your
clips are ready", "your DNA is built", "balance low"); **operator observability** is prompt 05 /
Issue 170 and **product/funnel analytics** is prompt 07 / Issue 172 — both reuse the same
event sink, so I cross-reference rather than duplicate.
**Method:** current industry standard researched first (links inline); every repo claim cited
`file_path:line`. Where I could not verify a claim, I say so.

> Guardrails this brief respects throughout: notification copy **never promises virality**
> ("your clips are ready", not "your viral clips are ready") — the same honesty constraint the
> structural test enforces in-app (`CLAUDE.md`, "Honesty Constraint"); **no token/secret/PII** in
> any message body or provider payload (mirrors the `event_log._redact()` boundary,
> `event_log.py:39-84`); per-creator isolation on every send (`docs/SOT.md:444`); YouTube
> analytics data is never forwarded to an email provider.

---

## 1. Executive summary — highest-leverage findings

1. **The gap is real and it is an activation leak, but the predecessor issues are already
   filed.** There is **zero out-of-app comms** — no SMTP/SendGrid/Postmark/Resend/SES integration
   anywhere in product code (confirmed: a repo-wide grep for provider names hits only test files
   and `boto3`, which is R2 storage only — `worker/storage.py:21`, `routers/auth.py:260`). The
   only feedback today is **in-app SSE while the tab is open**: progress events live on a
   per-task Redis Stream with a **1-hour TTL** (`worker/progress.py:53`, `_STREAM_TTL_SECONDS`)
   and the SSE endpoint requires an authenticated, owner-matched connection
   (`routers/tasks.py:117-131`). A creator who closes the tab during a minutes-to-hours pipeline
   (`docs/SOT.md:389-435`) learns nothing. **Crucially, two predecessor issues already exist and
   are 🔲 Not started: Issue 80 "Transactional email infrastructure" (`docs/issues.md:1008`,
   recommends Resend, first consumer = refund email) and Issue 81 "In-app notifications surface"
   (`docs/issues.md:1040`).** This brief's job is to *resolve their open Phase-1 questions with
   current evidence and dependency-order them under Issue 176*, not re-file them.

2. **Recommended provider: Resend, with the local-dev path being a console sink.** Resend is the
   modern default for a Python/FastAPI beta — official Python SDK (`pip install resend`,
   [docs](https://resend.com/docs/send-with-python)), 3,000 emails/mo free tier, and — decisively
   for our retry-heavy Celery world — **native idempotency keys** on `POST /emails`
   ([Resend idempotency](https://resend.com/blog/engineering-idempotency-keys), keys up to 256
   chars). The honest tradeoff to log in DECISIONS: Resend rides Amazon SES underneath and does
   **not** enforce transactional/marketing stream separation by default, whereas **Postmark** is
   the deliverability leader precisely because it isolates streams
   ([buildmvpfast 2026](https://www.buildmvpfast.com/blog/resend-vs-ses-vs-postmark-transactional-email-deliverability-saas-2026),
   [Postmark](https://postmarkapp.com/compare/resend-alternative)). For a beta sending almost
   entirely transactional mail, Resend's DX + idempotency win; Postmark is the documented fallback
   if inbox placement disappoints. SES-direct is cheapest at scale (~$10 vs ~$40/100k) but the raw
   API + reputation management is not worth it pre-scale.

3. **Send from Celery, never the request path — and we already have the exact pattern to copy.**
   The standard for at-least-once queues is an idempotent task keyed on a deterministic id
   ([Celery idempotency](https://medium.com/@hjparmar1944/fastapi-celery-work-queues-idempotent-tasks-and-retries-that-dont-duplicate-d05e820c904b)).
   Our worker tasks already do this everywhere (status-check-then-skip at entry,
   `worker/tasks.py:579-596`; `build_job_id` idempotency under an advisory lock,
   `worker/tasks.py:1149-1159`). A `send_email` task that derives its idempotency key from
   `(creator_id, event_type, entity_id)` and passes it straight to Resend's `Idempotency-Key`
   gives **two layers** of dedupe (our DB row + the provider) for free.

4. **The notification trigger points already exist as terminal events — wiring, not new
   infrastructure.** Every pipeline stage emits a terminal `done`/`error` on the per-task stream:
   clips ready (`worker/tasks.py:1468`), DNA built (`worker/tasks.py:1254`), render done
   (`worker/tasks.py:853`), and the various failure emits. The trial watchdog already runs daily
   (`worker/tasks.py:261`, `expire_trials`) and `LOW_BALANCE_THRESHOLD_MINUTES` is already a config
   (`config.py:231`). These are the natural fire points for a notification fan-out.

5. **In-app notifications should be a thin persistent table, NOT a reuse of the ephemeral SSE
   stream.** The SSE/Redis stream is correct for *live progress* but wrong for *durable "you have a
   result"* — it has a 1h TTL and requires an open connection. The event sink that *is* durable
   (`event_logs`, `event_log.py`) is deliberately PII-redacted, no-RLS, operator-only
   (`docs/COMPLIANCE.md:87`) — repurposing it for creator-facing notifications would violate its
   stated contract. The standard pattern is a dedicated `notifications` table read by a poll on
   page load (Issue 81 already proposes exactly this, `docs/issues.md:1058-1067`); SSE delivery is a
   Phase-3 nicety. Web push (VAPID) is the *complementary* out-of-app channel and is genuinely
   optional for beta (see §4).

6. **Billing reality changes the trigger matrix: there is no dunning.** Billing is **one-time
   minute-pack Stripe Checkout — "no subscriptions, no meters"** (`billing/stripe_client.py:2-4`).
   So "failed payment / dunning email" from the prompt **does not apply** — there are no recurring
   charges to fail. The monetization triggers that *do* apply are **low balance** and **trial
   ending** (and a purchase **receipt**, which Stripe Checkout already emails natively — we should
   not duplicate it). This is a cross-reference correction to prompt 06 / Issue 171.

---

## 2. Trigger × channel matrix

Channel legend: **E**=email, **A**=in-app notification (durable, poll), **P**=web push (optional,
Phase 3), **—**=intentionally not notified. "SSE" = the existing live-progress stream, which stays
as-is for tab-open users and is *additive* to the below, not a substitute.

| Event | Repo fire point | Email | In-app | Push | Urgency | Honest copy intent |
|-------|-----------------|:-----:|:------:|:----:|---------|--------------------|
| **Clips ready** (upload→clips done) | `worker/tasks.py:1468` (`done`, generate_clips) | E | A | P | High | "Your clips are ready to review." Never "viral". |
| **DNA built / brief ready** | `worker/tasks.py:1254` (`done`, build_dna) | E | A | — | High | "We've built your channel DNA — review and confirm it." |
| **Catalog sync done** (onboarding) | `sync_channel_catalog` done (`worker/tasks.py:286`) | — | A | — | Low | In-app only; it's a sub-step, not a finish line. |
| **Render done** (single re-render/clean/edit) | `worker/tasks.py:853` / `:994` / `:1081` | — | A | P | Med | "Your edited clip is ready." Push only if push opted-in. |
| **Ingest / pipeline failed (terminal)** | `RefundOnFailureTask.on_failure` (`worker/tasks.py:89`) | E | A | — | High | "We couldn't process *<title>* — your minutes were refunded." (refund is automatic, `docs/issues.md:986`) |
| **Trial ending (N days)** | `expire_trials` beat (`worker/tasks.py:261`); `TRIAL_DURATION_DAYS` (`config.py:228`) | E | A | — | Med | "Your free trial ends in N days." No virality, no dark-pattern urgency. |
| **Balance low** | threshold exists: `LOW_BALANCE_THRESHOLD_MINUTES` (`config.py:231`); fire on deduct in `billing/ledger.py` | E | A | — | Med | "You have N minutes left." |
| **Purchase receipt** | Stripe Checkout `checkout.session.completed` (`routers/billing.py:160`) | — (Stripe native) | A | — | Low | Stripe emails the receipt; we add an in-app "X minutes added" only. |
| **YouTube re-auth needed** (token revoked) | token-refresh failure path (`youtube/oauth.py`; `sync_channel_catalog` `YouTubeAuthError`, `worker/tasks.py:299`) | E | A | — | High | "Reconnect your YouTube account to keep analytics fresh." |
| **Account actions** (deletion confirm) | `DELETE /auth/me` (CLAUDE.md launch reqs) | E | — | — | High | "Your account and data have been deleted." Compliance receipt. |
| **Welcome** (first OAuth login) | `creator.email` set (`youtube/oauth.py:183`) | E | — | — | Low | "Welcome — here's how AutoClip learns your channel." |
| **First-clip nudge / re-engagement** | lifecycle (see §4) | E | — | — | Low | Triggered by *product state* (no clips reviewed in N days), not a timer. |

Notes:
- **Transactional vs. lifecycle is the legal hinge.** Under CAN-SPAM, the rows above the "Welcome"
  divider are *transactional/relationship* messages (confirm/facilitate a transaction the creator
  initiated) and may be sent without an unsubscribe link
  ([FTC CAN-SPAM](https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business)).
  **Welcome / first-clip / re-engagement are commercial-leaning lifecycle** and MUST carry an
  unsubscribe + physical address, and under GDPR rest on *legitimate interest* with an easy opt-out
  ([TermsFeed GDPR transactional](https://www.termsfeed.com/blog/gdpr-transactional-emails/)).
- Every notification is **product-event triggered, not elapsed-time triggered** — the modern
  lifecycle standard ([digitalapplied 2026](https://www.digitalapplied.com/blog/saas-customer-onboarding-email-sequence-2026-crm-playbook)).
  We already emit those product events; we just need a fan-out.

---

## 3. Architecture recommendation

### 3.1 Provider + deliverability
- **Resend** as the provider; `pip install resend` pinned `==` in `requirements.txt`
  ([PyPI](https://pypi.org/project/resend/)). Module-level singleton client in `clients.py`
  (matches the Anthropic/Voyage/storage singleton convention, `docs/SOT.md:93`).
- **Deliverability setup on `autoclip.studio`** (DNS, one-time, in `docs/SECRETS.md`/RUNBOOKS):
  SPF + **2048-bit DKIM** + **DMARC starting at `p=none` with rua reporting**, then tighten to
  `quarantine`→`reject` only after the reports are clean — the canonical safe rollout
  ([emailonacid](https://www.emailonacid.com/blog/article/email-deliverability/email-authentication-protocols/),
  [Mailgun 2025 reqs](https://www.mailgun.com/state-of-email-deliverability/chapter/email-authentication-requirements/)).
  This is now table-stakes: Google/Yahoo/Microsoft require authentication for bulk senders and
  authenticated mail is ~2.7× more likely to inbox. Authentication is **required before any
  send goes out**, even in beta.

### 3.2 Send pattern (Celery, idempotent, deduped)
```
domain event (task done / beat / deduct)
   │  enqueue send_notification.delay(creator_id, event_type, entity_id, payload)
   ▼
send_notification  (new Celery task, @celery.task, max_retries + retry-safe)
   1. load creator + check notification_preferences (skip if opted out / wrong channel)
   2. compute dedupe key = sha256(f"{creator_id}:{event_type}:{entity_id}")
   3. INSERT notification_deliveries row (UNIQUE on dedupe_key) → IntegrityError = already sent, skip
      (this is the Inbox/idempotent-consumer pattern; same shape as build_job_id, worker/tasks.py:1149)
   4. render template (text + html), strip any token/PII (reuse the _redact discipline)
   5. resend.Emails.send(..., idempotency_key=dedupe_key)   ← provider-side second layer
   6. on success: also INSERT a `notifications` row (in-app) for the same event
```
- **Never on the request path** — the API enqueues; the worker sends
  ([Celery best practice](https://oneuptime.com/blog/post/2025-01-06-python-celery-redis-job-queue/view)).
  The fan-out call sites are the existing terminal-event emits (§2 column 2); add one
  `send_notification.delay(...)` next to each `aemit(..., "done"/"error", ...)`.
- **Idempotency is double-layered**: our `notification_deliveries.dedupe_key` UNIQUE row +
  Resend's `Idempotency-Key`. A Celery redelivery or a duplicate beat tick cannot double-send.
- **Templating**: start with **Jinja2** (already a transitive dep via FastAPI ecosystem; confirm
  before pinning) for text+html bodies in `notify/templates/`. Issue 80 floated f-strings (KISS)
  vs Jinja2 vs MJML — recommend Jinja2: f-strings don't scale past 2 templates, MJML is overkill
  for a beta. Log this choice in DECISIONS.
- **Local-dev / test sink**: a `NOTIFY_BACKEND=console|resend` switch (default `console` in dev)
  that logs the rendered body instead of calling Resend — so the full pytest suite never hits the
  live provider (mirrors the "never hit live YouTube API in CI" rule, `CLAUDE.md` Testing Rules).

### 3.3 Data model (new tables/columns)
```sql
notification_preferences          -- one row per creator; consent + per-category channel opt-out
  creator_id (FK, PK), 
  email_transactional bool default true,   -- legally always-on for true transactional; UI shows but locks
  email_lifecycle bool default true,       -- welcome / nudge / re-engagement (unsubscribable)
  inapp_enabled bool default true,
  push_enabled bool default false,
  unsubscribe_token (uuid, unique),        -- one-click unsubscribe link, no auth required
  updated_at

notification_deliveries           -- idempotency ledger (Inbox pattern); also the audit trail
  id, creator_id (FK), event_type, entity_id,
  channel (email/inapp/push),
  dedupe_key (UNIQUE),             -- sha256(creator_id:event_type:entity_id)
  provider_message_id,             -- Resend id, for deliverability debugging (no PII)
  status (sent/skipped/failed), created_at

notifications                     -- in-app center; Issue 81's table, kept distinct from event_logs
  id, creator_id (FK), kind, title, body, link_url,
  seen_at (NULL = unread), dismissed_at, created_at
  -- RLS tenant_isolation policy (mirror chat_conversations, docs/SOT.md:379) + app-layer creator filter

push_subscriptions                -- ONLY if web push is approved (Phase 3)
  id, creator_id (FK), endpoint, p256dh, auth, user_agent, created_at
```
- `creators.email` already exists (`models.py:121`, set from Google userinfo at first login
  `youtube/oauth.py:183`) — no schema change needed to *address* mail; consent state is the new
  surface. Per-creator isolation: every notify query filters `creator_id`, and `notifications`
  carries an RLS policy like `chat_conversations` (`docs/SOT.md:379`) so creator A can never read
  creator B's inbox.
- **Unsubscribe**: the `unsubscribe_token` powers a no-auth `GET /unsubscribe/{token}` that flips
  `email_lifecycle=false` and is honored within 10 business days / kept live ≥30 days
  ([FTC](https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business)).
  RFC 8058 **List-Unsubscribe + List-Unsubscribe-Post** headers should be set on lifecycle mail
  (one-click) — required by bulk-sender rules.

### 3.4 In-app + web push (reuse, don't reinvent)
- **In-app**: `GET /api/notifications` (poll on load) + `POST /api/notifications/{id}/dismiss`,
  exactly as Issue 81 specs (`docs/issues.md:1066`). The React SPA already has the activity-panel
  shell concept (`static/activeTasks.js`, `activityPanel.js`, `docs/SOT.md:190-191`) and a
  TanStack-Query data layer — a notifications query slots in there. **Do not** push these through
  the per-task SSE stream (wrong TTL/lifetime). A later SSE *fan-out channel* keyed on
  `creator_id` (not `task_id`) could push unread-count deltas — Phase 3, optional, and it would
  reuse `worker/progress.py`'s Redis-Streams machinery, not duplicate it.
- **Web push (VAPID)**: the open standard (`pywebpush` server-side, service worker + VAPID keys
  client-side) — supported in Chrome/Firefox/Edge/Safari 16.4+
  ([MDN Push API](https://developer.mozilla.org/en-US/docs/Web/API/Push_API)). It is the
  *complementary* out-of-app channel for "clips ready" when the tab is closed but the browser is
  open. **Recommend deferring to Phase 3**: it needs a service worker, VAPID key management, and
  per-browser endpoint handling for marginal beta value over email. Flag as an open question, not a
  default build.

---

## 4. Minimal honest lifecycle sequence (beta)

Product-event-triggered, not drip-by-timer; short, and every lifecycle mail is unsubscribable.
Anti-spam: cap at one lifecycle email per creator per ~48h.

1. **Welcome** (fires when `creator.email` is first set, `youtube/oauth.py:183`) — what AutoClip
   does, the honesty disclaimer, next step. Highest open rate of any lifecycle mail
   ([howdygo](https://www.howdygo.com/blog/saas-onboarding-email-examples)).
2. **First-clip nudge** — fires only if **no video uploaded** N days after connect (product
   state, not a timer). Branches to the actual blocker (e.g. min-data gate not met), per the
   modern "target the blocker" standard ([digitalapplied](https://www.digitalapplied.com/blog/saas-customer-onboarding-email-sequence-2026-crm-playbook)).
   This is the direct fix for prompt 07 / Issue 172's long-wait drop-off ("we'll notify you").
3. **Re-engagement** — fires if active creator goes quiet (no clips reviewed in N days). One mail,
   honest, with a clear opt-out. No "we miss you" spam.

Everything else ("you're a power user", upsell drips) is **out of scope for beta** — keep the
suite to reliable transactional + these three.

---

## 5. Proposed issues (dependency-ordered)

These **subsume and supersede** the open Issues 80 and 81 by resolving their Phase-1 questions;
file them as the implementation children of Issue 176 and mark 80/81 as folded-in. Each needs a
`docs/DECISIONS.md` entry (new dependency and/or new data model — flagged per item).

### Issue 176a — Transactional email infrastructure (Resend) + deliverability
**Depends on:** none. **Supersedes:** Issue 80.
**What:** Add Resend as the email provider behind a `notify/mailer.py` typed API with a
`NOTIFY_BACKEND=console|resend` dev sink; module-level client in `clients.py`; Jinja2 templates in
`notify/templates/`. Configure SPF/DKIM/DMARC(`p=none`→tighten) on `autoclip.studio`.
**Acceptance criteria:**
- [ ] Phase 1: provider (Resend), templating (Jinja2), dev-sink decision logged in `docs/DECISIONS.md`
- [ ] `notify/mailer.py` typed `send(to, template, context, idempotency_key)`; unit-tested against console sink
- [ ] Resend client is a module-level singleton; `RESEND_API_KEY`, `EMAIL_FROM`, `NOTIFY_BACKEND` in `.env.example` + `docs/SECRETS.md`
- [ ] DNS auth records documented in `docs/RUNBOOKS.md`; DMARC starts at `p=none`
- [ ] No test hits the live provider (console backend default in CI)
**DECISIONS entry:** YES — new dependency (`resend`), provider choice + Postmark-fallback rationale.

### Issue 176b — Notification data model + idempotent Celery send task
**Depends on:** 176a. **New data model.**
**What:** Alembic migration for `notification_preferences`, `notification_deliveries`,
`notifications`. A `send_notification` Celery task implementing the §3.2 flow (preference check →
dedupe-key Inbox row → render → Resend with `Idempotency-Key` → in-app row).
**Acceptance criteria:**
- [ ] Migration + models; `notifications` has an RLS policy mirroring `chat_conversations`
- [ ] `send_notification` is idempotent under at-least-once redelivery (UNIQUE dedupe_key); integration test proves a double-enqueue sends once
- [ ] Preference check short-circuits before any send; transactional category cannot be disabled
- [ ] No token/PII reaches the provider payload (test asserts redaction)
**DECISIONS entry:** YES — three new tables + idempotency-key scheme.

### Issue 176c — Wire transactional triggers to the fan-out
**Depends on:** 176b. **Supersedes:** the refund-email/banner half of Issue 81.
**What:** Add `send_notification.delay(...)` at each terminal fire point in §2: clips ready
(`worker/tasks.py:1468`), DNA built (`:1254`), terminal failure/refund (`RefundOnFailureTask`,
`:89`), YouTube re-auth needed (`:299`), trial ending (`expire_trials`, `:261`), balance low
(emit from `billing/ledger.py` deduct path using `LOW_BALANCE_THRESHOLD_MINUTES`, `config.py:231`).
**Acceptance criteria:**
- [ ] Each trigger sends exactly one email + one in-app row per event (dedupe verified)
- [ ] Copy passes the honesty check (no virality language) — assert in a structural test like the existing disclaimer test
- [ ] Trial-ending and balance-low fire from existing beat/ledger paths (no new schedule unless justified)
**DECISIONS entry:** only if a trigger's source-of-truth changes (e.g. `expire_trials` gains state).

### Issue 176d — In-app notification center (poll) + unsubscribe + preferences UI
**Depends on:** 176b. **Supersedes:** the surface half of Issue 81.
**What:** `GET /api/notifications` + `POST /api/notifications/{id}/dismiss`; no-auth
`GET /unsubscribe/{token}` (flips `email_lifecycle`); a preferences pane in the React Profile page;
List-Unsubscribe headers on lifecycle mail.
**Acceptance criteria:**
- [ ] Endpoints enforce per-creator isolation (RLS + app filter); test cross-creator read returns nothing
- [ ] SPA renders unread notifications (reuse the activity-panel/TanStack shell, `docs/SOT.md:190`)
- [ ] One-click unsubscribe works without login and is honored ≤10 business days; RFC 8058 headers present
**DECISIONS entry:** only if unsubscribe/consent model diverges from §3.3.

### Issue 176e — Minimal lifecycle sequence (welcome / first-clip nudge / re-engagement)
**Depends on:** 176c, 176d.
**What:** Three product-event-triggered lifecycle emails (§4), each unsubscribable, ≤1 per 48h.
**Acceptance criteria:**
- [ ] Welcome fires on first `creator.email` set; nudge/re-engagement fire on product state, not timers
- [ ] Each carries unsubscribe + physical address (CAN-SPAM) and rests on legitimate interest (GDPR)
- [ ] Frequency cap enforced; opted-out creators get none
**DECISIONS entry:** YES — scope expansion (first marketing-class comms; coordinate consent posture with Issue 177).

### Issue 176f — (OPTIONAL) Web push for "job done"
**Depends on:** 176b. **Defer unless approved.**
**What:** VAPID web push as a complementary channel for clips-ready/render-done when the tab is
closed. Service worker + VAPID keys + `push_subscriptions` table + `pywebpush` send.
**Acceptance criteria:**
- [ ] Opt-in only (`push_enabled`); subscription tokens stored per-creator
- [ ] Push reuses the same `send_notification` dedupe ledger; no duplicate of email
**DECISIONS entry:** YES if built — new dependency + new browser-facing surface.

---

## 6. Open questions for the human (one-line answers)

1. **Provider:** Resend (recommended) — or Postmark for best-in-class deliverability at ~$50/100k?
2. **Web push in beta:** build now (176f) or defer to post-launch? (Recommend defer.)
3. **Lifecycle scope:** ship all three lifecycle emails (176e) for beta, or transactional-only first?
4. **`EMAIL_FROM` identity:** `noreply@autoclip.studio`, or a monitored `hello@`/`support@` for replies?
5. **Balance-low / trial-ending thresholds:** notify at `LOW_BALANCE_THRESHOLD_MINUTES`=10 (`config.py:231`) and trial T-minus how many days (2? 3?)?
6. **Folding-in:** confirm Issues 80 + 81 are marked superseded-by-176 rather than worked independently?

---

## 7. Docs to flag (stale / needs update on build)
- `docs/issues.md:1008,1040` — Issues 80 & 81 should be marked **folded into Issue 176** to avoid
  two teams building overlapping infra.
- `docs/SOT.md` — add the three notification tables to the Data Model and `notify/` to the file
  structure when 176b lands; add Resend to the Tech Stack table.
- `docs/COMPLIANCE.md` — add a "Communications consent & unsubscribe" section (CAN-SPAM/GDPR
  posture); coordinate with prompt 12 / Issue 177 so consent capture is described once.
- `docs/SECRETS.md` / `.env.example` — `RESEND_API_KEY`, `EMAIL_FROM`, `NOTIFY_BACKEND` (currently
  absent — confirmed gap).
- **Cross-reference, do not duplicate:** observability/alerting is prompt 05 / Issue 170 (operator
  email is a *different* channel than creator email); funnel analytics on these sends is prompt 07 /
  Issue 172; consent/erasure mechanics are prompt 12 / Issue 177; the no-dunning billing reality is
  a correction to prompt 06 / Issue 171.

---

## Sources
- [Resend vs SES vs Postmark 2026 — buildmvpfast](https://www.buildmvpfast.com/blog/resend-vs-ses-vs-postmark-transactional-email-deliverability-saas-2026)
- [Postmark vs Resend comparison](https://postmarkapp.com/compare/resend-alternative)
- [Email API pricing comparison June 2026](https://www.buildmvpfast.com/api-costs/email)
- [Resend Python SDK docs](https://resend.com/docs/send-with-python) · [resend on PyPI](https://pypi.org/project/resend/)
- [Resend idempotency keys (blog)](https://resend.com/blog/engineering-idempotency-keys) · [changelog](https://resend.com/changelog/idempotency-keys)
- [Email authentication protocols 2025 — Email on Acid](https://www.emailonacid.com/blog/article/email-deliverability/email-authentication-protocols/)
- [Email authentication requirements 2025 — Mailgun](https://www.mailgun.com/state-of-email-deliverability/chapter/email-authentication-requirements/)
- [FTC CAN-SPAM compliance guide](https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business)
- [GDPR and transactional emails — TermsFeed](https://www.termsfeed.com/blog/gdpr-transactional-emails/)
- [Celery idempotent tasks/retries — Medium](https://medium.com/@hjparmar1944/fastapi-celery-work-queues-idempotent-tasks-and-retries-that-dont-duplicate-d05e820c904b)
- [Python Celery + Redis job queue 2025 — OneUptime](https://oneuptime.com/blog/post/2025-01-06-python-celery-redis-job-queue/view)
- [MDN Push API](https://developer.mozilla.org/en-US/docs/Web/API/Push_API)
- [SaaS onboarding email sequence 2026 — digitalapplied](https://www.digitalapplied.com/blog/saas-customer-onboarding-email-sequence-2026-crm-playbook)
- [SaaS onboarding email examples — howdygo](https://www.howdygo.com/blog/saas-onboarding-email-examples)
