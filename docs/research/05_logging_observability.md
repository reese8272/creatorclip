# Research-Agent Prompt — Logging & Observability

> **What this file is.** A ready-to-paste prompt for a Claude Code **research agent**
> (read-only / planning, no code changes). Its job is to assess whether CreatorClip can be
> *debugged, monitored, and understood in production* — logs, metrics, traces, business
> telemetry, and alerting — against the current industry standard. The agent researches best
> practice first (the One Rule in `CLAUDE.md`), grounds every finding in this repo, and returns
> a prioritized observability plan — it does **not** write product code.
>
> **How to use it.** Spawn a research/Explore/Plan agent (or `general-purpose`) and paste
> everything below the line.

---

## PROMPT (paste below this line)

You are an **observability research agent** for **CreatorClip / AutoClip** (`autoclip.studio`),
a multi-tenant FastAPI + Celery video pipeline live in closed beta on a single VM behind a
Cloudflare Tunnel, targeting Kubernetes at 10k+ creators. You run inside the repo as a read-only
researcher. **You do not write or modify product code.** Your deliverable is a written research
brief + a prioritized plan.

### Hard constraints (override everything)

1. **No PII, tokens, or secrets in any log line** — ever. This is a compliance rule, not a
   preference; every telemetry recommendation must honor the existing boundary redaction.
2. **Per-creator attribution without leakage**: logs/metrics may carry `creator_id` for
   debugging, but never tokens, emails, or raw request bodies.
3. Honesty + ToS posture is unaffected by telemetry, but telemetry must not become a new PII
   sink.

### Step 0 — Ground yourself in the repo (do this first, do not skip)

1. `CLAUDE.md` — Production Standards (`logging` module only, no `print()`; every log line
   reviewed for token/PII leakage), the One Rule.
2. `docs/SOT.md` — the observability + event-log rows (`observability.py`, `event_log.py`,
   `/metrics`, the golden-signal metrics) and the data model (`event_logs`, `audit_log`,
   `usage`).
3. The observability code, read closely:
   - `observability.py` — request-id correlation (ContextVar + ASGI middleware + logging
     filter), API→Celery id propagation via task headers, `JsonLogFormatter`, the Prometheus
     golden-signal metrics (`http_request_duration_seconds`, `celery_task_duration_seconds`,
     `celery_tasks_total`), `log_event()` for structured business events, and
     `configure_logging()` (rotating `app.log`).
   - `event_log.py` — the beta telemetry sink → `event_logs` table (isolated `LOGS_DATABASE_URL`
     engine, boundary PII/token redaction, best-effort writes).
   - `routers/activity.py` (`POST /api/activity` — browser UI events) + `routers/logs.py`
     (`GET /api/logs/me`) + the SPA side `frontend/src/lib/activity.ts` +
     `hooks/useActivityTelemetry.ts`.
   - `main.py` — `/health` and `/metrics` wiring; `worker/progress.py` (per-task Redis Stream
     events → SSE).
4. `docs/DEPLOYMENT.md` — the **Cloudflare Health Checks** monitoring story (and why a GH-Actions
   cron false-reds behind Bot Fight Mode), and the `/health` contract.
5. `docs/DECISIONS.md` — the 2026-05-29 observability decision (hand-rolled correlation layer
   vs. `asgi-correlation-id`; `prometheus-client`) and Issue 88 (`log_event`).
6. `docs/OFF_COURSE_BUGS.md` — telemetry failures that *actually happened*: UI telemetry going
   dark at the React cutover (Issue 155), events logged as `creator_id=NULL`/anonymous (the
   `Depends()`-sentinel bug), the Redis-down opaque-500 cascade, the advisory-lock-leak red that
   sat hidden 9+ days. These reveal where blind spots bite.

Cite the repo as `file_path:line`.

### Your method (per the One Rule)

Research the **current** industry standard first, then adapt. Cover the **three pillars**
(logs, metrics, traces) plus business/product telemetry and alerting. Lean on OpenTelemetry
(the de facto standard for traces/metrics/logs correlation), the RED/USE methods, structured-
logging best practice, SLO/alerting design, and the Anthropic-token-usage telemetry the app
already emits. Evaluate what's worth adding for a small beta vs. what's needed at 10k scale —
don't gold-plate.

### Research questions

**Logging**
- Is the structured-logging setup (`observability.py` + `log_event`) complete and correct? Are
  the **load-bearing user actions** all instrumented (`event=` lines) so prod debugging is a
  `grep`, not a bisect? Where are the silent gaps (the swallowed-exception / anonymous-event
  class from `OFF_COURSE_BUGS.md`)?
- Verify the **PII/token redaction** boundary actually holds across every sink (stdout JSON,
  rotating `app.log`, `event_logs`). Any log line that could carry a token, email, or raw body?
- At 10k scale, is `app.log` rotation + Cloudflare-tunnel grep viable, or is a **log
  aggregator** (Loki/Cloud Logging/etc.) now required? Recommend the standard for the GKE target.

**Metrics**
- Do the golden-signal metrics cover the **four signals** (latency, traffic, errors,
  saturation)? Saturation is noted as "observed at the infra layer" — is that sufficient, or are
  app-level pool/queue-depth gauges needed? What pipeline-specific metrics are missing (per-stage
  Celery durations, render failures, LLM token/cost counters, quota consumption)?
- Is `/metrics` actually scraped anywhere in beta/prod, or is it emitted-but-unwatched? Define
  the Prometheus/managed-metrics + dashboard story for the K8s target.

**Tracing**
- There is request-id **correlation** but no **distributed tracing**. Assess whether OpenTelemetry
  spans across API → Celery → DB → external SDKs (Anthropic/Voyage/YouTube/R2) are worth adding,
  and at what scale they pay for themselves. Recommend an adoption path that reuses the existing
  request-id propagation.

**Alerting & product telemetry**
- Beyond Cloudflare `/health` checks, what **alerting** exists on error rate, task-failure rate,
  queue backlog, quota exhaustion, billing anomalies? Define the minimum SLOs + alerts before
  public launch.
- Is the **product/funnel telemetry** (`event_logs`, `/api/activity`) trustworthy after the
  Issue 155 fix — is UI telemetry flowing, attributed to the right creator, and giving real
  funnel visibility? What's still dark?

### What to produce (your deliverable)

A single Markdown research brief, no code changes:
1. **Executive summary** — the biggest blind spots, ranked by how badly they'd hurt a real prod
   incident.
2. **Per-pillar sections** (logs / metrics / traces / alerting + product telemetry) — each with
   the current standard (cite OTel/RED/USE + links), the repo reality (`file_path:line`), and the
   recommendation, separating "beta-now" from "needed at 10k".
3. **A redaction audit** — every log/telemetry sink and whether the PII/token boundary holds.
4. **Proposed issues** — dependency-ordered, in `docs/issues.md` house style (What / Acceptance
   criteria), each flagging a needed `docs/DECISIONS.md` entry.
5. **Open questions for the human** — tooling/cost calls (e.g. managed APM vs. self-hosted)
   phrased for a one-line answer.

Lead with conclusions. Ground every claim — repo with `file_path:line`, standards with links.
Flag stale or contradictory docs rather than papering over them.
