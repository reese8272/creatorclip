# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a source of
> truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-05-28 (PM session 3 close-out)
**Branch:** `main` — HEAD `97f6475`
**Working tree:** clean (one untracked `.claude/` dir, gitignored)
**Sync with `origin/main`:** **0 / 0** (fully pushed)
**Production:** ✅ green on `autoclip.studio` (last successful deploy at `6be61fb`, 23:45 UTC)

---

## 1. CURRENT FOCUS

**Batch 2 of Phase 2 hardening is shipped; the green-CI stamp on the most recent commit
(`97f6475`) is still pending because GitHub Actions runners are queue-starved.**

`97f6475` is a TEST-ONLY fix for Issue 49's webhook integration tests (greenlet bug —
test infra only, no source change). Production is already running the correct source.

### → NEXT ACTION

1. **Verify the queued CI eventually picks up (or accept it as wedged).**
   ```bash
   gh run list --branch main --limit 4
   ```
   - If the 3 runs on `97f6475` (CI / Integration tests / Docker publish) have completed
     and Integration tests is **success** — done. Nothing to do.
   - If still `queued` after ≥2h: most likely cause is **free-tier GitHub Actions
     minutes cap** (we ran ~25 CI cycles today). Check
     `github.com/settings/billing/summary` in browser; reset on the next billing cycle.
   - If `failure` on Integration tests: read `gh run view <id> --log-failed | tail -60`.
     The fix touches `tests/test_billing_integration.py`; the pattern is "fresh
     `AsyncSessionLocal()` for verification, never share `db_session` with TestClient."

2. **Confirm production health.**
   ```bash
   curl -fsS https://autoclip.studio/health     # expect {"status":"ok","postgres":"ok","redis":"ok"}
   ```

