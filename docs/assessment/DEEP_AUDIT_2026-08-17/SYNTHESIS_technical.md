# Technical verdict — architecture, stack, code

**Deep standards audit, 2026-08-17.** Synthesis of 9 technical domain reports (d01–d09) and the
6-modality vacuous-green sweep, against the locked v1 scope: **≤100-user private beta, one
DigitalOcean VM, K8s/10k track descoped.**

**How to read the findings.** Every claim below traces to a domain report or a sweep candidate.
Where a finding was adversarially verified, the **verifier's corrected statement is what I use** —
not the original claim. Findings that were **REFUTED are excluded entirely** and are named at the
end of §3 so nobody re-files them. Findings that were never adversarially checked are labelled
**[unverified]** every time they appear.

**The one-sentence answer to "why one baby snag after another":** the snags are not distributed
randomly — they cluster in one shape. Fourteen of this audit's verified defects are the same
mechanism: **a gate, probe, counter or status field whose scope is a hand-written literal or an
assumed success**, so it reports green over a thing it never touched. That is not an architecture
problem. The architecture is largely right. It is a *signal-integrity* problem, and it is fixable
with roughly two days of work concentrated in about eight files.

---

## 1. Is the architecture right?

Verdicts on each load-bearing structural bet. **Bold = do not change this.**

### The bets that are right — leave them alone

**1. No service layer. RIGHT — and do not add one.**
The officially maintained `fastapi/full-stack-fastapi-template` (verified active, last commit
2026-08-17) does exactly what this repo does: route handlers running `select()` against an injected
session, ownership predicate inline. This repo is *better* than the reference, because
`routers/_owned.py` (47 lines) collapses fetch+ownership into one query returning 404 for both
missing and foreign, and `routers/_enqueue.py` (75 lines) is a single enqueue+SSE-ownership seam
behind 19 endpoints. Adding `services/` would move ~60 `_async` bodies one directory sideways and
change none of the five defect classes those bodies actually produce. The only thing missing is the
*written* position, so the next session argues against a decision rather than a vacuum.

