# CreatorClip — AI-Powered Personal Editor for YouTube Creators — Project Kickstart

**Working title**: `creatorclip` (rename freely)
**Source**: condensed and expanded from the CreatorClip brief on branch `claude/creatorclip-ai-editor-a09fy`
**Last updated**: 2026-05-25 (v1 — kickstart)
**Status**: Pre-build planning. Drop this file into the new repo as `docs/KICKSTART.md`, fill the `<PLACEHOLDER>` blocks, then promote each section into its own doc (`docs/PRD.md`, `docs/SOT.md`, `docs/issues.md`, `CLAUDE.md`) before Issue 1.
**Honesty constraint (must appear in every interface and the system prompt)**: *CreatorClip predicts fit with your style and audience — it does not promise virality. Every recommendation is an estimate grounded in your own data, not a guarantee. We comply with the YouTube API Services Terms of Service at all times; creator analytics are handled per Google's data policies.*

> **The one rule above all others:** On every non-trivial decision — architecture, library choice, model selection, scoring math, security boundary, UX pattern — we **ALWAYS research the current industry standard and best practice first**, and we justify any deviation in `docs/DECISIONS.md`. We do not build from memory. We do not guess. This is non-negotiable and is enforced in Phase 1 (CHECK) of every issue.

---

## Table of Contents

