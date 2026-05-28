# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a source of
> truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-05-28 (PM session 2 — six-agent parallel batch)
**Branch:** `main`
**Working tree:** dirty — about to commit batch + docs + push
**Ahead/behind origin/main:** **+9 / -0** unpushed (Issues 36, 50, 54 + 4 merge commits + this docs commit)
**Production:** green on `origin/main` (`b282786`); unpushed work has not deployed yet

---

## 1. CURRENT FOCUS

**Push the 9-commit batch (Issue 36 + Batch 1 of six parallel agents) to `origin/main`
and confirm the deploy.**

Issues 36, 37, 45, 48, 50, 53, 54 are all merged on local `main` with the full suite
green (**349 passed, 1 skipped, 37 deselected**, was 326 → +23 net). Six parallel agents
ran in isolated worktrees, each closed its issue cleanly, and the merges resolved without
conflict (one auto-merged 3-way overlap on `docs/DECISIONS.md` between Issues 45 and 37).

### → NEXT ACTIONS (in order)

**1. Push + watch deploy.**

```bash
git push origin main           # triggers CI + Deploy to production
gh run watch                   # or: gh run list --branch main --limit 8
curl -fsS https://autoclip.studio/health
```

**2. Clean up the 4 agent worktrees + branches.**

```bash
for wt in agent-a02705a60354e96b1 agent-aa3da9fd2d7ebd988 \
          agent-a79712a3fdaa7f0ba agent-a93a13e675c3e7eb0; do
  git worktree remove --force ".claude/worktrees/$wt"
done
git branch -D worktree-agent-a02705a60354e96b1 \
              worktree-agent-aa3da9fd2d7ebd988 \
              worktree-agent-a79712a3fdaa7f0ba \
              worktree-agent-a93a13e675c3e7eb0
```

**3. Start Batch 2 (3 parallel) OR Batch 3 (serial worker/tasks.py).**

Per `docs/issues.md` and the dependency map:

- **Batch 2** (parallel, ~3 agents): Issues **49** (billing integration tests, depends on
  34 ✅), **51** (OAuth lifecycle tests — expand the existing file, depends on 36 ✅),
  **55** (bundled load-bearing test gaps — appends to many existing test files).
- **Batch 3** (serial, must NOT parallelize — all touch `worker/tasks.py` and/or
  `models.py` migrations): Issues **39** (Celery event-loop strategy), **43**
  (`ingest_done_at` column + purge filter), **46** (generate-clips retry safety + outcomes
  time-window), **47** (`last_analytics_refreshed_at` + beat fairness), **57** (Celery
  on_failure refund hook). Recommendation: do 39 first (event-loop pattern affects how
  the rest of the tasks are structured), then 43+47 in one PR (both add columns), then
  46 + 57 in their own PRs.
- **Batch 4 (blocked)**: 38 needs 37 ✅ (so now unblocked, can go after Batch 3),
  52 needs 39 (blocked), 56 needs 48 ✅ (now unblocked — research issue, low priority).

---

## 2. WHAT WORKS NOW (do not re-investigate)

- ✅ **Phase 2 hardening Issues 32, 33, 34, 35, 36, 37, 40, 41, 42, 44, 45, 48, 50, 53, 54**
  — 15 closed; full test suite green (**349 passed, 1 skipped, 37 deselected**).
- ✅ **SDK clients are module-level singletons with production-grade timeouts** —
  Anthropic, Stripe, Voyage, boto3. No more per-call client construction in hot paths.
- ✅ **Concurrent refresh is race-safe** — per-creator Redis lock with Lua release.
- ✅ **Redis pool is a process-wide singleton** via `youtube/_redis.py::get_redis_client()`.
- ✅ **Every protected route has an isolation test** (14 routes; zero SEV-0 findings).
- ✅ **Account-deletion cascade verified across all 17 dependent tables**.
- ✅ **Honesty constraint enforced structurally** — OpenAPI + static + schema scan.
- ✅ **`scripts/rotate_token_key.py` has integration coverage** — happy path + rollback +
  no-plaintext-in-logs.
- ✅ **Beta deploy pipeline** — green on `origin/main` (`b282786`); production health
  `https://autoclip.studio/health` returning ok/ok/ok.
- ✅ **6 parallel agents merged + suite green after every merge** — repeatable pattern.