**2. Postgres RLS as the tenancy backstop, with `tenant_session(creator_id)` taking the tenant id as
a required argument. RIGHT — this is the best piece of architecture in the repo.**
A call site structurally *cannot* forget the GUC; cross-tenant sweeps must use `AdminSessionLocal`,
and that allowlist is pinned bidirectionally by `tests/test_worker_invariants.py`. 27
`tenant_isolation` policies under `FORCE ROW LEVEL SECURITY`, a non-BYPASSRLS app role, and
migration 0045's `NULLIF` hardening that converts a crash into a deny. This is above what most
Series-A products ship. The *only* change it needs: derive the covered-table list from `pg_policies`
instead of from three separate hand-written tuples (§3 #14).

**3. Celery sync shells → one per-worker asyncio loop → post-fork `recreate_engine()`. RIGHT.**
Celery 5.6 still has no native async worker; the third-party pools remain thin. This is the
mainstream, lowest-risk choice and Issue 39's alternatives-ruled-out section is still accurate in
2026. One real bug inside it (§3 #7), not an architecture change: `run_async` must cancel-and-drain
on exception the way `asyncio.run` does.

**4. Workload-class queue split (`celery` vs `render`, Issue 432). RIGHT** — it is exactly what
Celery's own optimizing guide prescribes for mixed long/short workloads, and it came from a real
incident. Incomplete in two places (ingest's CPU passes still on the default queue; staging has no
`render` consumer at all), not wrong.

**5. Single DigitalOcean VM + docker-compose + Cloudflare Tunnel. RIGHT, and I would not revisit
it.** ≈$48/mo flat vs ≈$110–130/mo on Render with worse CPU for ffmpeg/MediaPipe and an ephemeral
filesystem hostile to the render pipeline. A PaaS costs ~2× and fixes nothing that is broken. What
the topology does *not* excuse is three things that cost under $10/month and half a day combined:
armed backups, an off-host uptime probe, and an auth-gated staging URL.

**6. `clips` stays one table. RIGHT — do not normalise it.**
25 columns is not wide by Postgres standards; what costs is *fetching* TOASTed columns, and that is
a `SELECT`-list problem (§3 #26), not a schema problem. The `clip_edit_documents` split was correct
because that document is rewritten on an autosave cadence; `reframe_track_jsonb`, `signals_jsonb`
and the geometry columns are written once each. Splitting them buys nothing at 100 users and costs
four more RLS policies, four more cascade paths, and four more chances to get an Issue-468-class CAS
transaction wrong — which is where this repo's SEV1s actually live.

**7. SSE over Redis Streams for progress; feature flags as DB rows with a 30 s TTL and hard
fail-open; prepaid minute packs instead of subscriptions; `yt-dlp` off by default with creators
uploading files. ALL RIGHT**, all properly reasoned. The flags choice deserves special mention: it
is DB-backed, so the kill switch is the one cost control that *survives* a Redis outage.

**8. React Router v7 Data Mode as router-only + TanStack Query v5 as the single server-state cache.
RIGHT** — precisely the 2026-recommended arrangement, and the app avoids the failure mode being
warned against (duplicated caching between loaders and Query) by using zero loaders. Currently
undocumented and therefore re-litigable; write it down.

**9. Structural tests as the architectural mechanism instead of layer boundaries. RIGHT in
principle** — `tests/test_usage_coverage.py`, `test_worker_invariants.py`, `test_model_config.py`,
`frontend/src/test/sourceScan.ts`. This fails loudly, which a layer boundary does not. But see the
failure mode below; it is the single highest-leverage correction in this audit.

### The bets that need work

**10. `worker/tasks.py` (7,179 lines) as the de-facto service layer.**
**The line count is not the problem.** Every defect logged against this file — format drift,
mislabeled log fields, unbilled LLM calls, redelivery double-spend, advisory-lock leak — is a
*missing cross-cutting concern in a single task body*. Splitting into four files leaves all five
classes exactly as likely.

The measurable structural fact is the **envelope duplication**: across ~60 `_async` bodies,
`tenant_session` is hand-rolled **55×**, `AdminSessionLocal` **47×**, `progress.aemit` **116×**,
`log_event` **38×**, `_try_advisory_lock` **18×**, `_spend_guard_blocked` **10×**. Each new task
re-derives which session factory to use, whether to take a lock, whether to check the spend guard,
and which progress events to emit, with nothing enforcing the answer.
`_generate_improvement_brief_async` is ~80 lines of envelope before the first line of business
logic. **Extract one `@task_body` async context manager and pin it with a test that every `_*_async`
goes through it** — the same shape as `record_llm_usage`, which is the one choke point that actually
killed a defect class here. The three-seam split (`worker/sweeps/` ~1,480 lines, `worker/llm_features/`
~1,150, `worker/render/` ~990) then becomes cosmetic, cheap (39 of 40 tasks pin `name=` explicitly,
so task identity survives a module move) and optional. If you split, fix `distill_style_prefs`'s
missing `name=` (`worker/tasks.py:1574`) in a *separate, earlier* deploy or in-flight messages
orphan.

**11. `config.py` as one 1,208-line / 214-field `Settings` god-object. Survivable; narrow it.**
67 of those fields are clip-engine algorithm constants between `:311` and `:660`, and **zero of them
are set by any of the five committed deployment configs**. Roughly 55 are genuine internal constants
that 12-Factor III places in code; about 8 are legitimately deploy-varying (feature toggles,
`MEDIAPIPE_FACE_MODEL_PATH`) and several are documented deliberate pre-calibration tunables
[verifier correction — the original "delete all 67" recommendation is too blunt]. **Do NOT migrate to
nested pydantic models with `env_nested_delimiter`** — that renames all 214 env vars on a
hand-edited VM `.env`, which is this project's worst failure class, at scale.

**12. 17 independent module-level `AsyncAnthropic` singletons, no `clients.py`.**
The decision *does* exist (`DECISIONS.md:3940`, Issue 242 item 4) — but it is descriptive, not
normative, and it is filed inside a *transactional-email provider* entry, which is why nobody can
find it. Drift is real: 60 s, 120 s, 180 s, and one client at the SDK's 600 s default. A ~20-line
`llm/_client.py::anthropic_client(timeout_s)` collapses it with zero behavioural change. **But the
factory is the optional half** — no factory can force a future module to call it. The load-bearing
half is replacing the hand-listed registries with the AST-discovery sweep the repo already proved in
`tests/test_usage_coverage.py`.

**13. Hand-written `frontend/src/types.ts` (814 lines), no OpenAPI codegen, `api<T>()` casts without
validation.** This is the one frontend bet I would change. 98 of 119 endpoints already declare a
`response_model`, so the generation input is finished — this is turning on a tap, not a migration.
**`openapi-typescript` + `openapi-fetch`** (6 kB, middleware supports the two behaviours `lib/api.ts`
actually has), Zod at only the ~5 schemaless endpoints (where `isCropTrack`/`isWaveformPeaks`
already are), and one CI step that regenerates and `git diff --exit-code`s. Do **not** adopt hey-api
or orval — both generate an SDK layer that duplicates the 89 existing call sites. Measured drift
today is 25 backend fields no UI reads and **zero** TS fields the API does not send, so the risk is
latent, not live [verifier: medium, not high].

**14. Build-not-buy active-speaker reframe (~3,200 lines, 38% of the clip engine). The BUILD
decision HOLDS.** The cost and latency arguments have weakened — hosted reframe APIs are commodity
now — but the argument that does not expire is that a hosted reframe API is a **new video-data
sub-processor** under GDPR Art. 28 and YouTube ToS §VII, receiving source video with creator PII and
third-party faces. For a solo maintainer in a private beta that is the wrong trade.
**What is genuinely open is the sub-decision inside it:** the 2026-08-04 rejection of a TalkNet-class
audio-visual ASD model in favour of co-occurrence + largest-face + mouth-motion voting. 10 of the
last 15 clip-engine decisions are reframe-geometry fixes (433/439/440/441/443/448/450), each adding
hand-tuned constants with no held-out set behind any of them. That is the full maintenance cost of
BUILD while declining the component that would make the build good. [verifier: reclassify to a LOW
design-direction item — "revisit at the next scale step", and it cannot be promoted until someone
measures CPU-only LR-ASD latency per clip on this VM.]

### The one architectural failure mode worth naming

**Structural tests are the right mechanism, and the implementation has one systematic flaw: their
scope is a hand-written Python/TS literal.** The sweep censused 101 module-level literals and
diffed the ~20 that define a gate's scope against ground truth. **Eleven had drifted.** Verified
examples: the per-creator quota gate covers 9 of 17 LLM routes; the kill-switch/spend-guard gate 10
of 17; the RLS regression sweep 17 of 31 policied tables; the LLM conformance registry 13 of 17
clients; the design-token gate a 16-name denylist; the BYPASSRLS gate exactly one file. Each of
these was written *as the fix* for a previous instance of the same bug, and each replaced a small
literal with a bigger literal.

**House rule the architecture needs: a structural gate discovers its own scope, or it is not a
gate.** The repo already owns two proofs that this works — `tests/test_usage_coverage.py` (a
bidirectional AST sweep that fails on both unmapped *and* stale entries) and
`frontend/src/test/sourceScan.ts` (walks the TS AST and exposes `sourcePaths()` so a caller can
assert the glob matched). Copy those two, delete the literals.

---

## 2. Is the stack right?

| Component | Verdict | Evidence |
|---|---|---|
| **FastAPI + Python 3.12** | **Right call.** | 24 routers / 98 endpoints is below the scale at which any 2026 reference claims a service layer pays for itself (d01). |
| **Celery + Redis** | **Right call, under-configured.** | Pinned `celery[redis]==5.4.0` (Apr 2024); 5.5 shipped soft shutdown + `REMAP_SIGTERM` + `worker_disable_prefetch`, 5.6 is out. No `stop_grace_period` anywhere, so every deploy SIGKILLs in-flight encodes after 10 s — and a *recovery sweep* (`sweep_stale_renders`) was built instead of the one compose line. One global `CELERY_SOFT_TIME_LIMIT_S=3000` covers a 5-second email and an 8-encode batch. No backpressure position at all: zero hits for backpressure/prefetch/priority across 13,018 lines of DECISIONS (d03). |
| **Postgres 16 + Alembic** | **Right call, and the migration discipline is top-decile.** | 0059/0062 are self-documenting, offline-mode-aware, idempotent, expand-only, with termination proofs in comments. `alembic/env.py:47-58` carries the full incident archaeology for the libpq-`options` fix. Correct enum handling across 22 `sa.Enum` columns (d02). |
| **pgvector** | **Right call for PG16; the ANN index is over-provisioning.** | `ix_dna_embeddings_hnsw` serves a `<=>` query that **has never existed** in this repo — the only production reader pulls whole 1024-dim vectors into Python and computes cosine there. Below ~10⁴–10⁵ vectors exact scan is the 2026 default advice anyway. [verifier: LOW, and the value is honesty not performance — a green integration test attests to infrastructure with no consumer.] |
| **Anthropic SDK** | **Right call. Strongest domain in the repo.** | One billing choke point enforced by a repo-wide AST sweep; floor-gated caching that *measures* rather than assumes and then reads `cache_creation.ephemeral_1h_input_tokens` back to confirm the write landed; refusal handling (`stop_reason == "refusal"`) at all three Opus 5 sites; `max_tokens` raised for Opus 5's default-on thinking with the reason recorded at each site. Wrongly configured in exactly two places: `preference/style_distill.py:31` has no explicit timeout (inherits 600 s), and two HTTP handlers hold a pooled DB session across the call. |
| **Opus 5 on the clip chain** (scoring / video-context / clip-metadata, ~2× cost) | **Defensible, unvalidated.** | The decision is recorded with reasoning. But there is no human-labelled clip corpus, so "did the Opus 5 upgrade make clips better?" is **unanswerable by construction** — and the triage stream that could answer it is being written and never read. Also: `docs/SOT.md` still says "no Opus", which makes the SoT wrong on the three most expensive calls. |
| **Voyage AI embeddings** | **Right call, wrongly instrumented.** | Every insert pays an HNSW graph insertion at `ef_construction=200` for zero read benefit (above), and `COST_PER_MTOK_VOYAGE` has **no production reader**, so embedding spend is invisible to the ledger and the spend guard. |
| **Deepgram nova-3 as default; WhisperX/AssemblyAI config-selectable** | **Right buy-side call — do not revisit at beta.** | No GPU at launch, hosted, cheapest path. The watch item is margin, not architecture: at $0.0097/min against the Stream pack's 4.0 ¢/min, Deepgram is ~24% of revenue per minute, and it is recorded in no ledger and invisible to the spend guard (`billing/spend_guard.py`'s own docstring self-scopes to "LLM spend caps"). That scope boundary should be *written down*, not instrumented [verified: E7, LOW]. |
| **ffmpeg** | **Right call, wrongly trusted.** | `clip_engine/render.py:94-127` `_run` treats `returncode == 0` as proof of work and discards stderr on the success path; none of the three render entry points inspects `out_path`. Verified repro: a real `render_clip_file(0.0, 0.6)` on a source whose container duration overstates its decodable data returned a **0.400 s / 28,851-byte** clip with ffmpeg's "Decoding error: Invalid data found" thrown away — then `render_status=done` and "Clip ready." The project already knows this hazard and fixed it in exactly one place (the poster helper, `:314-329`). |
| **Cloudflare R2** | **Right call, wrongly verified.** | Three independent probes attest "storage healthy" from **read-only** operations (`head_bucket`, `list_objects_v2`). An R2 token re-issued at Object-Read-only scope leaves `/health`, `doctor --full` and `live_smoke` all green while every `put_object` 403s [unverified: mC-6]. Bucket CORS is set from `sys.argv` by a human and reconciled against `ALLOWED_ORIGINS` by nothing [unverified: E4]. |
| **React 19 / Vite 8 / TS 6 / Tailwind v4 / Radix / TanStack Query v5 / RR7 / Uppy** | **Right call.** | `lib/` is genuinely pure-function-first with colocated tests on every piece of hard math; the timeline ARIA is real and sourced; 261 KB gz single chunk is above the 2026 budget but irrelevant here because `/` is a server-rendered landing page and the SPA is an authenticated repeat-visit tool [CONFIRMED — no action, write the budget down]. The weak seams are the type contract (§1.13) and that the whole frontend job is advisory. |
| **Stripe** | **Right call; the SDK trap is settled — do not revert.** | `RequestsClient` over `HTTPXClient` is deliberate and documented in place. The thin part is the *boundary*: the offered payment-method set is a Dashboard toggle (`payment_method_types` never set), `async_payment_succeeded` is named in a comment and is not a branch, and refunds/disputes are unhandled in both directions. |
| **Prometheus + OTel + Sentry** | **Wrong shape for a solo operator — this is the one stack choice I would change.** | Twelve metrics, **zero alert rules anywhere in the repo**. `/metrics` auto-disables in prod without `METRICS_TOKEN` (unset); nothing scrapes the VM; OTel deliberately refuses to bridge the prometheus-client registry. And underneath all that: `prometheus_client` is process-local with no `PROMETHEUS_MULTIPROC_DIR`, so 6 of 12 metrics are incremented in `worker`/`render-worker`/`beat` and **cannot be scraped even after the token is set** — and `llm_cost_usd_total` is ~61% worker-side, so the fix would produce a plausible-looking cost graph that under-reports by the majority [verified F3: LOW as a rider on the dead rail]. **The right answer at this scale is not to stand up Prometheus.** It is: emit the five or six numbers that matter from the code that already computes them, into the `notify/` + Resend rail that already exists. |

---

## 3. The ranked technical defect list

Ranked by (cost of being wrong) × (likelihood) **at ≤100 users on one VM**. Statements are the
verifier's where one exists. Hours are maintainer-hours including a test.

### Tier 1 — fix first

| # | What breaks | Where | Verdict | Fix |
|---|---|---|---|---|
| 1 | **The rate limiter fails CLOSED with an unhandled 500, not open.** `slowapi 0.1.9` is constructed with neither `swallow_errors` nor `in_memory_fallback`, so any redis error re-raises and every rate-limited route — including `GET /auth/me` (120/min, called by `AuthGate` on every load) and the OAuth callback — returns 500 while the body never runs. Reproduced against a dead Redis with the module's exact args. A Redis *stall* (>100 ms `socket_timeout`, e.g. BGSAVE fork or CPU starvation from a concurrent encode) is sufficient. Three documents state the opposite posture (`DECISIONS.md:2633-2634`, `AUDIT_BRIEF §5`, the module's own docstring), and the module carries a **99% coverage floor** with **zero** tests for Redis-unavailable behaviour. | `limiter.py:129-133`, `main.py:145-147` | CORRECTED (severity kept high) | **2 h** — set `in_memory_fallback_enabled=True` *or* amend all three docs; either way add the Redis-down test. |
| 2 | **Personalization is a measured no-op across its entire ramp while the API tells the creator it is active.** `LGBMClassifier` is fitted with `min_child_samples=20` untouched, so it is a **constant predictor for label counts 21–40** (measured: 200/200 degenerate at n=38–39, 198/200 at n=40, 0/200 at n≥42). The blend weight ramps 0→cap across exactly n=20→40, so the whole ramp lies inside the dead zone and the model only becomes non-constant at n=41 — by which point the weight is already saturated. `blended_score` is then a strictly monotone transform of `score`, i.e. the persisted order is byte-identical to DNA-only, while `GET /videos/{id}/clips` returns `personalization={active:true,…}`. Corollary: the LogisticRegression branch (n<20) is unreachable at serve time by construction. | `preference/model.py:196-208`, `:162-167`; `routers/clips.py:786-792` | CORRECTED (high; the correction *strengthens* it) | **4–6 h** — set `min_child_samples`/`num_leaves` for the small-n regime *or* raise the LightGBM switchover to ~60 and give LR a non-zero weight; then re-derive `PERSONALIZATION_THRESHOLD_LABELS` from the measured number instead of the other way round. |
| 3 | **The gate written four days ago to close this exact class certifies the false property.** The Issue-480 rerank eval's fixture is `rows_per_class: 20` — the single 40-row shape where the unique best split is exactly 20/20 and satisfies `min_child_samples` on both children. Verified by running the repo's own `_train_scorer`: **92 trees, spread 0.9999** on the fixture; move one label (24/16, 27/13, 30/10) and it collapses to **1 tree, spread 0.000000**. Its control test proves only that the harness *can* go red, by patching the weight to 0. | `tests/preference/test_rerank_eval.py:105-131`; `tests/eval/scenarios/ranking/rerank_preference_flips_order.yaml` | **CONFIRMED** | **2 h** — parametrise over an unbalanced split and assert `booster_.num_trees() > 1`. Verified to go red today. |
| 4 | **There are no backups of any kind.** `BACKUP_R2_BUCKET` has never been set; it gates *both* the pre-migration dump and the nightly cron (`backup_pg.sh` hard-dies on it). RPO is total loss of the billing ledgers, `preference_models` (the trained taste — irreplaceable), `creator_dna`, `clip_outcomes` and the consent records. `docs/RUNBOOKS.md:648` still reads `measured RTO recorded here: ________`. The *design* is textbook (Object-Lock Compliance not Governance, argv-safe encryption, mandatory `reapply_erasures.py` on restore). | `.env.example:116`, `scripts/backup_pg.sh:66-69`, `deploy.yml:293-300` | CORRECTED — HIGH consequence, but a **known owner-blocked gate** (GO_LIVE.md:59-61, LEFT_OFF.md:115), not new signal. The one genuinely new code-side item: `BACKUP_HEALTHCHECK_URL` (the dead-man's switch) is absent from `.env.example`. | **2 h** code + operator steps |
| 5 | **A Stripe Dashboard toggle creates a take-money-grant-nothing path the reconcile sweep cannot catch.** `payment_method_types` is never set, so the offered set is a UI toggle. `checkout.session.completed` fires with `payment_status:"unpaid"` → correctly ignored; `async_payment_succeeded` fires days later → **no branch handles it**; and the 48 h reconcile sweep filters on session **creation** time, so the settled session is already outside the window and never returns. Net: money collected, zero minutes granted, permanently, with no error. | `billing/stripe_client.py:93-116`, `routers/billing.py:245-260`, `billing/stripe_client.py:173-180` | **CONFIRMED** (latent — ACH/BNPL not currently enabled; becomes live on one Dashboard click) | **2 h** — pin `payment_method_types=["card"]` *and* branch on the async pair. |

### Tier 2 — fix this month

| # | What breaks | Where | Verdict | Fix |
|---|---|---|---|---|
| 6 | **The render never verifies its own output.** Reproduced on the real production function: a silently *short* clip (0.400 s against a 0.6 s request, 28 KB) is uploaded, marked `render_status=done`, and announced "Clip ready." ffmpeg's "partial file / Decoding error" goes to the stderr `_run` discards. The wholly-empty exit-0 case remains unguarded on `render_cleaned_clip_file` ("Clean ready.") and `render_summary_file`. A `st_size > 0` check is **not** the fix — a 28 KB truncated clip passes it. | `clip_engine/render.py:94-127`, `:941`, `:1105`, `:1288`; `worker/tasks.py:2666-2699` | CORRECTED (medium; mechanism restated) | **3 h** — ffprobe the output duration against the requested window on all three entry points; surface stderr on the success path. |
| 7 | **`run_async` abandons the coroutine on soft timeout, and it later resumes inside an unrelated task.** `loop.run_until_complete` (unlike `asyncio.run`) does not cancel the pending task when `SoftTimeLimitExceeded` escapes while suspended at an `await`. Verified: nothing leaks permanently — the coroutine resumes on the next `run_async` and its `finally` blocks do run. The real defect is **zombie-resume state corruption**: `render_clip`'s handler writes `render_status=failed`, then the resumed coroutine writes `render_uri` + `done` for the same clip; and `render_video_clips` has no soft-timeout branch, so it retries the batch while the zombie is still encoding → duplicate ffmpeg encode and duplicate R2 upload. | `worker/celery_app.py:129-139`; `worker/tasks.py:963`, `:988` | CORRECTED (high → **medium**) | **2 h** — wrap in `try/except BaseException` → `task.cancel()` → drain via `gather(..., return_exceptions=True)` → re-raise; add the regression test `tests/test_celery_event_loop.py` lacks. |
| 8 | **The deploy preflight never contacts a third-party provider.** Both automated paths run `scripts/doctor.py` **without `--full`**, and `--full` appears in zero workflows/scripts — only in eight prose docs. So the 2026-08-13 hardening that made `_live_stripe`/`_live_r2` drive the app's *own* clients (the fix for the 10-week Stripe outage's probe defect) **has no automated caller.** Scope corrections: bare mode still runs 8 presence/format sections plus live Postgres/Redis; **R2 *is* live-probed every deploy** via `/health` → `_check_storage()` → `head_bucket` with auto-rollback; Anthropic + Deepgram have partial nightly coverage via GitHub secrets (proving the code path, not the VM credential). **The genuine gap is 4 providers, not 5, and narrows further to Voyage + Stripe having no automated live probe anywhere.** `docs/SOT.md:298` and `DECISIONS.md:10471` both describe the bare invocation as validating "live reachability of every secret". | `.github/workflows/deploy.yml:279`, `scripts/deploy.sh:97`, `scripts/doctor.py:461` | CORRECTED (high → **medium**); A4 / mC-1 / E1 are **one** issue | **1 h** — pass `--full`, decide whether provider flakiness may block a deploy (if not, run it post-rollout non-blocking), correct the two docs. |
| 9 | **Nothing in the deploy pipeline or in continuous monitoring ever observes a Celery worker.** `/health` probes exactly Postgres, Redis, R2 — no worker heartbeat, no queue depth, no Alembic revision. Post-deploy Phase 1 reads only `status`; Phase 2 hard-asserts four **read** endpoints and marks the one write probe WARN-only. Rollout is `up -d` with **no `--wait`**. The correct `celery inspect ping` healthchecks in compose are read by `autoheal` (which does restart unhealthy workers — a real remediation loop the original finding denied) but by nothing in CI. **The sharp, genuinely unguarded case is `beat`: no healthcheck and no `autoheal` label at all**, and it drives `purge_stale_youtube_analytics` (YouTube ToS §III.E.4.b) and `purge_stale_event_logs` (GDPR Art. 5(1)(e)) — silent beat death is a compliance-drift path with zero signal. Note also that #282's own scope presumes a "worker heartbeat" signal that does not exist in the codebase. | `main.py:553-567`; `deploy.yml:302`, `:341-358`; `docker-compose.prod.yml:82-91` | CORRECTED (high → **medium**); D-4 duplicates d03's beat finding and mC-9 | **3 h** — worker/queue dimension on `/health`, one required `inspect ping` smoke step, healthcheck + `autoheal` label on `beat`. |
| 10 | **The spend guard's Redis client has no socket timeout** — the only one of the app's four Redis clients without bounded timeouts. Against a *wedged-but-connected* Redis, `await r.ttl(...)` in `creator_block_status` (reached from `require_budget` on ~11 LLM/render routes) has no deadline, so the documented fail-open arm is never reached: a hang is not an exception. Because it is asyncio it does **not** block the event loop; the concrete cost is unbounded request lifetime holding a checked-out `AsyncSession` against a 20-connection pool. Issue 312 diagnosed this exact class and fixed one of the two clients. | `youtube/_redis.py:32`; `billing/spend_guard.py:365-375` | CORRECTED (high → **medium**) | **1 h** — pass the same timeouts the other three clients use; pin with a kwargs test. |
| 11 | **`NOTIFY_BACKEND` defaults to `console` with no production guard, and the delivery row is committed `status=sent` *before* the send.** `_send_console` logs and returns `None`, so a no-op is indistinguishable from delivery: `notification_deliveries` says `sent`, the log says "email sent". Worse, the dedupe short-circuit only retries rows whose status is `failed`, so **console-era rows can never be re-sent after a flip to `resend` — the dead path is latched by its own success record.** The live prod value is *unknowable from this repo* (VM `.env`, never synced by `deploy.yml`), but `render.yaml:21-27` — the committed beta blueprint — pairs `ENV=production` with `NOTIFY_BACKEND=console`. The `MAILING_ADDRESS` lifecycle short-circuit is **not** part of this: it is an explicitly recorded CAN-SPAM fail-safe (`DECISIONS.md:12800`). In-app notifications are unaffected. | `config.py:993`; `notify/mailer.py:183-222`; `worker/tasks.py:6730-6738`, `:6741-6759` | CORRECTED (high → **medium**); D-1 and E3 are **one** issue | **2 h** — reject/warn on `NOTIFY_BACKEND != resend` under `ENV=production` (the exact treatment `STORAGE_BACKEND=local` already gets), and stamp the handling backend on the delivery row. |
| 12 | **The per-creator daily-quota gate iterates a 9-name literal while 17 routes carry the LLM flag; four violate the stated invariant.** `chat.post_message` and `chat.regenerate` have 25/day and **no burst cap**; `creators.identity_chat` has 40/hour and **no daily cap**; `creators.build_dna` has **120/minute** and no daily cap on a route that enqueues a Sonnet DNA build. Not an open spend hole — all four carry `require_budget` and are re-checked task-side against the $5/day per-creator cap — but `CLAUDE.md`'s "✅ per-creator quotas shipped via Issue 228" rests on a test that can no longer detect a new uncapped LLM route. | `tests/test_creator_quota.py:30,41`; `routers/creators.py:541-547`; `routers/chat.py:117,161` | CORRECTED (high → **medium**) | **2 h** — derive the route set from the resolver; fix the four decorators. |
| 13 | **The kill-switch/spend-guard gate verifies 10 of 17 LLM routes**, missing both chat routes, identity chat, improvement-brief, video-analysis and **both clips/generate routes** — the core product action. All 7 are correctly gated *today*, so this is latent, but `AUDIT_KNOWN_ISSUES.md:230`'s "gating is complete and CI-enforced" is false for 41% of the surface, and the sibling AST sweep excludes `routers/chat.py` and `routers/creators.py` entirely, so those three have **zero** structural coverage from either mechanism. | `tests/test_flags.py:411`; `tests/test_security_baselines.py:303-404` | CORRECTED (high → **medium**) | **2 h** |
| 14 | **The RLS regression sweep drives all three loops from two hand-written tuples; 8 of 31 policied tables never enter any loop** (`chat_conversations`, `clip_publications`, `data_exports` have *no* guard anywhere; five others are covered by sibling tests). No live defect — all eight carry the post-0045 hardened predicate — but nothing in the repo reconciles any literal against `pg_policies` (zero references), and migration 0045 is itself a second 27-name list that four later migrations outgrew. **The construction cannot detect a tenant table shipped with no policy at all — the exact 0038 leak.** Separately, `creator_api_keys` and `creator_identity` carry `creator_id` with no policy and no recorded exemption. | `tests/test_rls_isolation_integration.py:265`, `:579`; `models.py:279-318`, `:590-620` | CORRECTED (high → **medium**); merges d07 F4 | **3 h** — one catalog-driven test; add the two policies or record the exemptions. |
| 15 | **The prod deploy never asserts `alembic current == head`** — the staging job (same file) and `scripts/deploy.sh` both do, and the meta-test's whole-file substring check is satisfied by the staging job alone. Reproduced: mutating prod's step to `alembic upgrade head \|\| true` leaves all 22 `test_ci_config.py` tests green. Correction: lock timeouts, role differences and partial revisions all exit **non-zero** and fail loudly; the genuinely silent modes are a VM `.env` DSN resolving to a wrong-but-valid database (nothing validates it, and neither DSN is pinned in `docker-compose.prod.yml` the way staging pins them), the `skip_staging` break-glass path, and drift left by a prior break-glass deploy. | `deploy.yml:299-300` vs `:88-101`; `tests/test_ci_config.py:174-177` | CORRECTED (high → **medium**) | **1 h** |
| 16 | **The prod critical-journey smoke silently downgrades to a bare `echo` warning when `CC_JWT_SECRET` is unset** — and `gh secret list` shows it *is* unset, so this branch has been taken on every prod deploy. The staging gate 230 lines above needs no such secret: it reads it from inside the container. Scope: staging is a hard `needs:`, so code regressions in those endpoints do not ship unverified; the unguarded set is **prod-only** failure modes — `/opt/autoclip/.env` drift, prod DB state, live Stripe/R2/CORS config. Exactly the class that produced the 10-week Stripe outage. | `deploy.yml:365-370` vs `:135-137` | CORRECTED (high → **medium**) | **1 h** — mirror the staging invocation. |
| 17 | **Auto-rollback runs an unscoped `docker compose down`**, destroying `cloudflared`, `postgres`, `redis` and all three workers to swap an app image — while the *normal* path one step earlier uses the correctly scoped `up -d --remove-orphans`. Both `|| true`s swallow a failed recreate, so a failed rollback leaves the whole stack down behind a single `exit 1` that reads as "deploy failed", not "site is dark". Forward-roll trap: after a rollback, a bare `docker compose up -d` on the VM relaunches `:latest` because of `${IMAGE_TAG:-latest}`. No data loss (`down` without `-v`). | `deploy.yml:332-333`; `scripts/deploy.sh:84-85` | **CONFIRMED** | **0.5 h** — `up -d --force-recreate --no-deps app worker render-worker beat`. |
| 18 | **The staging stack has no consumer for the `render` queue and no `beat`**, and the test named `test_staging_prod_compose_parity` compares no service sets — it asserts two image strings, the staging image name, and pgbouncer's absence. Staging's worker runs with no `-Q`, so it consumes only `celery`; every render enqueued there sits in Redis forever. `render-worker` post-dates the parity matrix in `STAGING_ACCESS.md` by a month; `beat` is unmentioned. `STORAGE_BACKEND: local` is **not** part of this finding — it is a deliberate documented prod-bleed guard. The gate's smoke never enqueues a render, so nothing is *currently* false-greening a render; the vacuity is in the label. | `docker-compose.staging.yml:131`; `tests/test_ci_config.py:298` | CORRECTED (high → **medium**) | **2 h** — assert `set(prod) − allowlist ⊆ set(staging)` and that every `-Q <queue>` in prod has a consumer in both files. |
| 19 | **`run_layer0.py`'s static gates infer their result from stdout and never check `proc.returncode`, so a tool that runs and *fails* scores a perfect 0.** Reproduced against the real `.venv` binaries: mypy with zero targets or a bad config exits 2 with empty stdout → `{"status":"ok","value":0}` → passes the strict baseline of 0 → "All runnable gates passed", exit 0. `--require` cannot see it because it only escalates a `skipped` status. Scope: **ruff is independently guarded** by the separate required `lint` job, so the exposed set is mypy, bandit, pip-audit — most plausibly **pip-audit reporting "0 vulnerabilities" during a PyPI/OSV outage** on a required check. bandit is hollow via an ignored `errors[]` array rather than the returncode. | `.claude/skills/production-assessment/scripts/run_layer0.py:159-164,170-172,227-231,274-281` | CORRECTED (high → **medium**) | **2 h** |
| 20 | **`gate_module_coverage` fails OPEN on measurement drift**: when `_module_line_rate` returns `None` the loop `continue`s, `_failures` stays empty, and the gate evaluates `ok` — which `--require` accepts. Its anti-hollowing test never parses a coverage report at all, despite its name. **Correction: the gate is not vacuous today** — under the current single-root `--cov .`, coverage.py is source-based, so all five floored modules always resolve (verified: a one-test run reds correctly with all five violations). The reachable failure is *drift* — reverting to multi-root `--cov` (the exact Issue-368 cause, pinned by nothing), a package rename, an `omit` addition, or deleting root `auth.py`. These floors have already been silently lost once for a year and once for 7 weeks. | `run_layer0.py:396-413`, `:479-481`; `tests/test_layer0_module_coverage.py:86-95` | CORRECTED (high → **medium**) | **1 h** — treat `None` as failure; assert resolution against a real report. |
| 21 | **The nightly live-LLM/ASR lanes have no mechanism that distinguishes a real run from a fully-skipped one.** Both gate on `bool(ANTHROPIC_API_KEY)`; `${{ secrets.X }}` expands to `""` if the secret is unset/renamed/blanked, every test `skipif`-skips, pytest exits 0, both steps go green — and `GO_LIVE.md:71` keeps citing "LLM E2E nightly green daily" as proof the external APIs are live. The two meta-tests written to prevent exactly this assert **YAML string literals**, all of which a secret-less workflow satisfies. The workflow's one self-report writes *"scoring lane did not run"* into a step summary and exits 0. **Latent, not firing** — the 2026-08-17 run genuinely made 9 live calls. | `tests/test_llm_live.py:18`, `test_llm_live_scoring.py:42`, `ingestion/test_transcription_live.py:28`; `llm-e2e-nightly.yml:64,103`; `tests/test_ci_config.py:491,513` | CORRECTED (high → **medium**) | **1 h** — `test -n "$KEY"` + a minimum-passed-count assertion, pinned instead of the strings (`ci.yml:130-141`'s `render_env` collect-count is the in-repo pattern). |
| 22 | **The frontend↔backend contract is a closed loop.** `types.ts` is hand-written; `api.ts` casts without validation; `e2e/fixtures/mock-api.ts` imports its shapes **from `types.ts`**, so the *required* Playwright check re-asserts the frontend's own assumption at the network boundary. A backend field rename passes mypy, `tsc`, vitest, Playwright and the post-deploy smoke (which drives no browser). Measured drift is one-directional today (25 API fields the UI ignores, **0** TS fields the API does not send), and the repo has an additive-only API convention — so this is an unguarded gap with no demonstrated instance. | `frontend/src/types.ts`, `lib/api.ts:82`, `e2e/fixtures/mock-api.ts:11-29` | CORRECTED (high → **medium**) | **4–6 h** — `openapi-typescript` + a `git diff --exit-code` CI step. |
| 23 | **The 92-file vitest suite and all six AST structural gates are advisory**, including `no-synthetic-waveform.test.ts`, written *specifically* to make a shipped honesty bug (a fabricated waveform drawn under the label "Source timeline") impossible. `Visual regression` documents itself in-file as "GATING since 2026-07-29" and is not in the 8 required contexts. Mitigations: `tsc -b` **is** gated transitively through the required Docker build job, and `.githooks/pre-push` runs the vitest suite as a hard local gate (bypassable with `--no-verify`). | `ci.yml:437-457`, `:605-609`; `docs/BRANCHING.md:100-127` | CORRECTED (high → **medium**) | **0.5 h** — one `gh api` call; capture the recurring vitest cold-run flake first. |
| 24 | **The GDPR export is fully built and has no UI**, while `static/privacy.html:162` tells every visitor the export is "made available as a download from your account". Verified by parsing every API path literal in non-test `frontend/src`: 100 distinct backend paths, **zero** containing "export". The sibling erasure capability *did* get UI (`AccountDeletion.tsx`), so this is an omission, not a house convention. Corrections: it is **already tracked** in the repo's own queue (`docs/issues.md:4457`, Issue 495 deferral list), the very next policy paragraph gives a working email channel so Art. 15/20 is exercisable inside the statutory window, and the backend is integration-tested and routed to a queue prod consumes — the hollow is exactly one layer thick (the missing button). | `routers/export.py:44,90,109`; `static/privacy.html:162` | CORRECTED (high → **medium**) | **4 h** UI, or **0.5 h** to correct one policy clause |
| 25 | **`clips_ready` is enqueued with no reference to how many clips exist**, so a zero-clip video emails *"Your 0 clips from "<title>" are ready for review."* plus a "Review your clips" CTA, and writes an in-app row saying *"We found candidate clips from your video."* — with a `NotificationDelivery(status=sent)` committed for it. Both halves reproduced by driving the real `_generate_clips_async` and the production template renderer. Zero clips is a *designed* state (the five-code `skip_reason` taxonomy, the dashboard "Why no clips?" link), and Issue 458 deliberately **increased** how often it happens. The terminal SSE event one line earlier is honest ("Generated 0 clip(s)."); the dishonesty is confined to the outbound notification. No test passes `clip_count=0` anywhere. | `worker/tasks.py:3870-3884`, `:6895-6900`; `notify/templates/clips_ready.{txt,html}` | CORRECTED (high → **medium**) | **2 h** — zero-case copy carrying the `skip_reason` the API already computes. |

