# CreatorClip / AutoClip

**The only AI editor that truly knows your channel** — it learns your style from your own YouTube analytics, adapts as you evolve, and turns your long-form videos into short-form clips.

> **Status: ✅ Live at [autoclip.studio](https://autoclip.studio)** — a real, deployed SaaS with billing, subscriptions, staging/prod environments, and continuous deployment on every push to `main`.

CreatorClip (product name **AutoClip**) is an AI-powered short-form video auto-clipper. You link a YouTube channel, it studies how *your* channel actually performs, and it ingests long-form videos to automatically produce ranked, render-ready vertical (9:16) clips — with animated captions, active-speaker reframing, and AI-generated titles/thumbnails/hooks — scored against your channel's own "DNA" rather than a generic virality model.

**Honesty constraint baked into the product:** AutoClip predicts *fit with your style and audience* — it never promises virality. Every recommendation is an estimate grounded in your own data.

---

## Screenshots

<!-- TODO: add a demo GIF / Loom walkthrough of the full upload → clip → render flow here -->

| Link a video | Insights |
|---|---|
| ![Link a YouTube video](./link%20video.png) | ![Channel insights](./insights%201.png) |

*(Static UI captures from the live app. Drop a short Loom/GIF above to show the end-to-end pipeline in motion.)*

---

## What it does

1. **Learns your channel.** On onboarding, it pulls your YouTube Analytics (retention curves, demographics, activity windows) and Data API metadata, then builds a versioned **channel "DNA" profile** — a semantic model of your style, hooks, pacing, and what your audience actually watches.
2. **Ingests long-form video** through a durable async pipeline: download → audio extraction → transcription → energy/signal analysis → candidate detection → DNA-aware scoring → render.
3. **Finds and scores clips** by looking *backward from the peak* to start each clip at the setup, not the aftermath. Every score cites a named clipping principle and is ranked against your DNA plus a per-creator, recency-decayed preference model — not a one-size-fits-all score.
4. **Renders finished shorts** with ffmpeg: 9:16 cut, two-pass loudness normalization, active-speaker reframing (MediaPipe), and animated "bold pop" captions (libass).
5. **Ships extras a creator needs:** AI titles, thumbnails, hooks, chapters, upload-timing intelligence, an agentic Pro chat assistant scoped to your data, and one-click publishing back to YouTube.

---

## Key features

- **Channel "DNA" module** (`dna/`) — append-only, versioned per-creator style identity built from real analytics; Voyage AI embeddings stored in pgvector for semantic retrieval.
- **Async clip engine** (`clip_engine/`) — candidate detection, setup-aware windowing, principle-citing scoring/ranking, ffmpeg render, active-speaker reframe, and animated captions.
- **Transcription** (`ingestion/`) — Deepgram nova-3 by default (with MIP opt-out enforced); WhisperX (word-level, self-hosted) and AssemblyAI are config-selectable backends.
- **Semantic embeddings + retrieval** — Voyage AI (`voyage-3.5`) → PostgreSQL + pgvector, one store for relational data and vectors.
- **Per-creator preference model** (`preference/`) — a LightGBM / logistic reranker trained per session with exponential recency decay, so recent feedback reweights ranking.
- **AI knowledge tools** (`knowledge/`) — titles, thumbnails, hooks, chapters, clip captions/explanations via the Anthropic SDK with mandatory prompt caching.
- **Agentic Pro chat** (`chat/`) — a streaming, tool-using assistant scoped to the creator's own data (SSE, client-side tools, bounded tool loops).
- **Billing & subscriptions** (`billing/`) — Stripe-backed usage credits sold as per-input-minute "minute packs" with a volume taper, plus a ledger and refund flow. This is a real paid product.
- **YouTube OAuth 2.0** (`youtube/`) — Google OAuth with YouTube scopes; tokens Fernet-encrypted at rest (MultiFernet, with a zero-downtime key-rotation runbook); Analytics + Data API v3 clients with quota handling.
- **Production hardening** — per-creator Postgres Row-Level Security, rate limiting, structured logging with PII/token redaction, Prometheus metrics, Sentry, and OpenTelemetry tracing.

---

## Architecture

A FastAPI app serves the React SPA and API; long-running video work is offloaded to Celery workers over Redis so uploads never block a request. PostgreSQL (with the pgvector extension) is the single store for both relational data and embeddings. Media lives in Cloudflare R2. In production the app is fronted by a Cloudflare Tunnel — no inbound ports are open on the VM.

```
                         ┌──────────────────────────┐
   Browser ──HTTPS──▶    │  Cloudflare Tunnel        │
                         └───────────┬──────────────┘
                                     ▼
        ┌───────────────────────────────────────────────┐
        │  FastAPI app  (main.py, routers/)              │
        │  • serves React/TS SPA (frontend/dist)         │
        │  • Google OAuth 2.0 + session JWT              │
        │  • Stripe billing/webhooks                     │
        └──────┬───────────────────────────────┬────────┘
               │ enqueue jobs                   │ read/write
               ▼                                ▼
        ┌───────────────┐              ┌────────────────────────┐
        │ Redis         │◀────────────▶│ PostgreSQL 16          │
        │ Celery broker │              │ + pgvector (embeddings)│
        │ + beat sched. │              │ + per-creator RLS      │
        └──────┬────────┘              └────────────────────────┘
               │ consume
               ▼
   ┌──────────────────────────────────────────────────────────┐
   │ Celery workers — the async video pipeline                │
   │                                                          │
   │  ingest → transcribe → signals → DNA → clip → render     │
   │    │         │           │        │       │       │      │
   │  yt/R2   Deepgram/     librosa  Voyage  scoring  ffmpeg   │
   │          WhisperX               +pgvec  +pref    +MediaPipe│
   │                                          model   +libass  │
   └──────────────────────────────┬───────────────────────────┘
                                  ▼
                    Cloudflare R2 (source + rendered clips)
```

Cross-cutting: Anthropic Claude (per-task model registry — `sonnet-4-6` for reasoning, `haiku-4-5` for cheap classification) powers DNA, knowledge tools, chat, and analysis; Prometheus `/metrics`, Sentry, and OpenTelemetry provide observability.

---

## Tech stack

**Backend**
- Python 3.12, FastAPI, Uvicorn, Pydantic / pydantic-settings
- Celery + Redis (durable job queue, RedBeat scheduler)
- SQLAlchemy 2 (async) + Alembic (45 migrations), PostgreSQL 16 + pgvector
- Anthropic SDK (Claude), Voyage AI embeddings
- Deepgram / WhisperX / AssemblyAI (transcription), librosa (audio signals)
- ffmpeg, OpenCV, MediaPipe (reframe), pysubs2/libass (captions)
- LightGBM + scikit-learn (preference reranker)
- Stripe (billing), Resend (transactional email), Cloudflare R2 / boto3 (storage)
- cryptography (Fernet token encryption), PyJWT, bcrypt, slowapi (rate limiting)
- Prometheus client, Sentry SDK, OpenTelemetry (+ OpenLLMetry for GenAI spans)

**Frontend** (`frontend/`)
- React 19 + TypeScript, Vite
- Tailwind CSS v4 (shadcn-style components)
- TanStack Query v5, React Router v7
- Vitest + React Testing Library (unit), Playwright (E2E / visual)

**Infra / Ops**
- Docker + Docker Compose (dev, staging, prod), multi-stage image (backend + built SPA)
- Cloudflare Tunnel (prod ingress), autoheal, healthchecks
- GitHub Actions CI → GHCR image → auto-deploy to prod on push to `main`
- A written Helm chart (`deploy/charts/`) for a future GKE Autopilot + Cloud SQL + KEDA scale path
- Quality gates: ruff, mypy (pydantic plugin), bandit, pip-audit, ~79% coverage, weekly mutation testing

---

## Getting started (local dev)

**Prerequisites:** Docker + Docker Compose. (ffmpeg, fonts, and the ML deps are baked into the image.)

```bash
# 1. Clone
git clone https://github.com/reese8272/creatorclip.git
cd creatorclip

# 2. Configure environment
cp .env.example .env
#   then fill in the required keys (see below)

# 3. Build and run the full stack (app + worker + postgres + redis)
docker compose up --build

# 4. Apply database migrations
docker compose exec app alembic upgrade head
```

The API + SPA are served at `http://localhost:8000` (interactive docs at `/docs` in dev).

**Minimum required config** (see `.env.example` for the fully-documented list):

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude — DNA, knowledge tools, chat, scoring |
| `VOYAGE_API_KEY` | Embeddings (or use the local fallback) |
| `DATABASE_URL` | `postgresql+psycopg://…/creatorclip` |
| `REDIS_URL` | Celery broker + cache |
| `GOOGLE_OAUTH_CLIENT_ID` / `_SECRET` / `OAUTH_REDIRECT_URI` | YouTube OAuth |
| `TOKEN_ENCRYPTION_KEY` | Fernet key for encrypting YouTube tokens at rest |
| `JWT_SECRET_KEY` | Session JWT signing |
| `ALLOWED_ORIGINS` | CORS allowlist (never `*` in prod) |
| `STORAGE_BACKEND` | `local` in dev; **must be `r2`** in prod |

Frontend-only dev (against a running API):

```bash
cd frontend
npm install
npm run dev        # Vite dev server
npm test           # Vitest
```

Tests (backend):

```bash
pytest                       # unit lane (DB/Redis mocked, no Docker needed)
pytest -m integration        # integration lane (real Postgres + pgvector)
```

---

## Project structure

```
.
├── main.py               # FastAPI app entrypoint (serves API + React SPA)
├── config.py             # pydantic-settings config (fail-fast on missing keys)
├── db.py / models.py     # async SQLAlchemy engine, sessions (RLS), ORM models
├── auth.py / api_key.py  # OAuth session JWT + API-key auth
├── crypto.py             # Fernet token encryption at rest
├── routers/              # FastAPI route modules (auth, billing, clips, chat, …)
│
├── ingestion/            # audio extraction, transcription, energy/signal analysis
├── clip_engine/          # candidates → window → scoring → ranking → render/reframe/captions
├── dna/                  # channel "DNA" — versioned style identity + embeddings
├── preference/           # LightGBM recency-decayed per-creator reranker
├── knowledge/            # AI titles, thumbnails, hooks, chapters, captions
├── analysis/ improvement/# LLM analysis + improvement briefs
├── chat/                 # agentic streaming Pro chat assistant (tool use)
├── upload_intel/         # best-time-to-upload intelligence
├── youtube/              # OAuth, Analytics API, Data API v3, publish, quota
├── billing/              # Stripe client, minute packs, ledger, refunds
├── worker/               # Celery app, tasks, beat schedule, R2 storage
│
├── frontend/             # React 19 + TypeScript SPA (Vite, Tailwind v4)
├── alembic/              # database migrations (45)
├── deploy/               # Helm chart for the future GKE scale path
├── docs/                 # source-of-truth docs (SOT, PRD, DECISIONS, COMPLIANCE, …)
├── tests/                # pytest suites (unit + integration + clip-quality eval)
│
├── docker-compose.yml         # local dev
├── docker-compose.staging.yml # staging
├── docker-compose.prod.yml    # production (Cloudflare Tunnel + autoheal)
└── Dockerfile                 # multi-stage: deps + Vite SPA build + runtime
```

---

## Compliance

CreatorClip operates within the YouTube API Services Terms of Service: OAuth tokens are encrypted at rest and never logged, per-creator data isolation is enforced at the database level (Row-Level Security), source media is purged on a retention timer, and account deletion (token revocation + media purge) is a first-class right-to-erasure flow. See `docs/COMPLIANCE.md`.
