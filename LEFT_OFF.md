# LEFT_OFF — session handoff

> **Read this first.** Living "where we are right now" file. Not a changelog, not a source of
> truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-06-22 (Batch A + Issue 182 — all DEPLOYED)
**Branch:** `main` (checked out). **`main` == `staging` == `origin` @ `af1bd14`** (in sync).
Batch A (Issues 181/183/184/185) AND Batch-B Issue 182 (export presets + clip download) are all
built, merged ff staging→main, and **deployed to prod + verified**. Feature branches merged + deleted.
**Latest prod deploy VERIFIED:** deploy run `27976728707` → `success` for sha `af1bd14`;
`autoclip.studio/` → 302 → `/app/dashboard` (healthy, self-hosted VM). Docker-publish ✅.
**Working tree:** clean except this close-out edit (PROJECT_STATE + LEFT_OFF) — commit to both branches.
**Prod:** `https://autoclip.studio`. Now live: Batch-A render behavior (loudnorm always-on; opt-in
caption keyword-highlight / punch-in / denoise) **and** Issue 182 (1:1 + 16:9 export presets, clip
download endpoint, and the **clip-playback fix** — `<video>` now plays via the presigned download
endpoint instead of a dead `s3://` URI).

> ⚠️ **GitHub Actions is OUT OF MINUTES (billing).** Every CI job on `2bb7a76` shows
> *"job was not started because recent account payments have failed or your spending limit needs to be
> increased."* — this is **NOT a code failure**. Until billing is restored, **run the gates locally**
> (recipe in CONSTRAINTS below). They were run locally for `2bb7a76` and are green except one
> pre-existing dependency CVE (see CURRENT FOCUS).

---

## CURRENT FOCUS

**Batch A (render quality) + Batch-B Issue 182 are COMPLETE, DEPLOYED, and VERIFIED in prod.** Ran
the full issue-workflow (CHECK→APPROVE→BUILD→REVIEW) per issue, each with a research-backed brief +
`docs/DECISIONS.md` entry where a deviation existed.
- **Batch A:** **181** always-on two-pass `loudnorm` (−14 LUFS, near-silent guard); **183**
  `bold_pop_highlight` caption style (per-phrase salience scorer, punch-yellow); **184** opt-in
  `zoom_on_peak` punch-in (crop `t`-expression); **185** opt-in `denoise` (`afftdn` before loudnorm).
- **Issue 182:** `OUTPUT_PRESETS` (9:16/1:1/16:9, render-time via `style_preset["aspect"]`, 9:16
  byte-identical); `GET /clips/{id}/download` (presigned R2 / FileResponse, per-creator 404);
  **clip-playback fix** (`<video>` now uses the inline download endpoint, not a dead `s3://` URI).
- Also fixed two latent bugs: DRY worker transcript-load gate (Batch A) + the SEV2 playback bug (182).
Full suite **1024 passed, 3 skipped**; Layer-0 ruff/mypy/bandit/freshness green; frontend lint/tsc/build
+ 38 Playwright e2e green. All on `main`==`staging`==`origin` @ `af1bd14`; latest deploy run
`27976728707` → success.

> ⚠️ **Empirical render checks STILL OWED** — this dev box has no ffmpeg CLI binary (only `libav*`
> libs), so the audio/visual ACs (−14 LUFS via `ebur128`, no-pumping, denoise artifacts, punch-in
> look, keyword legibility, square/16:9 framing, **playback actually plays**) are **verified-by-
> construction in unit/e2e tests only**. Now live in prod → spot-check on a real rendered clip.

### → NEXT ACTION
1. **Commit this close-out** (`LEFT_OFF.md` + `docs/PROJECT_STATE.md`) and keep `main`==`staging`:
   commit on `main`, push, then `git checkout staging && git merge --ff-only main && git push origin
   staging`. (Docs-only → no-op image rebuild + redeploy on the self-hosted VM; harmless.)
2. **Spot-check the shipped work in prod** (the empirical checks above) on a freshly rendered clip —
   incl. that a clip now **plays** in Review and the Download button works.
3. **Next: Batch B publish cluster (paused here).** Issues **194–197** — add the `youtube.upload`
   write scope (incremental re-consent; `[DEC]` + Google-audit launch dependency), `clip_publications`
   table, idempotent `publish_to_youtube` Celery task (pre-audit forced `private`), scheduled publish,
   outcome-loop wiring. DB-backed → build-only verification in this env. Full ACs in finding 13
   (`docs/research/findings/13_*`). Alternatively **Batch C** — Issue 198 (personalization efficacy
   harness, the moat; needs Postgres to verify).
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
