# Ground truth — architecture map & decision index

**Produced:** 2026-08-17, Phase 0 of the deep standards audit.
**Purpose:** the real architecture (not the one `docs/SOT.md` claims), a categorized index of all
259 `docs/DECISIONS.md` entries, the load-bearing bets with rationale present/absent, and the named
decisions that were never made. **Descriptive, not prescriptive.**

---

**Scale of the artifact set:** `docs/DECISIONS.md` = **259 `##`-level decision entries** spanning 2026-05-25 → 2026-08-15 (13,018 lines). Backend Python ≈ 56k LOC (excl. tests), tests ≈ 78k LOC, frontend ≈ 34k LOC, 62 Alembic migrations, 39 tables, 98 HTTP endpoints.

---

# A) CATEGORIZED INDEX OF ALL 259 DECISIONS

Format: `line — date — heading` (heading text is itself the one-line summary; I add a gloss only where the heading is opaque).

## A1. Stack, platform, hosting, deploy, CI/CD (34)

| Line | Date | Decision |
|---|---|---|
| 10139 | 05-25 | **Project Kickoff Decisions** — north star, GKE Autopilot+Helm+KEDA as prod target, httpx over google-auth-oauthlib, pgvector/pg16 image, Deepgram as MVP transcription default, `asyncio.run()` in Celery tasks, numeric thresholds |
| 10315 | 05-26 | Beta deployment: VM + Docker Compose, **not** Kubernetes |
| 10348 | 05-27 | Issue 31: Operability kit (secrets registry, preflight doctor, deploy hardening, auto-heal) |
| 8200 | 05-30 | Container: `PYTHONPATH=/app` (prod DNA-stuck hotfix) |
| 9218 | 05-29 | Production-assessment harness + quality gates |
| 9177 | 05-29 | Skill-freshness convention + standards SSOT |
| 7417 | 05-31 | CI/CD: deploy job → self-hosted runner + manual deploy script |
| 7160 | 05-31 | Issue 101: `docker-publish.yml` → self-hosted runner |
| 3163 | 06-23 | Hybrid self-hosted + local CI/CD (off GitHub-hosted runners) |
| 5256 | 06-17 | Issue 144: CI consolidation, integration-on-PR, Cloudflare health monitoring |
| 2932 | 06-24 | CI green-up: real mypy fix + CVE pins + sudo-free workflow + paths-filter perms |
| 5226 | 06-17 | Issue 145: staging + main branch model (protection deferred to GitHub Pro) |
| 8 | 08-15 | **Retire the `staging` BRANCH; enforce branch protection against admins on `main`** (trunk-based) |
| 11659 | — | Issue 297: CalVer release versioning + auto Git/image tag on every main push |
| 11482 | — | Issue 271: single-VM auto-rollback on failed deploy smoke test |
| 12558 | 07-20 | Issue 360: PR CI moved off the prod-VM runner |
| 12588 | 07-20 | `deploy_ci` SEV2 hardening judgment calls |
| 2541 | 06-24 | **Beta hosting: managed PaaS (Render), not the self-managed VM** |
| 2563 | 06-24 | Render Blueprint added as the BETA host (GKE remains the scale path) |
| 2473 | 06-26 | **v1 scope locked to ≤100-user private beta; build-for-10k infra track DESCOPED** |
| 2395 | 06-27 | Issue 326 observability activates on the VM (deploy.yml secret-sync), **not** render.yaml — "the live app does not run on Render" |
| 12072 | 06-26 | Issue 326: beta observability via managed Grafana Cloud + Sentry + OTel |
| 4177 | 06-23 | Issue 259: PgBouncer sidecar on worker Deployment; admin_engine pool shrunk |
| 4212 | 06-23 | Issue 264: PgBouncer image → `edoburu/pgbouncer`, digest-pinned |
| 4336 | 06-23 | Issue 263: RedBeat adopted as Celery beat scheduler; beat liveness probe |
| 2863 | 06-24 | Issue 312: slowapi limiter keeps SYNC Redis storage + bounded socket timeout |
| 1946 | 07-01 | `event_log.py` pool pinned small; added to DEPLOYMENT.md connection budget (347) |
| 9138 | 05-29 | Issue 58: psycopg3 prepared statements + pool sizing for PgBouncer |
| 2425 | 06-26 | **Object storage (R2) mandatory in production; storage misconfig fail-fast + observable** |
| 12188 | 07-01 | Retain source video for rendering; split audio to `audio_uri` (migration 0039) |
| 2327 | 06-27 | DR batch: key escrow, encrypted PG backups, pre-migration dump, R2 immutability (255–258) |
| 10485 | 05-28 | Issue 32: pin `starlette` explicitly against transitive shadowing |
| 5295 | 06-17 | Issue 143: starlette 1.x migration + CVE remediation (FastAPI bump) |
| 150 | 08-13 | **Launch sequencing for a non-friend audience: four calls** |

## A2. Data model, DB, migrations, multi-tenancy (17)

| Line | Date | Decision |
|---|---|---|
| 9453 | 05-28 | **Issue 56: Postgres Row-Level Security — adopt now** (the tenancy keystone) |
| 9283 | 05-28 | Issue 79: RLS implementation per Issue 56 decision |
| 1961 | 06-30 | **Activate the RLS role split via the app role only; keep superuser as migrate role (343)** |
| 1908 | 07-01 | Issue 348: chat worker → `AsyncSessionLocal` + GUC; RLS added to 5 child tables |
| 8957 | 05-29 | Batch 3 (Issue 65): pgvector HNSW index + FK index |
| 8990 | 05-29 | Batch 2 (63+64): idempotent unique-keyed writes |
| 12474 | 07-20 | Issue 361 (races batch): shape of the two unique backstops |
| 11463 | — | Issue 270: migration safety — Squawk lint + lock/statement timeouts + rollback runbook |
| 12312 | 07-02 | Squawk gate made real: PyPI install, per-file rendering, offline timeouts |
| 6676* | 06-01 | Issue 118: `feedback_tags` as JSONB list; empty list stored as null |
| 6694* | 06-01 | Issue 119: ffmpeg drawtext for subtitle presets; `style_preset` as JSONB |
| 1161 | 08-04 | Issue 391 PR A: edit persistence — 3 deviations (autosave rate tier, no query invalidation, migration 0052 repairs 0048/0049) |
| 1124 | 08-04 | Issue 391 PR B: the render reads the document; `segments` deleted |
| 500 | 08-10 | Issue 444: triage is a verdict; a clip carries exactly one label (enum column + derived feedback row in one txn) |
| 12817 | — | Channel Fingerprint — two-tier DNA artifact; shareable tier excludes all analytics-derived fields (379) |
| 4287 | 06-23 | Issue 250: event-log retention (90 days) + inactive-account policy |
| 11107 | 06-23 | Issue 151: `event_logs` admin/query surface deferred to Issue 240 (Loki aggregator) |

`*` = `###` sub-entry under the 113–119 wave, listed because it is a schema decision.

## A3. LLM layer — models, caching, cost, structure, safety (25)

