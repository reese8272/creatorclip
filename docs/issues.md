# CreatorClip — Issue Backlog

Dependency-ordered. Each issue follows Check → Approve → Build → Review (see `CLAUDE.md`).
**Phase 1 of every issue begins by researching the current industry standard.**

Check `[ ]` → `[x]` when an acceptance criterion is met. Update `docs/PROJECT_STATE.md` when an issue closes.

---

## Issue 1: Repo scaffold + Docker Compose + health endpoint
**Depends on**: none
**Status**: 🔄 In Progress

**What**: New repo with `CLAUDE.md`, `requirements.txt`, `Dockerfile`, `docker-compose.yml`
(`app` + `worker` + `postgres` + `redis`), `main.py` with `/health`, `config.py` env loading,
`crypto.py` Fernet helpers.

**Acceptance criteria**:
- [ ] `docker compose up` brings all four services healthy
- [ ] `GET /health` returns `{status, postgres, redis}`
- [ ] `.env.example` lists every var from SOT
- [ ] Missing required env fails app start with a clear error
- [ ] `pytest` passes with a `/health` smoke test

---

## Issue 2: Postgres schema + Alembic + pgvector
**Depends on**: 1

**What**: SQLAlchemy models for every entity (see `docs/SOT.md` data model) + memory/feedback
tables. pgvector extension enabled. Alembic wired. Encrypted round-trip for token columns.

**Acceptance criteria**:
- [ ] `alembic upgrade head` creates every table incl. `creator_dna`, `dna_embeddings`, `clip_feedback`, `clip_outcomes`, `preference_models`
- [ ] pgvector column type works (insert + similarity query)
- [ ] Token encrypt/decrypt round-trip test passes
- [ ] Audit log append-only at the app layer

---

## Issue 3: Google/YouTube OAuth + creator session
**Depends on**: 2

**What**: OAuth 2.0 flow (`/auth/login`, `/auth/callback`), minimum YouTube Analytics + Data API
scopes, encrypted token storage + refresh, `get_current_creator` dependency, per-creator
isolation.

**Acceptance criteria**:
- [ ] Creator completes OAuth; channel identity + tokens persisted (encrypted)
- [ ] Expired access token auto-refreshes
- [ ] Protected routes 401 without a session
- [ ] Cross-creator data access rejected (isolation test)

---

## Issue 4: YouTube data fetch — metrics, retention, activity
**Depends on**: 3

**What**: `youtube/analytics.py` + `youtube/data_api.py`: per-video metrics, timestamp-level
retention curves, demographics, audience-activity windows, video metadata, caption availability.
Caching + backoff. **Resolve transcription-host decision (GPU vs hosted) here.**

**Acceptance criteria**:
- [ ] Fetches and stores metrics, retention curves, activity windows for the creator's catalog
- [ ] Quota/backoff handling on 403
- [ ] Minimum-data gate computed from catalog size
- [ ] Tests use recorded fixtures (no live API in CI)

---

## Issue 5: Ingestion pipeline — source + transcript + signals
**Depends on**: 4

**What**: Celery tasks: ingest (creator upload / guarded yt-dlp → R2), transcribe (WhisperX or
hosted, word-level), audio signals (energy/silence/laughter), unified signal timeline.

**Acceptance criteria**:
- [ ] A linked/uploaded video runs ingest → transcribe → signals as background tasks with status
- [ ] Word-level transcript persisted
- [ ] Signal timeline persisted (audio + retention-spike markers merged)
- [ ] `yt-dlp` path guarded to own-content only; off by default
- [ ] Tests cover the task chain with a short fixture clip

---

## Issue 6: Creator DNA builder + brief (Research Mode)
**Depends on**: 5

**What**: `dna/builder.py` ranks by engagement, analyzes top/bottom performers + Shorts-specific
patterns; `dna/brief.py` synthesizes a plain-language brief via Claude (prompt-cached corpus);
embeddings → pgvector; creator confirms → living profile.

**Acceptance criteria**:
- [ ] Produces top/bottom analysis + Shorts patterns (extraction point, optimal length, upload gap, ratio)
- [ ] Generates an editable plain-language Creator Brief
- [ ] Confirmed brief persists as a versioned DNA profile; edits supersede, never delete
- [ ] Recency weighting applied to performer selection
- [ ] Anthropic calls use prompt caching; tokens logged

