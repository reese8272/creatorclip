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
**Status**: ✅ Done (production live on `autoclip.studio` via Cloudflare Tunnel)

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

---

## Phase 2: Hardening & Test Coverage (Issues 32–55)

Discovered in the **2026-05-28 project-wide audit** (router-by-router read, parallel
subagent reads of `worker/`, `billing/`, `clip_engine/`, `dna/`, `ingestion/`, `preference/`,
`youtube/`, `crypto.py`, `scripts/`, plus a test-coverage gap audit).

Same Check → Approve → Build → Review loop applies; **Phase 1 must research the current
industry standard** for each fix before changing code. Dependency-ordered. Severities:
**SEV-0** blocks the app or causes data loss / cross-creator leak;
**SEV-1** security, ToS, billing, or availability gap;
**SEV-2** robustness / race / completeness;
**TESTS** missing load-bearing coverage per the 80/20 + 100%-on-load-bearing target.

> **Coverage target**: ~80% line overall, **100% line + branch on load-bearing modules**
> (`auth.py`, `crypto.py`, `routers/auth.py`, `routers/billing.py`, `routers/clips.py` render
> path, `billing/ledger.py`, `billing/stripe_client.py`, `worker/tasks.py` ingest+render+outcomes,
> `clip_engine/scoring.py`, `clip_engine/candidates.py` setup-before-peak, `youtube/oauth.py`,
> `youtube/quota.py`, every per-creator-isolation path). Not 99% — the global CLAUDE.md
> 80/20 rule explicitly bans over-testing.

---

### Issue 32: Restore test suite — `starlette` / FastAPI version mismatch
**Severity**: SEV-0 — pytest cannot collect; deploy validation impossible
**Depends on**: nothing
**Status**: ✅ Done (2026-05-28)

**What**: `python3.12 -m pytest -q` fails at import with
`TypeError: Router.__init__() got an unexpected keyword argument 'on_startup'`.2
Cause: installed `starlette==1.1.0` (published under the new `Kludex/starlette` maintainership;
starlette graduated from ZeroVer to 1.0 on 2026-03-22 — a legitimate breaking release, not a
typosquat) is incompatible with FastAPI 0.115.x which forwards `on_startup`/`on_shutdown` to
starlette's Router. The 1.x line removed those kwargs.

**Files**: `requirements.txt`, plus a `docs/DECISIONS.md` entry.

**Acceptance criteria**:
- [x] Phase 1 research: confirmed FastAPI 0.115.4's `Requires-Dist` is `starlette>=0.40.0,<0.42.0`; pinned `starlette==0.41.3` (highest within constraint); rejected uv/pip-tools migration as scope-creep for an SEV-0 hotfix
- [x] `requirements.txt` pins **both** fastapi and starlette explicitly with `==`
- [x] Fresh install in clean venv resolves to compatible versions
- [x] `pytest -q` collects and runs every test — **313 passed, 7 deselected** (the 7 are integration-marked, excluded by `pytest.ini`)
- [x] `docs/DECISIONS.md` entry — "2026-05-28 — Pin starlette explicitly to defend against transitive shadowing"
- [x] `docs/PROJECT_STATE.md` updated with current pass count

---

### Issue 33: Cross-creator data leak in `/creators/me/improvement-brief`
**Severity**: SEV-0 — other creators' analytics sent to Claude for the requesting creator
**Depends on**: 32
**Status**: ✅ Done (2026-05-28)

**What**: `routers/improvement.py:28` ran `select(VideoMetrics).limit(50)` with **no
`creator_id` filter**. The averages built at lines 30–43 and embedded in the Claude prompt
mixed all creators' data into one creator's brief.

**Files**: `routers/improvement.py`, `tests/test_improvement_isolation.py` (new),
`docs/COMPLIANCE.md`.

**Acceptance criteria**:
- [x] Query is `select(VideoMetrics).join(Video).where(Video.creator_id == creator.id).order_by(VideoMetrics.fetched_at.desc()).limit(50)` — ORDER BY added for determinism (was non-deterministic too)
- [x] Integration test (real Postgres): seeds creator A (avg_views=1,000, 5 videos) + creator B (avg_views=999,999, 5 videos); asserts creator A's analytics dict to Claude receives `videos_in_db=5` and `avg_views≈1,000` (not 10 / not 500,500 / not 999,999)
- [x] Second integration test: zero-data creator → HTTP 400 `"Not enough data — link some videos first."` instead of feeding `None` averages to Claude
- [x] `docs/COMPLIANCE.md` "Findings & Fixes Log" section added with the 2026-05-28 entry
- [x] Defense-in-depth RLS follow-up filed as **Issue 56** below
- ~~Audit-log entry on every improvement-brief call~~ — **dropped in Phase 1 brief**: audit-log-per-LLM-call is observability, not security. The security guarantee is the filter + the test. Adding rows to `audit_log` (currently reserved for security-critical events like `creator.deleted`) would dilute the table without strengthening isolation. If LLM observability is needed, do it uniformly across all LLM endpoints in a separate issue.

---

### Issue 34: Idempotent minute deduction on Celery retry
**Severity**: SEV-0 — billing double-charge on transient ingest failures
**Depends on**: 32
**Status**: ✅ Done (2026-05-28)

**What**: `worker/tasks.py:189` called `deduct_minutes` inside `_ingest_async` with no
per-video key. Celery's at-least-once delivery (`task_acks_late=True` + worker-crash-before-ack)
could re-run ingest and re-decrement the balance, charging up to 4× per video.

**Files**: `models.py`, `alembic/versions/0003_minute_deductions.py` (new), `billing/ledger.py`,
`worker/tasks.py`, `tests/test_billing.py`, `tests/test_billing_idempotency.py` (new),
`docs/SOT.md`, `docs/DECISIONS.md`.

**Resolution chose a ledger table over a column** for symmetry with the existing
`MinutePack` grants ledger and to enable a real billing-history surface later.

**Acceptance criteria**:
- [x] Phase 1: confirmed industry standard — Stripe `Idempotency-Key` pattern + Postgres `UNIQUE` constraint + SAVEPOINT for atomic two-write (see `docs/DECISIONS.md` 2026-05-28 entry on Issue 34)
- [x] New `MinuteDeduction` model + `0003_minute_deductions.py` migration with `UNIQUE(video_id)` idempotency key + `(creator_id, deducted_at)` index for usage queries
- [x] `deduct_for_video(video_id, creator_id, duration_s, session)` uses fast-path existence check, then SAVEPOINT-wrapped INSERT + atomic balance `WHERE balance >= n RETURNING`; rolls back on insufficient balance OR concurrent `IntegrityError`
- [x] 4 integration tests against real Postgres: sequential retry idempotency, two-coroutine concurrent race, 402-leaves-ledger-clean, audit fields persisted
- [x] `docs/SOT.md` data model section updated; `docs/DECISIONS.md` entry written
- [x] `tests/test_billing.py` mocked unit tests of the old `deduct_minutes` removed (replaced by real-DB integration tests — see comment block in the file)
- [x] Refund-on-terminal-failure spawned as new **Issue 57** below

---

### Issue 35: Idempotent DNA build — prevent orphan draft accumulation
**Severity**: SEV-0 — versioned DNA table accumulates orphans on Celery retry
**Depends on**: 32
**Status**: ✅ Done (2026-05-28)

**What**: `_build_dna_async` (worker/tasks.py:127–139) commits a draft via `dna/profile.create_draft`
then makes Voyage embedding + Anthropic brief calls. If anything after the draft commit
fails, Celery retries the whole task and `create_draft` inserts another row at version+1,
leaving the previous draft as orphan.

**Files**: `worker/tasks.py:127–139`, `dna/profile.py:30–60`.

**Acceptance criteria**:
- [ ] Either: (a) all draft + embedding + brief writes occur in one transaction, committed at the end; OR (b) task checks for an existing draft for this build-attempt-id before inserting
- [ ] Integration test: force a Voyage failure mid-build; on retry exactly one draft row exists
- [ ] `docs/DECISIONS.md` entry

---

### Issue 36: OAuth token lifecycle hardening
**Severity**: SEV-1 — zombie tokens, wasted quota, incomplete ToS revocation
**Depends on**: 32
**Status**: ✅ Done (2026-05-28)

**What**: Three related lifecycle gaps:
1. `routers/auth.py:140–157 delete_account` revokes only the **access_token** at Google; the **refresh_token** stays valid until the user manually disconnects in their Google Account.
2. `youtube/oauth.py:206–210 refresh_access_token` doesn't delete the `YoutubeToken` row on Google `invalid_grant` (HTTP 400 at refresh).
3. `youtube/data_api.py:55–76` and `youtube/analytics.py:42–57` retry-with-backoff on **every 403** regardless of whether `error.errors[].reason` is `quotaExceeded` (transient) or `authError`/`forbidden` (permanent). The beat loop keeps hitting Google for revoked creators forever.

**Files**: `routers/auth.py:140–157`, `youtube/oauth.py:201–230`, `youtube/data_api.py:55–76`, `youtube/analytics.py:42–57`.

**Acceptance criteria**:
- [x] `delete_account` revokes the refresh_token via `https://oauth2.googleapis.com/revoke?token=<refresh>`; tolerates 400 `token_revoked`/`invalid_token` as success
- [x] `refresh_access_token` deletes the `YoutubeToken` row + commits on Google `invalid_grant`
- [x] `data_api` / `analytics` inspect the response body and only retry `quotaExceeded`; on auth-error 403 raise a typed exception so the worker can clean up the token + mark creator disconnected
- [x] Tests for all three branches with mocked Google responses

**Implementation**: New `youtube/errors.py` (`YouTubeAuthError` + reason sets). See `docs/DECISIONS.md` (2026-05-28 — Issue 36). "Mark creator disconnected" is implemented as deletion of the `YoutubeToken` row, not a new `OnboardingState` value.

---

### Issue 37: External SDK timeouts + retry-with-backoff
**Severity**: SEV-1 — worker hangs on a stuck remote call
**Depends on**: 32
**Status**: ✅ Done (2026-05-28)

**What**: Anthropic, Stripe, Voyage, Deepgram, R2 (boto3) clients are constructed per-call
with no `timeout=` and no retry policy. Each SDK call can hang the worker indefinitely.

**Files**: `clip_engine/scoring.py:181`, `dna/brief.py:43–50`, `improvement/brief.py:48–55`,
`dna/embeddings.py:50,74`, `ingestion/transcribe.py:44–48`, `billing/stripe_client.py:19–20`,
`worker/storage.py:34–42`.

**Acceptance criteria**:
- [ ] Phase 1 (per `/claude-api` skill): research Anthropic SDK recommended `timeout=` / `max_retries=`; same for Stripe (`max_network_retries`), boto3 adaptive retry, Voyage tenacity-wrap, Deepgram httpx timeout
- [ ] Module-level singleton per SDK, constructed once from `config.settings`
- [ ] Per-call timeout override for known-long calls (improvement_brief with web_search may need 120s)
- [ ] Test that asserts each client config has a positive timeout

---

### Issue 38: Sync external calls inside `async def` + held DB sessions
**Severity**: SEV-1 — DB connection pool starvation under LLM load
**Depends on**: 32, 37
**Status**: ✅ Wave 1 Done (2026-05-28) — Wave 2 tracked as Issue 82

**What**: Sync calls (sync Anthropic, sync Voyage, sync Deepgram, boto3, subprocess) run
inside `async def` while an AsyncSession is open. The connection is pinned for the entire
LLM round-trip (often 10–40 s). Under any concurrent load this exhausts the pool.

**Scope split (2026-05-28)**: a full-codebase audit found 23 instances across both
class (1) sync-in-async and class (2) await-while-session-open patterns. Split into
two waves to keep PRs reviewable:

- **Wave 1 (this issue)** — Celery hot-path class (1) fixes. ✅ Done.
- **Wave 2 (Issue 82)** — AsyncAnthropic / AsyncVoyage migration in `dna/brief.py`,
  `improvement/brief.py`, `clip_engine/ranking.py`; router session-order refactor in
  `routers/auth.py`, `routers/videos.py`, `routers/clips.py`; the load test.

**Files (Wave 1)**: `worker/storage.py` (new async wrappers — `aupload_file`,
`adelete_file`, `adelete_prefix`, `alocal_path`); `worker/tasks.py`
(`_ingest_async` / `_transcribe_async` / `_signals_async` / `_render_clip_async` /
`_build_dna_async` / `_purge_stale_source_media_async` — sync calls offloaded via
`asyncio.to_thread`); `dna/embeddings.py` (`_aembed` wrapper); `tests/test_retention_tasks.py`
(patches updated for new async surface); `tests/test_worker_pipeline.py` (Issue 52
integration test patches updated for `alocal_path`).

**Acceptance criteria**:
- [x] Sync calls in the Celery ingest pipeline wrapped in `asyncio.to_thread` (Wave 1: probe_duration_s, extract_audio_wav, transcribe_audio, extract_audio_events, render_clip_file, upload_file, delete_file, Voyage `_embed`, sync Anthropic `generate_brief`)
- [→] Where Anthropic supports it, switch to `AsyncAnthropic` → **tracked as Issue 82**
- [→] Load test: 10 concurrent improvement-brief calls do not exhaust the connection pool → **tracked as Issue 82** (depends on routers/improvement.py refactor which is also in Wave 2)