| Line | Date | Decision |
|---|---|---|
| 10095 | 05-28 | Issue 37: External SDK timeouts + retry-with-backoff (module-level singleton clients) |
| 7709 | 05-31 | Issue 84: AI/LLM efficiency assessment + web_search tool bump |
| 8823 | 05-29 | Issue 69 (Batch 5): prompt-cache split + web_search extraction |
| 8556 | 05-30 | Issue 78b: clip-scorer prompt caching (1h TTL) + stable-first ordering |
| 5407 | 06-16 | Issue 138: SEV1 bulk sweep (cache-floor correction + SDK bump) |
| 5383 | 06-16 | Issue 140: remove inert cache marker on analyze-performer |
| 4105 | 06-23 | Issue 223: DNA-build cache marker removed; cross-call sharing infeasible |
| 4027 | 06-23 | Prompt caching re-enabled on titles/thumbnails/analysis; floor correction (218) |
| 2837 | 06-24 | **Issue 315: prompt-cache floor is 1024 for Sonnet 4.6 (supersedes ALL 2048 refs)** |
| 4059 | 06-23 | **Model-per-task assignment locked; stale Opus reference corrected (221)** |
| 11962 | 06-26 | **Issues 318–321: LLM Features & Hardening — model registry, live-API E2E harness, SDK conformance test, usage-ledger coverage guard** |
| 2507 | 06-26 | New build track: LLM production-standards hardening + verified E2E + new creator features |
| 2004 | 06-29 | **Structured outputs vs robust extraction for JSON LLM responses (342)** |
| 2049 | 06-29 | Live-in-isolation smoke harness: flag-gated synthetic canary (341) |
| 4082 | 06-23 | Usage cost ledger wired to all LLM callers; `cost_estimate` column (220) |
| 3007 | 06-24 | LLM cost ledger now prices cached tokens (read 0.1×, write 1.25×/2×) |
| 3743 | 06-23 | Issue 289: extended price book in config.py (cost-ledger completeness) |
| 12618 | 07-20 | Chat billing: unknown-model fallback moved from Sonnet to Opus rates |
| 4323 | 06-23 | Issue 237: LLM token Prometheus Counter label schema (provider/model/kind) |
| 3809 | 06-23 | **Issue 225: `<untrusted_content_policy>` clause in every system prompt** |
| 4359 | 06-23 | **Issue 224: trust-boundary hardening — untrusted content moved out of the `system` role** |
| 1934 | 07-01 | Issue 350: improvement-brief `pause_turn` loop + `max_uses=5` on web_search |
| 5099 | 06-17 | Issue 152: Pro chatbot — gate model, agentic streaming loop, margin guards |
| 3036 | 06-24 | Issue 96: chat-driven onboarding intake (non-streaming; validate-then-confirm) |
| 62 | 08-14 | **LLM surfaces omit the DNA block and swap the disclaimer when ungrounded, rather than injecting a placeholder** |

## A4. Clip engine — candidates, scoring, merge, render, reframe, captions (37)

| Line | Date | Decision |
|---|---|---|
| 10332 | 05-26 | Clip engine: extend `end_s` for early-peak candidates |
| 9105 | 05-29 | Issue 59: render from `setup_start_s` + ffmpeg accurate-seek finding |
| 11007 | 06-07 | Issue 127: sentence-boundary cuts + context-aware scoring |
| 6614 | 06-01 | Issue 120: per-type DNA candidate caps (longs 50, shorts 75) |
| 10979 | 06-02 | Issue 124: virality score formula + tooltip component |
| 6123 | 06-07 | Issue 134: filler-word + silence removal (two-tier lexicon, 800ms/150ms, single-pass filter_complex, 5ms afade, side-by-side `cleaned_render_uri`) |
| 6032 | 06-07 | Issue 135: text-based transcript editor (6 sub-decisions incl. 5s/85% caps, 0.04s floor) |
| 6243 | 06-07 | Issue 133: animated caption styles (pysubs2+libass, ASS BGR byte order, MarginV=290) |
| 4631 | 06-22 | Issue 183: keyword-highlight captions via dependency-free per-phrase scorer |
| 4664 | 06-22 | Issue 181: two-pass `loudnorm` (deviation from single-pass finding) |
| 4603 | 06-22 | Issue 184: auto-zoom punch-in via crop's per-frame `t` (not zoompan) |
| 4574 | 06-22 | Issue 185: opt-in noise reduction via `afftdn` (not `arnndn`) |
| 10609 | 05-28 | Issue 42: ffmpeg/subprocess timeout formula |
| 11794 | 06-23 | **Issue 189: per-frame active-speaker reframe — BUILD vs BUY** (BlazeFace + sendcmd; AutoFlip EOL; hosted APIs rejected on cost/ToS/latency) |
| 877 | 08-05 | `ACTIVE_SPEAKER_REFRAME_ENABLED` flipped **ON in prod** (422 / 189 reversal) |
| 921 | 08-05 | **Shorts clip-quality wave (427–430 + Opus upgrade + wider pool + 422 unblock)** — 10 rulings incl. sentence-snap pass, 90s clamp, IoMin≥0.8 dedup, Opus 5 on the clip chain, 12 clips/top-8 auto-render, caption band at 70% |
| 844 | 08-05 | Issue 433: region-aware reframe (chrome removal composed with speaker cuts) |
| 785 | 08-05 | Fresh-upload review wave (434–436 + camera-region floor) |
| 817 | 08-05 | Issue 431: append-mode regeneration ("Generate more clips") |
| 753 | 08-07 | Issue 439: vertical-overlap union rule, and NO height ceiling |
| 709 | 08-07 | Issue 440: face_pan holds seats; concurrency separates a two-shot from a move |
| 648 | 08-07 | Issue 441: seconds not ratios for overlap; narrow closed list for openers |
| 573 | 08-10 | Issue 443: video-level camera region is a per-window consensus judged by IoU |
| 443 | 08-11 | Issue 448: separate transient overlay-band pass, blurred not boxed, failing open |
| 395 | 08-12 | Issue 450: use the speaker mapping for the seat; split-screen REVERSED |
| 349 | 08-12 | Issue 445: the shortlist becomes ORDERING, not a filter |
| 3638 | 06-23 | Issue 217: "What's NOT clipped and why" — `skip_reason` design |
| 6355 | 06-07 | Issue 132 (YouTube Live Chat spike detection): DEFERRED, blocked on API availability |
| 1015 | 08-04 | L26 Track A build (414–417): three small build-time deviations (video-context pass, hybrid merge, batched clip metadata) |
| 2100 | 06-29 | Auto-render downloads the source ONCE per video (batch render task) |
| 2188 | 06-28 | **Clips auto-render on generation (upload = consent to spend)** |
| 9729 | 05-28 | Issue 46: generate-clips retry safety + outcomes 30-day floor |
| 12450 | 07-20 | Issue 359: stale-render staleness signal is a Redis render-start marker, not `updated_at` |
| 6409 | 06-07 | Issues 130 & 131: hook analyzer + auto chapter markers |
| 6494 | 06-07 | Issue 129: thumbnail concept generator (Claude multimodal over CV pipeline; 24h Redis cache; ephemeral results) |
| 6545 | 06-07 | Issue 128: title optimizer (ephemeral, generate 10 surface 5, CTR label-only, sync+to_thread) |
| 11743 | — | Issue 196: scheduled publish design decisions |

## A5. Preference model / personalization / ML (9)

| Line | Date | Decision |
|---|---|---|
| 9066 | 05-29 | **Issue 60: wire the personalization loop + maturity-gated blend** (w=0 below threshold, linear ramp to `PREFERENCE_WEIGHT_CAP`) |
| 8597 | 05-30 | Issue 78a: per-(creator, version) preference-scorer cache |
| 8770 | 05-29 | Issue 71 (Batch 7): preference hardening |
| 10656 | 05-28 | Issue 41: replace pickle with joblib + restricted unpickler allowlist |
| 6865 | 05-31 | Issue 102: keep joblib `NumpyUnpickler` module-global swap; offload via `asyncio.to_thread` |
| 2274 | 06-27 | **Personalization efficacy: offline ranking-eval methodology + recency-decay parameterization (198, 200)** |
| 2227 | 06-27 | `performed_well` baseline unit + impression/position log (201, 202) |
| 11515 | — | Issue 187: style becomes a learned Creator-DNA dimension |
| 3980 | 06-23 | Issue 216: honest personalization-status surface — envelope placement + copy |

