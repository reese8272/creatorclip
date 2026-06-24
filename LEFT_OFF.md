# LEFT_OFF — W2 core product SHIPPED to prod; onboarding wave + Issue-275 staging are next

> **Read this first.** Living "where we are right now" file for a brand-new session with zero memory.
> Source-of-truth docs live in `docs/`; this file orients and points to them — it is NOT a source of truth.

**Last updated:** 2026-06-23
**Checked out:** `main` @ **`bb5be31`** · working tree clean
**SHIPPED:** `main` == `staging` == `origin/main` == `origin/staging` == **`bb5be31`**. Prod health:
`autoclip.studio/` → 302 → `/app/dashboard` (200). Latest `Deploy to production` = success (smoke green).

---

## ✅ CURRENT FOCUS — what's live + what's next (start here)

**This session shipped ~24 issues across 9 prod deploys** (every push QA-gated + smoke-tested, auto-rollback
never triggered). The **entire usable-beta functional surface is now live on prod**: sign-up → clickwrap
consent + 13+ age gate → new-creator onboarding redirect → ingest → AI clips → review + "why-not-clipped"
transparency → **timeline/waveform Editor** → **publish to YouTube (incl. scheduled)** → outcome-learning
loop → **transactional notifications** ("your clips are ready" + 5 more), plus Insights, honesty surfaces,
funnel telemetry, and the logging/cost/retention stack.

**→ NEXT ACTION (pick one):**
1. **Onboarding wave — #204 / #100 / #96** (now unblocked: the #204 "identity = OPTIONAL, skip-and-nudge"
   product decision is made — see DECISIONS/this file — and #235 funnel is live). Clean next batch; build
   #204 first (resolve the optional-vs-required UI contradiction per the decision), then #100 folds in,
   then #96 (chat-driven intake). Dev-box verifiable (frontend + auth).
2. **Issue 275 — stand up real GKE staging** (the linchpin, unchanged from W1). It's the ONLY thing that
   unblocks the **moat/eval set #198–202** (NDCG/MAP/Kendall efficacy harness + adversarial scenarios +
   recency-decay calibration + continuous eval) and validates every behavioral AC this session marked
   "staging-pending." Runbook: `docs/runbooks/275-279-k8s-deploy.md`.
3. **Flip `ACTIVE_SPEAKER_REFRAME_ENABLED`** once a render-env exists: #189 per-frame reframe is shipped
   but **behind a default-OFF flag** (legacy Haar crop is still live). Verify on real media, then enable.

---

## WHAT SHIPPED THIS SESSION (9 deploys, in order)

1. **Batch 1** (`75cda13`) — #194/#195 publish (youtube.upload consent + idempotent upload task) landed off
   the held `feat/batch-b-publish`; **migration renumbered 0028→0030**. Plus **8 mis-tracked-OPEN issues
   reconciled to DONE** after a verified audit: #242 #233 #216 #220 #239 #237 #250 #222.
2. **Batch 2** (`24a4128`) — #243 notification data model + idempotent send task (mig 0031) · #196 scheduled
   publish (mig 0032).
3. **Core loop** (`f3ad126`) — #244 wire all 6 notification triggers (**delivers #193**) · #197 publish→ClipOutcome wire.
4. **Usability** (`a39491e`) — #215 new-creator onboarding redirect · #227 desc-clamp · #212 Insights rebuild · #217 clip-transparency.
5. **Editor** (`c58ff0b`) — #188 timeline+waveform Editor; Review streamlined to triage + "Refine →".
6. **Reframe** (`cbc68c5`) — #189 per-frame active-speaker reframe, **flag-gated default-off** (mediapipe lazy-imported, not installed → app imports clean).
7. **Funnel** (`cf060db`) — #235 funnel instrumentation + resolver /static→/app cleanup (**closes #161**).
8. **Consent** (`f1bd74f`) — #299 clickwrap consent + versioned record (mig 0033).
9. **Age gate** (`bb5be31`) — #300 COPPA 13+ age gate (mig 0034).

---

## VERIFICATION REALITY (important honesty — unchanged)

