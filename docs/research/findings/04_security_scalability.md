# Research Brief — Security & Scalability (Prompt 04 / Issue 169)

**Author:** security + scalability research agent (read-only)
**Date:** 2026-06-22
**Scope:** broad security posture + scale to 10k+ concurrent creators. **Out of scope** (cross-referenced, not duplicated): LLM prompt-injection / output safety → prompt 09 (Issue 174); privacy law / GDPR-CCPA / erasure-export → prompt 12 (Issue 177); DR / backups → prompt 175; observability → prompt 170.
**Method:** current industry standard researched first (OWASP, RFC 9700, AWS/PostgreSQL RLS guidance, KEDA/Celery scaling, Google/Anthropic quota docs), then grounded in the repo at `file_path:line`. Every external claim cites a link; unverifiable items are flagged.

---

## 1. Executive Summary

The codebase is unusually mature on the **per-request** security boundary: Postgres RLS with `FORCE` on 12 tenant tables (`alembic/versions/0010_rls_policies.py:111-125`), a transaction-scoped GUC via `set_config(..., true)` (`db.py:158-161`) that is the textbook PgBouncer-transaction-mode-safe pattern, MultiFernet token encryption with a rotation path (`crypto.py`), and per-creator rate limiting on ~54 routes. The honesty/ToS posture is strong.

The two highest-severity findings are structural, not cosmetic:

| # | Finding | Severity |
|---|---------|----------|
| S1 | **The entire Celery worker tier runs as the `BYPASSRLS` migrate role** (`db.py:55-62`, `worker/tasks.py` uses `AdminSessionLocal` for ~30 tenant queries). RLS — the one structural defense added after the Issue 33 cross-tenant leak — does **not** protect the pipeline that does most cross-tenant data handling (ingest → score → DNA → render → improvement-brief). The Issue 33 class of bug (a query missing `creator_id`, then fed to an LLM) is structurally re-exposed everywhere a worker builds a prompt. | **BLOCKER** |
| S2 | **No HTTP security-headers middleware.** `main.py` adds CORS, cache-bust, request-id and telemetry middleware but emits no `Content-Security-Policy`, `Strict-Transport-Security`, `X-Frame-Options`, `X-Content-Type-Options`, or `Referrer-Policy`. Given the documented stored-XSS history (Issue 149) and a cookie-auth SPA, this removes the browser-side backstop OWASP treats as baseline. | **SEV1** |
| S3 | **CSRF defense rests on `SameSite=Lax` alone** (`routers/auth.py:172`). Lax does not cover `POST`-shaped CSRF via `GET`-triggerable side effects and offers no defense-in-depth; OWASP's 2025 guidance is to layer Fetch-Metadata or double-submit on top for state-changing cookie-authed routes. | **SEV2** |

The **binding scaling constraint is the Postgres connection budget**, and it is already violated by the committed Helm values. At `values.prod.yaml` ceilings the fleet demands **~1,750 server connections** against a Cloud SQL Postgres whose default `max_connections` is 100-400 — the workers connect **directly** to Postgres with no PgBouncer (`deploy/charts/creatorclip/templates/worker/deployment.yaml` has no pgbouncer sidecar; only the app pod does). The DEPLOYMENT.md inequality is correct in form but its inputs are stale and the worker term is unpooled. This is a **BLOCKER** for 10k.

The load-test gate (`tests/perf/locustfile.py`) exists but is **deferred** and has never run green against a working staging stack (staging was broken until Issue 142); it cannot currently close the pre-launch gate.

Proposed issues: **11** (3 BLOCKER, 2 SEV1, 4 SEV2, 2 cleanup). **5 require a `docs/DECISIONS.md` entry** (S1 worker-RLS strategy, the connection-budget re-derivation, the YouTube quota-extension architecture, CSRF strategy choice, load-test pass/fail thresholds).

---

## 2. Security Findings

### S1 — Worker tier bypasses RLS entirely  **[BLOCKER]**

**Standard.** Defense-in-depth for multi-tenant SaaS is RLS *underneath* always-filter, on **every** connection that touches tenant data — not only the web path. OWASP ranks Broken Access Control #1 (94% of apps tested had some form). AWS's multi-tenant RLS guidance is explicit that the database policy is the safety net precisely because application code forgets the `WHERE`. ([AWS RLS](https://aws.amazon.com/blogs/database/multi-tenant-data-isolation-with-postgresql-row-level-security/), [OWASP Top 10 A01](https://owasp.org/Top10/A01_2021-Broken_Access_Control/))

