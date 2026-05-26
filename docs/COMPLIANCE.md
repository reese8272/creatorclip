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
| Source media | Raw video bytes | Purged after `SOURCE_MEDIA_RETENTION_HOURS` (default 72h) | Never stored longer than needed for processing |
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

---

## Pre-Public-Launch Compliance Gates

- [ ] YouTube data-retention refresh cadence confirmed and implemented
- [ ] Google OAuth app verification submitted (requires ToS + Privacy Policy pages)
- [ ] `yt-dlp` guard verified in code (off by default; own-content-only path documented)
- [ ] Account-deletion endpoint implemented (right-to-erasure: tokens + media + data)
- [ ] Token revocation handler implemented
- [ ] Scope review: no unnecessary scopes requested
