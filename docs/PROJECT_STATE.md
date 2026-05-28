# CreatorClip — Project State

Updated after every issue closes.

---

## Current Status

**Active issue**: Issue 23 — VM provisioning + Cloudflare DNS + HTTPS (BETA_DEPLOYMENT start)
**Last completed**: Issue 44 — Auth boundary hardening (SEV-1, three sub-fixes) (2026-05-28)
**Blocked**: _(none)_

> **2026-05-28 session note**: Completed Issue 44 (auth boundary hardening). Three security
> fixes: (1) `auth.py` `get_current_creator` now catches `ValueError`/`KeyError` alongside
> `PyJWTError` so a malformed JWT `sub` returns 401 instead of 500; (2) `DELETE /auth/me` rate-
> limited to 5/hour via the existing slowapi limiter; (3) `crypto.py` rewritten to use
> `MultiFernet` for zero-downtime key rotation + typed `TokenDecryptError`. Added
> `TOKEN_ENCRYPTION_KEY_PREVIOUS` optional setting. Test count delta: +8 tests (2 in
> `test_auth.py`, 6 in `test_crypto.py` replacing 1 old test). All existing tests updated for
> the new rate-limit requirement on `DELETE /me`.

> **2026-05-27 session note**: Built the operability kit (Issue 31). Found and fixed a
> **blocking pre-existing bug** — `routers/clips.py` imported the deleted `billing.tiers`, so
> `import main` failed and the app could not start (likely a real cause of failed/timed-out
> deploys). Fixed to the minute-packs `check_positive_balance` guard. Full suite now `313 passed`.
> Note: CI lint (`ruff check .`) has ~11 pre-existing violations unrelated to this work — flagged,
> not swept in. The local unprovisioned `.env` is missing most required vars (dev only).

---

## Issue Progress

| # | Title | Phase | Status | Notes |
|---|-------|-------|--------|-------|
| 1 | Repo scaffold + Docker Compose + health endpoint | Core | ✅ Done | All acceptance criteria met; tests pass |
| 2 | Postgres schema + Alembic + pgvector | Core | ✅ Done | All tables, enums, pgvector; alembic upgrade head verified against live DB |
| 3 | Google/YouTube OAuth + creator session | Core | ✅ Done | OAuth flow, JWT session, token refresh, get_current_creator |
| 4 | YouTube data fetch — metrics, retention, activity | Core | ✅ Done | data_api.py, analytics.py, routers/creators.py; Deepgram default logged |
| 5 | Ingestion pipeline — source + transcript + signals | Core | ✅ Done | Celery chain; Deepgram/WhisperX/AssemblyAI; audio events; unified timeline |
| 6 | Creator DNA builder + brief (Research Mode) | Core | ✅ Done | dna/builder+brief+profile+embeddings; build_dna task; /creators/me/dna endpoints; 99 tests pass |
| 7 | Clip engine — candidates with backward setup-finding | Core | ✅ Done | window.py, candidates.py; 20 tests + 2 eval YAML fixtures pass |
| 8 | Clip scoring + DNA-weighted ranking | Core | ✅ Done | scoring.py, ranking.py, routers/clips.py; 18 tests pass |
| 9 | Render — 9:16 cut + active-speaker reframe | Core | ✅ Done | render.py (ffmpeg+OpenCV), render_clip task, /clips/{id}/render endpoint; 10 tests pass |
| 10 | Review UI + feedback capture | Core | ✅ Done | routers/review.py, static/review.html+onboarding.html+profile.html; HTMX; 7 tests pass |
| 11 | Preference model — recency-decayed reranker | Core | ✅ Done | decay.py, features.py, model.py, train.py; rerank_with_preference; 19 tests pass |
| 12 | Upload intelligence + improvement brief | Core | ✅ Done | timing.py, brief.py (Claude+web_search), routers; 13 tests pass |
| 13 | Clip outcomes loop (strongest signal) | Core | ✅ Done | poll_clip_outcomes Beat task (48h+7d), performed_well, get_video_stats; 13 tests pass |
| 14 | Dashboard + static pages scaffold | Core | ✅ Done | index.html, insights.html, tos.html, privacy.html; StaticFiles mount + GET /; 12 tests pass |
| 15 | Connected user flow + auth guard | Core | ✅ Done | auth.js guard + auth:ready event; nav on all pages; review/profile/onboarding wired; 18 tests pass |
| 16 | Auto-trigger clip generation + status polling | Core | ✅ Done | generate_clips task; build_signals chains it; setInterval polling; /videos/{id}/status; 7 tests pass |
| 17 | Source media purge + YouTube analytics refresh | Core | ✅ Done | purge_stale_source_media + refresh_youtube_analytics Beat tasks; datetime fix; 13 tests pass |
| 18 | Per-creator rate limiting | Core | ✅ Done | slowapi + Redis; creator_id key from JWT; 10/h LLM, 20/h render, 120/min rest; 11 tests pass |
| 19 | Account deletion (right-to-erasure) | Core | ✅ Done | DELETE /creators/me; OAuth revoke; storage purge; cascade delete; audit log; 6 tests pass |
| 20 | YouTube API quota hardening | Core | ✅ Done | youtube/quota.py; atomic Lua consume; backoff in data_api; Beat refresh stops gracefully; 8 tests pass |
| 21 | Stripe billing — minute packs | Core | ✅ Done | billing/packs.py + ledger.py; atomic deduct_minutes; 60-min free trial on signup; pricing.html; 12 tests pass |
| 22 | Production Kubernetes deployment | Core | ✅ Done | Helm charts in deploy/; KEDA ScaledObject; PgBouncer sidecar; GKE Autopilot decision; deploy/README.md |
| 23 | VM provisioning + Cloudflare DNS + HTTPS | BETA | 🔲 Not started | DigitalOcean Droplet + Cloudflare Tunnel + docker-compose.prod.yml |
| 24 | Production environment configuration | BETA | 🔲 Not started | .env secrets, ALLOWED_ORIGINS, GitHub Actions secrets |
| 25 | External API services provisioning | BETA | 🔲 Not started | Anthropic, Voyage, Deepgram, Cloudflare R2 |
| 26 | Google OAuth consent screen + beta test users | BETA | 🔲 Not started | External status, add friends as test users |
| 27 | YouTube API quota check + backoff verification | BETA | 🔲 Not started | Confirm quota limits; request increase if needed |
| 28 | Beta go-live smoke test + friend onboarding | BETA | 🔲 Not started | Full E2E on live deployment; invite 2-3 friends |
| 29 | Google OAuth app verification | PROD | 🔲 Not started | Submit for Google review; ~1–4 weeks external |
| 30 | Production hardening + public go-live | PROD | 🔲 Not started | Load test; all gates green; v1.0.0 tag |
| 31 | Operability kit — secrets registry, preflight doctor, deploy hardening, auto-heal | BETA | ✅ Done | docs/SECRETS.md + docs/ACCESS.md; scripts/doctor.py (14 tests); cloudflared+autoheal+healthchecks; amd64-only build; fixed blocking billing.tiers import; 313 tests pass |
| 44 | Auth boundary hardening — malformed sub 401, DELETE /me rate limit, MultiFernet rotation | SEC | ✅ Done | auth.py ValueError/KeyError catch; routers/auth.py 5/hour on DELETE /me; crypto.py MultiFernet + TokenDecryptError; +8 tests |

