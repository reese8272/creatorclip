# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a
> source of truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-05-31 (Wave 5 closed end-to-end — SEV1 hotfix + cross-tab task persistence + global activity panel — but the deploy to production is blocked, see below)
**Branch:** `main` — HEAD `2c7b34d`. Only `main` exists locally and on origin.
**Sync with `origin/main`:** **0 / 0** — in sync.
**Working tree:** clean (untracked `Screenshot 2026-05-30 155339.png` only — the user's stuck-DNA screenshot from yesterday; delete-OK).
**Production:** ⚠️ **Wave 5 is NOT live yet.** Production is still serving commit `67fddc9` (the last successful Deploy, 2026-05-31 15:50). `/health` returns `{"status":"ok","postgres":"ok","redis":"ok"}` from the previous image. Wave 5's user-visible changes (the global activity panel + cross-tab persistence) are committed but not deployed.
**Tests (local, default lane):** 553 passed / 1 skipped / 94 deselected. **CI (GitHub Actions) is RED — see Blocker A below.**

---

## CURRENT FOCUS

Two stop-the-world items are blocking the Wave 5 user-facing rollout. Both must clear before the user sees the new activity panel and cross-tab task persistence in production.

### → BLOCKER A — Fix the CI unit-test failure (code-side, 1-line fix)

The new test I added in Wave 3 — `tests/test_billing.py::test_checkout_offloads_sync_stripe_to_thread` — passes locally but fails on CI with `assert 503 == 200`. Root cause: `routers/billing.py` returns 503 at the top of `create_checkout` when `settings.STRIPE_SECRET_KEY` is empty. My laptop's env has the key set; the CI runner does not. The other test in the same file (`test_checkout_returns_503_when_stripe_key_empty`) intentionally clears the key with `monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "")` — the new test must do the opposite.

**Exact fix:** open `tests/test_billing.py`, find `test_checkout_offloads_sync_stripe_to_thread`, and add at the top of the body, before the existing monkeypatch lines:

```python
from config import settings as _settings
monkeypatch.setattr(_settings, "STRIPE_SECRET_KEY", "sk_test_fake_key_for_test")
```

Then `pytest tests/test_billing.py::test_checkout_offloads_sync_stripe_to_thread -q` to confirm, commit as a one-line `fix(tests):` and push. CI's `Unit tests (pytest)` lane will go green. **Do not** weaken the prod 503 guard — the guard is correct; the test is wrong.

### → BLOCKER B — GitHub Actions billing (external, user-only)

The Wave 5 Deploy workflow (`gh run 26717953473`) was **not started by GitHub** — annotation reads verbatim:

> "The job was not started because recent account payments have failed or your spending limit needs to be increased. Please check the 'Billing & plans' section in your settings"

This is an Actions-runner billing issue, not a code problem. Until you (the human user) resolve it at https://github.com/settings/billing, no workflow_run-triggered Deploy will execute, regardless of what we push. Quality Gates + Docker publish still run on push (those are push-triggered, not workflow_run-triggered), so the image is published — the deploy step is the one that's gated.

**Order matters:** fix Blocker A first (Quality Gates is already green, but a red CI lane is a bad look + the Wave 5 image needs the test fix anyway); then fix Blocker B; then push or trigger a manual deploy; then verify https://autoclip.studio is serving the new code (look for `<script defer src="/static/activeTasks.js"></script>` in the index.html source).

### → THEN (post-blocker, pick one)

1. Run `/assess` to confirm SEV1=0 and refresh `docs/assessment/REPORT.md` (the post-Wave-4 assessment is now stale — it pre-dates Wave 5).
2. **Locust load test on staging** (Issue 78f) — the only remaining structural gate between `CONDITIONAL` and `YES` on the production-readiness verdict.
3. **Submit Google OAuth app verification** — fully unblocked since Wave 4 Fix 3 closed Issue 75b (30-day YouTube retention purge is live).
4. **Anthropic SDK 0.40 → 0.105.2** bump (Issue 84 follow-up) + drop unproductive `cache_control` markers on DNA + improvement-brief paths.
5. **Feature work:** Issues 93–100 (insights rebuild, clip-engine transparency, OBS hotkey, chat-driven intake, livestream recap, UI redesign, onboarding tutorial). Filed 2026-05-31 from the user's close-out list.

---

## WHAT WORKS NOW (verified this session — do not re-investigate)

- **Cross-tab task persistence.** `static/activeTasks.js` stores active tasks in `localStorage` (`creatorclip:active_tasks`), prunes stale entries > 1h (matches `_STREAM_TTL_SECONDS=3600` in `worker/progress.py`), and re-opens `EventSource` with `Last-Event-ID` on every page load. Public API: `window.activeTasks.{registerTask,getActiveTasks,findTask,subscribe,removeTask}`. Pinned by `tests/test_static.py::test_active_tasks_library_exists_and_exports_api`.
- **Global activity panel.** `static/activityPanel.js` mounts a bottom-right floating widget (Linear/Vercel-style) on every authenticated page (`index.html`, `onboarding.html`, `insights.html`, `profile.html`, `review.html`, `pricing.html`). Hidden when no tasks; expanded tray shows terminal-style streams per task. Pinned by `tests/test_static.py::test_all_authenticated_templates_include_active_tasks_and_panel`.
- **Fail-open `aset_owner` invariant — uniform across all 6 call sites.** Waves 3/4/5 closed: `routers/improvement.py:91-110` (brief), `routers/auth.py:117-119` (OAuth callback), `routers/videos.py:262-279` (upload), `routers/creators.py::sync_catalog` (~167), `routers/creators.py::build_dna` (~195), `routers/clips.py::render_clip` (~145). Every site wraps `await progress.aset_owner(...)` in `try/except redis.RedisError`; on failure, returns `stream_url=None` (client polls instead of streaming). Every response model now has `stream_url: str | None = None`.
- **YouTube ToS 30-day retention compliance (Wave 4 Fix 3 / Issue 75b).** `worker/tasks.py::_purge_stale_youtube_analytics_async` runs daily via Celery Beat (`worker/schedule.py`), deletes `VideoMetrics + RetentionCurve` (lock-step), `AudienceActivity`, `Demographics` past `YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS=30`. Inline ToS §III.E.4.b citation in `config.py`. Pre-monetization checkbox in `CLAUDE.md` ✅.
- **Refund pack_id partial UNIQUE race closed (Wave 4 Fix 2).** Migration `0013_refund_pack_id_unique` creates partial UNIQUE on `(pack_id) WHERE reason='refund'` via `CREATE INDEX CONCURRENTLY` in `autocommit_block`. `billing/refund.py` simplified: removed read-then-write SELECT guard; catches `IntegrityError` + rollback + return 0.
- **Streaming tools forwarding (Wave 3 Fix A).** `worker/anthropic_stream.py::stream_and_emit` extended with `tools: list | None = None`; dict-based `stream_kwargs` omits `tools` when None (preserves `dna/brief.py` shape). `improvement/brief.py:124-131` passes `tools=tools`. The Issue 84 web_search bump is now propagated through the streaming path, not just `.create()`.
- **Sync Stripe SDK off the event loop (Wave 3 Fix C).** `routers/billing.py:94` wraps `create_checkout_session` in `await asyncio.to_thread(...)`. Test `test_checkout_offloads_sync_stripe_to_thread` validates this, but **currently fails on CI — see Blocker A**.
- **Terminal-stage promotion (Wave 3 Fix E).** `worker/tasks.py::_signals_async` no longer emits terminal `done`; emits `step:ingest_complete` (non-terminal). `_generate_clips_async` now fires `step:generate_clips_start`, `step:score_and_rank`, and terminal `done` with `clip_count`. Stream key is `video_id` across both stages.
- **Anthropic SDK 0.40 audit (Wave 2 / Issue 84).** All 3 call sites verified compatible with current SDK; no breaking changes. Bumped `web_search` tool to GA (`web_search_20260209`) with dynamic filtering. Full assessment in `docs/assessment/llm/{dna_brief,clip_scoring,improvement_brief,REPORT}.md`.
- **Universal SSE progress visibility (Wave 2 / Issue 92).** Every long-running background task (DNA build, catalog sync, improvement brief, upload pipeline, render) emits via the Issue 86 SSE primitive on `/tasks/{task_id}/events`.

---

## THE ARC THAT LED HERE

Five waves in one autonomous session (2026-05-31):

1. **Wave 1** — 6 hotfix batch (2 SEV-1s from `/assess` + Issues 89/90/91/98 — balance pre-check, catalog list filter, clips counter filter, DNA banner state machine).
2. **Wave 2** — Issue 84 (Anthropic SDK + web_search audit + bump to GA) + Issue 92 (universal SSE progress visibility for every long-running background task).
3. **Wave 3** — 6 regression fixes from the new SSE coverage: streaming tools drop, `aset_owner` ordering, sync Stripe in async path, OAuth callback `aset_owner` gap, premature done event from `_signals_async`, silent skip-video — plus carry-forward Stripe SEV1.
4. **Wave 4** — 3 fixes: `videos.py` upload fail-open, refund `pack_id` partial UNIQUE (migration 0013), YouTube ToS 30-day retention purge (Issue 75b compliance gap).
5. **Wave 5** — 3 fixes: SEV1 hotfix extending fail-open to 3 remaining `aset_owner` sites (`creators.py::sync_catalog`, `creators.py::build_dna`, `clips.py::render_clip`); cross-tab task persistence (`static/activeTasks.js`); global activity panel on every authenticated page (`static/activityPanel.js`).

The user's two stated UX needs that drove Wave 5 directly:

- *"when we are going from tab to tab, we are not refreshing the information. When we do an analysis or a DNA update, we let that run regardless of what tab they are on"* → Wave 5 Fix 2.
- *"I do not see a lot of the new features on the website"* → Wave 5 Fix 3.

The user invoked `/close-out` immediately after Wave 5 was pushed; they did NOT pick a next direction, and the post-Wave-5 `/assess` has not been run.

---

## KEY COORDINATES & FACTS

| Item | Value |
|---|---|
| Public URL | `https://autoclip.studio` |
| Deploy VM | `147.182.136.107` |
| Container image | `ghcr.io/reese8272/creatorclip:latest` |
| Branch policy | `main` is the only branch; pushing to `main` triggers the Docker publish → workflow_run → Deploy pipeline |
| Alembic head | `0013_refund_pack_id_unique` (Wave 4 Fix 2) |
| Latest assessment | `docs/assessment/REPORT.md` (post-Wave-4 — STALE; Wave 5 not yet captured) |
| Assessment history | `docs/assessment/history/2026-05-31-post-wave-{1,2,3,4}-REPORT.md` |
| OFF_COURSE_BUGS additions this session | slowapi 429 TestClient collision (logged; workaround = per-creator session cookie keys the limiter per-UUID, not per-IP) |
| `CLAUDE.md` pre-monetization | YouTube data-retention/refresh fully compliant ✅; Google OAuth app verification ❌ (external — user action) |
| Memory dir | `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` |
| Secret names (NEVER log values) | `STRIPE_SECRET_KEY`, `JWT_SECRET`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `GOOGLE_OAUTH_CLIENT_SECRET`, `TOKEN_ENCRYPTION_KEY`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` |
| Last successful prod commit | `67fddc9` (Deploy run `26717189318`, 2026-05-31 15:50) |
| Failed Wave-5 Deploy | `gh run view 26717953473` — annotation: GitHub Actions billing problem (NOT a code defect) |
| Failing CI run on HEAD | `gh run view 26717928786` — `tests/test_billing.py::test_checkout_offloads_sync_stripe_to_thread` returns 503 (STRIPE_SECRET_KEY unset on CI) |

---

## CONSTRAINTS & GOTCHAS (next session: read before acting)

- **Pushing to `main` deploys to production.** No staging gate. The image goes to autoclip.studio on every green push. **Until Blocker B is resolved, Deploy will not run regardless of what you push** — the image still publishes, just doesn't deploy.
- **Production is currently STALE relative to `main`.** Wave 5 is committed but not deployed. Don't tell the user "the activity panel is live in prod" until you have verified `view-source:https://autoclip.studio/` contains `/static/activityPanel.js`.
- **RLS posture:** request-scoped sessions use `AsyncSessionLocal` with `session.info["creator_id"]` set. Refund and worker actions use `AdminSessionLocal()` (BYPASSRLS by design — the refund Beat task and stream-cleanup background tasks have no JWT to derive `creator_id` from).
- **slowapi rate-limit collision trap in TestClient.** Tests that hit a rate-limited router and use a shared in-memory limiter will collide at 30+/min on full-suite runs. Workaround: each test creates its own `Creator` row and uses `create_session_token(creator.id)` so slowapi keys per-creator-UUID. Logged in `docs/OFF_COURSE_BUGS.md`.
- **`AsyncMock(return_value=_FakeSession())` does NOT work for `AdminSessionLocal()` patching** — the factory is called synchronously by `async with`. Use `MagicMock(return_value=_FakeSession())`.
- **Refund SAVEPOINT requires `await session.rollback()` after `IntegrityError`.** The partial-UNIQUE catch in `billing/refund.py` MUST roll back the txn before returning 0; otherwise the next DB call in the same session errors with "current transaction is aborted".
- **`YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS=30` is ToS-mandated upper bound** (§III.E.4.b). Do not increase it. Decreasing is fine if storage cost demands it.
- **`# type: ignore` comments on Anthropic SDK call sites are obsolete after the SDK bump.** If/when Issue 84 follow-up lands, sweep them; mypy will surface the unused ones with `--warn-unused-ignores`.
- **Pre-existing `Event loop is closed` warnings in `tests/test_progress.py`** are a SEV2 carry-forward — not a Wave 5 regression. Don't chase them.
- **No virality promise anywhere.** Structural test pins this. Don't accidentally add one in marketing copy or LLM prompts during feature work.
- **OAuth tokens are Fernet-encrypted at rest** and must be read via `decrypt()`. Never log token values or return them in API responses.
- **Per-creator isolation on every query** touching creator-scoped tables. Missing `WHERE creator_id = ...` is a BLOCKER for review.

---

## POINTERS

- `docs/SOT.md` — current stack, file structure, schema (static/ tree now includes `activeTasks.js` + `activityPanel.js`)
- `docs/PROJECT_STATE.md` — every issue's status and session log
- `docs/issues.md` — work queue (Issues 84, 89, 90, 91, 92, 98 ✅; 93-100 ready)
- `docs/DECISIONS.md` — deviation log (Waves 1-5 entries in reverse-chronological order)
- `docs/COMPLIANCE.md` — YouTube ToS, retention, privacy posture (§2 expanded Wave 4)
- `docs/CLIPPING_PRINCIPLES.md` — named principles the engine cites
- `docs/OFF_COURSE_BUGS.md` — incidental defects log
- `docs/assessment/REPORT.md` — latest verdict (CONDITIONAL — stale, pre-Wave-5)
- `CLAUDE.md` — project rules; the One Rule (research industry standard FIRST) is non-negotiable
- Memory: `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/MEMORY.md`
