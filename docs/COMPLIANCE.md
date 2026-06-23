# CreatorClip — Compliance & YouTube API ToS

**Last updated**: 2026-06-23
Update this file any time data classes, retention policy, API scopes, or privacy posture changes.

---

## YouTube API Services Terms of Service

CreatorClip's entire differentiator depends on the YouTube Analytics and Data APIs. Violating
the ToS would result in API access revocation, destroying the product.

**Key obligations** (non-exhaustive — always read the current ToS at developers.google.com/youtube/terms):

1. **Minimum necessary scopes**: Request only the scopes required for the feature. Do not
   request write scopes unless directly needed (v1 is read + export only).

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
   backoff on 429/403 responses.

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
| Source media | Raw video bytes | Purged `SOURCE_MEDIA_RETENTION_HOURS` (default 72h) after ingest completion (`videos.ingest_done_at`), not upload time — see Issue 43 | Never stored longer than needed for processing |
| Rendered clips | 9:16 Short output | Until creator deletes | Stored in R2 |
| Transcripts | Word-level segments | Until video deleted | Derived from source; not YouTube-origin data |
| Creator DNA | Pattern profiles, brief text | Until creator deletes | Creator-owned derivative data |
| Feedback labels | upvote/downvote/skip/trim | Until creator deletes | Creator-owned |
| Event logs (telemetry) | UI + backend events: click/submit/navigate, http_request (path, method, status, duration, request_id, creator_id) | **90-day rolling purge** — rows with `at < now() - 90 days` are deleted daily by the `purge-stale-event-logs-daily` Celery Beat task (Issue 250). Configured via `EVENT_LOG_RETENTION_DAYS` (default 90). | Issue 151. **No PII / no tokens** — `event_log._redact()` masks email/token/secret-like keys at ingestion; creator is id-only. Dedicated `event_logs` table on a **separate engine, no FK to creators** (no RLS); per-creator reads isolated at the app layer (`/api/logs/me`); operators query directly. **Account deletion (Issue 248):** the DB cascade can't reach this engine, so `DELETE /auth/me` explicitly calls `event_log.purge_creator_events(creator_id)` to remove all of a creator's telemetry rows (best-effort; logged, never aborts the erasure). GDPR Art. 5(1)(e): 90 days is within the industry-standard range (60–180 days) for SaaS behavioral telemetry; justified by analytical utility, not a legal minimum. |
| Audit log | `creator.deleted` entries (creator_id UUID only — no email, no channel_id) | **Indefinite** — retained as evidence-of-erasure per Art. 5(1)(e) justified by security/breach investigation need. Legal counsel standard: 1–3 years for compliance audit logs. No PII stored (Issue 247 + EDPB CEF 2025 constraint). | CCPA disclosure: audit_log stores only pseudonymous UUIDs; retention is indefinite for compliance evidence. No personal information retained. |
| Chat conversations (Issue 152) | Pro-chatbot threads: the creator's own messages + assistant replies, per-message token counts | Persisted until the creator deletes the conversation (`DELETE /api/chat/conversations/{id}`); cascades on account deletion via `creator_id` FK | Creator-authored content scoped to one creator. `chat_conversations` RLS-gated (migration 0026); `chat_messages` reaches tenant via the conversation FK; reads filtered at the app layer. Tool calls fetch ONLY the requesting creator's analytics (no cross-creator data ever enters the prompt). No virality promise (honesty constraint in the system prompt). |

---

## OAuth Scopes (v1)

| Scope | Purpose | Required |
|-------|---------|---------|
| `https://www.googleapis.com/auth/youtube.readonly` | Read video metadata, captions | Yes |
| `https://www.googleapis.com/auth/yt-analytics.readonly` | Retention curves, metrics, demographics, activity | Yes |
| `https://www.googleapis.com/auth/youtube.upload` | (NOT requested in v1) Direct Shorts publishing | No — deferred to Phase 2 |

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
- **Sub-processors and Art. 30 record (Issue 251):** see `docs/SUBPROCESSORS.md` for the
  full sub-processor list (Anthropic, Voyage AI, Deepgram, Cloudflare R2, Stripe, Google),
  the personal data categories each processes, transfer mechanisms, and the DPA runbook.
  Deepgram `mip_opt_out=True` is enforced in code on every transcription call; Google
  YouTube analytics data must be deleted within 30 days if auth cannot be re-verified
  (Wave-4 Fix 3 — enforced by `purge-stale-youtube-analytics-daily` Beat task).

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
