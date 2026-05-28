# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a source of
> truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-05-28 (PM session)
**Branch:** `main` — HEAD `19f7caf` (clean local in sync with `origin/main`)
**Working tree:** **dirty** — Issue 36 closed but not yet committed
**Ahead/behind origin/main:** 0 / 0 (last push: the Phase 2 batch is already deployed)
**Production:** green on `origin/main` (`19f7caf`); `https://autoclip.studio/health` passing

---

## 1. CURRENT FOCUS

**Commit + push the Issue 36 hardening, then start Issue 37.**

Issue 36 (OAuth token lifecycle hardening — SEV-1) is fully implemented and the full
test suite is green (**335 passed**, 1 skipped, 16 deselected — was 326; +9 new). All
changes are in the working tree, none committed yet.

### → NEXT ACTIONS (in order)

**1. Commit + push Issue 36.**

```bash
git add youtube/errors.py routers/auth.py youtube/oauth.py \
        youtube/data_api.py youtube/analytics.py worker/tasks.py \
        tests/test_oauth_lifecycle.py \
        docs/DECISIONS.md docs/PROJECT_STATE.md docs/issues.md LEFT_OFF.md
git commit -m "feat: Issue 36 — OAuth token lifecycle hardening (SEV-1)"
git push origin main           # triggers CI + Deploy to production
gh run watch                   # confirm green
```

**2. Pick Issue 37 (External SDK timeouts + retry-with-backoff).**

Per `docs/issues.md:571`: Anthropic, Stripe, Voyage, Deepgram, R2 (boto3) clients are
constructed per-call with no `timeout=` and no retry policy — each can hang the worker
indefinitely. Acceptance criteria:
- Phase 1 must call `/claude-api` skill to confirm current Anthropic SDK recommended
  `timeout=` / `max_retries=`; same for Stripe (`max_network_retries`), boto3 adaptive
  retry, Voyage tenacity-wrap, Deepgram httpx timeout.
- Module-level singleton per SDK, constructed once from `config.settings`.
- Per-call timeout override for known-long calls (improvement_brief with web_search may
  need 120s).
- Test that asserts each client config has a positive timeout.

After 37: Issue 38 (sync-in-async + held DB sessions) unblocks the rest of the
worker/Celery chain (39, 43, 45–47).

---

## 2. WHAT WORKS NOW (do not re-investigate)

- ✅ **Phase 2 hardening Issues 32, 33, 34, 35, 40, 41, 42, 44, 36** — closed; suite green
  (335 passed, 1 skipped, 16 deselected integration tests).
- ✅ **OAuth lifecycle** (Issue 36, this session):
  - `DELETE /auth/me` revokes the **refresh** token; 400 `invalid_token` / `token_revoked`
    treated as success.
  - `get_valid_access_token` deletes the `YoutubeToken` row + commits on Google
    `invalid_grant`.
  - `youtube/errors.py` defines `YouTubeAuthError`; `_get_json` and `_fetch_report` share
    `_classify_error()` — retry transient 403/429, raise on permanent 401 / 403 reasons.
  - `_refresh_youtube_analytics_async` catches `YouTubeAuthError`, deletes the token row,
    commits, continues.
- ✅ **Beta deploy pipeline** — last `Deploy to production` workflow on `19f7caf` succeeded
  at 21:01 UTC.
- ✅ **Cloudflare Tunnel `autoclip-prod`** stable.
- ✅ **5 worktrees from prior session cleaned up** — none remain.

---

## 3. THE ARC THAT LED HERE

1. **Phase 2 batch (Issues 32–35, 40–42, 44)** merged on local `main`; pushed to
   `origin/main` between sessions and deployed.
2. **Issue 36 (this session)** — three-prong OAuth lifecycle fix per the Phase 1 brief
   (research per CLAUDE.md "One Rule Above All Others"):
   - Refresh-token revocation per Google OAuth 2.0 docs.
   - `invalid_grant` row deletion per RFC 6749 §5.2.
   - Reason-based 403 classification per YouTube Data API v3 error model.
   - Worker auth-cleanup wired into the daily beat loop.
3. Ruff lint + format on all touched files; suite re-run; docs updated.

---