- This dev box has **no Docker / Postgres / Redis / ffmpeg**. Backend **pytest suite can't fully run**
  (conftest needs live Redis/PG). **Everything behavioral is staging-pending → Issue 275**: RLS isolation,
  SSE, live `videos.insert`, the DB-backed eval harness, real reframe render, notification exactly-once,
  funnel events actually writing, all migrations' live `alembic upgrade`.
- What IS verified every push: `ruff`/`mypy`/`py_compile`/`bandit`, `cd frontend && npm run build` (tsc+vite),
  `npx vitest run`, and **DB-free unit tests via `pytest <file> --override-ini="addopts=" -p no:cacheprovider`**
  (the override dodges a known conftest PG-guard bug — see OFF_COURSE_BUGS). The prod **smoke test** is the
  real behavioral gate.
- **Never trust a build-agent's "tests passed"** — re-run everything at integration. This session that caught:
  a #242 honesty test that falsely "passed" (naive substring vs the canonical `assert_no_virality_promise`),
  the pre-existing `test_no_virality_in_openapi_schema_descriptions` failure, and a #217 `E402` ruff miss.

---

## KEY COORDINATES & FACTS

| Item | Value |
|------|-------|
| Trunk | `main`==`staging`==`origin/main`==`origin/staging` @ `bb5be31` — DEPLOYED |
| Prod | `autoclip.studio`; host `147.182.136.107`; deploy dir `/opt/autoclip` |
| Deploy chain | push `main` → GH **"Docker publish"** (~1-5min GHCR) → on success → **"Deploy to production"** (self-hosted: migrations → rollout → **smoke test w/ auto-rollback** → cleanup) |
| Known-red CI | GitHub-hosted **`CI`** fails in ~6s every push = **billing-disabled runner**. Ignore it. |
| Watch a run | `gh run watch <id> --exit-status --interval 15` |
| Migrations | linear `0026←0027←0028←0029←0030(clip_publications)←0031(notifications)←0032(clip_pub_schedule)←0033(consent)←0034(age)` |
| Feature flag | `ACTIVE_SPEAKER_REFRAME_ENABLED=false` (#189) — flip after render-env verify |
| Product decision | **#204: creator identity is OPTIONAL at onboarding** (skip → use video data, nudge later). Bake into DECISIONS.md when building #204. |
| Multi-agent run | spawn build agents with **`isolation: "worktree"`** (MANDATORY — without it they thrash the shared tree); assign **migration numbers up front** for parallel migration-adders; integrate with keep-both doc strips. |

---

## WHAT'S LEFT (post-this-session)

- **Onboarding wave (dev-box buildable now):** #204 (resolve optional-identity contradiction — decision made),
  #100 (tutorial, folds into 204+215), #96 (chat-driven intake, dep 204).
- **Moat / eval (genuinely staging-gated → Issue 275):** #198 efficacy harness (NDCG/MAP/Kendall), #199
  adversarial scenarios, #200 recency-decay calibration, #201 `performed_well` baseline (dep 198), #202
  continuous eval logging.
- **The big tail you deferred:** scale/quota/load, K8s & deploy (#275–280), DR/infra (#255–258), edge security
  (WAF/rate-limit), cost dashboards, external-legal (Google OAuth verification #29/#26), the deploy-gate
  capstone #303/#30. See `docs/issues.md` lanes.

---

## POINTERS (the real source-of-truth docs)

- `docs/PROJECT_STATE.md` — progress log; top has the W2-batch1 + per-issue DONE entries.
- `docs/issues.md` — Master Roadmap (waves/lanes/briefs). Statuses reconciled this session — but ALWAYS
  re-verify a "DONE" against the code before depending on it (audit-before-build is the #1 lesson).
- `docs/SOT.md` · `docs/DECISIONS.md` · `docs/COMPLIANCE.md` · `docs/CLIPPING_PRINCIPLES.md` · `docs/DEPLOYMENT.md`.
- `docs/runbooks/{275-279-k8s-deploy, 255-258-dr-durability, 24-25-26-beta-deploy-gates, 194-youtube-publish}.md`.
- `CLAUDE.md` — project rules (One Rule: research current standard first; per-issue CHECK→APPROVE→BUILD→REVIEW).
- Memory: `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` —
  `project_wave_execution_workflow.md` has the W0/W1/W2 outcomes, the supervised batch-of-3 loop, and the
  recurring lessons (audit-first, worktree isolation, migration numbering, never-trust-agent-green).
