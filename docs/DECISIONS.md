# CreatorClip — Design Decisions Log

Entries are added whenever an architectural decision is made, a library is chosen, or
implementation diverges from the PRD. Every entry must include what, why, source/evidence, and date.

---

## 2026-05-28 — Issue 39: Celery event-loop strategy

### What changed
- Replaced per-task `asyncio.run(...)` with a per-worker-process singleton event loop
  installed by the `worker_process_init` Celery signal.
- Added `db.recreate_engine()` and `db.dispose_engine()` so the SQLAlchemy async engine
  + asyncpg pool can be rebound to the worker child's loop after fork, and cleanly
  disposed on `worker_process_shutdown`.
- Added `worker.celery_app.run_async(coro)` — used by every task in `worker/tasks.py`
  (11 sites) instead of `asyncio.run`. Falls back to `asyncio.run` when no worker loop
  is installed (unit-test invocation path).
- `worker/tasks.py` now does `import db` and uses `db.AsyncSessionLocal(...)` so that
  rebinding the module-global sessionmaker in `db.recreate_engine()` is visible to
  task bodies at call time (`from db import AsyncSessionLocal` would capture the
  stale reference).

### Why
Every Celery task used to call `asyncio.run(_some_async(...))`, which creates a fresh
event loop per task. The first task in a worker process would bind the engine's
asyncpg pool to its loop; subsequent tasks would receive a *different* loop and hit
the classic `Future attached to a different loop` errors plus pool churn (each loop
discarded, connections re-handshaked). Under concurrent load this was a SEV-1 because
it manifests as intermittent worker failures rather than a single reproducible bug.

The fix pins one loop per worker process for the worker's lifetime and binds the
engine to it once. This is the canonical FastAPI + Celery + async-SQLAlchemy pattern;
SQLAlchemy's own docs spell out that async engines must be created *after* fork
because the asyncpg connection pool cannot survive across processes.

### Alternatives ruled out
- **`celery-pool-asyncio` / `celery-aio-pool`**: third-party pool replacements. Smaller
  community, replace the entire pool model, and unnecessary — our concurrency model is
  per-process prefork and we don't need cooperative I/O multiplexing inside a task.
- **`asgiref.async_to_sync`**: caches a loop per thread but does not address the
  engine-binding-on-fork problem. Same bug class would resurface.
- **Lazy `get_engine()` inside every coroutine**: scatters the fix across every task
  body and makes the contract implicit; one init signal is far easier to audit.
- **`gevent` / `eventlet` worker pool**: would require monkey-patching the entire
  stack; out of scope.

### Tradeoffs
- Each worker child holds a long-lived loop + pool. Trivial memory cost vs. eliminating
  the pool-rebind cost on every task.
- Engine pool sizing budget is unchanged: `concurrency × (pool_size + max_overflow)`,
  currently `concurrency × 30`. If we raise Celery concurrency, we must size the
  Postgres `max_connections` accordingly. Not a regression — the pre-fix code had the
  same upper bound; it just churned the pool more.
- `worker_process_init` calls `db.recreate_engine()` after fork. We use
  `engine.sync_engine.dispose(close=False)` to abandon (not close) any inherited
  parent connections so we don't yank file descriptors out from under the parent.
  In practice the parent has no open connections at fork time (it only imports the
  modules), but this is the SQLAlchemy-blessed safe default.

### Source / evidence
- SQLAlchemy 2.0 docs — "Using asyncio with multiprocessing":
  https://docs.sqlalchemy.org/en/20/core/pooling.html#using-connection-pools-with-multiprocessing-or-os-fork
- Celery worker signals reference:
  https://docs.celeryq.dev/en/stable/userguide/signals.html#worker-process-init
- Prior incident pattern: `Future attached to a different loop` is the symptom called
  out in Issue 39's spec; verified the cause by reading `worker/tasks.py:49–135` and
  `db.py:8` before the fix.

### Files
- `db.py` — added `_make_engine`, `recreate_engine`, `dispose_engine`.
- `worker/celery_app.py` — singleton `_LOOP`, `run_async`, init/shutdown signal hooks.
- `worker/tasks.py` — 11 × `asyncio.run` → `run_async`, 16 × `AsyncSessionLocal` →
  `db.AsyncSessionLocal`.
- `tests/test_celery_event_loop.py` — pins loop-reuse, fallback, init/shutdown,
  engine-rebind invariants (5 tests).
- `tests/test_retention_tasks.py`, `tests/test_pipeline_trigger.py`,
  `tests/test_oauth_lifecycle.py` — updated patch targets from `worker.tasks.*` to
  `db.AsyncSessionLocal` / `worker.tasks.run_async` to match the new import surface.

---

## 2026-05-28 — Issue 37: External SDK Timeouts + Retry-with-Backoff

### Anthropic SDK (`anthropic==0.40.0`)

**What**: Replaced per-call `Anthropic(...)` / `AsyncAnthropic(...)` construction in `dna/brief.py`, `improvement/brief.py`, and `clip_engine/scoring.py` with module-level singletons (`_ANTHROPIC`) constructed once from `config.settings`. Configured `timeout=httpx.Timeout(60.0, connect=10.0)` and `max_retries=2`. For `improvement/brief.py`, the web_search call uses `_ANTHROPIC.with_options(timeout=120.0)` per-call because web_search tool agentic loops routinely exceed 60s.

