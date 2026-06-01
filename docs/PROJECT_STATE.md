# CreatorClip — Project State

Updated after every issue closes.

---

## Current Status

**Active issue**: _(none in flight)_ — Issues 113–119 UX wave complete (2026-06-01).

**Last completed**: Issues 113–119 — UX wave. 7 issues built in one session. (A) **Issue 113** nav quick wins: `nav-balance` minutes chip + `?` tutorial button wired into all 4 main pages via `auth.js`. (B) **Issue 114** profile DNA collapsible `<details>` + "Synced / Not synced with DNA" chip comparing identity vs DNA timestamps. (C) **Issue 115** dashboard YouTube Analytics panel: new `GET /creators/me/insights/analytics?period=` endpoint aggregating video_metrics rows + time-period `<select>` on the dashboard. (D) **Issue 116** DNA rebuild streaming: `progressStream.js` wired into `rebuildDna()` on profile.html replacing "come back in ~30s". (E) **Issue 117** AI per-performer insights: Haiku 4.5 lazy + cached analysis per (video, dna_version), save/bookmark system (`creator_insights` table, migration 0017). (F) **Issue 118** structured feedback: `feedback_tags` + `feedback_note` on `clip_feedback` (migration 0018); multi-select approve/deny tag panels in review.html. (G) **Issue 119** review editing surface: `style_preset` on clips (migration 0019); `_SUBTITLE_FILTERS` in render.py; `RenderStyleIn` body on render endpoint; style picker UI. **Tests**: 652 passed (+23 from Issue 112's 629) / 2 skipped / 125 deselected. Layer 0: ruff 0 / mypy 0 / coverage 75.83% / bandit 0/0 / pip-audit 0 / freshness ok.

**Prior**: Issue 112 code-complete; Locust run is user-side. Pending: user runs Locust on staging VM to close axes A + E, Google OAuth verification, Issue 109 cleanups.

**Last completed**: Issue 112 — Locust load-test gate (axes A + E). Two deliverables: (A) **`/health` connection-churn fix** — `_check_postgres` now routes through `engine.connect()` (SQLAlchemy pool) + `asyncio.timeout(2.0)` instead of opening a fresh `psycopg.AsyncConnection` per probe; `_check_redis` uses a module-level `_health_redis` singleton initialized in lifespan instead of `aioredis.from_url()` per call. `psycopg` import + `_pg_dsn()` removed from `main.py`. 2 regression tests in `tests/test_health.py`. (B) **Staging infrastructure** — `docker-compose.staging.yml` with `edoburu/pgbouncer:1.23.1-p3` in transaction-pooling mode (`DEFAULT_POOL_SIZE=25`), isolated Postgres DB (`creatorclip_staging`) + Redis (DB index 1), app on port 8001. `tests/perf/seed_staging.py` upserts creator + 12 videos + confirmed DNA + identity row. `tests/perf/README.md` updated with 7-step runbook including pass criteria and result-recording instructions. **Pending user-side action**: SSH to prod VM → `docker compose -f docker-compose.staging.yml up -d` → `alembic upgrade head` → `seed_staging.py` → Locust 300 users 5 min → record axis A+E numbers in REPORT.md. **Tests**: 629 passed (+2 from Issue 110's 627) / 2 skipped / 125 deselected. Layer 0: pending run.

**Prior**: Issue 110 — post-Wave-9 /assess top-register cluster (5 fixes + production hotfix). All 3 net-new SEV2s from the post-Wave-9 walk closed: `/auth/logout` + `/billing/webhook` gained rate-limit decorators (CSRF + bot-net exhaustion vectors), and `start_improvement_brief` got `SELECT FOR UPDATE SKIP LOCKED` + fallback re-query to close the debounce race that would double-fire the billed Anthropic call. Also closes the Issue-105 misread: `_ingest_async` now captures `prior_source_uri` at function entry and calls `adelete_file` after the final commit with `source/...mp4` prefix+suffix guard — closes ToS-relevant orphan-mp4 retention leak that survived Issue 105's `.wav` short-circuit (which only fixed the retry case). Plus cleanup: `routers/auth.py:131` `_logging` workaround removed (the one site Issue 108 missed). DECISIONS entry documents two choices: SKIP LOCKED over advisory lock for the existing-row debounce race (canonical for SQLAlchemy 2.x async), and capture-then-delete-after-commit + R2 lifecycle as belt-and-suspenders (AWS Well-Architected primary/backstop split). **Production hotfix this turn**: `config.py` `LOCAL_MEDIA_DIR` validator relaxed to `STORAGE_BACKEND=="local"` only — Issue 105's validator was overreaching, rejected the `./media` default at `ENV=production` even when `STORAGE_BACKEND=r2` made the path dead config; crash-looped the first post-Wave-9 deploy. Hotfix `1acee71` shipped before the rest of Issue 110. **User-side action pending**: set 7-day TTL on R2 bucket's `source/` prefix via R2 dashboard (belt-and-suspenders for the orphan-mp4 cleanup). **Tests:** 627 passed (+7 from Wave 9's 620) / 2 skipped / 125 deselected. Layer 0: ruff 0 / mypy 0 / coverage **75.97%** / bandit 0/0 / pip-audit 0 / freshness ok. **15 unpushed commits** = Wave-9 batch (102/103/104/105/106/107/108) + Issue 95 frontend + Issue 110 + integration hotfixes; will push to redeploy alongside this docs commit.

**Prior**: Issue 108 — mechanical cleanup sweep over 38 of the 48 cleanup-severity items from the post-Wave-8 /assess. Module docstrings on empty `__init__.py` (clip_engine, worker); `.env.example` gains the missing `DATABASE_MIGRATION_URL` stanza; `worker/schedule.py` imports `timedelta` from `datetime` (was from `celery.schedules`); `routers/upload_intel.py` gets the missing module-level `logger`; `dna/identity.py` loses the dead `_ = sa` alias + unused `import sqlalchemy as sa`; `_logging` workarounds (the `import logging as _logging; _logging.getLogger(__name__).warning(...)` pattern) removed from clips/videos/creators routers, replaced with proper module-level `logger`; magic-number naming (`_DNA_BRIEF_MAX_CHARS`, `_HOUR_UNAVAILABLE_SENTINEL`, OBS-id collision math comment); `Optional["X"]` → `"X | None"` sweep in `models.py` (5 forward-ref relationship sites; PEP 604 forward refs use whole-expression-as-string form); 11 typing gaps closed across `auth.py`, `limiter.py`, `worker/tasks.py`, `worker/anthropic_stream.py`, `ingestion/transcribe.py`, `dna/embeddings.py`, `dna/brief.py`, `improvement/brief.py`, `billing/stripe_client.py`; duplicated `*QueuedOut` schemas extracted to `TaskQueuedOut` base in new `routers/_schemas.py` (`BriefQueuedOut` intentionally stays standalone — `task_id: str | None` is LSP-incompatible with base's `str`). Mid-sweep mypy hit 5 invariance-related issues (Protocol-typed lists vs SQLAlchemy `Mapped[T]`; `dict[str, object]` vs caller's narrower `dict[str, int|str|float|None]`); resolved by using `Sequence[Any]` + keeping the Protocol intent as inline comment, and `Mapping[str, object]` (covariant) in improvement/brief. Issue 109 filed as follow-up for the 10 deferred design-work items (`_enrich_videos` split, lifespan registry, fetch-then-validate query rewrite, `_fernet` lru_cache, cold-start principle misattribution, etc.). **Tests:** 620 passed (zero new tests — cleanups don't change behavior) / 1 skipped / 125 deselected. **Layer 0:** ruff 0 / mypy 0 / coverage **76.06%** / bandit 0/0 / pip-audit 0 / freshness ok.

**Prior**: Issue 106 — security tightening (5 fixes). `limiter.py::_creator_key` now verifies `exp` with 60s leeway (overrides /assess recommendation of 300s — DECISIONS entry; security-relevant decoder, RFC 7519 "few minutes"); `except Exception: pass` narrowed to `jwt.InvalidTokenError` with WARNING-level class-only logging (PyJWT error messages can include claim values). Closes per-creator quota-leak vector. `billing/stripe_client.py::create_checkout_session` accepts client-supplied `intent_id` (v4 UUID from sessionStorage), validates UUID shape, passes `options={"idempotency_key": intent_id}` to Stripe — double-click / router retry dedupes within Stripe's 24h window. `_STRIPE` client carries explicit `STRIPE_TIMEOUT_S=10` HTTPXClient timeout (default ~80s would pin an executor slot). `session.url` None-check raises `RuntimeError` instead of redirecting to the string `"None"`. `CheckoutRequest` gains `intent_id: UUID4` (Pydantic shape validation); `static/pricing.html::_getCheckoutIntentId()` generates `crypto.randomUUID()` once per page load. 5 new tests + 4 existing /billing/checkout tests updated for the new required field. **Tests:** 620 passed (+5) / 1 skipped / 125 deselected. Layer 0: ruff 0 / mypy 0 / coverage **76.02%** (+0.28pp) / bandit 0/0 / pip-audit 0 / freshness ok.

**Prior**: **Wave 9 — parallel-build batch (103/104/105/107) cherry-picked on top of Issue 102.** All four built via worktree-isolated subagents from one bulk-approved Phase-1 brief (4 issues, fully disjoint file trees). Cherry-picked into main one by one with full test runs between merges. Mid-merge hotfix: Issue 104's new per-creator `creator_key` quietly broke 8 tests using the `dependency_overrides[get_current_creator] = lambda: creator` pattern (lambda bypasses real auth dep → no `request.state.creator_id` → fallback to `get_remote_address` → all tests share the "testclient" rate-limit bucket → /auth/me 429 after 5 calls). Fix: new `tests/_helpers.py::override_current_creator(creator)` stashes id on `request.state`; sweep-replaced 26 call sites across 11 test files. Also a small ruff sweep (zip strict=, raise from exc, unused locals) on the parallel-built code. **Layer 0:** ruff 0 / mypy 0 / coverage **75.74%** (vs 69.54% baseline — +6.20pp) / bandit 0/0 / pip-audit **0** (was 16; Issue 107 closed via venv sync + 6 documented residuals in `[tool.pip-audit]`) / freshness ok. **Tests:** 615 passed (+29 from Issue 102's 586) / 1 skipped / 125 deselected. SEV1 trajectory: **4 → 2 → 1 → 3 → 0 → 1 → 2 → 0**. Scale-checklist axes B (Async loop hygiene), C (Celery idempotency), F (Rate limit / quota) all returned to ✅. 6 new commits on main since session start (Issue 95 frontend through Wave 9 integration), all unpushed pending user authorization (pushing to main auto-deploys).

**Prior**: Issue 105 — Worker idempotency + advisory locks. Seven load-bearing SEV2s from the post-Wave-8 /assess: (1) `_transcribe_async` + `_signals_async` idempotency probes skip when Transcript/Signals row already exists and status is past the stage; (2) `_ingest_async` orphan-mp4 short-circuit returns immediately when `source_uri` already ends in `.wav`; (3) `generate_clips` now carries `base=RefundOnFailureTask` so terminal failure auto-refunds minutes; (4) `pg_try_advisory_lock` (non-blocking) on 6 sites — `_retrain_preference_async`, `_poll_clip_outcomes_async`, `_purge_stale_source_media_async`, `_purge_stale_youtube_analytics_async`, `_refresh_youtube_analytics_async`, `_sync_channel_catalog_async` — with explicit `pg_advisory_unlock` in `finally`; (5) `SoftTimeLimitExceeded` caught before the broad `except` in ingest/transcribe/build_signals sync wrappers to fire `on_failure` immediately; config validator asserts `TRANSCRIPTION_TIMEOUT_S < CELERY_SOFT_TIME_LIMIT_S - 30`; (6) Redis singletons (sync + async) in `worker/progress.py` now carry `socket_timeout=2.0` + `socket_connect_timeout=2.0`; (7) `worker/storage._local_root()` uses `expanduser().resolve()`; `config.py` model_validator rejects relative `LOCAL_MEDIA_DIR` in production. 9 new unit tests; 4 pre-existing tests updated for advisory-lock mock compatibility. **Tests:** 606 passed (+9) / 1 skipped / 122 deselected. Layer 0: freshness ok.
**Prior completed**: Issue 102 — preference model event-loop offload. Both Wave-8 /assess SEV1s fixed: `preference/train.py::load_latest` now wraps `PreferenceScorer.from_bytes` in `await asyncio.to_thread(...)` so the process-wide `_UNPICKLER_LOCK` (Issue 71 RCE allowlist) serializes threads instead of coroutines, and `preference/train.py::build_and_save` wraps the LightGBM/LogisticRegression `fit` call in `await asyncio.to_thread(fit, X, y, w)` so a power creator's training pass doesn't block the surrounding async loop for seconds. Bundled the two paired SEV2s: training-feedback query now `ORDER BY ClipFeedback.created_at DESC LIMIT settings.PREFERENCE_MAX_TRAINING_LABELS` (default 5000 — industry standard for recency-decayed sklearn pipelines at 30d half-life), and `list(_POSITIVE_ACTIONS) + list(_NEGATIVE_ACTIONS)` collapsed to the already-defined `TRAINABLE_ACTIONS` frozenset. DECISIONS entry logged for the deviation from the /assess recommendation — joblib 1.x has no public per-load NumpyUnpickler injection slot (verified via industry-standards-researcher), so the module-global swap stays as the documented extension point; the `asyncio.to_thread` wrap alone solves the scale defect. 3 new regression tests in `tests/test_preference.py` pin (a) `fit` offload via to_thread, (b) `from_bytes` offload via load_latest, (c) query has `ORDER BY created_at DESC` + `LIMIT PREFERENCE_MAX_TRAINING_LABELS`. **Tests:** 586 passed (+3) / 1 skipped / 122 deselected. Layer 0: ruff 0 / mypy 0 / coverage **75.25%** (+0.06pp) / bandit 0/0 / freshness ok / pip_audit 16 (carries forward — Issue 107). Returns SEV1 count to 0; restores scale-checklist axis B (Async loop hygiene) to ✅.
**Prior**: Issue 95 frontend — `static/profile.html` API-key management card. List / create / revoke wired against the Wave-8 backend (`/creators/me/api-keys`). One-time-reveal modal with the canonical "won't be able to see it again" security copy (GitHub/OpenAI/Anthropic phrasing). Revoke confirm modal with the canonical "stop working immediately / cannot be undone" destructive-action wording (GitHub/Stripe phrasing). Listed keys render as masked prefix `ack_xxxxxxxx••…` in the JetBrains-Mono data register (Issue-99 Phase C convention). Empty state shows a one-sentence orient + the Generate CTA (GitHub empty-state pattern). Native `<dialog>` element used for both modals — zero JS deps, free focus trap + Escape handling, supported in every shipping browser. New `tests/test_static.py::test_profile_page_exposes_api_keys_section` pins the section + endpoints wiring + the both modals' load-bearing copy + the mono register on the prefix so a future "let me simplify this" PR can't silently regress the one-time-reveal or the revoke confirmation. **Tests:** 583 passed (+1) / 1 skipped / 122 deselected. Layer 0: freshness ok (ruff/mypy/coverage/bandit/pip-audit skipped — not installed locally; CI Quality gates remain authoritative). Self-audit: zero raw-key/token log lines, the raw key is held only in a single `<input>` value and wiped on modal close, no PII or token logging anywhere, per-creator isolation is the existing backend's responsibility (verified by Wave-8 integration tests).
**Prior**: Wave 8 — 4-issue batch on the new Issue-99 design system. **Issue 95 backend** (`alembic 0015`, `models.CreatorApiKey`, `api_key.py` module, `routers/api_keys.py` for management, `POST /clips/ingest` on `clips_router` with bearer-auth via the new dependency — companion-app ready; backend isolated and complete). **Issue 100** (`static/walkthrough.html` 5-panel first-run explainer, `auth.js` gate routing new creators with `onboarding_state='connected'` to it once, intake on onboarding.html now mandatory — Skip button + skipIdentity() removed, Build-DNA button gates on identity-exists). **Issue 93** (`routers/insights.py` new `GET /creators/me/insights` single-fetch aggregation of channel totals + DNA snapshot + top/bottom performers; insights.html rebuilt with 6 panels and mono data register throughout). **Issue 94** (`Why this clip?` `<details>` expander on review.html surfacing the Claude-authored `reasoning` field, the cited `principle`, the score, and the setup→peak→end timing — auto-opens on first clip to teach the affordance). **Tests:** 582 passed (+19 from Issue 99 Phase B's 563) / 1 skipped / 122 deselected (+22 integration). Layer 0: ruff 0 / mypy 0 / freshness ok. **Self-audit:** 16 explicit `creator.id` / `creator_id` filter sites in new endpoints, zero raw-key/token log lines, zero TODOs introduced, all new functions typed.

**Phase B entry**: Issue 99 Phase B — retrofitted the 8 remaining static templates (index, onboarding, insights, profile, review, tos, privacy, early-access) onto `_design-tokens.css`. Every template now links the shared design system, consumes `--color-*` semantic tokens, replaces inline reset/nav/btn/footer styles with the shared component layer (`.nav`, `.btn`, `.footer`, `.disclaimer`, `.chip`). Mono data register applied to high-data surfaces: dashboard summary card values, video-table YouTube-ID column, DNA stat cards (profile.html), insights upload-window activity %, optimal-gap value, version badges. Niche-chip and trim-handle controls refactored from inline-style cssText to `.chip` / standard input styling. Early-access.html keeps its conversion-page CTA semantics but on the same indigo accent (no more red splat — consistent palette). `tests/test_static.py` (+1 parametric test pinning all 9 templates link the tokens file + consume `--color-*`). **Tests:** 563 passed (+1) / 1 skipped / 100 deselected. Layer 0: ruff 0 / mypy 0 / freshness ok.

**Phase A entry**: Issue 99 Phase A — `static/_design-tokens.css` built with the Linear-locked palette (#0a0a0a / #5e6ad2 indigo / Inter Variable + JetBrains Mono / 4px grid / hairline borders) + minimal component layer (nav, card, .btn, .kbd, .mono, .badge, .footer). pricing.html retrofit as proof: dropped the Wave-7 inline `:root` stopgap; links to the shared tokens file; mono data register applied to minutes / price / $/min figures (first real use of the sans/mono split). `tests/test_static.py` (+2 tests) pins (a) the tokens file exists with the canonical Linear palette + .mono utility + Google-Fonts swap-display imports, (b) pricing.html consumes `--color-*` tokens (not the Wave-7 stopgap names). Phase B (retrofit 8 remaining templates) and Phase C (`.mono` applied to clip metadata / transcripts / video table durations / DNA cards) queued.

**Last wave**: Wave 7 — pricing.html CSS hotfix. Live-observed by the user on the freshly-deployed autoclip.studio: pricing page rendering in browser defaults (Times New Roman, blue underlined links) because `pricing.html` linked `/static/style.css` which never existed in the repo. Every `var(--surface)` / `var(--accent)` / etc. resolved to empty string. Fix: dropped the broken `<link rel="stylesheet">`; added a `:root` block defining `--bg / --surface / --border / --text / --muted / --accent` matching the inline-style palette the other authenticated templates already use; added minimal `.nav`/`.nav-brand`/`.nav-links` rules so the nav stops rendering as default browser links. Static-page test pins both halves of the fix. This is a deliberate STOPGAP until Issue 99's `_design-tokens.css` lands and supersedes every inline palette.

**Phase 1 also closed for Issues 95 + 99** (this session). User picked the design direction + OBS architecture from researched menus. Backlog entries rewritten to lock in the picks; Phase 3 builds those issues out in their own workflows.

**Last issue**: Issue 101 — moved `.github/workflows/docker-publish.yml` from `runs-on: ubuntu-latest` to `runs-on: self-hosted`. The deploy pipeline is now end-to-end zero-GitHub-hosted-minutes (both docker-publish and deploy run on the prod VM's self-hosted runner). Triggered by live billing-block failure: Wave 6's push fast-failed in 4s with "recent account payments have failed or your spending limit needs to be increased" — same shape as the prior Wave-5 fix(ux) push. CI / Quality / Integration workflows intentionally remain on `ubuntu-latest` (informational only; don't gate deploys per `workflow_run` dependency model). `tests/test_ci_config.py` pins both workflows' `runs-on: self-hosted` directives + the `Docker publish` ↔ `workflow_run: [Docker publish]` workflow-name linkage so a future "let me fix CI" PR can't silently re-introduce the billing dependency or break the deploy trigger. Operational requirement (user must do): `scripts/setup-runner.sh` on the VM once — until then both workflows queue indefinitely; `scripts/deploy.sh` remains the manual fallback.

**Last wave**: Wave 6 — "done-vs-visible" audit fixes. User-reported gap: "things marked done but not on the website." Audit found four real causes — (A) Issue-98 state-machine fix was forward-only and existing creators with confirmed DNA stayed `connected` permanently → banner stuck; (B) Pricing / TOS / Privacy / Early-Access pages had no inbound links from anywhere → unreachable from the app; (C) `PROJECT_STATE.md` "Queued" list still showed Issues 84 and 92 despite both being closed (bookkeeping rot driving the user's perception); (D) Issue-92 returned `stream_url` on the upload + clip-generate endpoints but `index.html` never subscribed, so the Wave-5 activity panel was hidden 100% of the time on the dashboard.

> **Closed Wave 8 — Issues 95 backend + 100 + 93 + 94 in one batch** (2026-05-31): Four user-requested issues shipped on the new Issue-99 design system. **Issue 95 backend (OBS companion app surface):** `alembic 0015_creator_api_keys` + `models.CreatorApiKey` (SHA-256-hashed keys, soft revoke via `revoked_at`, 8-char display prefix) + `api_key.py` (generate / hash / display_prefix / `get_current_creator_via_api_key` FastAPI dependency that stamps `session.info["creator_id"]` for RLS) + `routers/api_keys.py` (GET/POST/DELETE management) + `POST /clips/ingest` on clips_router (multipart upload + ffprobe + balance check + R2 PUT + start_pipeline; same fail-open `aset_owner` posture as `/videos/upload`). 14 unit + 12 integration tests covering generation entropy, hash determinism, bearer-header parsing edge cases, raw-key-shown-once invariant, list-excludes-raw, soft-revoke semantics, per-creator isolation on list+revoke, bearer-dependency rejects unknown/revoked/non-canonical keys + stamps last_used_at. **Issue 100 (first-run onboarding):** `static/walkthrough.html` 5-panel explainer (what-this-is / DNA / what-a-clip-is / badges / tell-us-about-you) with arrow-key nav + progress dots + `creatorclip:walkthrough_seen` localStorage flag. `auth.js` redirect gate fires only when `onboarding_state='connected'` AND flag-unset AND not on walkthrough/onboarding (loop guard). `onboarding.html` intake is now MANDATORY (skipIdentity + Skip button removed); Build-DNA button starts disabled, unlocks after `_checkIdentityExists()` returns true. **Issue 93 (insights rebuild):** new `GET /creators/me/insights` returning ChannelTotals (videos_analyzed, longs, shorts, ingested_done, total_minutes_processed) + DnaStats (latest version, status, optimal_clip_len_s, best_source_region, optimal_upload_gap_h) + Performer lists (top + bottom) resolved from DNA's top_video_ids_jsonb / bottom_video_ids_jsonb with order-preservation. `_fetch_performers` filters on `Video.creator_id == creator.id` — defends Issue-33-shape cross-creator leak even if DNA references a foreign Video ID. 8 integration tests including empty-state, totals math, latest-DNA-pick, performer resolution, stale-ID drop, per-creator isolation, cross-creator video ID drop, auth-required. Rebuilt insights.html with 6 panels using mono register throughout. **Issue 94 (clip transparency):** review.html now surfaces `clip.reasoning` (Claude's natural-language explanation) and `clip.principle` (named principle) via a `<details>` "Why this clip?" expander showing principle / reasoning / score / setup→peak→end timing. Auto-opens on first clip; respects user toggle thereafter. **Wave 8 totals:** +19 tests in default lane, +22 in integration lane. Layer 0 green across the batch. **Deferred to focused future sessions:** Issue 96 (chat-driven intake — needs multi-turn LLM design work) and Issue 97 (livestream recap — needs clip_engine recap-mode extension + subscription tier work).

> **Closed Issue 99 Phase B — retrofit 8 templates onto design system** (2026-05-31): One commit, eight templates: `static/index.html` (dashboard), `onboarding.html` (5-step setup), `insights.html` (timing + brief), `profile.html` (DNA view + identity edit), `review.html` (clip player + trim), `tos.html`, `privacy.html`, `early-access.html`. Every template now links `/static/_design-tokens.css`, drops the inline reset / nav / btn / footer styles, consumes `--color-*` semantic tokens for everything. Shared component layer (`.nav`, `.btn`, `.btn-primary`, `.btn-secondary`, `.chip`, `.disclaimer`, `.footer`) reused across the surface. Mono data register applied to: dashboard summary card values, video-table YouTube ID column, DNA stat cards on profile, insights window activity %, gap value, version badges. Inline cssText for the niche-chip UX refactored into `.chip` / `.chip.selected`. early-access marketing-page CTA brought onto the same indigo accent as the rest of the app (lost the red splat — consistent brand). New parametric test in `tests/test_static.py` pins all 9 templates link the tokens file + consume `--color-*`. **563 passed (+1)** / 1 skipped / 100 deselected. Layer 0: ruff 0 / mypy 0. Phase C (mono register applied to clip metadata in review.html, transcript timestamps when those views build) queued.

> **Closed Issue 99 Phase A — `_design-tokens.css` + pricing.html proof retrofit** (2026-05-31): Built `static/_design-tokens.css` (~250 lines, vanilla CSS, no build step) with the Linear-locked direction from DECISIONS: full :root palette + Inter/JetBrains Mono via Google Fonts (`display=swap` so system fallback renders instantly) + 4px spacing scale + 80-120ms motion + minimal component layer covering nav, card, .btn variants, .kbd chips, .mono data utility, .badge status pills, .disclaimer honesty band, .footer legal links. Retrofit pricing.html as the proof case: removed the Wave-7 inline `:root` stopgap, links to the shared file, page-specific styles now consume `--color-*` semantic tokens. Mono data register applied to minutes / price / $/min figures — first real use of the sans/mono composition pattern. Pop-tag swapped from a marketing-pill ("Most Popular") to a Linear-kbd-style outlined chip ("Most picked"). Tests: +2 in `tests/test_static.py` pinning both halves (tokens file shape + pricing consumption). **562 passed** (+1 from Wave 7's 561) / 1 skipped / 100 deselected. Layer 0: ruff 0 / mypy 0. Phase B (retrofit index/onboarding/insights/profile/review/tos/privacy/early-access) and Phase C (`.mono` applied to clip metadata / transcripts / video-table durations / DNA stat cards) queued — each as its own commit.

> **Closed Wave 7 — pricing.html CSS hotfix + Phase 1 lock-in for Issues 95 + 99** (2026-05-31): User-observed bug live on the freshly-deployed autoclip.studio: pricing page rendering in browser defaults. Root cause: `pricing.html:7` linked `/static/style.css` which never existed; every `var(--…)` in the inline `<style>` block resolved to empty. Fix: dropped the broken link; added `:root` block with `--bg / --surface / --border / --text / --muted / --accent` matching the inline-style palette other templates use; added `.nav`/`.nav-brand`/`.nav-links` rules. Test in `tests/test_static.py` pins both halves. **Stopgap** until Issue 99 supersedes. **Phase 1 also closed for Issues 95 + 99** via researched-menu selection: Issue 99 = Linear-style base (#0a0a0a / #5e6ad2 indigo / Inter + JetBrains Mono / hairline borders / 4px grid) + monospace data register for clip metadata / transcripts / timestamps; Issue 95 = Architecture B (Medal.tv-style companion app + folder watcher, backend exposes API-key-auth `POST /clips/ingest`). Backlog entries rewritten to lock the picks. **Tests:** 561 passed (+1 from Issue 101's 560) / 1 skipped / 100 deselected. Layer 0: ruff 0 / mypy 0.

> **Closed Issue 101 — Permanent fix for GH-hosted-runner billing block on deploys** (2026-05-31): One-line YAML change. `.github/workflows/docker-publish.yml` `runs-on: ubuntu-latest → runs-on: self-hosted` (matches `deploy.yml`, which moved in the Wave-5 close-out). Deploy pipeline (docker-publish → workflow_run → deploy) is now end-to-end self-hosted; eliminates GH-hosted billing as a deploy blocker permanently. CI / Quality / Integration remain on `ubuntu-latest` (informational only; not on the deploy critical path). New `tests/test_ci_config.py` (+3 unit tests) pins `runs-on: self-hosted` for both pipeline workflows + the `Docker publish` ↔ `workflow_run` name linkage so silent regressions can't slip in. `scripts/setup-runner.sh` banner updated to reflect coverage of BOTH pipeline workflows. Operational: runner is NOT yet installed on the VM — until then both workflows queue; `scripts/deploy.sh` remains the immediate fallback. **Tests:** 560 passed (+3) / 1 skipped / 100 deselected. Layer 0: ruff 0 / mypy 0 / format clean.

> **Closed Wave 6 — Done-vs-Visible audit fixes** (2026-05-31): Four mechanically-distinct sub-fixes. **Fix A** — new alembic migration `0014_backfill_onboarding_state` heals creators where `onboarding_state IN ('connected','awaiting_data')` AND a confirmed `creator_dna` row exists; `dna_pending` is intentionally excluded (legitimate rebuild-in-progress). Closes the Issue 98 carry-over. **Fix B** — added `<a href="/static/pricing.html">Pricing</a>` to the top nav of index/insights/profile/review (onboarding skipped per focused-task design); added a minimal `<footer>` linking Terms + Privacy + © AutoClip 2026 to every static template (9 pages). Closes the pre-launch Google OAuth verification gate around TOS/Privacy reachability. **Fix C** — removed Issues 84 and 92 from the queue list (both already closed above) + removed the duplicate Issue 84 close entry. **Fix D** — `index.html::linkVideo` and `index.html::generateClips` now consume `stream_url` + `task_id` from the POST response and register with `window.activeTasks.registerTask(...)`; the Wave-5 floating activity panel finally surfaces the upload→ingest→transcribe→signals and generate-clips streams on the dashboard. Existing 5s polling stays as belt-and-suspenders. **Tests:** +6 unit (Fix B nav/footer assertions + Fix D wiring assertions in `tests/test_static.py`) and +6 integration (Fix A backfill semantics in `tests/test_onboarding_state_backfill_integration.py`).

> **Closed Wave 5** (2026-05-31): Three fixes. **Fix 1** — extends fail-open `try/except redis.RedisError` to `routers/creators.py::sync_catalog`, `routers/creators.py::build_dna`, `routers/clips.py::render_clip` (3 sites × ~5 LOC each, mirrors Wave-3 Fix B exactly). Response models now `stream_url: str | None = None`. **Fix 2** — new `static/activeTasks.js` library: localStorage-backed lifecycle manager exposing `registerTask`/`getActiveTasks`/`subscribe`/`removeTask` on `window.activeTasks`. On every page mount, prunes >1h entries (matches server-side stream TTL), resumes EventSource per remaining entry with `Last-Event-ID`. **Fix 3** — new `static/activityPanel.js`: floating bottom-right Linear/Vercel-style widget shown on every authenticated page. Hidden when no tasks; collapsed badge "⚡ N running"; expanded shows per-task terminal-style streams. Wired into 6 authenticated templates (index, onboarding, insights, profile, review, pricing). onboarding.html + insights.html existing flows now ALSO call `activeTasks.registerTask` so the global panel surfaces them. **User-stated needs resolved:** "going tab-to-tab without refreshing" (localStorage + EventSource resume) AND "see new features on the website" (global activity panel on every page). **Tests:** 553 passed (+6 from Wave 4's 547) / 1 skipped / 94 deselected. Layer 0: ruff 0 / mypy 0 / format clean.

> **Closed Wave 4 — compliance + scale prep** (2026-05-31): Three small fixes. **Fix 1** — `routers/videos.py:262-279` wraps `aset_owner` in `try/except redis.RedisError` (mirrors Wave-3 Fix B/D); fail-open invariant now uniform across every aset_owner site. **Fix 2** — new Alembic migration `0013_refund_pack_id_unique` creates partial UNIQUE on `minute_packs(pack_id) WHERE reason='refund'`; `billing/refund.py` drops the read-then-write guard, catches `IntegrityError` from the SAVEPOINT, returns 0 on race (same pattern as `deduct_for_video`); closes the concurrent-refund double-credit race. **Fix 3 (Issue 75b)** — `YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS=30` setting + `purge_stale_youtube_analytics` daily Beat task that deletes stale rows from `video_metrics`, `retention_curves`, `audience_activity`, `demographics`. `docs/COMPLIANCE.md §2` expanded with concrete policy citation (verified via industry-standards-researcher against developers.google.com/youtube/terms/developer-policies §III.E.4.b + §III.D.2.3.b). CLAUDE.md pre-monetization checkbox flipped. **Tests:** 547 passed (+4 from Wave 3's 543) / 1 skipped / 94 deselected (default lane); 4 new integration tests pin the purge boundaries (5d/29d/35d). Layer 0: ruff 0 / mypy 0 / format clean.

> **Closed Wave 3 hotfix batch** (2026-05-31, kept for history below)
Previous: Wave 3 hotfix batch (3 SEV1s + 3 SEV2s).

> **Closed Wave 3 hotfix batch** (2026-05-31): Six small mechanical fixes addressing the regressions Wave 2 introduced + the carry-forward Stripe SEV1 (post-Wave-2 /assess flagged all six). **Fix A** — `worker/anthropic_stream.stream_and_emit` now accepts `tools` kwarg + `improvement/brief.py` threads it through (closes the SEV1 where 100% of streaming improvement briefs lost web_search grounding). **Fix B** — `routers/improvement.py` reorders `aset_owner` after row.job_id commit + wraps in `try/except RedisError` so a Redis blip returns `stream_url=None` instead of 500 (fail-open observability posture). **Fix C** — `routers/billing.py` wraps `create_checkout_session` in `await asyncio.to_thread(...)`, closing the carry-forward sync-Stripe-in-async SEV1. **Fix D** — `routers/auth.py:117-119` now stamps `aset_owner` on the post-OAuth catalog sync (one-line; same fail-open posture). **Fix E** — `_signals_async` now emits non-terminal `step:ingest_complete`; `_generate_clips_async` gets full emit instrumentation + terminal `done` on the same video_id stream key, so the SSE consumer stays subscribed through clip generation. **Fix F** — `_sync_channel_catalog_async` per-video failure handler emits `step:sync_metrics_skipped` (class name only — no exception message — preserves no-PII invariant). Tests: 543 passed (+10 from Wave 2's 533). Layer 0: ruff 0 / mypy 0 / format clean.

> **Closed Issue 92 — Universal progress visibility** (2026-05-31): Extended Issue-86's SSE primitive to 4 more long-running surfaces. Upload chain (`_ingest_async → _transcribe_async → _signals_async`) emits step events keyed by `video_id` (deterministic stream key — no Celery chain-id propagation needed). Render uses `clip_id` the same way. Catalog sync uses Celery `self.request.id` (single task, no chain) and emits per-video metric progress as `sync_metrics i=k total=N`. Improvement brief uses Celery `job_id` AND streams via the new `task_id` kwarg on `improvement/brief.py::generate_improvement_brief` (mirrors `dna/brief.py` Issue-86 pattern via `worker.anthropic_stream.stream_and_emit`). Routers stamp `progress.aset_owner` + return `stream_url` on all 4 endpoints. Frontend wired in `onboarding.html` (catalog sync) + `insights.html` (improvement brief); upload + render backends return `stream_url` for future Issues 100/95 UI consumers. 8 new tests in `tests/test_progress_emit_wiring.py` pin emit sequences, stream-key choice, terminal `done` events, and router wiring. 533 passed / 1 skipped / 89 deselected. Layer 0: ruff 0 / mypy 0.

> **Closed Issue 84 — AI/LLM efficiency assessment** (2026-05-31): Audited all 3 Anthropic call sites against current (May 2026) SDK + caching state, verified via industry-standards-researcher. Per-call-site reports written to `docs/assessment/llm/dna_brief.md`, `clip_scoring.md`, `improvement_brief.md` + consolidated `REPORT.md`. Key findings: (1) Sonnet 4.6 cacheable-prefix floor is 1024 tokens (not 2048 as our docstrings said) — cache markers on DNA brief + improvement brief silently don't engage today, 1.25× write premium for zero reads; (2) clip_scoring is the only call site where caching actually pays (1h TTL on DNA brief, correctly designed); (3) zero Opus-4.7-breaking parameters on our surface — clean migration path. Shipped Win A: `ANTHROPIC_WEB_SEARCH_TOOL` config default bumped `_20250305 → _20260209` (dynamic filtering: Claude pre-filters search results in code-exec before they hit the main context). 1-LOC config + 2 regression tests in `tests/test_brief_caching.py`. Follow-up issues flagged: Anthropic SDK 0.40 → 0.105.2 bump (unlocks TTL-tier observability), drop unproductive cache markers on DNA + improvement brief (post-SDK-bump so we can measure), per-call-site model settings + Haiku 4.5 A/B eval for clip scoring (~67% cost reduction opportunity).

**Queued for next session (in dependency-ordered execution sequence, 2026-05-31)**:
- **Issue 99** — UI redesign (Linear-style base + monospace data register). **Blocks everything below.** Phase 1 closed (direction picked); Phase 3 builds `static/_design-tokens.css` then retrofits 9 templates.
- **Issue 95** — OBS hotkey integration (Architecture B: companion app + folder watcher). Phase 1 closed; Phase 3 builds backend (`creator_api_keys` + `POST /clips/ingest`) here, companion app in a separate repo. Depends on Issue 99 for the API-key management UI.
- **Issue 93** — Insights page rebuild. SEV-1 UX. Inherits Issue 99 design.
- **Issue 94** — Clip-engine transparency. SEV-1 UX. Inherits Issue 99 design.
- **Issue 94** — Clip-engine transparency (why this clip, why-not for skipped videos). SEV-1 UX. Depends on 92.
- **Issue 99** — UI redesign (supersedes Issue 85). Phase 1 must present 5–8 reference sites for the user to pick patterns from.
- **Issue 100** — Onboarding tutorial + mandatory intake (related to Issues 96, 98, 99). Replaces today's silent "pending" status badges with self-explaining text.
- **Issue 96** — Multi-step / chat-driven intake form (CFO-Agent pattern; supersedes Issue 83 "optional" decision).
- **Issue 95** — Hotkey + OBS/streaming-software integration (instant-replay rolling-buffer clips). SEV-2 new feature.
- **Issue 97** — Livestream recap video (subscription-tier candidate; recurring vs minute-pack pricing).

**Prod-readiness gates still pending**:
- **RLS activation** — Hotfix B unblocks the manual workflow. Run `Activate RLS (Issue 79)` workflow with `dry_run=true` then `false`.
- **Issue 78f PgBouncer load test** — sole gate that moves the verdict from CONDITIONAL → YES.

**Blocked**: _(none)_

> **Closed Wave 1 — 6-hotfix batch** (2026-05-31): One branch, six commits, one CI cycle.
>
> - **Hotfix A — `worker/progress.py:214-232` aacquire_slot EXPIRE drift** (SEV-1 from `/assess`): Moved `client.expire()` out of the `count==1` branch so EXPIRE fires on every INCR. Bug: a creator holding ≥1 SSE streams continuously past `_STREAM_TTL_SECONDS=3600s` had the counter TTL elapse → next INCR reset to 1 → cap silently bypassed. Canonical Redis sliding-window pattern. 2 new regression tests pin the TTL refresh + cap behavior.
> - **Hotfix B — `billing/refund.py:41` AsyncSessionLocal → AdminSessionLocal** (SEV-1 from `/assess`): Refund is a system action with no per-creator context; under prod RLS the app-role session without `session.info["creator_id"]` returns zero rows from the `MinuteDeduction` SELECT → every refund silently no-ops. Matches the rest of the worker surface. **Now unblocks the RLS activation workflow** (was blocking the prod role split). Source-inspect + runtime-mock invariants pin the factory choice.
> - **Issue 89 — balance pre-check vs deduction mismatch**: New `check_balance_for_minutes(creator_id, minutes_needed, session)` helper raises 402 with concrete gap copy ("This video needs N minutes; you have M"). Wired into `/videos/upload` after `probe_duration_s` so a low-balance creator uploading a long video gets an actionable 402 BEFORE the R2 PUT, not a silent post-upload `failed` row. **Deviation from AC**: did NOT wire into `/clips/render` because `_render_clip_async` doesn't deduct — adding a per-clip pre-check there would deny re-renders of already-paid clips for no billing reason. Captured in `docs/DECISIONS.md`. 4 unit tests + 1 router-level integration test (mocks probe, asserts 402 + tmp cleanup + storage not called).
> - **Issue 90 — catalog rows excluded from /videos list**: `source_uri IS NOT NULL` filter on `list_videos`. Documented `source_uri IS NULL` as the canonical catalog-only marker in `docs/SOT.md` data-model section. Test introspects the compiled SQL to pin the filter.
> - **Issue 91 — "Clips ready" counter filters render_status=done**: Frontend filter in `static/index.html`; relabeled card "Clips rendered". Also fixed an unrelated unwrapping bug (`clips.length` was reading off the `{clips: [...]}` wrapper). Display now shows `M/N rendered` when partial. Static-page text assertion test.
> - **Issue 98 — DNA banner sticky + missing state transition**: Root cause was in `dna/profile.py::create_draft` — it never advanced `onboarding_state`, so the existing `confirm_draft` precondition (`if state == dna_pending`) never matched and state stayed `connected` forever. Fix: `create_draft` bumps `connected → dna_pending` so the canonical arc completes. 3 unit tests for the arc (idempotent on rebuild, no-regression on active). Frontend conditional at `static/index.html:160` already correct and now hides properly after confirm.
>
> **Layer 0 gates**: ruff 0 / mypy 0 / freshness ok. **Tests**: 523 passed / 1 skipped / 89 deselected (default lane). Integration tests for Issue 98 added; verification runs on CI's integration lane.

> **Closed Issue 88 — end-to-end verification** (2026-05-31): Initial Issue 88 deploy
> (commit `e9a2c3f`) shipped the filter-parity fix + `log_event` observability +
> targeted audit, all CI green. But when the user retried the build live, the
> data-gate still showed 0/0 because catalog sync phase 2 was silently failing
> on every video. Live ssh diagnostic against the worker container caught the
> real exception (`httpx.ReadTimeout`) — empty `str()` was why the warning log
> was blank. Hotfix `b464a34`: bumped read timeout 15s→60s, wrapped both YT
> retry loops in `try/except httpx.RequestError`, changed the catalog-sync
> warning to `%r` + `exc_info=True`. Re-verified: 3/3 manual `sync_video_analytics`
> calls returned OK; `metered_count_now: 21`. User then rebuilt DNA successfully
> — backend now has 3 `creator_dna` rows for Backboard Media (v1 draft, v2
> confirmed, v3 draft from a rebuild), 21 videos analyzed (6 longs + 15 shorts),
> 63 `dna_embeddings` rows, `optimal_clip_len_s=14.5`, `best_source_region=first_third`.
> Carry-over: `onboarding_state` did not advance to `active` despite v2 being
> `confirmed` — captured in Issue 98 ACs.

> **Closed Issue 88 — DNA filter parity + business-event observability** (2026-05-30): Closed the SEV-0 logical bug surfaced live on `reesepludwick@gmail.com` ("data-gate said 3 long + 20 shorts, build said insufficient 0/0"). Root cause: `check_data_gate` counted every Video row by kind; `rank_videos` required `ingest_status==done` AND metrics — two queries on the same table with diverging predicates. Fixes: `rank_videos` no longer requires `ingest_status==done` (DNA needs metrics only, not local-pipeline state); `check_data_gate` joins VideoMetrics + uses OR semantics (matches `build_patterns` raise condition); `sync_channel_catalog` chains a phase-2 `sync_video_analytics` call so metrics are present immediately (was waiting up to an hour for Beat refresh). New `observability.log_event(event, **fields)` helper emits structured JSON; wired into 7 user surfaces (auth callback, link, upload, sync_catalog, build_dna, confirm_dna, feedback) + diagnostic `dna_build_insufficient_data` event with total/metered/per-kind counts. Targeted display-vs-filter audit returned 4 findings (2 SEV-1, 2 SEV-2) — one fixed inline (data-gate `ready` used AND, blocking long-only/shorts-only creators), three filed as Issues 89-91. 8 new tests. **509 passed / 1 skipped / 85 deselected**; ruff 0 / mypy 0.
**Blocked**: _(none)_

> **Closed Issue 88 — DNA filter parity + business-event observability** (2026-05-30):
> Detailed in `docs/DECISIONS.md` (2026-05-30 entry). Triggered by a live user
> report: connecting `reesepludwick@gmail.com` showed 3 long + 20 shorts in
> step 2 but the DNA build said "Insufficient data: 0 long, 0 shorts." Two
> queries silently disagreed — the data-gate counted every Video row, the
> DNA build required `ingest_status==done` (set only by the local-clip pipeline,
> never by catalog sync) AND metrics. The fix: aligned both paths on a single
> predicate (metrics-only); chained metrics fetch into `sync_channel_catalog`
> so the user doesn't wait an hour for the Beat refresh. Then added a class
> of debug observability: `log_event(event, **fields)` helper + diagnostic
> log on the insufficient-data raise + 7 wired user surfaces. A targeted
> subagent audit on the same failure shape found a SEV-1 in `check_data_gate.ready`
> (used AND while the build accepts OR — blocked long-only creators); fixed
> inline. Three other findings spawned Issues 89, 90, 91. All gates green:
> ruff 0, mypy 0, **509 passed / 1 skipped / 85 deselected** (+8 new).

> **Closed Issue 87 — Catalog sync wiring + 180s Shorts threshold** (2026-05-30): Closed the SEV-0 onboarding bug surfaced live on `reesepludwick@gmail.com` / "backboard media" (20 Shorts + 3 long-form, data-gate reporting 0/0). `youtube/analytics.py::sync_video_catalog` was dead code — `grep -rn` returned exactly one hit (the definition itself). New `sync_channel_catalog` Celery task wraps it, enqueued (a) from the OAuth callback for new creators and (b) prepended to each creator's iteration of `_refresh_youtube_analytics_async`; new `POST /creators/me/catalog/sync` endpoint (5/min, 202+task_id) wires the onboarding "Refresh data status" button into a true sync trigger. Compounding fixes: `classify_video_kind` now reads `SHORTS_MAX_DURATION_S=180` (was hardcoded `<=60s` — YouTube raised the Shorts max to 180s in Oct 2024); `/videos/link` resolves kind via `get_videos_metadata`; `/videos/upload` probes duration locally via `probe_duration_s` before R2 PUT. 9 new unit tests + 1 OFF_COURSE_BUGS row closed. 501 passed / 1 skipped / 85 deselected; ruff 0 / mypy 0.
**Blocked**: _(none)_

> **Closed Issue 87 — Catalog sync wiring + 180s Shorts threshold** (2026-05-30):
> Documented in detail in `docs/DECISIONS.md` (2026-05-30 entry). Investigation
> triggered by the user reporting that connecting `reesepludwick@gmail.com` (channel
> "backboard media") and clicking "Refresh data status" returned 0 long-form videos
> and 0 Shorts despite the channel having 23 actual uploads. Root cause was structural,
> not data-related: the only function in the codebase that pulls a creator's uploads
> playlist (`youtube/analytics.py::sync_video_catalog`) had zero callers. The OAuth
> callback never called it; the hourly Beat refresh task only re-fetched analytics for
> already-known Video rows. The two write surfaces for new Video rows
> (`/videos/link`, `/videos/upload`) both hardcoded `kind=VideoKind.long` and the
> Shorts classifier was at the pre-2024 `<=60s` threshold, so even manual linking
> would have mis-bucketed every Short.
>
> Fix: new `sync_channel_catalog` Celery task wraps the existing
> `sync_video_catalog` (idempotent on `UNIQUE(creator_id, youtube_video_id)`, with
> token resolution + commit + safe-fail). OAuth callback enqueues it via `.delay()`
> for new creators so the redirect budget isn't blocked. The hourly Beat job prepends
> it to each per-creator iteration so newly published videos are discovered every
> refresh tick. New `POST /creators/me/catalog/sync` endpoint (5/min, 202+task_id)
> wires the onboarding "Refresh data status" button into a real sync trigger; the
> button now polls the data-gate every 4s until the row count stabilises. New
> `SHORTS_MAX_DURATION_S=180` config (matches YouTube's 2024 spec — verified at
> [Create a Short](https://support.google.com/youtube/answer/10059070)).
> `/videos/link` resolves kind via `get_videos_metadata` (safe-fails to long-form
> with a warning log); `/videos/upload` probes duration locally before R2 PUT. 8 new
> tests in `tests/test_catalog_sync.py` + 4 boundary tests updated in `test_analytics.py`;
> 3 retention-task mocks + 1 oauth-lifecycle mock updated to patch `sync_video_catalog`.
> All gates green: ruff 0, mypy 0, **501 passed / 1 skipped / 85 deselected**.

> **Closed Issue 86 — Live progress surface for long-running tasks** (2026-05-30): A
> reusable per-task observability primitive built on Redis Streams + SSE, designed
> to eliminate the frozen-spinner experience that triggered today's prod incident
> (3+ min of nothing during a `build_dna` crash-loop). DNA build is the first wired
> call site — `_build_dna_async` now emits `step` events at `acquire_lock`,
> `analyze_patterns`, `analyzed_patterns` (with counts), `call_claude`, `embed`, plus
> terminal `done`/`error`. The LLM segment streams via the new `generate_brief_streaming`
> path which wraps Anthropic's `messages.stream(...)` context manager — surfaces
> `message_start.usage` as a `cache` event (cache HIT/miss confirmable BEFORE the
> first token), forwards `text_delta` as `token` events, and is forward-compatible
> with `thinking_delta` once the SDK is bumped in Issue 84. Three layers, all
> additive: (1) `worker/progress.py` with `sync_emit`/`aemit`/`aset_owner`/
> `aacquire_slot`/`aread_since` against `task:{task_id}:events` Redis Streams
> (MAXLEN ~ 200, EXPIRE 3600 on terminal); (2) `routers/tasks.py` SSE endpoint
> `GET /tasks/{task_id}/events` with session-cookie auth, Redis-key ownership
> check (`task:{task_id}:owner` set by `routers/creators.py::build_dna`),
> `Last-Event-ID` resume, 12s `: keepalive` comment, per-creator concurrent cap
> of 3, 600s hard lifetime; (3) `static/progressStream.js` — ~50-line vanilla-JS
> EventSource reducer + a terminal-style `<pre>` block in `static/onboarding.html`.
> Cloudflare-Tunnel-safe headers (`Cache-Control: no-cache` + `X-Accel-Buffering: no`)
> ensure no proxy buffers the stream. New subprocess integration test
> `tests/test_worker_imports_integration.py` spawns a real Celery worker subprocess
> and asserts `from dna.brief import generate_brief` succeeds — guards the
> Dockerfile PYTHONPATH hotfix from today's incident forever. 7 sub-decisions
> (transport, bridge, thinking API, cache stat location, wire format, late-joiner,
> SSE security) captured in `docs/DECISIONS.md`. +24 unit tests + 1 integration.
> All gates green: ruff 0, mypy 0, **492 passed / 1 skipped / 85 deselected**.
>
> **Closed Issue 83 — Creator Intake Form** (2026-05-30): Adds a stated-identity layer
> (niche, audience, mission, tone, hard-nos, optional style sample) that is captured via
> a 5-field intake (3 required, 2+ optional via progressive disclosure) and fused with
> the inferred `creator_dna` at LLM-call time. Two structural decisions per the 2026
> industry-standard research (see DECISIONS 2026-05-30 entry): (1) stated and inferred
> are STRICTLY SEPARATE tables fused at query time — silently overriding stated intent
> with engagement signals is the YouTube-algorithm problem recreated inside our own
> tool, contradicting the North Star; (2) append-only versioning (partial unique
> `uq_one_current_identity_per_creator` is the DB backstop) keeps the audit trail
> intact. New `dna/identity.py` provides `get_current` / `get_history` / `upsert_identity`
> with FOR UPDATE serialization + IntegrityError race recovery, plus
> `format_for_prompt` that returns `None` (not "(no identity)") when missing for
> prompt-cache friendliness. New `dna/conflict.py` flags stated-niche-vs-inferred-pattern
> mismatches as a non-blocking profile-page nudge per the research's honesty pattern.
> `dna/brief.py` accepts a `stated_identity` block and moves the `cache_control`
> breakpoint to the new last stable block. `worker/tasks.py::_build_dna_async` fetches
> identity via `AdminSessionLocal` and passes through. New `youtube/categories.py`
> exposes the stable 15-option YouTube Data API niche list. New endpoints in
> `routers/creators.py`: public `GET /creators/niches` (intake form depends on it
> pre-session); authed `GET/POST /creators/me/identity` and
> `GET /creators/me/identity/history`. `static/onboarding.html` gets an optional
> 45-second intake card; `static/profile.html` gets full edit + version summary +
> conflict nudge. Alembic `0012_creator_identity`. +22 unit tests + 5 integration
> tests (append-only invariant, per-creator isolation, conflict detection, cache
> breakpoint placement). All gates green.

> **Prior Active**: Issue 78 — salvaged-from-PR#6 work. 78a (#9), 78b (#10), 78d (#11), 78g (#12), 78c (mypy 30→0) ✅ done. Remaining: **enable the `disallow_untyped_defs` ratchet** (deferred from 78c — ~20 pre-existing untyped-def signatures to annotate first), 78e (analytics retention purge — needs confirmed ToS staleness figure + data-deletion sign-off), 78f (PgBouncer load harness — needs real staging).

> **Closed Issue 78d — improvement-brief 202 + poll** (2026-05-30): the ~120s Claude +
> web_search brief moved off the request path (it could exceed an LB timeout). New
> `ImprovementBrief` model + `improvement_brief_status` enum (one row/creator) + migration
> 0009. `POST /creators/me/improvement-brief` → 202, debounces an in-flight build, enqueues
> `generate_improvement_brief`; the worker builds the creator-scoped analytics + DNA brief and
> runs the LLM (idempotent on `job_id`, safe-fail with a generic message — no token/PII/trace);
> `GET` polls the stored row; `insights.html` POST→poll. Mirrors the DNA-build precedent. +8
> integration tests; 3 GET-based isolation/offload tests rebased onto the task path;
> rate-limit test updated (10/hour LLM cap moved GET→POST). Gates: ruff 0, mypy 30, default
> 425 passed/1 skipped, integration 66 passed. Rationale + sources in DECISIONS (2026-05-30).

> **Closed Issue 75(f) — observability** (2026-05-29): new observability.py — a pure-ASGI
> RequestIDMiddleware (reads/mints X-Request-ID into a ContextVar, echoes it on the response;
> added outermost in main.py); JSON structured logs via JsonLogFormatter + RequestIDLogFilter
> (request_id on every line; configure_logging replaces basicConfig, idempotent, text fallback
> for dev); Prometheus golden signals (http_request_duration_seconds labelled by route template;
> celery_task_duration_seconds + celery_tasks_total) at /metrics gated by METRICS_ENABLED. The
> correlation id propagates API→Celery via before_task_publish/task_prerun/task_postrun signals
> (weak=False — Celery connects weakly by default). Added prometheus-client==0.25.0 (single CVE-clean
> dep; the correlation layer is hand-rolled to add zero new surface). Config: LOG_JSON,
> REQUEST_ID_HEADER, METRICS_ENABLED (+ .env.example). Deferred: OpenTelemetry distributed tracing.
> +9 DB-free tests; **410 passed, 1 skipped, 55 deselected**; gates ruff 0 / mypy 30 / bandit 0,0 /
> pip_audit 0. Rationale + sources in DECISIONS (2026-05-29).

> **Closed Issue 75(a) — pip-audit CVE remediation** (2026-05-29): 14 known vulns → 0.
> Patched 6 packages in requirements.txt: cryptography 43.0.3→46.0.7, python-multipart
> 0.0.20→0.0.27, PyJWT 2.9.0→2.12.0, lightgbm 4.5.0→4.6.0, python-dotenv 1.0.1→1.2.2,
> starlette 0.41.3→0.49.1 (forced FastAPI 0.115.4→0.120.4, smallest bump whose pin admits
> starlette 0.49.1). The disputed PyJWT PYSEC-2025-183 dropped off (2.12.0 out of its
> affected range). 2 residuals accepted-risk in run_layer0.py's PIP_AUDIT_IGNORES allowlist:
> pytest GHSA-6w46-j5rx-g56g (dev-only; pytest-asyncio caps pytest<9 — a test-stack cascade)
> and starlette PYSEC-2026-161 (Host header, fixable only on the starlette-1.x line / FastAPI
> 0.136.x). baselines.json pip_audit_vulns ratcheted 14→0. Verification: pip check clean;
> **401 passed, 1 skipped, 55 deselected** on bumped deps; run_layer0 gates ruff 0 / mypy 30 /
> bandit 0/0 / pip_audit 0. Justification + version evidence in DECISIONS (2026-05-29).
> Follow-up: starlette-1.x migration to close PYSEC-2026-161 (tracked in issues.md).

> **Closed Batch 8 / Issues 73(partial) + 74 + 75(partial)** (2026-05-29): Memory: librosa
> loads at sr=16000 (~3x less RAM) + WhisperX/SDK-client singletons. Security: youtube_video_id
> validated (^[A-Za-z0-9_-]{11}$ -> 422) before reaching a storage key. Robustness: Stripe
> prod fail-fast config validator; upload_intel skips out-of-range rows instead of 500.
> Deferred to Issue 75 tracking (with rationale in DECISIONS): full response_model coverage,
> Deepgram file-stream, 14 CVEs, analytics-retention cadence, observability, mypy->0, clip-scorer
> caching, scorer cache, brief 202/poll. DB-free unit tests for all four hardening items; updated
> 3 upload-streaming tests to valid 11-char IDs. Test count: **401 passed, 1 skipped, 55 deselected**
> (+4). Gates: ruff 0, mypy 30, bandit 0/0, coverage 70.45%.

> **Closed Issue 71** (2026-05-29, Batch 7): from_bytes monkeypatched a joblib global
> (not thread-safe -> RCE allowlist defeatable under concurrent loads); build_and_save
> max()+1 raced to IntegrityError; predict_score swallowed errors into 0.5. Fix: module
> threading.Lock around the swap (direct unpickler rejected -- joblib signature is
> version-fragile, see DECISIONS); pg_advisory_xact_lock(hashtext(creator_id)) for the
> version assignment; predict_score validates n_features_in_ and raises; load_latest
> returns None on feature-schema drift; rerank scores-then-mutates and falls back to DNA
> on scorer error. DB-free unit tests + fixed an existing mock-session test for the extra
> advisory execute. Test count: **397 passed, 1 skipped, 55 deselected** (+2). Gates: ruff 0, mypy 30, bandit 0/0, coverage 70.47%.

> **Closed Issue 70** (2026-05-29, Batch 6): poll_clip_outcomes re-polled every published
> clip every 7 days forever (no terminal guard) -> unbounded YouTube-quota drain. Added
> `clip_outcomes.final` (migration 0007) + partial index; the 7d checkpoint sets final and
> the query excludes final rows + caps candidates to clips created within 10 days; commit
> per creator. Integration test: 7d poll marks final, finalized outcome skipped. Test count:
> **395 passed, 1 skipped, 55 deselected** (+1 integration). Gates: ruff 0, mypy 30, bandit 0/0, coverage 70.38%.

> **Closed Issue 69** (2026-05-29, Batch 5): Both briefs interpolated per-creator
> data into the cached system block (prefix changed every call); improvement returned
> the web_search preamble instead of the answer. Split system into static-cached +
> volatile-uncached blocks; return `text_blocks[-1]`. `/claude-api` finding: Sonnet
> 4.6's min cacheable prefix is 2048 tokens and these static prefixes are ~400 — so
> caching can't engage for these low-frequency calls regardless; the split is
> correct-structure, and the real caching win (clip scorer's reused per-creator
> prefix) is tracked under Issue 75. DB-free unit tests for the split + final-block
> extraction; updated the existing 1-block test to the 2-block contract. Test count:
> **395 passed, 1 skipped, 54 deselected** (+4). Gates: ruff 0, mypy 30, bandit 0/0,
> coverage 70.47%.

> **Closed Issue 72** (2026-05-29, Batch 4b): Per-call `httpx.AsyncClient()` with no
> timeout on the token-refresh hot path; client built inside the retry loop in
> data_api/analytics. New `youtube/_http.py` lazy per-process singleton
> (`Timeout(15, connect=5)`) + `aclose()` reused everywhere and closed on API/worker
> shutdown; 5xx now backs off + retries. Rebased the oauth-lifecycle tests onto the
> `_http.client` boundary (they'd mocked the old per-call httpx). Test count: **392
> passed, 1 skipped, 54 deselected** (+2). Gates: ruff 0, mypy 30, bandit 0/0,
> coverage 70.49%.

> **Closed Issue 68** (2026-05-29, Batch 4b): Sync `generate_brief`, Voyage `_embed`
> (tenacity sleeping on the loop), `transcribe_audio`, and `extract_audio_events` ran
> on the worker's singleton loop with no transcription upper bound. All offloaded via
> `asyncio.to_thread`; transcription wrapped in `asyncio.wait_for(..., timeout=
> TRANSCRIPTION_TIMEOUT_S=300)` for a job-level bound. SDK-native timeouts deferred to
> Issue 75 (SDKs not installed to verify). DB-free unit test for the Voyage offload;
> existing pipeline tests confirm behavior-preservation. Test count: **390 passed, 1
> skipped, 54 deselected** (+2). Gates: ruff 0, mypy 30, bandit 0/0, coverage 70.32%.

> **Closed Batch 4a / Issues 66 + 67** (2026-05-29): Three synchronous calls ran on
> the API event loop (120s improvement brief, large-file upload, account-deletion
> purge), stalling every concurrent request on the worker (axis B). All three moved
> to `await asyncio.to_thread(...)`. The brief's 120s request duration (vs LB timeout)
> is tracked for a Celery 202/poll follow-up under Issue 75. Integration tests assert
> each call is offloaded. Test count: **388 passed, 1 skipped, 54 deselected** (+2
> integration). Gates: ruff 0, mypy 30, bandit 0/0, coverage 69.57%.

> **Closed Batch 3 / Issue 65** (2026-05-29): pgvector HNSW (`vector_cosine_ops`,
> m=16/ef_construction=200) on `dna_embeddings.embedding` matching the `<=>` query,
> plus `ix_clip_feedback_creator_id`; both `CREATE INDEX CONCURRENTLY` in an
> alembic autocommit_block (migration 0006). Reading the schema corrected two
> assessment items already covered (dna_embeddings.creator_id btree from 0001;
> preference_models.creator_id via the (creator_id,version) unique index) — no
> redundant indexes added. Integration test introspects `pg_indexes`. Migration-only,
> so the unit-coverage floor holds. Test count: **388 passed, 1 skipped, 52 deselected**
> (+2 integration). Gates: ruff 0, mypy 30, bandit 0/0, coverage 69.54%.

> **Closed Batch 2 / Issues 63 + 64** (2026-05-29): Idempotent unique-keyed writes.
> 63: `build_dna` stamps the Celery `task_id` as `creator_dna.build_job_id` and
> `_build_dna_async` early-returns before the paid LLM/Voyage calls on redelivery;
> `confirm_draft` locks `with_for_update()` + partial unique index
> `uq_one_confirmed_dna_per_creator` (ordered flush, non-deferrable). 64:
> `grant_minutes` now mirrors `deduct_for_video` (fast-path + SAVEPOINT +
> IntegrityError) so duplicate Stripe deliveries credit once. Migration `0005`.
> Integration tests for both. **Coverage floor moved 69.97→69.54%** (justified:
> DB-only idempotency code is integration-tested, not visible to the unit-coverage
> gate — see DECISIONS). Test count: **388 passed, 1 skipped, 50 deselected** (+3
> integration; updated 1 mocked unit test). Gates: ruff 0, mypy 30, bandit 0/0,
> coverage 69.54%.

> **Closed Batch 1 / Issues 61 + 62** (2026-05-29): Celery is at-least-once. A
> redelivered `build_signals`→`generate_clips` wiped feedback/outcomes via
> cascade-delete (data loss; corrupted the Issue-60 training signal), `acks_late`
> without `reject_on_worker_lost` dropped OOM-killed jobs, and no time limit meant a
> long task redelivered while still running. Fix: `generate_and_rank_clips`
> early-returns existing clips (idempotent, never cascade-wipes); added
> `task_reject_on_worker_lost` + the `soft(3000)<hard(3300)<visibility(3600)`
> invariant; `_render_clip_async` skips when already done. DB-free config-invariant
> test + integration tests (feedback survives re-gen; render skips when done).
> Test count: **388 passed, 1 skipped, 47 deselected** (+3 unit, +2 integration).
> Gates: ruff 0, mypy 30, bandit 0/0, coverage 70.02%.

> **Closed Issue 60** (2026-05-29): Personalization was dead code — `build_and_save`
> had no caller and `rerank_with_preference` was never invoked, so ranking was
> DNA-only (the North-Star "learns your style" loop never ran). Fix: idempotent,
> self-debouncing `retrain_preference` Celery task enqueued from the feedback
> endpoint; `rerank_with_preference` now called at the end of `generate_and_rank_clips`;
> flat 50/50 blend replaced with `preference_weight(label_count)` — 0 below
> PERSONALIZATION_THRESHOLD_LABELS (honest DNA fallback), ramping to
> PREFERENCE_WEIGHT_CAP by 2× the threshold (hybrid cold-start standard). Version-race
> + unpickler thread-safety deferred to Issue 71 (retrain catches IntegrityError
> meanwhile). DB-free unit tests (weight curve + rerank gating) + integration test
> (trains v1 then self-debounces). Test count: **385 passed, 1 skipped, 45 deselected**
> (+6 unit, +1 integration). Gates: ruff 0, mypy 30, bandit 0/0, coverage 70.18%.

> **Closed Issue 59** (2026-05-29): The render cut from `clip.start_s` (fixed
> peak−75s) while scoring/API/eval all key on `setup_start_s` → delivered Shorts
> didn't clip the setup. Fix: render via `_render_start_for(clip)` (pure helper,
> coalesces to `start_s` only when nullable `setup_start_s` is unset); set
> `-accurate_seek` explicitly. The assessment's "GOP drift" SEV-2 was a false
> positive — re-encode pipelines accurate-seek by default (DECISIONS). DB-free unit
> guards + an integration test that the persisted setup_start_s reaches the render.
> Test count: **379 passed, 1 skipped, 44 deselected** (+3 unit, +1 integration).
> Gates: ruff 0, mypy 30, bandit 0/0, coverage 70.06%.

> **Production assessment run** (2026-05-29): `/assess` across all 11 modules →
> verdict **PRODUCTION-READY = NO**. 1 BLOCKER, 25 SEV-1, 39 SEV-2, 34 cleanup;
> no cross-tenant leak, bandit 0/0. Findings tracked as Issues 58–75; full register
> in `docs/assessment/`. Also shipped the repeatable harness (`/assess` skill +
> ratcheted CI gates in `quality.yml` + baselines), the `best-practices` skill +
> freshness convention (`docs/SKILL_FRESHNESS.md`), and SSOT model-id config.

> **Closed Issue 58** (2026-05-29): psycopg3 prepared statements are incompatible
> with PgBouncer transaction-pooling mode (the production pooler) → would throw
> `prepared statement "_pg3_…" does not exist`; CI never caught it (direct
> Postgres). Fix: `connect_args={"prepare_threshold": None}`; pool ceiling cut
> 30→20/pod to stay under the 25-conn sidecar; `pool_recycle=1800`. Connection-
> budget inequality recorded in DEPLOYMENT.md; engine config guarded by
> `tests/test_db_engine_config.py`. Load-proof behind real PgBouncer deferred to
> staging Locust. Test count: **376 passed, 1 skipped** (+3). Gates: ruff 0, mypy 30,
> bandit 0/0, coverage 70.03%.

> **Closed Issue 79** (2026-05-28): Postgres RLS implementation. Closes the
> structural defense-in-depth gap that allowed Issue 33 (missed `creator_id`
> filter → cross-creator analytics in a Claude prompt). New alembic revision
> `0010_rls_policies` creates roles `creatorclip_app` (LOGIN, no BYPASSRLS) +
> `creatorclip_migrate` (LOGIN, BYPASSRLS granted out of band), grants the
> app role full DML on `public` (plus `ALTER DEFAULT PRIVILEGES` for future
> tables), and enables + forces RLS on the 12 tenant-owned tables
> (`videos`, `audience_activity`, `demographics`, `youtube_tokens`,
> `creator_dna`, `dna_embeddings`, `clips`, `clip_feedback`,
> `preference_models`, `minute_packs`, `minute_deductions`, `usage`). Policies
> read `current_setting('app.creator_id', true)::uuid` on USING + WITH CHECK.
> `creators` and `audit_log` are exempt — the former because the FastAPI
> auth dependency must resolve the current creator before the GUC is set,
> the latter because ops/oncall need to read all rows.
>
> Application wiring: new optional `DATABASE_MIGRATION_URL` env var (falls
> back to `DATABASE_URL` for single-role dev/CI); `db.py` now exposes
> `AsyncSessionLocal` (app role) AND `AdminSessionLocal` (admin role) — a
> global `after_begin` event listener on `Session` emits `SET LOCAL
> app.creator_id` whenever `session.info["creator_id"]` is set;
> `auth.get_current_creator` attaches the resolved creator id to
> `session.info` after the (exempt) Creator lookup. Worker tasks all moved
> from `db.AsyncSessionLocal()` to `db.AdminSessionLocal()` (16 sites) —
> worker code is trusted internal and many tasks are inherently
> cross-tenant (purge, poll_clip_outcomes, analytics refresh).
>
> Two minor implementation decisions surfaced and resolved (see DECISIONS):
> (a) JWT-to-creator bootstrap via the `creators` table exemption rather
> than a middleware pre-parse; (b) RLS-guarantee tests use `SET LOCAL ROLE
> creatorclip_app` within a transaction to assume the non-BYPASSRLS role
> for the visibility assertion — keeps existing integration tests
> untouched.
>
> New `tests/test_rls_isolation_integration.py` (marker: `integration`)
> seeds Creator A + B with one row per tenant table, then under
> `creatorclip_app` role + Creator A's GUC asserts that unfiltered
> `SELECT creator_id FROM <each tenant table>` returns zero Creator B rows.
> Second test verifies the `creators` table remains visible to the app role
> with no GUC set (auth-bootstrap path).
>
> Mutation rowcount audit (AC carry-over): satisfied by construction — the
> only two raw `session.execute(update/delete)` outside the ORM session
> pattern target the exempt `creators` table; everything else routes
> through `session.get(Model, id)` → mutate → commit, where `session.get`
> returns `None` for RLS-blocked rows and the existing
> `if not video: raise 404` is the rowcount guard. Documented in DECISIONS.
>
> Production runbook in `docs/DEPLOYMENT.md` covers the one-time SQL ops:
> `ALTER ROLE creatorclip_migrate BYPASSRLS`, set passwords, transfer table
> ownership to `creatorclip_migrate`, update `/opt/autoclip/.env` with
> `DATABASE_MIGRATION_URL`, restart app. pgbouncer-future caveat pinned:
> transaction pooling only, never statement pooling.
> Test count: **381 passed, 1 skipped, 56 deselected** (+2 RLS integration).

> **Closed Issue 38 Wave 1** (2026-05-28): Sync-in-async fixes for the Celery
> ingest pipeline. A full-codebase audit found 23 instances of sync external calls
> inside `async def` (class 1) or `await` while a DB session was open (class 2);
> Wave 1 closed all the class (1) findings in the Celery hot path (~14 of 23).
> Wave 2 is filed as Issue 82 — covers the AsyncAnthropic/AsyncVoyage SDK swap
> across `dna/brief.py` / `improvement/brief.py` / `clip_engine/scoring.py`, the
> router session-order refactor (`routers/auth.py` / `videos.py` / `clips.py` /
> `billing.py`), the `clip_engine/ranking.py` compute/persist split, and the
> 10-concurrent-improvement-brief load test.
>
> Wave 1 changes: new async wrappers in `worker/storage.py` (`aupload_file`,
> `adelete_file`, `adelete_prefix`, `alocal_path` — all dispatch to boto3 via
> `asyncio.to_thread`); the four Celery pipeline tasks
> (`_ingest_async` / `_transcribe_async` / `_signals_async` / `_render_clip_async`)
> now use the async wrappers + offload sync subprocess (ffmpeg / probe), librosa,
> WhisperX/Deepgram, and `render_clip_file` to threads; `_build_dna_async` wraps
> the sync Anthropic `generate_brief` call in `to_thread`; `dna/embeddings.py`
> gets a new `_aembed` async wrapper around the sync Voyage `_embed`;
> `_purge_stale_source_media_async` was restructured to release the session
> during the boto3 delete loop (select tuples → close → loop deletes via
> `adelete_file` → reopen session for a single UPDATE) — previously held one
> session across every R2 round-trip in the sweep.
>
> Test patches updated: `test_retention_tasks.py` for the new purge two-session
> + tuple shape and for `alocal_path`; `test_worker_pipeline.py` (Issue 52 file
> shipped earlier the same session) for `alocal_path`. Renamed worker tests
> still pass at 381 / 1 skipped / 54 deselected.

> **Closed Issue 52** (2026-05-28): Worker pipeline integration tests. The seven
> Celery async functions in `worker/tasks.py` (`_ingest_async`, `_transcribe_async`,
> `_signals_async`, `_render_clip_async`, `_generate_clips_async`, `_build_dna_async`,
> `_poll_clip_outcomes_async`) had no direct end-to-end coverage —
> `test_pipeline_trigger.py` only asserted registration / task chaining. New
> `tests/test_worker_pipeline.py` pins all 5 ACs against real Postgres with mocks at
> the storage (R2 / boto3) and external-SDK (YouTube Data API, ffmpeg) boundaries.
> Notable design: AC4 (per-creator median) seeds two creators with disjoint
> VideoMetrics — same fetched view count (100) yields opposite `performed_well`
> labels (A=False because 100 < 500 median, B=True because 100 ≥ 20 median) —
> a global-median computation would label both identically. AC5 (build_dna ValueError
> bypasses retry) calls `_build_dna_async` directly per the established
> `test_dna_build_idempotency.py` pattern; the task wrapper's `except ValueError:
> raise` is pinned by inspection because `build_dna.apply()` would call `asyncio.run`
> from inside the running pytest-asyncio loop (RuntimeError). No real fixture media
> files needed — `local_path` is mocked to yield a temp file, matching the existing
> `test_purge_integration.py` / `test_generate_clips_retry_integration.py` pattern.
> Test count: **381 passed, 1 skipped, 54 deselected** (+5 integration).

> **Closed Issue 56** (2026-05-28): Postgres Row-Level Security research-and-decide.
> Decision: **adopt RLS** as defense-in-depth underneath the existing
> application-layer always-filter for every tenant-owned table. Trigger context:
> the SEV-0 Issue 33 leak (a missed `creator_id` filter exposed cross-creator
> analytics to a Claude prompt) demonstrated that application-layer filtering is a
> linting problem disguised as a security property. RLS converts that into a
> structural guarantee: the database refuses to return cross-tenant rows even when
> application code forgets the WHERE. Implementation sketch pinned in
> `docs/DECISIONS.md`: 12 tables with direct `creator_id` columns get policies;
> two-role split (`creatorclip_app` no-BYPASSRLS + `creatorclip_migrate` BYPASSRLS;
> new `DATABASE_MIGRATION_URL`); `SET LOCAL app.creator_id` injected via
> SQLAlchemy `after_begin` event listener sourcing from FastAPI auth context;
> `FORCE ROW LEVEL SECURITY` on every covered table; mutation paths audit
> rowcount-zero-→-404. pgbouncer-future answer pinned: safe with transaction
> pooling, unsafe with statement pooling (we don't run pgbouncer today). Sources:
> Crunchy Data, pganalyze, Bytebase footguns writeup, SQLAlchemy 2.0 async docs
> + discussion #10469, Microsoft Azure multi-tenant guidance. **Implementation
> split to new Issue 79** — the Issue 56 spec was explicitly "research-and-decide",
> and the implementation is substantial enough (alembic migration + role split +
> middleware + mutation audit) to warrant its own focused PR. The decision
> ships now and Issue 79 inherits the carry-over ACs.

> **Closed Issue 57** (2026-05-28): Automatic refund on terminal ingest failure.
> Issue 34 made minute deduction per-video-idempotent, but a terminally-failing ingest
> still left the deduction in place. Policy decided (see DECISIONS): automatic refund,
> all terminal failure classes, surfaced via billing-history `MinutePack` row only
> (email + in-app banner split to new Issues 58 + 59 — both require infrastructure
> we don't have yet). New `billing/refund.py:refund_for_video` is idempotent on
> `pack_id="refund:<video_id>"`; new Celery base class `RefundOnFailureTask` in
> `worker/tasks.py` fires only when retries are exhausted, extracts `video_id` from
> `args[0]`, dispatches via `run_async`, and swallows internal exceptions so the
> task's original terminal failure stands. Applied to `ingest_video`,
> `transcribe_video`, `build_signals` (the three tasks where minutes can have been
> deducted by the time failure terminates). No alembic migration — `MinutePack`
> already supports the compensating-grant pattern. Disclosure language added to
> `docs/COMPLIANCE.md` as the canonical user-facing copy until pricing / ToS pages
> land in Phase 3.
> Test count: **381 passed, 1 skipped, 49 deselected** (+3 unit, +3 integration).

> **Closed Issue 46** (2026-05-28): Generate-clips retry safety + outcomes time-window
> bug. Two regressions in one issue. (1) `clip_engine/ranking.py:generate_and_rank_clips`
> unconditionally `DELETE FROM clips WHERE video_id = ...` before reinserting candidates;
> a late retry of `generate_clips` after `render_clip` had already completed wiped the
> `done` Clip rows, orphaning R2 objects and breaking the `ClipOutcome` FK chain. Fix:
> narrowed the DELETE WHERE to exclude `done` and `running` rows, and added an
> idempotency guard at the top of `_generate_clips_async` — if any `done` clip already
> exists for this video, log and return without re-extracting candidates. (2)
> `_poll_clip_outcomes_async`'s 7d arm had no upper bound on `Clip.created_at`, so every
> clip past its 7d checkpoint re-polled YouTube Data API every hour forever. Fix: added
> `Clip.created_at > now() - interval '30 days'` to the WHERE — after 30 days the
> `performed_well` label is stale enough that flipping it retroactively offers no
> preference-model signal. No migration needed. Predicate logic pinned via two unit
> tests in `tests/test_outcomes.py`; all three regressions pinned end-to-end against a
> real Postgres in `tests/test_generate_clips_retry_integration.py` (marker:
> `integration`).
> Test count: **375 passed, 1 skipped, 46 deselected** (+2 unit, +3 integration).

> **Closed Issue 47** (2026-05-28): Beat-job fairness on quota exhaustion. Old refresh
> task did `select(Creator)` with no ORDER BY and `break` on `QuotaExhaustedError` —
> next day's run started the same scan in the same heap order, so creators past the
> daily cutoff index never refreshed (SEV-2 starvation). Fix: added nullable
> `creators.last_analytics_refreshed_at` + `ix_creators_refresh_order` index;
> `ORDER BY last_analytics_refreshed_at NULLS FIRST, id` so newly-connected creators
> jump the queue and yesterday's starved creators go first today. Stamp set inside
> the successful inner try (commits with analytics writes); rollback on
> `QuotaExhaustedError` un-stamps by design, keeping the starved creator at the
> front. No backfill — NULL = "never refreshed" puts existing rows at the head on
> day 1, self-bootstrapping. Bundled into alembic `0004_video_done_creator_refreshed`
> per LEFT_OFF's explicit suggestion (one deploy step for both Issue 43 + 47 schema).
> Filter contract pinned via select-statement inspection (`order_by` clauses); stamp
> + no-stamp idempotency pinned via two unit tests; real-DB 5×3-cycle scenario in
> `tests/test_analytics_fairness_integration.py` (marker: `integration`).
> Test count: **373 passed, 1 skipped, 43 deselected** (+3 unit, +1 integration).

> **Closed Issue 43** (2026-05-28): Source-media purge correctness. Old filter was
> `Video.created_at < cutoff` — a stuck/in-progress ingest of an old upload would have
> its `source_uri` nulled mid-pipeline (SEV-1). Fix: added `videos.ingest_done_at`
> (nullable timestamptz) stamped exactly once in `_signals_async` under a
> `if video.ingest_done_at is None:` guard (Celery is at-least-once — retries must NOT
> refresh the stamp); swapped the purge filter to gate on
> `ingest_done_at IS NOT NULL AND ingest_done_at < cutoff`. Migration backfills
> existing `done` rows with `created_at` so already-completed videos keep their
> pre-migration retention window. Added partial index
> `ix_videos_purge_candidates ON videos(ingest_done_at) WHERE
> ingest_done_at IS NOT NULL AND source_uri IS NOT NULL` for cheap hourly sweeps.
> Filter contract pinned via SQL-whereclause inspection test;
> stamp idempotency pinned via two unit tests; real-DB three-row scenario in
> `tests/test_purge_integration.py` (marker: `integration`). `docs/COMPLIANCE.md`
> retention-clock row updated.
> Test count: **370 passed, 1 skipped, 42 deselected** (+3 unit, +1 integration).

> **Closed Issue 39** (2026-05-28 — Batch 3 kickoff): Celery event-loop strategy.
> Every task previously called `asyncio.run(...)`, creating a fresh loop per
> invocation and rebinding the SQLAlchemy async engine pool to whichever loop
> touched it first — the textbook cause of "Future attached to a different loop"
> + pool churn under concurrency. Fix: per-worker singleton `asyncio` loop installed
> by the `worker_process_init` Celery signal, and the engine rebound to that loop
> via new `db.recreate_engine()` (uses `engine.sync_engine.dispose(close=False)`
> to abandon inherited parent connections without yanking parent FDs). All 11 task
> bodies in `worker/tasks.py` now route through `worker.celery_app.run_async(coro)`.
> Switched `worker/tasks.py` from `from db import AsyncSessionLocal` to `import db`
> + `db.AsyncSessionLocal(...)` so the rebound sessionmaker is picked up at call time.
> Test count: **367 passed, 1 skipped, 41 deselected** (+5 new event-loop tests).
> Adjusted patch targets in `test_retention_tasks.py` / `test_pipeline_trigger.py` /
> `test_oauth_lifecycle.py` to match the new import surface.

> **Closed Batch 2** (2026-05-28 PM): Three TEST-ONLY issues via parallel agents.
>
> - **Issue 49**: 4 integration tests for the billing money paths (concurrent deduct
>   race, webhook idempotency same session_id, unknown pack_id, missing metadata).
>   Finding: webhook returns 200 `{"status": "ignored"}` for anomalies, NOT 4xx — this
>   is the correct Stripe pattern (2xx prevents retry storms; anomalies logged internally).
>   Tests document and assert the actual behavior.
> - **Issue 51**: 4 new tests appended to `tests/test_oauth_lifecycle.py` (now 15 total):
>   refresh-path success, callback caplog no-plaintext, authorization URL exact scopes
>   (no `youtube.upload`), `prompt=consent` + `access_type=offline` round-trip.
> - **Issue 55**: 9 surgical load-bearing tests across 8 existing files + 1 adversarial
>   YAML scenario (`loud_aftermath.yaml`).
>
> One merge-flow defect caught during Batch 2: Issue 51's new
> `test_callback_logs_no_token_plaintext` drives the full callback success path, which
> sets a `cc_session` JWT cookie on the session-scoped TestClient cookie jar — leaking
> auth into subsequent tests and causing `test_static::test_list_videos_requires_auth`
> to hit real Postgres. Fix: clear `client.cookies` in the finally block and `pop` only
> the dependency override this test set instead of `.clear()` (the project convention).
>
> Test count: **362 passed, 1 skipped, 41 deselected** (was 349; +13 unit / +4 integration).

> **Closed Batch 1** (2026-05-28 PM): Six issues landed via parallel agents in
> isolated worktrees, merged serially into main with full suite green after each merge.
>
> - **Issue 37** (SEV-1, SDK timeouts): module-level singletons for Anthropic / Stripe /
>   Voyage / boto3 with timeout + retry config. Anthropic 60s/2-retry, 120s override for
>   improvement_brief web_search path. Stripe `max_network_retries=3`. Voyage `timeout=30`
>   wrapped in tenacity (3 attempts, exp backoff). boto3 adaptive retry, max_attempts=5,
>   connect/read 10/60. Added `tenacity==9.1.4` to requirements.
> - **Issue 45** (SEV-2, refresh race + Redis pool): per-creator `SET NX EX 10` lock around
>   the Google refresh branch with canonical Lua compare-and-delete release. Module-level
>   `redis.asyncio.Redis` singleton in new `youtube/_redis.py` shared by oauth + quota.
> - **Issue 48** (TESTS): 14 new integration tests covering every protected route — zero
>   SEV-0 isolation findings (all routes correctly enforce per-creator filtering).
> - **Issue 50** (TESTS): 4 integration tests verifying cascade across all 17 dependent
>   tables; no missed FK cascades.
> - **Issue 53** (TESTS): renamed misnomered `test_compliance.py` → `test_retention_tasks.py`;
>   new `test_compliance_no_virality.py` with 3 structural scans (OpenAPI bodies, static
>   assets, schema descriptions). Codebase clean — no forbidden phrases.
> - **Issue 54** (TESTS): 3 integration tests for `scripts/rotate_token_key.py` —
>   happy-path full re-encrypt, corrupt-row rollback, caplog no-plaintext.
>
> Test count: **349 passed, 1 skipped, 37 deselected** (was 335 + 16 deselected;
> +14 unit / +21 integration). See `docs/DECISIONS.md` 2026-05-28 entries for Issues 37, 45.

> **Closed Issue 36** (2026-05-28): Three lifecycle gaps closed in one commit.
> (a) `DELETE /auth/me` now revokes the **refresh** token at
> `oauth2.googleapis.com/revoke` and tolerates 400 `invalid_token` / `token_revoked` as
> success — completes the right-to-erasure path. (b) `get_valid_access_token` now deletes
> the `YoutubeToken` row + commits on Google `invalid_grant` (RFC 6749 §5.2 permanent
> error), so subsequent refresh attempts immediately surface the existing
> "No OAuth tokens found — please reconnect" 401 instead of looping. (c) New
> `youtube/errors.py` (`YouTubeAuthError` + `PERMANENT_403_REASONS` / `TRANSIENT_403_REASONS`
> sets); `_get_json` and `_fetch_report` share a `_classify_error()` helper that retries
> transient 403/429 with exponential backoff and raises `YouTubeAuthError` on permanent
> 401 / 403 reasons (authError, forbidden, accountClosed, accountSuspended, channelClosed,
> ...). `worker/tasks.py::_refresh_youtube_analytics_async` catches `YouTubeAuthError`,
> deletes the offending `YoutubeToken` row, commits, and continues — eliminates the
> hourly-wasted-quota loop against revoked creators. "Mark creator disconnected" is
> represented as token-row absence (no `OnboardingState` enum change, no migration).
> 9 new tests in `tests/test_oauth_lifecycle.py`. Test count: **335 passed, 1 skipped,
> 16 deselected** (was 326; +9 new). See `docs/DECISIONS.md` 2026-05-28 Issue 36 entry.

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
> **2026-05-28 session note (Issue 40)**: Replaced `await file.read(max_bytes + 1)` bulk-read
> (SEV-1: up to 500 MB into heap per request) with a 1 MB streaming chunk loop. Temp file is
> always unlinked on the 413 rejection path via `except HTTPException`. 3 new tests in
> `tests/test_videos_upload_streaming.py`: 413 on oversize, tempfile cleanup verified, RSS delta
> asserted < 20 MB for a 100 MB rejected upload. Test count: **314 passed** (net +3).
> See `docs/DECISIONS.md` 2026-05-28 Issue 40 entry.

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

> **2026-05-28 session note**: Fixed SEV-0 Issue 35 — idempotent DNA build. `create_draft`,
> `embed_patterns`, `embed_brief` all gained `commit=False` path; `_build_dna_async` now
> issues a single atomic commit. 3 integration tests added in `tests/test_dna_build_idempotency.py`
> (marked `integration`; excluded from default `pytest -q` run per pytest.ini). Non-integration
> suite count unchanged at `313 passed`.

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
| 23 | VM provisioning + Cloudflare DNS + HTTPS | BETA | ✅ Done | DigitalOcean Droplet at `147.182.136.107` + Cloudflare Tunnel `autoclip-prod` + docker-compose.prod.yml; live at `autoclip.studio` |
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
| 35 | Idempotent DNA build (SEV-0) | HARDENING | ✅ Done | Single-transaction commit in `_build_dna_async`; `commit=False` param on `create_draft`, `embed_patterns`, `embed_brief`; 3 integration tests; 313 non-integration tests pass |
| 40 | Streaming upload + DoS guard | HARDENING | ✅ Done | 1 MB streaming chunk loop in upload_video; 413 + tempfile unlink on oversize; RSS delta test; 3 new tests in test_videos_upload_streaming.py; 314 tests pass |
| 44 | Auth boundary hardening — malformed sub 401, DELETE /me rate limit, MultiFernet rotation | SEC | ✅ Done | auth.py ValueError/KeyError catch; routers/auth.py 5/hour on DELETE /me; crypto.py MultiFernet + TokenDecryptError; +8 tests |
| 87 | Catalog sync wiring + 180s Shorts threshold (SEV-0 onboarding bug) | HARDENING | ✅ Done | New `sync_channel_catalog` Celery task wired into OAuth callback + Beat refresh + new `POST /me/catalog/sync` endpoint; `/videos/link` + `/videos/upload` resolve kind from real duration; `SHORTS_MAX_DURATION_S=180` configurable; 9 new tests; surfaced live on `reesepludwick@gmail.com`/"backboard media"; DECISIONS.md entry |
| 88 | DNA filter parity + business-event observability (SEV-0 logical bug) | HARDENING | ✅ Done | `rank_videos` no longer requires `ingest_status==done`; `check_data_gate` joins VideoMetrics + uses OR; `sync_channel_catalog` chains metrics fetch (no Beat wait); new `observability.log_event()` helper + diagnostic on insufficient-data raise + 7 wired surfaces; targeted audit spawned Issues 89-91; 8 new tests |
| 89 | Balance pre-check vs deduction mismatch — silent upload failures (SEV-1, spawned by Issue 88 audit) | HARDENING | 🔲 Not started | `check_positive_balance` raises only on `<= 0`; deduction needs `>= video_minutes`. Low-balance creator → upload succeeds → silent failed status with no message |
| 90 | Catalog-synced videos pollute /videos library list (SEV-2, spawned by Issue 88 audit) | HARDENING | 🔲 Not started | `list_videos` returns every Video row; catalog-only rows have no `source_uri` and will never transition out of pending. Dashboard polling loop hammers `/status` forever |
| 91 | "Clips ready" dashboard counter ignores render_status (SEV-2, spawned by Issue 88 audit) | HARDENING | 🔲 Not started | Counter shows total clips regardless of render; reviewer can only play rendered clips. User clicks into "12 ready", scrolls past 12 placeholders |

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
