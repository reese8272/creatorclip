# LEFT_OFF — session handoff

> **Read this first.** Living "where we are right now" file. Not a changelog, not a source of
> truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-06-18
**Branch:** `feature/issue-85-overhaul-regressions` @ `afd012b` (8 commits ahead of `origin/main`, pushed, upstream in sync)
**Working tree:** verified **pristine** — only `LEFT_OFF.md` modified (this file) + untracked `Screenshot 2026-06-17 211516.png`; all code/docs match committed. (Earlier this session a stale-editor-buffer **Save** reverted 10 code files + `docs/issues.md`; caught via `git diff HEAD` and restored with `git restore` — vitest 38/38 confirmed the fixes are back. The PR was never affected.)
**PR #27:** OPEN · **MERGEABLE** · `mergeStateStatus: CLEAN` · all 7 CI checks green (last verified 2026-06-18).
**Prod:** live at `https://autoclip.studio` — **does NOT have this work yet** (lands when PR #27 merges to `main`, which auto-deploys).

---

## CURRENT FOCUS

Ship the post-cutover regression batch (Issues 153–159) to production. **All code is done, committed, pushed, and CI-green. The only thing left is the merge + a post-deploy prod verification.**

**→ NEXT ACTION**
1. **User merges PR #27** into `main` (https://github.com/reese8272/creatorclip/pull/27) — it is `MERGEABLE` with all 7 CI checks passing. *Claude was asked NOT to merge; the user holds the prod trigger.* Merging to `main` **auto-deploys to live prod** (`docker-publish.yml` rebuilds the image incl. `frontend/dist` → `deploy.yml` runs on the self-hosted VM: `docker compose pull` + `alembic upgrade head` (no-op this batch) + `up -d`).
2. **Watch the deploy:** `gh run list --limit 5` — expect a "Docker publish" then "Deploy to production" run to go green after merge.
3. **Verify on prod (closes deferred criteria for Issues 153 & 155):**
   - SSH: `ssh creatorclip-vm` (configured host → `147.182.136.107`, root, keyed).
   - Re-light check (Issue 155 — UI telemetry): after clicking around the live site, confirm NEW `source='ui'` rows:
     ```
     cd /opt/autoclip
     docker exec autoclip-postgres-1 psql -U creatorclip -d creatorclip -c \
       "SELECT to_char(at,'HH24:MI:SS') at, event, page, left(target,30) target FROM event_logs WHERE source='ui' ORDER BY at DESC LIMIT 10;"
     ```
     Before this batch, the only `ui` rows were 5 stale ones at 16:42 UTC (pre-cutover). Success = fresh `click`/`navigate` rows post-deploy.
   - BLOCKER check (Issue 153): load `/app/onboarding` and `/app/walkthrough` on prod; confirm the **Terms + Privacy footer** renders (→ `/static/tos.html` + `/static/privacy.html`).

---

## WHAT WORKS NOW (don't re-investigate)

- **PR #27 CI is fully green:** ruff, mypy/bandit/pip-audit, unit pytest, **integration (postgres+redis)**, coverage ratchet, frontend (lint/test/build), docker smoke build.
- **Frontend locally:** lint clean · vitest **38/38** · vite build green.
- **Batch is frontend + docs ONLY** — zero backend code, zero alembic migrations (`alembic upgrade head` is a no-op on deploy). Low-risk.
- **Issues 153–159 delivered** (full specs + checked acceptance criteria in `docs/issues.md`):
  - 153 BLOCKER — ToS/Privacy footer restored on Onboarding & Walkthrough (was an OAuth-gate breach).
  - 154 SEV1 — Walkthrough CTA + a 2nd `DashboardBanners` fallback no longer dead-end into `/static/onboarding.html`.
  - 155 SEV2 — SPA UI telemetry restored (`lib/activity.ts` + `useActivityTelemetry` mounted via a `RootLayout` in `App.tsx`).
  - 156 SEV3 — false "activity panel" Walkthrough copy fixed.
  - 157 SEV2 — Insights loading state + surfaced swallowed upload-intel/saved errors.
  - 158 SEV2 — account-deletion UI (`DELETE /auth/me`) on Profile; closed the CLAUDE.md launch item.
  - 159 cleanup — orphaned-endpoint sweep triaged (DECISIONS 2026-06-18).
- **Audit dimensions that came back CLEAN** (no need to re-audit): tracing/observability (middleware stack unchanged) and security (no `dangerouslySetInnerHTML`; cache-`no-store` still fires on SPA shell; server-side auth boundary intact). Honesty/"no virality" invariant intact.
- **main and staging were synced** earlier this session (both at `8913ecb`); the 3 stale feature branches were deleted (local + remote).

---

## THE ARC THAT LED HERE

1. User asked to confirm live event-log ingestion → SSH'd to prod, found backend `http_request` rows flowing but **zero post-cutover `ui` rows** → root cause: the React SPA never ported `static/activity.js` (`/api/activity` had no caller).
2. User asked to fix it AND ensure no other regression leaked, via `/issue-workflow` + an assessment.
3. Ran a 6-dimension parallel behavioral-parity audit of the Issue 85 soft cutover → 4 dims surfaced gaps, 2 clean → filed Issues **153–161** in `docs/issues.md`.
4. Worked the batch 153→159 (one issue-workflow each, commit per issue, frontend gates green each time). Two items bigger than a batch fix were split into tracked follow-ups (160, 161).

---

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| PR | **#27** → `main`, OPEN, MERGEABLE, CI green |
| Branch / HEAD | `feature/issue-85-overhaul-regressions` / `afd012b` |
| Trunk | `main` (prod), `staging` (was in sync with main pre-batch) |
| Prod URL | `https://autoclip.studio` (Cloudflare-fronted) |
| Prod host | SSH alias `creatorclip-vm` → `147.182.136.107` (root); compose at `/opt/autoclip/docker-compose.prod.yml` |
| Prod image | `ghcr.io/reese8272/creatorclip:latest` (frontend built into image via multi-stage Dockerfile) |
| Prod DB | container `autoclip-postgres-1`, db/user `creatorclip` (telemetry table: `event_logs`) |
| Deploy trigger | push/merge to `main` → `docker-publish.yml` → `deploy.yml` (self-hosted VM runner) |
| Open follow-ups | **Issue 160** (cross-page active-tasks panel — gated by `MAX_CONCURRENT_SSE_PER_CREATOR=3`); **Issue 161** (repoint stale backend `next_action` `/static` URLs) |
| Telemetry path | `POST /api/activity` (`routers/activity.py`) → `event_log.record_event` → `event_logs` |

---

## CONSTRAINTS & GOTCHAS

- **Merging to `main` auto-deploys to live prod.** Don't merge casually.
- **Backend tests need a real Postgres** (project rule: no DB mocking). This dev env has **Redis only** (`redis-cli ping` works; `pg_isready` absent). That's why **Issue 161** (backend `next_action` URL repoint — touches a tested `NextActionOut` contract across 3 routers) was deferred: validate on CI/DB, not here.
- This batch ships **no migrations** — if a deploy log shows alembic doing work, something else changed.
- The `LEFT_OFF.md` "modified" + the screenshot in `git status` are **pre-existing local noise**, not part of PR #27 (only committed work was pushed).
- Issue 156 was *split*: the trivial copy-fix shipped; the **panel rebuild is Issue 160** (needs a single-EventSource-owner store + refactor of the 4 streaming sites to respect the 3-slot SSE cap — do NOT double-subscribe).
- Prod sits behind Cloudflare Bot Fight Mode — datacenter IPs may get a 403 challenge; verify via SSH/the VM, not a raw curl from CI.

---

## POINTERS (source of truth — do not duplicate here)

- `docs/issues.md` — Issues 153–161 full specs + acceptance criteria (canonical status).
- `docs/PROJECT_STATE.md` — "Current Status" top entry logs this session.
- `docs/DECISIONS.md` — 2026-06-18 entries: the 156→160 split, the 159 triage / 161 split.
- `docs/OFF_COURSE_BUGS.md` — the original telemetry-dark finding (now ✅ Issue 155).
- `docs/SOT.md`, `docs/DEPLOYMENT.md`, `docs/COMPLIANCE.md` — architecture / deploy / ToS.
- `CLAUDE.md` — project rules (read-order, issue workflow, standards).
- Memory: `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` (index `MEMORY.md`).