**Why**: The Anthropic Python SDK docs (sdk.anthropic.com/python) recommend constructing the client once and reusing it. Per-call construction wastes connection pool setup on every invocation. The 60s read timeout covers standard Claude calls; 120s override on the web_search path is needed because the tool loop typically takes 30–90s per the Anthropic docs on `web_search_20250305`. connect_timeout of 10s is an industry-standard value for TLS handshakes. `max_retries=2` uses the SDK's built-in exponential backoff on transient 529/500 errors.

**Source**: Anthropic SDK docs — `httpx.Timeout`, `max_retries`, `with_options`; Anthropic web_search tool docs noting agentic loop latency.

### Stripe SDK (`stripe==11.4.0`)

**What**: Added `stripe.max_network_retries = 3` at module level in `billing/stripe_client.py` and promoted `StripeClient` to a module-level singleton `_STRIPE`.

**Why**: Stripe's official Python library docs state that `max_network_retries` enables automatic retry with exponential backoff on 429 and 5xx errors. The default is 0 (no retries). Setting 3 is the Stripe-recommended value for production. The default 80s socket timeout is appropriate for Checkout session creation and is not overridden.

**Source**: Stripe Python library docs — `stripe.max_network_retries`; Stripe best practices guide.

### Voyage AI (`voyageai==0.3.2`)

**What**: Added lazy-initialized module-level singleton `_VOYAGE` (via `_voyage()` accessor) in `dna/embeddings.py` with `timeout=30`. Wrapped embedding calls in a `_embed()` function decorated with `@tenacity.retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))`. Added `tenacity==9.1.4` to `requirements.txt`.

**Why**: The voyageai SDK does not support built-in retries. Tenacity is the Python community standard for retry-with-backoff (used by Google, LangChain, etc.). Exponential backoff with min=1s/max=10s is the standard pattern for rate-limit-friendly API retries. The singleton is lazy (not eager at import time) because voyageai.Client validates the API key at construction, which would fail in test environments without `VOYAGE_API_KEY` set.

**Source**: Tenacity docs (tenacity.readthedocs.io); voyageai Python client source (`voyageai/_base.py`).

### boto3 / Cloudflare R2 (`boto3==1.35.54`)

**What**: Replaced per-call `boto3.client(...)` with a lazy module-level singleton `_R2` (via `_r2()` accessor) in `worker/storage.py`. Configured `botocore.config.Config(retries={"mode": "adaptive", "max_attempts": 5}, connect_timeout=10, read_timeout=60)`.

**Why**: boto3 docs recommend reusing the client to share the connection pool. Adaptive retry mode (botocore docs) uses a token bucket to avoid retry storms on throttling; `max_attempts=5` is the botocore recommended value for production S3 workloads. `connect_timeout=10` / `read_timeout=60` match AWS SDK best practices. The singleton is lazy because boto3 validates the endpoint URL at construction, which fails if `R2_ACCOUNT_ID` is empty (test environment).

**Source**: botocore Config docs; AWS SDK best practices guide for S3 retry configuration.

### Deepgram / WhisperX (`ingestion/transcribe.py`)

**What**: No change made. WhisperX is local-only (no network timeout relevant). The Deepgram fallback path uses `deepgram-sdk` which is commented out of `requirements.txt` and unreachable in all environments. There is no httpx-based fallback path.

**Why**: Implementing a timeout on an unreachable code path would be dead code. Noted here to close the loop on the Issue 37 audit.

**Date**: 2026-05-28

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

---

## 2026-05-27 — Issue 31: Operability kit (secrets registry, preflight doctor, deploy hardening, auto-heal)

### Secrets storage: plain gitignored `.env` + registry (not SOPS+age)

**What**: Secrets are kept in gitignored `.env` files (local + VM `/opt/autoclip/.env`, chmod 600),
documented in a single registry at `docs/SECRETS.md`. SOPS+age (encrypted-in-git) was considered
and deferred.

**Why**: For a <10-user close-friends beta on a single VM, plain `.env` with strict file
permissions is the industry-accepted baseline and matches the existing setup with zero new
tooling. SOPS+age adds a keypair to manage and deploy-step changes — robustness we don't need
until multi-operator or compliance requirements appear. Logged as the explicit upgrade path.

**Source**: Web research on single-VM Docker Compose secret management (GitGuardian; Docker docs;
cmmx.de SOPS/age guide), 2026-05-27. Owner chose plain `.env` + registry.

### Pre-existing bug fixed: `routers/clips.py` imported deleted `billing.tiers`

**What changed**: `routers/clips.py` imported `require_render` from `billing.tiers`, a module the
minute-packs rewrite (commit `41016e6`) deleted. The render endpoint now uses
`Depends(get_current_creator)` + `await check_positive_balance(...)`, matching the minute-packs
guard already used in `routers/videos.py`.

**Why**: The stale import meant `import main` raised `ModuleNotFoundError` — the app could not
start at all, the full test suite could not collect, and any container built from `main` would
crash on boot (a likely real cause of "deploy fails / times out"). Minutes are deducted at ingest
(`worker/tasks.py`), so a render needs only a positive-balance guard, not a second deduction.