---

## Issue 7: Clip engine — candidates with backward setup-finding
**Depends on**: 6

**What**: `clip_engine/window.py` rolling 60–90s window; `candidates.py` peak detection +
**backward look to setup start**; produces candidate windows.

**Acceptance criteria**:
- [ ] Given a signal timeline, emits candidate windows with `setup_start_s`, `peak_s`, `end_s`
- [ ] **Eval assertion**: on labeled fixtures, clip start lands at the setup, not the post-peak aftermath
- [ ] Configurable candidate count
- [ ] Pure logic where possible; deterministic given fixed input

---

## Issue 8: Clip scoring + DNA-weighted ranking
**Depends on**: 7

**What**: `scoring.py` combines signal features + Claude DNA-fit judgment (cached on DNA
profile); `ranking.py` orders by predicted fit. No preference model yet (cold-start path).

**Acceptance criteria**:
- [ ] Each candidate gets a `score` and `dna_match`
- [ ] Ranking reflects DNA (clips matching the brief rank higher) on a fixture
- [ ] Claude scoring rationale citable ("why this clip?")
- [ ] Tokens logged; prompt caching verified

---

## Issue 9: Render — 9:16 cut + active-speaker reframe
**Depends on**: 8

**What**: `render.py` ffmpeg cut + vertical reframe (face/active-speaker-centered) → R2;
render status on the clip.

**Acceptance criteria**:
- [ ] Candidate renders to a playable 9:16 Short
- [ ] Reframe keeps the speaker in frame on a fixture
- [ ] Render runs as a Celery task with status
- [ ] Output stored to configured storage backend

---

## Issue 10: Review UI + feedback capture
**Depends on**: 9

**What**: Player-first `review.html`: play, upvote/downvote/skip, drag-trim, choose format,
Next; `routers/review.py` persists every interaction as a label. **Decide the review-UI
framework question in Phase 1.**

**Acceptance criteria**:
- [ ] Creator can review a queue of candidate clips without full page reloads
- [ ] Each action (vote/skip/trim-delta/format) writes a `clip_feedback` row
- [ ] Trim handles produce timing-delta labels
- [ ] Tests cover the feedback endpoints end-to-end

---

## Issue 11: Preference model — recency-decayed reranker
**Depends on**: 10

**What**: `preference/` feature vectors + LightGBM/logistic reranker with exponential recency
decay; retrain per session; rerank candidates; surface the personalization threshold.

**Acceptance criteria**:
- [ ] Feedback updates a per-creator model
- [ ] Recency decay verifiably down-weights old feedback (unit test)
- [ ] Reranking shifts candidate order after the threshold volume
- [ ] Below threshold, falls back to DNA + signal ranking with an honest UI label

---

## Issue 12: Upload intelligence + improvement brief
**Depends on**: 11

**What**: `upload_intel/timing.py` best window + optimal gap from audience activity;
`improvement/brief.py` what's-working / underperforming / actions, grounded in data citations
+ live research (web-search tool).

**Acceptance criteria**:
- [x] `GET` returns a best upload window from the creator's own activity data
- [x] Returns optimal long-form → Short gap when supported
- [x] Improvement brief cites specific data rows + current-format research; no generic advice
- [x] Disclaimer/honesty text present (structural test)

---

## Issue 13: Clip outcomes loop (strongest signal)
**Depends on**: 12

**What**: When a creator publishes a clip, capture its real-world performance via the API
and feed it back as the strongest positive label.

**Acceptance criteria**:
- [x] Published clip outcomes fetched and stored
- [x] Outcome feeds the preference model at the highest weight
- [x] Tests cover the outcome → model path

---

---

## BETA_DEPLOYMENT Phase (Issues 23–28)

Goal: app running at a real URL, accessible to a small group of close YouTube friends.
All code is written. These issues are infrastructure + configuration only.

---

## Issue 23: VM provisioning + Cloudflare DNS + HTTPS
**Depends on**: nothing (external setup)
**Status**: 🔲 Not started

**What**: Provision a cloud VM (DigitalOcean Droplet at `147.182.136.107`), install Docker
+ Docker Compose, point `agenticlip.studio` at it via Cloudflare Tunnel (no open inbound
ports needed), and verify HTTPS is live.

