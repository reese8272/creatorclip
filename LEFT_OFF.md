# LEFT_OFF — Onboarding wave (#204/#100/#96) + hybrid CI/CD SHIPPED to prod; Issue-275 next

> **Read this first.** Living "where we are right now" file for a brand-new session with zero memory.
> Source-of-truth docs live in `docs/`; this file orients and points to them — it is NOT a source of truth.

**Last updated:** 2026-06-24
**Checked out:** `main` · only untracked design artifacts + an unrelated `tests/eval/` bot scaffold sit
in the tree.
**SHIPPED this session (2 prod deploys, both smoke-green):** the **onboarding wave** (#204/#100/#96) +
**CI fixes** + **hybrid local/self-hosted CI/CD**. Prod verified: `/health` 200, `/app/walkthrough` 200,
`/` → 302. (The 304–309 redesign shipped a prior session @ `80a7474` and is live; still pending the user's
own browser eyeball.)

---

## ✅ CURRENT FOCUS — onboarding wave shipped; next is Issue 275 (GKE staging)

This session delivered, all to prod:
1. **Hybrid CI/CD** (off GH-hosted minutes): a local **pre-push gate** (`.githooks/pre-push` →
   `scripts/ci_local.sh`, ratchets format/lint on *changed* files) + `ci.yml` flipped to **self-hosted**
   (PR/dispatch only on the single runner — `push` trigger dropped so CI can't starve the deploy; restore
   after a 2nd runner). See `docs/runbooks/local-ci-cd.md`.
2. **#204** — identity intake genuinely optional (removed the Build-DNA gate).
3. **#100** — routed new creators to the previously-orphaned walkthrough first; self-explaining badge tooltips.
4. **CI-green fixes** — `ruff format` (42 files), all eslint errors cleared, a suite-wide vitest timing
   flake fixed (`asyncUtilTimeout`).
5. **#96** — chat-driven intake (`Quick form | Chat it out` on `OnboardingIdentity`); guided Q&A →
   `propose_profile` tool → validated → confirm via existing `POST /me/identity` (model never writes).

**→ NEXT ACTION (in priority order):**
1. **One VM step to finish the CI/CD rollout** (optional but recommended): on the prod-VM runner host,
   `apt-get install ffmpeg libpq-dev gcc python3.12 python3.12-venv`, and register a **2nd self-hosted
   runner**, then restore `push:` in `ci.yml` so per-push self-hosted CI runs. Snippet in the runbook.
   Until then the local pre-push hook is the gate for direct-to-main pushes (working now).
2. **Issue 275 — stand up real GKE staging** (the linchpin). Unblocks the **moat/eval set #198–202** and
   validates every "staging-pending" behavioral AC. Runbook: `docs/runbooks/275-279-k8s-deploy.md`.
3. **Support the user's logged-in prod walkthrough** of the 304–309 redesign + the new onboarding flow
   (walkthrough → optional intake form/chat → DNA). Not browser-verified by Claude.
4. **Flip `ACTIVE_SPEAKER_REFRAME_ENABLED`** once a render-env exists (#189 shipped behind default-OFF flag).

---

## WHAT WORKS NOW (verified — don't re-investigate)

- **Redesign (304–309) is live & static-clean:** vitest **182/182**, `tsc -b && vite build` clean, eslint
  **0 new** (10 pre-existing baseline held). Built screen-by-screen with gates green at every step.
  - 304 foundation: `Chip` (decorative `alt=""`) + 8 animation states (`components/chip/ChipStates.tsx`,
    keyframes `chip-*` in `index.css`), sprites in `frontend/public/chip/`, nav + `/settings` route.
  - 305 Dashboard videos-first · 306 Review filmstrip trim + "Your call" card · 307 Editor short|long toggle
    + `LongFormEditor` · 308 Profile→snapshot + full Settings page · 309 Chip wiring (Insights/Analyze/Chat).
- **Scope calls (user-confirmed, in DECISIONS.md):** un-backed Settings controls + long-form full-source
  player/transcript are **honest "coming soon"/placeholder**, never faux-functional. Pricing untouched
  (already matched). Three logged deviations: `alt=""`, `MasterTimeline` is new (not a `Timeline` reuse).
- **Prior session (still live):** the full usable-beta loop — sign-up → clickwrap + 13+ age gate → onboarding
  → ingest → AI clips → review + why-not-clipped → Editor → publish (incl. scheduled) → outcome loop →
  transactional notifications, plus Insights/honesty/funnel/logging-cost-retention.

---

## THE ARC THAT LED HERE

1. Prior session shipped ~24 issues / 9 prod deploys = the W2 core product (see git history `75cda13`…`bb5be31`).
2. User dropped a high-fidelity design handoff (`React app visual review.zip`) — "100% of the UI I want, ~90% complete."
3. Ran `/issue-workflow`: grounded in code, decomposed into 6 issues (304–309), resolved 3 scope gaps with the
   user (presentational-only / honest scaffolds / screen-by-screen), researched a11y standards live.
4. Built 304 → 305 with checkpoints; user said "do the rest" → built 306–309 in one pass, all gated green.
5. User couldn't log in on dev → asked to commit/push/deploy to `main`. Done: `80a7474`, deploy success, live.

---

## KEY COORDINATES & FACTS

| Item | Value |
|------|-------|
| Trunk | `main` == `origin/main` @ `80a7474` — DEPLOYED |
| Prod | `autoclip.studio`; host `147.182.136.107`; deploy dir `/opt/autoclip` |
| Deploy chain | push `main` → GH **"Docker publish"** (~1min → GHCR) → on success → **"Deploy to production"** (self-hosted: migrations → rollout → **smoke test w/ auto-rollback** → cleanup) |
| Known-red CI | GitHub-hosted **`CI`** fails in ~6s every push = **billing-disabled runner**. Ignore it; the self-hosted deploy path is the real gate. |
| Watch a run | `gh run watch <id> --exit-status` · find deploy: `gh run list --workflow deploy.yml --limit 2` |
| Migrations | linear, ends at `0034` (age gate). **No new migrations this session** (redesign was presentational). |
| Frontend gates | `cd frontend && npm run build` (tsc+vite) · `npx vitest run` · `npm run lint` (expect 10 pre-existing, 0 new) |
| Feature flag | `ACTIVE_SPEAKER_REFRAME_ENABLED=false` (#189) — flip after render-env verify |
| Product decision | **#204: creator identity is OPTIONAL at onboarding** (skip → use video data, nudge later). |
| Redesign memory | `…/memory/project_autoclip_redesign.md` — full 304–309 plan + scope + status |

---

## CONSTRAINTS & GOTCHAS

- **`origin/staging` is now BEHIND `main`** — the redesign went straight to `main` per the user's request
  (not via the usual feature→staging→main flow). If staging parity matters, fast-forward `staging` to `80a7474`.
- **Dev box has no Docker / Postgres / Redis / ffmpeg / browser login.** Backend pytest can't fully run;
  Claude cannot log in to verify UI visually. The prod **smoke test** + the user's browser are the real gates.
- **Frontend lint baseline is 10 problems (6 err / 4 warn), all pre-existing** (Editor `set-state-in-effect`,
  Onboarding unused args, etc. — logged in `docs/OFF_COURSE_BUGS.md`). Goal each change: **0 NEW**, not 0 total.
- **`react-refresh/only-export-components`:** a component file must export only components — put shared
  constants/helpers in a sibling module (e.g. `chip/poses.ts`, `review/trim.ts`). This bit twice this session.
- **Never trust a build-agent's "tests passed"** — re-run gates at integration.
- **Pushing to `main` triggers a prod deploy.** Don't push unless you intend to deploy.

---

## POINTERS (the real source-of-truth docs)

- `docs/PROJECT_STATE.md` — progress log; top has the 304–309 redesign + W2 entries.
- `docs/issues.md` — Master Roadmap (waves/lanes/briefs). Always re-verify a "DONE" against code before depending on it.
- `docs/SOT.md` (frontend file layout incl. new chip/review/editor components) · `docs/DECISIONS.md`
  (the 2026-06-23 redesign entry, items 1–15) · `docs/COMPLIANCE.md` · `docs/CLIPPING_PRINCIPLES.md` · `docs/DEPLOYMENT.md`.
- `docs/runbooks/{275-279-k8s-deploy, 255-258-dr-durability, 24-25-26-beta-deploy-gates, 194-youtube-publish}.md`.
- `CLAUDE.md` — project rules (One Rule: research current standard first; per-issue CHECK→APPROVE→BUILD→REVIEW).
- Memory: `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` — `project_autoclip_redesign.md`,
  `project_wave_execution_workflow.md`, `user_frontend_experience.md` (user is newer to React — teach as you go).
</content>
</invoke>