---

## Open Research Items

- [x] **Pricing model**: Minute packs + Stripe Checkout one-time payments. Issue 21.
- [x] **Production deployment**: GKE Autopilot + Helm + KEDA + PgBouncer. Issue 22.
- [x] **Transcription compute**: Deepgram (hosted) for MVP; WhisperX selectable via config. Resolved 2026-05-25.
- [ ] **YouTube API quota**: Confirm daily quota limits from Google Cloud Console for the project. Issue 27.
- [ ] **Retention curve availability window**: Verify how far back retention curves are available for the target channel.
- [ ] **TOKEN_ENCRYPTION_KEY rotation runbook**: Required before public launch.

---

## Pre-Public-Launch Gates (all must be green before opening to outside creators)

- [x] Lock `ALLOWED_ORIGINS` to production domain; disable `/docs` — env-driven: `docs_url` conditional on `ENV=="development"`; `ALLOWED_ORIGINS` from `.env`
- [x] Per-creator rate limiting + usage quotas before each LLM/render job — Issue 18 (slowapi, 10/h LLM, 20/h render, 120/min rest)
- [x] YouTube data-retention/refresh fully compliant (see `docs/COMPLIANCE.md`) — Issue 17 (Beat purge + analytics refresh)
- [x] `TOKEN_ENCRYPTION_KEY` rotation runbook written — see `docs/RUNBOOKS.md`
- [x] Terms of Service + Privacy Policy pages live — Issue 14 (`/static/tos.html`, `/static/privacy.html`)
- [ ] Google OAuth app verification completed for requested scopes — external Google process (Issue 29)
- [x] Account-deletion endpoint (right-to-erasure: token revocation + media purge) — Issue 19
- [x] Billing wired — Issue 21 (minute packs, atomic balance, 60-min free trial, Stripe Checkout)
- [x] Eval harness hardened with adversarial/edge cases — 3 new fixtures; fixed early-peak MIN_CLIP_S bug