**Source**: Discovered while running `pytest` during Issue 31 Phase 3. The breaking commit was the
unpushed local `main` commit; this fix lands on top before any push. 2026-05-27.

### Image build: amd64 only

**What**: `docker-publish.yml` builds `linux/amd64` only (was `linux/amd64,linux/arm64`).

**Why**: The DigitalOcean droplet is x86_64. The arm64 build was pure wasted CI time — roughly
doubling image build duration for an architecture nothing runs. Contributed to slow deploys.

**Source**: Deploy-time analysis, 2026-05-27. If an arm64 host is ever added, restore the matrix.

### Cloudflared in Compose + no host port + auto-heal (beta VM)

**What**: `docker-compose.prod.yml` now (a) runs `cloudflared` as a service, (b) removes the app's
`ports: 80:8000` host mapping, (c) drops the dev `--reload` from the app command, (d) adds
liveness `healthcheck`s to `app` and `worker`, and (e) adds a `willfarrell/autoheal` sidecar that
restarts containers labelled `autoheal=true` when their healthcheck goes unhealthy. The tunnel's
public-hostname ingress must target `app:8000` (Compose DNS), documented in `docs/ACCESS.md`.

**Why**: Docker has no native restart-on-unhealthy (confirmed 2026); `autoheal` + per-service
healthchecks is the standard Compose pattern. Routing inbound traffic only through the tunnel
satisfies Issue 23's "no open inbound ports" acceptance and removes the `localhost:80` vs
`app:8000` ambiguity that breaks tunnels. App healthcheck is liveness-only so a transient Postgres
blip doesn't trigger an app restart loop.

**Source**: Web research on Docker Compose auto-healing (willfarrell/autoheal; oneuptime 2026
guides), 2026-05-27.

## 2026-05-28 — Issue 44: Auth boundary hardening

### `get_current_creator`: catch ValueError/KeyError alongside PyJWTError

**What changed**: `auth.py` — `uuid.UUID(payload["sub"])` moved inside the existing
`try/except`, with `(ValueError, KeyError)` added to the caught exception types. A malformed
`sub` (non-UUID string, missing key) now returns 401 "Invalid or expired session" instead of
propagating as a 500.

**Why**: The call was outside the `try` block, so any `ValueError` from `uuid.UUID()` or
`KeyError` from a missing `sub` key fell through to the global exception handler and surfaced
as a 500 with a stack trace in development mode. Per defence-in-depth, any invalid token
payload should yield 401 — not leak error details.

**Source**: Code review of `auth.py:43`; Python `uuid.UUID` docs confirm `ValueError` on
malformed input. 2026-05-28.

---

### `DELETE /me`: add 5/hour rate limit

**What changed**: `routers/auth.py` — `@limiter.limit("5/hour")` added to the
`delete_account` handler. `request: Request` added to handler signature (required by
slowapi for key extraction).

**Why**: The right-to-erasure endpoint had no rate limit. An attacker with a stolen session
could spam it; even accidental repeated clicks should be bounded. 5/hour is generous for
legitimate use (account deletion is a one-time action) and tight enough to prevent abuse.
The existing `limiter` from Issue 18 already uses `_creator_key` (JWT sub → creator UUID),
which gives correct per-creator isolation.

**Source**: slowapi docs on `@limiter.limit`; Issue 18 pattern in `routers/videos.py`.
2026-05-28.

---

### `crypto.py`: MultiFernet + typed TokenDecryptError

**What changed**: `crypto.py` — `_fernet()` now returns `MultiFernet([primary])` when no
previous key is configured, and `MultiFernet([primary, previous])` when
`TOKEN_ENCRYPTION_KEY_PREVIOUS` is set. `decrypt()` catches `cryptography.fernet.InvalidToken`
and re-raises as the new typed `TokenDecryptError`. `config.py` adds
`TOKEN_ENCRYPTION_KEY_PREVIOUS: str | None = None`. `.env.example` documents the rotation
workflow.

**Why MultiFernet over Fernet**: `MultiFernet.encrypt()` always uses the first (primary) key;
`MultiFernet.decrypt()` tries keys in order. This enables zero-downtime key rotation: set
`TOKEN_ENCRYPTION_KEY_PREVIOUS = old key`, run `scripts/rotate_token_key.py` to re-encrypt
all rows under the new primary, then clear `TOKEN_ENCRYPTION_KEY_PREVIOUS`. During the window
between setting the new primary and completing re-encryption, both old and new tokens are
readable. A single-key `MultiFernet([primary])` is functionally identical to `Fernet(primary)`
so there is no behaviour change when no previous key is configured.

**Why TokenDecryptError**: callers (`routers/auth.py`, `youtube/oauth.py`) were inconsistently
handling raw `cryptography.fernet.InvalidToken` — some caught it, some didn't. A project-level
typed exception makes the contract explicit and prevents internal cryptography exceptions from
leaking through unhandled.

**Source**: `cryptography` library docs on `MultiFernet`; Python exception-hierarchy best
practices. Confirmed: `MultiFernet` ships in the same `cryptography` package already pinned
in `requirements.txt`. 2026-05-28.

