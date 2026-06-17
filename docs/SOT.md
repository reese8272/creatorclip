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
| LLM | Anthropic SDK; `claude-sonnet-4-6` default, `claude-opus-4-7` for DNA synthesis | Prompt caching on DNA profile + evergreen corpus **mandatory**; web-search tool for live research |
| Embeddings | Voyage AI (`voyage-3.5`) → pgvector | Local sentence-transformers as offline fallback |
| Transcription | WhisperX (faster-whisper + forced alignment), word-level | Hosted fallback (Deepgram/AssemblyAI) behind `TRANSCRIPTION_BACKEND` config; GPU recommended |
| Audio analysis | librosa + pyloudnorm | Energy, silence, volume spikes, laughter/applause heuristic |
| Vision (Phase 2) | MediaPipe / face-emotion model | Deferred |
| DB | PostgreSQL 16 + pgvector | Relational + embeddings in one store |
| Session / queue broker | Redis 7 | Celery broker + short-lived caches |
| Object storage | Cloudflare R2 (S3-compatible) | Source video + rendered clips; local disk in dev; retention purge |
| Video processing | ffmpeg | Cut + 9:16 active-speaker reframe |
| YouTube | YouTube Analytics API + Data API v3 (OAuth 2.0) | Retention curves, demographics, activity windows, metadata, captions |
| Auth | Google OAuth 2.0 (YouTube scopes) + server-side session JWT | PyJWT; bcrypt where local creds needed |
| Token encryption at rest | `cryptography` MultiFernet on token columns | Primary key from `TOKEN_ENCRYPTION_KEY`; optional previous key for zero-downtime rotation |
| Preference model | LightGBM (or logistic regression) reranker | Recency-decayed sample weights; retrained per session |
| Frontend | Vanilla HTML/CSS/JS, player-first | No build step. **Review-UI framework is a flagged DECISIONS.md candidate — resolve before Issue 10.** |
| Containerization | Docker Compose (dev) | `app`, `worker`, `beat`, `postgres`, `redis`. Beta prod (`docker-compose.prod.yml`) adds `cloudflared` (tunnel, no host port) + `autoheal` (restart-on-unhealthy) + app/worker healthchecks |
| Production deployment | Kubernetes (research pending) | Docker Compose = dev/test only. Production target: EKS / GKE / managed K8s. See `docs/DEPLOYMENT.md`. |

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
├── clients.py                  # Anthropic singleton, Voyage client, YouTube client factory, storage client
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
│   ├── audio.py                # Energy, silence, laughter, volume spikes
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
│   ├── render.py               # ffmpeg cut + 9:16 active-speaker reframe + ASS burn-in + clean-pass filter_complex
│   ├── captions.py             # Animated word-level ASS subtitles (Issue 133 — bold_pop / gradient_slide / minimal via pysubs2 + libass)
│   ├── filler.py               # Filler-word + silence cut-list generator (Issue 134 — Tier1 unconditional + Tier2 pause-flanked + 800ms silence w/150ms tail)
│   └── edits.py                # User-supplied cut-list validator (Issue 135 — bounds, overlap, 5s/85% caps, sub-frame floor) for text-based editor
│
│   # static/editor-layout.css + static/hero.css added in Issue 136 (dark editor layout + pre-auth hero)
│   # static/page-shell.css added in Issue 137 (project-wide aurora + soft-card shell + overflow-x: clip guard)
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
├── routers/
│   ├── activity.py             # POST /api/activity — browser UI event logging (Issue 122)
│   ├── auth.py                 # OAuth login/callback, session
│   ├── creators.py             # Creator profile, DNA, onboarding state
│   ├── videos.py               # Link/upload video, ingestion status
│   ├── clips.py                # List candidate clips, get clip, render status
│   ├── review.py               # Feedback: upvote/downvote/skip/trim/format
│   ├── upload_intel.py         # GET timing recommendation
│   ├── improvement.py          # GET improvement brief
│   ├── analysis.py             # POST video-analysis (Issue 121) + hook-analysis (Issue 130) + chapters (Issue 131)
│   ├── thumbnails.py           # GET thumbnail-patterns + POST thumbnail-concepts (Issue 129)
│   ├── titles.py               # POST video title suggestions (Issue 128)
│   └── tasks.py                # SSE live-progress endpoint (Issue 86)
│
├── worker/
│   ├── celery_app.py           # Celery + Redis broker
│   ├── tasks.py                # Pipeline tasks (ingest → render)
│   ├── schedule.py             # Beat: profile refresh, token refresh, media purge
│   ├── progress.py             # Issue 86 — per-task Redis Stream emit/read + SSE slot cap + ownership
│   └── anthropic_stream.py     # Issue 86 — wraps Anthropic .stream() so tokens flow into progress events
│
├── static/
│   ├── index.html              # Dashboard
│   ├── onboarding.html         # Connect YouTube, min-data gate, DNA confirm
│   ├── review.html             # Fast clip review (player-first, single-player + Next)
│   ├── profile.html            # Creator DNA view/edit
│   ├── insights.html           # Upload timing + improvement brief
│   ├── progressStream.js       # Issue 86 — EventSource consumer that renders live task progress
│   ├── activeTasks.js          # Wave 5 — localStorage + SSE resume across page navigation; window.activeTasks
│   ├── activityPanel.js        # Wave 5 — floating bottom-right widget; reacts to activeTasks.subscribe
│   ├── activity.js             # Beta-testing UI event tracker: click/submit/navigate → POST /api/activity (Issue 122)
│   └── analysis.html           # Video performance analysis page (Issue 121)
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
│   └── eval/                   # Clip-quality eval: labeled videos + expected clip windows
│       └── scenarios/*.yaml
│
├── scripts/
│   ├── doctor.py               # Preflight secrets validator (presence/format/live, redacted) — deploy gate
│   └── rotate_token_key.py     # TOKEN_ENCRYPTION_KEY re-encryption (see docs/RUNBOOKS.md)
│
└── docs/
    ├── README.md              # ← START HERE: full documentation index (Issue 146)
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
  plan_tier, subscription_status, created_at

youtube_tokens
  creator_id (FK), access_token_encrypted, refresh_token_encrypted,
  scope, expires_at, updated_at

videos
  id, creator_id (FK), youtube_video_id, title, kind (long/short),
  published_at, duration_s, source_uri, origin (catalog/link/upload),
  captions_available, ingest_status (pending/running/done/failed), created_at
  -- origin is the canonical provenance discriminator (Issue 139):
  --   catalog = DNA/analytics reference from sync_video_catalog (no media,
  --     hidden from /videos so the dashboard never shows "pending forever").
  --   link    = registered by ID via POST /videos/link (no media — we never
  --     download from YouTube per ToS; shown with clippable=false, the
  --     creator uploads the source file to clip).
  --   upload  = carries source_uri (stored media); the only clip-trackable path.
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

- **Transcription compute**: WhisperX needs a GPU. Either provision a GPU box or default to hosted transcription — decide before Issue 5.
- **Production deployment**: Docker Compose is dev-only. Production needs Kubernetes (EKS/GKE research pending). See `docs/DEPLOYMENT.md`.
- **Pricing / billing**: Usage-based tiers with prompt caching. Research pending (Claude/Stripe patterns). See `docs/DECISIONS.md`.
- **Review-UI framework**: Vanilla JS may not deliver the "feels like scrolling" bar. Flagged as a DECISIONS.md candidate before Issue 10.
- **YouTube quota ceilings**: Analytics/Data API quotas may throttle large catalogs — needs backoff + caching, sized once real quota is known.
- **Preference cold-start**: below threshold, ranking leans on DNA + signals only; communicate honestly.
- **`TOKEN_ENCRYPTION_KEY` rotation runbook** not yet written — required before public launch.
- **Vision signals deferred**: cam-on reaction detection is Phase 2.
