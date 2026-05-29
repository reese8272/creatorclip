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
`TypeError: Router.__init__() got an unexpected keyword argument 'on_startup'`.
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
**Status**: 🔲 Not started

**What**: Sync calls (sync Anthropic, sync Voyage, sync Deepgram, boto3, subprocess) run
inside `async def` while an AsyncSession is open. The connection is pinned for the entire
LLM round-trip (often 10–40 s). Under any concurrent load this exhausts the pool.

**Files**: `routers/improvement.py:53`, `worker/tasks.py:264–302`, `dna/brief.py`, `dna/embeddings.py`, `ingestion/transcribe.py`.

**Acceptance criteria**:
- [ ] Sync calls wrapped in `await asyncio.to_thread(...)`, **OR** the DB session is released before the LLM call (read what you need, close, then call)
- [ ] Where Anthropic supports it, switch to `AsyncAnthropic`
- [ ] Load test: 10 concurrent improvement-brief calls do not exhaust the connection pool

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
**Status**: 🔲 Not started

**What**: `_ingest_async`, `_transcribe_async`, `_signals_async`, `_render_clip_async`,
`_generate_clips_async`, `_build_dna_async`, `_poll_clip_outcomes_async` have no direct
tests; `test_pipeline_trigger.py` calls the mock itself rather than the real task.

**Files**: `tests/test_worker_pipeline.py` (new), Celery eager mode + real Postgres.

**Acceptance criteria**:
- [ ] Ingest task on a 5 s test video → storage + DB state correct, minutes deducted exactly once
- [ ] Render task retried twice → `render_uri` set, `render_status=done`, no duplicate clip rows
- [ ] generate_clips retried after partial success → no rendered clips lost
- [ ] poll_clip_outcomes computes `performed_well` against per-creator median, NOT global
- [ ] build_dna below `MIN_VIDEOS_FOR_DNA` → `ValueError` surfaces without incrementing retry counter

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
**Status**: ✅ Done (2026-05-28) — decision: **adopt now**; implementation tracked as new Issue 60

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
- [→] If adopted: RLS policies cover every table with a `creator_id` column; migration role retains BYPASSRLS for alembic upgrades → **tracked as Issue 60**
- [→] If adopted: Issue 48 isolation tests extended to assert "unfiltered query returns zero rows for non-current creator" → **tracked as Issue 60**

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

### Issue 58: Transactional email infrastructure
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

### Issue 59: In-app notifications surface
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

### Issue 60: Implement Postgres Row-Level Security per Issue 56 decision
**Severity**: SEV-2 — structural defense-in-depth against cross-tenant leaks
**Depends on**: 56 ✅
**Status**: 🔲 Not started

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
- [ ] Alembic migration creates SELECT policies on all 12 tables listed in
      Issue 56's DECISIONS entry; `FORCE ROW LEVEL SECURITY` on each
- [ ] Role split: `creatorclip_app` (no BYPASSRLS, not table owner);
      `creatorclip_migrate` with BYPASSRLS; new env var
      `DATABASE_MIGRATION_URL` documented in `docs/SECRETS.md`
- [ ] `after_begin` event listener on `Session` sources `current_creator`
      from FastAPI request context and emits `SET LOCAL app.creator_id`
- [ ] Every UPDATE / DELETE that targets a tenant-owned table checks
      `result.rowcount` and raises 404 on 0 (silent-failure guard)
- [ ] Issue 48 isolation tests extended: with RLS active and Creator A in
      context, an unfiltered `SELECT * FROM <each table>` returns zero
      Creator B rows
- [ ] Production runbook in `docs/DEPLOYMENT.md` covers the one-time
      `BYPASSRLS` grant on the migration role
- [ ] No regression in existing test suite under RLS-enabled CI

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

## Phase 3 Backlog (post-production)

Items deferred until the product is live and stable:
- Vision signals (MediaPipe / face-emotion) — Phase 2
- Auto-publish to YouTube Shorts (additional OAuth scope)
- Multi-platform export (TikTok / Reels)
- Hot-key clipping during live recording / OBS integration
- In-app subtitle, font, crop editor on the review surface
