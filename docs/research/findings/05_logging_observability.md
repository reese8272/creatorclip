# Research Brief — Logging & Observability (Prompt 05)

> Read-only research agent output. No product code changed. Scope: **system observability**
> — logs, metrics, traces, alerting (system telemetry). Product/funnel analytics is prompt 07;
> notifications is prompt 11. All three reuse the same `event_logs` + SSE infrastructure
> assessed here; cross-references are flagged inline.
>
> Method follows the One Rule (`CLAUDE.md`): current industry standard researched live first,
> then grounded in the repo. Repo claims cite `file_path:line`; external claims cite links.
> Date: 2026-06-22. Highest existing issue in `docs/issues.md`: **165** → proposals start at 166.

---

## 1. Executive summary — blind spots ranked by prod-incident pain

CreatorClip has a genuinely good *foundation*: a hand-rolled correlation-id layer that
propagates API→Celery (`observability.py:39`, `:266-277`), JSON structured logs
(`observability.py:88`), golden-signal Prometheus metrics (`observability.py:62-76`), a
`/health` contract probing Postgres+Redis (`main.py:324-331`), a redacting DB telemetry sink
(`event_log.py:71-84`), and live UI telemetry restored after the React cutover
(`frontend/src/hooks/useActivityTelemetry.ts`). This is above-par for a closed beta. The gaps
below are what would actually hurt during a real incident, worst first:

1. **`/metrics` is emitted but almost certainly unwatched.** The golden signals exist
   (`observability.py:62-76`) and `/metrics` is exposed + token-gated (`main.py:279-292`), but
   nothing in `docs/DEPLOYMENT.md` scrapes it — monitoring is *only* a Cloudflare Health Check
   hitting `/health` (`docs/DEPLOYMENT.md:96-112`). **`/health` is binary up/down. There is no
   alert on error rate, p99 latency, or Celery task-failure rate.** A render pipeline that
   500s for every creator while Postgres+Redis stay healthy is completely invisible — exactly
   the failure class that hurts most. This is the #1 gap.

2. **Celery task failures have a metric but no alert and no per-stage visibility.**
   `celery_tasks_total{state}` and `celery_task_duration_seconds` are recorded
   (`observability.py:253-261`), but (a) nothing alerts when `state="FAILURE"` climbs, and
   (b) the long render/ingest/DNA pipeline has no *per-stage* timing or failure counter — a
   stuck reframe stage vs. a stuck transcription stage are indistinguishable in metrics. The
   advisory-lock-leak red that "sat hidden 9+ days" (`docs/OFF_COURSE_BUGS.md:24`) is the
   archetype: a silent task short-circuit with no alert surfaced it.

3. **The stdout/`app.log` log sink does NOT redact — only the DB sink does.**
   `event_log._redact()` scrubs sensitive keys before the `event_logs` row
   (`event_log.py:71-84`), but `JsonLogFormatter` promotes **any** `extra`/`log_event` field
   to a top-level JSON key with **zero** redaction (`observability.py:99-101`). The PII/token
   boundary on the file + stdout path is enforced *only by call-site discipline*. Today's call
   sites are clean (ids only — verified across all 13 `log_event` calls), but there is no
   structural guard, so the next careless `log_event("x", email=…)` leaks silently. This is a
   compliance hard-constraint with no backstop.

4. **No app-level saturation signal.** Golden signals are 3-of-4: latency, traffic, errors are
   covered; **saturation is asserted to be "observed at the infra layer"** (`observability.py:60-61`,
   `DECISIONS.md:3910-3913`) but there is no DB-pool, PgBouncer, Redis, or Celery-queue-depth
   gauge in the app. The Redis-down opaque-500 cascade (`docs/OFF_COURSE_BUGS.md:17`) and the
   PgBouncer auth-type staging outage (`docs/OFF_COURSE_BUGS.md:25`) were both saturation/health
   problems that no app gauge would have caught.

5. **No distributed tracing.** Correlation-id is request-level only; there are no spans across
   API → Celery → Postgres → Anthropic/Voyage/YouTube/R2 (`DECISIONS.md:3930-3932` explicitly
   defers OTel tracing). At beta scale a `grep request_id` is fine; at 10k it is not.

