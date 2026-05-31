# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a
> source of truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-05-31 (Wave 6 + Issue 101 committed; production three waves behind; runner install is the unblocker)
**Branch:** `main` — HEAD = post-Issue-101 commit. Only `main` exists locally and on origin.
**Working tree:** clean (except two untracked PNG audit screenshots).
**Production:** ⚠️ **Waves 4 + 5 + 6 + Issue 101 are NOT live.** Production still serving `67fddc9` (Wave-3 era). Three full waves backed up; deploy pipeline is correct in code but billing-blocked on every recent push.
**Tests (local):** 560 passed / 1 skipped / 100 deselected.

---

## CURRENT FOCUS

### → DEPLOY THE BACKLOG

Every push since `67fddc9` has fast-failed with:
> *"The job was not started because recent account payments have failed or your spending limit needs to be increased."*

Issue 101 (this session) made `.github/workflows/docker-publish.yml` `runs-on: self-hosted` so future pushes don't depend on GitHub-hosted billing. But the **runner isn't installed yet**, so until that's done, pushes will queue indefinitely.

Pick a path:

### Path A — Install the self-hosted runner (permanent fix, ~10 min one-time)

This is the only path that unblocks future pushes without a billing fix.

```bash
scp scripts/setup-runner.sh root@147.182.136.107:/tmp/
ssh root@147.182.136.107 bash /tmp/setup-runner.sh
```

The script prompts for a runner registration token. Get it from:
`https://github.com/reese8272/creatorclip/settings/actions/runners/new`
(Select Linux / x64 — copy only the token, not the full configure command.)

After install, BOTH `docker-publish.yml` AND `deploy.yml` run on the VM. The previously-queued runs will dispatch automatically. Zero GitHub-hosted minutes for the deploy pipeline from this point forward, forever.

### Path B — Fix the billing block in GitHub

Settings → Billing & plans → update payment method / raise spending limit. Then:
```
gh run rerun <run-id>      # re-trigger the failed docker-publish
```
or push an empty commit. Hosted runners will work again; the Issue 101 change is then *unused* until a future billing lapse re-triggers the protection. (Both paths are belt-and-suspenders.)

### Path C — Manual deploy right now (no runner needed)

To ship Waves 4 + 5 + 6 + 101 today without any GitHub Actions involvement:

```bash
GHCR_TOKEN=ghp_xxx VPS_USER=root VPS_SSH_KEY=~/.ssh/id_ed25519 ./scripts/deploy.sh
```

Mirrors every deploy.yml step (pull → preflight → migrate → up → smoke test).

Requires the `:latest` image to already be in GHCR. If GHCR doesn't have a recent image (Docker publish hasn't run successfully since `67fddc9`), build + push locally first:
```bash
docker build --platform linux/amd64 -t ghcr.io/reese8272/creatorclip:latest .
echo $GHCR_TOKEN | docker login ghcr.io -u reese8272 --password-stdin
docker push ghcr.io/reese8272/creatorclip:latest
./scripts/deploy.sh
```

### Verify after any path

- Dashboard banner gone for Backboard Media (Wave-6 Fix A migration `0014` ran on `alembic upgrade head`)
- `view-source:https://autoclip.studio/` contains the new footer `<footer ...>Terms ... Privacy ...` (Wave-6 Fix B)
- Pricing link visible in nav

---

## WHAT CHANGED THIS SESSION (2026-05-31 continuation)

### Issue A — CI test fix
- `tests/test_billing.py::test_checkout_offloads_sync_stripe_to_thread` now monkeypatches `STRIPE_SECRET_KEY` to a fake value so CI runners without the real key don't hit the 503 guard. The prod guard is unchanged and correct.

### Issue B — CI/CD alternative
- `deploy.yml` updated: `runs-on: ubuntu-latest` → `runs-on: self-hosted`. SSH/SCP third-party actions removed; deploy job runs directly on the VM using docker compose. No GitHub-hosted minutes consumed per deploy.
- `scripts/deploy.sh` added: manual SSH deploy fallback.
- `scripts/setup-runner.sh` added: one-time systemd service install for the runner on `147.182.136.107`.
- `docs/DECISIONS.md` updated with rationale.

---

## THEN (post-deploy, pick one)

1. Run `/assess` to refresh `docs/assessment/REPORT.md` (post-Wave-4 verdict is stale — pre-dates Wave 5).
2. **Locust load test on staging** (Issue 78f) — sole gate between `CONDITIONAL` and `YES` on the production-readiness verdict.
3. **Submit Google OAuth app verification** — fully unblocked since Wave 4 Fix 3 closed Issue 75b.
4. **Anthropic SDK 0.40 → 0.105.2** bump (Issue 84 follow-up) + drop unproductive `cache_control` markers on DNA + improvement-brief paths.
5. **Feature work:** Issues 93–100 (insights rebuild, clip-engine transparency, OBS hotkey, chat-driven intake, livestream recap, UI redesign, onboarding tutorial).

---

## WHAT WORKS NOW (do not re-investigate)

