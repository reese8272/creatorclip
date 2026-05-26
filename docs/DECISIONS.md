# CreatorClip — Design Decisions Log

Entries are added whenever an architectural decision is made, a library is chosen, or
implementation diverges from the PRD. Every entry must include what, why, source/evidence, and date.

---

## 2026-05-25 — Project Kickoff Decisions

### North Star Sentence

**What**: Settled on the north star: *"The only AI editor that truly knows your channel —
it learns your style from your own analytics, adapts as you evolve, and keeps you ahead of
the algorithm."*

**Why**: The product is broader than clipping — it's a full analyzer + advisor that adapts to
the creator's evolving style and keeps them informed about algorithm changes. The sentence must
communicate the personalization flywheel, not just the clip output.

**Source**: Creator (owner) input, 2026-05-25.

---

### Review UI: Single Player + Next

**What**: The review interface is a single-player + Next button, not a swipe-stack.

**Why**: Single-player makes precision trim handle interaction easier and more reliable.
Swipe-stack UX is faster for bulk review but sacrifices the trim-delta signal, which is the
strongest *timing* feedback. Trim handles are the visual centerpiece.

**Source**: Creator input, 2026-05-25.

---

### Pricing Model: Usage-Based Tiers (Research Pending)

**What**: Pricing is usage-based with tiered subscription floors, similar to Anthropic's own
model. A flat "low cap" monthly plan would frustrate prolific creators. A pure per-video model
adds friction.

**Why**: Creators' output volume varies enormously. A tiered usage model (e.g., base plan
includes N tokens/videos, then pay-as-you-go overage) aligns cost with value and doesn't
block high-output creators.

**Research needed**: Best practices for usage-based SaaS pricing + Stripe metered billing
implementation. Must be decided before public launch. Stripe + usage metering is the
industry-standard path.

**Source**: Creator input, 2026-05-25. Research not yet completed — see `docs/SOT.md` Known
Production Gaps.

---

### Production Deployment: GKE Autopilot + Helm + KEDA

**What**: GKE Autopilot is the production K8s platform. Helm charts in
`deploy/charts/creatorclip/`. KEDA ScaledObject autoscales Celery workers on Redis
queue depth. PgBouncer sidecar handles connection pooling. Cloud SQL for PostgreSQL 16
(pgvector enabled). GCP Secret Manager + External Secrets Operator for secrets.

**Why GKE Autopilot over EKS/DO**:
- No node management — Google provisions and upgrades nodes automatically
- Cloud SQL for PostgreSQL 16 has first-class pgvector support (vs. RDS which requires
  custom parameter groups and is slower to enable extensions)
- GCP Secret Manager + Workload Identity = cleanest managed-secrets story without extra agents
- Spot node pools for transcription workers available when we add WhisperX
- Familiarity: same provider as Cloudflare Tunnel integration already in dev

**KEDA vs HPA-only**: HPA on CPU is insufficient for Celery — a backlogged queue does
not spike CPU until workers are already overwhelmed. KEDA's `redis-listLength` trigger
scales on actual work queued, providing proactive scaling.