## A6. Security, privacy, compliance, legal (22)

| Line | Date | Decision |
|---|---|---|
| 10405 | 05-28 | Issue 44: auth boundary hardening |
| 10799 | 05-28 | Issue 36: OAuth token lifecycle hardening (SEV-1) |
| 10862 | 05-28 | Issue 45: concurrent token refresh lock + Redis pool singleton |
| 12634 | 07-20 | OAuth stored scope: replace-on-grant (reverses Issue 352 Batch D union) |
| 4515 | 06-22 | Issue 194: publish scope via incremental consent (opt-in only) |
| 6792 | 05-31 | Issue 106: JWT verify `leeway=60s`; override /assess recommendation of 300s |
| 3906 | 06-23 | Issue 230: CSRF defence — Fetch-Metadata (`Sec-Fetch-Site`) over double-submit |
| 3845 | 06-23 | Issue 226: retire legacy static HTML pages (XSS attack-surface removal) |
| 4162 | 06-23 | Issue 233: formatter-level redaction backstop |
| 2138 | 06-29 | Full-content verbose logging sink (pre-prod debugging, hard-gated off in prod) |
| 11602 | 06-23 | Issue 281: Sentry/GlitchTip — lazy import, `send_default_pii=False`, `before_send` scrub |
| 3373 | 06-23 | Issue 299: enforceable clickwrap ToS/Privacy acceptance + versioned consent record |
| 3324 | 06-23 | Issue 300: COPPA 13+ minimum-age gate + age-neutral screening |
| 3714 | 06-23 | Issue 252: Privacy Policy rewrite — deferred decisions |
| 4481 | 06-22 | Issue 249 [SEV1]: data-export endpoint (GDPR Art. 15/20) — format + scope |
| 4548 | 06-22 | Issue 247 [SEV1]: deletion audit log must not retain erased PII |
| 5330 | 06-16 | **Issue 139: linked-video visibility + the yt-dlp ToS decision** |
| 8739 | 05-29 | Batch 8 (73+74+75): input/memory/config hardening |
| 8690 | 05-29 | Issue 75a: pip-audit CVE remediation (14 → 0) |
| 10924 | 05-31 | Issue 107: pip-audit triage + Layer-0 re-baseline |
| 4258 | 06-23 | Issue 75 assessment-module reconciliation + starlette CVE closure |
| 10766 | 05-28 | Issue 40: streaming upload — chunk size + RSS assertion bound |

## A7. Billing / money (13)

| Line | Date | Decision |
|---|---|---|
| 10295 | 05-26 | **Billing: minute packs (replaces subscription tiers)** |
| 10534 | 05-28 | Issue 34: per-video idempotency for minute deduction (SAVEPOINT + UNIQUE) |
| 9596 | 05-28 | Issue 57: automatic refund on terminal ingest failure |
| 3875 | 06-23 | Issue 209: keep per-input-minute billing, add Stream pack, taper rationale |
| 4141 | 06-23 | Issue 208: money-refund policy — discretionary, ledger-append-only, no admin endpoint at launch |
| 4238 | 06-23 | Issue 207: Stripe Tax — flag-guarded, off by default until first tax registration |
| 5526 | 06-08 | Issue 126: trial UX + billing clarity |
| 5598 | 06-08 | Issue 125: video control model + minutes transparency |
| 1893 | 07-01 | Stripe v8: `max_network_retries` must go to `StripeClient()` (345) |
| 1871 | 07-01 | Rung-1/2 verification blockers: billing idempotency, `pause_turn`, test hardening |
| 223 | 08-12 | Issue 453: restore Stripe's sync transport rather than rewrite two money paths as async |
| 277 | 08-12 | Issue 455: drop `HTTPXClient` entirely (supersedes 453) |
| 110 | 08-14 | **Descope the separate Stripe account; bill everything under Ludwick Solutions LLC** |
| 2599 | 06-24 | Issue 228: per-creator daily LLM/render quota via STACKED slowapi limits |

## A8. Product / UX / frontend (48)

| Line | Date | Decision |
|---|---|---|
| 5042 | 06-18 | **Issue 85: full UI/UX overhaul to React — foundation (85a) + design system** |
| 5008 / 4964 / 4923 / 4879 / 4838 / 4794 | 06-18 | 85b pre-auth pages · 85c Dashboard · 85d Onboarding · 85e Insights+Analysis · 85f Review/Editor · **85g cutover `/` → SPA** |
| 11366 | 06-19 | Issue 162: Playwright E2E + visual harness for the SPA |
| 4744 | 06-19 | Issue 164: live-site Playwright audit (real backend + real auth) |
| 4767 | 06-19 | Issue 165: WCAG AA contrast — token retune + tailwind-merge root-cause fix |
| 11256 / 11300 / 11335 | 06-19 | UI polish passes 1–3 (index.css ↔ UI.md; deferred tokens + fit-tier thresholds; dark-mode elevation) |
| 3221 | 06-23 | AutoClip UI redesign + Chip mascot (304–309): scope + foundation |
| 2887 | 06-24 | AutoClip redesign fidelity polish: 10 prototype gaps |
| 1044 | 08-04 | Issue 413: app typeface is now Lexend |
| 1700 | 08-03 | **Issue 384: icon system — one swappable seam + a source-scanning gate + the glyph ruling** |
| 1621 | 08-03 | Issue 385: six UI primitives, not seven — two traps in swapping native controls for Radix |
| 1547 | 08-03 | Issue 400a: elevation highlight never composed; seven colour tokens never existed |
| 1369 | 08-03 | Issue 389: tool routes get their own chrome; why the disclaimer moved |
| 1226 | 08-03 | **Issue 390: Timeline v2 — the pixel is the unit** |
| 1304 | 08-03 | Issue 392: BBC's waveform FORMAT without BBC's waveform BINARY |
| 1437 | 08-03 | Issue 387: poster frames — three calls that read as inconsistent and are not |
| 1068 | 08-04 | Batch A/B close-out wave (407–411): four decisions |
| 1103 | 08-04 | Retroactive entries for 386, 388, 400b |
| 323 | 08-12 | Issue 452: the focused review view expands to fit, and drops the tooltip |
| 3429 | 06-23 | Issue 188: Timeline + waveform Editor surface (the backbone) |
| 3598 | 06-23 | Issue 212: Insights page rebuild — IA + scope boundary |
| 3687 | 06-23 | Issue 213: per-video clips map — timeline UI + batched counts endpoint |
| 3567 | 06-23 | Issue 227: description clamp is defensive/future-proofing |
| 2794 | 06-24 | **Issue 317: "Link a video" retired as the primary entry point in favour of upload** |
| 2971 | 06-24 | `POST /videos/link` adopts a catalog row instead of 409 |
| 2645 | 06-24 | Issue 310: synced-channel catalog browser |
| 3081 | 06-24 | Issue 100: new creators see the walkthrough FIRST |
| 3112 | 06-23 | Issue 204: identity intake is genuinely OPTIONAL before DNA build (reverses 100) |
| 8253 | 05-30 | **Issue 83: Creator Intake Form (stated identity layer)** |
| 5450 | 06-08 | Onboarding state aggregation on `/auth/me` + `/creators/me` |
| 5493 | 06-08 | Empty-state response envelopes on list endpoints (BFF posture) |
| 5659 / 5755 / 5800 / 5854 | 06-07/08 | Issue 136/137 legacy UI overhaul series (dark editor, hero, tool rail, overflow fix) |
| 5149 | 06-17 | Issue 147: UI/UX cohesion — shared component layer |
| 7108 | 05-31 | Wave 7: pricing.html CSS hotfix |
| 6939 | 05-31 | Issue 99: Linear-style base + monospace data register |
| 11500 | — | Issue 211: global active-tasks panel — plain ES-module singleton store over Zustand |
| 11199 | 06-18 | Descope: cross-page active-tasks panel split from 156 → 160 |
| 11222 | 06-18 | Issue 159 triage: orphaned endpoints retained; stale envelope URLs → 161 |
| 8138 | 05-30 | **Issue 86: live progress surface (SSE + Redis Streams)** |
| 7639 | 05-31 | Issue 92: universal progress visibility (extends 86) |
| 3474 | 06-23 | Issue 235: activation event definition + funnel taxonomy |
| 2741 | 06-24 | Issues 245+246: notification center / unsubscribe / lifecycle email sequence |
| 11682 | 06-23 | Issue 243: notification data model + idempotent send task |
| 3776 | 06-23 | Issue 244: notification trigger wiring — entity_id conventions + fire points |
| 3933 | 06-23 | Issue 242: transactional email provider (Resend) + Jinja2 + console dev sink |
| 1922 | 07-01 | Issue 349: notification mailer call moved outside the DB session with asyncio timeout |
| 7028 | 05-31 | Issue 95: companion app + folder watcher (Medal.tv pattern) |
| 12667 / 12741 | 07-29 | Ready-pass Wave 1 (applied metadata, trim re-render, publish UI, expired-source UX) / Wave 2 (hardening + cleanup tail) |

