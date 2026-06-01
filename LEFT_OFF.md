# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a
> source of truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-06-01 (Wave 9 + Issue 110 closed; in production)
**Branch:** `main` — HEAD `458346f`. Only `main` exists.
**Sync with `origin/main`:** **0 ahead / 0 behind** — fully in sync.
**Working tree:** clean (3 untracked PNG screenshots — audit artifacts; intentionally not tracked).
**Production:** ✅ **Current.** Deploy run `26732033216` (35s) finished successfully; `autoclip.studio` serves Wave 9 + Issue 110.
**Tests (local, default lane):** 627 passed / 2 skipped / 125 deselected (+7 from Issue 110; +44 total since Wave 8 open).

---

## CURRENT FOCUS

### No active issue — the deck is clear

This session ran a full Wave 9 batch (Issues 102/103/104/105/106/107/108), produced a
fresh post-Wave-9 `/assess`, shipped Issue 110 from that assess's top register, and
pushed everything to production.

The model has also been switched to **Sonnet 4.6** for future sessions.

### → NEXT ACTION — choose ONE

1. **Issue 109** — 10 deferred design-work cleanups from the Issue 108 sweep. No production impact,
   just code health. Each item needs its own Phase-1 brief before touching code. ~half day.
2. **Issue 111 (Haiku 4.5 A/B for clip scoring)** — the `/assess` flagged the Haiku 4.5 cost
   reduction (~67%) for clip-scoring was never filed as a tracked issue, not even in Issue 109.
   File + brief + A/B eval against `tests/eval/scenarios/*.yaml`. ~1 day.
3. **Locust load test (Issue 78f)** — the SOLE remaining structural gate between CONDITIONAL and YES
   on the production-readiness verdict. Settles scale-checklist axes A (pool math) and E
   (backpressure). File as Issue 112 if not already filed; provision the staging environment.
4. **Issue 96** — multi-turn chat intake (CFO-Agent pattern). Needs Phase-1 brief. Large, ~1 day.
5. **Issue 97** — livestream recap + subscription tier (Stripe recurring). Large, ~2 days.
6. **Google OAuth app verification** — fully unblocked from our side (Limited Use in Privacy Policy,
   TOS + Privacy linked from every template, 30-day analytics retention purge active, audit log on
   security events with IP + UA + request_id). External Google process; user-side action.
7. **R2 lifecycle rule** — set a 7-day TTL on the `source/` prefix in the Cloudflare R2 dashboard.
   Belt-and-suspenders for the Issue 110 orphan-mp4 fix. ~5 minutes in the R2 dashboard.

If the user says "just keep going" — start with #3 (Locust) since it's the only gate to YES on
the production-readiness assessment and has been open for 8 assessment cycles.

---

## WHAT WORKS NOW (do not re-investigate)

### Wave 9 batch (this session)
- **Issue 102** — preference `from_bytes` (joblib.load) + LightGBM `fit()` moved off the event
  loop via `asyncio.to_thread`. Both Wave-8 SEV1s closed.
- **Issue 103** — 6 carry-forward SEV2s swept: youtube oauth Redis fail-open, Deepgram normalizer
  KeyError, `_guard_audio_size` OSError → FileNotFoundError, `optimal_gap_hours` bounds guard,
  `dna_match` collinearity (scoring returns DNA-only + composite separately), IoU NMS on candidates.
- **Issue 104** — Wave-8 new surfaces: insights `nullif` aggregate → FILTER clause, temp-file
  leak fixed on both `ingest_clip` + `upload_video`, per-creator rate-limit key universally applied,
  api_keys audit-log rows with IP + UA + request_id.
- **Issue 105** — Worker idempotency + advisory locks: transcribe/signals idempotency probes,
  `_ingest_async` `.wav` short-circuit (retry case), `generate_clips` `RefundOnFailureTask`,
  6 advisory locks, `SoftTimeLimitExceeded` no-retry, redis socket_timeout, `LOCAL_MEDIA_DIR`
  absolute-path validator (relaxed to `STORAGE_BACKEND=local` only by the Issue 110 hotfix).
- **Issue 106** — Security: `limiter.py` JWT `verify_exp=True` + `leeway=60` (DECISIONS deviates
  from /assess 300 recommendation), Stripe `idempotency_key` + HTTP timeout + None-check.
- **Issue 107** — pip-audit 16 → 0 (6 documented in `pyproject.toml [tool.pip-audit].ignore-vulns`).
  Coverage re-baselined 69.54% → 75.20%.