**Steps**:
- SSH into the VM; install Docker Engine + Docker Compose v2
- Install `cloudflared`; authenticate and create a tunnel for `agenticlip.studio`
- Configure Cloudflare DNS to route `agenticlip.studio` → tunnel (CNAME, orange cloud)
- Write `docker-compose.prod.yml` cloudflared service (or run as systemd unit)
- Verify the app container listens on port 80 (already configured)

**Acceptance criteria**:
- [ ] `docker compose -f docker-compose.prod.yml up -d` starts all services without errors
- [ ] `https://agenticlip.studio/health` returns `{status: ok, postgres: ok, redis: ok}` from public internet
- [ ] HTTPS terminates at Cloudflare; no direct port exposure on the VM (ports 80/443 not open)
- [ ] SSH access is key-only; password auth disabled

---

## Issue 24: Production environment configuration
**Depends on**: 23 (needs the domain for OAUTH_REDIRECT_URI and ALLOWED_ORIGINS)

**What**: Build the production `.env` file on the VM with all required secrets, lock CORS
and docs settings, and set GitHub Actions secrets for CI/CD.

**Steps**:
- Copy `.env.example` → `.env` on the VM at `/opt/autoclip/`; fill every required field
- Generate `TOKEN_ENCRYPTION_KEY`: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
- Generate `JWT_SECRET_KEY`: `openssl rand -hex 32`
- Set `ENV=production`, `ALLOWED_ORIGINS=https://agenticlip.studio`, `OAUTH_REDIRECT_URI=https://agenticlip.studio/auth/callback`
- Set `APP_BASE_URL=https://agenticlip.studio`
- Confirm `/docs` is disabled (`ENV=production` already gates this in `main.py`)
- Set GitHub Actions secrets: `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, `VPS_PORT`, `GHCR_TOKEN`

**Acceptance criteria**:
- [ ] App starts with `ENV=production`; `/docs` returns 404
- [ ] `ALLOWED_ORIGINS` is exactly `https://agenticlip.studio` (no wildcard, no localhost)
- [ ] `TOKEN_ENCRYPTION_KEY` and `JWT_SECRET_KEY` are unique, random, and not committed to git
- [ ] `.env` is in `.gitignore` (verify)
- [ ] CI/CD pipeline (`deploy.yml`) succeeds end-to-end on a manual trigger

---

## Issue 25: External API services provisioning
**Depends on**: 24

**What**: Create accounts and provision API keys for every external service the app requires.
Verify all connections via `/health` and a real request.

**Services to provision**:
- **Anthropic**: API key → `ANTHROPIC_API_KEY`
- **Voyage AI**: API key → `VOYAGE_API_KEY`
- **Deepgram**: API key → `DEEPGRAM_API_KEY`; set `TRANSCRIPTION_BACKEND=deepgram`
- **Cloudflare R2**: bucket `creatorclip-beta`; generate R2 API token; fill `R2_ACCOUNT_ID=997799b711c382c4de3ab1501bd2751f`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET=creatorclip-beta`; set `STORAGE_BACKEND=r2`
- **Stripe**: live/test keys → `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`

**Acceptance criteria**:
- [ ] `GET /health` reports all services healthy with real credentials in place
- [ ] Deepgram: a short test audio file transcribes successfully via the app
- [ ] R2: a test file upload + download succeeds via the storage client
- [ ] All API keys in `.env` on the VM only — none in git, none in logs

---

## Issue 26: Google OAuth consent screen + beta test users
**Depends on**: 23 (needs the production domain for the redirect URI)

**What**: Configure the Google Cloud project's OAuth consent screen for external users in
Testing status, add beta testers' Google accounts as test users, and verify the full
OAuth flow end-to-end against the production URL.

**Steps**:
- In Google Cloud Console → APIs & Services → OAuth consent screen:
  - User type: **External**; Publishing status: **Testing** (stays in testing until public launch)
  - Add app name (`CreatorClip`), support email, and `agenticlip.studio` as authorized domain
  - Add scopes: `youtube.readonly`, `yt-analytics.readonly`, `userinfo.email`, `userinfo.profile`
  - Add each beta tester's Gmail address under **Test users** (up to 100 allowed in Testing status)
- In Credentials → OAuth 2.0 Client IDs: confirm Authorized redirect URI includes `https://agenticlip.studio/auth/callback`
- Verify `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` in `.env` match this project

