# LEFT_OFF — session handoff

> **Read this first.** Living "where we are right now" file. Not a changelog, not a source of
> truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-06-19
**Branch:** `main` @ `4d3f067` — in sync with `origin/main` (0 ahead / 0 behind).
**Working tree:** UNCOMMITTED — this session's Issue 162 work (Playwright E2E harness + doc updates).
New: `frontend/e2e/`, `frontend/playwright.config.ts`. Modified: `frontend/{package.json,
package-lock.json,vite.config.ts,eslint.config.js,.gitignore}`, `docs/{DECISIONS,SOT,PROJECT_STATE,
issues,OFF_COURSE_BUGS}.md`, and this file.
**Prod:** `https://autoclip.studio` (React SPA under `/app`). Auto-deploys on push to `main`. Live
commit unchanged this session (`4d3f067`) — Issue 162 is test tooling only, nothing user-facing shipped.

---

## CURRENT FOCUS

**Issue 162 (Playwright E2E + visual harness) is DONE and green, but UNCOMMITTED.** This session built
the harness, ran the first rendered-UI audit, and filed the findings as **Issue 163**. Two things
remain: commit Issue 162, then start Issue 163 (the UI polish).

### → NEXT ACTION
1. **Commit Issue 162** (test tooling — does NOT touch prod, safe to land on `main`):
   ```
   cd /home/reese/workspace/Youtube-Video-AI-Editor
   git add frontend/ docs/ LEFT_OFF.md
   git commit   # message: "feat(test): Playwright E2E + visual harness for the SPA (Issue 162)"
   ```
   Note: pushing `main` triggers a prod deploy + is gated — get explicit user go-ahead before `git push`
   (the commit itself is fine to make locally).
2. **Start Issue 163** (`docs/issues.md` → "Issue 163 — SPA UI polish from the Issue 162 audit").
   Run the harness first to regenerate the evidence: `cd frontend && npm run test:e2e` →
   screenshots land in `frontend/e2e/__screenshots__/`. Fix in priority order:
   - **[SEV2]** Mobile nav overflow at 390px (`Nav.tsx` / `AppChrome.tsx`) — add a responsive collapse below ~640px.
   - **[SEV3]** Review desktop empty bottom-right quadrant (`pages/Review.tsx` grid).
   - **[SEV3]** Analysis cards → 2×2 grid (`pages/Analysis.tsx`).
   - **[SEV3]** Chat empty-state vertical void (`pages/Chat.tsx`).
3. **Re-verify after each fix:** `npm run lint && npm test && npm run build && npm run test:e2e` (20/20).

## WHAT WORKS NOW (verified — don't re-investigate)

- **Playwright harness is installed and green: `npm run test:e2e` → 20/20** (10 routes × desktop-1440
  + mobile-390). `@playwright/test` 1.61 under `frontend/`; Chromium binary + system deps installed
  (the WSL2 `sudo apt` step is already done — Chromium launches natively now).
- **Backend is mocked at the network boundary** (`frontend/e2e/fixtures/mock-api.ts`, `page.route`,
  fixtures shaped to `src/types.ts`, `authed`/`anon` seeds) — every page renders with NO live
  backend, honoring the no-Docker constraint.
- **No regression from the harness:** `npm run lint` clean, `npm test` 44/44, `npm run build` ok.
  The two test runners are cleanly separated (Vitest→`src/` via `include`/`exclude`; Playwright→`e2e/`;
  ESLint React-rules scoped to `src/` so Playwright's `use()` fixture doesn't false-positive).
- **The overhaul renders well** (audit conclusion): honesty banner on every page, dark-mode elevation
  holds up, FitBadge reads, pricing accent-glow works, mobile reflows to single-column. The 4 issues
  in Issue 163 are the only real defects found.
- **Prior elevation epic remains live on prod** (passes 1–3, last session) — unchanged this session.

## THE ARC THAT LED HERE