- **Issue 108** — 38/48 cleanup-severity items swept (typing gaps, dead aliases, magic-number
  naming, `Optional["X"]` → `"X | None"`, `*QueuedOut` schema dedup via `routers/_schemas.py`).
  Issue 109 filed for the deferred 10.
- **Issue 110** — post-Wave-9 /assess top register: `/auth/logout` + `/billing/webhook` rate
  limits, improvement-brief `SELECT FOR UPDATE SKIP LOCKED`, `_ingest_async` orphan-mp4
  capture-then-delete-after-commit, auth.py `_logging` workaround removed. LOCAL_MEDIA_DIR
  hotfix shipped separately (`1acee71`).

### Longer-standing landmarks
- **Design system** — `static/_design-tokens.css` Linear-style palette (`#0a0a0a` / `#5e6ad2` /
  Inter + JetBrains Mono). All 9 templates retrofitted.
- **Self-hosted runner deploy pipeline** — both `docker-publish.yml` + `deploy.yml` on
  `self-hosted`. Zero GH-hosted minutes on deploys.
- **OBS companion app surface** — `creator_api_keys` table, `api_key.py`, `routers/api_keys.py`,
  `POST /clips/ingest` with bearer auth. Profile page has full key management UI (list/create/revoke
  with one-time-reveal modal).
- **Walkthrough gate** — first-run creators routed to `/static/walkthrough.html`; intake mandatory
  after (Skip removed).
- **Insights endpoint** — `GET /creators/me/insights` single-fetch (channel totals + DNA snapshot +
  top/bottom performers).
- **Clip transparency** — `Why this clip?` expander on review.html surfaces reasoning + principle +
  score + timing. Auto-opens once.
- **Stripe billing** — checkout with idempotency-key (client UUID via sessionStorage), HTTP timeout,
  `session.url` None-check, `intent_id: UUID4` field on `CheckoutRequest`.
- **4 of 11 modules fully clean** per `/assess` — youtube, upload_intel, billing, preference.

---

## THE ARC THAT LED HERE

1. Wave 8 (4-issue batch: Issues 95 backend + 100 + 93 + 94) closed on the Issue-99 design system.
2. Session opened with Issue 95 frontend + `/assess` sweep.
3. `/assess` surfaced 2 SEV1s (preference event-loop blocking) + pip-audit 16 + 51 SEV2s → Wave 9.
4. Wave 9 (7-issue parallel batch: 102/103/104/105/106/107/108) — 4 issues in parallel worktrees,
   3 directly on main. Mid-merge hotfix: Issue 104's `creator_key` required a 26-site test
   `override_current_creator` sweep (`tests/_helpers.py`).
5. Post-Wave-9 `/assess` — 0 SEV1, 4 clean modules, 32 SEV2, largest single-cycle drop.
6. Issue 110 closed the top-register items from that assess.
7. Two production deploys this session: first failed (LOCAL_MEDIA_DIR over-strict validator);
   hotfix + re-deploy both succeeded.

---

## KEY COORDINATES & FACTS

| Item | Value |
|---|---|
| Public URL | `https://autoclip.studio` |
| Production VM | `147.182.136.107` |
| Container image | `ghcr.io/reese8272/creatorclip:latest` |
| Repo | `github.com/reese8272/creatorclip` (NOT `Youtube-Video-AI-Editor` — old name 404s) |
| Self-hosted runner | `autoclip-prod-vm` (`self-hosted,linux,x64,prod`) — systemd service `actions.runner.reese8272-creatorclip.autoclip-prod-vm` |
| Last successful deploy | `26732033216` (Issue 110 commit `458346f`) |
| Alembic head | `0015_creator_api_keys` (Issue 95 backend — no new migrations this session) |
| Default model (next session) | Sonnet 4.6 (1M context) — user switched away from Opus 4.7 |
| `/assess` REPORT | `docs/assessment/REPORT.md` + `history/2026-06-01-post-wave-9-REPORT.md` — **CURRENT** (post Issue 110 fixes applied) |
| Assessment verdict | CONDITIONAL — 0 BLOCKER / 0 SEV1 / 32 SEV2 / ~30 cleanup; 4/11 modules clean |
| Memory dir | `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` |
| Secret names (NEVER log values) | `STRIPE_SECRET_KEY`, `JWT_SECRET_KEY`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `GOOGLE_OAUTH_CLIENT_SECRET`, `TOKEN_ENCRYPTION_KEY`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `GHCR_TOKEN`, `DEEPGRAM_API_KEY` |
| pip-audit ignores | `pyproject.toml [tool.pip-audit].ignore-vulns` — 6 entries with mandatory reason comments |
| API-key bearer header format | `Authorization: Bearer ack_<32 url-safe chars>` (companion-app upload to `/clips/ingest`) |

