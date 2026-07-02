# CreatorClip — Compliance & YouTube API ToS

**Last updated**: 2026-06-23
Update this file any time data classes, retention policy, API scopes, or privacy posture changes.

---

## YouTube API Services Terms of Service

CreatorClip's entire differentiator depends on the YouTube Analytics and Data APIs. Violating
the ToS would result in API access revocation, destroying the product.

**Key obligations** (non-exhaustive — always read the current ToS at developers.google.com/youtube/terms):

1. **Minimum necessary scopes**: Request only the scopes required for the feature. The base
   login flow is read-only; the `youtube.upload` write scope is requested **only** when a
   creator opts into publishing, via incremental authorization (Issue 194). See OAuth Scopes below.

2. **Data retention / refresh** (Wave-4 Fix 3 / Issue 75b — confirmed 2026-05-31):
   The YouTube API Services Developer Policies §III.E.4.b + §III.D.2.3.b require API
   clients to verify authorization every **30 calendar days** OR delete the stored
   data. The 30-day clock applies to all YouTube API Data, with `fetched_at` as the
   natural staleness proxy — if a creator's daily Beat refresh stops succeeding
   (token revoked, quota exhausted, transient outage >30d), `fetched_at` stops
   advancing and the row falls past the cutoff.

   **CreatorClip's implementation:**
   - `refresh_youtube_analytics` Beat task (daily, 24h cadence): re-fetches metrics
     for every creator with a valid token. Updates `fetched_at` on success.
   - `purge_stale_youtube_analytics` Beat task (daily, 24h cadence): deletes rows
     in `video_metrics`, `retention_curves`, `audience_activity`, and `demographics`
     whose `fetched_at < now() - YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS` (default 30).
   - Account-deletion endpoint (Issue 19): handles the **7-day** explicit-revoke
     window (§III.D.2.3.a) — when a creator invokes account deletion, all of
     their tokens, analytics, source media, clips, DNA, and feedback are deleted
     synchronously.

   **Setting:** `YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS` (default 30; in
   `config.py` + `.env.example`). Lengthening past 30 would be a documented ToS
   violation; shortening is safe but trades freshness for no compliance benefit.

   **Source:** https://developers.google.com/youtube/terms/developer-policies
   (verified 2026-05-31 via industry-standards-researcher).

3. **Display requirements**: Any display of YouTube data must comply with YouTube's branding
   guidelines.

4. **Quota management**: Do not exceed the project's daily quota. Implement exponential
   backoff on 429/403 responses (shipped — `youtube/errors.py` transient-403 set + the
   `_get_json`/`_fetch_report` retry loops). The Data API quota is **per-Google-Cloud-project,
   10,000 units/day, one project per client** — there is no cross-project sharding; raising
   it at scale (beta ~1,000 → GA ~10,000 creators) requires the **YouTube API Services
   compliance audit** (branding/attribution, privacy policy, user-data control), which is
   triggered by the quota-extension request (Issue 260). Per-creator fairness sub-budgets
   (Issue 260) ensure the non-interactive Beat refresh fan-out cannot drain the day's budget
   and starve interactive onboarding.

   **ETag/304 cache surface (Issue 260, new):** conditional Data API GETs cache the response
   body + ETag in Redis under `creatorclip:yt_etag:{sha256(url+params+creator_id)}`, TTL-bounded
   (`YOUTUBE_ETAG_CACHE_TTL_S`, default 6h), so a `304 Not Modified` is served without spending
   quota. This is an **ephemeral, per-creator-keyed cache** that holds **no OAuth tokens and no
   PII** — only public YouTube resource bodies (video metadata / playlist items) the creator is
   already authorized to read. It auto-expires on TTL and is bound by the same 30-day
   data-retention/refresh purge posture as the rest of the YouTube data class.

