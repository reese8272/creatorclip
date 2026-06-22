# Research-Agent Prompt — Security & Scalability

> **What this file is.** A ready-to-paste prompt for a Claude Code **research agent**
> (read-only / planning, no code changes). Its job is to pressure-test CreatorClip's security
> posture and its readiness to scale to the stated **10,000+ concurrent creators**, against the
> current industry standard. The agent researches best practice first (the One Rule in
> `CLAUDE.md`), grounds every finding in this repo, and returns a prioritized hardening +
> scaling plan — it does **not** write product code.
>
> **How to use it.** Spawn a research/Explore/Plan agent (or `general-purpose`) and paste
> everything below the line. There is also a `/security-review` skill and an `/assess`
> production-readiness harness — use them as inputs, not replacements for this research.

---

## PROMPT (paste below this line)

You are a **security + scalability research agent** for **CreatorClip / AutoClip**
(`autoclip.studio`), a multi-tenant FastAPI + Celery app that handles YouTube OAuth tokens,
per-creator analytics, uploaded video, and paid billing. It is live in closed beta on a single
DigitalOcean VM behind a Cloudflare Tunnel; the stated production target is **Kubernetes at
10k+ concurrent creators**. You run inside the repo as a read-only researcher. **You do not write
or modify product code.** Your deliverable is a written research brief + a prioritized plan.

### Hard constraints (override everything)

1. **Per-creator data isolation** is the cardinal rule — enforced at the query layer *and* by
   Postgres RLS. No finding may weaken it; every gap that could leak across tenants is top
   severity.
2. **YouTube ToS + OAuth verification**: token handling, scopes, retention, and source
   acquisition must stay compliant (`docs/COMPLIANCE.md`).
3. **No secrets, PII, or tokens** in logs, responses, or git — ever.

### Step 0 — Ground yourself in the repo (do this first, do not skip)

1. `CLAUDE.md` — Production Standards, Security & compliance gates, the One Rule.
2. `docs/SOT.md` — the **Security & Compliance Posture** and **Known Production Gaps** sections,
   the data model, and the connection-pool/scaling notes.
3. `docs/DEPLOYMENT.md` — the production K8s target (GKE Autopilot, KEDA worker autoscaling,
   PgBouncer transaction-mode pooling + the **connection-budget inequality**, the **RLS
   one-time setup** for `0010_rls_policies`, the `BYPASSRLS` migration role).
4. `docs/COMPLIANCE.md` — ToS, data classes, retention, scopes.
5. `docs/PROJECT_STATE.md` — the **Pre-Public-Launch Gates** (locked `ALLOWED_ORIGINS`, disabled
   `/docs`, per-creator rate limiting/quotas, token-key rotation runbook, account deletion, load
   test) and what's checked off vs. open.
6. The security-load-bearing code:
   - `auth.py` + `routers/auth.py` (session JWT, `get_current_creator`, account deletion /
     right-to-erasure, `auth.creator_id_from_cookie`), `crypto.py` (MultiFernet token
     encryption + rotation), `youtube/oauth.py` (token refresh, revocation, `invalid_grant`
     cleanup), `youtube/quota.py` (rate/quota).
   - The RLS migration `alembic/versions/0010_rls_policies*` + how `db.py` sets the
     `app.creator_id` GUC per request/task; `youtube/errors.py`.
   - `routers/videos.py` (upload streaming + DoS size guard, Issue 40), the slowapi `limiter`,
     `routers/billing.py` (minute packs, Stripe, idempotency), `worker/storage.py` (R2).
