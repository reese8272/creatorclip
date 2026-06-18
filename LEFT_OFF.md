# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a
> source of truth — those live in `docs/` (start at [`docs/README.md`](docs/README.md)).
> Updated at the end of every session.

**Last updated:** 2026-06-18 (Issue 85 React/TS UI overhaul — **85a–85g DONE; full migration complete (85g = soft cutover)**)
**Branch:** `main` — HEAD `7ca6330`
**Working tree:** ⚠️ **DIRTY — this session's work (Issue 85a–85g) is entirely uncommitted.** Changes under `frontend/` + `docs/` + `LEFT_OFF.md` **and now Python**: `main.py` (`/`→`/app` redirect), deleted `static/early-access.html`, and 4 test files repointed/flip-aware (`test_static.py`, `test_user_flow.py`, `test_pipeline_trigger.py`, `test_observability.py`). (One untracked stray `Screenshot 2026-06-17 211516.png` at repo root — leave it out of the commit.)
**Prod:** live & healthy at `https://autoclip.studio` — **does NOT have this session's work.**
**CI:** green on `origin/main`; `origin/main` and local `main` are in sync (0/0). This session's changes have not hit CI.

---

## CURRENT FOCUS

**Goal:** Execute Issue 85 — the full UI/UX overhaul to **React + TypeScript** — as an incremental
strangler-fig (one shippable slice at a time). Completed: **85a–85g — the full React/TS overhaul is DONE.** All seven app pages ported (dashboard,
onboarding, insights, analysis, review, profile, chat) + pre-auth (login, pricing, walkthrough); and
**85g soft cutover** flips `/` → `/app/dashboard` when the SPA bundle is built. Verified locally:
frontend **vitest 32/32** + eslint 0 + build clean; touched Python is AST-clean + **ruff clean**
(mypy/pytest **CI-authoritative** — no Postgres here). Nothing committed yet.

**→ NEXT ACTION** (pick one):

**A. Land 85a→85g** (recommended — the migration is complete; ship it). The branch model is
`feature → staging → main`, and **pushing to `main` auto-deploys**, so do NOT commit on `main`:
```bash
cd /home/reese/workspace/Youtube-Video-AI-Editor
git checkout -b feature/issue-85-react-overhaul
git add frontend/ docs/ LEFT_OFF.md
git status            # confirm frontend/node_modules + frontend/dist are NOT staged (gitignored)
git commit            # see suggested message below
git push -u origin feature/issue-85-react-overhaul
gh pr create --base staging   # PR into STAGING, not main
```
Watch **BOTH** the `Frontend (lint, test, build)` job **and the Python jobs** (this turn touched
`main.py` + 4 test files — first Python change in the 85 series). Suggested commit subject:
`feat(85): React/TS overhaul complete — 85a–85g (all pages ported; / → /app soft cutover)`
**CI is the real gate for the Python bits** (no Postgres/Docker here): watch the unit job (no SPA
build → `/` serves legacy, flip-aware root tests) AND the integration job (builds the image w/ the
SPA bundle → `/` redirects; the repointed `/static/index.html` tests must hold).

**B. Deferred follow-up (the "full retirement", staging-verified)**: delete/redirect the remaining
`static/*.html` (keep `tos.html` + `privacy.html`), repoint backend `next_action` URLs in
`routers/insights.py` + `routers/videos.py` from `/static/*` → `/app/*`, build the **global
activity-panel** widget (cross-page, in `AppChrome`), and a **React marketing hero** for anonymous
visitors if/when the app goes public (the Issue-136 `?yt=` funnel isn't ported — anon currently lands
on `/app/login`). Do this once the stack is verifiable on staging.

**C. Live visual QA** of the ported pages (login/pricing/walkthrough/profile/chat) in the running
app — **not yet done** (no Docker in this env). Needs the running backend + a seeded creator.

**Frontend gate (run from `frontend/`):** `npm run lint` · `npm run build` · `npm test`.
Last run this session: **eslint 0 · build clean · vitest 22/22.**

---

## WHAT WORKS NOW (verified this session — don't re-investigate)

