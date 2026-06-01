# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a
> source of truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-06-01 (Issues 113–120 + prod incident + /assess)
**Branch:** `main` — HEAD `6569d8f`
**Sync with `origin/main`:** 0 ahead / 0 behind — working tree **CLEAN**
**Production:** ✅ **Live and healthy.** Last deploy `26762218644` (success, 39s).
`https://autoclip.studio/health` → `{"status":"ok","postgres":"ok","redis":"ok"}`

---

## CURRENT FOCUS

### Fix the 6 SEV1s surfaced by /assess from the Issues 113–119 UX wave

The /assess run this session returned CONDITIONAL with 6 new SEV1s — all in new
code from Issues 113–119. They are concrete and all fixable in one session (~80 LOC).
No BLOCKERs remain. The previous BLOCKER (improvement_briefs UNIQUE) is confirmed
deployed and live.

### → NEXT ACTION

**Step 1 — File + build the SEV1 sweep as Issue 121**

All 6 findings are in `docs/assessment/REPORT.md` under "SEV1 — Issues 113–119".
Run `/issue-workflow` with this brief:

> Issue 121 — SEV1 sweep from Issues 113–119 surface
>
> Fix all 6 SEV1s surfaced in the post-Issues-113-119 /assess:
> 1. `routers/insights.py:376` — `Anthropic(...)` client constructed per-request;
>    move to module-level singleton with `timeout=60, max_retries=2`
> 2. `routers/insights.py:312` — `analyze_performer` endpoint missing
>    `@limiter.limit("20/hour", key_func=creator_key)` decorator
> 3. `routers/insights.py:378` — `__import__("asyncio").to_thread(...)` inline;
>    add `import asyncio` at module top and call directly
> 4. `models.py:728–757` — `CreatorInsight` missing composite index on
>    `(creator_id, video_id)`; add `sa.Index(...)` + migration `0020_creator_insight_index`
> 5. `worker/tasks.py:915` — dead `awaiting_data` state check; remove 2 lines
>    (state transition owned by `create_draft()` in `dna/profile.py:82`)
> 6. `auth.py:47` + `api_key.py:95` — RLS bootstrap exemption for `creators` table
>    has no CI regression test; add integration test asserting `pg_policies` returns
>    zero rows for `creators` (confirming it remains RLS-exempt)
> Also add token usage logging after the `client.messages.create()` call
> (`msg.usage.input_tokens` / `msg.usage.output_tokens`) — SEV2 but bundle it in.

**Step 2 — Locust load test (user-side, ~10 min)**

Staging stack is still alive on the prod VM. This is the SOLE remaining structural
gate from CONDITIONAL → YES on the production-readiness verdict.

```bash
# On the prod VM (ssh root@147.182.136.107):
docker compose -f /root/docker-compose.simple.yml up -d
# Wait for health, then run locust:
CC_BASE_URL=http://localhost:8001 \
CC_JWT_SECRET=<JWT_SECRET_KEY from /opt/autoclip/.env> \
CC_CREATOR_ID=00000000-1111-2222-3333-444444444444 \
locust -f /app/tests/perf/locustfile.py --host http://localhost:8001 \
    --users 300 --spawn-rate 20 --run-time 5m --headless \
    --csv /tmp/loadtest && cat /tmp/loadtest_stats.csv
```

Pass: p99 < 500ms on key routes, error rate < 1%, no `QueuePool limit` errors.

**Step 3 — Google OAuth app verification (external)**

Already submitted. Monitor Google Cloud Console → OAuth consent screen for review.
No code action needed.

---

## WHAT WORKS NOW (do not re-investigate)

### This session

- **Issues 113–119 (UX wave) — built and deployed:**
  - 113: Minutes balance chip (`nav-balance`) + `?` tutorial button in nav on all 4 main pages
  - 114: Profile DNA section collapsible `<details>` + "Synced / Not synced with DNA" chip
  - 115: `GET /creators/me/insights/analytics?period=7d|28d|90d|all` + dashboard analytics panel
  - 116: `progressStream.js` wired into `rebuildDna()` on `profile.html` (replaces "come back in 30s")
  - 117: AI per-performer analysis via Haiku 4.5, lazy + cached per (video, dna_version), save/bookmark
  - 118: `feedback_tags` + `feedback_note` on `clip_feedback`; multi-select approve/deny in review UI
  - 119: `style_preset` on clips; subtitle/background/captions style picker; `render_clip_file` extended
  - Migrations 0017 (creator_insights), 0018 (feedback_tags), 0019 (clip_style_preset) all applied to prod