5. **Source acquisition**: Downloading YouTube video bytes via third-party tools (`yt-dlp`)
   violates the ToS — **even for the creator's own content** (Issue 139 research, 2026-06-16).
   YouTube's ToS bars downloading unless a download link is shown; the stricter YouTube **API
   Services ToS** that binds CreatorClip additionally prohibits API clients from letting users
   download or "modify the audio or video portions of a video" outside YouTube Premium.
   Ownership is a copyright defense, not a ToS exemption. **CreatorClip's only compliant clip
   path is creator-initiated upload** (the source file obtained the sanctioned way, e.g. Google
   Takeout / original export). Linked + catalog videos are visible for analytics/DNA but are
   non-clippable until a source file is uploaded; `POST /videos/{id}/queue` returns 409 with
   upload guidance for source-less rows. `yt-dlp` (`youtube/ingest.py::download_via_ytdlp`,
   `YTDLP_ENABLED`) stays **off by default, commented-out of `requirements.txt`, and is NOT
   wired into the hosted pipeline** — it exists only as a self-host escape hatch for operators
   who own their own compliance posture, and must never be used on third-party channels.
   See DECISIONS 2026-06-16 (Issue 139) for the full rationale + sources.

6. **Token storage**: OAuth tokens must be encrypted at rest. CreatorClip uses Fernet
   encryption on token columns. Tokens must never appear in logs.

7. **Revocation handling**: When a creator revokes access, all stored tokens must be deleted
   and any ongoing API calls must cease.

---

## Data Classes & Retention Policy