## 4. KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| **Public URL** | `https://autoclip.studio` |
| **VM** | `147.182.136.107` — Ubuntu 24.04, 4 vCPU / 8 GB, NYC1 |
| **SSH alias** | `ssh creatorclip-vm` (key: `~/.ssh/id_ed25519`, user: `root`) |
| **Deploy dir on VM** | `/opt/autoclip/` |
| **Active tunnel** | `autoclip-prod` (`db79b904-9cbf-4a79-b336-3b8195e6d37b`) |
| **Cloudflare zone** | `autoclip.studio` (zone `764913b08938704d661e6613f0926ac9`) |
| **R2 bucket** | `creatorclip-beta` |
| **Docker image** | `ghcr.io/reese8272/creatorclip:latest` |
| **GitHub repo** | `github.com/reese8272/creatorclip` (private) |
| **App secrets on VM** | `/opt/autoclip/.env` (chmod 600; see `docs/SECRETS.md`) |
| **GitHub Actions secrets** | `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, `VPS_PORT`, `GHCR_TOKEN`, `PRODUCTION_URL` |
| **Test runner** | **`.venv/bin/python -m pytest -q`** (system `python3.12` is broken — see Gotchas) |
| **Active issue** | **Issue 37 — External SDK timeouts + retry-with-backoff (SEV-1)** |
| **Last completed** | Issue 36 — OAuth token lifecycle hardening (SEV-1, three sub-fixes) |
| **Stale branch deleted** | `issue-31-operability` (this session, local + remote) |

---

## 5. CONSTRAINTS & GOTCHAS

- **System `python3.12` cannot run pytest** — langsmith installed a newer pydantic
  (2.46.4) into the uv-managed Python while user-site has pydantic-core 2.27.2.
  `python3.12 -m pytest` fails at plugin-load with `SystemError: pydantic-core
  incompatible`. **Always use `.venv/bin/python` for tests.** Fix (deferred):
  `python3.12 -m pip install --user --break-system-packages "pydantic-core>=2.46.4"`.
- **Pushing to `main` triggers CI + production deploy.** Don't push speculative
  hardening work without running the suite first.
- **OAuth "disconnected" is represented as YoutubeToken-row absence**, not a new
  `OnboardingState` enum value. The existing `get_valid_access_token` raises 401 "No
  OAuth tokens found — please reconnect" when the row is missing, and the analytics-refresh
  beat loop's `try: get_valid_access_token ... except: continue` block silently skips it.
  See `docs/DECISIONS.md` 2026-05-28 Issue 36 entry. If product later needs a UI-visible
  `disconnected` state, that's a follow-up issue (enum + Alembic migration).
- **`importlib.reload(config)` is poison in tests.** Mutate `settings` attributes via
  `monkeypatch.setattr(settings, "X", v)` instead.
- **`from billing.ledger import check_positive_balance`** in `routers/videos.py` binds in
  the router namespace — patch `routers.videos.check_positive_balance`, not the source path.
- **`docker compose up -d --force-recreate cloudflared`** is required to pick up a new
  tunnel token.
- **Google OAuth app is in Testing mode** — only test users can sign in. Google
  verification required for YouTube scopes before opening to real users.

---

## 6. POINTERS

| Doc | Purpose |
|---|---|
| `docs/PROJECT_STATE.md` | Issue table + current status — Phase 2 hardening in progress |
| `docs/issues.md` | Full issue backlog with acceptance criteria — Issue 37 next |
| `docs/DECISIONS.md` | Architectural decisions — fresh 2026-05-28 entries for Issues 32–36, 40–42, 44 |
| `docs/SOT.md` | Architecture + data model |
| `docs/COMPLIANCE.md` | YouTube ToS + Findings & Fixes Log |
| `docs/SECRETS.md` | Every secret — still needs update: `agenticlip.studio` → `autoclip.studio` |
| `docs/ACCESS.md` | SSH access, CI deploy key, Cloudflare Tunnel runbook |
| `docs/DEPLOYMENT.md` | Dev setup, pre-deploy checklists |
| `.github/workflows/deploy.yml` | CD pipeline — currently green |
| `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/MEMORY.md` | Auto-memory index for this project |