- **Deploy repair:** Broken migration chain fixed (`0017.down_revision` was `"0016"` → corrected to
  `"0016_improvement_brief_unique"`). `ruff format` applied (CI checks both `ruff check` +
  `ruff format --check` — Layer 0 only runs `ruff check`; always run both locally).

- **Integration test repairs:** 3 mock signatures updated to accept `task_id=None`
  (`_generate_improvement_brief_async` passes `task_id` kwarg); `test_insights_integration.py`
  `VideoMetrics` seeded without `fetched_at` (NOT NULL) — fixed with `datetime.now(UTC)`.

- **Production incident — YouTube token expiry mid-sync:** Caught live in prod logs.
  `get_valid_access_token` was called once at the top of `_sync_channel_catalog_async`;
  Phase 2 (metrics fetch loop) could run > 60 min on large channels → 401 mid-loop.
  Fix: re-fetch token before Phase 2 starts (`01d6de7`).

- **Issue 120 — Per-type DNA caps (longs: 50, shorts: 75):**
  - `DNA_MAX_CANDIDATE_VIDEOS=500` replaced with `DNA_LONGS_CAP=50` + `DNA_SHORTS_CAP=75`
  - `rank_videos()` now queries longs and shorts separately; merges + re-sorts by weighted_score
  - Phase 2 catalog sync capped to 50+75=125 videos max per first-sync (~4 min); Beat task handles rest
  - 4 test mocks updated for two-query Phase 2 pattern

- **Full /assess run:** REPORT.md refreshed. CONDITIONAL (no BLOCKERs; 6 SEV1s from new surface;
  ingestion promoted to CLEAN). Full register in `docs/assessment/REPORT.md`.

- **Tests:** 652 passed / 2 skipped / 126 deselected. Layer 0: ruff 0 / mypy 0 / coverage 75.83% / bandit 0/0 / pip-audit 0.

### Longer-standing landmarks

- **Previous BLOCKER fixed:** `improvement_briefs UNIQUE(creator_id)` — migration 0016 applied to prod
- **Design system** — `static/_design-tokens.css` Linear-style palette; all 9 templates retrofitted
- **Self-hosted runner deploy pipeline** — both `docker-publish.yml` + `deploy.yml` on `self-hosted`
- **OBS companion app surface** — bearer-auth `POST /clips/ingest`, API-key management UI
- **Walkthrough gate** — first-run creators routed to `/static/walkthrough.html`
- **RLS** — Postgres Row-Level Security active on 12 tenant-owned tables (Issue 79)
- **Stripe billing** — checkout with UUID4 idempotency-key (Issue 106)

---

## THE ARC THAT LED HERE

1. Issues 113–119 UX wave built in one session (nav balance, DNA collapsible, dashboard analytics, DNA streaming, AI insights, structured feedback, clip style editor).
2. Issues 113–119 push triggered failed deploy: broken migration chain + ruff formatting failures — both fixed.
3. Production incident: brother's YouTube catalog sync hit 401 mid-loop (token expiry). Root cause found in live prod logs; token refresh fix deployed.
4. Issue 120 filed + built: per-type DNA caps (50 longs / 75 shorts) to bound first-sync to ~4 min. Deployed.
5. Full `/assess` run: CONDITIONAL, 6 new SEV1s all from Issues 113–119 surface, all in `routers/insights.py` + `models.py` + `worker/tasks.py`. Ready to sweep as Issue 121.

---

## KEY COORDINATES & FACTS

