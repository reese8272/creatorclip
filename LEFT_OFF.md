# LEFT_OFF — session handoff

> **Read this first.** Living "where we are right now" file. Not a changelog, not a source of
> truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-06-23 · **Checked out:** `main` @ `333461c` · **Working tree:** clean.
`main` == `staging` == `origin/main` == `origin/staging` @ `333461c`.

**This session (planning-only, no product code):** rebuilt `docs/issues.md` into the **Master Roadmap
to Production** and committed + **DEPLOYED** it (docs-only; deploy run `27996005160` → success).

---

## CURRENT FOCUS

**The roadmap is live; the next job is to START EXECUTING it.** `docs/issues.md` is now a
dependency-ordered execution plan: every open issue (181–303 + carry-over, **138 open**) has three
coordinates — **Wave** (W0–W5 dependency round), **Lane** (one of 19 file-disjoint subsystem owners that
run in parallel), **Batch** (per-wave parallel deployment unit) — plus an execution-ready brief
(source-verified files-to-touch, testable ACs, Blocked-by/Enables, `[DEC]`, verify-path, tests, risks).
**29 research-derived issues (275–303)** were added (founder-approved, tagged 🧪 in the file) and **13
sourced `[DEC]` recommendations** folded into briefs.

### → NEXT ACTION
1. **Pick the first wave to execute.** Open `docs/issues.md` → "Master plan — Lane × Wave matrix".
   **Wave 0 is startable today.** Deploy one agent per lane that has W0 issues; hand it that lane's
   brief(s); run lanes in parallel; respect each issue's **Blocked by** line + the **Hot-file
   coordination protocol** (esp. `worker/tasks.py` — 22 issues / 13 lanes; and the Alembic chain).
2. **Highest-leverage early picks:** **Issue 275** (GKE staging + first Helm deploy) — the linchpin that
   makes ~40 `staging`/`external` issues verifiable; **Issue 198** (personalization-efficacy harness —
   the moat, unblocks 199–202); **Track A** env gates 24/25/26 in parallel.
3. **Per issue, run `/issue-workflow N`** (CHECK→APPROVE→BUILD→REVIEW). The brief is condensed; open the
   `Src:` finding in `docs/research/findings/` for full ACs/evidence. `[DEC]` issues need a
   `docs/DECISIONS.md` entry at build (draft entries already exist in the findings/research).
4. **Held, separate from the roadmap:** merge `feat/batch-b-publish` (Issues 194/195) when a real DB +
   YouTube sandbox verification path exists — see KEY COORDINATES.

## WHAT WORKS NOW (verified — don't re-investigate)

- **Master roadmap committed + deployed** @ `333461c`. Deploy run `27996005160` → **success** (36s);
  Docker publish `27995964548` → success (1m2s). `autoclip.studio` was healthy after the prior deploy.
- **Roadmap adversarially validated:** zero dependency-order violations; **513/524 cited file paths
  confirmed real** (the 11 others are files-to-create); all 138 open issues reachable as `### Issue N:`;
  all carry acceptance criteria (the `/issue-workflow` + `/close-out` contract holds).
- **LIVE on prod (`main`):** Batch A (181/183/184/185 render quality), Issue 182 (1:1/16:9 export +
  `GET /clips/{id}/download` + clip-playback fix), and the **SEV1 privacy track 247/248/249** (audit PII
  leak fix, `event_logs` purge on deletion, Art. 15/20 data export — migration `0027_data_exports`).
- **Doc-set corrected for the "K8s research pending" staleness** — `CLAUDE.md`, `docs/SOT.md`,
  `docs/README.md`, `docs/DEPLOYMENT.md`, `docs/DECISIONS.md`, `docs/PROJECT_STATE.md` all updated.

## THE ARC THAT LED HERE

1. Prior sessions: React/TS overhaul + Playwright harness (162–165); 15 gap-closure research findings
   (166–180); a first backlog rebuild (181–274); Batch A + 182 + privacy 247–249 shipped to prod.
2. **This session:** founder asked for ONE execution-ready source of truth so agents can be deployed in
   conflict-minimized parallel batches all the way to prod, and to "research further" for gaps.
3. Ran a 16-agent **source-verified extraction** of every open issue + a 6-dimension **production-gap
   research** pass (deploy-arch, open `[DEC]`s, SRE completeness, launch sequence, legal/compliance,
   cost-at-scale) → +29 issues, +13 decisions, and the "K8s chart already exists" finding.