---

### Issue 39: Celery event-loop strategy
**Severity**: SEV-1 — pool churn + `Future attached to a different loop` errors under load
**Depends on**: 32
**Status**: ✅ Done (2026-05-28)

**What**: Every Celery task body calls `asyncio.run(...)`, spinning up a fresh event loop
and binding the SQLAlchemy async engine pool to whichever loop touched it first. Under
concurrency this causes connection-pool churn and the "Future attached to a different loop"
class of bugs.

**Files**: `worker/tasks.py:50,60,70,82,92,105,114,124,134`, `db.py:8`.

**Acceptance criteria**:
- [x] Phase 1: research current best practice (celery-pool-asyncio, asgiref.async_to_sync, per-worker-process singleton loop)
- [x] Single shared loop per worker process OR per-worker engine that is bound at worker init
- [x] `docs/DECISIONS.md` entry with the chosen pattern + tradeoffs

---

### Issue 40: Streaming upload + DoS guard
**Severity**: SEV-1 — up to 500 MB into memory per upload request
**Depends on**: 32
**Status**: ✅ Done (2026-05-28)

**What**: `routers/videos.py:90` reads `await file.read(max_bytes + 1)` — loads the entire
upload into RAM before validating size.

**Files**: `routers/videos.py:77–129`.

**Acceptance criteria**:
- [ ] Upload streams to a temp file in fixed chunks (e.g., 1 MB) with running byte-count check
- [ ] 413 returned as soon as max size is exceeded; partial upload deleted
- [ ] Test that the API container's RSS does not balloon for a rejected oversized upload

---

### Issue 41: Replace pickle in preference model (RCE surface)
**Severity**: SEV-1 — `pickle.loads()` from a DB blob = RCE if blob is ever attacker-controlled
**Depends on**: 32
**Status**: ✅ Done

**What**: `preference/model.py:39–40` calls `pickle.loads(weights_blob)` on
`preference_models.weights_blob`. Any future write path to that column (admin import, SQL
injection elsewhere, a bug) becomes RCE in the worker.

**Files**: `preference/model.py`, `models.py PreferenceModel`.

**Acceptance criteria**:
- [x] Replace pickle with joblib + allowlist, sklearn JSON, or LightGBM native `.txt` format
- [x] If pickle truly cannot be removed, wrap the blob in an HMAC envelope (key in env) and verify before load
- [x] Test that a tampered blob is rejected

---

### Issue 42: ffmpeg / subprocess timeouts
**Severity**: SEV-1 — corrupt source file hangs a worker forever
**Depends on**: 32
**Status**: ✅ Done (2026-05-28)

**What**: `clip_engine/render.py:22–24, 49–63` call `subprocess.run(cmd, ...)` with no
`timeout=`. A malformed source video can stall ffmpeg until the Celery hard timeout (if
configured) — or indefinitely (if not).

**Files**: `clip_engine/render.py:22, 49`.

**Acceptance criteria**:
- [x] Every `subprocess.run` gets `timeout=max(120, clip_length_s * 4)`
- [x] `subprocess.TimeoutExpired` caught and surfaced as render `failed`
- [x] Test with a fake `sleep`-ing ffmpeg confirms the timeout fires

---

### Issue 43: Source-media purge correctness
**Severity**: SEV-1 — in-progress ingest can have its source deleted out from under it
**Depends on**: 32
**Status**: ✅ Done (2026-05-28)

**What**: `_purge_stale_source_media_async` (worker/tasks.py:471–503) filters by
`Video.created_at < cutoff`. A long-pending or in-progress ingest of an old upload will
have its source purged mid-pipeline.

**Files**: `worker/tasks.py:471–503`, `models.py Video` (new column + migration).

**Acceptance criteria**:
- [x] `Video.ingest_done_at: datetime | None` column + migration; set on successful ingest
- [x] Purge filter uses `ingest_done_at IS NOT NULL AND ingest_done_at < cutoff`
- [x] Test: video created 100h ago, `ingest_done_at = NULL` → NOT purged; video done 100h ago → purged

---

### Issue 44: Auth boundary hardening
**Severity**: SEV-1 — 500 disclosure, deletion DoS surface, no zero-downtime key rotation
**Depends on**: 32
**Status**: ✅ Done (2026-05-28)

**What**: Three related fixes:
1. `auth.py:43` — `uuid.UUID(payload["sub"])` raises `ValueError` on malformed sub → 500 (with stack trace in dev `ENV`).
2. `routers/auth.py:130 DELETE /me` — **no rate limit** on right-to-erasure.
3. `crypto.py:6–15` — single `Fernet`; no `MultiFernet` for zero-downtime rotation; `decrypt()` raises raw `InvalidToken` that callers handle inconsistently.

**Files**: `auth.py:43`, `routers/auth.py:130`, `crypto.py:6–15`.

**Acceptance criteria**:
- [ ] `get_current_creator` returns 401 (not 500) for any sub parse failure
- [ ] `DELETE /me` has `@limiter.limit("5/hour")`
- [ ] `crypto.py` switches to `MultiFernet([primary, previous])` keyed on `TOKEN_ENCRYPTION_KEY` + new `TOKEN_ENCRYPTION_KEY_PREVIOUS`
- [ ] `decrypt()` wraps `InvalidToken` in a typed `TokenDecryptError`
- [ ] Tests for all three branches

---

### Issue 45: Concurrent token refresh lock + Redis pool
**Severity**: SEV-2 — refresh-token race; per-call aioredis connections
**Depends on**: 32, 36
**Status**: ✅ Done (2026-05-28)

**What**:
- `youtube/oauth.py:201` — two concurrent worker tasks can race a refresh; Google rotates the refresh_token on some flows, so last-write-wins can invalidate the in-flight token.
- `youtube/quota.py:64` — `aioredis.from_url(...)` opens a new connection per `consume()` call.

**Files**: `youtube/oauth.py:201`, `youtube/quota.py:64`.

**Acceptance criteria**:
- [ ] Per-creator Redis advisory lock around the refresh branch (`SET NX` with 10 s TTL)
- [ ] Module-level singleton aioredis pool for quota
- [ ] Tests with two concurrent refreshes assert only one Google call

---

### Issue 46: Generate-clips retry safety + outcomes time-window bug
**Severity**: SEV-2 — stale retry wipes rendered clips; outcomes query grows forever
**Depends on**: 32
**Status**: ✅ Done (2026-05-28)

**What**:
- `worker/tasks.py:78–85` retries `generate_clips`, which calls `ranking.py:89` `DELETE FROM clips WHERE video_id = ...`. A stale retry from an old failed task wipes already-rendered clips.
- `worker/tasks.py:367–431` defines `cutoff_48h` but the actual WHERE only uses `cutoff_7d`; the query refetches every clip past 7d on every hourly run forever.

**Files**: `worker/tasks.py:78–85, 367–431`, `clip_engine/ranking.py:89`.

**Acceptance criteria**:
- [x] Generate-clips guards on `Clip.render_status != RenderStatus.done` before delete (DELETE excludes both `done` and `running`; idempotency early-return in `_generate_clips_async` if any `done` clip already exists for the video)
- [x] Poll-outcomes WHERE bounds: `Clip.created_at > now() - interval '30 days'`
- [x] Tests for both regressions (2 unit predicates in `tests/test_outcomes.py`; 3 integration tests in `tests/test_generate_clips_retry_integration.py`)

---

### Issue 47: Beat-job fairness on quota exhaustion
**Severity**: SEV-2 — first-by-id creators starve later ones forever
**Depends on**: 32
**Status**: ✅ Done (2026-05-28)

**What**: `_refresh_youtube_analytics_async` iterates creators in `id` order, breaks on
`QuotaExhaustedError`; next day's run starts from the same order, perpetually starving later
creators.

**Files**: `worker/tasks.py:506–549, 367–431`.

**Acceptance criteria**:
- [x] Order by `Creator.last_analytics_refreshed_at NULLS FIRST` (new column + migration)
- [x] On quota exhaustion the loop records progress and resumes from the unrefreshed slice
- [x] Test: 5 creators, quota cap of 2; over 3 runs all 5 refresh

---

### Issue 48: Per-creator isolation tests across all protected routes
**Severity**: TESTS — load-bearing isolation guarantee
**Depends on**: 32
**Status**: ✅ Done (2026-05-28)

**What**: Only 3 of ~12 protected routes have an explicit cross-creator isolation test.

**Files**: `tests/test_isolation.py` (new; integration-marked, uses real Postgres).

**Acceptance criteria**:
- [ ] Cover: `GET /videos`, `POST /videos/link`, `POST /videos/upload`, `GET /videos/{id}/status`, `POST /videos/{id}/clips/generate`, `GET /videos/{id}/clips`, `GET /clips/{id}`, `POST /clips/{id}/render`, `POST /clips/{id}/feedback`, `GET /creators/me/dna`, `POST /creators/me/dna/confirm`, `GET /creators/me/upload-intel`, `GET /creators/me/improvement-brief`, `GET /billing/balance`
- [ ] Each: seed creators A and B, assert A authenticated cannot read/modify B's row — 404 (never 200 with sanitized data)
- [ ] Run under docker-compose real Postgres

---

### Issue 49: Billing race + Stripe webhook idempotency against real Postgres
**Severity**: TESTS — load-bearing money path
**Depends on**: 32, 34
**Status**: ✅ Done (2026-05-28)

**What**: Existing `test_billing.py` uses `AsyncMock` for sessions; SEV-0 "double-deduct"
and "double-fulfill" cases cannot be caught.

**Files**: `tests/test_billing_integration.py` (new), real Postgres.

**Acceptance criteria**:
- [ ] Two concurrent `deduct_minutes` calls on a balance < their combined need — exactly one succeeds, the other 402s
- [ ] Stripe webhook called twice with the same `stripe_session_id` — `MinutePack` ledger has exactly one row
- [ ] Webhook with unknown `pack_id` — no ledger row
- [ ] Webhook with missing metadata — no ledger row

---

### Issue 50: Account-deletion cascade tests against real Postgres
**Severity**: TESTS — load-bearing privacy / right-to-erasure
**Depends on**: 32
**Status**: ✅ Done (2026-05-28)

**What**: `test_account_deletion.py` uses MagicMock; FK cascade behavior is never verified.
A weakened cascade in a future migration would silently leave PII behind.

**Files**: `tests/test_account_deletion_integration.py` (new) or expand existing.

**Acceptance criteria**:
- [ ] Real Postgres: seed creator with rows in every dependent table (`YoutubeToken`, `Video`, `VideoMetrics`, `Clip`, `ClipFeedback`, `ClipOutcome`, `RetentionCurve`, `AudienceActivity`, `Demographics`, `Transcript`, `Signals`, `CreatorDna`, `DnaEmbedding`, `PreferenceModel`, `MinutePack`, `Usage`)
- [ ] After `DELETE /me`: every dependent table has zero rows for that creator_id
- [ ] Audit-log row recorded
- [ ] Storage purge called for `source/` AND `clips/` prefixes
- [ ] Google revoke failure path: deletion still succeeds; audit + cascade still occur

---

### Issue 51: OAuth lifecycle tests
**Severity**: TESTS — load-bearing auth + ToS
**Depends on**: 32, 36
**Status**: ✅ Done (2026-05-28)

**Files**: `tests/test_oauth_lifecycle.py` (new).

**Acceptance criteria**:
- [ ] Token <5 min from expiry triggers refresh, persists re-encrypted blob, returns new access_token
- [ ] Google 400 `invalid_grant` → YoutubeToken row deleted, 401 raised
- [ ] Google 403 `quotaExceeded` → backoff and retry
- [ ] Google 403 `authError` → token deleted, no retry
- [ ] OAuth callback logs at INFO and DEBUG contain neither access_token nor refresh_token (use `caplog`)
- [ ] Authorization URL requests exactly the four documented scopes; no `youtube.upload`

---

### Issue 52: Worker pipeline integration tests
**Severity**: TESTS — load-bearing pipeline
**Depends on**: 32, 34, 39
**Status**: ✅ Done (2026-05-28)

**What**: `_ingest_async`, `_transcribe_async`, `_signals_async`, `_render_clip_async`,
`_generate_clips_async`, `_build_dna_async`, `_poll_clip_outcomes_async` have no direct
tests; `test_pipeline_trigger.py` calls the mock itself rather than the real task.

**Files**: `tests/test_worker_pipeline.py` (new) — 5 integration tests against real
Postgres, mocks at the storage / external-SDK boundary per the established codebase
pattern (no real fixture media files needed; `local_path` is mocked to yield a temp
file, external SDKs mocked at their entry points). No Celery eager mode needed —
direct `await _<task>_async(...)` invocation matches the pattern in
`test_dna_build_idempotency.py` / `test_generate_clips_retry_integration.py`.