- **Self-hosted runner deploy path** — `deploy.yml` runs on `self-hosted` label; install runner with `scripts/setup-runner.sh`; manual fallback via `scripts/deploy.sh`.
- **CI test isolation** — `test_checkout_offloads_sync_stripe_to_thread` now passes on CI without a real Stripe key.
- **Cross-tab task persistence.** `static/activeTasks.js` — localStorage-backed, EventSource resume, `Last-Event-ID`. API: `window.activeTasks.{registerTask,getActiveTasks,findTask,subscribe,removeTask}`.
- **Global activity panel.** `static/activityPanel.js` — floating bottom-right widget on every authenticated page.
- **Fail-open `aset_owner` invariant — uniform across all 6 call sites.** `try/except redis.RedisError` + `stream_url: str | None = None` on every site.
- **YouTube ToS 30-day retention compliance.** Daily purge via Celery Beat.
- **Refund pack_id partial UNIQUE race closed.** Migration `0013_refund_pack_id_unique`.
- All Wave 1–5 fixes — see `docs/PROJECT_STATE.md` for full list.

---

## KEY COORDINATES & FACTS

| Item | Value |
|---|---|
| Public URL | `https://autoclip.studio` |
| Deploy VM | `147.182.136.107` |
| Container image | `ghcr.io/reese8272/creatorclip:latest` |
| Branch policy | `main` is the only branch; pushing to `main` triggers Docker publish → workflow_run → Deploy |
| Deploy trigger | `workflow_run` on Docker publish; also `workflow_dispatch`; now runs on self-hosted runner |
| Alembic head | `0013_refund_pack_id_unique` (Wave 4 Fix 2) |
| Latest assessment | `docs/assessment/REPORT.md` (post-Wave-4 — STALE; Wave 5 not captured) |
| Assessment history | `docs/assessment/history/2026-05-31-post-wave-{1,2,3,4}-REPORT.md` |
| `CLAUDE.md` pre-monetization | YouTube data-retention/refresh fully compliant ✅; Google OAuth app verification ❌ (external — user action) |
| Memory dir | `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` |
| Secret names (NEVER log values) | `STRIPE_SECRET_KEY`, `JWT_SECRET`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `GOOGLE_OAUTH_CLIENT_SECRET`, `TOKEN_ENCRYPTION_KEY`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `GHCR_TOKEN` |
| Last successful prod commit | `67fddc9` (Deploy run `26717189318`, 2026-05-31 15:50) |
| Runner install script | `scripts/setup-runner.sh` — run on the VM once |
| Manual deploy script | `scripts/deploy.sh` — run locally with `GHCR_TOKEN` set |

---

## CONSTRAINTS & GOTCHAS (next session: read before acting)

- **Pushing to `main` deploys to production.** No staging gate. Once the self-hosted runner is installed, every push that produces a green Docker publish will auto-deploy.
- **Runner must be registered before the deploy job can run.** Until `setup-runner.sh` is run on the VM, `workflow_run`-triggered deploys will queue indefinitely. Use `scripts/deploy.sh` as the fallback.
- **Production is currently STALE relative to `main`.** Wave 5 is committed but not deployed. Don't tell the user "the activity panel is live in prod" until you have verified `view-source:https://autoclip.studio/` contains `/static/activityPanel.js`.
- **RLS posture:** request-scoped sessions use `AsyncSessionLocal`; refund/worker use `AdminSessionLocal()` (BYPASSRLS by design).
- **slowapi rate-limit collision trap in TestClient.** Per-creator UUID key avoids collision. Logged in `docs/OFF_COURSE_BUGS.md`.
- **`AsyncMock(return_value=_FakeSession())` does NOT work for `AdminSessionLocal()` patching** — use `MagicMock`.
- **`YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS=30` is ToS-mandated upper bound.** Do not increase.
- **Pre-existing `Event loop is closed` warnings in `tests/test_progress.py`** — SEV2 carry-forward, not a regression.
- **No virality promise anywhere.** Structural test pins this.
- **OAuth tokens are Fernet-encrypted at rest** — read via `decrypt()`. Never log.
- **Per-creator isolation on every query** — missing `WHERE creator_id = ...` is a BLOCKER.

---

## POINTERS

- `docs/SOT.md` — current stack, file structure, schema
- `docs/PROJECT_STATE.md` — every issue's status and session log
- `docs/issues.md` — work queue (Issues 93–100 ready)
- `docs/DECISIONS.md` — deviation log (latest: 2026-05-31 CI/CD self-hosted runner)
- `docs/COMPLIANCE.md` — YouTube ToS, retention, privacy posture
- `docs/CLIPPING_PRINCIPLES.md` — named principles the engine cites
- `docs/OFF_COURSE_BUGS.md` — incidental defects log
- `docs/assessment/REPORT.md` — latest verdict (CONDITIONAL — stale, pre-Wave-5)
- `CLAUDE.md` — project rules; the One Rule is non-negotiable
- Memory: `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/MEMORY.md`