**PgBouncer sidecar vs RDS Proxy**: Sidecar eliminates the network hop to a separate
pooler, is free, and transaction mode allows up to 25 upstream connections per pod
(→ 750 at 30 pods, well within Cloud SQL's 1,000 limit).

**Source**: Compared providers on pgvector support, managed node overhead, secrets
integration, and community KEDA+Celery patterns. 2026-05-26.

---

---

### OAuth HTTP Calls: httpx Instead of google-auth-oauthlib

**What**: The OAuth token exchange, token refresh, userinfo, and YouTube Channels calls are
implemented directly with `httpx.AsyncClient` rather than using `google-auth-oauthlib` /
`google-api-python-client`.

**Why**: `google-auth-oauthlib` is synchronous — using it in an async FastAPI handler requires
`asyncio.run_in_executor()` boilerplate. The OAuth endpoints are simple POST/GET calls that
`httpx` handles natively in 3–4 lines each. Fewer dependencies, fully async, and easier to
test (patch the `_call_*` helpers rather than monkey-patching Google internals).

**Source**: httpx docs; FastAPI async best practices. Confirmed: no Google library provides
a first-party async implementation as of 2026-05.

---

### Numeric Thresholds Set as Defaults

**What**: The following defaults were set based on the kickstart document's suggested values:

| Variable | Default | Rationale |
|----------|---------|-----------|
| `CLIPS_PER_VIDEO_DEFAULT` | 8 | Enough candidates to cover diverse moments without overwhelming review |
| `MIN_VIDEOS_FOR_DNA` | 10 | Minimum for meaningful top/bottom performer analysis |
| `MIN_SHORTS_FOR_DNA` | 5 | Minimum for Shorts-specific pattern extraction |
| `PERSONALIZATION_THRESHOLD_LABELS` | 20 | Minimum feedback volume for reranker to produce meaningful signal |

All are environment-configurable and can be tuned once real usage data exists.

**Source**: Kickstart document defaults; no external research needed (tunable post-launch).

---

### Postgres Docker Image: pgvector/pgvector:pg16

**What**: Using `pgvector/pgvector:pg16` in docker-compose instead of `postgres:16` + manual
extension install.

**Why**: The official pgvector Docker image pre-installs the extension, eliminating the
`CREATE EXTENSION` step that frequently trips up fresh setups. Same underlying Postgres 16;
no functional difference.

**Source**: pgvector GitHub README recommendation, standard practice.

---

### Transcription Backend: Deepgram as MVP Default

**What**: `TRANSCRIPTION_BACKEND` defaults to `"deepgram"` (hosted API). WhisperX remains
available via `TRANSCRIPTION_BACKEND=whisperx` for self-hosted GPU deployments. The
`DEEPGRAM_API_KEY` field is already in Settings (optional, empty default).

**Why**: No GPU infrastructure exists for the MVP. Deepgram's Nova-3 model provides
word-level timestamps, speaker diarization, and competitive accuracy without the operational
overhead of managing a GPU box or container. WhisperX is preserved as a config-selectable
path for production cost optimisation once volume justifies the GPU spend.

**Source**: Resolves the "Transcription compute" open research item. Decision: hosted API
for MVP, self-hosted as a future cost lever. 2026-05-25.

---

### asyncio.run() in Celery Tasks

**What**: Celery task functions (`ingest_video`, `transcribe_video`, `build_signals`) use
`asyncio.run()` to call async SQLAlchemy helpers. Each task creates a fresh event loop
per invocation.

**Why**: Celery workers are process-based and synchronous by default. The project's
SQLAlchemy setup is async-only (`create_async_engine`). The alternatives — a parallel sync
engine or `nest_asyncio` — add more complexity. `asyncio.run()` is the documented SQLAlchemy
approach for non-async call sites, and Celery workers run in their own processes so there is
no event-loop conflict.

**Source**: SQLAlchemy async docs "Using Asyncio" section; Celery docs recommend keeping
task functions synchronous. 2026-05-25.

---

## 2026-05-26 — Billing: Minute Packs (replaces subscription tiers)

**What**: Billing model is pre-paid minute packs, not subscriptions. `Creator.plan_tier` and
`Creator.subscription_status` replaced with `Creator.minutes_balance` (int) and a
`minute_packs` ledger table. Stripe Checkout in one-time payment mode — no subscriptions,
no Billing Meters. Five purchasable packs (Starter 200 min → Studio 5,000 min) with
programmatically-verified volume discounts. 60-minute free trial granted on first login.
Minutes deducted atomically at ingest via `UPDATE … WHERE minutes_balance >= X RETURNING`.

**Why**: Subscriptions require monthly commitment — a poor fit for creators who post
episodically. Minute packs let creators pay for exactly what they use and never expire,
which is a better conversion funnel ("try 60 free minutes, buy more when you need them").
One-time Stripe Checkout is also significantly simpler to implement than subscriptions
(no Customer Portal, no dunning, no invoice lifecycle).

**Source**: Product decision, 2026-05-26. Feature branch `claude/zealous-wozniak-5KVb7`
merged into main.

---

## 2026-05-26 — Beta deployment: VM + Docker Compose, not Kubernetes

**What**: BETA_DEPLOYMENT phase (Issues 23–28) runs on a single cloud VM (DigitalOcean
Droplet, 4 vCPU / 8 GB RAM) with Docker Compose + Cloudflare Tunnel, not Kubernetes.
This is a scoped exception to the "Docker Compose = dev only" stance in `docs/SOT.md`.

**Why**: Kubernetes is right for 10k+ scale but adds unnecessary operational complexity
for a close-friends beta with < 10 users. The existing CI/CD pipeline (`deploy.yml`)
already handles image build, SSH deploy, and DB migration — no K8s tooling needed for
beta. `docs/SOT.md` still targets GKE Autopilot for production (Issue 22 Helm charts
are ready); this is a scoped beta exception only.

**Source**: Practical deployment gap analysis, 2026-05-26. Production deployment phase
(Issues 29–30) retains the Kubernetes target.

---

## 2026-05-26 — Clip engine: extend end_s for early-peak candidates

**What changed**: `clip_engine/candidates.py` — `end_s` now computed as
`min(duration_s, max(peak_s + POST_PEAK_S, setup_start_s + MIN_CLIP_S))` instead of
`min(duration_s, peak_s + POST_PEAK_S)`.

**Why**: Adversarial eval fixture `peak_very_early` surfaced a bug: when a retention spike
occurs near t=0 (e.g. 12s), the setup-to-post-peak window is only ~27s, below `MIN_CLIP_S`
(30s). The candidate was silently discarded. The fix extends `end_s` just enough to meet the
minimum, so early-video hooks are never dropped.

**Source**: `tests/eval/scenarios/peak_very_early.yaml` — engine returned 0 candidates.
Debug confirmed `end_s - setup_start_s = 27.5 < 30.0`. 2026-05-26.