**Acceptance criteria**:
- [x] Ingest task → storage + DB state correct, minutes deducted exactly once (2 invocations → 1 `MinuteDeduction` row, balance decremented by ceil(duration/60))
- [x] Render task retried 3× → `render_uri` set, `render_status=done`, exactly 1 Clip row
- [x] generate_clips retried after partial success → done clip preserved, no new pending rows (covers Issue 46 idempotency guard end-to-end with Signals + Transcript context)
- [x] poll_clip_outcomes computes `performed_well` against per-creator median, NOT global (Creator A median=500 → False; Creator B median=20 → True; both fed the same fetched views=100; global median would label both identically)
- [x] build_dna below `MIN_VIDEOS_FOR_DNA` → `ValueError` propagates, no `CreatorDna` draft row created (task wrapper at `worker/tasks.py:184-196` re-raises ValueError without `self.retry` — pinned by inspection; integration test calls `_build_dna_async` directly per existing `test_dna_build_idempotency.py` pattern)

---

### Issue 53: Compliance structural scan — no virality across all surfaces
**Severity**: TESTS — load-bearing honesty / ToS
**Depends on**: 32
**Status**: ✅ Done (2026-05-28)

**What**: `tests/test_compliance.py` is misnamed — it actually covers retention tasks. The
"no response promises virality" constraint is asserted only in two LLM-output paths. Need a
structural scan across every JSON response, every static HTML/CSS/JS, every Pydantic schema
description.

**Files**: rename existing `tests/test_compliance.py` → `tests/test_retention_tasks.py`;
add new `tests/test_compliance_no_virality.py`.

**Acceptance criteria**:
- [ ] Walk every public route from the OpenAPI schema; hit each with an authed test creator; assert no response body contains `viral`, `virality`, `guaranteed views`, `promise`
- [ ] Same scan across every file under `static/` and every Pydantic schema description in the OpenAPI doc
- [ ] Whitelist the named principle `Audience-fit over generic virality` by exact-match exclusion

---

### Issue 54: `scripts/rotate_token_key.py` integration test
**Severity**: TESTS — pre-public-launch gate
**Depends on**: 32, 44
**Status**: ✅ Done (2026-05-28)

**Files**: `tests/test_rotate_token_key.py` (new), real Postgres.

**Acceptance criteria**:
- [ ] Seed `YoutubeToken` rows encrypted with key A; run rotate A → B; assert all rows decrypt with B
- [ ] Inject a corrupted ciphertext mid-run; script rolls back, exits non-zero, zero rows mutated
- [ ] Script logs never contain plaintext token (verify with `caplog`)

---

### Issue 55: Bundled load-bearing test gaps
**Severity**: TESTS
**Depends on**: 32
**Status**: ✅ Done (2026-05-28)

**What**: Cluster of smaller load-bearing assertions, one test each — appended to the
existing test files rather than a new file per item.

**Acceptance criteria** (each item = one test):
- [ ] `auth.get_current_creator` returns 401 (not 500) when JWT sub UUID points to a creator deleted after token issuance
- [ ] `clip_engine.scoring.score_candidates` clamps Claude scores outside `[0, 1]`
- [ ] `routers.billing.checkout` returns 503 when `STRIPE_SECRET_KEY` is empty
- [ ] `dna.profile.confirm_draft` supersedes the previously confirmed profile (only one row in `confirmed` after confirm)
- [ ] `routers.videos.upload_video` rejects with 413 a file just over `UPLOAD_MAX_MB` and writes nothing to storage
- [ ] `preference.train.build_and_save` excludes `skip` feedback, includes `trim` as positive, weights `performed_well=True` rows 3×
- [ ] Account deletion writes audit log row even when storage purge raises
- [ ] `youtube.quota.consume` raises (not silent-allows) when Redis is unreachable
- [ ] Adversarial clip-engine eval: "loud aftermath" scenario asserts `setup_start_s` precedes the climax, not the post-peak laugh

---

### Issue 56: Evaluate Postgres Row-Level Security for tenant-owned tables
**Severity**: SEV-2 — defense-in-depth against future missed creator_id filters
**Depends on**: 32, 48
**Status**: ✅ Done (2026-05-28) — decision: **adopt now**; implementation tracked as new Issue 79

**What**: The current isolation model is application-layer always-filter — every protected
query carries `where(creator_id == ...)`. This is the 2026 industry-standard foundation,
but it failed once already (Issue 33: a missed filter leaked cross-creator analytics into
a Claude prompt). Postgres Row-Level Security (RLS) is the recommended defense-in-depth
"safety net underneath" the application filter: the database refuses to return cross-tenant
rows even when application code forgets the WHERE.