| Data class | What we store | Retention | Notes |
|-----------|--------------|-----------|-------|
| YouTube OAuth tokens | access_token_encrypted, refresh_token_encrypted | Until revocation | Fernet-encrypted; never logged |
| Video metrics | views, watch time, engagement rate, fetched_at | Refresh per YouTube policy | Refresh cadence TBD — confirm from ToS |
| Retention curves | timestamp-level audience_watch_ratio | Refresh per YouTube policy | |
| Audience activity | day/hour activity windows | Refresh per YouTube policy | |
| Demographics | Aggregated payload JSON | Refresh per YouTube policy | |
| Source media | Raw video bytes (`videos.source_uri`) | Purged `SOURCE_MEDIA_RETENTION_HOURS` (default 72h) after ingest completion (`videos.ingest_done_at`), not upload time — see Issue 43 | Never stored longer than needed for processing. The video is retained through the render window (migration 0039) — rendering IS processing; auto-render runs within minutes of ingest. |
| Extracted audio | Derived WAV (`videos.audio_uri`, migration 0039) — input for transcription + signal extraction | Purged alongside the source video at 72h by `purge_stale_source_media` | Derived artifact, not YouTube-origin data. Split from `source_uri` so ingest no longer discards the video the renderer needs. |
| Rendered clips | 9:16 Short output | Until creator deletes | Stored in R2 (media bucket). Delete-protected by a short R2 Object Lock window on `clips/` (Issue 258) reconciled with right-to-erasure (Issue 254). |
| Encrypted DB backups (Issue 256) | Nightly encrypted `pg_dump` (the full Postgres slice — incl. creator emails + aggregated demographics, all as in-DB) stored in the **separate** `creatorclip-backups` R2 bucket | **`daily/` ≤ 14 days** (within the 30-day YouTube analytics-staleness ceiling for the analytics rows it carries); `weekly/` ~56d for the non-analytics precious slice; `predeploy/` short. Enforced by R2 Lifecycle rules. | PII-bearing → at-rest encrypted (openssl AES-256), Object Lock (Compliance mode) to resist deletion, separate bucket from media. OAuth tokens are carried as Fernet **ciphertext** (useless without the separately-escrowed key, Issue 255). **Right-to-erasure:** a creator's rows leave the live DB immediately on `DELETE /auth/me`; backups age out within the windows above (documented "beyond use" stance, coordinated with Issue 254). |
| Transcripts | Word-level segments | Until video deleted | Derived from source; not YouTube-origin data |
| Creator DNA | Pattern profiles, brief text | Until creator deletes | Creator-owned derivative data |
| Feedback labels | upvote/downvote/skip/trim | Until creator deletes | Creator-owned |
| Event logs (telemetry) | UI + backend events: click/submit/navigate, http_request (path, method, status, duration, request_id, creator_id) | **90-day rolling purge** — rows with `at < now() - 90 days` are deleted daily by the `purge-stale-event-logs-daily` Celery Beat task (Issue 250). Configured via `EVENT_LOG_RETENTION_DAYS` (default 90). | Issue 151. **No PII / no tokens** — `event_log._redact()` masks email/token/secret-like keys at ingestion; creator is id-only. Dedicated `event_logs` table on a **separate engine, no FK to creators** (no RLS); per-creator reads isolated at the app layer (`/api/logs/me`); operators query directly. **Account deletion (Issue 248):** the DB cascade can't reach this engine, so `DELETE /auth/me` explicitly calls `event_log.purge_creator_events(creator_id)` to remove all of a creator's telemetry rows (best-effort; logged, never aborts the erasure). GDPR Art. 5(1)(e): 90 days is within the industry-standard range (60–180 days) for SaaS behavioral telemetry; justified by analytical utility, not a legal minimum. |
| Audit log | `creator.deleted` entries (creator_id UUID only — no email, no channel_id) | **Indefinite** — retained as evidence-of-erasure per Art. 5(1)(e) justified by security/breach investigation need. Legal counsel standard: 1–3 years for compliance audit logs. No PII stored (Issue 247 + EDPB CEF 2025 constraint). | CCPA disclosure: audit_log stores only pseudonymous UUIDs; retention is indefinite for compliance evidence. No personal information retained. |
| Chat conversations (Issue 152) | Pro-chatbot threads: the creator's own messages + assistant replies, per-message token counts | Persisted until the creator deletes the conversation (`DELETE /api/chat/conversations/{id}`); cascades on account deletion via `creator_id` FK | Creator-authored content scoped to one creator. `chat_conversations` RLS-gated (migration 0026); `chat_messages` reaches tenant via the conversation FK; reads filtered at the app layer. Tool calls fetch ONLY the requesting creator's analytics (no cross-creator data ever enters the prompt). No virality promise (honesty constraint in the system prompt). |
| Notification preferences (Issue 243) | `email_transactional`, `email_lifecycle`, `inapp_enabled`, `push_enabled`, `unsubscribe_token` | Until account deletion (CASCADE) | No RLS — primary key is `creator_id` (one-row-per-creator). `email_transactional` is always-on (CAN-SPAM / GDPR Art. 6(1)(b)). `email_lifecycle` is unsubscribable (one-click, no auth required). No PII beyond the `creator_id` FK. |
| Clip impressions (Issue 202) | `clip_id`, `rank`, `shown_at` per creator — what rank each clip was shown at, and when | Until account deletion (CASCADE) | Position record for counterfactual/IPS ranking evaluation. **No PII, no YouTube-origin data** — only internal ids, an integer rank, and a timestamp. `creator_id` FK cascades on `DELETE /auth/me` (right-to-erasure). Per-creator isolation via the `tenant_isolation` RLS policy (migration 0037). A rolling-window purge (à la event_logs) may be added if volume warrants. |
| Notification deliveries (Issue 243) | `event_type`, `entity_id`, `channel`, `dedupe_key` (SHA-256 hex), `provider_message_id` (Resend opaque id), `status` | Until account deletion (CASCADE) | Internal idempotency ledger — not exposed via creator-facing API. No PII: `dedupe_key` is a SHA-256 hex digest; `provider_message_id` is an opaque provider reference with no PII. |
| Notifications / in-app center (Issue 243) | `kind`, `title`, `body`, `link_url`, `seen_at`, `dismissed_at` | Until account deletion (CASCADE) | RLS `tenant_isolation` ENABLE+FORCE (migration 0031) — same policy as `chat_conversations`. Creator-visible only. Copy honesty-constrained (no virality language). |
| **Clickwrap consent record (Issue 299)** | `terms_accepted_at` (TIMESTAMPTZ), `terms_version` (VARCHAR 32), `privacy_version` (VARCHAR 32) on the `creators` row | Until account deletion (CASCADE with creator row) | Stores the affirmative acceptance timestamp + the version strings of the ToS and Privacy Policy presented at first sign-in. Recorded only on the first OAuth callback (`is_new=True`). NULL on legacy rows (pre-migration 0033). Evidence artifact per 9th Cir. *Chabolla v. ClassPass* (2025) and GDPR Art. 7 recorded-consent requirement. Configured via `TOS_VERSION` / `PRIVACY_VERSION` in `config.py` (bumped on material policy changes). No PII beyond creator_id. |
| **COPPA minimum-age attestation (Issue 300)** | `minimum_age_confirmed_at` (TIMESTAMPTZ) on the `creators` row | Until account deletion (CASCADE with creator row) | Stores the UTC timestamp when the creator checked the "I confirm I am 13 or older" attestation checkbox and completed the OAuth flow for the first time. Recorded in the same `is_new=True` block as the consent record (`routers/auth.py`). NULL on legacy rows (pre-migration 0034). Age-neutral phrasing per FTC amended COPPA Rule (16 CFR Part 312, effective 2025-06-23). CreatorClip is a **general-audience service not directed to children** and does not knowingly collect PII from persons under 13. Deletion path for under-age accounts: account deletion endpoint (`DELETE /auth/me`) purges all data — same path as voluntary erasure. |