**Acceptance criteria**:
- [ ] At least 2 beta testers added as test users in Google Cloud Console
- [ ] OAuth consent screen shows app name and correct scopes
- [ ] Full OAuth flow works end-to-end: visit `https://agenticlip.studio/auth/login` → Google consent → redirect back → creator record created in DB
- [ ] Protected routes return 401 without a session (verify with curl)
- [ ] Cross-creator isolation test passes on the live DB

---

## Issue 27: YouTube API quota check + backoff verification
**Depends on**: 25, 26

**What**: Confirm the project's YouTube API daily quota is sufficient for beta usage and
verify that the app's backoff + caching handles quota exhaustion gracefully.

**Steps**:
- Check Google Cloud Console → APIs & Services → Quotas for:
  - YouTube Data API v3 (default: 10,000 units/day)
  - YouTube Analytics API (default: varies)
- Calculate expected units per active user per day (catalog fetch + per-video metrics)
- Request a quota increase via Google Cloud Console if needed
- Simulate a 403 and confirm `analytics.py` retries with backoff
- Document quota limits and per-user unit cost in `docs/DECISIONS.md`

**Acceptance criteria**:
- [ ] Quota limits documented with units-per-user estimate for beta
- [ ] Quota increase requested (or confirmed sufficient) before inviting friends
- [ ] 403 response from YouTube API triggers exponential backoff (test passes or manual verification)
- [ ] Quota headroom: at least 3× expected daily usage for beta group

---

## Issue 28: Beta go-live smoke test + friend onboarding
**Depends on**: 23–27

**What**: Run the full user journey end-to-end on the live deployment, then invite 2–3
close YouTube friends. Monitor logs for 48 hours before expanding the invite list.

**Pre-invite checklist**:
- [ ] Full pipeline smoke test: OAuth → link a video → ingest → transcribe → signals → DNA build → clip candidates → render → review UI
- [ ] Celery Beat tasks confirmed running: `purge_stale_source_media`, `refresh_youtube_analytics`, `poll_clip_outcomes`
- [ ] Rate limits verified live (hit the LLM rate limit endpoint; confirm 429 response)
- [ ] Account deletion flow works on the live DB
- [ ] `docker compose logs --tail 200 app worker` clean (no unhandled exceptions)
- [ ] Browser console clean on index, review, onboarding, and profile pages

**Onboarding**:
- [ ] Each friend added as a Google OAuth test user (Issue 26)
- [ ] Share URL + brief instructions (connect YouTube, wait for DNA build, try the review queue)
- [ ] Monitor logs for 48 hours; document any issues in `docs/PROJECT_STATE.md`

**Acceptance criteria**:
- [ ] At least 2 friends complete onboarding and generate their first clip candidates
- [ ] No data-isolation breach between creator accounts (verified in DB)
- [ ] No PII or tokens visible in logs
- [ ] BETA_DEPLOYMENT phase declared done in `docs/PROJECT_STATE.md`

---

## PRODUCTION_DEPLOYMENT Phase (Issues 29–30)

Goal: scalable, verified, publicly launchable infrastructure. Start after beta is stable.
K8s provider decided and Helm charts written (Issue 22 — GKE Autopilot + KEDA + PgBouncer).
Billing implemented (Issue 21 — minute packs + Stripe Checkout).

---

## Issue 29: Google OAuth app verification
**Depends on**: 28 (needs a stable production URL and privacy policy live)

**What**: Submit the Google OAuth consent screen for verification. Required to move from
Testing status (100-user limit) to Published (unlimited users).

**Steps**:
- Ensure TOS and Privacy Policy pages are live at `agenticlip.studio` (already built — Issue 14)
- Prepare scope justification for each requested YouTube scope
- Submit for verification via Google Cloud Console → OAuth consent screen → Publish App
- Respond to Google review team requests (this process typically takes 1–4 weeks)

**Acceptance criteria**:
- [ ] App submitted for verification
- [ ] Publishing status changes from "Testing" to "In production"
- [ ] OAuth flow works for a Google account NOT in the test users list
- [ ] `docs/PROJECT_STATE.md` Pre-Public-Launch Gates: Google OAuth verification checked off