3. **Clean up the 7 locked agent worktrees** (deferred from this session — harness
   process-locked them; should be GC'd by now but verify):
   ```bash
   for wt in agent-a02705a60354e96b1 agent-a0485b2967d2669d0 agent-a79712a3fdaa7f0ba \
             agent-a82d5d6d0a8b1ff37 agent-a93a13e675c3e7eb0 agent-a95d613a38698708c \
             agent-aa3da9fd2d7ebd988; do
     git worktree remove -f -f ".claude/worktrees/$wt" 2>/dev/null || true
   done
   git branch -D worktree-agent-a02705a60354e96b1 worktree-agent-a0485b2967d2669d0 \
                  worktree-agent-a79712a3fdaa7f0ba worktree-agent-a82d5d6d0a8b1ff37 \
                  worktree-agent-a93a13e675c3e7eb0 worktree-agent-a95d613a38698708c \
                  worktree-agent-aa3da9fd2d7ebd988 2>/dev/null || true
   git worktree prune
   ```

4. **Pick the next batch of work.**
   - **Batch 3 (serial — all touch `worker/tasks.py` and/or `models.py` migrations):**
     Issues **39** (Celery event-loop strategy — foundational, do first),
     **43** (`Video.ingest_done_at` + purge filter, migration),
     **47** (`Creator.last_analytics_refreshed_at` + beat fairness, migration),
     **46** (generate-clips retry safety + outcomes time-window bug),
     **57** (refund on terminal ingest failure — needs Phase 1 policy decision).
     43 + 47 can share a single alembic revision if you want.
   - **Batch 4 (unblocked now):** **38** (sync-in-async + held DB sessions — needed 37 ✅),
     **56** (Postgres RLS evaluation — needed 48 ✅; research-and-decide issue),
     **52** (worker pipeline integration tests — still blocked on 39).

---

## 2. WHAT WORKS NOW (do not re-investigate)

- ✅ **18 of 26 Phase 2 hardening issues closed**: Issues 32–37, 40–45, 48–51, 53–55.
- ✅ **Test suite green locally**: `362 passed, 1 skipped, 41 deselected` (was 326 at
  session start → +36 net this session across two parallel batches + Issue 36).
- ✅ **Ruff clean** across the repo.
- ✅ **Production health verified**: `{"status":"ok","postgres":"ok","redis":"ok"}` on
  `autoclip.studio`. Last successful deploy at `6be61fb` (23:45 UTC); `97f6475` source
  is identical (test-only delta).
- ✅ **OAuth lifecycle** (Issues 36, 45, 51): refresh-token revocation, invalid_grant
  row deletion, reason-based 403 classification, per-creator refresh lock with Lua
  release, scope set verified exact (no `youtube.upload`), no token plaintext in logs.
- ✅ **SDK singletons + timeouts** (Issue 37): Anthropic, Stripe, Voyage, boto3 all
  module-level singletons with production-grade timeout + retry config. `tenacity==9.1.4`
  added to requirements.
- ✅ **Redis singleton** at `youtube/_redis.py::get_redis_client()` shared by oauth + quota.
- ✅ **Compliance scan** (Issue 53): structural no-virality scan across OpenAPI bodies,
  static assets, schema descriptions. Codebase clean.
- ✅ **Isolation tests** (Issue 48): 14 protected routes verified; zero SEV-0 findings.
- ✅ **Cascade tests** (Issue 50): all 17 dependent tables verified.
- ✅ **Billing integration tests** (Issue 49): concurrent race + webhook idempotency
  + unknown-pack + missing-metadata covered against real Postgres.
- ✅ **Bundled load-bearing gaps closed** (Issue 55): 9 surgical tests + 1 adversarial
  YAML scenario (`loud_aftermath.yaml`).
- ✅ **`scripts/rotate_token_key.py` coverage** (Issue 54): happy path + rollback +
  no-plaintext.
- ✅ **Two real test-infra bugs caught + fixed at merge time** (kept out of prod):
  TestClient cookie jar leakage in OAuth callback test; SQLAlchemy `MissingGreenlet`
  from sharing a session across event loops in webhook tests.

---

## 3. THE ARC THAT LED HERE

1. **Phase 1 (Issues 1–31)** closed in earlier sessions; beta live on `autoclip.studio`.
2. **Earlier Phase 2 batch** (Issues 32–35, 40–42, 44) closed in prior sessions.
3. **2026-05-28 PM session 1** — Issue 36 OAuth lifecycle (3-prong fix: revoke refresh,
   invalid_grant row deletion, 403 reason classification). Shipped at `b282786`.
4. **2026-05-28 PM session 2 (Batch 1)** — 6 parallel agents in isolated worktrees
   closed Issues 37, 45, 48, 50, 53, 54. CI failure on first push (rotate_token_key test
   assumed empty table) fixed in `c2ff63b`.
5. **2026-05-28 PM session 3 (Batch 2, this session)** — 3 parallel agents closed
   Issues 49, 51, 55. Two real bugs caught during merge:
   - Issue 51's callback test set `cc_session` cookie in TestClient's session-scoped
     jar, breaking `test_static::test_list_videos_requires_auth`. Fixed in `ad74b5f`.
   - Issue 49's webhook tests shared `db_session` across event loops →
     `MissingGreenlet`. Fixed in `97f6475`.

---

## 4. KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| **Public URL** | `https://autoclip.studio` |
| **Health endpoint** | `https://autoclip.studio/health` |
| **VM** | `147.182.136.107` — Ubuntu 24.04, 4 vCPU / 8 GB, NYC1 |
| **SSH alias** | `ssh creatorclip-vm` |
| **Deploy dir on VM** | `/opt/autoclip/` |
| **Active Cloudflare tunnel** | `autoclip-prod` (token in `/opt/autoclip/.env`) |
| **R2 bucket** | `creatorclip-beta` |
| **Docker image** | `ghcr.io/reese8272/creatorclip:latest` |
| **GitHub repo** | `github.com/reese8272/creatorclip` (private) |
| **App secrets on VM** | `/opt/autoclip/.env` (chmod 600 — see `docs/SECRETS.md` for the key list) |
| **GH Actions secret names** | `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, `VPS_PORT`, `GHCR_TOKEN`, `PRODUCTION_URL` |
| **Test runner** | **`.venv/bin/python -m pytest -q`** (system `python3.12` is broken — see Gotchas) |
| **Active issue** | _(none in flight)_ — Batch 3 (serial: 39, 43, 46, 47, 57) queued next |
| **Last completed** | Batch 2 — Issues 49, 51, 55 (2026-05-28 PM session 3) |
| **Phase 2 progress** | 18 of 26 hardening issues closed |
| **Test count** | 362 passed, 1 skipped, 41 deselected (403 collected) |

---

## 5. CONSTRAINTS & GOTCHAS

- **GitHub Actions free-tier minutes may be capped.** 3 runs on `97f6475` have been
  `queued` for 37+ min. If usage is the cause, monthly reset clears it; otherwise
  GitHub capacity. Prod is already deployed on equivalent source, so this is
  cosmetic-only.
- **System `python3.12` cannot run pytest** — uv-managed Python has pydantic 2.46.4
  but user-site has pydantic-core 2.27.2 → `SystemError` at plugin load. Always use
  `.venv/bin/python`. Fix (deferred):
  `python3.12 -m pip install --user --break-system-packages "pydantic-core>=2.46.4"`.
- **Pushing to `main` triggers CI + production deploy.** Deploy is gated on Docker
  publish success — NOT integration tests. So a test failure won't block production
  if Docker publish passes. (Considered for a future hardening issue.)
- **TestClient cookie jar is session-scoped** (`@pytest.fixture(scope="session")` in
  `tests/conftest.py`). Any test that completes an OAuth callback / login / cookie-
  setting flow MUST call `client.cookies.clear()` in teardown.
- **SQLAlchemy 2.0 async sessions cannot cross event loops.** TestClient runs handlers
  in its own loop — never share the test's `db_session` (an `AsyncSession`) with a
  TestClient request via `dependency_overrides`. Pattern: let production `get_session()`
  build a fresh `AsyncSessionLocal`-backed session per request; for post-call
  verification open a separate `async with AsyncSessionLocal() as ...:` in the test's
  loop.
- **`app.dependency_overrides.clear()` (project convention) wipes ALL overrides.** This
  works as long as tests are ordered such that one test's setup compensates for
  another's teardown. A correctly-cleaning test can EXPOSE upstream pollution.
  Surgical alternative: `app.dependency_overrides.pop(KEY, None)`.
- **OAuth "disconnected" = `YoutubeToken` row absence** (no `OnboardingState` enum
  value; no migration). See `docs/DECISIONS.md` 2026-05-28 Issue 36 entry.
- **`scripts/rotate_token_key.py` operates on the whole `youtube_tokens` table by
  design.** Any integration test for it must `DELETE FROM youtube_tokens` first
  because other integration tests leave rows behind with ephemeral keys.
- **Module-level Redis singleton lives at `youtube/_redis.py::get_redis_client()`**
  (Issue 45). Batch 3 worker/tasks.py work should reuse it.
- **Agents in isolated worktrees must use relative paths.** Absolute paths starting
  with `/home/reese/workspace/Youtube-Video-AI-Editor/...` will leak into the primary
  repo (happened with Issues 50 and 54 in Batch 1; mitigated in Batch 2 with explicit
  prompts).
- **7 agent worktrees are harness-process-locked** at session close — see Phase 1
  Action 3 for cleanup commands. They block `git worktree remove` without `-f -f`.
- **Google OAuth app is still in Testing mode.** Verification required before public
  launch (Issue 29).

---

## 6. POINTERS

| Doc | Purpose |
|---|---|
| `docs/PROJECT_STATE.md` | Issue table + closed-batch summaries (Phase 2: 18/26 done) |
| `docs/issues.md` | Full issue backlog with acceptance criteria — Batches 3/4 queued |
| `docs/DECISIONS.md` | Architectural decisions — 2026-05-28 entries for Issues 32–37, 40–42, 44, 45 |
| `docs/SOT.md` | Architecture + data model |
| `docs/COMPLIANCE.md` | YouTube ToS + Findings & Fixes Log |
| `docs/SECRETS.md` | Every secret by NAME (no values) |
| `docs/ACCESS.md` | SSH access, CI deploy key, Cloudflare Tunnel runbook |
| `docs/DEPLOYMENT.md` | Dev setup + pre-deploy checklists |
| `docs/CLIPPING_PRINCIPLES.md` | Named principles registry cited by the clip engine |
| `CLAUDE.md` | Project rules + Check→Approve→Build→Review workflow |
| `.github/workflows/deploy.yml` | CD pipeline (gated on Docker publish, not integration) |
| `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/MEMORY.md` | Auto-memory index for this project |