6. **LLM token usage is logged as free text, never aggregated — and the `usage` table is never
   written.** Every Anthropic call logs `tokens: in=… out=…` as a plain `logger.info` string
   (`knowledge/hooks.py:218-224`, `chat/runner.py:109`, `routers/insights.py:586`, etc.), but
   there is **no Prometheus token/cost counter** and the `usage` table's `tokens_in`/`tokens_out`
   columns (`models.py:674-675`) have **zero writers** (grep-confirmed). Cost/quota anomalies —
   a runaway prompt, a creator burning the LLM budget — are invisible in metrics and unbilled in
   the ledger. Cross-ref: prompt 07 (funnel) and billing both need this.

7. **Worker logs have no durable file sink.** The API installs the rotating `app.log`
   (`main.py:49` → `observability.py:164-175`), but `worker/celery_app.py:15` calls
   `configure_logging(json_logs=…)` **without `log_dir`** — worker JSON logs go to stdout only.
   On the single VM, if stdout isn't captured, a crashed render's logs are gone. Less acute than
   #1-#4 but a real debugging gap.

The throughline: **detection is the weak link, not instrumentation.** CreatorClip records good
signals and then watches almost none of them. Most fixes are cheap (a scrape + a handful of
alerts) and high-leverage.

---

## 2. Per-pillar assessment

### 2.1 Logs