1. [Core Insight](#1-core-insight)
2. [PRD](#2-prd)
3. [SOT — Source of Truth](#3-sot--source-of-truth)
4. [The Three-Layer Framework + Clipping Principles](#4-the-three-layer-framework--clipping-principles)
5. [Open-Ended Questions — Answered](#5-open-ended-questions--answered)
6. [Starting Issue Backlog](#6-starting-issue-backlog)
7. [Project Workflow & Required Commands](#7-project-workflow--required-commands)
8. [CLAUDE.md Template (drop into new repo)](#8-claudemd-template)

---

# 1. Core Insight

> **The reason every AI clipping tool tops out at "technically correct but soulless" is a context problem, not a detection problem.**

Eklipse, Opus Clip, and Medal are **generic signal detectors**. They fire on loud audio, kills, and chat spikes — events that happen *after* a moment lands — so they clip the aftermath, not the moment. They start from zero on every video. They can't read dry humor, irony, or the pause before a punchline. They never learn from feedback.

The unlock isn't a smarter detector. It's **complete creator context + a feedback loop that compounds**. CreatorClip is a full-time editor that knows *you*: it learns your style, scores against *your* audience's proven behavior (from your own YouTube Analytics), clips the **setup** instead of the reaction, and gets measurably better every session with no ceiling on how well it can know you.

**The moat is not the AI. The moat is the feedback loop and the Creator DNA profile — and it compounds the longer a creator uses it.**

---

# 2. PRD

**Version**: 0.1 | **Status**: Draft, ready for issue scoping after Section 5 placeholders are filled

## Problem Statement

Every AI clipping tool on the market detects generic virality signals and ignores the individual creator. The result is clips that feel like they were made by someone who has never watched the channel: the wrong moments, cut at the wrong time, with no memory and no improvement. What creators actually need is an editor that knows them — one that improves every session, never leaves a good moment on the table, reads tone and delivery, and grounds every recommendation (clip selection, upload timing, content advice) in *their own* analytics rather than generic advice.

## Target User (v1)

Individual YouTubers with an existing content catalog (long-form + Shorts). The product is multi-tenant from day one because YouTube OAuth is inherently per-creator, but the MVP onboards a small, hand-picked set (the developer's own channel plus a few invited creators) before any public launch.

## User Stories

- As a creator, I want to **connect my YouTube account once** (OAuth) and have CreatorClip read my retention curves, engagement, demographics, and audience-activity windows.
- As a creator with too small a catalog, I want a **clear "not enough data yet" state** telling me exactly how many more videos/Shorts unlock Research Mode.
- As a creator, I want a **one-time Research Mode pass** over my catalog that builds my **Creator DNA**: what my best clips have in common, where in my videos they come from, my hook patterns, my optimal Short length, and what consistently underperforms.
- As a creator, I want a **plain-language Creator Brief** I can read, edit, confirm, or disagree with — and that becomes my living profile.
- As a creator, I want the engine to **run automatically on a new video** and surface a ranked set of candidate Shorts scored against *my* DNA, not a generic virality score.
- As a creator, I want clips that **start at the setup, not the aftermath** — the moment the bit begins, not the reaction after it lands.
- As a creator, I want a **review experience that feels like scrolling** — upvote / downvote / skip / drag-trim — where every interaction silently trains the model.
- As a creator, I want the model to **reflect my taste more over time**, weighting recent feedback more heavily so a content pivot isn't anchored to who I was 18 months ago.
- As a creator, I want **upload-timing recommendations pulled from my own audience-activity data** — not generic "post at 5pm" advice.
- As a creator, I want a **content-improvement brief** after each profile refresh: what's working, what's underperforming, and specific actions — informed by **live research** of current Shorts formats and algorithm changes, not stale knowledge.
- As the operator, I want **per-creator usage tracked** (videos processed, clips generated, tokens) so cost and quotas are visible before monetization.

## Technical Decisions

**Decision**: **YouTube-first, OAuth-grounded, multi-tenant from day one** — **Why**: The entire differentiator is "uses the creator's own analytics." That requires per-creator OAuth (YouTube Analytics + Data API). Single-user shortcuts would have to be torn out immediately, so we build per-creator isolation from the first table.

**Decision**: **Job-pipeline architecture (Celery + Redis), not a monolithic request** — **Why**: Ingestion → transcription → signal extraction → DNA scoring → render is minutes-to-hours of work per video. It must run as durable background tasks with retries and progress, never inside an HTTP request.

**Decision**: **PostgreSQL 16 + pgvector as the single store** for relational data *and* embeddings — **Why**: Creator profiles, clips, and feedback are relational; DNA/clip embeddings are vectors. pgvector keeps both in one database (KISS) instead of standing up a separate vector DB for v1.

**Decision**: **Claude (Anthropic SDK) as the only LLM**, with prompt caching and the web-search tool — **Why**: The reasoning work (DNA pattern synthesis, clip-fit scoring rationale, improvement briefs, live trend research) is nuanced language judgment. Prompt caching on the DNA profile + evergreen corpus keeps cost flat as catalogs grow; the web-search tool supplies live algorithm/format research.

**Decision**: **WhisperX (faster-whisper + forced alignment) for word-level transcripts**, with a hosted-API fallback (Deepgram/AssemblyAI) behind config — **Why**: The "clip the setup" mechanic needs word-level timestamps to find sentence/beat boundaries. WhisperX gives that locally; the hosted fallback exists for environments without a GPU. This is a load-bearing infra decision — see Known Production Gaps and `docs/DECISIONS.md`.

**Decision**: **The clip engine looks *backwards* from a peak signal** (rolling 60–90s window) to find the setup start — **Why**: This is *the* product differentiator. Competitors react to peaks and clip the aftermath; we anchor the clip start at where the beat began.

**Decision**: **The preference model is a learned reranker (recency-decayed), not a fine-tuned LLM** — **Why**: Per-creator fine-tuning is expensive, slow, and brittle. A gradient-boosted/logistic reranker over clip features + embeddings, retrained per session with exponential recency decay on sample weights, is the honest, buildable version of "trained on them specifically" and updates in seconds.

**Decision**: **Voyage AI embeddings (Anthropic's recommended partner) in pgvector** — **Why**: Consistent with an Anthropic-centric stack; high-quality embeddings for DNA patterns and clip content. Local sentence-transformers is the offline fallback.

**Decision**: **ffmpeg for cutting + active-speaker-centered vertical reframe** — **Why**: Industry-standard, scriptable, no per-render licensing. Vertical (9:16) reframe via face/active-speaker crop is required for Shorts output.

**Decision**: **Object storage (Cloudflare R2, S3-compatible) for source video + rendered clips**, local disk in dev — **Why**: Video is heavy and egress-expensive. R2 has zero egress fees and an S3 API. Source media is purged on a retention timer (see Compliance).

**Decision**: **Vanilla HTML/CSS/JS, player-first review UI**, no build step — **Why**: Matches the house style. **Flagged risk**: the review UX (swipeable, instant trim handles) is the make-or-break feature and is the *one* place a small framework may be justified — this is an explicit `docs/DECISIONS.md` candidate, decided before Issue building the review UI.

**Decision**: **Facial-expression / vision signals are deferred to Phase 2** — **Why**: Vision is the heaviest, least-reliable signal and is only useful when the cam is on. Transcript + audio + retention curves carry the MVP; vision is additive later.

**Decision**: **Source acquisition = creator-initiated** (the creator's own content via the API where permitted, or explicit upload), with `yt-dlp` treated as a ToS-risk convenience, not the default — **Why**: Downloading arbitrary YouTube video bytes can violate the YouTube ToS. The compliant path is the creator acting on their *own* content. See `docs/COMPLIANCE.md`. This is a hard constraint, not a preference.

## Out of Scope (v1)

- Platforms other than YouTube (TikTok / Reels export is MVP+)
- Direct auto-publishing to YouTube Shorts (recommend + export in v1; publishing is a later issue)
- Live-stream ingestion (long-form + Shorts only at MVP; streams gated behind an hour cap later)
- Vision / facial-expression signals (Phase 2)
- Fine-tuned per-creator LLMs (the reranker is the learning layer)
- Team / multi-seat accounts, agencies managing many channels
- Mobile-native app (responsive web only)
- A virality *guarantee* of any kind — we predict fit, honestly

## Acceptance Criteria (v1 MVP)

### YouTube Integration
- [ ] Creator completes Google OAuth with the minimum YouTube Analytics + Data API scopes; tokens stored encrypted and auto-refreshed.
- [ ] App fetches per-video metrics, **timestamp-level retention curves**, demographics, traffic sources, and **audience-activity windows**.
- [ ] A clear **minimum-data gate** is surfaced when the catalog is too small ("Upload at least X videos / Y Shorts to unlock Research Mode").

### Research Mode (Creator DNA)
- [ ] Ranks the catalog by **engagement rate** (not raw views) and analyzes top 5–10 and bottom 5–10 performers.
- [ ] Extracts: hook structure (first 3s), best source region within long-form, clip-length patterns, title/thumbnail framing, retention-curve shape, tone/delivery, and Shorts-specific patterns (extraction points, optimal length, upload gap, Shorts-per-long-form ratio).
- [ ] Produces a **plain-language Creator Brief** the creator can edit, confirm, or flag disagreements on.
- [ ] The confirmed brief persists as the **living DNA profile** (versioned).

### Clip Engine
- [ ] Runs as a background job when a video is linked/uploaded.
- [ ] Ingests transcript (word-level), audio energy/silence/laughter, and retention-curve spikes into a unified signal timeline.
- [ ] **Looks backwards from each peak signal** to set the clip start at the setup, not the reaction (verified against a labeled eval set).
- [ ] Produces N configurable candidate clips, each **scored against the Creator DNA** and ranked by predicted fit.
- [ ] Renders each candidate as a 9:16 Short with active-speaker-centered reframe.

### Review UI
- [ ] Player-first review surface: watch → **upvote / downvote / skip**, **drag trim handles**, select format.
- [ ] Every interaction writes a training label (upvote/downvote/skip/trim-delta/format).
- [ ] Feels fast — no full page reloads between clips.

### Preference Model
- [ ] Feedback updates a per-creator reranker with **exponential recency decay** on sample weights.
- [ ] Reranking measurably shifts candidate order after a configurable feedback volume; the honest "personalization kicks in at ~N labels" threshold is surfaced to the creator.
- [ ] A published-clip outcome (real-world performance) is recorded as the **strongest positive signal** when available.

### Upload Intelligence
- [ ] `GET` returns a **best upload window** (day/hour) derived from the creator's own audience-activity data.
- [ ] Returns an **optimal long-form → Short gap** when the catalog supports it.
- [ ] Surfaces as a single plain recommendation, not a dashboard of numbers.

### Content Improvement Layer
- [ ] After each Research Mode run / profile refresh, generates a brief: what's working, what's underperforming, specific actions.
- [ ] Uses **live web research** for current formats/algorithm changes; recommendations are specific to this creator, never generic.

### Operational & Compliance
- [ ] All Anthropic calls use **prompt caching** on the DNA profile + evergreen corpus; token usage logged per call.
- [ ] Per-creator usage (videos, clips, tokens) tracked.
- [ ] YouTube Analytics data retention/refresh complies with YouTube API Services policy; source media purged on a retention timer.
- [ ] OAuth tokens encrypted at rest; no token or PII in logs.
- [ ] `docker compose up` brings the full stack live; `pytest` green; clip-quality eval harness green.

---

# 3. SOT — Source of Truth

**Last updated**: 2026-05-25

This describes how CreatorClip **will be built**. Update on every architectural change. Conflicts with the PRD: this file wins — log divergence in `docs/DECISIONS.md`.

## Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| Backend | FastAPI (Python 3.12+) | Async-first |
| Task queue | Celery + Redis | Durable video jobs: ingest → transcribe → signals → DNA → clip → render. `arq` considered; Celery chosen for maturity. |
| LLM | Anthropic SDK; default `claude-sonnet-4-6`, `claude-opus-4-7` for DNA synthesis | Prompt caching on DNA profile + evergreen corpus mandatory; web-search tool for live research |
| Embeddings | Voyage AI (`voyage-3.5`) → pgvector | Local sentence-transformers as offline fallback |
| Transcription | WhisperX (faster-whisper + alignment), word-level | Hosted fallback (Deepgram/AssemblyAI) behind config; GPU recommended |
| Audio analysis | librosa + pyloudnorm | Energy, silence, volume spikes, laughter/applause heuristic |
| Vision (Phase 2) | MediaPipe / face-emotion model | Facial expressions, scene cuts — deferred |
| DB | PostgreSQL 16 + pgvector | Relational + embeddings in one store |
| Session / queue broker | Redis 7 | Celery broker + short-lived caches |
| Object storage | Cloudflare R2 (S3-compatible) | Source video + rendered clips; local disk in dev; retention purge |
| Video processing | ffmpeg | Cut + 9:16 active-speaker reframe |
| YouTube | YouTube Analytics API + Data API v3 (OAuth 2.0) | Retention curves, demographics, activity windows, metadata, captions |
| Auth | Google OAuth 2.0 (YouTube scopes) + server-side session JWT | bcrypt where local creds needed; PyJWT |
| Token encryption at rest | `cryptography` Fernet on token columns | Key from `TOKEN_ENCRYPTION_KEY` |
| Preference model | LightGBM (or logistic regression) reranker | Recency-decayed sample weights; retrained per session |
| Frontend | Vanilla HTML/CSS/JS, player-first | No build step (review-UI framework is a flagged `DECISIONS.md` candidate) |
| Containerization | Docker Compose | `app`, `worker`, `postgres`, `redis` |
| Deployment (v1) | `<PLACEHOLDER: host>` — see deployment note | Cloudflare Tunnel; transcription needs GPU or hosted API |

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key |
| `VOYAGE_API_KEY` | Yes (unless local embeddings) | Voyage AI embeddings key |
| `DATABASE_URL` | Yes | `postgresql+psycopg://user:pass@host:5432/creatorclip` |
| `REDIS_URL` | Yes | `redis://localhost:6379/0` — Celery broker + cache |
| `GOOGLE_OAUTH_CLIENT_ID` | Yes | Google OAuth client ID (YouTube scopes) |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Yes | Google OAuth client secret |
| `OAUTH_REDIRECT_URI` | Yes | OAuth callback URL |
| `TOKEN_ENCRYPTION_KEY` | Yes | Fernet key for YouTube token columns; document rotation runbook |
| `JWT_SECRET_KEY` | Yes | Session JWT secret (32-byte random) |
| `JWT_EXPIRY_MINUTES` | No | Default `60` |
| `TRANSCRIPTION_BACKEND` | No | `whisperx` (default) \| `deepgram` \| `assemblyai` |
| `DEEPGRAM_API_KEY` / `ASSEMBLYAI_API_KEY` | Conditional | Required if hosted transcription backend selected |
| `WHISPER_MODEL` | No | Default `large-v3` |
| `STORAGE_BACKEND` | No | `r2` (default prod) \| `local` (dev) |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` | Conditional | Required if `STORAGE_BACKEND=r2` |
| `SOURCE_MEDIA_RETENTION_HOURS` | No | Default `72`; source video purge timer |
| `CLIPS_PER_VIDEO_DEFAULT` | No | Default `<PLACEHOLDER: e.g., 8>` |
| `MIN_VIDEOS_FOR_DNA` | No | Default `<PLACEHOLDER: e.g., 10>` |
| `MIN_SHORTS_FOR_DNA` | No | Default `<PLACEHOLDER: e.g., 5>` |
| `PERSONALIZATION_THRESHOLD_LABELS` | No | Default `<PLACEHOLDER: e.g., 20>` |
| `LLM_TIMEOUT_SECONDS` | No | Default `120` |
| `ENV` | No | `development` \| `production`; gates `/docs`, logging |
| `ALLOWED_ORIGINS` | Yes (prod) | Lock to production domain; never `*` in prod |

## File Structure

```
/                               # project root
├── CLAUDE.md                   # Drop in from Section 8
├── .env / .env.example
├── requirements.txt
├── pytest.ini
├── docker-compose.yml
├── Dockerfile
│
├── main.py                     # FastAPI entrypoint
├── config.py                   # Env loading; fail-fast on missing required
├── db.py                       # SQLAlchemy engine + session
├── auth.py                     # Google OAuth + session JWT; get_current_creator
├── crypto.py                   # Fernet helpers for token columns
├── clients.py                  # Anthropic singleton, Voyage client, YouTube client factory, storage client
│
├── youtube/
│   ├── oauth.py                # OAuth flow, token storage/refresh (encrypted)
│   ├── analytics.py            # Retention curves, demographics, activity windows
│   ├── data_api.py             # Video metadata, captions
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
│   ├── profile.py              # CreatorDNA model + living profile CRUD (versioned)
│   ├── brief.py                # Plain-language creator brief generation (Claude)
│   └── embeddings.py           # Profile + clip embeddings → pgvector
│
├── clip_engine/
│   ├── window.py               # Rolling 60–90s context window
│   ├── candidates.py           # Peak detection + backward look for setup start
│   ├── scoring.py              # Multi-signal + DNA-weighted scoring (Claude + features)
│   ├── ranking.py              # DNA-weighted + preference-model rerank
│   └── render.py               # ffmpeg cut + 9:16 active-speaker reframe
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
│   └── seed/                   # Evergreen corpus: hook psychology, pacing, retention theory
│
├── upload_intel/
│   └── timing.py               # Best upload window + optimal gap from analytics
│
├── improvement/
│   └── brief.py                # Content-improvement brief generation
│
├── routers/
│   ├── auth.py                 # OAuth login/callback, session
│   ├── creators.py             # Creator profile, DNA, onboarding state
│   ├── videos.py               # Link/upload video, ingestion status
│   ├── clips.py                # List candidate clips, get clip, render status
│   ├── review.py               # Feedback: upvote/downvote/skip/trim/format
│   ├── upload_intel.py         # GET timing recommendation
│   └── improvement.py          # GET improvement brief
│
├── worker/
│   ├── celery_app.py           # Celery + Redis broker
│   ├── tasks.py                # Pipeline tasks (ingest → render)
│   └── schedule.py             # Beat: profile refresh, token refresh, media purge
│
├── static/
│   ├── index.html              # Dashboard
│   ├── onboarding.html         # Connect YouTube, min-data gate, DNA confirm
│   ├── review.html             # Fast clip review (player-first)
│   ├── profile.html            # Creator DNA view/edit
│   └── insights.html           # Upload timing + improvement brief
│
├── tests/
│   ├── conftest.py
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
└── docs/
    ├── PRD.md
    ├── SOT.md
    ├── DECISIONS.md
    ├── PROJECT_STATE.md
    ├── issues.md
    ├── CLIPPING_PRINCIPLES.md   # Named clipping/retention principles the engine cites
    ├── COMPLIANCE.md            # YouTube API ToS, data retention, privacy posture
    └── DEPLOYMENT.md
```

## Data Model (initial)

```
creators
  id, google_sub (unique), channel_id, channel_title, email,
  onboarding_state (connected/awaiting_data/dna_pending/active),
  plan_tier, subscription_status, created_at

youtube_tokens
  creator_id (FK), access_token_encrypted, refresh_token_encrypted,
  scope, expires_at, updated_at

videos
  id, creator_id (FK), youtube_video_id, title, kind (long/short),
  published_at, duration_s, source_uri (nullable until ingested),
  captions_available, ingest_status (pending/running/done/failed), created_at

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
  video_id (FK), timeline_jsonb (audio energy, silence, laughter, vision)

creator_dna                          # the living profile (versioned)
  id, creator_id (FK), version, brief_text, patterns_jsonb,
  top_video_ids_jsonb, bottom_video_ids_jsonb,
  optimal_clip_len_s, best_source_region, optimal_upload_gap_h,
  status (draft/confirmed/superseded), created_at

dna_embeddings
  id, creator_id (FK), kind (pattern/clip/hook), embedding (vector), ref_jsonb

clips
  id, video_id (FK), creator_id (FK), setup_start_s, start_s, end_s, peak_s,
  score, dna_match, signals_jsonb, format (short/horizontal),
  render_uri (nullable), render_status, rank, created_at

clip_feedback
  id, clip_id (FK), creator_id (FK),
  action (upvote/downvote/skip/trim/format),
  trim_start_s (nullable), trim_end_s (nullable), chosen_format (nullable),
  created_at

clip_outcomes                        # strongest positive signal
  clip_id (FK), published_youtube_id, views, retention,
  performed_well (bool), fetched_at

preference_models
  creator_id (FK), version, weights_blob, feature_schema_jsonb, updated_at

usage
  creator_id (FK), period, videos_processed, clips_generated,
  tokens_in, tokens_out

audit_log
  id, at, actor, action, entity_type, entity_id, before_jsonb, after_jsonb
```

## Processing Pipeline (Celery)

```
   creator links/uploads a video
                │
                ▼
        ┌───────────────┐
        │   Ingest      │  acquire source (upload / guarded yt-dlp), normalize, store to R2
        └──────┬────────┘
               ▼
        ┌───────────────┐
        │ Transcribe    │  WhisperX word-level (or captions / hosted fallback)
        └──────┬────────┘
               ▼
        ┌───────────────┐
        │ Signals       │  audio energy/silence/laughter + retention-curve spikes → timeline
        └──────┬────────┘
               ▼
        ┌───────────────┐
        │ Candidates    │  detect peaks → look BACKWARDS in 60–90s window → setup start
        └──────┬────────┘
               ▼
        ┌───────────────┐
        │ Score         │  features + Claude DNA-fit judgment (cached on DNA profile)
        └──────┬────────┘
               ▼
        ┌───────────────┐
        │ Rank          │  DNA-weighted + per-creator preference reranker
        └──────┬────────┘
               ▼
        ┌───────────────┐
        │ Render        │  ffmpeg cut + 9:16 active-speaker reframe → R2
        └──────┬────────┘
               ▼
        candidate clips ready for Review UI
                │
                ▼  (creator feedback)
        ┌───────────────┐
        │ Preference    │  feedback → recency-decayed reranker update
        │ Update Loop   │
        └───────────────┘

Research Mode (runs once at onboarding, refreshed on cadence) sits parallel:
  catalog metrics + retention curves → top/bottom analysis → pattern extraction
  → Claude synthesis → Creator Brief → creator confirms → living DNA profile + embeddings
```

## Security & Compliance Posture (v1)

- **YouTube API Services ToS is a hard constraint.** Comply with display requirements, quota limits, and **data-retention/refresh rules** (stored analytics refreshed or purged per policy). Documented in `docs/COMPLIANCE.md`.
- **Source-acquisition compliance:** creator-initiated only (own content via API where permitted, or explicit upload). `yt-dlp` is a ToS-risk convenience, off by default, never used on third-party channels.
- **OAuth tokens encrypted at rest** (Fernet); never logged; refreshed via the standard flow.
- **Per-creator data isolation** enforced at the query layer; tests assert no cross-creator leakage.
- **PII minimization:** store only what the features need; demographics aggregated.
- **Source media purged** on a retention timer (`SOURCE_MEDIA_RETENTION_HOURS`); rendered clips kept until the creator deletes them.
- **TLS in transit** via Cloudflare Tunnel; secrets in env only, never committed.
- **`ALLOWED_ORIGINS` locked** to the production domain; `/docs` disabled in production.
- **Honesty enforcement:** no interface or response promises virality; the engine predicts *fit* — a structural test verifies the disclaimer/honesty text appears where required.

## Known Production Gaps

- **Transcription compute:** WhisperX wants a GPU; the v1 deployment host may not have one. Either provision a GPU box or default to a hosted transcription API — decide before Issue 4 and log in `docs/DECISIONS.md`.
- **Review-UI framework:** vanilla JS may not deliver the "feels like scrolling" bar; a small framework is a flagged candidate.
- **Vision signals deferred:** cam-on reaction detection is Phase 2.
- **Auto-publish deferred:** v1 exports; direct Shorts publishing is a later issue.
- **YouTube quota ceilings:** Analytics/Data API quotas may throttle large catalogs — needs a backoff + caching strategy, sized once real quota is known.
- **Preference cold-start:** below the personalization threshold, ranking leans on DNA + signals only; communicate this honestly.
- **`TOKEN_ENCRYPTION_KEY` rotation runbook** not yet written.
- **Eval harness** covers happy paths; needs adversarial/edge coverage (no-cam, music-only, very long videos).

---

# 4. The Three-Layer Framework + Clipping Principles

The engine always reasons against three layers, with a dual knowledge base underneath.

```
Layer 1 — Creator DNA        (research mode; built once, refreshed over time)
Layer 2 — Clip Engine        (runs on every new video, informed by Creator DNA)
Layer 3 — Preference Model   (updates from feedback; improves every session, recency-decayed)

        ── Dual knowledge base ──
Evergreen RAG  : hook psychology, pacing, retention theory, cut structure (timeless)
Live research  : current algorithm changes, trending formats (Claude web-search tool)
```

**The timing fix (the core mechanic):** competitors react to peaks (chat spike, loud audio) that occur *after* a moment lands, so they clip the aftermath. CreatorClip holds a **rolling 60–90s context window**; when a peak signal fires it looks **backwards** to where the setup began (a sentence/beat boundary in the word-level transcript, preceded by a quieter audio baseline) and starts the clip there. The clip ends after the payoff resolves — not at the reaction.

**Named clipping/retention principles the engine cites** (lives in `docs/CLIPPING_PRINCIPLES.md`):

- **Hook in the first 3 seconds** — the opening determines retention; weak openings lose the audience before the payoff.
- **Clip the setup, not the aftermath** — start where the beat begins, not where the reaction peaks.
- **Tension and release** — a clip needs a setup and a payoff; a payoff with no setup feels random.
- **Pattern interrupt** — a change of beat every few seconds holds attention.
- **Dead-air elimination** — trim silence and filler; momentum is retention.
- **Retention curve is ground truth** — rewatch spikes mark genuinely high-value moments; lean on the creator's own data over generic heuristics.
- **Loop-ability** — Shorts that loop cleanly retain; favor cut points that resolve.
- **Front-load value** — never bury the payoff late in the clip.
- **One idea per Short** — a single clear beat outperforms a montage.
- **Native length over generic length** — match *this creator's* proven optimal Short length, not a fixed 60s.
- **Audience-fit over generic virality** — every score is against this creator's DNA and audience, never a one-size signal.

**The engine's job is not to lecture.** It cites the principle in one line when explaining a clip or a brief. The creator can ask "why this clip?" to get the longer reasoning on demand.

---

# 5. Open-Ended Questions — Answered

Each subsection answers a question from the brief. `<PLACEHOLDER>` markers are where you must decide before Issue 1.

## 5.1 Product

**Q: Exact credit/usage model — per video, per clip, flat subscription?**
A: `<PLACEHOLDER: Recommended starting point — flat subscription tier with a monthly cap on videos processed; overage by additional video. Pick the cap, e.g., "X videos/month included." Usage is already tracked in the `usage` table from day one so any model is mechanical later.>`

**Q: How many clips per video by default — configurable?**
A: Default `CLIPS_PER_VIDEO_DEFAULT` = `<PLACEHOLDER: e.g., 8>`, configurable per run in the UI. The engine generates more candidates internally and surfaces the top N.

**Q: How does the creator override the DNA profile manually?**
A: The Creator Brief is editable on `profile.html`; edits create a new confirmed DNA version (old version superseded, never deleted). Manual overrides are weighted as high-confidence signals.

**Q: Streams at MVP, or long-form + Shorts only?**
A: **Long-form + Shorts only at MVP.** Streams add an hour-cap and chunking problem; deferred. `<PLACEHOLDER: if streams later, set the hour cap.>`

**Q: Onboarding for a creator with a small catalog (<10 videos)?**
A: Show the **minimum-data gate** ("Upload at least X videos / Y Shorts to unlock Research Mode") driven by `MIN_VIDEOS_FOR_DNA` / `MIN_SHORTS_FOR_DNA`. Until then, the engine can still clip individual videos using signals + evergreen principles, clearly labeled "DNA not yet built."

## 5.2 Profile & Learning

**Q: What triggers a profile refresh?**
A: Milestone-based by default — every `<PLACEHOLDER: e.g., 10>` new videos — plus a manual "refresh now" button, plus continuous reranker updates from feedback (the reranker updates every session; the *DNA brief* refreshes on milestone/manual).

**Q: How do we handle a content pivot?**
A: **Exponential recency decay** on feedback sample weights (recent feedback dominates) plus recency weighting when selecting the top/bottom performers for DNA synthesis. A creator who pivots converges to the new style within a few refresh cycles.

**Q: At what feedback volume does personalization meaningfully kick in?**
A: `PERSONALIZATION_THRESHOLD_LABELS` = `<PLACEHOLDER: e.g., 20>`. Below it, ranking leans on DNA + signals and the UI says "still learning your taste — N more reviews to personalize." We communicate this **honestly**.

## 5.3 Data & API

**Q: YouTube Analytics API rate limits / quota?**
A: `<PLACEHOLDER: confirm current quota from Google Cloud Console for the project.>` Mitigation regardless: cache fetched analytics, fetch incrementally, exponential backoff on 403/quota errors.

**Q: Retention-curve access — all videos or recent only?**
A: `<PLACEHOLDER: verify availability window via the API for the target channel.>` Design assumes it may be limited; the DNA builder degrades gracefully when a curve is unavailable.

**Q: Video ingestion approach?**
A: **Creator-initiated upload or own-content via API where permitted.** `yt-dlp` is a ToS-risk convenience, off by default, never on third-party channels. See `docs/COMPLIANCE.md`.

**Q: Top videos are years old vs. current style?**
A: Recency weighting in DNA selection (above) plus the improvement brief can flag "your strongest historical clips predate your current format" so old hits don't anchor the profile.

## 5.4 Content Improvement Layer

**Q: How/when does the brief surface?**
A: After each Research Mode run / profile refresh, and on-demand from `insights.html`. `<PLACEHOLDER: decide if a weekly cadence is also wanted.>`

**Q: How do we avoid generic YouTube advice?**
A: Every recommendation must cite a specific row of the creator's own data (a retention curve, a top/bottom performer, an activity window) **and** be grounded in live research for current formats — generic advice with no data citation is rejected by the brief generator.

**Q: Boundary between "improve" and overstepping the creator's voice?**
A: The brief advises on *structure, timing, and packaging* (hooks, pacing, length, upload window) — never on *what to say or who to be*. This boundary is encoded in the brief prompt.

## 5.5 Distribution & Publishing

**Q: Publish directly to Shorts, or export only?**
A: **Export only at MVP** (download + optional "add to queue"). Direct publishing via the Data API is a later issue gated on additional OAuth scope.

**Q: TikTok / Reels as secondary outputs?**
A: Out of scope for v1; the 9:16 render is already platform-agnostic, so MVP+ export to other platforms is mechanical.

**Q: Scheduling — recommend-and-autopost, or recommend only?**
A: **Recommend only at MVP.** Auto-post is bundled with the direct-publishing issue later.

## 5.6 UI / UX

**Q: What does review actually look like?**
A: **Player-first, one clip at a time**, keyboard + swipe friendly: space to play, arrow up/down to vote, drag handles to trim, single tap to choose format. The bet is that it must "feel like scrolling." `<PLACEHOLDER: confirm swipe-stack vs. single-player-with-next.>`

**Q: How do we make trimming fast enough that creators do it?**
A: Pre-rendered candidate with draggable in/out handles on a waveform+thumbnail strip; trim deltas are the strongest *timing* signal, so the handles are the visual centerpiece, not buried in a menu.

**Q: Is the DNA profile visible to the creator or internal?**
A: **Visible** — the plain-language Creator Brief *is* the profile's human face; the embeddings/weights behind it stay internal.

## 5.7 The Core Bet

**Q: The one sentence that makes this indispensable on day one?**
A: `<PLACEHOLDER: Write it. Candidate from the brief — "Give me clips that actually sound like me, cut at the right moment, that get better every time I use them." If you can't articulate one north-star sentence, stop and find it before Issue 1.>`

---

# 6. Starting Issue Backlog

Dependency-ordered. Each issue follows Check → Approve → Build → Review (Section 7). **Phase 1 always begins by researching the current industry standard for the patterns the issue touches.**

---

**Issue 1: Repo scaffold + Docker Compose + health endpoint**
**Depends on**: none
**What**: New repo with `CLAUDE.md` (Section 8), `requirements.txt`, `Dockerfile`, `docker-compose.yml` (`app` + `worker` + `postgres` + `redis`), `main.py` with `/health`, `config.py` env loading, `crypto.py` Fernet helpers.
**Acceptance criteria**:
- [ ] `docker compose up` brings all four services healthy
- [ ] `GET /health` returns `{status, postgres, redis}`
- [ ] `.env.example` lists every var from SOT
- [ ] Missing required env fails app start with a clear error
- [ ] `pytest` passes with a `/health` smoke test

---

**Issue 2: Postgres schema + Alembic + pgvector**
**Depends on**: 1
**What**: SQLAlchemy models for every entity (Section 3) + memory/feedback tables. pgvector extension enabled. Alembic wired. Encrypted round-trip for token columns.
**Acceptance criteria**:
- [ ] `alembic upgrade head` creates every table incl. `creator_dna`, `dna_embeddings`, `clip_feedback`, `clip_outcomes`, `preference_models`
- [ ] pgvector column type works (insert + similarity query)
- [ ] Token encrypt/decrypt round-trip test passes
- [ ] Audit log append-only at the app layer

---

**Issue 3: Google/YouTube OAuth + creator session**
**Depends on**: 2
**What**: OAuth 2.0 flow (`/auth/login`, `/auth/callback`), minimum YouTube Analytics + Data API scopes, encrypted token storage + refresh, `get_current_creator` dependency, per-creator isolation.
**Acceptance criteria**:
- [ ] Creator completes OAuth; channel identity + tokens persisted (encrypted)
- [ ] Expired access token auto-refreshes
- [ ] Protected routes 401 without a session
- [ ] Cross-creator data access is rejected (isolation test)

---

**Issue 4: YouTube data fetch — metrics, retention, activity**
**Depends on**: 3
**What**: `youtube/analytics.py` + `youtube/data_api.py`: per-video metrics, timestamp-level retention curves, demographics, audience-activity windows, video metadata, caption availability. Caching + backoff. **Resolve the transcription-host decision (GPU vs hosted) here.**
**Acceptance criteria**:
- [ ] Fetches and stores metrics, retention curves, activity windows for the creator's catalog
- [ ] Quota/backoff handling on 403
- [ ] Minimum-data gate computed from catalog size
- [ ] Tests use recorded fixtures (no live API in CI)

---

**Issue 5: Ingestion pipeline — source + transcript + signals**
**Depends on**: 4
**What**: Celery tasks: ingest (creator upload / guarded yt-dlp → R2), transcribe (WhisperX or hosted, word-level), audio signals (energy/silence/laughter), unified signal timeline.
**Acceptance criteria**:
- [ ] A linked/uploaded video runs ingest → transcribe → signals as background tasks with status
- [ ] Word-level transcript persisted
- [ ] Signal timeline persisted (audio + retention-spike markers merged)
- [ ] `yt-dlp` path guarded to own-content only; off by default
- [ ] Tests cover the task chain with a short fixture clip

---

**Issue 6: Creator DNA builder + brief (Research Mode)**
**Depends on**: 5
**What**: `dna/builder.py` ranks by engagement, analyzes top/bottom performers + Shorts-specific patterns, extracts patterns; `dna/brief.py` synthesizes a plain-language brief via Claude (prompt-cached corpus); embeddings → pgvector; creator confirms → living profile.
**Acceptance criteria**:
- [ ] Produces top/bottom analysis + Shorts patterns (extraction point, optimal length, upload gap, ratio)
- [ ] Generates an editable plain-language Creator Brief
- [ ] Confirmed brief persists as a versioned DNA profile; edits supersede, never delete
- [ ] Recency weighting applied to performer selection
- [ ] Anthropic calls use prompt caching; tokens logged

---

**Issue 7: Clip engine — candidates with backward setup-finding**
**Depends on**: 6
**What**: `clip_engine/window.py` rolling 60–90s window; `candidates.py` peak detection + **backward look to setup start**; produces candidate windows.
**Acceptance criteria**:
- [ ] Given a signal timeline, emits candidate windows with `setup_start_s`, `peak_s`, `end_s`
- [ ] **Eval assertion**: on labeled fixtures, clip start lands at the setup, not the post-peak aftermath
- [ ] Configurable candidate count
- [ ] Pure logic where possible; deterministic given fixed input

---

**Issue 8: Clip scoring + DNA-weighted ranking**
**Depends on**: 7
**What**: `scoring.py` combines signal features + Claude DNA-fit judgment (cached on DNA profile); `ranking.py` orders by predicted fit. No preference model yet (cold-start path).
**Acceptance criteria**:
- [ ] Each candidate gets a `score` and `dna_match`
- [ ] Ranking reflects DNA (clips matching the brief rank higher) on a fixture
- [ ] Claude scoring rationale citable ("why this clip")
- [ ] Tokens logged; prompt caching verified

---

**Issue 9: Render — 9:16 cut + active-speaker reframe**
**Depends on**: 8
**What**: `render.py` ffmpeg cut + vertical reframe (face/active-speaker-centered) → R2; render status on the clip.
**Acceptance criteria**:
- [ ] Candidate renders to a playable 9:16 Short
- [ ] Reframe keeps the speaker in frame on a fixture
- [ ] Render runs as a Celery task with status
- [ ] Output stored to configured storage backend

---

**Issue 10: Review UI + feedback capture**
**Depends on**: 9
**What**: Player-first `review.html`: play, upvote/downvote/skip, drag-trim, choose format; `routers/review.py` persists every interaction as a label. **Decide the review-UI framework question in Phase 1.**
**Acceptance criteria**:
- [ ] Creator can review a queue of candidate clips without full page reloads
- [ ] Each action (vote/skip/trim-delta/format) writes a `clip_feedback` row
- [ ] Trim handles produce timing-delta labels
- [ ] Tests cover the feedback endpoints end-to-end

---

**Issue 11: Preference model — recency-decayed reranker**
**Depends on**: 10
**What**: `preference/` feature vectors + LightGBM/logistic reranker with exponential recency decay; retrain per session; rerank candidates; surface the personalization threshold.
**Acceptance criteria**:
- [ ] Feedback updates a per-creator model
- [ ] Recency decay verifiably down-weights old feedback (unit test)
- [ ] Reranking shifts candidate order after the threshold volume
- [ ] Below threshold, falls back to DNA+signal ranking with an honest UI label

---

**Issue 12: Upload intelligence + improvement brief**
**Depends on**: 11
**What**: `upload_intel/timing.py` best window + optimal gap from audience activity; `improvement/brief.py` what's-working / underperforming / actions, grounded in data citations + live research (web-search tool).
**Acceptance criteria**:
- [ ] `GET` returns a best upload window from the creator's own activity data
- [ ] Returns optimal long-form→Short gap when supported
- [ ] Improvement brief cites specific data rows + current-format research; no generic advice
- [ ] Disclaimer/honesty text present (structural test)

---

**Issue 13: Clip outcomes loop (strongest signal)**
**Depends on**: 12
**What**: When a creator publishes a clip, capture its real-world performance via the API and feed it back as the strongest positive label.
**Acceptance criteria**:
- [ ] Published clip outcomes fetched and stored
- [ ] Outcome feeds the preference model at the highest weight
- [ ] Tests cover the outcome → model path

---

**Issue 14+: Vision signals, auto-publish, multi-platform export, eval hardening, key-rotation runbook**
(See SOT "Known Production Gaps" — each becomes its own issue once the core loop is shipped.)

---

# 7. Project Workflow & Required Commands

The workflow you'll use in the new repo. Mirrors LIVABILITY's discipline.

> **ALWAYS, EVERY ISSUE, NO EXCEPTIONS:** Phase 1 begins by researching the **current industry standard and best practice** for every non-trivial pattern the issue touches — architecture, libraries, model choice, scoring math, security boundaries, UX. We build to the standard and justify any deviation in `docs/DECISIONS.md`. We never build from memory or guess.

## 7.1 Four-Phase Issue Workflow

Run this loop for every issue. Do not start Issue N+1 until Issue N has cleared all four phases.

### Phase 1 — CHECK
Research the industry-standard approach for every non-trivial pattern. Look up current best practices (not memory). Present a brief in this exact format:

> **Issue N — [title]**
> **Approach:** [specific pattern, library, or architecture]
> **Why for this project:** [1–2 sentences tying to stack/constraints]
> **Industry standard checked:** [what current best practice you confirmed, and the source]
> **Alternatives ruled out:** [what we considered and why it lost]
> **Good to go?**

### Phase 2 — APPROVE
Wait for explicit confirmation. If the approach changes during discussion, that's a candidate for `docs/DECISIONS.md`. "Just go" or "yes" counts as approval.

### Phase 3 — BUILD
- Follow all Coding Principles and Production Standards.
- Write tests alongside the code.
- Run the full test suite before Phase 4.

### Phase 4 — REVIEW & ASSESS

**Resource lifecycle**
- [ ] DB sessions opened via context manager, guaranteed to close
- [ ] External clients (Anthropic, Voyage, YouTube, storage) module-level singletons
- [ ] Celery tasks idempotent and retry-safe; temp media cleaned up

**Path and config safety**
- [ ] All file paths absolute
- [ ] All new config in `.env.example` with description
- [ ] Nothing belonging in `.gitignore` left unignored

**Code cleanliness**
- [ ] No TODO, commented blocks, debug statements
- [ ] No duplicated logic
- [ ] Every new function typed

**Security & compliance (load-bearing for this project)**
- [ ] OAuth tokens read via `decrypt()`; never logged
- [ ] No PII or token in any log line
- [ ] Per-creator isolation enforced on every query
- [ ] YouTube ToS / data-retention rules respected; source media purge honored
- [ ] Honesty text present where required (no virality promises) — structural test green

**Clip-quality correctness (project-specific)**
- [ ] Clip start lands at the setup, not the aftermath (eval green)
- [ ] Scores cite a named principle from `docs/CLIPPING_PRINCIPLES.md`
- [ ] Ranking reflects DNA + (above threshold) preference model
- [ ] Recency decay actually reweights feedback

**Docs**
- [ ] `docs/SOT.md` updated if stack/schema/structure changed
- [ ] `docs/DECISIONS.md` updated if implementation diverged
- [ ] `docs/CLIPPING_PRINCIPLES.md` updated if a new principle is cited
- [ ] `docs/COMPLIANCE.md` updated if data handling changed

**Close out**
- [ ] All acceptance criteria checked off
- [ ] `docs/PROJECT_STATE.md` updated

## 7.2 Slash Commands & Skills to Use

| Command | When to use it |
|---|---|
| `/init` | Once at repo creation. You can also paste Section 8 directly. |
| `/issue-workflow` | At the start of every issue. Walks the Check → Approve → Build → Review loop. |
| `/best-practices` | **Phase 1 of every non-trivial issue** — design patterns, security, cloud, testing, scoring math. This is how we hit "industry standard always." |
| `/production-principles` | Before Phase 1 of any issue touching auth, OAuth tokens, creator data, or money. |
| `/production-standard` | Phase 4 sanity check that code meets the production bar. |
| `/production-code` | Code-level production review of a specific module. |
| `/production-security` | **Every issue touching OAuth tokens, creator analytics, storage, or isolation.** Non-negotiable. |
| `/production-tech` | Picking a new dependency, model, or framework (transcription backend, reranker lib, embeddings). |
| `/production-process` | Adjusting CI/CD, deployment, ops processes. |
| `/code-review` | On the diff before pushing each issue's final commit. `--comment` posts to PR. |
| `/review` | On a PR before merge. |
| `/security-review` | **Every branch before merging to main.** Required for OAuth-token + creator-data class. |
| `/verify` | After Phase 3 to drive the change in the running app (connect → ingest → review). |
| `/run` | Spin up the app for manual driving. |
| `/claude-api` | Any time you write or modify an Anthropic SDK call — keeps prompt caching, model IDs, web-search tool, structured output correct. |
| `/session-start-hook` | Once if running on Claude Code on the web — sets up SessionStart hook for tests/linters in cloud sessions. |
| `/update-config` | Adjusting `.claude/settings.json` permissions, hooks, env vars. |
| `/fewer-permission-prompts` | Periodically; allowlists safe Bash/MCP calls. |
| `/loop` | Recurring dev tasks (e.g., `/loop 30m /check-ci`). Not for scheduled jobs — use Celery beat. |

## 7.3 Pre-Deploy Checklist

**Gate 1 — Automated**
```
pytest
```
Zero failures. Clip-quality eval harness in `tests/eval/` green.

**Gate 2 — Manual smoke test**
```
docker compose up
```
Drive the change at `http://localhost:8000`:
- [ ] Connect YouTube → ingest a video → candidates render
- [ ] Review flow captures feedback; ranking responds
- [ ] Edge cases from acceptance criteria behave (no-cam, captions-only, tiny catalog)
- [ ] Honesty text visible where required; no virality promise anywhere
- [ ] No regression in adjacent flows (auth, DNA, insights)
- [ ] Browser console clean

Only then deploy.

## 7.4 Coding Principles

- **DRY** — extract any logic used more than once
- **SOLID** — invoke `/best-practices` for depth
- **KISS** — simplest solution that meets acceptance criteria; >30-line function = probably split
- **Industry standard ALWAYS** — research current best practice in Phase 1, every issue: FastAPI idioms, Anthropic SDK best practices (prompt caching, web-search tool, structured output), Celery patterns, OAuth 2.0 correctness, pgvector usage. Any deviation gets a `docs/DECISIONS.md` entry with the reason.

## 7.5 Production Standards

- No hardcoded secrets. `.env` only.
- All config via `python-dotenv`; fail fast on missing required.
- `logging` module only — no `print()`.
- Proper HTTP status codes.
- Pydantic on every endpoint.
- Error messages safe — no stack traces, no DB errors to client.
- `requirements.txt` pinned with `==`.
- **Project-specific**: every log line and every LLM prompt reviewed for token/PII leakage; per-creator isolation enforced; no response promises virality.

## 7.6 Testing Rules

- Full pytest run before every issue close
- Tests for new behavior written alongside the code
- 80/20: happy path + load-bearing edge cases
- Tests live in `tests/`, mirror source structure
- No mocking the DB — use a real test Postgres (with pgvector) via docker-compose
- Test the API surface end-to-end with FastAPI `TestClient`
- **YouTube API**: never hit the live API in CI — use recorded fixtures
- **Clip-quality eval harness** — `tests/eval/scenarios/*.yaml`: labeled videos + expected clip windows (setup-start assertion); runs before every `clip_engine/` change

---

# 8. CLAUDE.md Template

Drop into the new repo's root as `CLAUDE.md`.

```markdown
# CLAUDE.md — CREATORCLIP Project Rules

These rules govern every session. They override default Claude Code behavior where noted.

---

## The One Rule Above All Others

On EVERY non-trivial decision — architecture, library, model, scoring math, security
boundary, UX pattern — we ALWAYS research the current industry standard and best
practice FIRST, and justify any deviation in `docs/DECISIONS.md`. We do not build
from memory. We do not guess. This is enforced in Phase 1 (CHECK) of every issue.

---

## Honesty Constraint (must appear in every interface and the system prompt)

CreatorClip predicts fit with your style and audience — it does not promise virality.
Every recommendation is an estimate grounded in your own data, not a guarantee. We
comply with the YouTube API Services Terms of Service at all times.

---

## Read Order (Every Session)

Before writing a single line of code, read these files in order:

1. `docs/SOT.md` — current stack, architecture, file structure
2. `docs/PROJECT_STATE.md` — which issues are done, in progress, or blocked
3. `docs/issues.md` — the issue being worked
4. `docs/DECISIONS.md` — any deviations from the PRD already made
5. `docs/COMPLIANCE.md` — YouTube ToS, data retention, privacy posture
6. `docs/CLIPPING_PRINCIPLES.md` — named principles the engine cites

If any are missing or stale, flag it before proceeding.

---

## Project Structure

Canonical layout is enforced. Do not create files outside it without updating
`docs/SOT.md` first. See `docs/SOT.md` for the full tree.

Rules:
- Python source lives at root, in `routers/`, `youtube/`, `ingestion/`, `dna/`,
  `clip_engine/`, `preference/`, `knowledge/`, `upload_intel/`, `improvement/`,
  or `worker/` — nowhere else
- Frontend assets go in `static/`
- All documentation goes in `docs/`
- Tests mirror source structure in `tests/`

---

## Source of Truth Files

| File | Purpose | Updated when |
|---|---|---|
| `docs/PRD.md` | Requirements | Rarely; only on formal scope change |
| `docs/SOT.md` | Architecture | Any time stack/schema/structure changes |
| `docs/DECISIONS.md` | Deviation log | Any decision diverging from PRD or industry standard |
| `docs/PROJECT_STATE.md` | Progress | Every time an issue is completed |
| `docs/issues.md` | Work queue | Check `[ ]` → `[x]` when an issue is done |
| `docs/COMPLIANCE.md` | YouTube ToS + data handling | Any time data classes / retention / scopes change |
| `docs/CLIPPING_PRINCIPLES.md` | Named principles registry | Any time a new principle is cited |

---

## Issue Workflow — Check → Approve → Build → Review & Assess

One issue at a time. Do not begin Issue N+1 until Issue N clears all four phases.

### Phase 1 — CHECK
Research the industry-standard approach (not memory). Present a brief:

> **Issue N — [title]**
> **Approach:** [specific pattern]
> **Why for this project:** [1–2 sentences]
> **Industry standard checked:** [current best practice confirmed + source]
> **Alternatives ruled out:** [what we considered]
> **Good to go?**

### Phase 2 — APPROVE
Wait for explicit confirmation. Capture changed approaches in `docs/DECISIONS.md`.

### Phase 3 — BUILD
- Follow Coding Principles and Production Standards
- Write tests alongside code
- Run full test suite before Phase 4

### Phase 4 — REVIEW & ASSESS

**Resource lifecycle**
- [ ] DB sessions via context manager, guaranteed to close
- [ ] External clients (Anthropic, Voyage, YouTube, storage) module-level singletons
- [ ] Celery tasks idempotent + retry-safe; temp media cleaned up

**Path and config safety**
- [ ] All paths absolute
- [ ] All new config in `.env.example` with description
- [ ] Nothing belonging in `.gitignore` left unignored

**Code cleanliness**
- [ ] No TODO, commented blocks, debug
- [ ] No duplicated logic
- [ ] Every new function typed

**Security & compliance (load-bearing)**
- [ ] OAuth tokens read via decrypt(); never logged
- [ ] No PII or token in any log line
- [ ] Per-creator isolation enforced on every query
- [ ] YouTube ToS / retention respected; source media purge honored
- [ ] No virality promise anywhere (structural test green)

**Clip-quality correctness**
- [ ] Clip start at the setup, not the aftermath (eval green)
- [ ] Scores cite a named principle from `docs/CLIPPING_PRINCIPLES.md`
- [ ] Ranking reflects DNA + (above threshold) preference model
- [ ] Recency decay actually reweights feedback

**Docs**
- [ ] `docs/SOT.md` updated if stack/schema/structure changed
- [ ] `docs/DECISIONS.md` updated if implementation diverged
- [ ] `docs/CLIPPING_PRINCIPLES.md` / `docs/COMPLIANCE.md` updated as needed

**Close out**
- [ ] All acceptance criteria checked off
- [ ] `docs/PROJECT_STATE.md` updated

---

## Coding Principles

> Invoke `/best-practices` for deep guidance — EVERY non-trivial issue.

### DRY
Extract any logic used more than once.

### SOLID
Single Responsibility, Open/Closed, Liskov, Interface Segregation, Dependency Inversion.

### KISS
Simplest solution wins. No premature abstractions. >30-line function = probably split.

### Industry Standard ALWAYS
- Research current best practice in Phase 1 of every issue — never build from memory
- FastAPI-idiomatic backend; Celery task patterns; OAuth 2.0 correctness
- Anthropic SDK best practices (prompt caching, web-search tool, structured output, token limits)
- pgvector usage patterns; recency-decayed reranking standards
- Any deviation requires a `docs/DECISIONS.md` entry

---

## Production Standards

- No hardcoded secrets. `.env` only, never committed.
- All config via `python-dotenv`. Fail fast on missing required.
- `logging` module only — no `print()`.
- Proper HTTP status codes (200, 400, 401, 404, 422, 500/502).
- Pydantic on every endpoint.
- Error messages safe.
- `requirements.txt` pinned with `==`.
- Project-specific: every log line and every LLM prompt reviewed for token/PII
  leakage; per-creator isolation enforced; no response promises virality.

---

## Testing Rules

- Full pytest run before every issue close
- Tests for new behavior written with the code
- 80/20: happy path + load-bearing edges
- `tests/` mirrors source structure
- No DB mocking — use real Postgres (+ pgvector) via docker-compose
- Never hit the live YouTube API in CI — use recorded fixtures
- API-surface end-to-end with FastAPI `TestClient`
- Clip-quality eval harness in `tests/eval/` — labeled videos + expected clip
  windows (setup-start assertion); runs before every `clip_engine/` change

---

## Production Deployment

Runs in Docker Compose on <PLACEHOLDER: target host> with traffic via Cloudflare
Tunnel to <PLACEHOLDER: domain>. Transcription needs a GPU or a hosted API —
resolve before first deploy.

Deploy:
\`\`\`bash
ssh <user>@<host>
cd ~/creatorclip
git pull origin main
docker compose pull && docker compose up -d
\`\`\`

Status:
\`\`\`bash
docker compose ps
docker compose logs --tail 100 app worker
\`\`\`

### Pre-Deploy Checklist
Gate 1: `pytest` (including `tests/eval/`) — zero failures.
Gate 2: `docker compose up` locally, drive connect → ingest → review, confirm
happy path + edge cases + honesty text visibility + no console errors.

---

## Code Style

- Python: PEP 8, max 100 chars, type hints on every signature
- HTML/JS: vanilla, 2-space indentation
- SQL: uppercase keywords, lowercase identifiers, parameterized queries always
- Comments only when WHY is non-obvious
- Naming: snake_case Python, camelCase JS, UPPER_SNAKE constants

---

## Architecture Constraints

- Backend: FastAPI + Python 3.12+
- Task queue: Celery + Redis (durable video jobs)
- LLM: Anthropic SDK with prompt caching mandatory; web-search tool for live research; tokens logged after every call
- Embeddings: Voyage AI → pgvector
- DB: PostgreSQL 16 + pgvector + Alembic
- Transcription: WhisperX (word-level) with hosted fallback behind config
- Video: ffmpeg cut + 9:16 active-speaker reframe
- Storage: Cloudflare R2 (S3-compatible); local disk in dev
- Auth: Google OAuth 2.0 (YouTube scopes) + session JWT; tokens Fernet-encrypted
- Preference model: recency-decayed reranker (LightGBM/logistic), not a fine-tuned LLM
- Frontend: vanilla HTML/CSS/JS (review-UI framework is a flagged DECISIONS candidate)
- Containerization: Docker Compose
- Deployment: Cloudflare Tunnel from a host machine

Deviations require a `docs/DECISIONS.md` entry before implementation.

---

## Clip-Engine Rules (project-specific)

- The engine clips the SETUP, not the aftermath — backward look from peak in a 60–90s window
- Every clip score cites a named principle from `docs/CLIPPING_PRINCIPLES.md`
- Scoring is against THIS creator's DNA + audience, never a generic virality score
- The preference model weights recent feedback more heavily (exponential recency decay)
- Personalization threshold is communicated honestly; below it, ranking falls back to DNA + signals
- No interface or response ever promises virality

---

## Pre-Public-Launch Requirements

MVP onboards a small, invited set of creators. Before any public launch:

- Lock `ALLOWED_ORIGINS` to the production domain; disable `/docs` in production
- Per-creator rate limiting + usage quotas enforced before each LLM/render job
- YouTube data-retention/refresh fully compliant; documented in `docs/COMPLIANCE.md`
- `TOKEN_ENCRYPTION_KEY` rotation runbook written
- Terms of Service + Privacy Policy pages (Google OAuth verification requires them)
- Google OAuth app verification completed for the requested scopes
- Account-deletion endpoint (GDPR/CCPA right-to-erasure), incl. token revocation + media purge
- Billing + plan-tier columns wired; Stripe gated behind all of the above
- Eval harness hardened with adversarial/edge cases
```

---

## End

Once `<PLACEHOLDER>` blocks in Section 5 are filled and the new repo exists:

1. Copy this file to the new repo as `docs/KICKSTART.md`
2. Promote Section 2 → `docs/PRD.md`, Section 3 → `docs/SOT.md`, Section 4 → `docs/CLIPPING_PRINCIPLES.md`, Section 6 → `docs/issues.md`, Section 8 → `CLAUDE.md`
3. Create empty `docs/DECISIONS.md`, `docs/PROJECT_STATE.md`, `docs/COMPLIANCE.md`
4. Run `/init` if you want Claude Code to scaffold further
5. Run `/issue-workflow` and begin Issue 1



# ADDITIONAL NOTES OF ASPIRATION
- have a hotkey to clip the last x seconds while recording or streaming
- able to access your audio and video and manage it through the app (kinda like OBS)
- when you edit shorts or view the shorts, have a left and right arrow to look at them one by one, and you then have a mini editing tool to crop, clip, cut, subtitle, change fonts, edits, etc. So that if you DID want to change it at all (and give it feedback which would be like a comment box at the bottom), you can do that there as well.
- potentially turn this into a more editor / analyzer / video and audio management tool all in one. allows you to really keep everything together.