**Repo reality.** Two engines exist (`db.py:14-77`): the app engine (`creatorclip_app`, no BYPASSRLS, RLS-gated) and `admin_engine` (`creatorclip_migrate`, `BYPASSRLS`). `worker/tasks.py` uses `db.AdminSessionLocal()` for essentially all tenant work — 30+ call sites (e.g. `worker/tasks.py:367,422,445,514,569,...`). The `after_begin` GUC listener (`db.py:132-161`) only fires when `session.info["creator_id"]` is set, which workers never set. The original SEV-0 leak (Issue 33, `docs/COMPLIANCE.md:158-183`) was an unfiltered `select(VideoMetrics).limit(50)` whose averages went into a Claude prompt — that exact code path lives in the worker-driven brief generation today, with RLS providing **zero** protection there. The migration's own docstring (`0010_rls_policies.py:16-18`) states this is by design ("Celery worker tasks connect as `creatorclip_migrate` (BYPASSRLS)").

**Why it's a BLOCKER.** The product's cardinal rule (CLAUDE.md) is per-creator isolation, and the worker is where DNA, scoring, and improvement briefs assemble cross-creator-capable queries into LLM prompts. A single forgotten filter there is an undetectable cross-tenant leak with no DB backstop.

**Fix.** Run worker tenant-scoped tasks on the **RLS-gated app role** with the `creator_id` GUC set per task (workers already know the `creator_id` they were dispatched with). Reserve `AdminSessionLocal`/BYPASSRLS for genuinely cross-tenant sweeps (purge, beat refresh, advisory-lock admin) and add child-table RLS policies (`video_metrics`, `retention_curves`, `transcripts`, `signals`, `clip_outcomes` — left unpoliced per `0010_rls_policies.py:38-43`) so JOIN-free worker queries are still gated. Requires a `DECISIONS.md` entry (it changes the documented worker-role strategy).

### S2 — No security-headers middleware  **[SEV1]**