## A9. Worker, concurrency, idempotency, resilience (16)

| Line | Date | Decision |
|---|---|---|
| 10020 | 05-28 | **Issue 39: Celery event-loop strategy** |
| 9028 | 05-29 | Batch 1 (61+62): worker at-least-once safety |
| 8920 | 05-29 | Batch 4a (66+67): blocking calls off the API event loop |
| 8893 | 05-29 | Issue 68: worker-loop offload + transcription timeout |
| 8863 | 05-29 | Issue 72: shared YouTube HTTP client + 5xx backoff |
| 8800 | 05-29 | Issue 70: bound `poll_clip_outcomes` |
| 10735 | 05-28 | Issue 35: idempotent DNA build (SEV-0) |
| 9835 | 05-28 | Issue 47: beat-job fairness via `last_analytics_refreshed_at` |
| 9925 | 05-28 | Issue 43: source-media retention clock = ingest completion, not upload |
| 6717 | 06-01 | Issue 110: `SELECT FOR UPDATE SKIP LOCKED` debounce + capture-then-delete-after-commit |
| 8509 | 05-30 | Issue 78d: improvement-brief → 202 + poll (async Celery) |
| 4446 | 06-22 | Issue 195: idempotent `publish_to_youtube` + `videos.insert` quota re-verified |
| 11896 | — | Issue 260: YouTube Data API quota at scale — per-creator fairness sub-budget + ETag/304 caching |
| 8036 | 05-30 | Issue 87: catalog sync wiring + 180s Shorts threshold |
| 12130 | 06-30 | L21 edge-case hardening wave (329–340) |
| 12509 | 07-20 | Assessment fix batch (backend-misc) judgment calls |

## A10. Observability, testing, quality gates (18)

| Line | Date | Decision |
|---|---|---|
| 8635 | 05-29 | Issue 75f: observability — correlation ids + structured logs + metrics |
| 7906 | 05-30 | Issue 88: DNA filter parity + business-event observability + display-vs-filter audit |
| 11546 | 06-23 | Issue 238: app-level saturation gauges — reuse singletons, no per-scrape connection |
| 11407 | 06-23 | **Issue 265: eval gate — required commit-status pattern for `clip_engine/` CI enforcement** |
| 11427 | — | Issue 267: test isolation via pytest-randomly |
| 11444 | — | Issue 269: diff/patch-coverage gate + per-module floors |
| 11580 | — | Issue 268: flake detection + quarantine signal |
| 11638 | — | Issue 272: visual-regression baselines on stable routes |
| 2673 | 06-24 | Issue 273: mutation-testing scope = load-bearing core only, REPORT-only, weekly |
| 8462 | 05-30 | Issue 78c: mypy 30 → 0 + ratchet enabled |
| 5193 | 06-17 | Issue 146: docs consolidation + searchable index |
| 4412 | 06-22 | `docs/issues.md` rebuilt into the Master Roadmap to Production |
| 1768 | 08-03 | Issue tracker reset: `issues.md` archived and rebuilt around Lane L25 |
| 4699 | 06-22 | Gap-closure backlog rebuild + four v1 scope decisions |
| 12227 / 12326 / 12354 / 12409 | 07-02 | W1 wave scope + five research-resolved decisions · W1 rounds 2–3 · W2 wave (six) · W3 wave (five) |
| 8371 | 05-30 | Reconcile merge: local-main hardening + origin Issue 78 salvage |
| 6838 | 05-31 | Issue 103: six Wave-9 carry-forward fixes |
| 7289 / 7449 / 7516 / 7581 / 7762 / 5951 | 05-31 / 06-07 | Wave 6 "done-vs-visible" · Wave 5 SEV1+cross-tab persistence · Wave 4 compliance+scale · Wave 3 hotfix · Wave 1 hotfix · post-Issue-135 audit fixes (6 SEV1s) |