- **85a foundation** (all under `frontend/src/`):
  - **React Router v7 Data Mode** (`App.tsx`: `createBrowserRouter` + `RouterProvider`, basename `/app`).
  - **TanStack Query v5** data layer (`lib/queryClient.ts`, provider in `main.tsx`); `useAuth` is a cached `useQuery`.
  - **`useTaskStream`** SSE hook (`hooks/useTaskStream.ts`) — EventSource lifecycle + guaranteed unmount cleanup.
  - **React Testing Library + jsdom** on Vitest (`test/setup.ts`, `vite.config.ts` `test` block).
  - **Design system** in **`docs/UI.md`** + applied to `index.css` `@theme` (warmer **OKLCH** dark-Linear palette; token NAMES preserved so utilities still resolve).
- **85b layouts + pages:**
  - `AuthGate` (protects routes; 401 → `/app/login`) + `AppChrome` (auth-agnostic Nav/Footer) replace the old `AppLayout`. Four route contexts: protected/public × chrome/bare.
  - **`useAuth` resolves to `user: null` on 401 (no hard redirect)** — this is what lets **pricing render for anonymous visitors**.
  - Ported: **Login** (`/app/login`, faithful; Google button = real nav to `/auth/login`, `?yt=` carried), **Pricing** (`/app/pricing`, public-or-authed; Stripe checkout + `crypto.randomUUID` intent preserved), **Walkthrough** (`/app/walkthrough`, 5-panel first-run + keyboard nav). Profile + Chat re-homed onto the shared shell.
- **85c dashboard** (`pages/Dashboard.tsx` + `components/dashboard/*`, route `/app/dashboard`):
  - Summary cards, YouTube-analytics panel (period selector), link-a-video form, video table with
    per-row Queue/Generate/review/Titles actions + the Issue-139 upload affordance, empty-state hero,
    trial + low-balance + DNA-CTA banners.
  - **Live status = gated TanStack `refetchInterval`** (polls `/videos` only while a clip-trackable
    video is in-flight; pauses on tab blur). Per-video clip counts via `useQueries`.
  - **Activity panel: inline now, global floating widget deferred** (user-approved). SPA catch-all +
    Nav "Dashboard" now point at `/app/dashboard`. `Badge` gained a `danger` variant.
- **85d onboarding** (`pages/Onboarding.tsx` + `components/onboarding/*`, route `/app/onboarding`, protected+bare):
  - 5-step flow: connect → data gate (catalog sync **live SSE console** + gated data-gate poll) →
    optional slim identity intake (unlocks step 4) → DNA build (**live SSE console** + brief poll) → confirm (→ profile).
  - Dual `useTaskStream` consoles (`StreamConsole`). **Issue-100 identity gate preserved** (Build-DNA
    disabled until identity exists). Dashboard `DnaCta` rewired to SPA routes by `setup.step`.
- **85e insights + analysis** (`pages/Insights.tsx`, `pages/Analysis.tsx` + `components/insights/*`, `components/analysis/*`):
  - Insights `/app/insights`: channel + DNA snapshots, sortable top/bottom performers w/ per-row AI analyze + save, upload windows, improvement brief (SSE log + gated poll), saved insights.
  - Analysis `/app/analysis`: token-streamed video-analysis prose + four `?video_id=`-gated features (Title Optimizer, Hook Analyzer, Chapter Markers, Thumbnail Concepts).
  - **New `useTaskResult` hook** (token/step/done-payload) + `onToken`/`onStep` on the stream layer + `useStreamAction` helper. Nav + dashboard links rewired to SPA routes.
- **85f review/editor** (`pages/Review.tsx` + `components/review/*`, route `/app/review`):
  - **Player-first redesign** (replaces the Issue-136 icon-rail/drawer): player + Keep/Drop/Skip/Trim + tag feedback lead; transcript editor alongside; Why-this-clip / Caption style / Clean pass as collapsible sections.
  - Full clip-queue nav. Transcript editor reimplemented (drag-select → `.ed-word[data-index]` snapping, cuts in state + localStorage, merge/undo, apply→poll→confirm). New `useCleanedUriPoll` hook (clean + edit share it). All nav + dashboard review links → `/app/review`.
- **Tests:** 32/32 (85a 6 + 85b 5 + 85c 5 + 85d 3 + 85e 4 + 85f 3: no-video prompt; clip loads meta/reasoning/transcript/disclaimer; Keep opens tag panel). **All nav links are now SPA-internal.**
- **85g cutover (soft, Python):** `main.py` `/` → `RedirectResponse('/app/dashboard', 302)` when `_SPA_BUILT`, else legacy index (fresh-checkout safe). `early-access.html` deleted. Root tests flip-aware (`skipif(_SPA_BUILT)`); legacy-content `/` tests repointed to `/static/index.html`. AST + ruff clean locally; **mypy/pytest CI-authoritative**.
- **Strangler-fig:** legacy `static/*.html` still on disk + served (now unlinked) as rollback insurance; full retirement deferred to a staging-verified follow-up.