---

## OAuth Scopes (v1)

| Scope | Purpose | Required |
|-------|---------|---------|
| `https://www.googleapis.com/auth/youtube.readonly` | Read video metadata, captions | Yes |
| `https://www.googleapis.com/auth/yt-analytics.readonly` | Retention curves, metrics, demographics, activity | Yes |
| `https://www.googleapis.com/auth/youtube.upload` | Direct Shorts publishing (Issue 194) | **Opt-in only** — requested via incremental consent (`/auth/connect-publishing`) when a creator enables publishing; never in base login |

The base login flow requests only the read scopes above. The `youtube.upload` write scope
is added **per-creator, on publish opt-in**, via Google incremental authorization
(`include_granted_scopes=true`) — keeping the base flow minimum-necessary and the
verification surface small for read-only creators (Issue 194, `docs/DECISIONS.md` 2026-06-22).

> **Launch dependency:** shipping uploads to the public requires Google OAuth app
> verification **and** the YouTube API Services compliance audit. Until that clears,
> `publish_to_youtube` (Issue 195) forces `privacyStatus=private` (creator publishes manually).

Requesting only the minimum necessary scopes reduces OAuth verification friction and limits
the blast radius of a token compromise.

---

## Privacy Posture

- PII minimization: store only what features require.
- No email or channel identity in log lines.
- No YouTube analytics data in LLM prompts beyond what is needed for the specific analysis.
- Account-deletion endpoint must purge: tokens, analytics, source media, rendered clips,
  DNA profile, feedback labels. Required before public launch.
- **Deletion-log minimization (Issue 247):** the `creator.deleted` audit row must contain
  **no PII** — no email, no channel_id, no other personal data. `audit_log` is never purged
  and RLS-exempt, so any PII written there would survive the erasure (GDPR Art. 17 — EDPB
  CEF 2025). Only the internal `creator_id` (pseudonymous once the creator row is gone) is
  retained as evidence-of-erasure. Pinned by `test_delete_account_writes_audit_log`.
- Demographics data: aggregated payloads only; no individual viewer data is stored.
- **Global Privacy Control (Issue 302):** the backend detects `Sec-GPC: 1` on every request
  (`request.state.gpc` — detection only, never logged or persisted) and serves the
  machine-readable declaration at `/.well-known/gpc.json` per the W3C GPC spec
  (w3c.github.io/gpc). Because CreatorClip does not sell or share personal information
  (no ad-tech, no cross-context behavioural advertising), a GPC opt-out is satisfied by
  default; the Privacy Policy's CCPA section states this recognition explicitly.
- **Sub-processors and Art. 30 record (Issue 251):** see `docs/SUBPROCESSORS.md` for the
  full sub-processor list (Anthropic, Voyage AI, Deepgram, Cloudflare R2, Stripe, Google),
  the personal data categories each processes, transfer mechanisms, and the DPA runbook.
  Deepgram `mip_opt_out=True` is enforced in code on every transcription call; Google
  YouTube analytics data must be deleted within 30 days if auth cannot be re-verified
  (Wave-4 Fix 3 — enforced by `purge-stale-youtube-analytics-daily` Beat task).

---

## Communications consent & unsubscribe (Issues 245 / 246)

CreatorClip sends two classes of email, governed differently:

**Transactional (always-on).** clips_ready, dna_built, refund_issued, reauth_required,
trial_ending, balance_low, catalog_sync_done. These are required for the operation of the
account (GDPR Art. 6(1)(b) contract necessity; CAN-SPAM treats true transactional mail as
exempt from the unsubscribe requirement). `notification_preferences.email_transactional` is
locked to `True`: the Settings UI shows the toggle disabled-on, and the API request model
(`PreferencesPatch`) omits the field so it can never be disabled server-side. No
List-Unsubscribe header is attached to transactional sends.