---

### Preflight doctor as the deploy gate

**What**: New `scripts/doctor.py` validates presence + format + live reachability of every secret
and prints a **redacted** status table (length + last-4 only). `config.py` keeps its fail-fast on
*missing* required vars; the doctor adds *validity* and *connectivity*. `deploy.yml` runs
`python scripts/doctor.py` after image pull and **before** migrations/cutover, so a bad secret
fails the deploy early with safe, visible output rather than a silent crash.

**Why**: The owner's core pain was being unable to see *why* a deploy failed without exposing
secrets. A redacted doctor is the standard "preflight/doctor" answer; pydantic-settings only
covers presence.

**Source**: Web research on pydantic-settings validation patterns, 2026-05-27.

---

## 2026-05-28 — Issue 32: Pin `starlette` explicitly to defend against transitive shadowing

### What changed
`requirements.txt` now pins `starlette==0.41.3` directly, in addition to the existing
`fastapi==0.115.4` pin. Previously starlette was an unpinned transitive dep.

### Why
On 2026-05-28 the test suite failed to collect with
`TypeError: Router.__init__() got an unexpected keyword argument 'on_startup'`.
Root cause: the installed environment had drifted to `starlette==1.1.0`, the published
upstream **on the same day** (starlette 1.2.0 was released earlier in the day; 1.1.0 was
2026-05-23). `starlette` graduated from ZeroVer to 1.0 on 2026-03-22, with the package
moving from `encode/starlette` to `Kludex/starlette` on PyPI (Marcelo Trylesinski now
primary maintainer; Tom Christie co-maintainer). The 1.x line **removed**
`on_startup`/`on_shutdown` from `Router.__init__`, which FastAPI 0.115.x still forwards.