---

## 3. THE ARC THAT LED HERE

1. Phase 1 + earlier Phase 2 batch closed previously (Issues 1–31, 32–35, 40–42, 44).
2. **Issue 36 (PM session 1)** — three-prong OAuth lifecycle fix (revoke refresh,
   invalid_grant row deletion, reason-based 403 classification). Pushed at `b282786`.
3. **Batch 1 (PM session 2, this session)** — six parallel agents in isolated worktrees:
   - Issue 37 (SDK timeouts + singletons + tenacity)
   - Issue 45 (refresh lock + Redis pool)
   - Issue 48 (14 isolation tests)
   - Issue 50 (cascade tests)
   - Issue 53 (no-virality scan + rename)
   - Issue 54 (rotate_token_key tests)
4. Two agents (50, 54) leaked changes into main's working tree instead of their
   isolated worktrees — committed directly. The other four were merged via
   `git merge --no-ff worktree-agent-<id>`. One auto-merged 3-way conflict on
   `docs/DECISIONS.md` resolved cleanly (both Issue 45 and Issue 37 appended entries).

---

## 4. KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| **Public URL** | `https://autoclip.studio` |
| **VM** | `147.182.136.107` — Ubuntu 24.04, 4 vCPU / 8 GB, NYC1 |
| **SSH alias** | `ssh creatorclip-vm` |
| **Deploy dir on VM** | `/opt/autoclip/` |
| **Active tunnel** | `autoclip-prod` (`db79b904-9cbf-4a79-b336-3b8195e6d37b`) |
| **R2 bucket** | `creatorclip-beta` |
| **Docker image** | `ghcr.io/reese8272/creatorclip:latest` |
| **Test runner** | **`.venv/bin/python -m pytest -q`** |
| **Active issue** | Batch 2 (49, 51, 55) — parallel-safe; OR Batch 3 (39, 43, 46, 47, 57) — serial worker/tasks.py |
| **Last completed** | Batch 1 — Issues 37, 45, 48, 50, 53, 54 (2026-05-28 PM session 2) |
| **Issues left** | 11 hardening issues + 2–3 Phase 1 external-process-bound items |

---

## 5. CONSTRAINTS & GOTCHAS

- **Two agents in this session leaked into main's working tree** (50, 54), as happened
  with Issues 41/42 in the prior session. The Agent tool's `isolation: "worktree"` only
  isolates the working directory — if the agent passes absolute paths or `cd`s back to
  the primary worktree, changes land there. Detection: `git worktree list` shows fewer
  worktrees than agents launched. Treatment: commit the leaked files in main as a
  separate commit per-issue; no merge needed.
- **Auto-merge on `docs/DECISIONS.md` worked cleanly** when both Issue 45 and Issue 37
  branches appended new entries below the same base (Issue 36's entry). Git's `ort`
  strategy handled the 3-way append as a non-conflict. If ever it doesn't, the
  resolution is: keep both blocks, in any order.
- **System `python3.12` cannot run pytest** — always use `.venv/bin/python`.
- **Pushing to `main` triggers CI + production deploy.**
- **OAuth "disconnected" = YoutubeToken-row absence** (no enum value, no migration).
- **`importlib.reload(config)` is poison in tests** — use `monkeypatch.setattr(settings, ...)`.
- **Module-level Redis singleton is now at `youtube/_redis.py::get_redis_client()`** —
  Issues 39, 47, 57 should reuse it instead of constructing their own.
- **tenacity is now a direct dep** (`tenacity==9.1.4`) for SDK retry wrappers.

---

## 6. POINTERS

| Doc | Purpose |
|---|---|
| `docs/PROJECT_STATE.md` | Issue table + current status — Phase 2 hardening 15/26 done |
| `docs/issues.md` | Full issue backlog — Batches 2/3/4 mapped above |
| `docs/DECISIONS.md` | Architectural decisions — 2026-05-28 entries for Issues 32–37, 40–42, 44, 45 |
| `docs/SOT.md` | Architecture + data model |
| `docs/COMPLIANCE.md` | YouTube ToS + Findings & Fixes Log |
| `docs/SECRETS.md` | Every secret |
| `.github/workflows/deploy.yml` | CD pipeline — currently green |
| `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/MEMORY.md` | Auto-memory index |