**Lifecycle (commercial-leaning, opt-out).** welcome, first_clip_nudge, re_engagement. Sent
under GDPR **legitimate interest** with a clear opt-out, and treated as commercial mail under
CAN-SPAM:

- **One-click unsubscribe (RFC 8058).** Every lifecycle send carries
  `List-Unsubscribe: <{APP_BASE_URL}/unsubscribe/{token}>` and
  `List-Unsubscribe-Post: List-Unsubscribe=One-Click`. The link resolves to an unauthenticated
  `GET /unsubscribe/{token}` that flips `email_lifecycle=False`. The token is the unguessable,
  unique UUID4 `notification_preferences.unsubscribe_token`.
- **Honored ≤10 business days** (CAN-SPAM): the opt-out takes effect immediately on click — the
  `send_notification` task gates every lifecycle send on `email_lifecycle`.
- **Link live ≥30 days** (CAN-SPAM): the token does not expire or rotate on unsubscribe.
- **Physical postal address** (CAN-SPAM §A.5): every lifecycle template renders
  `MAILING_ADDRESS`. This MUST be set to a real, deliverable address before lifecycle email is
  enabled in production — the default is a non-deliverable placeholder.
- **Frequency cap.** A shared 48h budget (`LIFECYCLE_FREQUENCY_CAP_HOURS`) across all lifecycle
  events prevents a creator receiving more than one lifecycle email per window.
- **No virality promise.** All copy + templates pass the honesty structural check.

The unsubscribe endpoint never reveals which email/creator a token maps to (a missing token
returns a generic 404), so it cannot be used as an enumeration oracle.

---

## Billing & Refund Policy

CreatorClip's billing is pay-per-use: minutes are deducted from the creator's
balance when video ingest begins and minutes packs are purchased via Stripe.

**Refund policy** (Issue 57):

- **Automatic refund on terminal ingest failure.** If a video's ingest chain
  (`ingest_video → transcribe_video → build_signals`) exhausts its retries
  without producing usable signals, the minutes deducted for that video are
  credited back to the creator's balance automatically.
- The refund appears as a `MinutePack` row with `reason="refund"`,
  `pack_id="refund:<video_id>"`, and `price_cents=0` — visible in the
  creator's billing history page.
- Refund applies to **all terminal failures regardless of cause** — whether
  the failure is on our side (storage 5xx, ffmpeg crash, Whisper error) or
  on the source side (corrupt upload, unsupported codec). The "you pay for
  what we deliver" stance is simpler to communicate and trust-positive.
- The refund is **idempotent** on the `pack_id` key — a duplicate failure
  notification cannot double-refund.

Until the pricing and ToS pages land (Phase 3), this section is the
canonical user-facing disclosure of refund behavior.

---

## Pre-Public-Launch Compliance Gates

- [x] YouTube data-retention refresh cadence confirmed and implemented (Wave-4 Fix 3 / Issue 75b — 30 days, partial-staleness purge daily; 2026-05-31)
- [ ] Google OAuth app verification submitted (requires ToS + Privacy Policy pages)
- [ ] `yt-dlp` guard verified in code (off by default; own-content-only path documented)
- [ ] Account-deletion endpoint implemented (right-to-erasure: tokens + media + data)
- [ ] Token revocation handler implemented
- [ ] Scope review: no unnecessary scopes requested
- [x] Accessibility Statement published at `/static/accessibility.html` (Issue 301 — EAA enforceable since 2025-06-28, WCAG 2.1 AA target, EN 301 549 v3.2.1 conformance standard, 'Partially conforms' posture)
- [x] Privacy Policy GDPR Art. 13-14 / CCPA rewrite complete (Issue 252 — sub-processors named, SCCs disclosed, CCPA notice-at-collection, demographics aggregation, breach contact, cookies clause; Draft marker retained pending counsel sign-off)

---

## Findings & Fixes Log

Cross-creator isolation, ToS, and privacy issues caught after the original kickoff —
documented here so the compliance trail is auditable.

### 2026-05-28 — SEV-0 cross-creator data leak in `/creators/me/improvement-brief` (Issue 33)

