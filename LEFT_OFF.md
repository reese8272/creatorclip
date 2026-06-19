# LEFT_OFF — session handoff

> **Read this first.** Living "where we are right now" file. Not a changelog, not a source of
> truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-06-19
**Branch:** `main` — Issues 162/163 are live on `origin` @ `d476860`. **Issues 164 + 165 are committed
LOCALLY but NOT pushed** (push is gated). `main` is ahead of `origin/main`; staging not yet re-synced.
**Working tree:** see below (164/165 committed; verify with `git status`).
**Prod:** `https://autoclip.studio` (React SPA under `/app`). Live is still `d476860` (Issue 163). The
**Issue 165 contrast fix has NOT shipped yet** — it lands on the next push.

---

## CURRENT FOCUS

**Issues 164 + 165 are DONE and verified locally, committed, awaiting a gated push.** This session
also: live-audited the real site, found a systemic WCAG-AA contrast gap, and fixed it at the root.

### → NEXT ACTION
1. **Push when the user OKs it** (gated — prod deploy). `git push origin main` then fast-forward
   `staging` to match (`git push origin main:staging` + `git branch -f staging main`).
2. **Re-verify on prod after the deploy rolls out:** `cd frontend && npm run test:prod` — expect the
   axe `color-contrast` count to drop from 420 → ~0 live. Needs a fresh `cc_session` (the captured one
   in `e2e/.auth/` expires ~1h; re-capture via `npm run test:prod:auth:cookie` after pasting a new
   cookie into `e2e/.auth/cc_session.txt`).
3. **Then pick the next issue** — 160 and 161 still open.

### What Issues 164/165 changed (all verified locally — lint, vitest 45/45, build, test:e2e smoke+a11y green)
- **Issue 164** — live-site harness: `frontend/playwright.config.prod.ts` + `e2e/prod/` (audit.spec,
  flows.spec, save-auth.mjs, build-auth-from-cookie.mjs). Runs vs prod with a real session. Scripts:
  `test:prod`, `test:prod:flows`, `test:prod:auth`, `test:prod:auth:cookie`, `test:prod:report`.
- **Issue 165** — WCAG AA contrast fix: `src/index.css` (`--color-subtle` ↑, accent split +
  `--color-accent-text`), `src/lib/utils.ts` (**root cause:** `extendTailwindMerge` registers the
  custom font-size scale so button text-color classes stop being dropped), 28 `text-accent`→
  `text-accent-text` swaps, Profile `<dl>` dt/dd, Review slider `aria-label`. New gate
  `e2e/a11y.spec.ts` (0 serious, 9 routes × 2 vp).
- **Residual (OFF_COURSE):** paid flows `analysis`/`titles` timed out at 60s on the real account
  (chat ✓) — slow LLM vs latency, needs investigation.

### Live-site audit how-to (Issue 164 — reuse this)
- Auth: log into `autoclip.studio` in a normal browser → DevTools → Application → Cookies → copy
  `cc_session` → paste into `frontend/e2e/.auth/cc_session.txt` → `npm run test:prod:auth:cookie`.
  (Headed `npm run test:prod:auth` exists but Google blocks the automated-browser OAuth.)
- Then `npm run test:prod` (audit) / `npm run test:prod:flows` (paid, real cost). Findings →
  `e2e/.results/prod/`, screenshots → `e2e/__screenshots__/prod/` (both gitignored).
- `e2e/.auth/` is gitignored (holds a live session token) — never commit it.

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