1. Prior session: 3 UI polish passes (incl. dark-mode elevation fix) merged to main + deployed.
2. **This session:** user asked if I could "poke and prod the UI/UX." Found the SPA had only
   Vitest/jsdom tests (no rendering engine → can't see CSS/layout/elevation bugs).
3. Ran the full issue-workflow for **Issue 162**: researched Playwright vs Cypress + the mocked-backend
   pattern (DECISIONS 2026-06-19), built the harness, hit the expected WSL2 sudo blocker (user ran
   `sudo npx playwright install-deps chromium`), then went 20/20 green.
4. Reviewed all 20 screenshots → 4 findings. User asked to promote them to the next issue →
   **Issue 163** filed, OFF_COURSE_BUGS rows marked "Promoted → Issue 163".

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Repo | `github.com/reese8272/creatorclip` |
| Prod URL | `https://autoclip.studio` (Cloudflare-fronted; SPA at `/app`, React Router basename `/app`) |
| Frontend | `frontend/` — React 19 + TS + Vite 8 + Tailwind v4; build = `npm run build` (in `frontend/`) |
| E2E harness | `frontend/playwright.config.ts` + `frontend/e2e/` (`smoke.spec.ts`, `fixtures/mock-api.ts`). Run: `npm run test:e2e` · UI mode: `test:e2e:ui` · report: `test:e2e:report` |
| Screenshots | `frontend/e2e/__screenshots__/{desktop,mobile}-<page>.png` — **gitignored** (audit output, not snapshot baselines) |
| Playwright | `@playwright/test` 1.61; Chromium installed at `~/.cache/ms-playwright`; system deps installed via `sudo apt` (done) |
| SPA routes | dashboard, insights, analysis (`?video_id=`), review (`?video_id=`), profile, chat, onboarding, walkthrough, pricing, login. Defined in `frontend/src/App.tsx` |
| Auth plumbing | `useAuth.ts` probes `GET /auth/me` (401→null); `AuthGate.tsx` redirects to `/login`. Mock seed `anon` makes `/auth/me` 401 |
| SPA serving | `main.py` serves `frontend/dist` under `/app`. **`frontend/dist` is gitignored** — built in `Dockerfile` at image build |
| Deploy | push to `main` → `docker-publish.yml` (image) → `deploy.yml` (self-hosted VM) |
| CI | `.github/workflows/ci.yml` — incl. "Frontend (lint, test, build)". **Note: CI does not yet run `test:e2e`** — Playwright is local-only for now |
| Next issue numbers | 162 = DONE (this session); 163 = OPEN (UI polish); 160/161 still open from prior |

## CONSTRAINTS & GOTCHAS

- **Chromium now launches natively** — the old `LD_LIBRARY_PATH` brew shim from the previous LEFT_OFF
  is obsolete; system libs were installed via `sudo apt` (`playwright install-deps`) this session.
- **`npm run test:e2e` auto-starts the Vite dev server** (`webServer` in the config, port 5173) and
  reuses an already-running one locally. No backend needed — it's fully mocked.
- **Two runners must stay separated** — Vitest's default glob otherwise grabs `e2e/*.spec.ts` and
  crashes on Playwright's `test` export (already fixed via `vite.config.ts` `include`/`exclude`).
- **Pushing to `main` triggers a prod deploy** and is gated (auto-mode denies agent-initiated
  default-branch pushes without clear user intent). Get explicit go-ahead before any `git push`.
- **`main` and `staging` are kept byte-identical via fast-forward only** — never merge-commit between them.
- **Dark-mode depth = surface contrast + borders + top-edge highlight, NOT black drop-shadows** — apply
  to the Issue 163 polish work too.
- Backend tests need a real Postgres (no DB mocking rule) — this env has **Redis only**. Frontend
  work (Issue 163) doesn't need it.

## POINTERS (sources of truth — do not duplicate here)

- `docs/issues.md` — **Issue 162** (done) + **Issue 163** (the next focus) specs. Issues 160/161 still open.
- `docs/DECISIONS.md` — **2026-06-19, Issue 162** entry: Playwright vs Cypress, mocked-backend rationale, sources.
- `docs/OFF_COURSE_BUGS.md` — the 4 audit findings (2026-06-19), marked "Promoted → Issue 163".
- `docs/SOT.md` — frontend section now lists the `e2e/` harness + scripts.
- `docs/PROJECT_STATE.md` — top "Last completed" entry covers Issue 162.
- `docs/UI.md` — design system (tokens, type, motion); relevant for Issue 163 layout fixes.
- `CLAUDE.md` — project rules (read-order, Check→Approve→Build→Review, research-first, off-course-bug log).
- Memory: `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` (index `MEMORY.md`).