### Tier 3 — real, low cost, do them opportunistically

| # | What breaks | Where | Verdict | Fix |
|---|---|---|---|---|
| 26 | `list_clips` hydrates the full ORM entity for up to 100 rows to compute one boolean from a ~15–20 KB TOASTed column, defeating the stated design. Second instance: `_load_hook_texts` loads every `Transcript` in full (word-level `segments_jsonb`, the largest payload in the system) to extract one opening sentence per video. [Corrected: **two** instances, not three — drop the `signals_jsonb` one; those keys are returned to the client.] | `routers/clips.py:824-830`; `dna/builder.py:153-156` | CORRECTED (→ low) | **1 h** — `load_only` + a projected `IS NOT NULL`. |
| 27 | The design-token contract gate can only fail for 16 hardcoded names, so **two undeclared Tailwind utilities are live and emit zero CSS** (`bg-surface-raised` ×2, `bg-accent-subtle`) — the same class the gate was written from. The denylist scoping is deliberate and recorded (a blanket check fights Tailwind's built-in palette; my own blanket scan produced ~34 mostly-false hits), so the accurate framing is "a gate that can only catch the bug it was written from". Corroborating: `bg-surface-raised` was documented as SEV2 with the exact fix on 2026-06-24, and Issue 400a fixed the *other half of the same line* because `border` was on the denylist and `surface-raised` was not. Impact is purely cosmetic. | `frontend/src/test/design-tokens.contract.test.ts:68`; `ActivityPanel.tsx:148,191`; `BrandKitSection.tsx:119` | CORRECTED (high → **medium**, cosmetic) | **2 h** — invert to an allowlist. |
| 28 | Two HTTP handlers hold a request-scoped `AsyncSession` in an open transaction across a multi-minute Anthropic call, contradicting recorded decision Issue 82b *and* `DECISIONS.md:12749`'s claim that the last three such holds were closed — so it is a doc-accuracy defect as much as a code one. Blast radius is small (20/hr + 10/hr per-creator caps, a 24 h cache, a single-flight lock, 2 uvicorn workers). Drop the "must become 202 + job_id" prescription — `analyze_performer` is a 256-token Haiku call. | `routers/insights.py:824,911`; `routers/thumbnails.py:182,273` | CORRECTED (high → **medium**) | **2 h** — release → await → reacquire a re-stamped tenant session (the in-repo pattern). |
| 29 | The LLM conformance registry covers 13 of 17 clients and the model registry 14 of 17 keys; `preference/style_distill.py:31` inherits the SDK's 600 s read timeout. **The durable point is not the timeout** (its caller's `soft_time_limit=120` bounds it): the assertion is `timeout is not None`, which the SDK default satisfies, so listing the module would not have caught it either. | `tests/test_llm_conformance.py:34`; `tests/test_model_config.py:15`; `preference/style_distill.py:31` | CORRECTED (high → **medium**) | **2 h** — AST discovery + require an explicit non-default timeout. |
| 30 | `ix_dna_embeddings_hnsw` serves a query that has never existed; a green integration test attests to it, and `DECISIONS.md:8957`'s justification ("the `<=>` cosine query was an unindexed scan") is false. Also moots Issue 56's deferred RLS/ANN tradeoff, which is unfalsifiable because no ANN scan exists. | `alembic/versions/0006`, `tests/test_vector_index_integration.py:35-39` | CORRECTED (→ **low**) | **1 h** — drop both, or record that it is pre-provisioned. |
| 31 | `clip_impressions` is inserted **and committed inside a GET**, and `Review.tsx:239-246` polls that GET every 4 s while any clip renders — so one viewing session writes ~150 duplicate rows per clip. Any future IPS analysis without a session key will systematically over-weight videos that rendered slowly. [Corrected: the table **is** indexed on `(creator_id, shown_at)` by migration 0037 — the original "no index" failure scenario is refuted; `models.py` merely fails to mirror it.] No retention sweep exists. | `routers/clips.py:857-877`; `models.py:1092-1104` | CORRECTED (→ **low**) | **2 h** — dedupe key + a purge alongside the event-log sweep. |
| 32 | Six of eight Issue-289 price-book constants have **zero production readers**, and `PRICE_BOOK_VERSION` is stored in no column, so the rate-change detection its comment describes is uncomputable. Consequently `usage.cost_estimate` and the spend guard account for **Anthropic only**. The operator-facing false signal is `docs/RUNBOOKS.md:723`, whose Monthly Cost Review heads its first COGS line "LLM **+ transcription** cost estimates" and drives it off `SUM(cost_estimate)`. [Corrections: the two "price-book math" tests are **not** tautologies — overriding `COST_PER_MIN_DEEPGRAM` fails three of them, and their pinning behaviour is a recorded decision; transcription *volume* is metered per creator at ingest and R2 volume by real gauges — only the USD conversion is missing.] **The right fix is one DECISIONS sentence and a corrected runbook heading, not new instrumentation.** | `config.py:179-199`; `docs/RUNBOOKS.md:723` | CORRECTED (high → **low**) | **1 h** |
| 33 | The `usage` ledger has no *programmatic* reader — but it is **not dead output**: its designed consumer is the operator-run Monthly Cost Review (two raw-SQL aggregates), pinned in CI by `tests/test_incident_docs.py:35-45`, and `GO_LIVE.md:84` honestly rates that row CODE-GREEN. What is genuinely wrong is smaller: `Usage.videos_processed` and `Usage.clips_generated` are written by nothing and read by nothing (permanently 0); `tests/test_usage_coverage.py:3-6`'s docstring **misstates the semantics** ("deducts from the creator's token/minute pack" — it does not; minutes come from video duration at ingest), so its "revenue leak" rationale is false even though the gate is real (it protects the live Redis spend-guard rail); and the GDPR export omits `usage`. | `models.py:1304-1322`; `billing/ledger.py:75`; `tests/test_usage_coverage.py:3-6` | CORRECTED (high → **low**) | **1 h** |
| 34 | Even after `METRICS_TOKEN` and a scraper are fixed, the Prometheus rail comes back **wrong**, not right: `prometheus_client` is single-process (`PROMETHEUS_MULTIPROC_DIR` unset, `MutexValue` verified), so 6 of 12 metrics live in `worker`/`render-worker`/`beat` registries no scrape can reach — and `llm_tokens_total`/`llm_cost_usd_total` are ~61% worker-side (11 of 18 `record_llm_usage` sites), so they would **under-report silently**, which is worse than being absent. `uvicorn --workers 2` splits even the HTTP histogram. [This is a rider on d08's "metrics exported nowhere", not an independent finding; the "daily paid R2 sweep" framing is immaterial — ~$0.003/month.] | `observability.py:84-165`, `:786-788`; `main.py:476` | CORRECTED (high → **low**) | **2 h** *when* the rail is repaired |
| 35 | `docs/MIGRATIONS.md` Rule 2's NOT NULL recipe (and Templates C/D) stops after `VALIDATE CONSTRAINT` and never emits `SET NOT NULL` or drops the redundant CHECK — so a follower gets a validated CHECK while `attnotnull` is never set and every model reader still sees `nullable=True`. Latent (zero `NOT VALID` usages exist today) and the same shape as the already-logged Rule 4 defect (`UPDATE … LIMIT` is MySQL). | `docs/MIGRATIONS.md:64-80,166-200` | **CONFIRMED** | **0.5 h** — append to the existing OFF_COURSE row. |
| 36 | `docs/DEPLOYMENT.md:162-163` claims `/health` returns **503** when a dependency is down. It does not — it always returns 200 with the JSON `status` flipping to "degraded", and that behaviour is **deliberate and documented** (liveness-only, so a blip does not trigger an autoheal restart). The risk is that the false sentence leads an operator to configure status-code matching only and lose the body match on `"status":"ok"`, which is the load-bearing half. **Do not file this as missing alerting** — that is #282, an explicit owner deferral with written re-open criteria. | `main.py:553-567`; `docs/DEPLOYMENT.md:156,162-163` | CORRECTED (high → **low**) | **0.25 h** |
| 37 | The reframe geometry subsystem (3,189 lines, 38% of `clip_engine`) has absorbed 7 of the last 15 clip-engine issues, each adding hand-tuned constants with **no held-out labelled set** behind any of them; lightweight self-hosted ASD (LR-ASD, IJCV 2025) is a legitimate no-new-sub-processor upgrade path. [Corrected to a **low** design-direction item; the specific mouth-motion failure scenario is unsupported and is not the mechanism behind Issues 440 or 450. Cannot be promoted until someone measures CPU-only LR-ASD latency per clip on this VM.] | `clip_engine/reframe.py`, `speaker_map.py` | CORRECTED (→ **low**) | research spike |
| 38 | 8 `Settings` fields are undocumented in `.env.example` — three of them **Anthropic model overrides** an operator cannot discover, plus `COST_CACHE_WRITE_MULTIPLIER` (money path), `CELERY_SOFT_TIME_LIMIT_S`, `YOUTUBE_PUBLISH_PRIVACY`, `MAX_INGESTED_CHANNEL_TITLE_CHARS`, `REDBEAT_REDIS_URL` — and no parity test exists. [This is the real residual of the "67 unused settings" finding; **drop the `extra="ignore"` scenario** — `extra="forbid"` is not an option and none of the three cited precedent incidents were caused by an ignored key.] | `config.py` ↔ `.env.example` | CORRECTED (→ **low**) | **1 h** — one parity test iterating `Settings.model_fields`. |
| 39 | The clip-quality funnel breadcrumb (`peaks=… pre_nms=… after_nms=… final=…`, "why did I get N clips?") is at **DEBUG**, and prod `LOG_LEVEL=INFO` with DEBUG explicitly forbidden — so the one raw-vs-kept breadcrumb on the clip pipeline is structurally unreachable in the only environment that matters. Separately, a wholly-empty LLM score set (`scored = []`) silently degrades 100% of candidates to signal-only with **no aggregate log line**. | `clip_engine/candidates.py:372-379`; `clip_engine/scoring.py:521-600` | **[unverified — mD/D-7]** | **1 h** |

### Unverified but usable — labelled, not ranked with the above

These were never adversarially checked. They are consistent with the verified material and several
are cheap, but treat their severity and scope as unconfirmed.

- **`alembic check` does not exist in CI, and 20 of 28 production indexes are absent from
  `Base.metadata`** (`alembic/env.py:18`). A column added to `models.py` with no migration passes
  the unit lane (DB mocked), the integration lane (if no test touches it), and prod's `upgrade head`
  (a legitimate no-op) — then 500s on every request. One CI step closes it; it will fail today until
  those 20 indexes are declared. *[d02 F3]*
- **Six versioned JSONB payloads, no upcaster anywhere, and the two readers behave in opposite
  ways** — `ClipEditDocument` rejects a newer version (correct), `parse_geometry` returns `None`
  (degrades to a *plausible wrong answer*: a trimmed clip reports its untrimmed source duration and
  claims no baked edits). A version bump with no backfill silently re-introduces the exact
  regression Issue 470 closed. *[d02 F6]*
- **Erasure discards the purge result.** `purge_uris` returns the set it *succeeded* in deleting and
  its docstring says the caller must keep the DB pointer for the rest; `routers/auth.py:513-520`
  logs a count, discards the return, and deletes the creator anyway. An Object-Lock-refused
  `clips/{id}.mp4` then lives in R2 with no row, no creator-scoped prefix, and no sweep that can
  reach it — and the endpoint returned 204. *[d07 F6]*
- **The 30-day YouTube analytics purge is real and correct for its four tables and leaves a verbatim
  frozen copy of the same analytics in `creator_dna.patterns_jsonb`, which is never deleted.** Fine
  for an active creator under ToS §III.E.4.b; the exposure is precisely the revoked-token case the
  purge exists for. *[d07 F5]*
- **No Docker log rotation anywhere** (`json-file` defaults `max-size` to unlimited), 8 always-on
  containers, one disk. A full volume stops Postgres WAL writes → every upload/render/grant fails →
  `/health` reports `postgres: error` → autoheal restart-loops forever. Five-minute fix; most
  plausible way this VM dies on its own. *[d08 F6]*
- **Refunds and chargebacks reported by Stripe are unhandled in both directions**, and reconciliation
  runs Stripe→ledger only. A charged-back Stream pack leaves the minutes spendable. Issue 208
  decided refunds *we* initiate; it says nothing about refunds Stripe *reports*. *[d09 F4]*
- **Spend-guard fail-open is unobservable** — one log line per process, no counter, no
  `record_event`, and a disarmed cost cap is indistinguishable from a working one. *[d09 F6]*
- **No `stop_grace_period`; `SoftTimeLimitExceeded` handled on 5 of ~41 tasks; no backpressure
  position; beat has no liveness on the live deployment** (the last is corroborated by verified
  finding #9). *[d03 F2/F3/F5/F7]*
- **The efficacy harness has no DNA-only arm**, so it structurally cannot see the LightGBM
  degeneracy; and **`clip_impressions` + `ClipTriage` — the two most valuable data assets in the
  project — have no reader, no query, and no owner.** `grep keep_rate` returns zero hits repo-wide.
  *[d05 F3/F5]*
- **The behavioural clip-quality eval is n=2, runs strictly post-deploy, and nothing watches it**;
  the `eval/clip-quality` required status posts `success` with "Skipped" for any PR that does not
  touch `clip_engine/` — and a `config.py` model swap or a `knowledge/util.py` prompt change does
  not touch it. *[d05 F4, d04 F5]*
- Sweep items not individually verified: `test_every_ffmpeg_task_is_routed` compares a literal to a
  copy of itself **and its claim is already false** (`ingest_video`, `backfill_video_peaks`,
  `backfill_video_camera_regions` all shell out to ffmpeg on the default queue) *[mC-7]*; no probe
  anywhere performs an R2 **write** *[mC-6]*; `live_smoke.check_pipeline` reads back the rows
  `_seed()` just wrote and prints four `[PASS] pipeline:` lines *[mC-5]*; `check_publish` reports
  PASS on an `import` statement *[mC-8]*; `CSRF_FETCH_METADATA_ENABLED` defaults off, is set by no
  deploy artifact, and a green required-lane test asserts cross-site POST succeeds *[E6]*; the OAuth
  redirect URI's *path* is reconciled against the route table by nothing — the same shape as the
  `/webhooks/stripe` 404 *[E5]*; R2 CORS is set from `sys.argv` and reconciled by nothing *[E4]*;
  `VideoContext.prompt_version` is at v3 and compared by nothing, so no prompt improvement ever
  reaches an already-processed video *[F4]*; clickwrap consent versions are recorded once and
  compared never, and `PRIVACY_VERSION` has **already been bumped** without re-prompting anyone
  *[F5]*; `GET /billing/packs` is dead and `Pricing.tsx` retypes the prices, guarded by a test that
  asserts three hardcoded strings *[F6]* — that one is a money-path drift trap and I would fix it
  even unverified.

### Excluded — REFUTED, do not re-file

- *"`response.model` is never recorded, so a rolling alias can re-point silently."* **Refuted by the
  vendor docs**: every model this repo configures is a pinned snapshot (4.6-generation dateless IDs
  are pinned, not evergreen) or a terminal single-snapshot alias. Two evidence citations were also
  wrong — `record_llm_usage` takes no model argument and the ledger has no model column.
- *"`render.yaml` is armed config that would deploy a second `ENV=production` copy with
  `VERBOSE_LOGGING_ALLOW_PROD=true`."* **Refuted** — `autoDeployTrigger: commit` is Render's
  documented default and the file is inert unless a Blueprint is linked, which is already an owner
  check. Residual is hygiene only (§4).
- *"The prompt-cache floor was never revisited after the Opus 5 move."* **Refuted** — the exact
  tradeoff is recorded at `DECISIONS.md:969-971`; the floor helper is shared with Sonnet callers
  where 512 would emit inert 2× writes. (A second verifier kept a *low* residual worth ~$0.003/call;
  net economic value over the whole beta is single-digit dollars. **Leave it.**)
- *"`Unit` and `Coverage floor` duplicate ~190 s of work per PR."* **Refuted** — the repo is public
  so Actions are free, Coverage is never on the critical path, the jobs are not duplicates, and
  consolidating them would re-introduce the exact 7-week vacuous-gate outage Issue 479 fixed.
- *"`ci_local.sh` prints 'Local CI passed' after skipping every gate."* **Refuted** on three
  independent grounds, including that the premise misdescribes the maintainer's actual environment.

---

## 4. What would you delete?

**First, the headline number is not the problem.** 56k source lines for 98 endpoints, a 3,200-line
computer-vision reframe pipeline, a Celery worker, 17 LLM call sites and a billing ledger is roughly
what this product *is*. 78k test lines against 56k source (1.39:1) is within normal. **Do not go
looking for lines to delete.** Go looking for the ~5% that is duplicated logic and the ~1% that is
configuration surface nobody turns.

### Delete

| What | Size | Why |
|---|---|---|
| ~55 clip-engine algorithm constants → module constants beside their algorithms (`reframe.py`, `overlay_bands.py`, `camera_region.py`, `sentence_snap.py`, `filler.py`) | ~300 lines of `config.py:311-660` + ~55 `.env.example` rows | Set by **zero** of the five committed deployment configs. Keep the ~8 that genuinely vary per deploy (feature toggles, `MEDIAPIPE_FACE_MODEL_PATH`) and the documented pre-calibration tunables. |
| `worker/tasks.py:2448-2457` `_render_start_for` + the 12 other inline `setup_start_s if … else start_s` expressions | ~40 lines | Duplicated *logic* — the only duplication that hurts. `clip_engine/edits.py:360` already claims "so no surface can mis-measure clips" and is imported at 1 of 14 sites. Add a source-scan test banning the inline form. |
| `ix_dna_embeddings_hnsw` + its integration-test assertion | 1 index, 1 test | No reader, ever. |
| `Usage.videos_processed`, `Usage.clips_generated`; `EventLog.status_code`, `EventLog.duration_ms` | 4 columns | Written by nothing, read by nothing, permanently 0/NULL — and misleading to an operator running `SELECT *`. *(event_log columns [unverified])* |
| 34 skipped functions in `tests/test_static.py` (Issue-226 static-page residue) + 8 skips in the issue-numbered files | ~a few hundred lines | [Corrected — the original "4,400 lines" claim is wrong.] **Rename the rest, do not delete them**: `test_static.py` is now the security-header/legal-copy suite, and the eight `test_issue_*.py` files hold 77 *live* tests named for when they were written. |
| `settings.MAX_SNAP_S` + `SENTENCE_BOUNDARY_MIN_PAUSE_MS` and the structural tripwire test pinning the dead signature | ~30 lines | Snapping moved to `sentence_snap.py`, which uses its own module constant. An operator tuning these in `/opt/autoclip/.env` changes nothing, silently. **[unverified — E8]** |
| `render.yaml` (219 lines), `deploy/charts/` (11 files, 532 lines), 12 root PNGs (~1.0 MB) | ~750 lines + 1 MB | **Hygiene only** — the "armed config" framing was refuted. `git rm`, tag `parked/render-blueprint` and `parked/helm-chart`. Git tags are the archive. |
| `GET /creators/me/thumbnail-patterns` | ~180 lines incl. the single-flight lock | Three stacked rate limits, two dependencies, a 24 h cache, a SEV1-grade single-flight protocol and ~13 tests, protecting an endpoint **no client calls**. The underlying vision analysis is alive via `/thumbnail-concepts`. Delete the read endpoint, keep `knowledge/thumbnails.py`. **[unverified — F7]** |
| The `demographics` fetch — *or* wire it | 1 daily paid Analytics call per creator | `select(Demographics)` appears nowhere in production code, while `README.md:27`, `walkthrough.md:11`, `docs/SOT.md:25` and `docs/COMPLIANCE.md:156` all state it feeds the DNA — and COMPLIANCE justifies the `yt-analytics.readonly` scope to Google *by naming demographics as a purpose*. Under data-minimisation, PII-adjacent data collected under a stated purpose that does not exist is the worst kind of dead output. **Delete the fetch and correct four documents, or wire it into `dna/builder.py`.** **[unverified — F8]** |
| `GET /api/logs/me` | ~40 lines | No client; the docstring's own fallback is "operators query the table directly", which is honest — so delete the endpoint, keep the rail. **[unverified — F9]** |

### Wire up — these are half-built features where finishing is right and deleting is wrong

- **The GDPR export UI** (§3 #24). The backend is built, tested and routed; the Privacy Policy
  already promises it. It is one button. *(Or, if it stays deferred, fix the one policy clause.)*
- **`ClipTriage` + `clip_impressions` → a keep-rate metric.** These are the two most valuable data
  assets the project owns. `ClipTriage` has been producing one clean, deduped human label per clip
  since 2026-08-10 — the thing the highlight-detection field would kill for. `grep keep_rate`
  returns **zero hits repo-wide**. `kept / (kept + dropped)` bucketed by served rank is a SQL query.
  It is also the only way to ever answer "did the Opus 5 upgrade make clips better?"
- **`clips.blended_score`** — written on every rerank, serialized into no response, read by no
  client. Finish the loop or the personalization work is unobservable end to end. **[unverified — B11]**
- **The BFF envelope's `state` and `next_action`** — a documented, deliberate deviation from
  AIP-158/REST was paid for, and two thirds of it was never collected client-side, so empty states
  are hand-written in the SPA anyway — the exact duplication the deviation removed.
- **`VideoTranscriptOut.degraded` and `ClipListOut.truncated`.** [Corrected: `degraded` *does* reach
  a human, as a raw snake_case SSE step label flashed during ingest — which is its own problem. No
  **durable** surface reads it.] A creator who wasn't watching the stream trims against a transcript
  the system already knows is bad.
- **`GET /billing/packs`** → drive `Pricing.tsx` from it. Two sources of pricing truth, and the test
  claiming to enforce parity asserts three hardcoded strings. Money-path drift trap.
  **[unverified — F6]**
- **Consent-version comparison** and **`VideoContext.prompt_version` comparison** — both are
  recorded stamps whose entire purpose is a comparison that was never written, and `PRIVACY_VERSION`
  has already been bumped once past every existing creator. **[unverified — F4/F5]**

### Keep — it is cheap, and deleting it would be a mistake

- **The 78k lines of tests.** The ratio is normal and the best of them (`test_usage_coverage.py`,
  `test_scoring_goldens.py`, the eval scenarios) are the strongest quality mechanism in the repo.
- **`clip_engine/` at 8,365 lines including 3,189 of reframe geometry.** That is the product.
- **`clips` as one table**, `config.py` as one class (after moving the algorithm constants), no
  service layer, and the Helm chart *as a tag* if the product ever outgrows the beta.
- **`preference/efficacy.py` (509 lines)** even though nothing calls it in production — it is real
  offline ranking methodology (nDCG/MAP/MRR, chronological split, paired bootstrap CI) and it is one
  dictionary entry away from being able to answer whether personalization helps.

---

## 5. Where the next bug lands

Ranked by (churn × defect density × gate-coverage weakness). **The disagreements with an
intuitive ranking are the point, so I've marked them.**

**1. `worker/tasks.py`** — 131 commits (highest in the repo), 7,179 lines, ~41 tasks, **no
per-module coverage floor**, and ~60 hand-rolled envelopes with nothing enforcing which session
factory / lock / spend check / progress event a new task uses. Every historical defect here is a
*missing cross-cutting concern*, and this audit added four more (zombie resume, unverified render
output, `clips_ready` on zero clips, `render_video_clips_done` counting attempts). Until the
`@task_body` envelope exists, this file will keep generating one-line omissions that no gate sees.

**2. The reframe geometry cluster** (`clip_engine/reframe.py` 1,363 + `speaker_map.py` +
`shots.py` + `camera_region.py` + `overlay_bands.py`) — 7 of the last 15 clip-engine decisions were
"we picked the wrong face/seat/region", each adding a hand-tuned threshold with **no held-out set
behind any of them**, and the eval harness asserts *candidate geometry*, not render framing, so it
structurally cannot see a regression here. Each fix is validated only against the instance that
produced it.

**3. `routers/clips.py`** — 2,893 lines, 27 endpoints, 67 commits, carries 3 of the 4 open SEV2s, no
per-module floor, and it is where the read-path cost (#26), the impression write-amplification
(#31) and the `blended_score`/`skip_reason` surfacing all live. It is also the single file a
frontend contract break would hit first.

**4. `preference/` + `clip_engine/ranking.py` — and this is my sharpest disagreement with a
churn-based ranking.** Churn puts this near the bottom (24–31 commits, quiet for weeks). I put it
fourth because **the signal here is currently zero**: the model is degenerate across its entire
serving band, the gate written to catch that certifies the opposite, the efficacy harness lacks the
one arm that would expose it, and the API reports `active: true` regardless. A module where every
instrument reads green while the feature does nothing is not low-risk; it is a place where bugs land
and *stay*.

**5. The deploy/CI configuration surface, treated as one module** — `.github/workflows/deploy.yml`,
`run_layer0.py`, the three compose files, `scripts/doctor.py`. **This is the ranking most likely to
surprise**, because it is not a "module" in `docs/SOT.md`'s sense. But it produced **eight verified
defects in a single sweep** (#8, #9, #15, #16, #17, #18, #19, #20), it is exempt from the patch-
coverage gate by `pyproject.toml`'s `omit` list (`scripts/*`, `.claude/*`), and `run_layer0.py` is
literally exempt from the gate it implements. Highest defect density per line in the entire audit.

**6. `chat/`** — **never type-checked by mypy at all**, outside `_LLM_MODULES`,
`_BILLED_LLM_ROUTES`, `_LLM_RENDER_ROUTERS` and `_LLM_ROUTES`, both routes missing a burst cap, and
it is exactly where two of the July SEV2s landed. The lowest gate coverage of any live package.

**7. The Stripe boundary** (`routers/billing.py:245-260`, `billing/stripe_client.py`) — *not*
`billing/ledger.py`, which is the strongest code in the repo. Every remaining money defect is on the
*edge*: an unhandled event type, a Dashboard-controlled payment-method set, a reconcile window keyed
on the wrong timestamp, an unverified webhook URL. This is where a single Dashboard click still
turns into silent revenue loss.

**8. `frontend/src/hooks/`** — 14 of 15 hooks have no colocated test, including `useEditDocument`
(the autosave + revision-CAS path) and `useUploader`; the suite that would test them is advisory;
there is no coverage measurement at all. The failure shape is the one this project has already paid
for twice: the UI reports success over a discarded write.

**9. `config.py` / `.env.example`** — #2 and #3 by churn (99 / 87 commits) and the epicenter of the
config-drift class, but the defects it produces are *drift* (8 undocumented settings, dead knobs),
not logic. High frequency, low consequence. One parity test defuses most of it.

**Where the next bug will *not* land** (and this is worth saying, because these have churn):
`models.py` + `alembic/versions/` (57 commits, but the discipline is top-decile and each migration
is self-documenting), `db.py` + the RLS core, `billing/ledger.py`, and `knowledge/util.py`. These
are the parts of the codebase that have earned the right to be left alone.

---

## 6. What is genuinely right

Named specifically so a future session does not "improve" any of them.

**Tenancy — above current standard.**
`db.py:200` `tenant_session(creator_id)` takes the tenant id as a **required argument**, so a call
site structurally cannot forget the GUC (55 uses in `worker/tasks.py` alone); `AdminSessionLocal` is
the only escape hatch and its allowlist is pinned **bidirectionally** by
`tests/test_worker_invariants.py`; one `after_begin` listener serves both factories; migration 0045's
`NULLIF` hardening converts a pooled-connection crash into a deny; `clip_edit_documents`
denormalises `creator_id` specifically so its policy is a direct-column comparison, and the pattern
has a *name* ("the house pattern"). `routers/_owned.py` returns 404 for both missing and foreign,
documented as defence-in-depth *under* RLS rather than as the isolation mechanism.

**The billing ledger — the strongest code in the repo.**
`grant_minutes` and `deduct_for_video` are mirror images: fast-path SELECT → `begin_nested()`
SAVEPOINT → `flush()` to surface the UNIQUE conflict *now* → clean no-op. The balance update is a
single conditional `UPDATE … WHERE minutes_balance >= minutes RETURNING` — no read-modify-write.
`grant_minutes` correctly **re-raises** `IntegrityError` when `stripe_session_id is None`, because a
non-keyed grant has no UNIQUE to race on and swallowing it would silently give a beta user 0 trial
minutes — that distinction is the difference between an idempotency handler and a bug-swallower, and
it is explained in the code. The `after_commit`/`after_rollback` listener pair is a correct
transactional outbox. `model_rates()` falls back to **Opus** rates for unknown models — failing
expensive rather than cheap on the money path. The Stripe idempotency key is tenant-prefixed with
the account-wide collision hazard spelled out. Webhook rate limiting sits **in front of** signature
verification. `payment_status == "paid"` is deliberately tighter than Stripe's own reference, with
the reason stated.

**`tests/test_usage_coverage.py` — the model every other gate should copy.**
A repo-wide AST sweep that discovers every `messages.create`/`messages.stream` site and fails on
**both** unmapped sites *and* stale map entries. The staleness half is what most such gates omit and
is why this one cannot rot into a vacuous pass. Its per-caller evidence markers are deliberately
narrowed so a sibling billing line cannot satisfy them. (Fix its docstring — it misstates what
`record_llm_usage` does — but do not touch the mechanism.)

**Prompt-injection defence and LLM honesty.**
`wrap_untrusted()` JSON-encodes attacker-influenceable text into an XML-labelled envelope in the
**user** role, plus an `<untrusted_content_policy>` clause in every system prompt, single-sourced in
`knowledge/util.py`, citing OWASP LLM01:2025 — verified present in all 17 modules including the four
the conformance registry misses. `dna_system_block` returns **`None`** rather than a placeholder,
with the honesty incident that motivated it written into the docstring, and the disclaimer is keyed
off the same signal so the two cannot drift. Refusal handling (`stop_reason == "refusal"`) at all
three Opus 5 sites, degrading honestly instead of indexing into an empty `content` array.
`max_tokens` raised at each site for Opus 5's default-on thinking, with the reason recorded — the
most common Opus-5 migration bug, caught in all three places.

**Prompt caching that is measured, not assumed** — floor-gated on a real token count, static block
first, per-creator DNA second, style notes appended *after* the last breakpoint with the byte-prefix
reasoning written out, and the log line reads back `usage.cache_creation.ephemeral_1h_input_tokens`
to confirm the write landed in the 1-hour tier. Most teams set `cache_control` and never check.

**Migration discipline — top-decile.**
`0059` is a nullable `ADD COLUMN` whose docstring explains why there is deliberately no backfill,
why no new RLS policy is needed, why no index, and which template it follows. `0062` is a batched,
idempotent, offline-mode-aware data repair with a **termination proof in a comment** and a
`logger.warning` that explains why it is WARNING and not INFO. `alembic/env.py:47-58` carries the
full archaeology of the silent-no-op-migration outage. Correct Postgres enum handling across 22
`sa.Enum` columns. `Clip.__table_args__`'s `DEFERRABLE INITIALLY DEFERRED UNIQUE(video_id, rank)` is
a correct and non-obvious use of deferred constraints, and `Clip.triage`'s `server_default` is
load-bearing during rolling restarts and says so.

**Celery mechanics.**
`visibility_timeout_s()` *derives* the `soft < hard < visibility` invariant instead of
hand-maintaining it, with the Redis-broker caveat quoted and the failure it prevents named — the
single most commonly missed Celery+Redis footgun, closed structurally.
`acks_late` + `prefetch_multiplier=1` + `reject_on_worker_lost` is the documented triple, with its
precondition stated rather than assumed. `_rollback_then_unlock` rolls back first so the unlock can
run and falls back to `session.invalidate()` so Postgres frees the lock at session end.
`_keyset_batches` bounds sweep memory with keyset pagination on exactly the two unbounded result
sets. The DEFERRABLE `uq_clips_video_rank` compare-and-set distinguishes a lost race from a real
integrity bug instead of swallowing both. **39 of 40 tasks pin `name=` explicitly** — almost nobody
does this before it bites them.

**The clip-quality eval gate.** 32 scenarios, a 100% pass-rate assertion, a ratcheted
`SCENARIO_FLOOR` pinned in **two** files, a regex scan forbidding unapproved `skip`/`xfail` with an
empty allowlist, and — the detail that shows real understanding — CI enforcement as a **commit
status rather than a required job, because GitHub reports a skipped required job as success.**
`tests/test_scoring_goldens.py` replays **real recorded Anthropic response bodies** through
`anthropic.types.Message.model_validate` into the real parse path, including a genuine
`stop_reason="max_tokens"` truncation golden, with a sha256 pin on `_OUTPUT_SCHEMA` and a
configured-model pin that reds CI on a model swap.

**Privacy and erasure.** `worker/erasure.py` enumerates DB pointers **∪** deterministically
constructed keys, so it reaches the clean-confirm orphans a prefix sweep cannot, with each pattern
pinned by `tests/test_erasure_keys.py`. The deletion audit row stores the UUID and **deliberately
not** the email or channel id, with the reasoning inline. `scripts/reapply_erasures.py` replays the
cascade after a restore and is mandatory in the DR runbook. All 28 `creator_id` FKs are
`ON DELETE CASCADE`; `event_logs` is purged explicitly. `crypto.py` is 55 clean lines of MultiFernet
with a rotation script, a runbook, and a field validator that rejects a malformed previous key
*before* it can break a live rotation.

**The staging gate — the strongest thing in the pipeline.** It deploys the exact `sha-` image (never
`:latest`), migrates a **persistent** volume, asserts `alembic current == heads`, and keeps the
volume by using `stop` not `down`. Every one of those four choices exists because a real incident
taught it, and the comments say which. The rollback still **exits 1** (`"auto-rollback without exit
1 would hide the deployment failure from alerting"`), `PREV_IMAGE` is captured by **RepoDigest** so
the target is immutable, prune runs *after* the smoke, and `sync_secret` never blanks a VM value.
`tests/test_ci_config.py` testing the pipeline *as an artifact* is unusual and correct.

**Frontend.** `lib/` is genuinely pure-function-first — every piece of hard math (`timelineZoom`,
`editorCuts`, `editCommands`, `saveScheduler`, `peaks`, `cropTrack`, `fit`, `safeUrl`) is a pure
module with a colocated test and thin components over it. `src/test/sourceScan.ts` walks the
TypeScript AST (with a written explanation of why regex fails on this tree) and exposes
`sourcePaths()` so a caller can assert the glob matched. `isCropTrack` and `isWaveformPeaks` are the
only two runtime type guards in the app and they sit at **exactly** the two endpoints where the
backend declares no schema — the instinct was correct and precisely targeted. `TimelineRail.tsx:70`
documents why the container is `role="group"` and not `role="slider"` (`role="slider"` forces
descendants to `presentation`) — a correct, sourced, non-obvious call.

**Incident-archaeology comments** (`worker/tasks.py:5334-5340`, `db.py:31-35`,
`routers/thumbnails.py:167-177`, `main.py:188-210`, `celery_app.py:66-70`, `alembic/env.py:47-58`).
Several findings in this audit were **only findable because those comments stated the intended
rule** and the code had drifted from it. That is the highest compliment an auditor can pay a
codebase.

---

### The two-day fix list, if you only do one thing

Sorted by verified impact per hour, all from Tier 1–2:

1. `in_memory_fallback_enabled=True` on the limiter + a Redis-down test — **2 h** (#1)
2. `min_child_samples` / threshold fix + the unbalanced-fixture assertion — **6 h** (#2, #3)
3. `--full` on the preflight; mirror the staging smoke invocation in prod; scoped rollback;
   `alembic current == head` in the prod job — **3.5 h** (#8, #16, #17, #15)
4. ffprobe the render output on all three entry points — **3 h** (#6)
5. `BACKUP_R2_BUCKET` + `BACKUP_HEALTHCHECK_URL` + one cron line — **2 h** (#4)
6. `payment_method_types=["card"]` + the async-payment branch — **2 h** (#5)
7. Replace the four literal registries (quota, flags, RLS, conformance) with discovery — **8 h**
   (#12, #13, #14, #29)
8. `run_async` cancel-and-drain; socket timeout on the shared Redis client; worker dimension on
   `/health` + healthcheck on `beat` — **6 h** (#7, #10, #9)