**What**: `routers/improvement.py` was calling `select(VideoMetrics).limit(50)` with **no
`creator_id` filter**. The averages built from that query (`avg_views`,
`avg_engagement_rate`, `avg_view_duration_s`) and embedded in the Claude prompt mixed
**every creator's** metrics together. Direct violation of the per-creator isolation rule
stated in `CLAUDE.md` and a leak of analytics data that creators have not consented to
sharing.

**Discovered**: 2026-05-28 project-wide audit (filed as Phase 2 Issue 33 in `docs/issues.md`).

**Fix**: query now joins `Video` and filters `Video.creator_id == creator.id`, ordered by
`fetched_at DESC` for determinism, limited to the most recent 50 of the requesting
creator's metrics. A zero-data short-circuit (`HTTP 400 "Not enough data — link some
videos first."`) prevents brand-new creators from hitting the LLM with `None` averages.

**Verification**: `tests/test_improvement_isolation.py` — integration test seeds two
creators with disjoint metrics and asserts creator A's analytics dict receives only A's
data (no blend, no B leak). Test runs in the `integration.yml` CI workflow against a real
Postgres + pgvector instance with full Alembic schema.

**Defense-in-depth follow-up**: filed as **Issue 56 — Evaluate Postgres Row-Level Security
for tenant-owned tables** in the Phase 2 backlog. Postgres RLS would have prevented this
class of bug structurally (the database refuses to return cross-tenant rows even when the
application forgets the WHERE), and is the industry-standard safety net underneath
always-filter for compliance-sensitive multi-tenant SaaS.

### 2026-05-30 — Missing Google Limited Use disclosure in the public Privacy Policy (Issue 78g)

**What**: `static/privacy.html` described what YouTube data AutoClip collects and how it is
used, but did not carry the explicit **Limited Use** attestation that Google's API Services
User Data Policy requires of apps accessing Google user data. Google flags a missing Limited
Use disclosure during OAuth app verification, so this was a launch blocker.

**Discovered**: 2026-05-30, reconciling the salvaged "legal / Limited Use / CORS" item
(Issue 78g) from closed PR #6 against what already shipped. CORS lockdown, `/docs` prod
gating, and the ToS/Privacy pages themselves were already present — the disclosure was the
only gap.

**Fix**: added a **Limited Use disclosure** section to `static/privacy.html` with the
canonical attestation ("AutoClip's use and transfer to any other app of information received
from Google APIs will adhere to the Google API Services User Data Policy, including the
Limited Use requirements", linking the policy) plus the four affirmative commitments: use
limited to the user-facing features; no third-party transfer except as needed / for security
/ to comply with law; no use for advertising; no human reads without consent, security need,
legal need, or aggregation/anonymization.

**Verification**: `tests/test_static.py::test_privacy_page_has_limited_use_disclosure` pins
the required language ("Limited Use", "Google API Services User Data Policy", "information
received from Google APIs", and the no-advertising commitment) so it cannot silently regress.

---

## Authentication — Session JWT and Revocation Window (Issue 232)

**Mechanism:** Session cookies carry a stateless HS256 JWT signed with `JWT_SECRET_KEY`. The
token is validated on every authenticated request by verifying the signature and `exp` claim.

**Non-revocability (deliberate tradeoff):** Logout deletes the browser cookie but does NOT
invalidate the token server-side. A stolen session cookie remains valid until the `exp` claim
fires. This is intentional:

- **Exposure window:** `JWT_EXPIRY_MINUTES` (default 60 minutes). Reduce this setting for
  higher-assurance environments.
- **Why not a Redis jti deny-list:** Adding revocation via Redis makes every auth check
  hard-depend on Redis. If Redis is unavailable, all sessions become invalid — the Issue-76
  class of availability failure. For a B2C SaaS with no admin-privilege escalation path, the
  60-minute window is the accepted posture.
- **Deferral:** The jti Redis deny-list is documented as optional and deferred. If implemented,
  it must be fail-open (Redis down → accept) to avoid the availability risk.

**Source:** https://cheatsheetseries.owasp.org/cheatsheets/JSON_Web_Token_for_Java_Cheat_Sheet.html

**Logged:** Issue 232 (2026-06-23).
