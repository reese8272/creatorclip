# LEFT_OFF — session handoff

> **Read this first.** Living "where we are right now" file. Not a changelog, not a source of
> truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-06-22 (Batch A + 182 DEPLOYED; publish 194/195 + privacy 247–249 on branches)
**Checked out:** `feat/sev1-privacy`.

**LIVE on prod** (`main` == `staging` == `origin` @ `3aa95d7`, verified): Batch A (181/183/184/185 —
render quality) + Issue 182 (1:1/16:9 export presets, `GET /clips/{id}/download`, clip-playback fix).

**TWO FEATURE BRANCHES IN FLIGHT (committed, NOT merged, NOT deployed):**
- **`feat/batch-b-publish`** (@ `99040a9`) — Issues **194** (`youtube.upload` opt-in incremental
  consent) + **195** (`publish_to_youtube` resumable upload task + `clip_publications` table, migration
  **0027**, idempotent, forced-private). **HELD off prod until verified** on a real DB + YouTube sandbox.
- **`feat/sev1-privacy`** (@ `49df3e6`, current) — Issues **247** (audit PII leak), **248** (purge
  `event_logs` on deletion), **249** (Art. 15/20 data-export, migration **0027**). SEV1 launch-blockers.

> ⚠️ **MIGRATION COLLISION:** both branches add a `0027`. Privacy = `0027_data_exports`, publish =
> `0027_clip_publications`. Whichever merges **second** must be renumbered to `0028` (down_revision =
> the first one's `0027`) or alembic gets two heads.

> ⚠️ **DB-heavy work is MOCK-VERIFIED ONLY** here (Redis-only box). All four issues' migrations, RLS,
> and real upload/aggregation run for the first time on a staging/prod deploy. Verify there before trusting.

> ⚠️ **GitHub Actions is OUT OF MINUTES (billing).** Every CI job on `2bb7a76` shows
> *"job was not started because recent account payments have failed or your spending limit needs to be
> increased."* — this is **NOT a code failure**. Until billing is restored, **run the gates locally**
> (recipe in CONSTRAINTS below). They were run locally for `2bb7a76` and are green except one
> pre-existing dependency CVE (see CURRENT FOCUS).

---

## CURRENT FOCUS

**Shipping push, this session.** Each issue ran the full workflow (CHECK→APPROVE→BUILD→REVIEW) with a
research-backed brief + `docs/DECISIONS.md` entry. Status by track:

- ✅ **LIVE: Batch A (181/183/184/185)** render quality + **Issue 182** export presets/download +
  clip-playback fix. Deployed + verified @ `3aa95d7`. Also fixed 2 latent bugs (DRY worker gate; SEV2
  playback).
- 🟡 **BRANCH (held): publish 194 + 195** on `feat/batch-b-publish`. Opt-in `youtube.upload` incremental
  consent + resumable `publish_to_youtube` task + `clip_publications` (migration 0027) + idempotency +
  forced-private. Verified-by-mocks; **needs real DB + YouTube sandbox** before prod. `videos.insert`
  quota re-verified (1600→100, Dec 2025). **196 (scheduled publish) + 197 (outcome-loop) NOT done.**
- 🟡 **BRANCH: SEV1 privacy 247 + 248 + 249** on `feat/sev1-privacy`. Deletion PII leak fixed; `event_logs`
  purged on deletion; Art. 15/20 JSON data-export (migration 0027). All GDPR launch-blockers. Mock-verified.

Full suite green at each step (latest **1033 passed, 3 skipped**); Layer-0 + frontend (lint/tsc/build +
38 e2e serial) green.

> ⚠️ **Empirical/real-env checks OWED** (this box is Redis-only, no ffmpeg CLI, no Postgres, no Google):
> render audio/visual ACs (−14 LUFS/pumping/denoise/punch-in/captions); the **two 0027 migrations + RLS**;
> a real `videos.insert` upload; the export aggregation + cross-tenant isolation. All run first on staging.

### → NEXT ACTION (decisions pending — get user go-ahead)
1. **Merge order + migration renumber.** Decide which branch lands on main first. The SECOND branch must
   renumber its `0027_*` migration to `0028` (down_revision = the first's `0027`). Privacy is the more
   verifiable/launch-critical set; publish is held for sandbox verification — so **privacy first** is the
   natural order (then publish's `clip_publications` → 0028).
2. **Verify on a DB env before prod.** Both branches' migrations/RLS/isolation + publish's upload need a
   real Postgres (+ YouTube sandbox for publish). No live staging-verify path right now (CI billing dead;
   staging push doesn't auto-deploy) — so verification likely = the prod deploy itself (runs `alembic
   upgrade`) or a manual DB run. Treat the first deploy as the verification gate; watch closely.
3. **Finish the publish cluster:** 196 (scheduled publish — extends `clip_publications` with
   `scheduled_at`/`platform` + beat sweep + UI) and 197 (wire published clips into the outcome loop).
4. **Follow-ups:** a Profile "Download my data" button for 249 (endpoint exists, no UI yet); publish UI
   (the connect-publishing button exists, but no "publish this clip" action — comes with 196).
5. **Spot-check the LIVE render work** (Batch A + 182) on a real clip in prod (empirical checks above).
6. **Still open: msgpack CVE** (`OFF_COURSE_BUGS.md`) — pin `msgpack>=1.2.1` or ignore-list. Untouched.
4. **Still open: the dependency CVE** (logged in `docs/OFF_COURSE_BUGS.md`, 2026-06-22): msgpack 1.1.2
   `GHSA-6v7p-g79w-8964` → pin `msgpack>=1.2.1` or add to the Issue-107 accepted-risk ignore-list with
   a DECISIONS note. (Untouched this session.)

## WHAT WORKS NOW (verified this session — don't re-investigate)

- **Local CI replication is GREEN** on `2bb7a76` (run because GH Actions is out of minutes):
  - `ruff check` ✅ · `ruff format --check` ✅ (241 files) · **unit tests ✅ 992 passed, 3 skipped** ·
    `mypy` ✅ 0 · `bandit` ✅ 0 high/0 med · freshness ✅
  - **Frontend** ✅ — `npm run lint` clean, `npm test` 45 passed (15 files), `npm run build` ok.
  - `pip-audit` — 1 non-baselined CVE: **msgpack** `GHSA-6v7p-g79w-8964` (transitive via librosa;
    pre-existing, not from this commit; low exposure). pytest CVE-2025-71176 is already ignore-listed.
  - **Not run locally:** integration tests (need Postgres+pgvector — only Redis is available here) and
    the coverage-floor gate. A docs-only commit cannot affect them; last green was Issue 164/165.
- **The commit is docs-only** — `requirements.txt`, `pyproject.toml`, and all source/frontend files
  were untouched (verified via `git show --stat`).
- **Branches are clean & synced** — only `main` + `staging` remain, both @ `2bb7a76` = `origin`.

## THE ARC THAT LED HERE

1. Prior sessions delivered the React/TS overhaul + the Playwright harness (Issues 162–165) and then
   authored 15 read-only research-agent prompts over the conceptual gaps (Issues 166–180).
2. **This session:** the user ran `/issue-workflow` to turn the research into a backlog. The 15 findings
   had already been produced (`docs/research/findings/`) — each proposing concrete implementation issues.
3. Fanned out parallel readers to extract every proposed issue + every open question; mapped the full
   done/open status of Issues 1–180; asked the founder the 4 genuine product-scope questions.
4. Founder decided: **stream-VOD recap = expand v1 now**; **publishing = export + YouTube publish**
   (TikTok/Reels deferred); **multilingual = English-only v1** (i18n deferred); **editor = full timeline tool**.
5. Archived finished work, rebuilt `issues.md` (181–274, deduped + prioritized), logged decisions,
   committed, promoted to main+staging, deleted the feature branch, verified gates locally.

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Repo | `github.com/reese8272/creatorclip` |
| Prod URL | `https://autoclip.studio` (Cloudflare-fronted; React SPA at `/app`) |
| Branches | `main` (live/deploys) + `staging` (pre-prod), kept fast-forward-identical. Both @ `2bb7a76` |
| Backlog | `docs/issues.md` — rebuilt; Issues 181–274 (new) + carry-over open + deferred parking lot |
| Research findings | `docs/research/findings/01–15` — full ACs + evidence + draft DECISIONS per issue (the `Src:` of each new issue) |
| Archive | `docs/archive/issues_snapshot_2026-06-22.md` (old full backlog), `off_course_bugs_snapshot_2026-06-22.md`, `research_prompts_2026-06-22/` |
| venv | `.venv` (Python 3.12.7) — has ruff 0.15.15, mypy, pytest, bandit, pip-audit (matches CI) |
| Redis | local: `redis-server --daemonize yes --save '' --appendonly no` (tests fail-fast without it) |
| Layer-0 gates | `python3 .claude/skills/production-assessment/scripts/run_layer0.py --gates ...` |
| pip-audit ignore-list | `pyproject.toml [tool.pip-audit]` + `run_layer0.py PIP_AUDIT_IGNORES` (keep in lockstep) |
| Deploy | push to `main` → `docker-publish.yml` (image) → `deploy.yml` (self-hosted VM) |
| CI | `.github/workflows/ci.yml` — **currently red due to GH Actions billing, not code** |

## CONSTRAINTS & GOTCHAS

- **GH Actions = out of minutes.** Red CI is a billing artifact, not a code failure. Verify locally
  (recipe below) until the user restores billing. Don't chase the red checks.
- **Local CI recipe** (from repo root): `source .venv/bin/activate` → start Redis (above) →
  `ruff check . && ruff format --check .` → `pytest --tb=short -q` → `python3 .claude/skills/production-assessment/scripts/run_layer0.py --gates ruff,mypy,bandit,pip_audit,freshness` →
  frontend: `cd frontend && npm run lint && npm test && npm run build`.
  (The trailing `RuntimeError: Event loop is closed` after pytest is harmless redis-asyncio shutdown noise.)
- **Integration tests need a real Postgres + pgvector** (no DB mocking rule); this env has **Redis only**.
- **Pushing to `main` triggers a prod deploy** and is gated — get explicit user go-ahead. `main` and
  `staging` are kept byte-identical via fast-forward only (never merge-commit between them).
- **Every new issue's full spec is in its finding**, not in `issues.md` (which is a condensed tracker).
  Always open the `Src:` finding before building.
- **~40 of the new issues carry a `[DEC]` flag** — they need a `docs/DECISIONS.md` entry at build time
  (the 4 scope expansions especially); draft entries already exist in the findings.

## POINTERS (sources of truth — do not duplicate here)

- `docs/issues.md` — the rebuilt backlog (priority-ordered; start at Issue 181).
- `docs/research/findings/01–15` — full acceptance criteria + `file_path:line` evidence per new issue.
- `docs/research/README.md` — findings → filed-issues index.
- `docs/DECISIONS.md` — **2026-06-22** entry: the 4 scope decisions + backlog rebuild rationale.
- `docs/OFF_COURSE_BUGS.md` — 4 open items (httpx2, dashboard N+1, flow timeout, **msgpack CVE**).
- `docs/PROJECT_STATE.md` — top "Last completed" entry covers the rebuild; Issues 166–180 marked done.
- `docs/SOT.md`, `docs/BRANCHING.md`, `CLAUDE.md` — stack/branch model/project rules.
- Memory: `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` (index `MEMORY.md`).