**Standard.** [OWASP Secure Headers Project](https://owasp.org/www-project-secure-headers/) baseline: `Content-Security-Policy`, `Strict-Transport-Security`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`. FastAPI ships none by default and must add them via middleware ([FastAPI security guide](https://davidmuraya.com/blog/fastapi-security-guide/)).

**Repo reality.** `main.py:213-276` registers CORS, `StaticCacheBustMiddleware`, the http-event logger, and `RequestIDMiddleware` — no response-header hardening. The cache-bust middleware sets `cache-control: no-store` on HTML (`main.py:201`) but that is not a security header. Given Issue 149 (stored XSS via a YouTube title rendered into `innerHTML`) and Issue 138's reflected-LLM-output XSS sweep, a CSP would have been the structural backstop both relied on instead.

**Fix.** Add a small response-header middleware (HSTS only when `ENV=production`; a CSP scoped to the SPA's asset origins; `frame-ancestors 'none'`; nosniff). Pin the headers with a `test_static`-style assertion. No DECISIONS entry (industry-baseline).

### S3 — CSRF rests on SameSite=Lax alone  **[SEV2]**

**Standard.** [OWASP CSRF Cheat Sheet (updated Dec 2025)](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html) now lists **Fetch Metadata** as a complete standalone defense; for cookie-authed apps it recommends SameSite *plus* a token/Fetch-Metadata layer, not SameSite alone. `Lax` permits cross-site top-level navigations and is weaker than `Strict` for state-changing routes.

**Repo reality.** The session cookie is `httponly`, `samesite="lax"`, `secure` in prod (`routers/auth.py:167-174`) — correct as far as it goes, and the OAuth `state` cookie is validated (`routers/auth.py:77-79`), which protects the login flow. But every state-changing route (`POST /videos/upload`, `POST /clips/...`, `DELETE /auth/me`, `POST /billing/checkout`) relies on cookie auth + Lax with no second factor. The SPA already sends requests via `lib/api.ts`, so a custom-header / Fetch-Metadata check is cheap.

**Fix.** Add a `Sec-Fetch-Site` check (reject `cross-site` on state-changing methods) or a double-submit header the SPA already controls. Choose one and log it in `DECISIONS.md`.

### S4 — OAuth/token handling: strong, with two gaps  **[SEV2 / cleanup]**

**Standard.** [RFC 9700 (Jan 2025)](https://datatracker.ietf.org/doc/rfc9700/): encryption at rest, short access-token life, refresh-token rotation, revocation invalidates the whole family. ([Obsidian](https://www.obsidiansecurity.com/blog/refresh-token-security-best-practices))

**Repo reality — good.** Tokens are MultiFernet-encrypted (`crypto.py:13-43`), never logged (decode failures log the exception *class* only, `limiter.py:52-57`), `invalid_grant` is treated as permanent and the row deleted (`youtube/oauth.py:251-260`), refresh is serialized by a Redis lock with Lua compare-and-delete and a fail-open posture (`youtube/oauth.py:307-332`), and account deletion revokes the refresh token at Google then cascade-deletes (`routers/auth.py:204-282`). Scopes are minimal and read-only (`youtube/oauth.py:46-52`, `docs/COMPLIANCE.md:92-102`).

**Gaps.**
- **[SEV2] The `TOKEN_ENCRYPTION_KEY` rotation runbook is a still-open pre-launch gate.** `docs/SOT.md:461` says "not yet written," but `docs/RUNBOOKS.md` is referenced as canonical and `scripts/rotate_token_key.py` exists — the docs **contradict each other**. Verify the runbook is complete and flip the SOT gate, or write it. (Flagged stale-doc.)
- **[cleanup] Session JWT cannot be revoked server-side.** It's a stateless HS256 token (`auth.py:18-29`); logout only deletes the cookie (`routers/auth.py:181`). A stolen cookie is valid until `exp` (60 min default). Acceptable at this lifetime, but note it — there is no token-family revocation for the *session* layer the way there is for the OAuth layer.

### S5 — Input/output safety: mostly closed  **[cleanup]**

- **Upload DoS guard (Issue 40):** correct — streams in 1 MB chunks with a hard `UPLOAD_MAX_MB` cap and a single outer `try/finally` for temp cleanup (`routers/videos.py:234-294`). Minor: it does not reject early on the `Content-Length` header, so a lying client still streams up to the cap before the 413; cheap to add, low value.
- **De-pickled preference model (Issue 41):** **closed.** `preference/model.py` loads `weights_blob` through a `_RestrictedUnpickler` allowlist (`preference/model.py:40-132`) that raises `UnpicklingError` on any class outside the numpy/sklearn set — the standard mitigation for the pickle-RCE surface.
- **XSS (Issue 149 class):** the SPA migration (Issue 85) removed the `innerHTML` rendering path; no `dangerouslySetInnerHTML` (confirmed in Issue 153-159 audit, `docs/PROJECT_STATE.md:71`). CSP (S2) is the missing structural backstop. Reflected-LLM-output safety is **prompt 09's lane**.
- **SSRF:** all Google/YouTube calls funnel through `youtube/oauth.py` and `youtube/_http.py` helpers (no user-supplied URLs); `yt-dlp` is off by default and unwired (`docs/COMPLIANCE.md:50-63`). No open SSRF surface found.

### S6 — Rate limiting & abuse  **[SEV2]**

**Repo reality.** slowapi limiter keyed per-creator via `request.state.creator_id` (`limiter.py:61-77`), with a hardened JWT path (`exp` verified, 60s leeway, narrow exception — Issue 106, `limiter.py:40-58`). ~54 `@limiter.limit` decorators across routers, with LLM/render routes tightened to `10-20/hour` (e.g. `routers/clips.py`, `routers/analysis.py`). Storage is Redis-backed (no in-memory fallback — by design, but it means a Redis outage opens the limiter; logged as the Issue-76 cascade in `OFF_COURSE_BUGS.md:17`).

**Gaps.**
- **[SEV2] The IP-keyed fallback is bypassable for cost-bearing work.** `creator_key()` and `_creator_key()` fall back to `get_remote_address` when no creator is resolved (`limiter.py:58,76`). The genuinely expensive endpoints all require `get_current_creator` (so they get the per-creator key), which is good — but confirm no LLM/render route can be reached with the IP fallback active. The cost ceiling on actual spend is the **minutes balance**, enforced at `check_positive_balance` / `check_balance_for_minutes` before R2 PUT (`routers/videos.py:232,287`) and in the worker (`worker/tasks.py:1683-1691`) — this is the real abuse backstop and it is present. Chat has its own daily cap (`config.py:76`, `routers/chat.py:118`).
- **The pre-launch gate "per-creator rate limiting + usage quotas before each LLM/render job"** is **substantially met** (rate limits + minutes ledger), but there is no single documented place asserting *every* LLM/render entrypoint is gated. Recommend a structural test enumerating LLM/render routes and asserting each carries both a limiter and a balance pre-check.

### S7 — Secrets & supply chain  **[cleanup — healthy]**

Secrets are env-only via pydantic-settings with prod fail-fast (`config.py:261-263`); `requirements.txt` is `==`-pinned with CVE-bump comments (starlette 1.3.1 for CVE-2026-54283/48818, cryptography 48.0.1, python-multipart 0.0.31, aiohttp 3.14.1). Layer 0 gates run ruff/mypy/bandit/pip-audit per CLAUDE.md. **One supply-chain note [cleanup]:** the Helm pgbouncer image is `bitnami/pgbouncer:1.22.0` (`values.yaml:93`) while `OFF_COURSE_BUGS.md:25` records the pinned `edoburu/pgbouncer` tag vanished from Docker Hub and staging fell back to `:latest` — pin a digest and reconcile the two pgbouncer images (staging vs Helm) before prod.

---

## 3. Scalability Findings

### C1 — The connection budget is already violated by committed Helm values  **[BLOCKER]**

**Standard.** PgBouncer transaction-pooling is correct for RLS + async SQLAlchemy ([PostgreSQL RLS + PgBouncer guidance](https://ricofritzsche.me/mastering-postgresql-row-level-security-rls-for-rock-solid-multi-tenancy/)). The whole point is to keep server-side connections bounded well under Postgres `max_connections`; Cloud SQL Postgres 16 defaults are low (≈100-400 by tier).

**Repo reality / the inequality.** DEPLOYMENT.md:51-56 states:
`Σ(PgBouncer default_pool_size) + Σ(celery_pool × worker_concurrency × worker_replicas) ≤ Postgres max_connections − superuser_reserved`.

Solving with the **committed prod values**:
- App tier: HPA max **20** pods (`values.prod.yaml:14-15`), each with a PgBouncer sidecar at `defaultPoolSize: 50` (`values.prod.yaml:32-33`) → **20 × 50 = 1,000** server connections. (The app→sidecar leg is fine: per-pod SQLAlchemy ceiling is 20 (`db.py:39-40`) ≤ 25/50 sidecar.)
- Worker tier: KEDA max **50** replicas (`values.prod.yaml:18`), **no PgBouncer sidecar** (`templates/worker/deployment.yaml` — connects direct via `envFrom`), `--concurrency=2` (`templates/worker/deployment.yaml:34`). Each worker process owns an `admin_engine` pool of `pool_size 5 + max_overflow 10 = 15` (`db.py:59-60`). Worst case ≈ **50 × 15 = 750** direct connections (concurrency 2 means 2 tasks share the per-process pool, so the pool ceiling, not concurrency, dominates).
- **Total peak ≈ 1,750 server connections** vs a Cloud SQL `max_connections` that is almost certainly 100-800. **The inequality fails by 2-15×.**

**Why BLOCKER.** Two compounding problems: (1) the magnitudes are off by an order of magnitude, and (2) **the worker tier has no pooler at all** — it is the unpooled term that breaks first, and it scales to 50 replicas. Even the app term (1,000) likely exceeds the DB tier.

**Fix.**
1. Put **PgBouncer in front of the workers too** (sidecar or a shared PgBouncer Deployment), transaction mode — note the `prepare_threshold=None` already set (`db.py:33`) is the prerequisite and is correctly in place.
2. Re-derive the inequality for real Cloud SQL `max_connections` and record the chosen `defaultPoolSize`, HPA/KEDA maxima, and DB tier in `DECISIONS.md`.
3. Shrink the worker `admin_engine` pool (15 is large for `--concurrency=2`).
4. **Per 1k creators sizing** (see §4) to back the numbers.

### C2 — Async correctness under load: largely resolved  **[cleanup — healthy, with one risk]**

The historically dangerous patterns are fixed: per-worker singleton event loop with engine rebind on fork (Issue 39, `worker/celery_app.py:54-88`, `db.py:83-116`), sync-in-async offloaded to `to_thread` (Stripe `routers/billing.py:145`, transcription/Voyage/boto3 throughout `worker/tasks.py:495,602,693,817`), `acks_late + reject_on_worker_lost + prefetch_multiplier=1` (`worker/celery_app.py:34-39`), and the advisory-lock leak (Issue 143) fixed with rollback-before-unlock + `finally`-clause unlocks (`worker/tasks.py:415-418` and the `pg_advisory_unlock` finally blocks at 1386/1533/1648/1885/1991). Tasks are idempotent on stable keys (`worker/tasks.py:325,1147`).

**Residual risk [SEV2 → verify under load]:** the **token-refresh-during-session-open** pattern. `get_valid_access_token` (`youtube/oauth.py:283-361`) does a Google HTTP round-trip (and up to 3×200ms polls) while holding the caller's DB session. On the API path this can pin a connection for ~600ms+ during a refresh storm. Issues 38/82 fixed the LLM-round-trip-while-session-open class for the heavy LLM calls (now offloaded), but confirm the token-refresh path releases or doesn't hold a pooled connection across the Google call — this is exactly the kind of hold the connection budget can't absorb at 10k. **Needs load-test confirmation, not just code reading.**

### C3 — Autoscaling & single points of failure  **[SEV2]**

**Standard.** KEDA on Redis `listLength` is the canonical Celery autoscaler ([ThinhDA](https://thinhdanggroup.github.io/blog-on-auto-scaling-celery-tasks/), [KEDA](https://keda.sh/)); guidance warns against aggressive scale policies (flapping) and to make Redis/beat HA. ([KodeKloud](https://notes.kodekloud.com/docs/Kubernetes-Autoscaling/Kubernetes-Event-Driven-Autoscaling-KEDA/KEDA-Scaling-With-Redis-List))

**Repo reality.** Sound: HPA on app CPU 70% (2-20 pods), KEDA on `celery` queue depth `listLength=5/replica` (1-50), beat pinned to 1 (`values.yaml:38-52`, `values.prod.yaml`, `templates/beat/deployment.yaml:9-11`). **SPOFs:**
- **Beat (1 replica):** correct that it must be singleton (duplicate scheduling otherwise), but a beat outage silently halts token refresh, analytics refresh, media purge, and the 30-day staleness purge — the last is a **ToS-compliance** obligation (`docs/COMPLIANCE.md:26-39`). Needs a liveness probe + alert, and ideally a redundant scheduler with a lock (e.g. a leader-elected beat or RedBeat). **[SEV2]**
- **Redis (single):** broker + limiter + quota + SSE progress + refresh-lock all ride one Redis (`values.yaml:71-74` points at a single `redis-service`). Its outage degrades to opaque 500s today (`OFF_COURSE_BUGS.md:17`). Managed HA Redis (Memorystore/Upstash with replica) before 10k. **[SEV2]**
- **KEDA `listLength` only counts the default `celery` queue** (`keda.redisQueueName: "celery"`); confirm no tasks route to other queues that KEDA can't see (priority queues for clip-render vs catalog-sync are mentioned in `config.py:156` "mixed pool" comment).

### C4 — External-quota ceilings: the YouTube Data API is a hard wall  **[BLOCKER for 10k]**

**Standard / the binding number.** The **YouTube Data API quota is per-Google-Cloud-project, 10,000 units/day, shared across ALL users**, and Google's Developer Policies require **exactly one project per API client** — you cannot shard across projects to scale ([Google quota guide](https://developers.google.com/youtube/v3/getting-started), [ChannelCrawler](https://channelcrawler.com/insights/youtube-api-daily-limit-quotas-costs-and-how-to-scale-beyond-10-000-units-channelcrawler)). Raising it requires a **compliance audit** by YouTube's API Services team. The YouTube **Analytics** API is metered separately (per-user query quotas, far less of a wall than the Data API). ([Google quota_and_compliance_audits](https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits))

**Repo reality.** Quota is tracked atomically in Redis with a Lua check-then-incr against `YOUTUBE_QUOTA_DAILY_UNITS=8000` (`youtube/quota.py:39-83`, `config.py:180`) and PT-keyed daily reset (Issue 76). Costs are correct (captions = 50 units, `youtube/quota.py:35`). **But:** the 8,000-unit budget is a single global pool with **no per-creator fairness** — the Beat refresh fan-out (`refresh_youtube_analytics` over every creator) can drain the day's budget and starve interactive onboarding, the exact degradation the module docstring worries about but does not prevent. `consume()` is global; there is no per-creator sub-budget.

**The 10k math (per 1k creators):** onboarding one creator costs ≈ channels.list (1) + playlistItems pages (~1-5) + videos.list batches (~1 per 50 videos) + captions.list (50 **each** if pulled) ≈ **10-60+ units**, plus a daily analytics refresh. 1,000 creators ≈ **10,000-60,000 units/day just for refresh** — i.e. **one project's entire default quota is exhausted by the first ~150-1,000 creators.** At 10k creators the Data API need is **100k-600k units/day**, requiring a quota-extension audit and aggressive caching (ETags, field filtering, batching — Google says 50-80% reduction is achievable). **This is a BLOCKER and needs a `DECISIONS.md` entry** documenting the quota-extension plan + per-creator fairness budget + caching strategy.

**Anthropic / Voyage / Deepgram / R2:**
- **Anthropic** rate limits are org-wide RPM/ITPM/OTPM by tier ([rate-limits](https://platform.claude.com/docs/en/api/rate-limits)): Tier 4 ≈ 4,000 RPM / 2M ITPM / 400k OTPM for Sonnet/Opus. Prompt caching (mandatory per CLAUDE.md) and the per-creator LLM rate limits (`10-20/hour`) keep this manageable, but 10k creators each generating clips will need Tier 4 + the **Batch API / service tiers** for the non-interactive worker LLM calls. Cross-reference prompt 09/167 (LLM cost). Confirm a 429/529 backoff exists on every Anthropic call (the stream wrapper is `worker/anthropic_stream.py`).
- **Voyage / Deepgram:** per-key concurrency caps; both are offloaded to threads already. Size their plans against the per-1k ingest rate. Not researched in depth (lower risk).
- **R2:** zero egress, high throughput — not a ceiling; watch S3 request-rate per prefix (`source/{creator_id}/`, `clips/{creator_id}/`) which is naturally sharded by creator.

### C5 — Load testing: gate cannot close today  **[SEV1]**

`tests/perf/locustfile.py` exercises the hot authenticated READ paths + a light write across N seeded creators (`CC_CREATOR_IDS` fan-out, `tests/perf/locustfile.py:36-46`) — a reasonable read-path scaffold. **But:** it is explicitly deferred, it does **not** drive the Celery pipeline (by design — but that's where the connection budget and worker pooling break, §C1/C2), and it has never run green (staging was unusable until Issue 142, `OFF_COURSE_BUGS.md:25`). The pre-launch load-test gate (`docs/PROJECT_STATE.md`) is therefore **open and unclosable** until thresholds are defined and a run is executed. Define scenarios + pass/fail (below) and log them in `DECISIONS.md`.

---

## 4. Scale Model

### Connection budget — solved for the committed prod ceilings

| Term | Source | Value |
|------|--------|-------|
| App pods (HPA max) | `values.prod.yaml:14-15` | 20 |
| PgBouncer server pool / app pod | `values.prod.yaml:32-33` | 50 |
| **App → Postgres** | 20 × 50 | **1,000** |
| Worker replicas (KEDA max) | `values.prod.yaml:18` | 50 |
| Worker per-process DB pool ceiling | `db.py:59-60` (5+10) | 15 |
| Worker pooler | none (`templates/worker/deployment.yaml`) | **direct** |
| **Worker → Postgres** | 50 × 15 | **750** |
| **Fleet peak** | | **≈ 1,750** |
| Cloud SQL `max_connections` (typical tier) | DB tier-dependent | **100-800** |

**Verdict:** fails by ~2-15×. The worker term is the first to break (unpooled, scales to 50). **Required:** PgBouncer in front of workers; re-pick `defaultPoolSize`, HPA/KEDA maxima, and DB tier so `app_pool_sum + worker_pool_sum ≤ max_connections − superuser_reserved (≈ 3-10)`. Record in `DECISIONS.md`.

### External-quota math — per 1,000 creators/day

| Service | Per-creator/day (est.) | Per 1k creators/day | At 10k | Constraint |
|---------|------------------------|---------------------|--------|-----------|
| YouTube **Data** API | 10-60 units | 10k-60k units | 100k-600k | **Hard wall** — default 10k/project; needs audit-gated extension + caching + fairness |
| YouTube **Analytics** API | a few queries | low | moderate | Per-user metered; not the wall |
| Anthropic (worker LLM) | a few clip-scoring calls | thousands of req | needs Tier 4 + Batch | Org RPM/ITPM — caching mandatory |
| Voyage embeddings | per-video embed | per-1k ingest rate | plan-sized | Per-key concurrency |
| Deepgram | per-minute transcribe | per-1k ingest rate | plan-sized | Per-minute cost + concurrency |

### Load-test scenarios + pass/fail (to close the gate)

1. **Read-path steady state** (existing Locust, fan out `CC_CREATOR_IDS` to ≥500 seeded creators, 300-1000 users): **PASS** = p99 < 500ms on `/videos`, `/creators/me`, `/billing/balance`; **0** pool-saturation timeouts; PgBouncer `cl_waiting` ≈ 0.
2. **Pipeline soak** (drive `POST /videos/upload` + clip-generate at the per-1k ingest rate against a worker fleet at KEDA max): **PASS** = no `QueuePool limit ... connection timed out`, no "Future attached to a different loop", server connections stay under the §4 budget (scrape `pg_stat_activity`).
3. **Refresh-storm** (force many near-expiry tokens to refresh concurrently): **PASS** = token-refresh path (C2) does not pin connections beyond budget; p99 acceptable.
4. **Redis-degradation** (kill Redis mid-run): **PASS** = graceful degradation, not opaque 500 cascade (regression check on `OFF_COURSE_BUGS.md:17`).

Thresholds → `DECISIONS.md`.

---

## 5. Proposed Issues (dependency-ordered, `docs/issues.md` house style)

> Severity tags: BLOCKER / SEV1 / SEV2 / cleanup. "DECISIONS" = needs a `docs/DECISIONS.md` entry before build.

### Issue A — [BLOCKER] Run worker tenant tasks under RLS (stop universal BYPASSRLS)  *(DECISIONS)*
**What:** Move per-creator worker tasks off `AdminSessionLocal`/`creatorclip_migrate` onto the RLS-gated app role with the `app.creator_id` GUC set per task; reserve BYPASSRLS for true cross-tenant sweeps; add child-table RLS policies for `video_metrics`, `retention_curves`, `transcripts`, `signals`, `clip_outcomes`.
**Acceptance:** every worker query that reads/writes tenant data runs with the GUC set; an integration test seeds two creators and asserts a deliberately-unfiltered worker query returns 0 cross-tenant rows under RLS; cross-tenant sweeps still function; `DECISIONS.md` records the role-strategy change.

### Issue B — [BLOCKER] Pool worker DB connections + re-derive the connection budget  *(DECISIONS)*
**What:** Add a PgBouncer (transaction mode) in front of the worker tier; re-derive the DEPLOYMENT.md inequality against the real Cloud SQL `max_connections`; pick `defaultPoolSize`, worker pool size, and HPA/KEDA maxima that satisfy it.
**Acceptance:** computed fleet-peak server connections ≤ `max_connections − reserved`; pipeline-soak load test (scenario 2) shows no pool-saturation; chosen numbers recorded in `DECISIONS.md` and `DEPLOYMENT.md`.

### Issue C — [BLOCKER] YouTube Data API quota at scale: extension + fairness + caching  *(DECISIONS)*
**What:** Submit the quota-extension audit; add per-creator fairness sub-budgets so Beat refresh can't starve interactive onboarding; add ETag/field-filter/batch caching to cut Data API usage.
**Acceptance:** projected units/day at target creator count documented and within the extended quota; per-creator budget enforced in `youtube/quota.py`; caching reduces measured units/creator; plan + numbers in `DECISIONS.md`.

### Issue D — [SEV1] Add HTTP security-headers middleware
**What:** Middleware emitting CSP (SPA-scoped), HSTS (prod only), `X-Frame-Options: DENY`/`frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`.
**Acceptance:** every HTML/app response carries the headers (prod); a structural test pins them; CSP does not break the SPA.

### Issue E — [SEV1] Define + run the deferred load test to close the gate  *(DECISIONS)*
**What:** Implement scenarios 1-4 (§4) against a working staging stack; record p99/pool/quota pass-fail.
**Acceptance:** all four scenarios run green on staging; thresholds + results in `DECISIONS.md`; pre-launch load-test gate checked off in `PROJECT_STATE.md`.

### Issue F — [SEV2] CSRF defense-in-depth on state-changing routes  *(DECISIONS)*
**What:** Add a Fetch-Metadata (`Sec-Fetch-Site`) or double-submit check on all cookie-authed mutating routes, on top of SameSite.
**Acceptance:** cross-site state-changing requests rejected; SPA flows unaffected; choice logged in `DECISIONS.md`.

### Issue G — [SEV2] Beat + Redis high-availability
**What:** Liveness probe + alert on beat; leader-elected/locked redundant scheduler (RedBeat or equiv.); managed HA Redis with replica.
**Acceptance:** beat outage alerts within minutes and the staleness-purge ToS task can't silently stop; Redis failover does not cause the opaque-500 cascade.

### Issue H — [SEV2] Verify token-refresh doesn't pin DB connections under load
**What:** Audit `get_valid_access_token` so the Google round-trip + retry polls don't hold a pooled DB connection; confirm via refresh-storm load test (scenario 3).
**Acceptance:** refresh path holds no pooled connection across the external call; scenario 3 passes within budget.

### Issue I — [SEV2] Structural test: every LLM/render entrypoint is rate-limited AND balance-gated
**What:** Enumerate LLM/render routes + tasks; assert each carries a `@limiter.limit` and a `check_balance*` pre-check.
**Acceptance:** test fails if a new LLM/render route ships without both gates.

### Issue J — [cleanup] Reconcile + pin the PgBouncer image; resolve token-rotation doc contradiction
**What:** Pin a PgBouncer image digest shared by staging + Helm; verify `RUNBOOKS.md` rotation procedure is complete and flip the open gate in `SOT.md:461`.
**Acceptance:** one pinned image; SOT no longer contradicts RUNBOOKS; pre-launch token-rotation gate accurate.

### Issue K — [cleanup] Early `Content-Length` rejection on upload + session-revocation note
**What:** Reject oversize uploads on the `Content-Length` header before streaming; document the stateless-session non-revocability tradeoff (or add a short deny-list if warranted).
**Acceptance:** oversize upload 413s before streaming; tradeoff documented.

---

## 6. Open Questions for the Human (one-line answers)

1. **DB tier:** what is the production Cloud SQL instance's `max_connections` (or chosen tier)? — needed to size Issue B exactly.
2. **YouTube quota:** has the quota-extension audit been started, and what's the target creator count for v1 launch? — sizes Issue C and gates 10k.
3. **Worker-RLS migration appetite:** OK to move workers onto the app role + GUC (Issue A), accepting that genuinely cross-tenant sweeps must be explicitly opted into BYPASSRLS?
4. **CSRF mechanism:** Fetch-Metadata (`Sec-Fetch-Site`) vs double-submit header for Issue F? (Fetch-Metadata is the lower-friction 2025 default given the SPA.)
5. **Beat HA:** acceptable to adopt RedBeat (Redis-backed, leader-safe) for the scheduler, or keep single-replica beat with alerting only?

---

### Doc-staleness flags raised
- `docs/SOT.md:461` ("rotation runbook not yet written") **contradicts** `docs/RUNBOOKS.md` + `scripts/rotate_token_key.py` existing. (Issue J)
- `docs/DEPLOYMENT.md:51-56` connection-budget inequality is correct in form but its inputs are stale and omit the unpooled worker term. (Issue B)
- `docs/SOT.md:444` ("isolation enforced at the query layer") undersells the RLS layer that now exists — and oversells it for the worker tier, which has none. (Issue A)