---

## CONSTRAINTS & GOTCHAS (next session: read before acting)

- **Pushing to `main` auto-deploys.** Self-hosted runner picks up Docker publish, then
  `workflow_run` triggers Deploy. No staging gate. Each push = a production cut.
- **CI / Quality / Integration on hosted runners still fast-fail.** Intentional (Issue 101) —
  informational only; don't gate deploys. The Quality Gates workflow DID pass on this session's
  push; CI + Integration fail (billing block) as expected.
- **LOCAL_MEDIA_DIR validator** was relaxed in the Issue 110 hotfix: only fails fast in
  production when `STORAGE_BACKEND=local`. STORAGE_BACKEND=r2 (prod) skips the check because the
  path is dead config. Do NOT revert this.
- **`tests/_helpers.py::override_current_creator`** must be used instead of
  `lambda: creator` in ALL test dependency overrides for `get_current_creator`. The lambda
  bypasses `request.state.creator_id` stash → slowapi `creator_key` falls back to IP →
  all tests share the "testclient" rate-limit bucket → 429s. See `tests/_helpers.py` for usage.
- **`routers/_schemas.py::TaskQueuedOut`** is the base for `BuildQueuedOut`,
  `CatalogSyncQueuedOut`, `RenderQueuedOut`. `BriefQueuedOut` stays standalone (`task_id:
  str | None` is LSP-incompatible with the base's `str` — improvement-brief debounce can return no
  task). Don't subclass it.
- **Runner is the single point of failure for deploys.** Fallback: `./scripts/deploy.sh`
  with `GHCR_TOKEN` set.
- **`/docs` is exempted from RLS lookups** — auth dependency resolves Creator before the GUC
  is set. Don't touch this.
- **YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS=30** is a ToS upper bound. Do NOT increase.
- **Pre-existing `Event loop is closed` warnings** in `tests/test_progress.py` are SEV2
  carry-forward, not a regression.
- **Repo was renamed** `Youtube-Video-AI-Editor → creatorclip` — old name returns 404 from
  `gh api`; runner registration tokens are repo-scoped.
- **R2 `source/` lifecycle rule** (7-day TTL) is a pending user-side action in the R2 dashboard —
  not yet configured; tracked as an AC in Issue 110 docs.
- **`docs/assessment/REPORT.md` is CURRENT** (post-Wave-9 + Issue 110 close). Anyone
  claiming "production ready" based on it should note the CONDITIONAL verdict gates on Locust.
- **OAuth tokens are Fernet-encrypted at rest.** Read via `decrypt()`; never log.
- **Per-creator isolation on every query.** Missing `WHERE creator_id = ...` is a BLOCKER.
- **Bearer-auth surface (`/clips/ingest`) and session-cookie surface are separate.** Don't
  conflate `get_current_creator` and `get_current_creator_via_api_key`. Both now stash
  `request.state.creator_id` — this is required for `creator_key` to work on both surfaces.
- **`_ingest_async` orphan-mp4 fix**: capture + delete is best-effort. If the worker crashes
  between commit and `adelete_file`, the mp4 leaks until the R2 lifecycle rule sweeps it.
  Do NOT add a retry loop around `adelete_file` — the task would then retry a delete that already
  succeeded on the prior attempt (idempotent on key-not-found, but noisy).

---

## POINTERS

- `docs/SOT.md` — current stack, file structure, schema
- `docs/PROJECT_STATE.md` — every issue's status + session log (Wave 9 + Issue 110 at the top)
- `docs/issues.md` — issue backlog (Issues 109/111/112 are the natural next items)
- `docs/DECISIONS.md` — deviation log (Issue 102 joblib/to_thread, Issue 106 JWT leeway,
  Issue 110 SKIP LOCKED + capture-then-delete-after-commit — all 2026-06-01)
- `docs/COMPLIANCE.md` — YouTube ToS, retention, privacy posture
- `docs/CLIPPING_PRINCIPLES.md` — named principles registry
- `docs/OFF_COURSE_BUGS.md` — incidental defect log
- `docs/assessment/REPORT.md` — current `/assess` verdict (post-Wave-9 + Issue 110, CURRENT)
- `docs/assessment/history/2026-06-01-post-wave-9-REPORT.md` — immutable snapshot
- `tests/_helpers.py` — `override_current_creator(creator)` — use this in all test dep overrides
- `routers/_schemas.py` — `TaskQueuedOut` base for 3 of 4 `*QueuedOut` schemas
- `CLAUDE.md` — project rules; the One Rule is non-negotiable
- Memory: `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/MEMORY.md`