---

## THE ARC THAT LED HERE

1. Prior session adopted React/TS (Profile pilot), beta logging (149/151), Pro chatbot (152) — **now committed/landed** (see `git log`; `origin/main` current).
2. User asked (via `/issue-workflow`) **how to bring React/TS to the rest of the project**; they have **no prior React/TS experience** (taught the model from the ported code).
3. Approved **foundation-first sequencing + a genuine redesign**. Filed the migration as **85a–85g** in `docs/issues.md`.
4. Built **85a** (architecture + design system), **85b** (pre-auth + presentational pages),
   **85c** (dashboard — first data-heavy page; gated-refetch live status), **85d** (onboarding —
   focused 5-step flow; dual `useTaskStream` SSE consoles; identity-gated DNA build), **85e**
   (insights + analysis — the LLM-streaming pages; new `useTaskResult`/`useStreamAction` primitives),
   **85f** (review/editor — player-first redesign; transcript editor; `useCleanedUriPoll`), then
   **85g** (soft cutover — `/` → `/app/dashboard` when the SPA is built; first Python touch of the
   series). The full React/TS overhaul is now complete.
5. En route: **`early-access` descoped** — it POSTs to a non-existent `/billing/early-access` route and sells subscriptions that contradict the minutes-pack model (logged in `docs/OFF_COURSE_BUGS.md`; product decision deferred to 85g).

---

## KEY COORDINATES & FACTS

| What | Value |
|------|-------|
| Branch / HEAD | `main` / `7ca6330` (work uncommitted) |
| Trunk model | `feature → staging → main`; **push to `main` auto-deploys** (`docs/BRANCHING.md`) |
| Prod URL / health | `https://autoclip.studio` · `/health` |
| Frontend dir | `frontend/` (Vite + React 19 + TS + Tailwind v4); SPA served under `/app/*` |
| Frontend gate | from `frontend/`: `npm run lint`, `npm run build`, `npm test` |
| New deps this session | `@tanstack/react-query`, `@testing-library/*`, `jsdom` |
| Migration breakdown | Issue 85a–85g in `docs/issues.md` — **all ✅ (85g = soft cutover); full static retirement deferred** |
| Secrets (names only) | `docs/SECRETS.md` — never read values |

---

## CONSTRAINTS & GOTCHAS

- **Do not commit on `main`** — branch first; a push to `main` triggers the production deploy.
- **No Docker in this env** → backend tests + image build are **CI-authoritative**; **no live visual QA** possible locally. 85a–85f touched no Python; **85g touches `main.py` + 4 test files** — AST + ruff verified locally, but **mypy/pytest are CI-authoritative** (no Postgres here). Watch unit AND integration jobs (the `/` flip differs by whether the SPA bundle is present — see the redirect/legacy split).
- **Tailwind `@theme` parser trap:** never let a `*/` sequence appear inside a CSS comment (e.g. `--text-*/`) — it closes the comment early and breaks the build. `@theme` values also can't use `var(...)` (inline literals instead).
- **react-hooks v7 lint rules** are strict: no synchronous `setState` in an effect body (use lazy `useState` init or callbacks), and no `window.location.href =` mutation (use `window.location.assign(...)`).
- **`MemoryRouter` in tests** needs `basename="/app"` + `initialEntries={['/app/...']}` or it renders nothing (location must be under the basename).
- **`frontend/dist` + `frontend/node_modules` are gitignored** — confirm they're not staged.
- Verify `origin/main` is current before branching (it is now: 0/0).

---

## POINTERS

- **Docs index:** [`docs/README.md`](docs/README.md)
- **Architecture / stack / file layout:** [`docs/SOT.md`](docs/SOT.md)
- **Design system (this overhaul):** [`docs/UI.md`](docs/UI.md)
- **Decisions (incl. 85a/85b, 2026-06-18):** [`docs/DECISIONS.md`](docs/DECISIONS.md)
- **Progress / session log:** [`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md)
- **Issue queue (85a–85g):** [`docs/issues.md`](docs/issues.md)
- **Incidental defects (early-access):** [`docs/OFF_COURSE_BUGS.md`](docs/OFF_COURSE_BUGS.md)
- **Auto-memory:** `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/MEMORY.md`
