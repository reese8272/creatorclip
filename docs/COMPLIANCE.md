# CreatorClip — Compliance & YouTube API ToS

**Last updated**: 2026-05-25
Update this file any time data classes, retention policy, API scopes, or privacy posture changes.

---

## YouTube API Services Terms of Service

CreatorClip's entire differentiator depends on the YouTube Analytics and Data APIs. Violating
the ToS would result in API access revocation, destroying the product.

**Key obligations** (non-exhaustive — always read the current ToS at developers.google.com/youtube/terms):

1. **Minimum necessary scopes**: Request only the scopes required for the feature. Do not
   request write scopes unless directly needed (v1 is read + export only).

2. **Data retention / refresh**: Analytics data returned by the YouTube Analytics API must be
   refreshed or deleted according to Google's data policies. CreatorClip must not cache
   analytics indefinitely. The current policy and required refresh cadence must be documented
   here once confirmed.

3. **Display requirements**: Any display of YouTube data must comply with YouTube's branding
   guidelines.

4. **Quota management**: Do not exceed the project's daily quota. Implement exponential
   backoff on 429/403 responses.

5. **Source acquisition**: Downloading YouTube video bytes via third-party tools (`yt-dlp`)
   may violate the ToS. CreatorClip's compliant path is creator-initiated upload or use of
   the creator's own content via authorized API methods. `yt-dlp` is off by default and
   must never be used on third-party channels.

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
- Demographics data: aggregated payloads only; no individual viewer data is stored.
- **Google API *Limited Use* disclosure** (2026-05-29): the affirmative statement that
  CreatorClip's use of Google API data adheres to the Google API Services User Data Policy
  (incl. the Limited Use requirements) is live in `static/privacy.html` and restated in the
  homepage footer (`static/index.html`). Served at the stable URL `/privacy`. This is the
  mandatory prerequisite for Google OAuth verification of YouTube scopes.
- **CORS is domain-locked in production** (`config.py` `_lock_prod_cors`): boots-fail on
  `*`/localhost/`http://` `ALLOWED_ORIGINS` when `ENV=production`; `/docs` disabled outside dev.

---

## Pre-Public-Launch Compliance Gates

- [ ] YouTube data-retention refresh cadence confirmed and implemented
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