---

## Issue 30: Production hardening + public go-live
**Depends on**: 29

**What**: Run the full pre-public-launch checklist, load test the K8s deployment, and
cut the first production release.

**Pre-launch gates** (all must be green):
- [ ] All items in `docs/PROJECT_STATE.md` Pre-Public-Launch Gates checked off
- [ ] Load test: simulate 50 concurrent users running the ingest → clip pipeline; p99 latency acceptable
- [ ] `TOKEN_ENCRYPTION_KEY` rotation runbook tested end-to-end on staging
- [ ] `ALLOWED_ORIGINS` locked to production domain; `/docs` returns 404
- [ ] No virality promise in any UI or API response (structural test green)
- [ ] Monitoring + alerting live (Cloudflare Analytics + application-level error rate alert)
- [ ] Final security review: no PII in logs, no tokens in responses, per-creator isolation confirmed
- [ ] Account-deletion (right-to-erasure) tested on the production K8s environment

**Go-live**:
- [ ] `docs/PROJECT_STATE.md` updated: PRODUCTION_DEPLOYMENT phase declared done
- [ ] `docs/DEPLOYMENT.md` updated with the final production runbook
- [ ] Git tag: `v1.0.0`

---

## Issue 31: Operability kit — secrets registry, preflight doctor, deploy hardening, auto-heal
**Depends on**: nothing (cross-cuts the BETA phase)
**Status**: ✅ Done (2026-05-27)

**What**: Make secrets and deploys legible and reliable. A single secrets registry, a redacted
preflight validator, faster/more-consistent deploys, and container auto-recovery.

**Delivered**:
- `docs/SECRETS.md` — canonical registry of every secret/config value (what, where it lives, how
  to get/rotate) + the creatorclip/autoclip/agenticlip naming map.
- `scripts/doctor.py` (+ `tests/test_doctor.py`) — presence + format + live checks with redacted
  output; non-zero exit so it gates the deploy. `--full` / `--offline` / `--json` modes.
- `docs/ACCESS.md` — click-by-click SSH + CI deploy-key + Cloudflare Tunnel runbook (tailored to
  the droplet + agenticlip.studio), with key inventory/consolidation steps.
- Deploy hardening — amd64-only image build; `deploy.yml` doctor preflight before migrate/cutover;
  job/domain naming reconciled.
- `docker-compose.prod.yml` — `cloudflared` service + no host port; app/worker healthchecks; dev
  `--reload` dropped; `willfarrell/autoheal` sidecar for restart-on-unhealthy.
- Bug fix — `routers/clips.py` stale `billing.tiers` import (app could not start) → minute-packs
  `check_positive_balance` guard.

**Acceptance criteria**:
- [x] Every secret documented in `docs/SECRETS.md` with location + how-to-obtain
- [x] `python scripts/doctor.py` reports per-secret status with values redacted; exits non-zero on failure
- [x] `docs/ACCESS.md` gives concrete SSH + tunnel steps for this infrastructure
- [x] Prod compose exposes no host port; `cloudflared` fronts the app; app/worker auto-heal on unhealthy
- [x] CI image builds amd64-only; deploy runs the doctor preflight before cutover
- [x] App imports and full suite green (`313 passed`)

---

### Issue 40: Streaming upload + DoS guard
**Severity**: SEV-1 — up to 500 MB into memory per upload request
**Depends on**: 32
**Status**: ✅ Done (2026-05-28)

**What**: `routers/videos.py:90` reads `await file.read(max_bytes + 1)` — loads the entire
upload into RAM before validating size.

**Files**: `routers/videos.py:77–129`.

**Acceptance criteria**:
- [x] Upload streams to a temp file in fixed chunks (e.g., 1 MB) with running byte-count check
- [x] 413 returned as soon as max size is exceeded; partial upload deleted
- [x] Test that the API container's RSS does not balloon for a rejected oversized upload

---

## Phase 3 Backlog (post-production)

Items deferred until the product is live and stable:
- Vision signals (MediaPipe / face-emotion) — Phase 2
- Auto-publish to YouTube Shorts (additional OAuth scope)
- Multi-platform export (TikTok / Reels)
- Hot-key clipping during live recording / OBS integration
- In-app subtitle, font, crop editor on the review surface
