# LEFT_OFF — session handoff

> **Read this first.** Living "where we are right now" file. Not a changelog, not a source of
> truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-06-19
**Branch:** `main` @ `322a0ba` — in sync with `origin/main`. Working tree: only `LEFT_OFF.md` modified (this file).
**Trunk state:** `origin/main` @ `322a0ba` · `origin/staging` @ `322a0ba`. **`main` and `staging` are byte-identical** (`git diff main staging` empty). The `fix/ui-dark-mode-elevation` branch is **merged and deleted** (local + remote).
**Prod:** `https://autoclip.studio` (React SPA under `/app`). Auto-deploys on push to `main`. **Now serving `322a0ba`** — the dark-mode elevation fix shipped this session (Deploy to production run `27838545605`, success, 16:57 UTC).

---

## CURRENT FOCUS

The elevation fix is **merged to main and deployed to prod.** The merge work is done. The one
remaining step is a **human eyeball check** — confirm the cards now visibly separate from the dark
background on the live site (a bot/headless check can't do this reliably; see gotchas).

### → NEXT ACTION
1. **Visually confirm prod** — hard-refresh `https://autoclip.studio/app` (Cmd/Ctrl-Shift-R) and
   check that surfaces/cards now have visible elevation (surface contrast + borders + top-edge
   highlight) instead of looking flat. This is the acceptance check for pass 3.
2. **If it looks right** — UI elevation epic is closed. Pick up the open follow-ups below (Issue 160 / 161).
3. **If it still looks flat** — do NOT assume a stale deploy (pipeline is verified green). Re-render
   the *actual compiled CSS* in headless Chromium (recipe in the table) and compare before/after,
   exactly as the root-cause probe did this session. The fix targets `frontend/src/index.css`.
4. **Housekeeping:** this `LEFT_OFF.md` is the only uncommitted change. Commit it when ready
   (`git add LEFT_OFF.md && git commit`) — it's not part of any code change, so it can land alone.

---

## WHAT WORKS NOW (verified — don't re-investigate)

- **Three UI polish passes are done, merged to main, and live on prod** (each gated: `npm run build`
  + 44 vitest + `eslint`, all green):
  - *Pass 1* (`04bf5d0`): reconciled `frontend/src/index.css` to `docs/UI.md` (radii, semantic type
    scale, Geist font) + applied the design system to the shared primitives.
  - *Pass 2* (`beca860`): per-page sweep — `FitBadge` on Review clips, accent-glow, page titles →
    `text-h1/h2`, entrance motion, elevated remaining flat cards, fixed the "Friday Friday"
    upload-window bug, added fit-tier + FitBadge tests.
  - *Pass 3* (`322a0ba`): dark-mode elevation fix — **this session's deliverable, now on main/prod.**
- **Root cause of "looks the same" is SOLVED.** Rendered the real compiled CSS in headless Chromium:
  the card `shadow-sm`/`shadow-inset` was a **black shadow on near-black bg → invisible**. Fixed via
  surface-contrast + brighter borders + a stronger top-edge highlight (NOT shadows).
- **The deploy pipeline is verified working end-to-end** this session: push to `main` → "Docker
  publish" (success) → "Deploy to production" via `workflow_run` (success, 43s). A stale deploy is
  **not** a plausible failure mode.
- **Merge hygiene is clean:** main fast-forwarded `beca860..322a0ba` (no merge commit); main ≡
  staging; the feature branch is gone from both local and remote. Only `main` + `staging` remain.
- **Token-adoption audit = 100%** — every design-system token group has consumers.

## THE ARC THAT LED HERE

1. User merged prior work (Issues 153–159 regression batch) to staging+main with identical history.
2. User reviewed screenshots: UI felt "blocky / not production-grade." Found the React port was real
   but the design system (`docs/UI.md`) had been **built and never applied**.
3. Pass 1 (primitives) + Pass 2 (per-page sweep) — both shipped to main and auto-deployed.
4. User: "it looks almost the exact same." Probed by rendering the actual CSS → elevation was
   invisible on dark. Pass 3 fixed it; landed on staging, then blocked at the `main` push (the
   auto-mode classifier denied an agent-initiated default-branch push without explicit intent).
5. **This session:** user gave the explicit go-ahead ("merge the feature branch to staging and
   main"). Promoted `322a0ba` to main via fast-forward, cleaned up the branch, watched the prod
   deploy go green. Done — pending the visual confirmation above.

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Repo | `github.com/reese8272/creatorclip` |
| Prod URL | `https://autoclip.studio` (Cloudflare-fronted; SPA at `/app`, React Router basename `/app`) |
| Live commit | `322a0ba` on `main` (= `staging`); Deploy run `27838545605` (success, 16:57 UTC) |
| Frontend | `frontend/` — React 19 + TS + Vite 8 + Tailwind v4; build = `npm run build` (in `frontend/`) |
| Elevation fix lives in | `frontend/src/index.css` (surface/border/top-edge tokens) |
| SPA serving | `main.py` serves `frontend/dist` under `/app`. **`frontend/dist` is gitignored** — built in `Dockerfile` (`npm run build`) at image build |
| Deploy | push to `main` → `docker-publish.yml` (image) → `deploy.yml` (self-hosted VM): `docker compose pull` + `alembic upgrade head` + `up -d` |
| Prod host | SSH alias `creatorclip-vm` → `147.182.136.107` (root, keyed); compose at `/opt/autoclip/docker-compose.prod.yml` |
| Prod image / DB | `ghcr.io/reese8272/creatorclip:latest` · container `autoclip-postgres-1`, db/user `creatorclip` |
| CI | `.github/workflows/ci.yml` — 7 jobs incl. "Frontend (lint, test, build)"; on push/PR to `main`+`staging` |
| Fit-tier thresholds | `frontend/src/lib/fit.ts` — strong ≥ 0.70, moderate ≥ 0.45 (clip score 0–1 from `clip_engine/scoring.py`); **tunable first-pass defaults** |
| Headless render recipe | `npx playwright` (chromium installed) + `LD_LIBRARY_PATH=/home/linuxbrew/.linuxbrew/opt/nss/lib:/home/linuxbrew/.linuxbrew/opt/nspr/lib` (system libnss3/libnspr4 missing; brew provides them) |
| Open follow-ups | **Issue 160** (cross-page active-tasks panel — gated by `MAX_CONCURRENT_SSE_PER_CREATOR=3`); **Issue 161** (repoint stale backend `next_action` `/static` URLs) |

## CONSTRAINTS & GOTCHAS

- **Pushing to `main` triggers a prod deploy** AND is gated (auto-mode denies agent-initiated
  default-branch pushes without clear user intent). Get explicit go-ahead before any future push.
- **`main` and `staging` are kept byte-identical via fast-forward only** — never create merge commits
  between them. Verify with `git diff main staging` (must be empty).
- **Dark-mode depth = surface contrast + borders + top-edge highlight, NOT black drop-shadows** (they
  vanish on near-black). Apply this to any future UI token work.
- **`frontend/dist` is gitignored & built at deploy** — to view changes locally you must `npm run
  build` then serve the backend against `frontend/dist`; viewing prod requires the deploy to have run.
- Headless Chromium here needs the `LD_LIBRARY_PATH` brew shim (table above); no passwordless sudo to
  `apt install` the libs.
- Backend tests need a real Postgres (project rule: no DB mocking) — this env has **Redis only**.
- Prod sits behind Cloudflare Bot Fight Mode — datacenter IPs may get a 403; verify via SSH/the VM
  or a real browser, not a raw curl from CI. (This is why step 1 above is a human eyeball check.)

## POINTERS (sources of truth — do not duplicate here)

- `docs/UI.md` — design system (tokens, type, motion, confidence badges); status notes for passes 1–3.
- `docs/DECISIONS.md` — **2026-06-19 entries** cover all 3 UI passes + fit-tier thresholds + the
  dark-mode elevation correction (with render evidence).
- `docs/issues.md` — Issues 153–161 specs/status. `docs/PROJECT_STATE.md` — progress log.
- `docs/OFF_COURSE_BUGS.md` — incidental defects ("Friday Friday" logged + marked fixed).
- `docs/SOT.md`, `docs/DEPLOYMENT.md`, `docs/COMPLIANCE.md` — architecture / deploy / ToS.
- `CLAUDE.md` — project rules (read-order, Check→Approve→Build→Review, research-first).
- Memory: `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` (index `MEMORY.md`).