FastAPI 0.115.4 declares `starlette>=0.40.0,<0.42.0` in its `Requires-Dist`, so the broken
install can only happen on an env where pip ran without that constraint applied (drift via
an unrelated `pip install` that didn't reference the requirements file). The explicit pin
on starlette closes that drift path.

### Why not pip-tools / uv lockfile right now
The 2026 industry-standard answer for production Python dep management is `uv` with
`uv.lock` (cross-platform, auto-maintained, 10–100× faster than pip-tools), or `pip-tools`
(`requirements.in` → compiled `requirements.txt`) as the lower-friction alternative. Both
would prevent this category of bug structurally. We're deferring the tooling migration:
a hotfix for an SEV-0 collection failure shouldn't carry a CI/Dockerfile/dev-workflow
overhaul with it. **Re-evaluate when production K8s deployment lands (Issue 30)** — at
that point the operational case for a lockfile is unambiguous.

Until then, the rule is **explicit `==` pinning of every runtime-affecting transitive dep
in `requirements.txt`** as the minimum bar.

### Source / evidence
- `python3.12 -m pip show fastapi` reports `Requires-Dist: starlette<0.42.0,>=0.40.0`
- FastAPI 0.115.4 `pyproject.toml` on GitHub confirms the same constraint
- PyPI `starlette` project page (2026-05-28): latest 1.2.0, source repo
  `https://github.com/Kludex/starlette`, maintainers Marcelo Trylesinski + Tom Christie
- Industry references on 2026 dependency-management practice: Astral `uv` docs;
  Real Python "uv vs pip"; Cuttlesoft "Python Dependency Management in 2026";
  pydevtools handbook on pip-tools

### Verification
With `starlette==0.41.3` pinned and `pip install -r requirements.txt` re-run in a clean
venv, `pytest -q` runs the full suite to **313 passed, 7 deselected** (the 7 are
integration-marked tests excluded by `pytest.ini`'s `-m "not integration"`).

---

## 2026-05-28 — Issue 34: Per-video idempotency for minute deduction (SAVEPOINT + UNIQUE)

### What changed
A new `minute_deductions` ledger table (migration `0003_minute_deductions.py`,
model `MinuteDeduction`) is added with **`UNIQUE(video_id)`** as the idempotency key.
`billing.ledger.deduct_minutes(creator_id, duration_s, session)` is replaced by
`deduct_for_video(video_id, creator_id, duration_s, session)`, and `worker/tasks._ingest_async`
calls the new function with `video.id` + `video.creator_id`.

The new function:
1. Fast-checks for an existing deduction row (skip without opening a savepoint if found).
2. Opens `session.begin_nested()` (SAVEPOINT) wrapping two writes:
   - INSERT into `minute_deductions` + `session.flush()` to surface UNIQUE conflicts now.
   - `UPDATE creators SET minutes_balance = minutes_balance - n WHERE id = :cid AND minutes_balance >= n RETURNING`.
3. On `IntegrityError` (concurrent retry won the race) → roll back savepoint, return 0.
4. On insufficient balance → raise `HTTPException(402)` inside the savepoint, which auto-rolls back the INSERT.

### Why
Celery is configured with `task_acks_late=True` in `worker/celery_app.py`, which makes
delivery at-least-once: if a worker crashes after the deduction commits but before
acking the message, the broker redelivers and the task runs again. The previous
`deduct_minutes` had no per-video key — each retry just re-decremented the balance,
charging the creator 2–4× for a single video. The `UNIQUE(video_id)` constraint moves
the idempotency guarantee from "the application remembers" to "the database refuses",
which is the only durable place for a money primitive.

### Why a ledger table instead of `Video.minutes_charged_at`
`MinutePack` (existing) ledgers **grants in**. `MinuteDeduction` (new) ledgers **costs
out**. `Creator.minutes_balance` is the running total of both. This is the symmetric
design used by every customer-facing billing system (Stripe usage records, AWS billing,
Adyen). It also lets us answer "show my usage history for the last 30 days" with one
indexed query — `Video.minutes_charged_at` would have lost that audit trail.

### Why SAVEPOINT (`session.begin_nested`)
Two writes (deduction record + balance decrement) must succeed atomically. SAVEPOINT
makes them an undo unit *inside* the caller's larger transaction — the caller can
continue doing other work in the same transaction even when our two writes roll back.
This is the SQLAlchemy-2.0-async idiomatic pattern for "atomic sub-operation within
a larger flow."

### Industry standard checked
- **Stripe Idempotency-Key pattern** — store key + result on first call; replay returns
  stored result. The `MinuteDeduction.video_id UNIQUE` is the same pattern with
  `video_id` as the natural opaque key.
- **AWS "Designing Idempotent APIs"** — same model: client supplies an idempotency token,
  server uses a unique constraint to short-circuit duplicates.
- **Celery docs** explicitly state task idempotency is the caller's responsibility;
  `task_acks_late=True` + worker crashes make duplicates a *normal* occurrence, not an
  edge case.
- **Postgres UNIQUE + SAVEPOINT** vs. application-level locking — UNIQUE is the
  database's natural primitive when a key exists. We use both: UNIQUE for the
  idempotency guarantee, SAVEPOINT for atomicity between the two writes.

### Refund-on-permanent-failure deferred
If `_ingest_async` eventually exhausts all Celery retries after the deduction lands,
the creator paid for a permanently-failed ingest. That refund policy is a product
decision (refund threshold? automatic vs. support-initiated?) and is filed as
**Issue 57** in `docs/issues.md`. Today's exposure is small — ingest failures are
observable in logs and support can manually refund via `grant_minutes`.

### Verification
- `pytest -q`: **311 passed, 13 deselected** (was 313/9 — net -2 mocked deduct_minutes
  unit tests, +4 real-DB integration tests in `tests/test_billing_idempotency.py`).
- Integration tests assert: (a) sequential retry is idempotent, (b) two concurrent
  coroutines for the same video_id charge exactly once, (c) insufficient balance leaves
  zero ledger rows, (d) deduction record carries minutes + duration + timestamp.

### Source / evidence
- Stripe Idempotency docs; AWS Best Practices "Designing Idempotent APIs"
- SQLAlchemy 2.0 async docs: "Using SAVEPOINT with begin_nested"
- Celery docs: at-least-once delivery + `task_acks_late`
- Existing project precedent: `MinutePack` grants ledger (Issue 21)

---

## 2026-05-28 — Issue 42: ffmpeg/subprocess timeout formula

### What changed
Every `subprocess.run` call in `clip_engine/render.py` now has an explicit `timeout=`:

- `_run(cmd, label, timeout_s=120.0)` — optional float arg, passed directly to
  `subprocess.run(timeout=timeout_s)`; catches `subprocess.TimeoutExpired` and re-raises
  as `RuntimeError(f"ffmpeg {label} timed out after {timeout_s}s")`.
- `_frame_dimensions` — direct `subprocess.run(..., timeout=30)` hardcoded; ffprobe
  reads only container headers and should return in milliseconds on a healthy file.
- `_extract_keyframe` — threads `timeout_s: float = 120.0` through to `_run` so callers
  can pass the same budget as the render.
- `render_clip_file` — computes `render_timeout_s = max(120.0, duration * 4)` and passes
  it to both `_extract_keyframe` and the final render `_run` call.

### Timeout formula: `max(120, clip_duration_s * 4)`

**Why 4×**: libx264 `fast` preset on 1080p encodes at approximately real-time speed on
modern consumer hardware (i7/Ryzen with AVX2). 4× gives 3 full "real-time equivalents" of
headroom above the encode itself, covering disk I/O, container muxing, startup overhead,
and moderate system load. A 30s clip → 120s ceiling (floor kicks in). A 60s clip → 240s.
A 90s clip → 360s.

**Why floor at 120s**: Very short clips (< 30s) would get absurdly tight budgets with 4×
alone (e.g. a 10s clip would get only 40s). 120s is ample for any short ffmpeg invocation
regardless of clip length and matches the existing `LLM_TIMEOUT_SECONDS` default, making
it the project's "standard slow-operation timeout".

**Why ffprobe = 30s hardcoded**: ffprobe reads only the container header — it finishes in
milliseconds on any non-corrupt file. 30s is already 2–3 orders of magnitude more generous
than needed; threading the render timeout through would be misleading (the ffprobe call is
not proportional to clip length).

### What the error surfaces to
`_run` raises `RuntimeError` on timeout. The Celery render task's existing error handler
catches `RuntimeError` and sets `clip.render_status = failed`. No new error handling path
was needed.

### Source / evidence
- Python docs: `subprocess.run(..., timeout=N)` raises `subprocess.TimeoutExpired` after N
  seconds, which also sends `SIGKILL` to the child process.
- ffmpeg wiki on encode speed: "fast" preset encodes near 1× real-time for 1080p H.264 on
  modern x86 CPUs.
- Project precedent: `LLM_TIMEOUT_SECONDS` defaults to 120s in `config.py`.

---

## 2026-05-28 — Issue 41: Replace pickle with joblib + restricted unpickler allowlist

### What changed
`preference/model.py` — `to_bytes` / `from_bytes` now use **joblib** for serialisation
instead of raw `pickle`.  A new `_RestrictedUnpickler` class (subclass of
`joblib.numpy_pickle.NumpyUnpickler`) overrides `find_class` to enforce an explicit
allowlist of permitted `(module, name)` pairs.  `from_bytes` temporarily patches
`joblib.numpy_pickle.NumpyUnpickler` with `_RestrictedUnpickler` for the duration of
the `joblib.load` call, then restores the original.

No schema change — `preference_models.weights_blob` remains `bytes`.

### Why joblib over raw pickle
joblib is sklearn's officially documented serialisation format:
> "joblib.dump / joblib.load — use this for sklearn estimators as it handles
> large numpy arrays more efficiently than pickle" — scikit-learn User Guide §Model
> persistence.

It is already a transitive dependency (`scikit-learn → joblib`), so no new package
is needed.  Blobs written by `joblib.dump` are forward-compatible across
minor sklearn/joblib versions; raw pickle blobs are not.

### Why the allowlist is the load-bearing defence
joblib uses pickle internally — `joblib.load` without the restricted unpickler is
functionally identical to `pickle.loads` from a security standpoint.  The allowlist
closes the RCE surface by ensuring that `find_class` rejects any module or class
that is not in the pre-approved set, **before** any `__reduce__` / `__setstate__`
output is invoked.

### Allowlist derivation
The full `(module, name)` set was determined empirically by running a subclass of
`pickle.Unpickler` against real `joblib.dump` outputs for both `LogisticRegression`
and `LGBMClassifier`:

| Entry | Reason |
|-------|--------|
| `preference.model.PreferenceScorer` | The wrapper class itself |
| `sklearn.linear_model._logistic.LogisticRegression` | Cold-start model |
| `lightgbm.sklearn.LGBMClassifier` | Warm-start model |
| `lightgbm.basic.Booster` | LightGBM's internal tree model |
| `joblib.numpy_pickle.NumpyArrayWrapper` | joblib emits this for every ndarray |
| `numpy.ndarray` | Model weight arrays |
| `numpy.dtype` | Array dtypes |
| `numpy._core.multiarray.scalar` | Scalar numpy values |
| `collections.defaultdict` | LightGBM's internal param dict |
| `collections.OrderedDict` | LightGBM's internal param dict |

### Alternatives ruled out
- **HMAC envelope around raw pickle**: defers the attack surface instead of closing it.
  The blob still becomes RCE if the HMAC key leaks.  HMAC-only is the "if pickle truly
  cannot be removed" fallback the issue specified — joblib + allowlist is strictly
  stronger.
- **LightGBM native `.txt` format + sklearn JSON**: requires separate serialisation
  paths per model type, custom re-assembly of the `PreferenceScorer` wrapper, and
  additional validation of the sklearn JSON format.  More code surface for the same
  security property.

### Thread-safety note
The temporary `_jnp.NumpyUnpickler` patch is not thread-safe if two `from_bytes`
calls execute concurrently in the same process.  Celery workers are single-threaded
per-task (one task per process with the `prefork` pool), so this is safe in the
current architecture.  If the project ever switches to a threaded Celery pool or
calls `from_bytes` from async code, replace the patch with a thread lock.

### Verification
- `tests/test_preference.py` — 4 new tests:
  - `test_scorer_round_trips_joblib`: legitimate scorer survives to_bytes → from_bytes
    with identical `predict_score` output
  - `test_scorer_round_trips_preserves_label_count`: `label_count` attribute preserved
  - `test_tampered_blob_is_rejected`: joblib blob with `os.system` `__reduce__` raises
    `pickle.UnpicklingError("class not allowed: posix.system")`
  - `test_tampered_blob_arbitrary_global_rejected`: joblib blob with `subprocess.Popen`
    gadget raises `pickle.UnpicklingError("class not allowed: subprocess.Popen")`

### Source / evidence
- scikit-learn User Guide "Model persistence": https://scikit-learn.org/stable/model_persistence.html
- Python docs `pickle.Unpickler.find_class`: https://docs.python.org/3/library/pickle.html#pickle.Unpickler.find_class
- Python HOWTO "Restricting globals" pattern for safe unpickling
- joblib source: `joblib.numpy_pickle.NumpyUnpickler`, `_unpickle` (joblib 1.5.3)
## 2026-05-28 — Issue 35: Idempotent DNA build (SEV-0)

### Single-transaction commit for draft + embeddings + onboarding state

**What changed**: `dna/profile.create_draft`, `dna/embeddings.embed_patterns`, and
`dna/embeddings.embed_brief` each gained a keyword-only `commit: bool = True` parameter.
`worker/tasks._build_dna_async` now calls all three helpers with `commit=False` and issues
a single `await session.commit()` at the end of the function, after all three `session.add()`
chains are staged.

**Why**: The original code committed inside `create_draft` before calling the Voyage API for
embeddings. If the Voyage call raised (network error, quota exhaustion, etc.), Celery retried
the whole task. On retry, `create_draft` queried `max(version)` — which now returned the orphan
draft row — and inserted a new row at version+1. The root cause is a partial commit that left a
permanent row before the unit of work was complete.

The fix makes the database write atomic: if the Voyage call or any subsequent write fails, the
`AsyncSessionLocal` context manager's `__aexit__` calls `session.rollback()`, and no draft row
exists for the next retry to bump the version against.

**Alternatives ruled out**: Deleting the orphan on retry detection (fragile — requires detecting
partial state; race-prone). Using a SAVEPOINT to wrap the embeddings (overkill — the entire
`_build_dna_async` function is one logical unit of work; a single outer transaction is the
idiomatic choice).

**Backward compatibility**: `commit=True` is the default on all three helpers, so all existing
callers (`confirm_draft`, `routers/creators.py`, any future standalone call) continue to commit
immediately without code changes.

**Source**: Standard SQLAlchemy async unit-of-work pattern (defer commit to the outermost
caller that owns the transaction boundary). 2026-05-28.
## 2026-05-28 — Issue 40: Streaming upload — chunk size and RSS assertion bound

### Chunk size: 1 MB

**What**: `upload_video` reads `UploadFile` in 1 MB chunks into a `NamedTemporaryFile`, keeping
only the current chunk in memory at any one time.

**Why 1 MB**: Standard FastAPI / ASGI streaming guidance (Starlette issue #1746; python-multipart
docs) recommends chunk sizes between 512 KB and 4 MB. 1 MB is the midpoint — syscall overhead
is negligible (≤ 500 iterations for a 500 MB file), while the per-request heap ceiling is 1 MB
of upload data regardless of file size. Smaller chunks add syscall noise; larger chunks make the
heap ceiling proportionally higher. No project-specific tuning data exists at this stage, so the
industry midpoint was chosen.

**Source**: Starlette streaming docs; python-multipart FAQ; ASGI file-upload best practices.
2026-05-28.

### RSS delta assertion bound: 20 MB for a 100 MB rejected upload

**What**: `test_rss_delta_bounded_for_rejected_upload` asserts that `ru_maxrss` grows by no more
than 20 MB when a 100 MB upload is rejected.

**Why 20 MB**: With 1 MB chunks, only the current chunk (≤ 1 MB) should be live at any moment.
However, the Python runtime, test framework, OS buffer cache, and Starlette request internals
introduce measurement noise. The 20 MB ceiling is 20× the chunk size — tight enough to catch a
regression to bulk-read (which would show a ~100 MB delta) while loose enough to absorb normal
runtime overhead. This is a conservative bound; in practice the delta observed is 1–3 MB.

**Source**: `resource.getrusage` documentation (Linux: kilobytes, macOS: bytes); empirical
observation during implementation. 2026-05-28.

---

## 2026-05-28 — Issue 36: OAuth token lifecycle hardening (SEV-1)

### Revoke the refresh token, not the access token

**What**: `DELETE /auth/me` now POSTs the decrypted **refresh_token** to
`https://oauth2.googleapis.com/revoke`. A `400` with body `{"error": "invalid_token"}` or
`{"error": "token_revoked"}` is treated as success; other 4xx is logged but does not abort
account deletion.

**Why**: Revoking only the access token leaves the refresh token usable until the user
manually visits `myaccount.google.com/permissions` — an incomplete right-to-erasure and a
YouTube ToS gap. Google's OAuth 2.0 docs explicitly state revoking a refresh token
invalidates every access token derived from it, so one call suffices.

**Source**: Google OAuth 2.0 — Revoking a Token
(`developers.google.com/identity/protocols/oauth2/web-server#tokenrevoke`); OAuth 2.0
RFC 6749 §2.3.1.

### Discard the token row on `invalid_grant`

**What**: `youtube/oauth.py::get_valid_access_token` now deletes the `YoutubeToken` row +
commits when `refresh_access_token` returns `400 {"error": "invalid_grant"}`. Other 4xx
during refresh leaves the row in place (could be transient client misconfig).

**Why**: Per RFC 6749 §5.2, `invalid_grant` is a permanent error — the user has revoked
consent, the grant expired (6 mo unused), or a password reset with reauth invalidated it.
Re-attempting the refresh hourly was wasted quota and noisy logs. Deleting the row makes
the next call surface the existing "No OAuth tokens found — please reconnect" 401.

**Source**: OAuth 2.0 RFC 6749 §5.2; Google identity docs on refresh-token expiration.

### Classify 403 errors by `error.errors[].reason`

**What**: New `youtube/errors.py` defines `YouTubeAuthError(reason, status_code)` plus
`PERMANENT_403_REASONS` (authError, forbidden, accountClosed, accountSuspended,
accountDelegationForbidden, channelClosed, channelSuspended) and `TRANSIENT_403_REASONS`
(quotaExceeded, rateLimitExceeded, userRateLimitExceeded). `_get_json` in
`youtube/data_api.py` and `_fetch_report` in `youtube/analytics.py` now share a
`_classify_error()` helper: transient reasons + 429 still retry with exponential backoff;
permanent reasons + 401 raise `YouTubeAuthError` immediately, no retries.
`worker/tasks.py::_refresh_youtube_analytics_async` catches `YouTubeAuthError`, deletes
the creator's `YoutubeToken` row, commits, and continues to the next creator.

**Why**: Previously every 403 triggered four backoff retries — 7+ seconds of blocking and
four wasted quota hits per beat tick per revoked creator. Over time the daily beat loop
would consume a meaningful slice of the channel quota on creators who had revoked access.
The reason-based branching mirrors how `google-api-python-client` exposes
`HttpError.error_details` and how official YouTube samples branch on `reason`.

**"Mark creator disconnected" via token-row absence**: Rather than add a new
`OnboardingState.disconnected` enum value (which would require an Alembic migration), we
delete the `YoutubeToken` row. The existing `get_valid_access_token` already raises
`HTTPException(401, "No OAuth tokens found — please reconnect")`, and the beat loop's
prefix `try: get_valid_access_token ... except: continue` block then silently skips that
creator. A future issue can add a UI-visible `disconnected` state if the product needs it.

**Source**: YouTube Data API v3 — Errors reference
(`developers.google.com/youtube/v3/docs/errors`); Google APIs error model
(`developers.google.com/identity/protocols/oauth2/openid-connect#errors`); existing
worker skip-on-exception pattern in `worker/tasks.py:_refresh_youtube_analytics_async`.

---

## 2026-05-28 — Issue 45: Concurrent token refresh lock + Redis pool singleton (SEV-2)

### Per-creator Redis advisory lock in `get_valid_access_token`

**What changed**: `youtube/oauth.py::get_valid_access_token` now wraps the Google refresh
call with a per-creator Redis advisory lock (`SET refresh-lock:{creator_id} <uuid> NX EX 10`).

- **Lock acquired**: proceed with the existing refresh + DB commit, then release via a Lua
  compare-and-delete script that only deletes the key if the value still matches our token.
  This prevents a worker whose TTL expired mid-flight from deleting another worker's lock.
- **Lock not acquired**: poll up to 3 times with 200 ms sleeps, re-reading the
  `YoutubeToken` row each time. If the row's `expires_at` is now in the future by > 5 min,
  return its decrypted access token. If still expired after all retries, raise
  `HTTPException(503, "Token refresh in progress; please retry")`.

**Why SET NX EX over Redlock**: SET NX + a reasonable TTL (10s) is the canonical
single-node Redis distributed-lock pattern, documented in the official Redis SETNX page and
in "The Redlock algorithm" article. Redlock (multi-node quorum) is appropriate when Redis
itself is clustered; this project runs a single Redis instance so SET NX is correct and
significantly simpler. The Lua compare-and-delete (KEYS[1] == ARGV[1] → DEL) is the
canonical safe-release idiom from the Redis docs to prevent accidental release of another
client's lock if our TTL expires.

**Why 10s TTL**: One Google token-refresh round-trip completes in < 1s under normal
conditions. 10s gives 10× headroom before the lock auto-expires, covering network hiccups
and slow Google responses while still protecting against a worker crash leaving the lock
indefinitely. A shorter TTL risks expiring mid-refresh; a longer TTL extends the worst-case
stall for waiting workers.

**Why 200ms / 3-retry poll**: Total worst-case wait is 600ms — acceptable for an interactive
`/clips` request. Three retries avoids an infinite loop while giving the lock holder enough
time to complete the Google round-trip and DB commit.

**Source**: Redis SETNX docs (`redis.io/commands/setnx`); Redis "Distributed Locks with
Redis" article (`redis.io/docs/manual/patterns/distributed-locks`). 2026-05-28.

---

### Module-level Redis singleton in `youtube/_redis.py`

**What changed**: `youtube/quota.py` previously called `aioredis.from_url(...)` on every
`consume()` and `remaining()` call, creating a new connection-pool per call. A new helper
module `youtube/_redis.py` exposes `get_redis_client()` which initialises a single
`redis.asyncio.Redis` instance at first call and reuses it on all subsequent calls.
Both `youtube/quota.py` and `youtube/oauth.py` import from this module.

**Why singleton over per-call `from_url`**: `redis-py` 4.2+ creates an internal
`ConnectionPool` per `Redis` instance. Per-call `from_url` creates a new pool every time,
leaking connections and adding latency. The singleton pattern ensures one pool is shared
across the process — the standard recommendation in the redis-py docs and the pattern used
by every production redis-py deployment.

**Why a separate `_redis.py` module**: `oauth.py` and `quota.py` are separate concerns but
both need Redis. Putting the singleton in either one and importing from the other creates a
circular dependency risk. A dedicated `_redis.py` (underscore = package-internal) is the
clean DRY solution.

**Source**: redis-py docs "Connection Pools" section; PEP 8 on module naming conventions
for package-internal helpers. 2026-05-28.