This is a **research-and-decide** issue, not a foregone implementation. The trade-offs
(per the 2026 industry surveys cited in `docs/COMPLIANCE.md`'s 2026-05-28 entry) are real:

- **Pros**: structural guarantee against missed-filter regressions; AWS / PlanetScale /
  techbuddies all recommend it for compliance-sensitive multi-tenant SaaS; properly indexed,
  it has no measurable perf cost vs. application filtering.
- **Cons**: requires `SET LOCAL app.creator_id = ...` middleware at request entry,
  alembic `CREATE POLICY` DDL on every tenant-owned table, BYPASSRLS lockdown on the
  migration role, and RLS-aware integration tests (the Issue 48 prototype needs to verify
  that with RLS enabled, an *unfiltered* query in test code also returns zero rows).

**Files (if we proceed)**: new alembic migration, request-entry middleware in `main.py`,
RLS-aware test updates, `docs/SOT.md` + `docs/DECISIONS.md` + `docs/COMPLIANCE.md` entries.

**Acceptance criteria**:
- [x] Phase 1: research current production patterns for RLS + SQLAlchemy 2.0 + async (pgbouncer compatibility pinned in DECISIONS: safe with transaction pooling, unsafe with statement pooling; we don't run pgbouncer today)
- [x] Decision documented in `docs/DECISIONS.md`: **adopt now**
- [→] If adopted: RLS policies cover every table with a `creator_id` column; migration role retains BYPASSRLS for alembic upgrades → **tracked as Issue 79**
- [→] If adopted: Issue 48 isolation tests extended to assert "unfiltered query returns zero rows for non-current creator" → **tracked as Issue 79**

---

### Issue 57: Refund on terminal ingest failure
**Severity**: SEV-2 — creator paid for a permanently-failed ingest
**Depends on**: 34
**Status**: ✅ Done (2026-05-28)

**What**: Issue 34 made minute deduction per-video-idempotent — the creator can no longer
be double-charged. But if `_ingest_async` deducts minutes and then ingest fails on every
Celery retry (max_retries=3 → 4 total attempts), the deduction sticks for a permanently-
failed ingest. The product needs a policy for this.

**Policy decided (2026-05-28 — see `docs/DECISIONS.md`)**:
- Automatic refund (no human gate)
- All terminal failures regardless of cause
- Surfaced via billing-history `MinutePack` row only; email + in-app banner split into
  new Issues 58 + 59 (require infrastructure we don't have yet)

**Files**: `billing/refund.py` (new — refund helper), `worker/tasks.py`
(`RefundOnFailureTask` base class applied to ingest_video, transcribe_video, build_signals),
`tests/test_billing_refund.py` (unit), `tests/test_billing_refund_integration.py`
(integration), `docs/COMPLIANCE.md` (new "Billing & Refund Policy" section).

**Acceptance criteria**:
- [x] Phase 1: decide refund policy + UX surface; document in `docs/DECISIONS.md`
- [x] Celery `on_failure` hook (after final retry) refunds via
      `grant_minutes(..., reason="refund", pack_id=f"refund:{video_id}")` — idempotent
      via read-then-write check on `pack_id`
- [x] Integration test: ingest fails terminally → 1 `MinuteDeduction` row + 1 refund
      `MinutePack` row → net balance change = 0
- [x] User-visible disclosure of refund policy in `docs/COMPLIANCE.md` (canonical home
      until TOS / pricing pages land in Phase 3)

---

### Issue 80: Transactional email infrastructure
**Severity**: FEATURE — enables refund email + future password-reset / verification / launch comms
**Depends on**: 57
**Status**: 🔲 Not started

**What**: We have zero email infrastructure. The first consumer is the refund-success email
deferred from Issue 57; the next consumers are password reset, email verification,
quota-warning, and launch comms.

**Open questions to research / decide in Phase 1**:
- Provider: Resend (recommended — modern API, cheap, good Python SDK) vs. Sendgrid /
  Postmark / Amazon SES?
- Templating: inline Python f-strings (KISS for v1), Jinja2 templates, or MJML for
  responsive HTML?
- DKIM / SPF / DMARC setup on `autoclip.studio`?
- Local-dev path: console-sink the email body, or use Resend's sandbox mode?
- Idempotency keys on outbound sends (avoid duplicate refund emails on retry)?

**Files (if we proceed)**: `notify/mailer.py` (new), `notify/templates/refund.txt|.html`
(new), `worker/tasks.py` (extend `RefundOnFailureTask.on_failure` to enqueue a
`send_refund_email` task), `tests/test_mailer.py` (new), `.env.example`
(`RESEND_API_KEY`, `FROM_EMAIL`).

**Acceptance criteria**:
- [ ] Phase 1: pick provider + templating approach; document in `docs/DECISIONS.md`
- [ ] Mailer module with a typed send-email API and unit-test coverage
- [ ] Refund-success email wired and triggered from `RefundOnFailureTask.on_failure`
- [ ] Local-dev sink (console or sandbox) so tests don't hit the real provider
- [ ] `docs/SECRETS.md` updated with the new env vars

---

### Issue 81: In-app notifications surface
**Severity**: FEATURE — enables refund banner + future deploy notices / quota warnings
**Depends on**: 57
**Status**: 🔲 Not started

**What**: We have no notifications system. The first consumer is the refund-success banner
deferred from Issue 57; future consumers include scheduled-maintenance notices, quota
warnings, "your trial expires in N days", and YouTube re-auth prompts.

**Open questions to research / decide in Phase 1**:
- Storage model: dedicated `notifications` table vs. a generic
  `creator_events` log we can also use for analytics?
- Delivery model: poll (`GET /api/notifications` on page load) vs. SSE / WebSocket push?
  v1 poll is fine; push is a Phase 3 lift.
- Read/dismiss state: per-notification `seen_at` timestamp + per-creator `dismissed_at`?
- UI shape: persistent banner at the top of `/dashboard`, toast on first-load only, or
  inbox-style notification center?

**Files (if we proceed)**: new alembic migration adding `notifications` table,
`models.py` (Notification model), `routers/notifications.py` (new),
`static/notifications.js` (new), `tests/test_notifications.py` (new), `worker/tasks.py`
(`RefundOnFailureTask.on_failure` adds an emit call alongside the refund).

**Acceptance criteria**:
- [ ] Phase 1: pick storage + delivery model; document in `docs/DECISIONS.md`
- [ ] `notifications` table + alembic migration
- [ ] `GET /api/notifications` + `POST /api/notifications/:id/dismiss` endpoints
- [ ] Dashboard renders pending notifications as a dismissible banner
- [ ] Refund event emits a notification when an ingest is terminally refunded

---

### Issue 82: Issue 38 Wave 2 — AsyncAnthropic + AsyncVoyage migration + router session-order refactor
**Severity**: SEV-2 — closes remaining ~9 of 23 findings from the Issue 38 audit; pool starvation under web-request load
**Depends on**: 38 ✅ (Wave 1)
**Status**: 🔲 Not started

**What**: Wave 2 of the async-correctness work split from Issue 38. Closes the
findings that require an SDK swap (sync Anthropic → `AsyncAnthropic`, sync Voyage →
async-native if available, otherwise keep the `_aembed` thread wrap) and the router
session-order refactors where the FastAPI request session is held through external
HTTP calls.

Findings carry-over from Issue 38 audit:
- **AsyncAnthropic migration**: `dna/brief.py` (`_ANTHROPIC` singleton + `generate_brief`),
  `improvement/brief.py` (`_ANTHROPIC` + `generate_improvement_brief`); `clip_engine/scoring.py`
  if it uses sync Anthropic.
- **Router session-order**: `routers/auth.py` (`/callback` holds session through 3 Google
  HTTP round-trips; `delete_account` holds session through Google revoke + boto3 prefix delete);
  `routers/videos.py:upload_video` (holds session through stream + boto3 upload);
  `routers/clips.py:generate_clips` (holds request-scoped session through LLM scoring);
  `routers/billing.py:checkout` (sync Stripe call).
- **clip_engine/ranking.py**: `generate_and_rank_clips` holds session through async LLM scoring —
  refactor into `score_and_rank` + `persist_ranked_clips` so callers can release session during
  the LLM phase.
- **Load test (carry-over Issue 38 AC)**: 10 concurrent `/creators/me/improvement-brief` calls
  must not exhaust the DB connection pool.

**Files (if we proceed)**: `dna/brief.py`, `improvement/brief.py`, `clip_engine/ranking.py`,
`clip_engine/scoring.py`, `routers/auth.py`, `routers/videos.py`, `routers/clips.py`,
`routers/billing.py`, new `tests/test_pool_starvation_load.py`,
`docs/DECISIONS.md` (entry for the AsyncAnthropic migration choice + Stripe sync-call disposition).

**Acceptance criteria**:
- [ ] All Anthropic call sites use `AsyncAnthropic`; sync `Anthropic` import removed
- [ ] All routers acquire the DB session AFTER any external HTTP / LLM round-trip — read inputs first, close, then call
- [ ] `clip_engine/ranking.py` split into compute-phase (no session) + persist-phase (own session)
- [ ] Load test: 10 concurrent improvement-brief calls under default pool size produce zero pool-exhaustion errors

---

### Issue 79: Implement Postgres Row-Level Security per Issue 56 decision
**Severity**: SEV-2 — structural defense-in-depth against cross-tenant leaks
**Depends on**: 56 ✅
**Status**: ✅ Done (2026-05-28)

**What**: Implement the RLS adopt-now decision from Issue 56. See
`docs/DECISIONS.md` 2026-05-28 entry on Issue 56 for the full implementation
sketch (table list, role split, SET LOCAL injection point, FORCE RLS,
pgbouncer-future compatibility, silent UPDATE/DELETE gotchas).

**Files (if we proceed)**: new alembic migration (CREATE POLICY on 12
tables + FORCE RLS + role grants), `db.py` (after_begin event listener
sourcing `current_creator` from FastAPI context), `config.py` +
`.env.example` (new `DATABASE_MIGRATION_URL`), `alembic/env.py` (use the
migration URL), `routers/*.py` (audit every UPDATE/DELETE for rowcount
checks), `tests/test_isolation.py` (extend per AC4),
`docs/SOT.md` + `docs/DEPLOYMENT.md` (role-split runbook).

**Acceptance criteria**:
- [x] Alembic migration creates SELECT policies on all 12 tables listed in
      Issue 56's DECISIONS entry; `FORCE ROW LEVEL SECURITY` on each
      (alembic revision `0010_rls_policies`)
- [x] Role split: `creatorclip_app` (no BYPASSRLS, not table owner);
      `creatorclip_migrate` with BYPASSRLS; new env var
      `DATABASE_MIGRATION_URL` documented in `docs/SECRETS.md`
- [x] `after_begin` event listener on `Session` sources `current_creator`
      from FastAPI request context (via `session.info["creator_id"]` set in
      `auth.py:get_current_creator`) and emits `SET LOCAL app.creator_id`
- [x] Every UPDATE / DELETE that targets a tenant-owned table checks
      `result.rowcount` and raises 404 on 0 — satisfied by construction
      (existing `session.get → mutate → commit` pattern + the two raw
      mutations targeting the exempt `creators` table). See DECISIONS entry
      for the audit summary.
- [x] Issue 48 isolation tests extended: with RLS active and Creator A in
      context, an unfiltered `SELECT * FROM <each table>` returns zero
      Creator B rows
- [x] Production runbook in `docs/DEPLOYMENT.md` covers the one-time
      `BYPASSRLS` grant on the migration role (plus passwords + ownership
      transfer + pgbouncer-future caveat)
- [x] No regression in existing test suite (381 passed, 1 skipped,
      56 deselected — +2 RLS integration tests)

---

### Phase 2 close-out gates

- [ ] Every SEV-0 and SEV-1 issue above resolved (32–47)
- [ ] Overall `pytest --cov` line coverage ≥ 80%
- [ ] Load-bearing modules (listed at top of Phase 2) ≥ 95% line + 100% branch
- [ ] Phase 2 declared done in `docs/PROJECT_STATE.md`
- [ ] `docs/DECISIONS.md` updated for every implementation choice that diverged from the obvious path
## Issue 35: Idempotent DNA build (SEV-0)
**Depends on**: 6
**Status**: ✅ Done (2026-05-28)

**What**: `_build_dna_async` committed the draft row inside `create_draft`, then made Voyage
embedding calls. On retry, `create_draft` inserted another row at version+1, leaving the prior
draft as an orphan. Fix: defer commit until all writes (draft + embeddings + onboarding state)
are staged, then flush atomically.

**Acceptance criteria**:
- [x] All draft + embedding + brief writes occur in one transaction, committed at the end
- [x] Integration test: force a Voyage failure mid-build; on retry exactly one draft row exists
- [x] `docs/DECISIONS.md` entry (2026-05-28)
- [x] Upload streams to a temp file in fixed chunks (e.g., 1 MB) with running byte-count check
- [x] 413 returned as soon as max size is exceeded; partial upload deleted
- [x] Test that the API container's RSS does not balloon for a rejected oversized upload
## Issue 44: Auth boundary hardening (SEV-1)
**Depends on**: 3, 18, 19
**Status**: ✅ Done (2026-05-28)

**What**: Three security sub-fixes: (1) malformed JWT `sub` returns 401 not 500,
(2) `DELETE /me` rate-limited to 5/hour, (3) `crypto.py` MultiFernet + typed exception.

**Acceptance criteria**:
- [x] Malformed sub → 401 (not 500); test asserts
- [x] 6th `DELETE /me` in an hour → 429; test asserts rate limit registered
- [x] Encrypt with primary, set previous-only-key, decrypt with previous; round-trip works
- [x] Decrypt of garbage → `TokenDecryptError` (not raw `InvalidToken`)
- [x] All existing tests still pass

---

## Phase 2.6 — Production Assessment Findings (2026-05-29)

Generated by `/assess` (verdict: PRODUCTION-READY = NO). Full per-finding detail
with backed fixes lives in `docs/assessment/modules/*.md` and the ranked register
in `docs/assessment/REPORT.md`. Worked one at a time (CHECK → APPROVE → BUILD →
REVIEW). Re-run `/assess` after each batch to confirm the finding clears.

## Issue 58: psycopg3 prepared statements break under PgBouncer + pool math (SEV-0)
**Depends on**: —
**Status**: Code complete (2026-05-29) — staging Locust verification pending

**What**: `db.py:14-20` `create_async_engine` does not disable psycopg3 server-side
prepared statements, but `docs/DEPLOYMENT.md:46` runs PgBouncer in transaction-pooling
mode → `prepared statement "_pg3_…" does not exist` in production (CI passes because it
hits Postgres directly). Also: per-pod pool ceiling `pool_size=10 + max_overflow=20 = 30`
exceeds the 25-conn PgBouncer sidecar; no `pool_recycle`. (scale-checklist axis A)

**Acceptance criteria**:
- [x] `connect_args={"prepare_threshold": None}` passed to the engine (`db.py`, asserted by `tests/test_db_engine_config.py`)
- [x] `pool_size`+`max_overflow` (15+5=20) ≤ PgBouncer sidecar (25); total-connections inequality recorded in `docs/DEPLOYMENT.md`
- [x] `pool_recycle=1800` set
- [ ] Verified behind PgBouncer via a Locust run (`tests/perf/`) — **deferred to staging** (no PgBouncer in CI/dev container; the misconfiguration is certain by inspection + library docs, but the green-under-load proof needs the real pooler)

## Issue 59: Render from setup_start_s, not the start_s fallback (SEV-1)
**Depends on**: —
**Status**: ✅ Done (2026-05-29)

**What**: `worker/tasks.py:291` cut the clip from `clip.start_s` (the fixed peak−75s
fallback, `candidates.py:97`), not `clip.setup_start_s` (the computed setup boundary).
Scoring, API, and the eval all key on `setup_start_s` but the rendered bytes didn't —
defeated CLIPPING_PRINCIPLE #2, the core differentiator.

**Acceptance criteria**:
- [x] Render cuts from `setup_start_s` via `_render_start_for(clip)` (coalesces to `start_s` only when the nullable `setup_start_s` is unset)
- [x] Frame-accurate ffmpeg seek — `-accurate_seek` set explicitly (already the encoding default; pinned so a future `-c copy` can't reintroduce GOP drift). The assessment's "drift" SEV-2 was a false positive for this re-encoding pipeline (see `docs/DECISIONS.md`)
- [x] DB-free unit guards (`tests/test_render.py::test_render_start_*`, `::test_render_clip_file_uses_accurate_seek_before_input`) + end-to-end integration test (`tests/test_render_setup_start_integration.py`) asserting the persisted `setup_start_s` reaches `render_clip_file`

## Issue 60: Wire the personalization loop (SEV-1)
**Depends on**: —
**Status**: ✅ Done (2026-05-29)

**What**: Personalization was unshipped. `preference/train.py:28 build_and_save` had no
caller (model never trained → `load_latest` always None), and
`clip_engine/ranking.py:26 rerank_with_preference` was never invoked (model never applied).
Ranking was DNA-only; the North-Star "learns your style" loop did not run. Also: fixed
50/50 blend with no maturity gating (no honest below-threshold fallback per CLAUDE.md).

**Acceptance criteria**:
- [x] Idempotent, self-debouncing `retrain_preference(creator_id)` Celery task (`worker/tasks.py`), enqueued from the feedback endpoint (`routers/review.py`) after each write; no-op when no new trainable feedback since the latest model version
- [x] `rerank_with_preference` called at the end of `generate_and_rank_clips`, gated on a trained model
- [x] `preference_weight(label_count)` ramps the blend: 0 below `PERSONALIZATION_THRESHOLD_LABELS`, linear to `PREFERENCE_WEIGHT_CAP` (new config) by 2× threshold; blend `(1-w)*dna + w*pref` recorded in `docs/DECISIONS.md`
- [x] DB-free unit tests (weight curve + rerank gating below/above threshold + no-model) + integration test (`tests/test_retrain_preference_integration.py`): trains v1 then self-debounces
- Deferred (explicit): `build_and_save` version-race hardening → **Issue 71**; `from_bytes` off-loop/caching → **Issues 68/71** (retrain task catches `IntegrityError` as a minimal guard meanwhile)

## Issue 61: generate_clips idempotency — stop wiping feedback/outcomes (SEV-1)
**Depends on**: —
**Status**: ✅ Done (2026-05-29, Batch 1)

**What**: `build_signals` re-enqueued `generate_clips` on every run; `generate_and_rank_clips`
did `delete(Clip).where(video_id==…)` and `Clip.feedback`/`outcome` are
`cascade=all,delete-orphan` (models.py:364/367). A redelivery (acks_late) silently destroyed
creator feedback labels + published-clip outcomes → corrupted the preference training signal.

**Acceptance criteria**:
- [x] `generate_and_rank_clips` is idempotent — early-returns existing clips (in rank order) instead of delete+reinsert when clips already exist for the video; never cascade-wipes feedback
- [x] Integration test (`tests/test_generate_clips_idempotency_integration.py`): re-run generation → existing clip + its feedback survive

## Issue 62: Celery delivery safety — reject_on_worker_lost + time/visibility limits (SEV-1)
**Depends on**: 61
**Status**: ✅ Done (2026-05-29, Batch 1)

**What**: `worker/celery_app.py` set `acks_late=True` without
`task_reject_on_worker_lost` → an OOM-killed media task was silently dropped (video stuck
forever). No `task_time_limit`/`task_soft_time_limit` and no broker `visibility_timeout`
override → a long task redelivered while still running → double execution. (axis C)

**Acceptance criteria**:
- [x] `task_reject_on_worker_lost=True` (safe with Issue 61)
- [x] `task_soft_time_limit=3000` < `task_time_limit=3300` < `broker_transport_options.visibility_timeout=3600` — invariant guarded by `tests/test_celery_config.py`
- [x] `render_clip` idempotent — `_render_clip_async` early-returns when `render_status==done` and `render_uri` set; integration test asserts no re-encode/storage I/O

## Issue 63: Idempotent build_dna on redelivery (SEV-1)
**Depends on**: —
**Status**: ✅ Done (2026-05-29, Batch 2)

**What**: `create_draft` derived version via `max(version)+1`; a post-commit redelivery
inserted a second draft (duplicate DNA + double Anthropic/Voyage spend). `confirm_draft`
also lacked locking (two confirms → two `confirmed`).

**Acceptance criteria**:
- [x] `build_dna` idempotent on a stable key — the Celery `task_id` (`self.request.id`) is stamped as `creator_dna.build_job_id`; `_build_dna_async` early-returns before the paid LLM/Voyage calls when a draft for that job already exists (migration `0005`)
- [x] Partial unique index `uq_one_confirmed_dna_per_creator ON creator_dna(creator_id) WHERE status='confirmed'` + `with_for_update()` in `confirm_draft` (ordered flush: supersede before promote, since the index is non-deferrable; `IntegrityError` backstop)
- [x] Integration tests (`tests/test_dna_idempotency_integration.py`): same job_id twice → one draft + one brief call; confirm twice / new draft → exactly one confirmed

## Issue 64: Self-idempotent grant_minutes (SEV-1)
**Depends on**: —
**Status**: ✅ Done (2026-05-29, Batch 2)

**What**: `grant_minutes` was not self-idempotent; money-credit idempotency rode on a caller
TOCTOU check backstopped only by `UNIQUE(stripe_session_id)`, and the race loser raised an
uncaught 500. Hardened at the source, mirroring `deduct_for_video`.

**Acceptance criteria**:
- [x] Fast-path existence check (keyed grants) + `begin_nested()` SAVEPOINT + `flush()` + `IntegrityError` catch → idempotent no-op on duplicate delivery
- [x] Concurrent-grant integration test (`tests/test_billing_grant_idempotency_integration.py`): two sessions, same `stripe_session_id` via `asyncio.gather` → one MinutePack, balance credited once

## Issue 65: pgvector HNSW index + missing FK indexes (SEV-1)
**Depends on**: —
**Status**: ✅ Done (2026-05-29, Batch 3)

**What**: `dna_embeddings.embedding Vector(1024)` had no HNSW/IVFFlat index → O(rows) `<=>`
cosine scans that degrade as the corpus grows. `clip_feedback.creator_id` was an unindexed
FK hit by the preference training query + retrain debounce. (axis H)

**Acceptance criteria**:
- [x] Alembic migration `0006`: `CREATE INDEX CONCURRENTLY ix_dna_embeddings_hnsw ... USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=200)` in an `autocommit_block` (online-safe); op class matches the `<=>` query
- [x] `CREATE INDEX CONCURRENTLY ix_clip_feedback_creator_id ON clip_feedback (creator_id)`
- [x] Integration test (`tests/test_vector_index_integration.py`) introspects `pg_indexes` for both
- Scope correction: `dna_embeddings.creator_id` (indexed in 0001) and `preference_models.creator_id` (covered by the `(creator_id, version)` unique index) needed no new index — assessment was imprecise; see `docs/DECISIONS.md`

## Issue 66: Move the 120s improvement brief off the API event loop (SEV-1)
**Depends on**: —
**Status**: ✅ Done (2026-05-29, Batch 4a)

**What**: `routers/improvement.py:65` called the synchronous 120s Anthropic+web_search
`generate_improvement_brief` directly inside an `async def` handler → pinned the event
loop, collapsing p99 for every concurrent request on that worker. (axis B)

**Acceptance criteria**:
- [x] Brief generation offloaded via `await asyncio.to_thread(generate_improvement_brief, ...)` — frees the loop
- [x] Integration test asserts the call is offloaded (recorded through a to_thread shim)
- Follow-up (Issue 75): the request still runs up to 120s (can exceed an LB/gateway timeout); the full 202/poll Celery UX is tracked there. `to_thread` resolves the axis-B loop-blocking now.

## Issue 67: Move synchronous large-file upload off the API loop (SEV-1)
**Depends on**: —
**Status**: ✅ Done (2026-05-29, Batch 4a)

**What**: `routers/videos.py` called synchronous `upload_file` (boto3 R2 PUT / `shutil.copy2`)
inside `async def upload_video` → a multi-hundred-MB write blocked the loop. Same class:
`delete_prefix` in `delete_account`.

**Acceptance criteria**:
- [x] `await asyncio.to_thread(upload_file, …)` in upload; `await asyncio.to_thread(delete_prefix, prefix)` in delete_account
- [x] Integration test asserts the storage write is offloaded; existing upload/streaming tests still pass

## Issue 68: Sync LLM/Voyage/transcription off the worker loop + timeouts (SEV-1)
**Depends on**: —
**Status**: ✅ Done (2026-05-29, Batch 4b)

**What**: Sync calls ran on the worker's singleton event loop and transcription had no
upper bound: `generate_brief` (Anthropic), `_embed` (Voyage, tenacity sleeping on the
loop), `transcribe_audio` (Deepgram/AssemblyAI/WhisperX), `extract_audio_events` (librosa).
(axes B/E)

**Acceptance criteria**:
- [x] All four offloaded via `await asyncio.to_thread(...)` (Voyage in `dna/embeddings.py`; brief/transcribe/audio-events at the `worker/tasks.py` call sites)
- [x] `TRANSCRIPTION_TIMEOUT_S` (config + `.env.example`, default 300) applied as a job-level bound via `asyncio.wait_for(asyncio.to_thread(transcribe_audio, …), timeout=…)`
- [x] Tenacity backoff no longer sleeps on the event loop (the whole `_embed` runs in a thread)
- Follow-up (Issue 75): SDK-native request timeouts (Deepgram/AssemblyAI) — `wait_for` bounds the job but can't kill the worker thread; the SDKs aren't installed here to verify their timeout params. Voyage already self-bounds (timeout=30).

## Issue 69: Fix prompt caching — split static/volatile blocks (SEV-1)
**Depends on**: —
**Status**: ✅ Done (2026-05-29, Batch 5)

**What**: both briefs interpolated per-creator data INTO the `cache_control` system block
(prefix changed every call); `improvement/brief.py` returned `text_blocks[0]` — with
web_search that's the "let me search…" preamble, not the answer.

**Acceptance criteria**:
- [x] System split into a static cached prefix + a separate uncached volatile block (both briefs); verified via `/claude-api`
- [x] `improvement` returns the final text block (`[-1]`) after the last tool_use; dna uses `[-1]` for consistency; multi-block fixture test
- [x] DB-free unit tests assert the split shape (no creator data in the cached block) + the final-block extraction; existing `test_generate_brief_uses_prompt_caching` updated to the 2-block contract
- **Finding (`/claude-api`):** Sonnet 4.6's minimum cacheable prefix is 2048 tokens; these static prefixes are ~350-450 tokens, so the cache does NOT engage for these low-frequency calls regardless of structure. The split is correct-structure, not a cost win here. The real caching beneficiary — the clip scorer's large per-creator prefix reused across videos — is tracked under Issue 75. (see `docs/DECISIONS.md`)

## Issue 70: Bound poll_clip_outcomes quota drain (SEV-1)
**Depends on**: —
**Status**: ✅ Done (2026-05-29, Batch 6)

**What**: `poll_clip_outcomes` re-polled every `ClipOutcome` 7 days after its last fetch
with no terminal marker → unbounded YouTube-quota drain; also held one session across an
N×M awaited-network loop. (axes E/F)

**Acceptance criteria**:
- [x] `clip_outcomes.final` column (migration `0007`) + partial index; the 7d checkpoint poll sets `final=True` and the query excludes `final IS TRUE`
- [x] Candidate set capped to clips created within ~10 days (`Clip.created_at >= now-10d`)
- [x] Commit per creator so a slow YouTube call can't hold one transaction across the batch
- [x] Integration test: a 7d poll marks `final`; a finalized outcome is never polled again

## Issue 71: Preference unpickler thread-safety + version race (SEV-1)
**Depends on**: —
**Status**: ✅ Done (2026-05-29, Batch 7)

**What**: `from_bytes` monkeypatched a joblib module global during load → not thread-safe
(the RCE allowlist could be defeated under concurrent loads); `build_and_save` `max()+1`
raced to `IntegrityError`; `predict_score` swallowed all errors into `0.5`.

**Acceptance criteria**:
- [x] The global swap is serialized by a module `threading.Lock` (a directly-instantiated unpickler was rejected — joblib's `NumpyUnpickler` signature is version-fragile; documented in DECISIONS)
- [x] Version assignment serialized via `pg_advisory_xact_lock(hashtext(creator_id))` (held to commit)
- [x] `predict_score` validates `n_features_in_` and **raises** on drift; `load_latest` returns `None` on feature-schema drift; `rerank_with_preference` scores all clips before mutating and falls back to DNA order if the scorer raises
- [x] DB-free unit tests: predict_score raises on feature mismatch; rerank falls back to DNA when the scorer raises

## Issue 72: OAuth httpx singleton + timeouts (SEV-1)
**Depends on**: —
**Status**: ✅ Done (2026-05-29, Batch 4b)

**What**: `youtube/oauth.py` built a fresh `httpx.AsyncClient` per call with no timeout on
the token-refresh hot path; `data_api`/`analytics` constructed the client inside the retry
loop (no connection reuse). (axes B/E)

**Acceptance criteria**:
- [x] New `youtube/_http.py`: lazy per-process singleton `client()` (`Timeout(15, connect=5)`) + `aclose()`; reused by all three OAuth helpers + `_get_json` + `_fetch_report`; closed in the API lifespan and worker shutdown
- [x] 5xx responses get backoff-retry before `raise_for_status` (idempotent GETs)
- [x] Unit tests: singleton identity + recreate-after-close; 503-then-200 backoff via httpx MockTransport. Existing oauth-lifecycle tests rebased onto the `_http.client` boundary
- Note: token-refresh logging already emits only a message + creator id (no exception object) — verified, no change needed

## Issue 73: Pydantic response_model + input validation on routes (SEV-2)
**Depends on**: —
**Status**: Partially done (2026-05-29, Batch 8) — security item done; response_model coverage tracked in Issue 75

**What**: `youtube_video_id` was an unvalidated `Form(...)` interpolated into a storage key;
most endpoints return a bare `dict` with no `response_model`.

**Acceptance criteria**:
- [x] `youtube_video_id` validated against `^[A-Za-z0-9_-]{11}$` (422 on bad input) on both `/videos/link` and `/videos/upload`, before the value reaches a storage key — DB-free unit test
- [x] A Pydantic `*Out` model + `response_model=` on every endpoint — DONE (action #3). 18 endpoints across 7 routers now declare a `response_model` (typed OpenAPI + response-side field allow-list). Standing guard `tests/test_response_models.py` fails if a future documented JSON route ships without one.

## Issue 74: Bound transcription/audio memory (SEV-2)
**Depends on**: —
**Status**: Done (2026-05-29, Batch 8) — Deepgram-stream item deferred to Issue 75

**What**: `librosa.load(sr=None)` decoded the full waveform at native rate (≈690 MB/hr) → OOM
vector; WhisperX model + SDK clients reconstructed per call.

**Acceptance criteria**:
- [x] `librosa.load(sr=16000)` — ~3× less memory; heuristics need no more fidelity (the universal path; verified locally)
- [x] WhisperX model + align model cached via `lru_cache`; Deepgram client + AssemblyAI key as module-level singletons
- [ ] Deepgram full-file `f.read()` → file-stream — **deferred (Issue 75)**: the deepgram SDK isn't installed here to verify the streaming API, and `sr=16000` already removes the dominant memory vector

## Issue 75: SEV-2 / cleanup long tail + dependency CVEs + compliance (tracking)
**Depends on**: —
**Status**: Open (tracking) — two concrete items done; research/infra items remain

**Done in Batch 8:**
- [x] (c) Stripe-key **prod fail-fast** — `config.py` `model_validator` requires `STRIPE_SECRET_KEY`/`STRIPE_WEBHOOK_SECRET` when `ENV=production` (DB-free unit test)
- [x] (d) `upload_intel/timing.py` `IndexError`→500 — out-of-range `day_of_week`/`hour` rows skipped (DB-free unit test)

**Done 2026-05-29 (CVE remediation session):**
- [x] (a) **14 pip-audit CVEs → 0.** Patched 6 packages (cryptography→46.0.7,
  python-multipart→0.0.27, PyJWT→2.12.0, lightgbm→4.6.0, python-dotenv→1.2.2,
  starlette→0.49.1 via FastAPI→0.120.4); 2 residuals accepted-risk in
  `gate_pip_audit`'s `--ignore-vuln` allowlist (pytest dev-cascade GHSA-6w46-j5rx-g56g;
  starlette Host-header PYSEC-2026-161, fixable only on the starlette-1.x line) with
  DECISIONS justification. `pip_audit_vulns` baseline ratcheted 14→0; full suite green.

**Remaining (each its own focused effort — not single-commit changes):**
- [ ] **starlette-1.x migration** (FastAPI→0.136.x) to close PYSEC-2026-161 and drop it
  from the ignore-list — a major-line bump; do as its own issue with a full test run.
- [ ] (b) YouTube **analytics retention/refresh cadence** vs ToS (`youtube/analytics.py`, COMPLIANCE.md §2) — needs the actual ToS cadence figure, then a scheduled refresh/purge
- [ ] (e) ratchet `mypy_errors` 30→0, then enable `disallow_untyped_defs`
- [x] (f) observability — DONE (2026-05-29). `observability.py`: pure-ASGI
  `RequestIDMiddleware` (mint/echo `X-Request-ID`, ContextVar) + JSON structured logs
  with `request_id` + Prometheus golden signals (`http_request_duration_seconds`,
  `celery_task_*`) at `/metrics`; correlation id propagated API→Celery via
  before_task_publish/task_prerun/task_postrun signals. +9 tests. See DECISIONS
  2026-05-29. Follow-up: OpenTelemetry distributed tracing (deferred).
- [x] **Full `response_model` coverage** across the 18 endpoints (from Issue 73) — DONE (action #3).
  `*Out` models + `response_model=` on every documented JSON endpoint in 7 routers; standing guard
  `tests/test_response_models.py`. Faithful field-for-field modeling verified by the full endpoint suite.
- [x] **Deepgram file-stream** upload (from Issue 74) — DONE (action #2). `transcribe.py`
  streams the open file handle (`FileSource.buffer` accepts a `BufferedReader`) instead of
  `f.read()`, so httpx uploads in chunks and the ~115 MB/hr WAV is never held in a Python
  bytes object. Plus a `TRANSCRIPTION_MAX_MB` fail-fast size guard before any read/upload.
- [x] **SDK-native transcription timeout** (Deepgram/AssemblyAI) — DONE (action #2). New
  `TRANSCRIPTION_HTTP_TIMEOUT_S` (default 120, kept < the 300s job `wait_for`): Deepgram gets
  an `httpx.Timeout` per `transcribe_file`; AssemblyAI sets `aai.settings.http_timeout`. A hung
  provider socket now returns the blocking thread before the job timeout (which can't cancel it).
- [ ] **Clip-scorer prompt caching** — the real caching beneficiary (large per-creator prefix reused across videos), from Issue 69. Re-run also flagged the cheap prefix-ordering win: put the static principles block BEFORE `{dna_brief}` in `clip_engine/scoring.py:182-191` so the long static prefix is shared across creators
- [ ] **Per-(creator, version) scorer cache** so `from_bytes` runs once, not per rerank (from Issue 71) — confirmed still absent: `preference/train.py:116` deserializes on every rerank (`clip_engine/ranking.py:39`)
- [x] **Improvement-brief 202/poll** Celery UX (the 120s request can exceed an LB timeout; from Issue 66) — done in Issue 78d (2026-05-30)
- [ ] ~23 remaining SEV-2 + ~24 cleanup items in `docs/assessment/modules/*.md` — see Issue 76 for the net-new ones from the re-run

---

## Issue 76: Re-assessment re-run findings (2026-05-29, post-hardening /assess)
**Depends on**: —
**Status**: Open (tracking) — net-new findings from the post-Issues-58–75 `/assess` re-run.
Verdict moved NO → **CONDITIONAL** (0 BLOCKER, 4 SEV1, 23 SEV2, 24 cleanup). Full register and
backed fixes in `docs/assessment/REPORT.md` + `docs/assessment/modules/*.md`; snapshot in
`docs/assessment/history/2026-05-29-rerun-post-hardening-REPORT.md`.

**SEV1**
- [x] **`build_dna` concurrent-redelivery double-spend** (`worker/tasks.py:423-430` + `dna/profile.py:52-55`).
  The idempotency re-check ran in its own closed session, not serialized against the draft INSERT;
  `build_job_id` was non-unique. Serial redelivery was safe (Issue 63), but two *concurrent* same-`job_id`
  deliveries both ran the paid Anthropic brief + Voyage embeddings before the version UNIQUE collided,
  and the loser raised → Celery retries. **DONE (action #1):** per-creator `pg_advisory_xact_lock` at
  the top of the build txn with the re-check under it + partial UNIQUE on `build_job_id WHERE NOT NULL`
  (migration 0008) + IntegrityError→no-op. Concurrent-redelivery regression test verified on real PG.

**SEV2 (net-new)**
- [ ] clip_engine `ranking.py:129` — `dna_match` seeded to the composite score, never refined →
  preference model fed a duplicate of its own target as a "DNA-fit" feature (collinear). Persist a
  DNA-only fit distinct from `clip.score`, or rename to `seed_score`.
- [ ] clip_engine `candidates.py:94-113` — candidate windows never deduped; adjacent peaks can yield
  near-identical clips (vs principle #9). Drop candidates overlapping a kept one >50% IoU.
- [x] DONE (Issue C) — clip_engine `routers/clips.py:67` — `extract_candidates`/`compute_features` CPU runs on the
  FastAPI loop. Dispatch to Celery (202) or `asyncio.to_thread`.
- [x] DONE (Issue A) — youtube `oauth.py:303-313` — lock-wait re-read hits the identity map (`expire_on_commit=False`)
  → stale token → spurious 503 under concurrent refresh. `session.refresh`/`populate_existing=True`.
- [x] youtube `quota.py:51` — DONE (beta-blocker). Daily counter now keyed by the
  `America/Los_Angeles` date (Google's reset zone) via `_QUOTA_RESET_TZ`, so it rolls over with
  Google's quota instead of ~8h early on the UTC date. Regression test pins a UTC-vs-PT split day.
- [x] DONE (Issue A) — youtube `ingest.py:44-62` — `extract_audio_wav` `subprocess.run` has no `timeout=`. Add bounded
  timeout (∝ duration, floor ~600s); map `TimeoutExpired`→RuntimeError.
- [x] DONE (Issue A) — youtube `analytics.py:51`/`data_api.py:93` — 429 backoff ignores `Retry-After`. Honor it.
- [ ] worker `tasks.py:547-556` — `poll_clip_outcomes` doesn't `break` on quota exhaustion (vs
  analytics refresh). Catch `QuotaExhaustedError` and break.
- [ ] worker `tasks.py:357-394` — `_render_clip_async` not concurrent-safe: two workers both read
  `pending`, both encode+upload same key. `with_for_update()` + re-check under lock.
- [ ] worker `tasks.py:222-259` — `_ingest_async` re-extracts/re-uploads the derived WAV on redelivery
  (no corruption, wasted ffmpeg+R2). Short-circuit when `source_uri` is already the derived key.
- [x] DONE (Issue B) — dna `builder.py:223-224,137-161` — `_enrich_video` N+1: ~60 serial queries/build. Batch into
  3 `IN (...)` queries.
- [x] DONE (Issue B) — dna `builder.py:107-117` — `rank_videos` unbounded fetch into worker memory. Cap with
  `.limit(DNA_MAX_CANDIDATE_VIDEOS=500)`.
- [x] DONE (Issue B) — dna `builder.py:201-202` — `kind` compared against bare string literals vs `VideoKind` enum
  value → a rename silently empties buckets. Compare against `VideoKind.long.value`.
- [ ] routers list endpoints (`videos.py:40-55`,`clips.py:93-99`,`upload_intel.py:22-25`) — unbounded
  `list(scalars())`. Add keyset/offset pagination with a hard cap (100).
- [ ] routers `videos.py:62,93` — `link_video`/`upload_video` raw `Form(...)` with no request model
  (id regex-validated). Wrap in a body model or record the multipart deviation in DECISIONS.
- [x] _root_infra `main.py:102-107` — DONE (beta-blocker). `/metrics` now gated behind a
  `METRICS_TOKEN` bearer token (constant-time compare); config fails fast in production if metrics
  are enabled without a token, so the scrape surface can't be exposed unauthenticated. Empty token
  = open for dev/internal-network. Tests cover the gate + the prod fail-fast.
- [ ] _root_infra `observability.py:189-211` — correlation-id ContextVars are safe only under the
  prefork pool. Assert/document the prefork assumption, or key task start off `task.request`.
- [x] billing `ledger.py:89-92` — DONE (beta-blocker). The non-keyed (trial/manual) path now
  re-raises IntegrityError instead of swallowing it, so a new beta user can't silently get 0 trial
  minutes; the keyed (Stripe) path still no-ops on the UNIQUE race. Integration test covers it.
- [ ] upload_intel `timing.py:54-55` — `optimal_gap_hours` left out of the 75d bounds/coercion guard;
  the two functions disagree on a valid row. Filter+coerce first (mirror `best_upload_windows`).
- [ ] ingestion `transcribe.py:71-85,99-110` — hosted-provider normalizers use hard-key indexing →
  opaque `KeyError` on a missing timestamp. Switch to `.get(..., default)` (WhisperX already does).

**cleanup (24)**: typing gaps the mypy ratchet will catch, DRY extractions, magic-constant naming,
the clip-scorer cache-prefix ordering. Per-finding detail in `docs/assessment/modules/*.md`.

---

## Issue 77: Beta UX polish + brand rename to AutoClip
**Depends on**: —
**Status**: Done (2026-05-30) — caught during a real device walkthrough of the beta UI.

- [x] **Brand → AutoClip.** Renamed the user-facing brand from "CreatorClip" to "AutoClip"
  across `static/*` + `auth.js`, the user-facing brief disclaimers (`dna/brief.py`,
  `improvement/brief.py`), the CLAUDE.md honesty constraint sentence, and the two
  brand-asserting tests. ("Creator DNA" the *feature* is unchanged.) **Internal identifiers
  intentionally left as `creatorclip`** (package, docker image, DB role, Redis key prefix,
  docs) — a deeper rename is a separate, larger task if wanted.
- [x] **Dashboard "undefined / vundefined".** `index.html` read `dna.status`/`dna.version`
  but `/creators/me/dna` nests them under `.profile`. Now reads `dna.profile?.status/version`
  and shows "Not built" when null.
- [x] **Channel-data raw JSON.** `onboarding.html` step 2 used htmx to swap the data-gate
  JSON straight into the page. Replaced with a JS render (✓/• per type + friendly summary),
  loaded on page open and on refresh.
- [x] **"Queued (task <uuid>)" + "what does queued mean".** Build-DNA no longer leaks the
  Celery task id; copy explains what's happening and then **polls `/creators/me/dna`** and
  flips to "Your Creator Brief is ready — review & confirm" when the draft lands.
- [x] **API path leaked as UI copy.** Backend `get_dna` "No DNA profile yet. POST
  /creators/me/dna/build…" → friendly "No Creator DNA yet — build it from the setup screen…".

---

## Issue 78: Salvage net-new work from closed PR #6 (`claude/busy-mendel-1r2oZ`)
**Depends on**: —
**Status**: Open — re-implement cleanly on top of current `main` (do NOT merge the old branch).

PR #6 was a parallel earlier-session workstream that branched off pre-PR#5 `main` and
re-did much of Issue 75 (CVEs, observability, response_model — all now already on `main`
via #4/#5). It conflicted across 18 files and was **closed without merging** to avoid
regressing `main`. These items it contained are **genuinely not yet on `main`** — rebuild
each fresh, test-gated, against current `main` (the old commits remain in git history on
the retired branch for reference):

- [x] **Per-(creator, version) preference-scorer cache** (Issue 78a, 2026-05-30) — so
  `from_bytes` runs once, not per rerank (also tracked under Issue 76). Per-worker bounded
  LRU keyed by `(creator_id, version)` in `preference/_scorer_cache.py`; `load_latest` now
  does a cheap version+schema query, returns the cached scorer on hit, fetches+deserializes
  the blob only on miss. Monotonic versions ⇒ free invalidation. +5 DB-free tests.
- [x] **Clip-scorer prompt caching (1h TTL)** (Issue 78b, 2026-05-30) — the real caching
  beneficiary (large per-creator prefix reused across videos); also Issue 76. Split the
  system into a static instructions+principles block (stable, first) and a per-creator
  `CREATOR DNA:` block carrying a `{"type":"ephemeral","ttl":"1h"}` breakpoint; candidates
  stay in the uncached user message. Extended TTL (now GA, no beta header) widens the reuse
  window from 5 min → 1h so a creator's batch of videos still hits. Prefix-ordering fix
  (static-first) done; verified via `/claude-api`: at Sonnet-4.6's 2048-token floor only the
  `[static+DNA]` per-creator prefix caches (static alone ~400 tok is sub-floor — the
  cross-creator share is future-proofing, not a present win).
- [x] **mypy 30 → 0** then enable `disallow_untyped_defs` (Issue 75e ratchet) — done (Issue
  78c, 2026-05-30). Enabled the `pydantic.mypy` plugin (−9 config false positives); real fixes
  in `preference/train.py` (loop-var shadow + explicit ndarray types), `youtube/oauth.py`
  (`if creator is None` narrowing), `worker/tasks.py` (None-guard before `delete_file`),
  `preference/model.py` (dropped 2 stale ignores); targeted `# type: ignore[...]` for
  third-party stub lag (anthropic 0.40 `cache_control`/server-tool params, redis async `eval`,
  cv2 `.data`, slowapi handler). Flipped on `disallow_untyped_defs` +
  `disallow_incomplete_defs`; baseline ratcheted 30→0. 431 passed; ruff 0.
- [x] **Improvement-brief → 202 + poll** (Issue 78d, 2026-05-30) — async Celery job (the
  120s request can exceed an LB timeout; also Issue 76). New `ImprovementBrief` model +
  `improvement_brief_status` enum (one row per creator) + migration 0009. `POST
  /creators/me/improvement-brief` now returns 202, debounces an in-flight build, and enqueues
  `generate_improvement_brief`; the worker builds the creator-scoped analytics + DNA brief and
  runs the LLM call (idempotent + safe-fail); `GET` polls the stored row; `insights.html`
  POST→poll. Mirrors the DNA-build precedent. +8 integration tests (3 GET-based isolation
  tests rebased onto the task path).
- [ ] **YouTube analytics retention purge** (Issue 75b) — needs the confirmed ToS staleness
  figure, then a scheduled purge of stale VideoMetrics/RetentionCurve/etc.
- [ ] **PgBouncer load-test harness** — to actually verify the axis-A pool BLOCKER fix
  (Issue 58) under load.
- [x] **Legal routes / Limited Use / CORS lockdown** (Issue 78g, 2026-05-30) — reconciled
  with what already shipped. Verified already-done: CORS locked to `ALLOWED_ORIGINS` (never
  `*`, `main.py`); `/docs`+`/redoc` gated on `ENV=="development"`; `static/tos.html` +
  `static/privacy.html` served + tested. The genuine delta was the **Google Limited Use
  disclosure** — required by `docs/COMPLIANCE.md` in the public Privacy Policy before launch
  but absent; added the canonical attestation + the four Limited-Use commitments to
  `static/privacy.html`, with a test pinning the required language.

---

### Issue 83: Creator Intake Form — stated identity layer fused with inferred DNA
**Severity**: FEATURE — addresses "DNA takes a long time" cold-start + adds a structural honesty signal
**Depends on**: 79 ✅ (RLS — so per-creator queries on `creator_identity` are policy-gated once RLS is activated)
**Status**: ✅ Done (2026-05-30)

**What**: A self-described identity layer fused with the inferred `creator_dna`. Captures
who the creator says they are (niche, audience, mission), how they describe their voice
(tone tags), what they refuse to do (hard-nos), and an optional writing/style sample.
Used as a stable per-creator system block in every Claude call so the LLM treats the
creator's own words as authoritative for "what they're trying to build," and only uses
inferred patterns to fill in "what's actually working."

**Why**: Two problems in one. (1) The inferred DNA pipeline takes 30+s end-to-end (LLM call
+ analytics fetch + embeddings) and can't ship a useful first-pass result until everything
finishes. (2) The inferred pipeline can only see what has accidentally performed well —
it cannot see what the creator is trying to build. The stated identity ships an instant
cold-start signal and adds the dimension inference structurally lacks.

**Approach** (per the 2026 industry-standard research summarized in DECISIONS):
- Form-driven, NOT sample-text-driven (samples are already covered by the inferred path).
- 5-field intake: 1 required niche multi-select (1–3 of the YouTube Data API categories) +
  1 required audience description + 3 optional fields (mission, content pillars, tone tags,
  hard-nos, style sample).
- **Strictly separate from `creator_dna`** — two tables, fused at query time, never merged.
  Silently overriding stated intent with engagement is the YouTube-algorithm problem
  recreated inside our own tool; we surface conflicts to the creator instead.
- Append-only versioned (`creator_identity.superseded_at IS NULL` ⇔ current; partial
  unique index is the DB-level backstop).
- Multi-step UX with skip-from-step-1 affordance (research: 52.9% higher completion vs
  single-page; forced pre-value intake is the 70%-drop-off norm).

**Files (delivered)**:
- `alembic/versions/0012_creator_identity.py` — append-only table + partial unique + history index
- `models.py::CreatorIdentity`
- `youtube/categories.py` — static 15-option NICHE_OPTIONS list (YouTube Data API IDs)
- `dna/identity.py` — `get_current` / `get_history` / `upsert_identity` with FOR UPDATE +
  IntegrityError race recovery, plus `format_for_prompt` (cache-friendly: returns None
  rather than emitting "(no identity)") and `validate_*` helpers shared with the router
- `dna/conflict.py` — niche-keyword-vs-inferred-patterns mismatch detector, returns a
  one-line nudge for the UI
- `dna/brief.py` — `generate_brief()` accepts `stated_identity`; injects it as the 2nd
  system block; moves the `cache_control` breakpoint to the last stable block
- `worker/tasks.py::_build_dna_async` — fetches identity via `AdminSessionLocal` and
  passes it into `generate_brief`
- `routers/creators.py` — `GET /creators/niches` (public; intake form depends on it),
  `GET /creators/me/identity` (current + conflict nudge), `POST /creators/me/identity`
  (creates new version), `GET /creators/me/identity/history` (max 20 versions)
- `static/onboarding.html` — optional inline intake card (step 3 of 5)
- `static/profile.html` — full identity edit form, current-version summary, conflict nudge
- `tests/test_identity_unit.py` — 21 unit tests
- `tests/test_identity_integration.py` — 4 integration tests against real Postgres

**Acceptance criteria**:
- [x] `creator_identity` table created via alembic 0012; cascade-deletes with creator
- [x] Partial unique index `uq_one_current_identity_per_creator` enforces ≤1 current row
- [x] `POST /creators/me/identity` creates a new version, stamps `superseded_at` on prior
- [x] `GET /creators/me/identity` returns current row (or null) + conflict nudge (or null)
- [x] `GET /creators/me/identity/history` returns newest-first, max 20
- [x] `GET /creators/niches` returns the stable category list (works pre-session)
- [x] Validation: 1–3 niches (must be from the known list), audience required, dedup'd lists
- [x] Identity injected into `dna/brief.py` as the 2nd system block with cache_control on it
- [x] Identity is omitted entirely (not "(no identity)") when missing — cache-friendly
- [x] Conflict detector returns a niche-mismatch nudge when stated niche keywords miss patterns
- [x] Per-creator isolation: A's GET never returns B's row (integration test)
- [x] Onboarding step 3 + profile.html edit form both wired; skip path preserved
- [x] No virality language anywhere new; preserves the AutoClip honesty constraint
- [x] All gates green: ruff format/check, mypy 0, pytest passes

---

### Issue 84: AI/LLM efficiency assessment — context engineering, caching, latency vs quality
**Severity**: ASSESSMENT — informs every downstream LLM change
**Depends on**: 83 ✅ (so identity injection is in scope of the audit)
**Status**: 🔲 Not started

**What**: User asked for a focused assessment of how we use Anthropic right now:
context engineering at every call site, the actual realized prompt-cache hit rate (the
2026 5-minute TTL change in particular), and the speed-vs-quality tradeoffs at each
step. Goal: the service should feel **fast** without being **shallow**.

**Scope (do a real Phase 1 CHECK before scoping further — Anthropic SDK + caching
state move fast):**
- Audit every Anthropic call site: `dna/brief.py`, `improvement/brief.py`,
  `clip_engine/scoring.py`. Confirm block ordering, cache breakpoints, system-vs-user
  placement, max_tokens, model choice (Sonnet vs Opus for which call).
- Measure the realized cache hit rate from `cache_read_input_tokens` /
  `cache_creation_input_tokens` logs over a representative session.
- Identify pipeline opportunities to co-locate calls under one cache prefix (clip
  scoring + ranking + explanation in one pass?) to actually reap caching savings
  given the 5-minute TTL.
- Identify any call that could be batched (Anthropic batch API), streamed, or
  replaced with a smaller model.
- Evaluate latency: time-to-first-byte and total wall-clock for the DNA build (the
  motivating "DNA takes a LONG time" complaint).
- Recommend a **target SLO** per call site (e.g. "DNA brief P50 < 8s, P95 < 20s").

**Acceptance criteria**:
- [ ] Phase 1: research the current Anthropic SDK + caching best practices for 2026
- [ ] Per-call-site report in `docs/assessment/llm/<call-site>.md` with placement,
      cache hit rate, model, max_tokens, observed latency, recommended changes
- [ ] One pipeline candidate identified for co-located calls under a shared cache prefix
- [ ] At least one concrete latency win shipped (or a rationale for why no change is safe)
- [ ] DECISIONS entry capturing any model / placement / pipeline changes

---

### Issue 85: UI redesign — sleek editing-tool aesthetic (away from "AI-generated website" vibe)
**Severity**: FEATURE — pre-public-launch polish
**Depends on**: 84 ✅ (so any UI surfacing of LLM output is informed by the assessment) — soft dep
**Status**: 🔲 Not started

**What**: User flagged that current static pages (`static/index.html`,
`static/onboarding.html`, `static/profile.html`, `static/review.html`,
`static/insights.html`, `static/pricing.html`) feel like "an AI-generated website,"
not "an actual editing tool." Beta-quality is fine for current users; brand pass is
wanted before public launch. The Issue 83 identity edit form is the most recent
contributor to that vibe and should be reworked in this issue.

**Scope (Phase 1 CHECK should look at):**
- 2026 editing-tool UI references (CapCut, Descript, Riverside, Final Cut for web,
  Frame.io, Lumen5). Common shapes: timeline-first, player-first, dark base with
  saturated accent, dense info-rich panels, keyboard-driven.
- Design system foundations: typography (system stack → swap to Inter/Geist?), 8-pt
  spacing scale, color palette beyond `#6c63ff`, motion language.
- The review surface (`static/review.html`) — should it feel like a TikTok-style
  vertical scroller or a timeline-first editor? Player-first is the established design
  intent (see `docs/PRD.md` "feels like scrolling" bar).
- Whether to introduce a CSS framework (Tailwind / Open Props) or keep hand-rolled.
  The current "vanilla HTML/CSS/JS, no build step" stance is a flagged DECISIONS
  candidate per `docs/SOT.md` ("review-UI framework").
- Component-library candidates if a framework is adopted.

**Acceptance criteria**:
- [ ] Phase 1: industry references collected; framework-vs-vanilla DECISIONS entry
- [ ] Design system documented (typography, spacing, color, motion) in `docs/UI.md`
- [ ] Review surface redesigned to the chosen player-first / timeline-first shape
- [ ] Profile + onboarding + insights surfaces reworked to the design system
- [ ] Identity intake form (Issue 83) reworked in the new aesthetic
- [ ] No regression in the structural honesty test (the AutoClip predicts-fit
      disclaimer must remain visible per `CLAUDE.md`)
- [ ] Mobile-responsive baseline (90% of YouTubers check phone first)
- [ ] All a11y basics: keyboard navigation, focus rings, contrast AA on body text

---

### Issue 86: Live progress surface for long-running LLM + worker tasks
**Severity**: FEATURE — pre-public-launch polish; directly addresses today's prod incident (3+ min frozen spinner on DNA build) and provides a reusable observability primitive for every long task
**Depends on**: nothing — pure additive surface; lands cleanly before Issue 84/85
**Status**: ✅ Done (2026-05-30)

**What**: A reusable per-task progress facility on three layers — (1) **Redis Streams**
(`XADD`/`XREAD` on key `task:{task_id}:events`) as the worker→web bridge with bounded
retention (MAXLEN ~ 200) and TTL expiry, (2) a new authenticated FastAPI endpoint
`GET /tasks/{task_id}/events` returning `text/event-stream` (SSE) that tails the stream
with `BLOCK 5000` reads, sets the three Cloudflare-safe headers (`Cache-Control: no-cache`,
`Content-Type: text/event-stream`, `Connection: keep-alive`, plus nginx's
`X-Accel-Buffering: no`), emits a `: keepalive` comment every ~12s, honors `Last-Event-ID`
for reconnect, and enforces a per-creator concurrent-connection cap via Redis `INCR`/`DECR`,
(3) a tiny `worker/progress.py` helper — `sync_emit` / `aemit` `(task_id, event_type,
**fields)` — that every Celery task calls at meaningful stage boundaries, plus a context
manager `stream_and_emit(client, task_id, ...)` in `worker/anthropic_stream.py` that
wraps `Anthropic().messages.stream()` to forward `message_start.usage` (cache hit/miss +
input tokens) → `thinking_delta` chunks → `text_delta` chunks → final usage, returning
`(final_text, usage_dict)`.

DNA build is the first wired call site; the same `emit()` calls drop into
`_ingest_async`, `_transcribe_async`, `_signals_async`, `_render_clip_async`, and
`_generate_improvement_brief_async` in follow-up PRs without touching the SSE / Redis
layer.

**Why**: Today's prod incident — the user clicked "Build Creator DNA," the worker
crash-looped on a `ModuleNotFoundError`, and the UI showed nothing for 3+ minutes
before timing out. The frozen-spinner experience is the same even on the happy path:
the LLM call takes ~30s with zero user-facing feedback. Live progress is the single
biggest "feels like a real tool, not a generic AI website" signal we can ship and
directly motivates Issue 85; the cache-hit observability the streaming wrapper
yields also feeds Issue 84.

**Approach** (per the 2026 industry-standard research summarized in DECISIONS):

| Sub-decision | Choice | Why won |
|---|---|---|
| Transport | SSE (text/event-stream) | One-way append-only; every LLM provider uses SSE; passes Cloudflare Tunnel + corp proxies without protocol upgrade. WebSocket overkill, long-poll laggy, HTTP/2 push deprecated. |
| Worker→web bridge | Redis Streams (XADD/XREAD) | Pub/Sub loses events on page refresh — Streams persist + replay from `0-0` or `Last-Event-ID`. Postgres LISTEN/NOTIFY has an 8 KB payload limit + no late-joiner replay. Already-existing Redis singleton, zero new infrastructure. |
| Wire format | Plain JSON-per-event + named `event:` types | EventSource `addEventListener` filters by event type natively. Vercel Data Stream Protocol locks frontend into Vercel React SDK. |
| Cache stat reporting | Read from `message_start.usage` | Anthropic puts `cache_read_input_tokens` / `cache_creation_input_tokens` here, available BEFORE the first token — confirmable in the stream. |
| Late-joiner support | XREAD from `Last-Event-ID` cursor (or `0-0`) | EventSource auto-reconnects with this header — the replay is free. |
| Per-creator SSE cap | Redis `INCR sse:count:{creator_id}` + EXPIRE | Per-creator hold-open exhaustion guard; idle timeout caps stale subscribers from a forgotten tab. Set to 3 (two tabs + slow reconnect). |
| Ownership | `task:{task_id}:owner = creator_id` set by API on enqueue | SSE endpoint refuses without ownership match — task ids leak nothing on their own. |

**Files (planned)**:
- `worker/progress.py` — NEW; `sync_emit`, `aemit`, ownership helpers, slot helpers, `aread_since`
- `worker/anthropic_stream.py` — NEW; `stream_and_emit(client, task_id, ...)` returning `(text, usage)`
- `dna/brief.py` — extract `_build_request` helper; add `generate_brief_streaming` alongside `generate_brief` (legacy callers untouched)
- `worker/tasks.py::_build_dna_async` — `aemit` step events at each stage; switch to `generate_brief_streaming`; terminal `done`/`error` emit
- `routers/tasks.py` — NEW; `GET /tasks/{task_id}/events` SSE endpoint with auth, ownership, keepalive, resume, concurrent cap, lifetime cap
- `routers/creators.py::build_dna` — set ownership in Redis post-`.delay()`, return `stream_url` in response
- `main.py` — mount `routers/tasks.py`
- `static/progressStream.js` — NEW; ~40-line EventSource reducer
- `static/onboarding.html` — replace `pollForBrief` with `subscribeToTaskStream`; add terminal-style `<pre>` block
- `tests/test_progress.py` — NEW; sync_emit/aemit/ownership/slot/replay unit tests against real Redis
- `tests/test_anthropic_stream.py` — NEW; stream wrapper with mocked Anthropic client
- `tests/test_tasks_sse.py` — NEW; SSE endpoint integration (auth, ownership, replay, cap, terminal close)
- `tests/test_worker_imports_integration.py` — NEW; subprocess celery worker that imports first-party packages — catches today's PYTHONPATH bug class forever
- `docs/DECISIONS.md` — new entry capturing all 7 sub-decisions
- `docs/SOT.md` — file structure additions

**Acceptance criteria**:
- [x] `worker/progress.py` writes XADD with MAXLEN ~ 200, EXPIRE 3600 on terminal events; both sync and async variants
- [x] `worker/anthropic_stream.py::stream_and_emit` forwards `message_start.usage`/`text_delta`/`thinking_delta` and returns `(final_text, usage_dict)`
- [x] `_build_dna_async` emits `step` events at: `acquire_lock`, `analyze_patterns`, `call_claude`, `embed`, and terminal `done`/`error`
- [x] `GET /tasks/{task_id}/events`: auth required (session cookie), ownership-checked, three Cloudflare-safe headers + `X-Accel-Buffering: no`, ~12s keepalive comment, `Last-Event-ID` resume, per-creator concurrent cap = 3, hard lifetime cap = 600s
- [x] Frontend renders progress live in a terminal-style block during the DNA build
- [x] No virality language emitted; no PII/token in any progress payload (compliance tests green)
- [x] `tests/test_worker_imports_integration.py` boots a real celery worker subprocess and confirms `from dna.brief import generate_brief` succeeds — guards the PYTHONPATH fix
- [x] All gates green: ruff format/check, mypy 0, pytest default (492 passed / 1 skipped / 85 deselected)
- [x] DECISIONS entry capturing the 7 sub-decisions
- [x] SOT updated; PROJECT_STATE updated; this issue closed

---

## Issue 87: Wire up channel catalog sync + fix kind classification (SEV-0 onboarding bug)
**Status**: ✅ Done (2026-05-30)

**The bug**: A freshly-connected creator (verified live on `reesepludwick@gmail.com`,
channel "backboard media": 20 Shorts + 3 long-form videos) saw the onboarding data-gate
report `0 long-form videos / 0 Shorts` and never moved past it. `sync_video_catalog` in
`youtube/analytics.py` was defined but had **zero callers** in the entire repo — neither
the OAuth callback nor the Beat refresh task ever populated the `videos` table from the
uploads playlist. Two related defects compounded it: `/videos/link` and `/videos/upload`
both hardcoded `kind=VideoKind.long` (so a manually-pasted Short couldn't be classified
correctly either), and `classify_video_kind` still used the pre-2024 `<=60s` Shorts
threshold (YouTube raised the official max to 180s in October 2024).

**Files**:
- `config.py` + `.env.example` — new `SHORTS_MAX_DURATION_S=180`
- `youtube/data_api.py::classify_video_kind` — reads from settings (180s default)
- `worker/tasks.py` — new `sync_channel_catalog` Celery task + `_sync_channel_catalog_async`
- `worker/tasks.py::_refresh_youtube_analytics_async` — prepends `sync_video_catalog` per creator
- `routers/auth.py::callback` — enqueues `sync_channel_catalog.delay(...)` for new creators
- `routers/creators.py` — new `POST /creators/me/catalog/sync` (5/min, 202+task_id)
- `routers/videos.py::link_video` — resolves `kind`/`duration_s` via `get_videos_metadata`
- `routers/videos.py::upload_video` — resolves `kind` from `probe_duration_s` before R2 PUT
- `static/onboarding.html::refreshDataGate` — POSTs to `/catalog/sync` then polls data-gate
- `tests/test_catalog_sync.py` — 8 unit tests
- `tests/test_analytics.py` — classify boundary tests updated to 180s
- `tests/test_retention_tasks.py` / `tests/test_oauth_lifecycle.py` — mock `sync_video_catalog`
- `docs/DECISIONS.md`, `docs/SOT.md`, `docs/OFF_COURSE_BUGS.md`, `docs/PROJECT_STATE.md`

**Acceptance criteria**:
- [x] `classify_video_kind(180)` → Short, `(181)` → long (load-bearing boundary tested)
- [x] `sync_channel_catalog` Celery task exists, idempotent (skips known video IDs), commits
- [x] `_refresh_youtube_analytics_async` calls `sync_video_catalog` before per-video analytics
- [x] OAuth callback enqueues catalog sync for new creators (async — never blocks redirect)
- [x] `POST /creators/me/catalog/sync` returns 202 + `task_id`; rate-limited 5/min
- [x] `/videos/link` resolves kind from YouTube metadata; falls back to long on API failure
- [x] `/videos/upload` resolves kind from local `probe_duration_s`
- [x] Onboarding "Refresh data status" button triggers sync + polls until count stabilises
- [x] Per-creator isolation preserved (sync_video_catalog filters by `Video.creator_id`)
- [x] Source / evidence captured in `docs/DECISIONS.md` (YouTube 180s spec; OAuth post-sync pattern)
- [x] All gates green: ruff 0, mypy 0, **501 passed / 1 skipped / 85 deselected** (+9 new)

---

## Issue 88: DNA filter parity + business-event observability (SEV-0 logical bug)
**Status**: ✅ Done (2026-05-30)

**The bug**: User reported live — `reesepludwick@gmail.com` connected, the
onboarding step-2 data-gate showed "3 long-form videos, 20 Shorts ready", but
clicking "Build Creator DNA" raised "Insufficient data: 0 long videos, 0
shorts." Two queries on the `videos` table filtered differently for "what
exists" vs "what's usable":
- `youtube/analytics.py:288 check_data_gate` counted every Video row by kind.
- `dna/builder.py:113 rank_videos` required `ingest_status==done` AND
  `engagement_rate IS NOT NULL`.

The catalog sync from Issue 87 creates rows with `ingest_status=pending` (the
local-clip-pipeline state, not a DNA prerequisite) and doesn't fetch metrics
until the hourly Beat refresh. So the gate cheerfully said "ready" while the
build couldn't see a single eligible video.

**Files**:
- `dna/builder.py::rank_videos` — drop `ingest_status==done` filter
- `dna/builder.py::build_patterns` — diagnostic `dna_build_insufficient_data` event on raise
- `youtube/analytics.py::check_data_gate` — JOIN VideoMetrics; OR semantics on `ready`
- `worker/tasks.py::_sync_channel_catalog_async` — phase 2: fetch metrics for unmeasured videos
- `observability.py` — new `log_event(event, **fields)` helper
- `routers/auth.py`, `routers/videos.py`, `routers/creators.py`, `routers/review.py` — emit events at 7 user surfaces
- `tests/test_issue_88_filter_parity.py` — 8 new tests
- `tests/test_catalog_sync.py` — updated for phase-2 commit
- `docs/assessment/REPORT.md` — new targeted-audit section
- `docs/DECISIONS.md`, `docs/PROJECT_STATE.md`, `docs/issues.md` (Issues 89-91 spinoffs)

**Acceptance criteria**:
- [x] `rank_videos` does NOT require `ingest_status==done` (test asserts WHERE excludes it)
- [x] `check_data_gate` joins VideoMetrics; same predicate as `rank_videos`
- [x] `check_data_gate.ready` uses OR (matches `build_patterns` AND-only-raise semantics)
- [x] `sync_channel_catalog` chains `sync_video_analytics` for unmeasured videos (no metrics wait)
- [x] `log_event(event, **fields)` emits structured JSON; promoted to top-level keys in production
- [x] Wired into 7 surfaces: auth callback, link, upload, sync_catalog, build_dna, confirm_dna, feedback
- [x] Diagnostic `dna_build_insufficient_data` event includes total/metered/per-kind counts
- [x] Targeted display-vs-filter audit complete; SEV-1+ findings filed as Issues 89-91
- [x] All gates green: ruff 0, mypy 0, **509 passed / 1 skipped / 85 deselected** (+8 new)
- [x] DECISIONS, SOT, PROJECT_STATE, assessment REPORT updated

---

## Issue 89: Balance pre-check vs deduction mismatch — silent failed uploads (SEV-1)
**Status**: 🔲 Not started (surfaced by Issue 88's targeted assessment)

**What**: `billing/ledger.py:173 check_positive_balance` raises 402 only when `balance <= 0`. The actual `deduct_for_video` (`billing/ledger.py:144`) requires `balance >= video_minutes(duration_s)` (e.g. 60 minutes for a 60-min video). Called from `routers/videos.py:163` (`upload_video`) and `routers/clips.py:139` (`render_clip`). A creator with 1-minute balance uploading a 60-minute video passes the pre-check, the upload completes, then `_ingest_async`'s deduction silently 402s inside the Celery task; `RefundOnFailureTask` runs but has nothing to refund. The user sees "failed" with no actionable message.

**Files**: `billing/ledger.py`, `routers/videos.py`, `routers/clips.py`, `tests/test_billing*.py`.

**Acceptance criteria**:
- [ ] New `check_balance_for_minutes(creator_id, minutes_needed, session)` helper that raises 402 with `"This video needs N minutes; you have M"`.
- [ ] `/videos/upload` calls it AFTER probe_duration_s (line 205) with `video_minutes(duration_s)`.
- [ ] `/clips/{id}/render` calls it with `video_minutes(clip duration)` before enqueuing.
- [ ] Integration test: 1-minute creator, 60-min video → 402 BEFORE upload completes; no ledger row written.
- [ ] User-facing copy on the 402 surfaces the gap (currently a generic "Insufficient balance").

---

## Issue 90: Catalog-synced videos pollute /videos library list (SEV-2)
**Status**: 🔲 Not started (surfaced by Issue 88's targeted assessment)

**What**: After Issue 87 catalog sync ships, a creator with 200+ uploads will see "200 videos, all pending" on the dashboard. `routers/videos.py:60 list_videos` returns every Video row regardless of `source_uri` / `ingest_status`. The dashboard's polling loop (`static/index.html:267-279`) keeps hitting `/status` for catalog-only rows that will NEVER transition (no `start_pipeline` was called — they're DNA-only references). Looks broken.

**Files**: `routers/videos.py`, `static/index.html`, `tests/test_videos*.py`.

**Acceptance criteria**:
- [ ] Either (a) exclude `source_uri IS NULL` rows from `/videos` by default (treat as DNA-only); OR (b) tag them with a distinct "catalog" badge + suppress the polling loop + hide the "Generate clips" button.
- [ ] Dashboard "Videos in library" count reflects the user's mental model (clippable videos), not the full catalog.
- [ ] Documented in `docs/SOT.md` data-model section so the meaning of `source_uri IS NULL` is canonical.

---

## Issue 91: "Clips ready" dashboard counter ignores render_status (SEV-2)
**Status**: 🔲 Not started (surfaced by Issue 88's targeted assessment)

**What**: Dashboard counter `clipsReadyCount += clips.length` (`static/index.html:196`) counts every clip regardless of render state. Reviewer (`static/review.html:154`) only plays clips with `render_uri`; un-rendered clips show "(not yet rendered)" with an empty player. Render must be triggered manually per-clip via `/clips/{id}/render` (`routers/clips.py:130`) — NOT auto-chained after `generate_clips` in `worker/tasks.py:136`. So most clips will be `RenderStatus.pending` immediately after generation.

**Files**: `static/index.html`, `routers/clips.py`, `tests/test_clips*.py`.

**Acceptance criteria**:
- [ ] Either (a) add a `?render_status=done` query param to `GET /videos/{id}/clips` and have the dashboard use it for the counter; OR (b) have the dashboard JS filter `clips.filter(c => c.render_status === 'done').length`.
- [ ] Counter label changed to "Clips rendered" (or similar) to match what it actually counts.

---

## Phase 3 Backlog (post-production)

Items deferred until the product is live and stable:
- Vision signals (MediaPipe / face-emotion) — Phase 2
- Auto-publish to YouTube Shorts (additional OAuth scope)
- Multi-platform export (TikTok / Reels)
- Hot-key clipping during live recording / OBS integration
- In-app subtitle, font, crop editor on the review surface