4. Synthesized dependency **waves + file-disjoint lanes + batches**, deterministically generated the
   verbose `issues.md`, adversarially validated, fixed the findings, corrected the docs, committed +
   pushed to main+staging, and verified the deploy succeeded.

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Repo | `github.com/reese8272/creatorclip` |
| Prod URL | `https://autoclip.studio` (Cloudflare-fronted; React SPA at `/app`) |
| Branches | `main` (live/deploys) + `staging` (pre-prod), kept fast-forward-identical. Both @ `333461c` |
| **Backlog / roadmap** | `docs/issues.md` — **Master Roadmap** (Wave/Lane/Batch + briefs). Prior backlog archived `docs/archive/issues_pre_roadmap_2026-06-22.md` |
| Held publish branch | `feat/batch-b-publish` @ `79a5562` (Issues 194/195; migration already renumbered `0027`→`0028_clip_publications`). NOT merged; needs real DB + YouTube sandbox. |
| Alembic head on main | `0027_data_exports` — next migration is `0028`; **publish branch + roadmap Issue 190 both want `0028`** → assign at merge (hot-file protocol) |
| Research findings | `docs/research/findings/01–15` — full ACs + `file_path:line` evidence (the `Src:` of each issue) |
| New issues | 275–303 (🧪 RESEARCH-DERIVED, founder-approved) — supply-chain, pod resilience, error-tracking, feature flags, WAF/edge, Redis persistence, spend caps, GO_LIVE checklist, clickwrap/COPPA/a11y/GPC |
| K8s artifacts | Helm chart `deploy/charts/creatorclip/` (GKE Autopilot + Cloud SQL PG16 + KEDA) — **written, never run on a real cluster** (Issue 275) |
| venv | `.venv` (Python 3.12.7) — ruff/mypy/pytest/bandit/pip-audit (matches CI) |
| Redis (local tests) | `redis-server --daemonize yes --save '' --appendonly no` (tests fail-fast without it) |
| Deploy | push to `main` → `docker-publish.yml` (image) → `deploy.yml` (self-hosted VM) |

## CONSTRAINTS & GOTCHAS

- **GH Actions CI is RED on billing, not code.** Every push shows a 5–6s fast-fail ("recent account
  payments have failed / spending limit"). The same red appears on prior commits. The real deploy runs
  on the self-hosted VM (Docker-publish → Deploy-to-production) and is **unaffected** — it succeeded this
  session. Don't chase the red checks; verify locally until billing is restored.
- **Pushing to `main` triggers a prod deploy** (gated — get explicit user go-ahead). `main` and `staging`
  are kept byte-identical via fast-forward only.
- **This dev box is Redis-only** — no Docker, Postgres, ffmpeg CLI, or live APIs. ~40 issues are
  `staging`/`render-env`/`external` verify-only; their code is unit-tested here but load-bearing ACs run
  first on staging. **Issue 275 (GKE staging) is what makes those verifiable.**
- **Hot-file coordination:** `worker/tasks.py` is the #1 contention point (22 issues / 13 lanes); see the
  protocol table in `issues.md`. Alembic migrations share one linear chain — assign revision numbers at
  merge, never author two in parallel against the same head.
- **`worker/tasks.py` early refactor** (split per-stage) would reduce churn before the heavy L02/L08/L09
  waves — flagged in the roadmap, not yet an issue.
- **Open `[DEC]` to settle at build:** whether prompt caching stacks inside Anthropic Batch mode (Issue
  219) — research split; confirm via the spike.
- **Still open (`docs/OFF_COURSE_BUGS.md`):** msgpack CVE `GHSA-6v7p-g79w-8964` (pin `>=1.2.1` or
  ignore-list); TestClient httpx2 deprecation (Issue 274); dashboard N+1 (Issue 213); flow-test timeout.

## POINTERS (sources of truth — do not duplicate here)

- `docs/issues.md` — the Master Roadmap. Start at the "How to use this file" + Lane×Wave matrix.
- `docs/research/findings/01–15` — full ACs + evidence per issue (the `Src:` link).
- `docs/DECISIONS.md` — **2026-06-22** top entry: roadmap rebuild + 29 new issues + K8s-stale correction.
- `docs/PROJECT_STATE.md` — top entry covers this session's rebuild.
- `docs/SOT.md`, `docs/DEPLOYMENT.md`, `CLAUDE.md` — stack / deploy model / project rules (now K8s-current).
- `docs/OFF_COURSE_BUGS.md` — open incidental defects.
- Memory: `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` (index
  `MEMORY.md`; see `project_master_roadmap.md`).