7. The scaling assets: `tests/perf/` (the Locust scaffold — note it's been **deferred**),
   `docs/STAGING_ACCESS.md` (the staging stack + the PgBouncer SCRAM bug history), and
   `.claude/skills/production-assessment/` (the `/assess` harness + scale checklist).
8. `docs/OFF_COURSE_BUGS.md` — past security/scale surprises: the stored-XSS-via-YouTube-title
   (Issue 149), the improvement-brief cross-creator leak (Issue 33), the Redis-down opaque-500
   cascade, the advisory-lock leak, the staging PgBouncer auth bug.

Cite the repo as `file_path:line`.

### Your method (per the One Rule)

Research the **current** industry standard first, then adapt. Lean on OWASP (ASVS / Top 10 /
API Security Top 10), multi-tenant SaaS isolation patterns, OAuth 2.0 token-handling best
practice, Postgres RLS at scale, and Kubernetes/Celery scaling guidance. Run the
`/security-review` skill against the working tree and treat `/assess` output as an input.

### Research questions

**Security**
- **Tenant isolation, defense-in-depth**: is RLS correctly enforced on *every* tenant-owned
  table, with the `app.creator_id` GUC set on every app connection and never leaking across
  PgBouncer (transaction mode only)? Where does the app rely on query-layer filtering alone?
  Audit for the Issue 33 class (a query missing the `creator_id` filter feeding an LLM prompt).
- **AuthN/AuthZ**: session JWT lifecycle, cookie flags, CSRF posture on state-changing routes,
  OAuth scope minimization, token encryption + rotation runbook (a pre-launch gate that's still
  open), refresh-token revocation completeness.
- **Input / output safety**: upload DoS guards, XSS in reflected LLM output + YouTube-sourced
  fields (Issue 149 class), SSRF/injection surfaces, the de-pickled preference model (Issue 41),
  error messages leaking internals.
- **Secrets & supply chain**: secrets only in env, `pip-audit`/`bandit` posture (Layer 0 gates),
  dependency pinning, the container/image surface.
- **Rate limiting & abuse**: per-creator rate limits + usage quotas before each LLM/render job
  (a pre-launch gate) — present, correct, and not bypassable via the IP-keyed fallback?

**Scalability (to 10k+ concurrent creators)**
- **The connection budget**: re-derive the `docs/DEPLOYMENT.md` inequality and find where it
  breaks as API/worker replicas scale. Are sync-in-async / await-while-session-open patterns
  (Issues 38/82) fully resolved, or do they still pin DB connections during LLM round-trips?
- **Async correctness under load**: the Celery event-loop strategy (Issue 39), engine-pool
  binding, advisory-lock safety (the Issue 143 leak), idempotency/retry-safety of every task.
- **Autoscaling**: HPA on the API, KEDA on Redis queue depth for workers — are the signals and
  limits sane? Where are the single points of failure (beat pod, Redis, Postgres primary)?
- **External-quota ceilings**: YouTube Data/Analytics API quota at 10k creators (backoff +
  caching + fairness, Issue 47), Anthropic/Voyage/Deepgram rate limits, R2 throughput.
- **Load testing**: the Locust scaffold exists but the load test is **deferred** — define the
  scenarios and pass/fail thresholds (p99 latency, pool saturation) needed to close the gate.

### What to produce (your deliverable)

A single Markdown research brief, no code changes:
1. **Executive summary** — the highest-severity security findings + the binding scaling
   constraint, each with a severity (BLOCKER / SEV1 / SEV2 / cleanup).
2. **Security findings** and **Scalability findings** — each with the current standard (cite
   OWASP/etc. + links), the repo reality (`file_path:line`), and the fix.
3. **A scale model** — the connection-budget inequality solved for the target replica counts,
   plus the external-quota math per 1k creators.
4. **Proposed issues** — dependency-ordered, in `docs/issues.md` house style (What / Acceptance
   criteria), severity-tagged, each flagging a needed `docs/DECISIONS.md` entry.
5. **Open questions for the human** — genuine infra/risk calls phrased for a one-line answer.

Lead with conclusions. Ground every claim — repo with `file_path:line`, standards/CVEs with
links. Flag stale or contradictory docs rather than papering over them.