| Item | Value |
|---|---|
| Public URL | `https://autoclip.studio` |
| Production VM | `147.182.136.107` |
| Container image | `ghcr.io/reese8272/creatorclip:latest` |
| Repo | `github.com/reese8272/creatorclip` |
| Self-hosted runner | systemd service `actions.runner.reese8272-creatorclip.autoclip-prod-vm` |
| Last successful deploy | `26762218644` (Issue 120, HEAD `6569d8f`) |
| Alembic head (prod + local) | `0019_clip_style_preset` — fully in sync |
| Issue 121 scope | 6 SEV1s in `routers/insights.py`, `models.py`, `worker/tasks.py` — see REPORT.md |
| Locust staging creator | UUID `00000000-1111-2222-3333-444444444444` in `staging_postgres_data` volume |
| Default model | Sonnet 4.6 (1M context) |
| `/assess` REPORT | `docs/assessment/REPORT.md` + `history/2026-06-01-post-issues-113-119-REPORT.md` |
| Assessment verdict | CONDITIONAL — 0 BLOCKERs / 6 SEV1s (all fixable, Issue 121) / ~18 SEV2 / 8/11 clean |
| DNA caps (new) | `DNA_LONGS_CAP=50`, `DNA_SHORTS_CAP=75` (replaced `DNA_MAX_CANDIDATE_VIDEOS=500`) |
| Secret names (never log values) | `STRIPE_SECRET_KEY`, `JWT_SECRET_KEY`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `GOOGLE_OAUTH_CLIENT_SECRET`, `TOKEN_ENCRYPTION_KEY`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `GHCR_TOKEN`, `DEEPGRAM_API_KEY` |
| Memory dir | `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` |

---

## CONSTRAINTS & GOTCHAS (next session: read before acting)

- **Pushing to `main` auto-deploys.** Self-hosted runner: Docker publish → Deploy. No staging gate. Each push = a production cut.
- **Integration tests failing (pre-existing, non-blocking).** CI "Integration tests" lane always fails — it needs a real Postgres that CI doesn't provision. Does NOT block deploy. Do not try to fix by changing the CI config.
- **`ruff format --check` is a CI gate; Layer 0 only runs `ruff check`.** Always run `ruff format .` before pushing, or the CI "Lint (ruff)" step will fail even if `ruff check` passed locally.
- **`analyze_performer` endpoint has no rate limiter** (Issue 121 SEV1). Until Issue 121 is shipped, any authenticated user can exhaust the Anthropic quota by hammering it. Ship Issue 121 promptly.
- **`CreatorInsight` missing composite index** (Issue 121 SEV1). `GET /creators/me/insights/saved` and the cache-check in `analyze_performer` will full-table-scan until migration 0020 lands.
- **Staging stack is still alive** on the prod VM (`root-*` containers). Tear down after the Locust run: `docker compose -f /root/docker-compose.simple.yml down -v && docker compose -f /root/docker-compose.staging.yml down -v`.
- **`tests/_helpers.py::override_current_creator`** must be used instead of `lambda: creator` in ALL test dependency overrides for `get_current_creator` (Issue 104 fix).
- **`BriefQueuedOut` stays standalone** — `task_id: str | None` is LSP-incompatible with `TaskQueuedOut`'s `str`. Do not subclass it.
- **`LOCAL_MEDIA_DIR` validator relaxed** (Issue 110 hotfix): only fails fast in production when `STORAGE_BACKEND=local`. Do NOT revert.
- **OAuth tokens Fernet-encrypted at rest.** Read via `decrypt()`; never log.
- **Per-creator isolation on every query.** Missing `WHERE creator_id = ...` is a BLOCKER (RLS is a structural backstop but app-layer filters are still required).
- **`YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS=30`** is a YouTube ToS hard limit. Do NOT increase.

---

## POINTERS

- `docs/SOT.md` — current stack, file structure, data model (updated for Issues 113–120)
- `docs/PROJECT_STATE.md` — every issue's status + session log
- `docs/issues.md` — backlog (Issue 120 filed + closed; Issue 121 is the next item)
- `docs/DECISIONS.md` — deviation log (Issue 120 entry: why count-based over time-based)
- `docs/COMPLIANCE.md` — YouTube ToS, retention, privacy posture
- `docs/CLIPPING_PRINCIPLES.md` — named principles registry
- `docs/assessment/REPORT.md` — current `/assess` verdict (post Issues 113–119, CURRENT)
- `docs/assessment/history/2026-06-01-post-issues-113-119-REPORT.md` — immutable snapshot
- `tests/_helpers.py` — `override_current_creator(creator)` — use in all test dep overrides
- `routers/_schemas.py` — `TaskQueuedOut` base schema
- `CLAUDE.md` — project rules; the One Rule is non-negotiable
- Memory: `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/MEMORY.md`
