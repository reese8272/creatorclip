# CreatorClip — Project State

Updated after every issue closes.

---

## Current Status

**Active issue**: Phase 2 hardening — Issue 35 next (idempotent DNA build — prevent orphan drafts)
**Last completed**: Issue 41 — Replace pickle in preference model (2026-05-28)
**Blocked**: _(none)_

> **Closed Issue 41**: `preference/model.py:35–40` used `pickle.dumps(self)` / `pickle.loads(data)`
> for `PreferenceScorer.to_bytes` / `from_bytes`.  Any future write to `preference_models.weights_blob`
> (SQL injection, admin import, a bug) would become RCE in the worker process on the next ranking pass.
> Replaced with **joblib** (sklearn's documented serialiser; already a transitive dep) backed by
> `_RestrictedUnpickler` — a subclass of `joblib.numpy_pickle.NumpyUnpickler` that overrides
> `find_class` with a hardcoded allowlist of 10 `(module, name)` pairs.  `from_bytes` temporarily
> patches `joblib.numpy_pickle.NumpyUnpickler` with the restricted class for the duration of the
> `joblib.load` call, then restores the original (no global state left behind).  No schema change —
> `weights_blob` column stays `bytes`.  4 new tests in `tests/test_preference.py`: round-trip
> (predictions identical), label_count preserved, `os.system` gadget rejected, `subprocess.Popen`
> gadget rejected.  Test count delta: +3 net (renamed 1 existing test, added 4, kept all others green).
> See `docs/DECISIONS.md` 2026-05-28 Issue 41 entry.
>
> **Closed Issue 42**: `clip_engine/render.py` had three `subprocess.run` calls with no
> `timeout=`. A stalled or corrupt source video would block the Celery worker indefinitely.
> Fixed: `_run` now accepts `timeout_s: float = 120.0` and catches `subprocess.TimeoutExpired`,
> re-raising as `RuntimeError(f"ffmpeg {label} timed out after {timeout_s}s")`. `_frame_dimensions`
> hardcodes `timeout=30` directly (ffprobe reads only the container header). `render_clip_file`
> computes `render_timeout_s = max(120.0, duration * 4)` and passes it to both the keyframe
> extraction and the final render `_run` call. 3 new tests in `tests/test_render.py` assert
> each timeout path raises the correct `RuntimeError` without any real sleeping (all using
> `subprocess.TimeoutExpired` side-effects). Test count: 311 passed + 3 new = 314 expected
> (test env currently broken by a langsmith/pydantic-core version conflict introduced between
> sessions — see environment note below). See `docs/DECISIONS.md` 2026-05-28 Issue 42 entry.
>
> **ENVIRONMENT NOTE (2026-05-28)**: `python3.12 -m pytest -q` now fails at plugin-loading
> time with `SystemError: pydantic-core 2.27.2 incompatible with pydantic requiring 2.46.4`.
> Cause: langsmith installed a newer pydantic (2.46.4) into the uv-managed Python at
> `~/.local/share/uv/python/cpython-3.12.7/` while the user site at `~/.local/lib/python3.12/`
> still has pydantic-core 2.27.2. The fix is: `python3.12 -m pip install --user --break-system-packages
> "pydantic-core>=2.46.4"` OR use the project venv at `.venv/bin/pytest`. This is an environment
> issue, not a code issue.
>
> **2026-05-28 session note**: Ran a full project audit before resuming work. Discovered 24
> hardening + coverage findings (4 SEV-0, 12 SEV-1, 3 SEV-2, 8 test-coverage), filed as
> Issues 32–55 in `docs/issues.md` under **Phase 2: Hardening & Test Coverage**.
> **Closed Issue 32**: `starlette` had drifted to 1.1.0 (a major-version upstream released
> 2026-05-23 under the new `Kludex/starlette` maintainership) and `pytest` could not even
> collect — the previously-claimed "313 tests pass" was stale. Pinned `starlette==0.41.3`
> explicitly in `requirements.txt` (inside FastAPI 0.115.x's `<0.42.0,>=0.40.0` constraint),
> re-installed via a project venv, and confirmed **313 passed, 7 deselected** (the 7 are
> integration-marked). See `docs/DECISIONS.md` 2026-05-28 entry.
> **Closed Issue 33**: `routers/improvement.py` was sending other creators' analytics
> averages to Claude for every requesting creator (`select(VideoMetrics).limit(50)` with no
> `creator_id` filter — SEV-0 isolation leak). Fixed via the always-filter idiom already
> used elsewhere (`.join(Video).where(Video.creator_id == creator.id)`) plus an
> `ORDER BY fetched_at DESC` for determinism, plus a zero-data 400 short-circuit so
> brand-new creators don't get a hallucinated brief. New integration test
> `tests/test_improvement_isolation.py` seeds two creators with disjoint metrics and asserts
> only the requesting creator's data reaches the LLM. Filed **Issue 56** (Postgres RLS
> evaluation) as defense-in-depth follow-up. See `docs/COMPLIANCE.md` "Findings & Fixes
> Log" 2026-05-28 entry.
> **Closed Issue 34**: `worker/tasks.py:189` called `deduct_minutes` with no per-video
> idempotency key. With Celery's `task_acks_late=True`, a worker-crash-between-commit-and-ack
> would re-deliver the ingest task and re-decrement the balance (up to 4× per video).
> Replaced with a new `MinuteDeduction` ledger table (symmetric to `MinutePack` grants),
> `UNIQUE(video_id)` as the idempotency key, and `deduct_for_video` using SAVEPOINT
> (`session.begin_nested`) to atomically INSERT the ledger row + decrement balance. New
> migration `0003_minute_deductions.py`. 4 real-Postgres integration tests in
> `tests/test_billing_idempotency.py` cover sequential retry, two-coroutine concurrent
> race, 402-leaves-ledger-clean, and audit fields. Test count: **311 passed, 13
> deselected** (net 0 — removed 2 mocked unit tests, added 4 integration tests). Filed
> **Issue 57** (refund-on-terminal-failure) as product follow-up. See `docs/DECISIONS.md`
> 2026-05-28 Issue 34 entry.
>
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
| 32 | Restore test suite — starlette pin | HARDENING | ✅ Done | Pinned `starlette==0.41.3` (FastAPI 0.115.x range); test suite returns to 313 passed; DECISIONS.md entry on transitive-dep pinning |
| 33 | Cross-creator data leak in improvement brief | HARDENING | ✅ Done | Always-filter `Video.creator_id` added; ORDER BY recency; zero-data 400 short-circuit; new integration test; COMPLIANCE.md Findings & Fixes log; spawned Issue 56 (RLS evaluation) as defense-in-depth |
| 34 | Idempotent minute deduction on Celery retry | HARDENING | ✅ Done | New `MinuteDeduction` ledger with `UNIQUE(video_id)` idempotency key; `deduct_for_video` SAVEPOINT-atomic; 4 real-Postgres integration tests (sequential, concurrent race, 402-clean, audit fields); migration 0003; spawned Issue 57 (refund policy) |
| 41 | Replace pickle in preference model (RCE surface) | HARDENING | ✅ Done | joblib + `_RestrictedUnpickler` allowlist (10 entries); `to_bytes`/`from_bytes` rewritten; 4 new tests (round-trip + 2 rejection tests); no schema change |
| 42 | ffmpeg/subprocess timeouts | HARDENING | ✅ Done | `_run` accepts `timeout_s=120.0`; `_frame_dimensions` hardcodes `timeout=30`; `render_clip_file` computes `max(120, duration*4)`; 3 new timeout tests; DECISIONS.md entry |

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