*(6655 — 06-01 "Issues 113–119 UX wave" spans A2/A4/A8 and is counted once in A4's parent context.)*

---

# B) HONEST ARCHITECTURE MAP

## B1. The real layer diagram

```
Browser (React 19 SPA, /app/*)
  │  fetch + cookie session  ·  SSE (/tasks/{key}/events)
  ▼
FastAPI (main.py, 24 routers, 98 endpoints)
  │  routers import models + SQLAlchemy select DIRECTLY (21/24 do)
  │  routers ALSO import domain packages directly (billing, dna, knowledge,
  │  clip_engine, youtube, preference)
  ▼
[ NO SERVICE LAYER ]  ← there is no services/ package; the layer is absent by
                        design, not accident
  ▼
Domain packages: clip_engine/ dna/ knowledge/ preference/ billing/ chat/
                 ingestion/ youtube/ analysis/ improvement/ notify/
  ▲
  │
Celery worker (worker/tasks.py — 7,179 lines, ~40 tasks + their `_async` bodies)
  │  each task = thin sync shell → `asyncio.run(_x_async(...))` → business logic
  ▼
Postgres 16 + pgvector (39 tables, 27 RLS tenant_isolation policies, 62 migrations)
Redis 7 (Celery broker + RedBeat + progress Streams + slowapi + flag/scorer caches)
Cloudflare R2 (source, clips, posters, peaks)
```

## B2. The seams that actually exist (and hold)

These are the deliberate, well-defended boundaries — the good news:

1. **`routers/_owned.py`** — a single generic `get_owned(session, Model, id, creator_id)` that collapses fetch+ownership into one query and returns **404 for both missing and foreign**. Small, correct, and explicitly documented as defense-in-depth *under* RLS.
2. **`routers/_enqueue.py`** — the one enqueue+SSE-ownership seam behind all 19 enqueue endpoints, with an explicit fail-open posture on Redis.
3. **`db.py` `tenant_session(creator_id)`** — the worker's counterpart to the request-path auth dependency. The `creator_id` is a *required argument*, so a call site structurally cannot forget the GUC. Cross-tenant sweeps must use `AdminSessionLocal`, and that allowlist is **pinned by a test** (`tests/test_worker_invariants.py`). This is the single best piece of architecture in the repo.
4. **`db.py` `after_begin` listener** — RLS GUC injection registered on the `Session` class, discriminated by `session.info["creator_id"]`. One mechanism, both factories.
5. **`clip_engine/` internal seams are real**: `candidates.py` (pure peak detection) → `sentence_snap.py` (pure) → `merge.py` (pure NMS/IoU) → `scoring.py` (the only LLM call) → `ranking.py` (blend + persist) → `render.py` (ffmpeg). `speaker_map.py` is documented as **PURE**; `shots.py`, `camera_region.py`, `overlay_bands.py`, `edits.py`, `filler.py` are all pure/testable. The impure surface is concentrated in `render.py` + `reframe.py`.
6. **`knowledge/util.py`** — one `wrap_untrusted()` + one `dna_system_block()` + one `extract_json_block()`, so the injection policy and cache-floor gate are single-sourced across ~10 LLM callers.
7. **Frontend `lib/`** — genuinely well-factored: every hard piece of math (`timelineZoom`, `timelineInteraction`, `editorCuts`, `editCommands`, `saveScheduler`, `peaks`, `cropTrack`, `fit`, `safeUrl`) is a pure module with a colocated `.test.ts`. React components are thin over these.

## B3. Coupling hotspots (ranked)

**1. `worker/tasks.py` (7,179 lines) is the de-facto service layer.**
~40 Celery tasks, each a 10-line sync shell, plus **~60 `_async` functions that contain essentially all cross-cutting business logic**: ingest orchestration, DNA build, clip generation and persistence, render planning (`_ClipRenderPlan`, `_load_clip_render_plan`), clean/edit/trim swaps, publish-to-YouTube, Stripe reconciliation, lifecycle email scanning, data export collection, five distinct LLM feature pipelines (analysis / titles / thumbnails / hooks / chapters), the chat turn driver, notification building, storage gauges, and six backfill/purge sweeps. This file imports from nearly every domain package. **This is where layering is violated most.** The right decomposition is obvious from the function names (`worker/pipeline/`, `worker/render/`, `worker/sweeps/`, `worker/llm_features/`), and nothing about the Celery contract prevents it — the task shells could stay in `tasks.py` and delegate.

**2. `routers/clips.py` (2,893 lines) and `routers/videos.py` (1,479).**
Routers own DB queries, ownership checks, billing pre-checks (`check_balance_for_minutes`, `require_budget`), domain calls (`clip_origin_s`, `is_shortlisted`, `probe_duration_s`, `classify_video_kind`), transaction management, and response shaping. `routers/insights.py` (1,080) goes further and instantiates **its own Anthropic client at line 764** and builds prompts inline — an LLM call living in the HTTP layer. That is the clearest single layering violation in the router tier.

**3. `config.py` (1,208 lines) is a god-object.**
One `Settings` class with ~29 labeled sections (reframe planner, virtual tripod, caption placement, camera region, overlay bands, originality guard, spend guard, lifecycle email, OTel, Sentry, Stripe, consent versions, verbose logging…). Every tuning constant for the clip engine is here rather than beside the algorithm. This makes `config.py` a change-magnet touched by nearly every feature and creates an import-time coupling from *everything* to *everything*.

**4. No central LLM client module.**
`SOT.md` states this explicitly ("there is no central clients.py") and calls it the Issue-37 lifecycle rule. The consequence: **17 separate `AsyncAnthropic(...)` module-level singletons**, each independently specifying `timeout` and `max_retries`. I verified drift already exists — `clip_engine/scoring.py` and `dna/brief.py` use `httpx.Timeout(60.0)`, `chat/runner.py` and `knowledge/titles.py` use `120.0`. The `tests/test_llm_conformance.py` + `test_sdk_timeouts.py` guards are what keep this from rotting further; they're doing the job an abstraction would do.

**5. Frontend type-sharing is hand-written.**
`frontend/src/types.ts` is **814 hand-maintained lines** mirroring Pydantic response models. There is **no OpenAPI codegen** (`openapi-typescript`, `orval`, `hey-api` — none present in `package.json`). The API client (`lib/api.ts`) is a well-built typed `fetch` wrapper, but `api<T>()` casts blindly: `T` is asserted, never validated. A backend field rename compiles clean on both sides and fails at runtime.

## B4. Where the code diverges from `docs/SOT.md`

| # | SOT claim | Reality | Severity |
|---|---|---|---|
| 1 | *"**no Opus** — see DECISIONS (Issue 221)"* (SOT line 16) | `config.py` sets `ANTHROPIC_MODEL_SCORING`, `ANTHROPIC_MODEL_VIDEO_CONTEXT`, `ANTHROPIC_MODEL_CLIP_METADATA` = **`claude-opus-5`** (DECISIONS 2026-08-05 §6, ~2× cost). SOT was never updated. | **High** — the SOT's stated cost/model posture is wrong on the three most expensive calls |
| 2 | `TRANSCRIPTION_BACKEND` env table says default `whisperx` (line 52) | Tech-stack table on line 18 says Deepgram is default; kickoff decision says Deepgram. The env table contradicts the same file two rows up. | Medium (internal contradiction) |
| 3 | Beta hosting = Render managed PaaS (2 DECISIONS entries, 06-24) | Live prod is **DigitalOcean VM + docker-compose + cloudflared**, confirmed by the 06-27 entry ("the live app does not run on Render — Render Postgres is empty and was never used") and `LEFT_OFF.md`. `render.yaml` (9.2 KB) is still at repo root as a live-looking artifact. | **High** — a superseded decision that was never marked superseded; the reversal is buried inside an observability entry |
| 4 | *"Production deployment: Kubernetes — chart written, GKE deploy unvalidated"* | Accurate, and honestly stated. But the DECISIONS entries that justify pool sizing, PgBouncer sidecar, KEDA, connection budgets (259/263/264/58) are all **unverified assumptions** that still gate `db.py`'s pool math (`_POOL_SIZE=15`, `prepare_threshold=None`) in a deployment that has no PgBouncer. | Medium — live config carries K8s-shaped constraints for no live benefit |
| 5 | RLS: SOT §Security says isolation is *"enforced at the query layer"* | Understates it. Reality is stronger: 27 `tenant_isolation` policies, `FORCE ROW LEVEL SECURITY`, NULLIF-hardened GUC (migration 0045), app role without BYPASSRLS activated in prod 06-30. But the SOT does not mention that **`creators` and `audit_log` are RLS-exempt**, nor that the *full* role split (ownership transfer to `creatorclip_migrate`) was **not** done — `DATABASE_MIGRATION_URL` still points at the superuser. | Medium — the exemption + partial split is load-bearing and belongs in SOT |
| 6 | File structure block lists `clip_engine/` with 15 files | Matches (17 incl. `__init__` + `summary_select.py`, which SOT omits). `routers/` list omits `api_keys.py`, `export.py`, `video_review.py`, `_schemas.py`, `_enqueue.py`, `_owned.py`. | Low |
| 7 | `tests/` block lists ~18 test files | Reality: **~250 test modules** + `tests/perf`, `tests/fixtures`, `tests/preference`, `tests/ingestion`, `tests/scripts`. SOT's test map is ~7% complete. | Low but misleading |
| 8 | Data model section lists tables inline in SQL-ish prose | 39 tables in `models.py`; SOT documents ~35 and is missing `creator_api_keys`, `clip_impressions`, `summaries`, `feature_flags`, `data_exports`. | Medium |
| 9 | SOT "Processing Pipeline" ASCII diagram | Missing the two newest stages that DECISIONS added: **video-context (L26/415)** and **batched clip-metadata (417)**. The prose note above the diagram mentions them; the diagram itself is stale. | Low |

## B5. Data-model observations

- **39 tables, 24 `relationship()` declarations, 62 migrations, 27 RLS policies.** Migration cadence is ~1 per 1.5 days of project life — high but each is small and Squawk-linted with lock/statement timeouts (Issue 270).
- **36 JSONB columns.** Several are doing relational work: `signals.timeline_jsonb`, `transcripts.segments_jsonb` (word-level — this is the largest payload in the system and is read whole), `clips.signals_jsonb`, `clips.reframe_track_jsonb`, `clips.pending/effective_geometry_jsonb`, `creator_dna.patterns_jsonb`, `demographics.payload_jsonb`, `video_context.context_jsonb`. The reasoning is recorded in places (`clip_edit_documents` has an excellent inline rationale for why it is *not* a JSONB column on `clips` — TOAST detoast cost on list pages), which suggests the JSONB choices are deliberate rather than lazy. But **there is no recorded decision on JSONB schema versioning/migration** beyond ad-hoc `version` keys.
- **Index discipline is thin in `models.py`** (13 `index=True`, 4 explicit `Index(`) but 22 migrations create indexes — so indexes live in migrations, drifting from the model definitions. That's a known smell: `alembic --autogenerate` diffs will be noisy.
- **Denormalization is deliberate and documented**: `clip_edit_documents.creator_id` is denormalized "so RLS is a direct-column policy (house pattern)". Good — the pattern has a name and a reason.
- **`clips` is the hot table** — it now carries `score`, `blended_score`, `rank`, `triage`, `shortlisted`-adjacent logic, `render_status`, `cleaned_render_uri`, `downloaded_at`, `applied_*`, `suggested_*`, `poster_uri`, `style_preset`, `reframe_track_jsonb`, `pending_geometry_jsonb`, `effective_geometry_jsonb`, `signals_jsonb`. That is **four distinct lifecycles** (engine output, creator verdict, render pipeline state, delivery metadata) in one row. `list_clips` selects all columns for up to 100 rows.

## B6. The clip engine — quality measurement, honestly

Pipeline: `candidates.extract_candidates` (peak detect + backward 75s look) → `sentence_snap` (post-extraction edge snap, the single snapping authority) → `merge.merge_candidates` (LLM moments ∪ signal peaks under signal-priority NMS) → `scoring.score_candidates` (one Opus 5 call over ≤18 candidates, json_schema output) → `ranking.rank_candidates` → `suppress_contained` (IoMin ≥ 0.8) → `rerank_with_preference` (maturity-gated blend) → `persist_ranked_clips` → `render`.

**What `tests/eval/` actually asserts — this matters and is easy to over-read:**

- `tests/eval/scenarios/*.yaml` are **~38 hand-written synthetic signal timelines**, not real video. Each supplies a `timeline` of `{silence, energy_spike, retention_spike, laughter}` events and asserts **geometry**: `peak_s_min/max`, `setup_start_s_max`, `min_candidates`.
- The invariant they defend is one thing, defended well: **"clip the setup, not the aftermath."** `aftermath_louder_than_setup.yaml` is the canonical adversarial case.
- CI enforcement (Issue 265) is genuinely sophisticated: a **commit status** (not a required job) because GitHub reports skipped required jobs as success. Plus `test_eval_scenario_count_floor` (a ratcheted floor, raised 6→14→15→18→…) and `test_eval_scenario_no_unapproved_skip_markers` so scenarios can't be deleted or `@skip`-ed away.
- **What it does NOT measure:** nothing in `tests/eval/` touches the LLM, real audio, real transcripts, or human judgment of whether a clip is *good*. There is no human-labeled clip corpus, no inter-rater agreement, no A/B of engine versions against creator keep-rate.
- The **LLM output quality** layer is separate and is stronger than most projects: `tests/test_scoring_goldens.py` replays **real recorded Anthropic response bodies** (recorded by `scripts/record_scoring_goldens.py`, deserialized back through `anthropic.types.Message` so SDK drift is caught) including a **real `stop_reason="max_tokens"` truncation golden**, with a sha256 pin on `_OUTPUT_SCHEMA` that forces re-recording when the schema changes. That is a genuinely current-standard eval pattern.
- The **ranking-quality** layer is `preference/efficacy.py` (509 lines): nDCG@k, MAP@k, MRR, Kendall tau, chronological split, paired bootstrap delta with CI. Real offline ranking eval methodology (DECISIONS 2026-06-27, Issues 198/200/201/202). It measures the *reranker*, not the clip engine.

**The ML / heuristic boundary** is at `clip_engine/ranking.rerank_with_preference` ← `preference/model.PreferenceScorer`. LogisticRegression below `PERSONALIZATION_THRESHOLD_LABELS`, LightGBM at or above; blend `(1-w)*score + w*pref` with `w=0` under threshold ramping linearly to `PREFERENCE_WEIGHT_CAP=0.5` at 2× threshold. `clips.score` is now guaranteed immutable (the DNA/LLM composite), `clips.blended_score` holds the reranked value (migration 0059) — a clean separation added late. Serialization is joblib behind a `_RestrictedUnpickler` allowlist with a process-global swap under a `threading.Lock` — the RCE surface on `preference_models.weights_blob` is explicitly closed and reasoned about (Issues 41/71/102).

## B7. LLM layer

- **17 module-level `AsyncAnthropic` singletons**, each with explicit `timeout=httpx.Timeout(60|120, connect=10)` + `max_retries=2`. Enforced by `tests/test_llm_conformance.py` and `test_sdk_timeouts.py`.
- **Model registry**: 20 `ANTHROPIC_MODEL_<TASK>` settings in `config.py`, with `tests/test_model_config.py` banning hardcoded model literals. Sonnet 4.6 for reasoning, Haiku 4.5 for classify, **Opus 5 for the three clip-quality calls**.
- **Prompt caching**: two-block system prompts (static corpus, then per-creator DNA brief), `cache_control ephemeral ttl:1h` on the DNA block — but **only when a measured token count clears the model's floor** (`knowledge/util.dna_system_block`). Inert cache markers were actively hunted and removed (Issues 138/140/218/223). The cost ledger prices cache reads at 0.1× and 1h writes at 2×.
- **Structured output**: per-site choice — native `output_config` json_schema where no web_search, `extract_json_block` where citations are on (structured output 400s with citations). Both paths route through `extract_json_block` as defense-in-depth.
- **Cost accounting**: every LLM call funnels through `billing.ledger.record_llm_usage`, which is simultaneously the ledger write, the Prometheus `llm_cost_usd_total` counter, and the spend-guard tap. `tests/test_usage_coverage.py` asserts *every* LLM task calls it. This is a strong single choke point.
- **Prompt safety**: `wrap_untrusted(name, value)` JSON-encodes attacker-influenceable text into an XML-labelled block placed in the **user** role, plus an `<untrusted_content_policy>` clause in every system prompt (Issues 224/225, OWASP LLM01:2025 cited).
- **Streaming**: `worker/anthropic_stream.py` wraps `.stream()` so cache hit/miss surfaces before the first token, `text_delta`→SSE `token`, `thinking_delta`→`thinking`, unknown deltas silently dropped. Each emit is individually try/excepted so a Redis hiccup can't abort before `get_final_message()`.

## B8. Frontend

- React 19.2 · Vite 8 · TS 6 · Tailwind v4 · Radix primitives · TanStack Query v5 · React Router v7 Data Mode (`basename=/app`) · Uppy 5 for direct-to-R2 multipart.
- **State**: TanStack Query is the only server-state layer (one `QueryClient`, `staleTime: 30s`, `retry: 1`, `refetchOnWindowFocus: false` — with the rationale written in the file). Client state is deliberately minimal: **one** ES-module singleton store (`stores/activeTasks.ts`, Zustand explicitly rejected in Issue 211).
- **~17 routes** across five contexts (AuthGate → AppChrome / ToolChrome / bare / public-or-authed / anon).
- **Structural gates in `src/test/sourceScan.ts`** — four source-scanning tests that fail the build on glyph icons, native form controls, `<video controls>`, or undeclared colour tokens. This is an unusual and effective way to make design-system rules non-negotiable.
- **Type sharing is the weak point** (see B3.5).

---

# C) THE ~20 LOAD-BEARING TECHNICAL BETS

Ranked by cost-of-being-wrong. "Rationale?" = is there a recorded decision entry that would survive an audit.

| # | Bet | Implemented in | Rationale recorded? |
|---|---|---|---|
| 1 | **Postgres RLS is the tenant-isolation backstop, with the app role stripped of BYPASSRLS** | `db.py` `after_begin` + `tenant_session`; migrations 0010/0026/0038/0040/0044/**0045**; `routers/_owned.py` | ✅✅ Exceptional — DECISIONS 9453 (adopt, 8 sources, pgbouncer pooling-mode matrix pinned), 9283 (impl), 1961 (prod activation with live before/after evidence). The 0045 NULLIF hardening documents a real empty-string-GUC failure on pooled connections. |
| 2 | **`worker/tasks.py` is the service layer** (7,179 lines, no `services/`) | `worker/tasks.py` | ❌ **Never decided.** No entry defends or even names this. It is the largest structural risk and the only one with zero recorded rationale. |
| 3 | **Celery tasks are sync shells calling `asyncio.run()` on async SQLAlchemy; engines rebuilt post-fork** | `db.py::recreate_engine`, every task in `worker/tasks.py` | ✅ DECISIONS 10278 (kickoff) + 10020 (Issue 39) + re-entry-guard hardening (Issues 123/352). Well-defended. |
| 4 | **Opus 5 on the clip-quality chain (scoring / video-context / clip-metadata)** — ~2× token cost on the highest-volume calls | `config.py:117,141,145` | ✅ DECISIONS 921 §6 (with the deliberate non-lowering of the 1024 cache floor explained). ⚠️ **But `SOT.md` still says "no Opus"** — the SOT is actively wrong here. |
| 5 | **Auto-render top-8 on generation: "upload = consent to spend"** | `AUTO_RENDER_TOP_N`, `render_video_clips` | ✅ DECISIONS 2188 + 2100 (source downloaded once per video) + 921 §7 |
| 6 | **Build, don't buy, active-speaker reframe** (BlazeFace + ffmpeg sendcmd) | `clip_engine/reframe.py` (1,363) + `speaker_map.py` + `shots.py` + `camera_region.py` + `overlay_bands.py` ≈ **3,200 lines** | ✅✅ DECISIONS 11794 — cost/ToS/latency triangulated, AutoFlip-EOL trap documented, 8 sources, flag-gated with a 4-item unlock checklist. Model rationale. ⚠️ But it is now ~38% of the clip engine and 10 of the last 15 decisions are reframe-geometry fixes. |
| 7 | **Prompt caching is floor-gated at measured token counts, not assumed** | `knowledge/util.dna_system_block`, `clip_engine/scoring.py:407` | ✅ DECISIONS 2837 (1024 for Sonnet 4.6, supersedes all 2048 refs) + 4027 + 4105 + 5383 + 5407 |
| 8 | **One choke point for LLM cost: `record_llm_usage`** (ledger + Prometheus + spend guard) | `billing/ledger.py:193`, guarded by `tests/test_usage_coverage.py` | ✅ DECISIONS 4082, 3007, 3743, 12618 |
| 9 | **Per-input-minute prepaid packs, not subscriptions** | `billing/`, `minute_packs`, `minute_deductions` (UNIQUE on video_id) | ✅ DECISIONS 10295 + 3875 (taper) + 10534 (idempotency via SAVEPOINT+UNIQUE) + 9596 (auto-refund on terminal failure) |
| 10 | **Maturity-gated personalization blend; DNA fallback below 20 labels** | `preference/model.preference_weight`, `ranking.rerank_with_preference`, `clips.blended_score` | ✅✅ DECISIONS 9066 (with cold-start literature) + 2274 (offline nDCG/MAP/bootstrap methodology) + 3980 (honest UI surface) |
| 11 | **17 independent Anthropic client singletons, no `clients.py`** | every LLM module | ⚠️ Partial — DECISIONS 10095 (Issue 37 timeouts/retries) covers the *policy*, not the *no-abstraction* choice. `SOT.md:112` asserts it as a rule with no linked decision. Timeout drift (60 vs 120s) already exists. |
| 12 | **`config.py` as one 1,208-line Settings god-object** | `config.py` | ❌ **Never decided.** Grown by accretion, one section per issue. |
| 13 | **Hand-written `types.ts` (814 lines); no OpenAPI codegen** | `frontend/src/types.ts`, `lib/api.ts` | ❌ **Never decided.** No entry evaluates `openapi-typescript`/`orval`. `api<T>()` casts without runtime validation. |
| 14 | **SSE over Redis Streams for live progress (not WebSockets/polling)** | `worker/progress.py`, `routers/tasks.py`, `lib/taskStream.ts`, `hooks/useTaskStream|useTaskResult` | ✅ DECISIONS 8138 + 7639 (universal progress) |
| 15 | **Prod = single DigitalOcean VM + docker-compose + Cloudflare Tunnel; K8s chart written but never run** | `docker-compose.prod.yml`, `deploy/charts/creatorclip/` | ⚠️ Fragmented across **four** entries (10315 VM, 2541 Render, 2563 Render blueprint, 2395 "actually the VM"), two of which are superseded but **not marked superseded**. `render.yaml` still ships. This is the most confusing decision trail in the file. |
| 16 | **Structured outputs where possible, `extract_json_block` where citations forbid it** | `clip_engine/scoring.py`, `knowledge/*`, `knowledge/util.extract_json_block` | ✅✅ DECISIONS 2004 — including an honest logged deviation from the approved brief (scoring/chapters shipped Track B) |
| 17 | **Untrusted content JSON-wrapped into the user role + policy clause in every system prompt** | `knowledge/util.wrap_untrusted`, `tests/test_prompt_safety.py` | ✅ DECISIONS 4359 + 3809, OWASP LLM01:2025 + Anthropic guidance cited |
| 18 | **Clip-quality CI gate as a GitHub commit status over synthetic YAML scenarios with a ratcheted count floor** | `.github/workflows/ci.yml:459`, `tests/test_clip_engine.py`, `tests/eval/scenarios/` | ✅✅ DECISIONS 11407 — the skipped-required-job-reports-success quirk is exactly right |
| 19 | **Preference model serialized as joblib behind a restricted unpickler allowlist** | `preference/model.py` | ✅ DECISIONS 10656 + 8770 + 6865 (with the LabelEncoder-missing incident that silently disabled every mature model) |
| 20 | **Direct browser→R2 presigned multipart upload; API only signs and registers** | `routers/videos.py` `/videos/uploads/*`, `frontend/src/lib/uploader.ts` (Uppy + golden-retriever + SW) | ✅ Issue 395 (referenced in SOT + ready-pass entries); ⚠️ the CORS-policy step is a manual `scripts/r2_set_cors.py` run per environment |
| 21 | **YouTube source is never downloaded (`yt-dlp` off by default); creators upload files** | `youtube/ingest.py`, `origin` discriminator | ✅✅ DECISIONS 5330 (Issue 139, the yt-dlp ToS decision) + 2794 (Issue 317 retiring link-as-primary). Existential-risk bet, properly reasoned. |
| 22 | **Feature flags are DB rows with a 30s TTL cache and hard fail-open** | `flags.py` | ✅ Issue 284 (referenced in `config.py:949`); the fail-open posture is documented in the module docstring |

---

# D) DECISIONS NOT YET MADE — the named gaps

A system of this exact shape normally needs an explicit written position on each of these. I found none.

### D1. Architecture / structure
1. **Module-decomposition policy for `worker/tasks.py`.** No entry says "tasks.py is the service layer and that's fine" or "we will split it at N lines." At 7,179 lines with ~40 tasks it is past every conventional threshold, and the split is mechanically obvious. **This is the #1 undocumented bet.**
2. **Whether a service/domain layer should exist at all.** Routers query the DB, enforce billing, call domain packages, and (in `insights.py`) call Anthropic. A one-paragraph "we deliberately have no service layer because X" would settle it; there isn't one.
3. **`config.py` decomposition.** 29 sections, 1,208 lines, one class. No position on splitting per-domain settings modules or moving algorithm constants next to their algorithms.
4. **Frontend ↔ backend type contract.** No decision on OpenAPI codegen vs. hand-written types, and no runtime validation (zod/valibot) at the API boundary. `SOT.md` describes `types.ts` as "API response shapes" without acknowledging it as a manually-synced duplicate.

### D2. Data
5. **JSONB payload versioning and migration strategy.** 36 JSONB columns, several with ad-hoc `version` keys, no recorded policy for evolving them, no backfill pattern, no "when does a JSONB key graduate to a column."
6. **`clips` table lifecycle split.** Four lifecycles in one row with `list_clips` selecting all columns for 100 rows. There is an excellent recorded rationale for why `clip_edit_documents` is *not* on `clips` (TOAST detoast) — but the same argument now applies to `reframe_track_jsonb`, `signals_jsonb`, and the two geometry columns, and was not revisited.
7. **Index ownership.** Indexes live in migrations (22 files) but not in `models.py` (13 `index=True`). No policy, so `--autogenerate` will keep proposing spurious drops.
8. **Data-retention beyond source media and event logs.** `SOURCE_MEDIA_RETENTION_HOURS=72` and 90-day event logs are decided. Nothing decided for: rendered clips in R2, `transcripts.segments_jsonb`, `dna_embeddings`, `chat_messages`, `clip_impressions`. R2 storage cost is flagged as Issue 293 but has no position.
9. **pgvector at scale.** Issue 56 explicitly deferred the "RLS is evaluated post-index-scan, so cross-tenant embeddings appear in ANN candidates before filtering" problem as "revisit at scale." No revisit entry exists.

### D3. LLM
10. **Model-upgrade / deprecation policy.** 20 pinned model IDs across `config.py`. No recorded position on what happens when Anthropic deprecates a model, how a swap is validated (the scoring goldens would need re-recording — the README exists, the *policy* doesn't), or who owns the regression check.
11. **A prompt-versioning and prompt-regression regime.** `PROMPT_VERSION` was bumped 1→2 in one decision; there is no cross-cutting policy, no prompt registry, no golden set per prompt beyond scoring.
12. **LLM output quality SLO.** There is cost accounting (excellent), latency handling, and truncation handling — but no recorded definition of "the scorer is performing acceptably," no drift monitor, no periodic re-eval cadence. The scoring goldens pin *parsing*, not *judgment quality*.
13. **LLM-as-judge / human eval for clip quality.** `tests/eval/` asserts geometry on synthetic timelines. No human-labeled clip corpus exists, so there is no way to answer "did the Opus 5 upgrade actually make clips better?" — which is precisely what that upgrade claimed to buy.
14. **Fallback when Anthropic is degraded.** Timeouts and `max_retries=2` are set per client. No recorded decision on circuit-breaking, cross-model fallback, or graceful degradation of the pipeline when the LLM is down for an hour.

### D4. Operations / scale
15. **The deployment position needs one authoritative entry.** Four entries, two silently superseded, `render.yaml` still in the repo, a Helm chart that has never run, and `db.py` pool math tuned for a PgBouncer that isn't deployed. A single "Deployment: current state and why" entry marking 2541/2563 SUPERSEDED would close it.
16. **Backpressure / queue-depth policy.** Celery `--concurrency=2` on one VM, auto-render fanning out 8 renders per upload, ffmpeg + MediaPipe both CPU-bound. No decision on queue priority, per-creator concurrency caps, or what happens when 10 creators upload simultaneously. KEDA was the answer — and KEDA was descoped.
17. **Single-VM failure domain / RTO-RPO.** DR entries cover backups, key escrow, and R2 immutability (255–258). Nothing states the recovery-time objective or what the plan is when the one droplet dies.
18. **Idempotency/exactly-once policy as a stated pattern.** It is implemented superbly and repeatedly (UNIQUE keys, advisory locks, SAVEPOINT, CAS revisions, dedupe keys, `SKIP LOCKED`) across ~10 separate entries — but there is no single entry naming the house pattern, so each new task re-derives it.
19. **Frontend performance budget.** No decision on bundle size, code-splitting, or route-level lazy loading, in an app with 17 routes, Uppy, Radix, and canvas waveform rendering.
20. **Accessibility beyond contrast.** WCAG AA contrast is decided (Issue 165) and `@axe-core/playwright` is installed — but no recorded position on keyboard-only operation of the timeline/editor (the most complex surface), focus management, or screen-reader support.
21. **Multi-user / team accounts.** Every table keys on a single `creator_id`; RLS policies are direct-column on it. If a creator ever needs an editor/VA, the isolation model has no seam for it. No entry acknowledges this as a deliberate one-tenant-one-user bet.
22. **Deprecation/cleanup policy for descoped artifacts.** `render.yaml`, the unrun Helm chart, orphaned `static/*.js` (SOT itself flags "some orphaned post-retirement; pending an asset-cleanup pass"), and root-level fixtures (a 35 MB `.mp4`, 8 PNGs, `dump.rdb`, `{{pkgetc}}/`) are all committed. No stated policy on when a parked artifact gets removed vs. kept.

---

## Bottom line

The decision discipline here is genuinely above industry norm for a solo project — 259 dated entries, most with an "Industry standard checked" section, live URLs, and explicitly logged *deviations* from approved plans. Tenant isolation, LLM cost accounting, idempotency, and prompt-injection defense are all at or above current standard and are backed by structural tests rather than convention.

The gap is not rigor; it is **that the rigor is applied to features and not to structure.** Every algorithm choice has a documented rationale; the three largest structural facts about the codebase — that `worker/tasks.py` is the service layer, that `config.py` is a god-object, and that the frontend types are hand-duplicated — have **no decision entries at all**. And `docs/SOT.md`, the file that claims to be authoritative, has drifted on the model registry, the transcription default, the deployment target, and the test map.

The two highest-leverage corrective actions: (1) write the missing structural entries so the next refactor argues against a recorded position instead of a vacuum, and (2) reconcile `SOT.md` against `config.py` and mark the two superseded Render entries as SUPERSEDED.