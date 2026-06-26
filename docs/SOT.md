# CreatorClip — Source of Truth

**Last updated**: 2026-05-25
**Conflicts with PRD.md**: this file wins — log divergence in `docs/DECISIONS.md`.

This describes how CreatorClip **is built**. Update on every architectural change.

---

## Tech Stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| Backend | FastAPI (Python 3.12+) | Async-first |
| Task queue | Celery + Redis | Durable video jobs: ingest → transcribe → signals → DNA → clip → render |
| LLM | Anthropic SDK; per-task model registry (Issue 318): **`claude-sonnet-4-6`** for reasoning/streaming tasks (DNA brief, titles, thumbnails, scoring, analysis, improvement, chat, intake); **`claude-haiku-4-5`** for cheap classify tasks (hooks, chapters, performer analysis); **no Opus** — see docs/DECISIONS.md (Issue 221). Each task has an independently overridable `ANTHROPIC_MODEL_<TASK>` env var in config.py. Sonnet 4.6 cacheable-prefix floor = 1024 tokens; Haiku 4.5 = 4096 tokens (confirmed 2026-06-26). | Prompt caching on DNA profile + evergreen corpus **mandatory**; web-search tool for live research |
| Embeddings | Voyage AI (`voyage-3.5`) → pgvector | Local sentence-transformers as offline fallback |
| Transcription | Deepgram nova-3 (default, `TRANSCRIPTION_BACKEND=deepgram`) | WhisperX (faster-whisper + forced alignment) available as self-hosted opt-in; AssemblyAI also supported; all selected via `TRANSCRIPTION_BACKEND` config. MIP opt-out (`mip_opt_out=True`) enforced on every Deepgram call (Issue 251). |
| Audio analysis | librosa (RMS energy) | Energy, silence, volume spikes, laughter/applause heuristic. Loudness normalization is ffmpeg `loudnorm` (two-pass, −14 LUFS) at render time, not analysis (Issue 181). |
| Vision (Phase 2) | MediaPipe / face-emotion model | Deferred |
| DB | PostgreSQL 16 + pgvector | Relational + embeddings in one store |
| Session / queue broker | Redis 7 | Celery broker + short-lived caches |
| Object storage | Cloudflare R2 (S3-compatible) | Source video + rendered clips; local disk in dev; retention purge |
| Video processing | ffmpeg | Cut + 9:16 active-speaker reframe |
| YouTube | YouTube Analytics API + Data API v3 (OAuth 2.0) | Retention curves, demographics, activity windows, metadata, captions |
| Auth | Google OAuth 2.0 (YouTube scopes) + server-side session JWT | PyJWT; bcrypt where local creds needed |
| Token encryption at rest | `cryptography` MultiFernet on token columns | Primary key from `TOKEN_ENCRYPTION_KEY`; optional previous key for zero-downtime rotation |
| Preference model | LightGBM (or logistic regression) reranker | Recency-decayed sample weights; retrained per session |
| Frontend | **React + TypeScript (Vite, Tailwind v4, shadcn-style)** — strangler-fig migration from the legacy vanilla UI is COMPLETE (legacy app pages retired, Issue 226). Data layer **TanStack Query v5**; routing **React Router v7 Data Mode**; tests **Vitest + React Testing Library** (unit/component) and **Playwright** (E2E/visual harness, `frontend/e2e/`, backend mocked — Issue 162). | Framework resolved 2026-06-17; foundation + design system 2026-06-18 (Issue 85a, DECISIONS.md). SPA served under `/app/*`; the legacy `static/` app pages have been RETIRED (Issue 226). Layouts = `AuthGate` (protects routes) + `AppChrome` (Nav/Footer shell); four route contexts (protected/public × chrome/bare). Ported: Dashboard (`/app/dashboard`, live status via gated TanStack refetch — Issue 85c), Onboarding (`/app/onboarding`, protected+bare 5-step flow w/ dual SSE consoles — Issue 85d), Insights + Analysis (`/app/insights`, `/app/analysis` — LLM-streaming via new `useTaskResult` hook — Issue 85e), Review/Editor (`/app/review` — player-first redesign + transcript editor — Issue 85f), Profile, Chat, Pricing (public-or-authed), Login, Walkthrough. **Cutover COMPLETE: `/` redirects to `/app/dashboard` (`main.py` `_SPA_BUILT` gate). The legacy vanilla app pages were retired (Issue 226) and backend `next_action` URLs repointed `/static/*.html` → `/app/*`. Only `tos`/`privacy`/`accessibility` HTML + shared/legacy CSS/JS remain under `/static`.** Build: `npm --prefix frontend run build` → `frontend/dist/`. Design system in `docs/UI.md` (warmer OKLCH dark-Linear palette in the SPA `@theme`); legacy pages keep `static/_design-tokens.css`. |
| Transactional email | Resend (Python SDK v2.32.2) | `NOTIFY_BACKEND=console` in dev/CI (logs only); `NOTIFY_BACKEND=resend` in production. Jinja2 paired `.txt`/`.html` templates in `notify/templates/`. Native idempotency-key API maps onto Celery at-least-once retry. SPF/2048-bit DKIM/DMARC DNS runbook in `docs/RUNBOOKS.md`. Issue 242. |
| Containerization | Docker Compose (dev) | `app`, `worker`, `beat`, `postgres`, `redis`. Beta prod (`docker-compose.prod.yml`) adds `cloudflared` (tunnel, no host port) + `autoheal` (restart-on-unhealthy) + app/worker healthchecks |
| Production deployment | Kubernetes — **chart written, GKE deploy unvalidated** | Architecture locked (DECISIONS): **GKE Autopilot + Cloud SQL PG16 + KEDA + External Secrets**; Helm chart at `deploy/charts/creatorclip/` (rolling-update + probes, KEDA-on-Redis-depth, PgBouncer sidecar). It has **never run on K8s** — "staging" is still Docker-Compose on the prod VM, so the scale/pool `[DEC]`s are unverified. **Issue 275** (GKE staging + first Helm deploy) is the linchpin; gaps tracked as Issues 275–280 (Lane L12). Docker Compose = dev/test only. See `docs/DEPLOYMENT.md` + `docs/issues.md`. |

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key |
| `VOYAGE_API_KEY` | Yes (unless local embeddings) | Voyage AI embeddings key |
| `DATABASE_URL` | Yes | `postgresql+psycopg://user:pass@host:5432/creatorclip` |
| `REDIS_URL` | Yes | `redis://localhost:6379/0` — Celery broker + cache |
| `GOOGLE_OAUTH_CLIENT_ID` | Yes | Google OAuth client ID |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Yes | Google OAuth client secret |
| `OAUTH_REDIRECT_URI` | Yes | OAuth callback URL |
| `TOKEN_ENCRYPTION_KEY` | Yes | Primary Fernet key for YouTube token columns; rotation runbook in `docs/RUNBOOKS.md` |
| `TOKEN_ENCRYPTION_KEY_PREVIOUS` | No | Previous Fernet key; set during zero-downtime rotation so old tokens remain readable. Clear after `scripts/rotate_token_key.py` completes. |
| `JWT_SECRET_KEY` | Yes | Session JWT secret (32-byte random) |
| `ALLOWED_ORIGINS` | Yes | Comma-separated list. Lock to production domain; never `*` in prod |
| `JWT_EXPIRY_MINUTES` | No | Default `60` |
| `TRANSCRIPTION_BACKEND` | No | `whisperx` (default) \| `deepgram` \| `assemblyai` |
| `DEEPGRAM_API_KEY` / `ASSEMBLYAI_API_KEY` | Conditional | Required if hosted transcription backend selected |
| `WHISPER_MODEL` | No | Default `large-v3` |
| `STORAGE_BACKEND` | No | `r2` (production) \| `local` (dev) |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` | Conditional | Required if `STORAGE_BACKEND=r2` |
| `SOURCE_MEDIA_RETENTION_HOURS` | No | Default `72`; source video purge timer |
| `CLIPS_PER_VIDEO_DEFAULT` | No | Default `8` |
| `MIN_VIDEOS_FOR_DNA` | No | Default `10` |
| `MIN_SHORTS_FOR_DNA` | No | Default `5` |
| `SHORTS_MAX_DURATION_S` | No | Default `180`. YouTube's official Shorts maximum (raised from 60s in Oct 2024). Issue 87. |
| `PERSONALIZATION_THRESHOLD_LABELS` | No | Default `20` |
| `LLM_TIMEOUT_SECONDS` | No | Default `120` |
| `ACTIVE_SPEAKER_REFRAME_ENABLED` | No | Default `False`. Gates the per-frame MediaPipe reframe path in render.py (Issue 189). Keep False until render-env smoke test passes. |
| `REFRAME_SAMPLE_FPS` | No | Default `5.0`. Frames/second to sample for face detection in the per-frame reframe path. Ignored when `ACTIVE_SPEAKER_REFRAME_ENABLED=false`. |
| `ENV` | No | `development` \| `production`; gates `/docs`, error verbosity |
| `ALLOWED_ORIGINS` | Yes (prod) | Comma-separated origins; never `*` in production |
| `CLOUDFLARE_TUNNEL_TOKEN` | Yes (prod) | Token for the `cloudflared` service in `docker-compose.prod.yml`; routes `autoclip.studio` → `app:8000` with no open inbound ports |

---

## File Structure

```
/                               # project root
├── CLAUDE.md
├── .env / .env.example
├── requirements.txt
├── requirements-dev.txt        # assessment/dev tooling: mypy, pytest-cov, bandit, pip-audit, mutmut, locust
├── pytest.ini
├── docker-compose.yml
├── Dockerfile
│
├── .claude/skills/production-assessment/  # /assess harness (Layer 0 gates + per-module rubric + scale checklist)
├── .github/workflows/quality.yml          # ratcheted CI gates (types/coverage/SAST/CVEs)
├── docs/assessment/            # production-readiness register: baselines + per-module findings + report history
├── tests/perf/                 # Locust load-test scaffold (concurrency evidence)
│
├── main.py                     # FastAPI entrypoint, /health, /metrics (Issue 75f)
├── config.py                   # Pydantic Settings; fail-fast on missing required
├── db.py                       # SQLAlchemy async engine + session (Issue 2)
├── auth.py                     # Google OAuth + session JWT; get_current_creator (Issue 3)
├── crypto.py                   # Fernet helpers for token columns
├── observability.py            # Correlation id (ContextVar+ASGI mw), JSON logs, Prometheus golden signals; API→Celery propagation (Issue 75f)
├── event_log.py                # Beta telemetry sink → event_logs table (Issue 151). Isolated engine (LOGS_DATABASE_URL), boundary PII/token redaction, best-effort writes
│   # NOTE: there is no central clients.py. External API clients are MODULE-LEVEL
│   # singletons in the modules that use them (Issue 37 lifecycle rule): Anthropic in
│   # dna/brief.py, clip_engine/scoring.py, chat/runner.py, chat/intake.py, knowledge/*,
│   # analysis/brief.py, improvement/brief.py, routers/insights.py; Voyage in dna/embeddings.py;
│   # YouTube + storage clients constructed where used. Each sets timeout + max_retries.
│
├── youtube/
│   ├── oauth.py                # OAuth flow, token storage/refresh (encrypted)
│   ├── analytics.py            # Retention curves, demographics, activity windows
│   ├── data_api.py             # Video metadata, captions
│   ├── categories.py           # Static YouTube category enum (Issue 83 intake niches)
│   └── ingest.py               # Source acquisition (upload / yt-dlp guard), normalize
│
├── ingestion/
│   ├── transcribe.py           # WhisperX or hosted; word-level segments
│   ├── audio.py                # Energy, silence, laughter, volume spikes; generate_waveform_image (ffmpeg showwavespic, Issue 188)
│   ├── vision.py               # (Phase 2) facial expression / scene detection
│   └── signals.py              # Unified multimodal signal timeline
│
├── dna/
│   ├── builder.py              # Research Mode: top/bottom analysis, pattern extraction
│   ├── profile.py              # CreatorDNA inferred profile CRUD (versioned)
│   ├── identity.py             # Creator STATED identity CRUD (Issue 83; append-only)
│   ├── conflict.py             # stated-vs-inferred mismatch detector (Issue 83)
│   ├── brief.py                # Plain-language creator brief generation (Claude) — fuses identity
│   └── embeddings.py           # Profile + clip embeddings → pgvector
│
├── clip_engine/
│   ├── window.py               # Rolling 60–90s context window
│   ├── candidates.py           # Peak detection + backward look for setup start
│   ├── scoring.py              # Multi-signal + DNA-weighted scoring (Claude + features)
│   ├── ranking.py              # DNA-weighted + preference-model rerank
│   ├── render.py               # ffmpeg cut + 9:16 active-speaker reframe + ASS burn-in + clean-pass filter_complex; flag-gated per-frame reframe path (Issue 189, ACTIVE_SPEAKER_REFRAME_ENABLED)
│   ├── reframe.py              # (NEW Issue 189) per-frame MediaPipe BlazeFace face tracking → EMA-smoothed crop-center timeline → ffmpeg sendcmd script; lazy import; gated by ACTIVE_SPEAKER_REFRAME_ENABLED (default False — render-env pending)
│   ├── captions.py             # Animated word-level ASS subtitles (Issue 133 — bold_pop / gradient_slide / minimal via pysubs2 + libass)
│   ├── filler.py               # Filler-word + silence cut-list generator (Issue 134 — Tier1 unconditional + Tier2 pause-flanked + 800ms silence w/150ms tail)
│   └── edits.py                # User-supplied cut-list validator (Issue 135 — bounds, overlap, 5s/85% caps, sub-frame floor) for text-based editor
│
│   # static/editor-layout.css + static/hero.css added in Issue 136 (dark editor layout + pre-auth hero)
│   # static/page-shell.css added in Issue 137 (project-wide aurora + soft-card shell + overflow-x: clip guard)
│   # static/components.css added in Issue 147 (shared component layer on tokens: .eyebrow/.stat-cell/.status-pill/.callout/…; full per-template migration → Issue 148)
│
├── preference/
│   ├── model.py                # Learned reranker (online update)
│   ├── features.py             # Feature vector per clip
│   ├── decay.py                # Exponential recency decay weighting
│   └── train.py                # Update loop from feedback
│
├── knowledge/
│   ├── rag.py                  # Evergreen RAG retrieval (pgvector)
│   ├── research.py             # Live web search (Claude web-search tool)
│   ├── thumbnails.py           # Thumbnail pattern analysis + concept generation (Issue 129)
│   ├── titles.py               # Title suggestion generation (Issue 128)
│   ├── hooks.py                # First-30s hook analysis vs retention curves (Issue 130)
│   ├── chapters.py             # Auto chapter marker generation from transcript (Issue 131)
│   ├── clip_titles.py          # Per-clip Short-title + hook-rewrite generator (Issue 322)
│   ├── clip_captions.py        # Per-clip caption-hook / thumbnail overlay-text (Issue 323)
│   ├── clip_explain.py         # Per-clip Why-This-Clip narrative, cites CLIPPING_PRINCIPLES (Issue 325)
│   ├── util.py                 # Shared transcript extraction helpers
│   └── seed/                   # Evergreen corpus: hook psychology, pacing, retention theory
│
├── upload_intel/
│   └── timing.py               # Best upload window + optimal gap from analytics
│
├── analysis/
│   └── brief.py                # Video performance analysis (Claude streaming, Issue 121)
│
├── improvement/
│   └── brief.py                # Content-improvement brief generation
│
├── chat/                       # Pro chatbot (Issue 152)
│   ├── prompt.py               # Cached, honesty-constrained system prompt
│   ├── tools.py                # 8 creator-scoped tools (DNA/videos/perf/averages/timing + clips/outcomes/title-gen) — every query filtered by creator_id (Issue 324)
│   └── runner.py               # Manual agentic streaming loop (stream → tool_use → execute → loop), iteration/token capped
│
├── routers/
│   ├── activity.py             # POST /api/activity — browser UI events → app.log + event_logs (Issue 122/151)
│   ├── logs.py                 # GET /api/logs/me — creator's own event_logs rows, app-level isolation (Issue 151)
│   ├── chat.py                 # /api/chat/* — Pro chatbot: gated+quota'd message → SSE stream, list/get/regenerate (Issue 152)
│   ├── auth.py                 # OAuth login/callback, session
│   ├── creators.py             # Creator profile, DNA, onboarding state
│   ├── videos.py               # Link/upload video, ingestion status
│   ├── clips.py                # List candidate clips, get clip, render status; POST /clips/{id}/title-suggestions, /caption-hooks, /explanation (Issues 322/323/325)
│   ├── review.py               # Feedback: upvote/downvote/skip/trim/format
│   ├── upload_intel.py         # GET timing recommendation
│   ├── improvement.py          # GET improvement brief
│   ├── analysis.py             # POST video-analysis (Issue 121) + hook-analysis (Issue 130) + chapters (Issue 131)
│   ├── thumbnails.py           # GET thumbnail-patterns + POST thumbnail-concepts (Issue 129)
│   ├── titles.py               # POST video title suggestions (Issue 128)
│   ├── publications.py         # Scheduled publish: POST/GET/confirm/cancel ClipPublication (Issue 196)
│   ├── notifications.py        # /api/notifications (list/dismiss/preferences) + no-auth GET /unsubscribe/{token} (Issue 245)
│   └── tasks.py                # SSE live-progress endpoint (Issue 86)
│
├── notify/                     # Transactional email + notification helpers (Issues 242-243)
│   ├── __init__.py
│   ├── mailer.py               # send(to, template, context, idempotency_key); NOTIFY_BACKEND dispatch
│   ├── dedupe.py               # make_dedupe_key(creator_id, event_type, entity_id) → sha256 hex (Issue 243)
│   └── templates/              # Jinja2 paired .txt + .html per email type
│       ├── clips_ready.txt     # Placeholder — populated by Issues 243+
│       └── clips_ready.html
│
├── worker/
│   ├── celery_app.py           # Celery + Redis broker
│   ├── tasks.py                # Pipeline tasks (ingest → render)
│   ├── schedule.py             # Beat: profile refresh, token refresh, media purge
│   ├── progress.py             # Issue 86 — per-task Redis Stream emit/read + SSE slot cap + ownership
│   └── anthropic_stream.py     # Issue 86 — wraps Anthropic .stream() so tokens flow into progress events; stream_message() returns full final message for the chat tool loop (Issue 152)
│
├── static/                     # Legacy vanilla app pages RETIRED (Issue 226). The React SPA
│   │                           # under /app/* is the only UI; /static still serves these:
│   ├── tos.html                # Terms of Service (footer-linked; Google OAuth verification gate)
│   ├── privacy.html            # Privacy Policy (COPPA/children's-privacy section, Issue 300)
│   ├── accessibility.html      # Accessibility statement
│   └── *.css / *.js            # shared/legacy assets still served at /static/* (page-shell.css,
│                               #   components.css, _design-tokens.css, editor-layout.css, hero.css;
│                               #   progressStream.js, activeTasks.js, activity.js, auth.js, editor.js,
│                               #   tooltip.js, util.js) — some orphaned post-retirement; pending an
│                               #   asset-cleanup pass. (Former app pages index/onboarding/review/
│                               #   profile/insights/analysis .html are deleted.)
│
├── frontend/                   # React + TS SPA (2026-06-17 adoption; served under /app/*)
│   ├── index.html              # Vite entry shell
│   ├── vite.config.ts          # base=/app/, React + Tailwind v4 plugins, @ alias, dev API proxy, vitest (jsdom; include=src/, exclude e2e/)
│   ├── package.json            # scripts: dev / build / lint / test (vitest) / test:e2e (playwright); deps incl. @tanstack/react-query
│   ├── playwright.config.ts    # E2E/visual harness (Issue 162): desktop 1440 + mobile 390 Chromium, Vite webServer, baseURL /app/
│   ├── e2e/                    # Playwright: smoke.spec.ts (every route × 2 viewports, console/JS-error asserts) · fixtures/mock-api.ts (backend mocked via page.route; authed/anon seeds) · __screenshots__/ (gitignored audit captures)
│   ├── dist/                   # build output (gitignored) — `npm --prefix frontend run build`
│   └── src/
│       ├── main.tsx            # QueryClientProvider → App
│       ├── App.tsx             # React Router v7 Data Mode (createBrowserRouter, basename /app)
│       ├── index.css           # Tailwind v4 @theme — Issue 85 design system (warmer OKLCH; docs/UI.md)
│       ├── types.ts            # API response shapes
│       ├── test/setup.ts       # Vitest + RTL setup (jest-dom matchers, cleanup)
│       ├── lib/                # api.ts (typed fetch) · queryClient.ts · brief.ts (+test) · taskStream.ts (SSE) · utils.ts
│       ├── hooks/              # useAuth.ts (TanStack Query; 401→null) · useTaskStream.ts (SSE log hook +test) · useTaskResult.ts (token/step/done-payload SSE hook, Issue 85e) · useStreamAction.ts (POST→stream helper, 85e) · useCleanedUriPoll.ts (clean/edit ready-poll, 85f)
│       ├── components/         # AuthGate.tsx (+test, protects routes) · AppChrome.tsx (Nav/Footer shell) · Nav.tsx (+test; Editor+Settings links, Issue 304) · Footer.tsx · DisclaimerBand.tsx · Chip.tsx (+test — decorative mascot, Issue 304)
│       ├── components/chip/    # poses.ts (CHIP_POSES registry + ChipPose) · ChipStates.tsx (8 loading/thinking animations — Issue 304); sprites in public/chip/
│       ├── components/ui/      # shadcn-style primitives: button / card / badge / modal
│       ├── components/profile/ # DnaCard · Brief · IdentitySection · IntakeModeSection · ApiKeysSection
│       ├── components/dashboard/ # AnalyticsPanel (panel|sidebar variants) · UploadVideoForm (inline file upload, Issue 317; replaced LinkVideoForm) · VideoTable (Video·Status·Clips·Actions) · EmptyHero · DashboardBanners · StageStepper (Issue 85c; videos-first reorg + SummaryCards removed, Issue 305)
│       ├── components/onboarding/ # StepCard · StreamConsole · OnboardingIdentity (Issue 85d)
│       ├── components/insights/ # InsightsPanel · ChannelSnapshot/DnaSnapshot · PerformerPanel · UploadWindows · ImprovementBrief · SavedInsights (Issue 85e)
│       ├── components/analysis/ # AnalysisPanel (StatusChip/CopyButton) · AnalysisQuery · TitleOptimizer · HookAnalyzer · ChaptersPanel · ThumbnailConcepts (Issue 85e)
│       ├── components/editor/   # Timeline (+test) — waveform + synced playhead + cut overlays (Issue 188) · LongFormEditor (master candidate timeline + suggested clips + chapters + export — Issue 307)
│       ├── components/review/   # ClipPlayer (player+filmstrip, Issue 306) · TrimFilmstrip (+test) + trim.ts (dual-handle trim) · YourCall (triage card) · WhyThisClip · CaptionStylePanel · CleanPassPanel · TranscriptEditor · CollapsibleTool (plain/ReactNode title) (Issue 85f/306)
│       └── pages/              # Dashboard (+test, 85c; videos-first reorg 305) · Onboarding (+test, 85d) · Insights (+test, 85e; chip-idea 309) · Analysis (+test, 85e; chip-magnify 309) · Review (+test, 85f; filmstrip trim + Your-call card + Chips, 306) · Editor (+test, 188; short|long mode toggle + long-form source, 307) · Profile (+test; read-only snapshot, 308) · Settings (+test; full build 308) · Chat (chip-wave/think/streaming, 309) · Pricing (+test) · Login · Walkthrough (+test)
│
├── tests/
│   ├── conftest.py
│   ├── test_health.py
│   ├── test_auth.py
│   ├── test_analytics.py
│   ├── test_ingest.py
│   ├── test_signals.py
│   ├── test_dna.py
│   ├── test_clip_engine.py     # Includes the "setup not aftermath" assertion
│   ├── test_scoring.py
│   ├── test_preference.py      # Recency decay actually reweights
│   ├── test_review.py
│   ├── test_upload_intel.py
│   ├── test_model_config.py    # Issue 318 — per-task model key registry + literal ban (always runs)
│   ├── test_llm_live.py        # Issue 319 — flag-gated live API tests (requires RUN_LLM_LIVE=1; mark: llm_live)
│   ├── test_llm_conformance.py # Issue 320 — SDK conformance: singleton/timeout, typed exceptions, cache floors
│   ├── test_usage_coverage.py  # Issue 321 — usage ledger coverage guard (all LLM tasks call record_llm_usage)
│   ├── test_brief_quota.py     # Issue 321 — per-creator brief daily quota (BRIEF_DAILY_LIMIT_PER_CREATOR)
│   └── eval/                   # Clip-quality eval: labeled videos + expected clip windows
│       └── scenarios/*.yaml
│
├── scripts/
│   ├── doctor.py               # Preflight secrets validator (presence/format/live, redacted) — deploy gate
│   ├── rotate_token_key.py     # TOKEN_ENCRYPTION_KEY re-encryption (see docs/RUNBOOKS.md)
│   └── llm_e2e.py              # Live-API LLM verification harness (Issue 319); RUN_LLM_LIVE=1 guard
│
└── docs/
    ├── README.md              # ← START HERE: full documentation index (Issue 146)
    ├── UI.md                   # Frontend design system (Issue 85; SPA tokens, type, motion, confidence badges)
    ├── PRD.md
    ├── SOT.md                  # (this file)
    ├── DECISIONS.md
    ├── PROJECT_STATE.md
    ├── issues.md
    ├── CLIPPING_PRINCIPLES.md
    ├── COMPLIANCE.md
    ├── OFF_COURSE_BUGS.md      # Incidental-defect log
    ├── DEPLOYMENT.md
    ├── BRANCHING.md            # Branch model (feature→staging→main) + protection ruleset (Issue 145)
    ├── RUNBOOKS.md             # Canonical encryption/JWT-key rotation procedures
    ├── SECRETS.md              # Canonical secrets/config registry (what, where, how-to-obtain)
    ├── ACCESS.md               # SSH + CI deploy key + Cloudflare Tunnel + closed-beta OAuth onboarding
    ├── STAGING_ACCESS.md       # Staging stack runbook + llm_harness E2E driver
    ├── SKILL_FRESHNESS.md      # Skill-freshness convention + --require-fresh gate
    ├── COMPETITIVE_RESEARCH.md # Market/pricing/UX analysis (was other_apps_research.md)
    ├── research/               # Gap-closure research-agent prompts (Issues 166–180; see research/README.md)
    └── archive/                # Superseded docs, preserved for provenance (Issue 146)
```

---

## Data Model

```sql
creators
  id, google_sub (unique), channel_id, channel_title, email,
  onboarding_state (connected/awaiting_data/dna_pending/active),
  analysis_mode (auto/selective/manual; default auto — Issue 125),
  trial_ends_at (TIMESTAMPTZ NULL — set on first OAuth login, Issue 126),
  terms_accepted_at (TIMESTAMPTZ NULL — clickwrap acceptance timestamp, Issue 299),
  terms_version (VARCHAR 32 NULL — ToS version shown at acceptance, Issue 299),
  privacy_version (VARCHAR 32 NULL — Privacy Policy version shown at acceptance, Issue 299),
  minimum_age_confirmed_at (TIMESTAMPTZ NULL — COPPA 13+ attestation timestamp, Issue 300),
  plan_tier, subscription_status, created_at

youtube_tokens
  creator_id (FK), access_token_encrypted, refresh_token_encrypted,
  scope, expires_at, updated_at

videos
  id, creator_id (FK), youtube_video_id (NULLABLE since Issue 317), title, kind (long/short),
  published_at, duration_s, source_uri, origin (catalog/link/upload),
  captions_available, ingest_status (pending/running/done/failed), created_at
  -- origin is the canonical provenance discriminator (Issue 139):
  --   catalog = DNA/analytics reference from sync_video_catalog (no media,
  --     hidden from /videos so the dashboard never shows "pending forever").
  --   link    = registered by ID via POST /videos/link (no media — we never
  --     download from YouTube per ToS; shown with clippable=false, the
  --     creator uploads the source file to clip). NOTE (Issue 317): the
  --     paste-a-URL UI is retired in favour of file upload; the /videos/link
  --     endpoint is retained only for catalog-row adoption (→ Issue 310).
  --   upload  = carries source_uri (stored media); the only clip-trackable path.
  --     POST /videos/upload: youtube_video_id is OPTIONAL (Issue 317) — a
  --     standalone raw upload (no published video) leaves it NULL; the
  --     (creator_id, youtube_video_id) unique constraint still holds (PG NULLs
  --     are distinct). Storage key uses a uuid4 token when the id is absent.
  -- /videos filters `origin != catalog`. source_uri now means strictly
  --   "has stored media" (used by ingest + the stale-media purge), no longer
  --   doubling as the catalog discriminator. (Issue 139 supersedes Issue 90.)

video_metrics
  video_id (FK), views, watch_time_s, avg_view_duration_s,
  engagement_rate, fetched_at

retention_curves
  video_id (FK), timestamp_s, audience_watch_ratio,
  relative_retention_performance, is_rewatch_spike

audience_activity
  creator_id (FK), day_of_week, hour, activity_index, fetched_at

demographics
  creator_id (FK), payload_jsonb, fetched_at

transcripts
  video_id (FK), source (whisperx/captions), segments_jsonb (word-level)

signals
  video_id (FK), timeline_jsonb (audio energy, silence, laughter, retention spikes)

creator_dna                          -- the inferred profile (versioned)
  id, creator_id (FK), version, brief_text, patterns_jsonb,
  top_video_ids_jsonb, bottom_video_ids_jsonb,
  optimal_clip_len_s, best_source_region, optimal_upload_gap_h,
  status (draft/confirmed/superseded), created_at

creator_identity                     -- the STATED profile (Issue 83, append-only)
  id, creator_id (FK, CASCADE), version,
  niches (JSONB array of YouTube category IDs), audience_summary,
  content_pillars (JSONB), tone_tags (JSONB), hard_nos (JSONB),
  mission, style_sample,
  created_at, superseded_at (NULL = current; partial UNIQUE enforces ≤1 current)

creator_style                        -- brand-kit render defaults (Issue 186, one row per creator)
  id, creator_id (FK, CASCADE, UNIQUE uq_creator_style_creator_id),
  style JSONB (subtitle, background, captions_enabled, zoom_on_peak, denoise, aspect),
  updated_at
  RLS: tenant_isolation on creator_id

dna_embeddings
  id, creator_id (FK), kind (pattern/clip/hook), embedding (vector), ref_jsonb

clips
  id, video_id (FK), creator_id (FK), setup_start_s, start_s, end_s, peak_s,
  score, dna_match, signals_jsonb, format (short/horizontal),
  render_uri, render_status, rank, created_at

clip_feedback
  id, clip_id (FK), creator_id (FK),
  action (upvote/downvote/skip/trim/format),
  trim_start_s, trim_end_s, chosen_format, created_at

clip_outcomes                        -- strongest positive signal
  clip_id (FK), published_youtube_id, views, retention,
  performed_well (bool), fetched_at

preference_models
  creator_id (FK), version, weights_blob, feature_schema_jsonb, updated_at

improvement_briefs                    -- async 202 + poll brief (Issue 78d)
  id, creator_id (FK, indexed, one row/creator),
  status (pending|ready|failed), brief_text, error (safe msg only),
  job_id (Celery idempotency), requested_at, completed_at

minute_deductions                     -- cost-side ledger (Issue 34)
  id, video_id (FK, UNIQUE — idempotency key), creator_id (FK),
  minutes_deducted, duration_s, deducted_at

usage
  creator_id (FK), period, videos_processed, clips_generated,
  tokens_in, tokens_out

audit_log
  id, at, actor, action, entity_type, entity_id, before_jsonb, after_jsonb

-- Issues 113-119 additions --

creator_insights                      -- AI per-performer + channel insights (Issue 117)
  id, creator_id (FK), video_id (FK nullable), insight_type (performer_analysis|trend|recommendation),
  title, content, dna_version, is_saved, created_at
  -- Cached per (video_id, dna_version); creator can bookmark via is_saved

-- clip_feedback additions (Issue 118) --
  feedback_tags: JSONB | None         -- list of tag strings e.g. ["good_hook", "right_length"]
  feedback_note: Text | None          -- free-text "Other" field

-- clips additions (Issue 119) --
  style_preset: JSONB | None          -- {subtitle, background, captions_enabled}

chat_conversations                    -- Pro chatbot threads (Issue 152)
  id, creator_id (FK), title, created_at, updated_at
  -- RLS tenant_isolation policy (migration 0026, mirrors 0010) + app-layer filter

chat_messages                         -- one user/assistant turn (Issue 152)
  id, conversation_id (FK), role (user|assistant), content,
  tokens_in, tokens_out, cache_read (assistant rows only — per-message cost log), created_at
  -- reaches tenant via conversation FK (child-table pattern; no own RLS policy)

notification_preferences              -- per-creator consent + channel opt-out (Issue 243)
  creator_id (PK, FK), email_transactional bool default true,
  email_lifecycle bool default true,   -- unsubscribable (welcome/nudge/re-engagement)
  inapp_enabled bool default true, push_enabled bool default false,
  unsubscribe_token (uuid, unique),    -- one-click unsubscribe link, no auth required
  updated_at
  -- No RLS (PK = creator_id; single-row-per-creator, no cross-tenant read possible)
  -- email_transactional is always-on (CAN-SPAM / GDPR Art. 6(1)(b)); UI locks toggle

notification_deliveries               -- idempotency ledger; Inbox pattern (Issue 243)
  id, creator_id (FK), event_type, entity_id, channel (email|inapp|push),
  dedupe_key (UNIQUE),                 -- sha256(creator_id:event_type:entity_id)
  provider_message_id,                 -- Resend opaque id, no PII
  status (sent|skipped|failed), created_at
  -- No RLS (internal audit table, not exposed via creator-facing API)

notifications                         -- durable in-app notification center (Issue 243, Issue 81)
  id, creator_id (FK), kind, title, body, link_url,
  seen_at (NULL = unread), dismissed_at, created_at
  -- RLS tenant_isolation policy (ENABLE + FORCE), migration 0031 — mirrors chat_conversations

clip_publications                     -- YouTube publish attempts + scheduled publishes (Issues 195/196)
  id, clip_id (FK CASCADE), creator_id (FK CASCADE, RLS tenant_isolation)
  task_id (nullable, UNIQUE — Celery task id; assigned by Beat sweep or direct enqueue; idempotency guard)
  youtube_video_id, status (publish_status_enum), error
  scheduled_at (TIMESTAMPTZ nullable — target publish time; Beat sweep selects WHERE <= now() AND status=confirmed)
  platform (publish_platform_enum, default youtube)
  confirmed_at (TIMESTAMPTZ nullable — when creator confirmed the schedule)
  created_at, updated_at
  -- Status lifecycle: scheduled → confirmed → pending → running → done|failed
  -- cancel sets status=failed, error='Cancelled by creator' (audit-trail preservation)
```

---

## Processing Pipeline (Celery)

```
creator links/uploads a video
             │
             ▼
      ┌─────────────┐
      │   Ingest    │  acquire source, normalize, store to R2
      └──────┬──────┘
             ▼
      ┌─────────────┐
      │ Transcribe  │  WhisperX word-level (or captions / hosted fallback)
      └──────┬──────┘
             ▼
      ┌─────────────┐
      │  Signals    │  audio energy/silence/laughter + retention spikes → timeline
      └──────┬──────┘
             ▼
      ┌─────────────┐
      │ Candidates  │  detect peaks → look BACKWARDS 60–90s → setup start
      └──────┬──────┘
             ▼
      ┌─────────────┐
      │    Score    │  features + Claude DNA-fit judgment (cached on DNA profile)
      └──────┬──────┘
             ▼
      ┌─────────────┐
      │    Rank     │  DNA-weighted + per-creator preference reranker
      └──────┬──────┘
             ▼
      ┌─────────────┐
      │   Render    │  ffmpeg cut + 9:16 active-speaker reframe → R2
      └──────┬──────┘
             ▼
      candidate clips ready for Review UI
             │
             ▼  (creator feedback)
      ┌─────────────┐
      │  Preference │  feedback → recency-decayed reranker update
      │ Update Loop │
      └─────────────┘

Research Mode (parallel):
  catalog metrics + retention curves → top/bottom analysis
  → pattern extraction → Claude synthesis → Creator Brief
  → creator confirms → living DNA profile + embeddings
```

---

## Security & Compliance Posture

- **YouTube API Services ToS is a hard constraint.** See `docs/COMPLIANCE.md`.
- **Source-acquisition compliance:** creator-initiated only. `yt-dlp` off by default, never on third-party channels.
- **OAuth tokens encrypted at rest** (Fernet); never logged; refreshed via standard flow.
- **Per-creator data isolation** enforced at the query layer; tests assert no cross-creator leakage.
- **PII minimization:** store only what features need; demographics aggregated.
- **Source media purged** on `SOURCE_MEDIA_RETENTION_HOURS` timer.
- **TLS in transit** via Cloudflare Tunnel; secrets in env only, never committed.
- **`ALLOWED_ORIGINS` locked** to production domain; `/docs` disabled in production.
- **Honesty enforcement:** no interface or response promises virality; structural test verifies disclaimer text.

---

## Known Production Gaps

- **Transcription compute**: hosted Deepgram is the current default (no GPU needed at launch); WhisperX self-host is selectable via config. The measured self-host-vs-hosted break-even + R2 storage-cost monitoring is **Issue 293**.
- **Production deployment**: Docker Compose is dev-only. Kubernetes architecture is **chosen and the Helm chart is written** (`deploy/charts/creatorclip/`: GKE Autopilot + Cloud SQL PG16 + KEDA) but has **never run on a real cluster** — closing that is **Issue 275** (GKE staging + first Helm deploy), with the rest of the deploy gaps as Issues 275–280, 287 (Lane L12). See `docs/DEPLOYMENT.md` + `docs/issues.md`.
- **Pricing / billing**: per-input-minute minute-packs shipped (one-time, never-expiring). Remaining refinements — Stripe↔ledger reconciliation, payment-status guard, Stripe Tax, refund runbook, packaging/Stream pack — are **Issues 205–209**; spend caps + margin dashboard are **Issues 289–292**. See `docs/DECISIONS.md`.
- **Review-UI framework**: ✅ Resolved — React + TS + Tailwind v4 adopted (2026-06-17) with a documented design system (2026-06-18, Issue 85a; `docs/UI.md`). Strangler-fig migration COMPLETE (Issues 85a–85g + the AutoClip redesign 304–309); legacy vanilla app pages retired (Issue 226). The player-first "feels like scrolling" review surface shipped in the review-page port (85f/306).
- **YouTube quota ceilings**: Analytics/Data API quotas may throttle large catalogs — needs backoff + caching, sized once real quota is known.
- **Preference cold-start**: below threshold, ranking leans on DNA + signals only; communicate honestly.
- **`TOKEN_ENCRYPTION_KEY` rotation/escrow** — ✅ rotation procedure documented in `docs/RUNBOOKS.md`
  ("TOKEN_ENCRYPTION_KEY Rotation") and re-encryption script at `scripts/rotate_token_key.py`
  (zero-downtime via `MultiFernet([primary, previous])`). Off-box escrow is **Issue 255** (still open,
  pre-public-launch gate). The "runbook not yet written" framing was stale — fixed in Issue 264.
- **Vision signals deferred**: cam-on reaction detection is Phase 2.