**Current standard.** Structured (JSON) logs with trace/correlation context on every line is the
de-facto baseline; OpenTelemetry's logging model injects trace+span ids into log records so logs
filter by trace id ([OTel logs correlation](https://signoz.io/blog/opentelemetry-fastapi/)).
PII/secret handling is a *layered* defense: allowlist-by-default fields, a key-blocklist scrubber
middleware, **and** a collector-side scrub before logs leave the host
([OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html);
[Dash0 logging best practices](https://www.dash0.com/guides/logging-best-practices)). At scale,
ship to a log aggregator rather than grepping files.

**Repo reality.**
- JSON formatter + per-record `request_id` injection is correct and idempotent
  (`observability.py:80-104`, `:137-175`). Text fallback for dev (`observability.py:152-156`).
- `log_event()` is the structured-business-event helper (`observability.py:110-134`), but it is
  **under-used**: only **13 call sites across 8 files** (auth, creators ×3, review, dna/builder,
  videos ×2, clips, api_keys ×2, activity). Whole load-bearing surfaces emit **no** `event=`
  line — e.g. the render/clip-generation pipeline stages in `worker/tasks.py`, ingestion,
  billing webhooks, upload-intel. So "prod debugging is a grep, not a bisect"
  (`observability.py:112-114`) is aspirational, not yet true. The Issue-88 anonymous-event class
  (`docs/OFF_COURSE_BUGS.md:31`) and the UI-telemetry-dark class (`:34`) are exactly the
  swallowed/missing-event failures this pillar warns about.
- **Redaction gap (load-bearing):** `JsonLogFormatter` emits every non-reserved record attr
  verbatim (`observability.py:99-101`) — no scrubbing. `event_log._redact()` exists only for the
  DB sink (`event_log.py:71-84`). See the redaction audit (§3).
- Worker has no file sink (`worker/celery_app.py:15` — no `log_dir`).

**Recommendation.**
- *Beta-now:* (a) add a key-blocklist scrub inside `JsonLogFormatter`/`RequestIDLogFilter` mirroring
  `event_log._REDACT_SUBSTRINGS` so the file/stdout path has the same backstop as the DB path
  (closes #3); (b) instrument the missing load-bearing surfaces with `log_event` — render pipeline
  stage start/done/error, ingest, billing webhook received/processed, quota-exceeded; (c) pass
  `log_dir` to the worker's `configure_logging`.
- *At 10k:* adopt **Grafana Loki** as the aggregator. It is the standard for Prometheus/Grafana
  shops, GCS-backed (GKE-native), ~10–100× cheaper storage than Elasticsearch, and ~80% YoY
  adoption growth ([Loki on GKE](https://lucaberton.com/blog/grafana-loki-kubernetes-log-aggregation-2026/);
  [grafana/loki](https://github.com/grafana/loki)). `app.log` rotation + Cloudflare-tunnel grep
  does **not** scale past one VM. Worth a `DECISIONS.md` entry (Loki vs. GCP Cloud Logging — see
  open questions).

### 2.2 Metrics

**Current standard.** The Four Golden Signals — **latency, traffic, errors, saturation**
([Google SRE](https://sre.google/sre-book/monitoring-distributed-systems/)). RED (Rate/Errors/
Duration) is the request-side subset; USE (Utilization/Saturation/Errors) covers infra resources;
most teams run both ([SRE golden signals](https://www.splunk.com/en_us/blog/learn/sre-metrics-four-golden-signals-of-monitoring.html)).
For LLM apps the standard is now the **OpenTelemetry GenAI semantic conventions** — token-count
metrics (`gen_ai.usage.input_tokens`/`output_tokens`) drive near-real-time cost tracking within
1–3% of the bill ([OTel GenAI semconv](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/);
[Datadog GenAI](https://www.datadoghq.com/blog/llm-otel-semantic-convention/)).

**Repo reality.**
- Three golden-signal metrics: `http_request_duration_seconds` (latency + traffic via `_count` +
  errors via `status` label), `celery_task_duration_seconds`, `celery_tasks_total`
  (`observability.py:62-76`). Cardinality bounded by route-template labelling
  (`observability.py:224-231`) — correct.
- **Saturation missing at app level** (`observability.py:60-61`). No DB-pool / queue-depth /
  Redis-memory gauge.
- **No pipeline-specific metrics:** no per-stage Celery durations, no render-failure counter, no
  LLM token/cost counter, no quota-consumption gauge. Token usage is text-logged only
  (`knowledge/hooks.py:218`, `chat/runner.py:109`, `routers/insights.py:586`); the `usage` table
  (`models.py:664-677`) has no writer.
- **`/metrics` is exposed (`main.py:279-292`) but unscraped** — no Prometheus/Grafana/Alertmanager
  anywhere in `docs/DEPLOYMENT.md`. Emitted-but-unwatched (#1).

**Recommendation.**
- *Beta-now:* stand up a scrape (even one Grafana Cloud / self-hosted Prometheus pointed at
  `/metrics` with the bearer token from `settings.METRICS_TOKEN`, `main.py:286-290`) so the
  signals already emitted become visible. Add a render-failure counter + LLM token counter
  (`gen_ai`-shaped labels: provider, model, kind) wherever the `usage` dict is already computed.
- *At 10k:* on GKE use **Google Managed Service for Prometheus** (or self-hosted Prometheus
  Operator) + Grafana. Add app-level saturation gauges: SQLAlchemy pool checked-out count,
  Celery queue depth (Redis `LLEN`), Redis used-memory. Persist token usage to the `usage`
  ledger (closes the unbilled-tokens half of #6 — coordinate with billing + prompt 07).

### 2.3 Traces

**Current standard.** OpenTelemetry is the de-facto standard for distributed tracing; the critical
practice is **trace-context propagation across the Celery message boundary** via task headers, or
workers start orphan traces ([Uptrace Celery](https://uptrace.dev/guides/opentelemetry-celery);
[OneUptime: propagate context through Celery headers](https://oneuptime.com/blog/post/2026-02-06-propagate-opentelemetry-trace-context-celery-headers/view)).
Use head-based sampling (`TraceIdRatioBased`, e.g. 10%) + batch export in production
([FastAPI OTel guide 2026](https://dev.to/kaushikcoderpy/fastapi-distributed-tracing-the-complete-opentelemetry-guide-2026-k)).

**Repo reality.** Request-id **correlation** exists (`observability.py:39`, `:241-249`) but **no
spans** — explicitly deferred (`DECISIONS.md:3930-3932`, also noted at `:3247`). External SDK calls
(Anthropic, Voyage, YouTube, R2) and DB queries are untimed except as coarse HTTP/task histograms.

**Recommendation.**
- *Beta-now:* **do not adopt full OTel tracing.** Correlation-id + `grep request_id` is adequate
  at beta volume; OTel needs a collector to operate and the payoff is low at single-VM scale.
- *At 10k:* adopt OTel tracing on the **existing** propagation rail — CreatorClip already carries
  an id through the same Celery-header mechanism OTel uses (`observability.py:42`, `_stamp_request_id`
  at `:241`), so the adoption path is "stamp the W3C `traceparent` alongside `x_request_id`, swap
  the hand-rolled stamp for `opentelemetry-instrumentation-celery`, keep `request_id` as a span
  attribute for continuity." Auto-instrument FastAPI, Celery, SQLAlchemy, httpx; head-sample 10%.
  This is a `DECISIONS.md`-worthy change (it revisits the 2026-05-29 deferral).

### 2.4 Alerting & product telemetry

**Current standard.** Alert on **SLO error-budget burn rate**, not raw thresholds:
multi-window/multi-burn-rate — fast burn (e.g. 14.4× over 1h, confirmed over 5m) pages; slow burn
(e.g. budget <25% over longer windows) warns ([Google SRE Workbook: alerting on SLOs](https://sre.google/workbook/alerting-on-slos/)).
Pre-launch guidance: "start with recording rules and a **single critical alert**, then add warning/
info tiers and tune on real incidents" ([same](https://sre.google/workbook/alerting-on-slos/)).

**Repo reality.**
- Only alert is Cloudflare Health Checks on `/health` (`docs/DEPLOYMENT.md:96-112`). The GH-Actions
  cron is correctly retired as a false-red anti-pattern behind Bot Fight Mode
  (`docs/DEPLOYMENT.md:99-119`; `DECISIONS.md:541-542`). `/health` is binary; it covers Postgres+
  Redis reachability (`main.py:295-331`) but **nothing on error rate, task-failure rate, queue
  backlog, quota exhaustion, or billing anomalies**.
- **Product/funnel telemetry** flows through `event_logs` via `record_event` (`event_log.py:102`),
  fed by `POST /api/activity` (UI) + the `http_request` middleware (backend) (`main.py:242-267`),
  read back at `GET /api/logs/me` (`routers/logs.py`). After the Issue-155 + Issue-151 fixes
  (`docs/OFF_COURSE_BUGS.md:31`, `:34`), UI events flow and are attributed via
  `creator_id_from_cookie` (`routers/activity.py:43-46`). **Caveat:** the Issue-155 entry says
  "live `source='ui'` row confirmation pending the batched deploy" — the fix is verified by tests,
  **not yet by a live prod row** (`docs/OFF_COURSE_BUGS.md:34`). Flag: confirm before trusting
  funnel data. Deeper funnel analysis is **prompt 07's** lane — this brief only certifies the
  pipe is sound.

**Recommendation.**
- *Beta-now:* define **2 SLOs + 1 page-level alert**: (1) API availability (5xx rate) and (2)
  Celery task-success rate, each as a fast-burn alert off the metrics already emitted. This is the
  highest-ROI item in the whole brief and directly closes #1/#2.
- *At 10k:* full multi-window burn-rate alerting via Alertmanager (or Grafana Cloud) on availability,
  latency p99, task-failure rate, queue backlog, and quota/billing anomalies. Add an admin-gated
  cross-creator operator view of `event_logs` (the deliberate follow-up noted in `routers/logs.py:5-7`).

---

## 3. Redaction audit — every sink vs. the PII/token boundary

| # | Sink | Where | Redaction? | Verdict |
|---|------|-------|-----------|---------|
| 1 | **stdout JSON** (`StreamHandler`) | `observability.py:159-162` + `JsonLogFormatter:88-104` | **None** — every `extra` attr emitted verbatim (`:99-101`) | ⚠️ **Boundary holds by call-site discipline only.** No structural guard. |
| 2 | **Rotating `app.log`** | `observability.py:164-175` (same `JsonLogFormatter`) | **None** (same as #1) | ⚠️ Same gap; persists to disk on the VM. |
| 3 | **`event_logs` DB row** | `event_log.record_event` → `_redact` `:71-84`, `:128-147` | **Yes** — key-blocklist scrub, 20-key cap, 500-char truncate, creator-by-id-only (`:13`) | ✅ Holds. Best-effort write never raises (`:146-147`). |
| 4 | **`/api/activity` ingress** | `routers/activity.py:48-52` | Caps key count + str length; **does not scrub by key name** before the file sink at `:55-62`; DB sink at `:66-74` *is* scrubbed by #3 | ⚠️ Raw `extra` reaches the **file** sink unscrubbed (feeds gap #1). |
| 5 | **Redis Stream / SSE progress** | `worker/progress.py:74-85` | No scrub; payloads are stage/token fields by convention (`DECISIONS.md:2861`, `:2870`) | ✅ in practice (no PII in emit payloads — invariant documented) but **unenforced**. |
| 6 | **LLM token log lines** | `knowledge/hooks.py:218`, `chat/runner.py:109`, `routers/insights.py:586` | Counts only (no prompt text) | ✅ Safe. |
| 7 | **`/metrics`** | `main.py:279-292` | Label cardinality bounded to route templates (`observability.py:224-231`); bearer-gated, prod fail-fast (`config.py:273-279`) | ✅ No PII; no raw paths. |

**Current `log_event` call sites are all clean** (verified: ids, booleans, `channel_id`,
`api_key_id` — never email/token; e.g. `routers/auth.py:157-162`, `routers/api_keys.py:158-161`).
**Audit conclusion:** the boundary *currently* holds everywhere, but sinks #1/#2/#4 hold it by
discipline, not structure. One scrub added to `JsonLogFormatter` (mirroring `_REDACT_SUBSTRINGS`,
`event_log.py:39-56`) closes the structural gap across all three at once.

---

## 4. Proposed issues (dependency-ordered, `docs/issues.md` house style)

### Issue 166: Redaction backstop on the stdout/file log sink
**What**: Add a key-blocklist scrub to `JsonLogFormatter` (and/or `RequestIDLogFilter`) mirroring
`event_log._REDACT_SUBSTRINGS`, so the stdout + `app.log` sinks enforce the no-PII/token rule
structurally, not by call-site discipline. Apply the same scrub to the `/api/activity` file-sink
path (`routers/activity.py:55`).
**Acceptance criteria**:
- [ ] A `log_event("x", email="a@b.com", token="sk-…")` line emits `[redacted]` for both keys in JSON mode
- [ ] DB-sink behavior (`event_log._redact`) unchanged; scrub logic shared/DRY (no duplicate blocklist)
- [ ] Unit test asserts redaction on stdout JSON for each blocklisted substring
- [ ] No regression in Layer-0 gates
- [ ] **`DECISIONS.md` entry**: formatter-level redaction (deviation from "call-site discipline only").

### Issue 167: Instrument load-bearing surfaces with `log_event`
**What**: Add `log_event` lines to the render/clip pipeline stages (`worker/tasks.py`), ingestion,
billing webhook receipt/processing, and upload-intel — so every load-bearing user action is a grep,
not a bisect. Cover the swallowed-exception / anonymous-event classes from `OFF_COURSE_BUGS.md`.
**Acceptance criteria**:
- [ ] Each pipeline stage emits `event=…_started` / `…_done` / `…_failed` with `creator_id` + `task_id`
- [ ] Billing webhook emits received/processed/rejected events (no signature/secret in fields)
- [ ] No PII/token in any new field (gated by Issue 166's backstop)
- [ ] Test asserts the render-failure path emits a `*_failed` event

### Issue 168: SLO definitions + first burn-rate alerts (the detection gap)
**What**: Define 2 SLOs — API availability (5xx rate) and Celery task-success rate — and ship a
single fast-burn page alert per SLO off the metrics already emitted (`observability.py:62-76`).
Document the Prometheus recording rules + Alertmanager (or Grafana Cloud) routing.
**Acceptance criteria**:
- [ ] `/metrics` is actually scraped (config committed); SLO targets documented in `docs/DEPLOYMENT.md`
- [ ] Fast-burn alert fires in a synthetic error-injection test
- [ ] Alert routes to a real channel (cross-ref prompt 11 / notifications)
- [ ] **`DECISIONS.md` entry**: SLO targets + burn-rate thresholds chosen (cite Google SRE Workbook).

### Issue 169: Pipeline + LLM-cost metrics; populate the `usage` ledger
**What**: Add a render-failure counter, per-stage Celery duration labels, an LLM token/cost counter
(OTel-GenAI-shaped labels: provider, model, kind), and **write the `usage` table**
(`models.py:664-677`) from the token usage already computed at every Anthropic call site.
**Acceptance criteria**:
- [ ] `usage.tokens_in`/`tokens_out` increment per creator+period after each LLM call
- [ ] Prometheus exposes a token counter labelled by model/kind; render-failure counter present
- [ ] Token-log lines remain (counts only); no prompt text in metrics labels (cardinality-safe)
- [ ] **`DECISIONS.md` entry**: token-metric label schema (align with OTel GenAI semconv).
- [ ] Cross-ref: billing + prompt 07 consume this.

### Issue 170: App-level saturation gauges
**What**: Add the missing 4th golden signal at app level — SQLAlchemy pool checked-out gauge,
Celery queue depth (Redis `LLEN`), Redis used-memory — so the saturation class behind the
Redis-down cascade (`OFF_COURSE_BUGS.md:17`) and PgBouncer outage (`:25`) is observable.
**Acceptance criteria**:
- [ ] Three gauges exposed on `/metrics`; cardinality bounded
- [ ] A queue-backlog warning alert defined (builds on Issue 168)
- [ ] No new connection churn (reuse existing pools / health singleton, `main.py:55`)

### Issue 171: Worker durable log sink
**What**: Pass `log_dir` to the worker's `configure_logging` (`worker/celery_app.py:15`) so worker
JSON logs survive container restarts like the API's `app.log` does.
**Acceptance criteria**:
- [ ] Worker writes a rotating JSON log; `request_id` present on every line
- [ ] No double-logging when API + worker share a host/volume

### Issue 172: Log aggregator for the GKE target (Loki) — 10k-scale
**What**: Adopt a log aggregator for the Kubernetes target; `app.log` + tunnel-grep does not scale
past one VM. Recommend Grafana Loki (Prometheus/Grafana-native, GCS-backed, cost-efficient).
**Acceptance criteria**:
- [ ] Logs from API + worker pods queryable by `request_id` and `creator_id` in one place
- [ ] Collector-side scrub as defense-in-depth (OWASP layered redaction)
- [ ] **`DECISIONS.md` entry**: Loki vs. GCP Cloud Logging chosen (see open questions).

### Issue 173: OpenTelemetry distributed tracing on the existing rail — 10k-scale
**What**: Adopt OTel tracing reusing the current API→Celery propagation (`observability.py:241-249`):
emit W3C `traceparent` alongside `x_request_id`, auto-instrument FastAPI/Celery/SQLAlchemy/httpx,
keep `request_id` as a span attribute, head-sample ~10%.
**Acceptance criteria**:
- [ ] A render request produces one trace spanning API → Celery → DB → Anthropic/Voyage/YouTube/R2
- [ ] `request_id` correlates a log line to its trace
- [ ] Sampling + batch export configured; perf overhead measured
- [ ] **`DECISIONS.md` entry**: revisits the 2026-05-29 "tracing deferred" decision (`DECISIONS.md:3930-3932`).

**`DECISIONS.md` entries required:** Issues **166, 168, 169, 172, 173** (5 of 8). 167, 170, 171 are
straight extensions of existing patterns, no deviation.

---

## 5. Open questions for the human (one-line answers)

1. **Managed vs. self-hosted observability?** Grafana Cloud (logs+metrics+traces+alerts, one bill,
   fast) vs. self-hosted Prometheus+Loki+Grafana on GKE (cheaper at scale, more ops). Pick one
   before Issues 168/172/173 — it sets the whole stack.
2. **Loki vs. GCP Cloud Logging** for the GKE target? Loki = unified with Grafana/Prometheus + cheap
   GCS storage; Cloud Logging = zero-ops on GKE but pricier and less Grafana-native. (Issue 172.)
3. **Is `/metrics` scraped by *anything* in beta today, or truly unwatched?** Confirms whether #1 is
   "no dashboard" or "no scrape at all" — changes Issue 168's first step.
4. **Has a live `source='ui'` `event_logs` row been confirmed in prod since the Issue-155 deploy?**
   `OFF_COURSE_BUGS.md:34` says confirmation was pending; funnel trust (prompt 07) depends on it.
5. **Should LLM token usage write the `usage` ledger now (billing-grade) or stay metrics-only for
   beta?** Decides Issue 169's scope and its coupling to billing.

---

### Stale / contradictory docs flagged
- `DECISIONS.md:3910-3913` and `observability.py:60-61` both assert saturation is "observed at the
  infra layer" — but no infra saturation monitoring is documented anywhere (`docs/DEPLOYMENT.md`
  has only `/health`). The claim is aspirational; §2.2/Issue 170 corrects it.
- `observability.py:112-114` describes `log_event` as making prod debugging "a grep, not a bisect" —
  true only for the 8 instrumented files; the render pipeline (the most failure-prone surface) is
  not instrumented. Issue 167 closes the gap between the docstring's promise and reality.
- `OFF_COURSE_BUGS.md:34` (Issue 155) marks UI telemetry "✅ Fixed" but notes live prod confirmation
  was still pending — verify before relying on funnel data (open question 4).
