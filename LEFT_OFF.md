# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-07-02  
**Branch at close:** `fix/llm-idempotency-and-test-hardening` — all commits merged; origin/main @ `2dbd17c`, origin/staging @ `9e5027a`  
**Working tree:** 2 modified docs (`PROJECT_STATE.md`, `OFF_COURSE_BUGS.md`) uncommitted + untracked debug artifacts (HAR/PNG — ignore)  
**CI/Deploy:** Docker publish + Deploy to production queued on main (`2dbd17c`) — triggered by PR #40 merge at 12:38 UTC; staging CI also queued

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source of truth.

---

## CURRENT FOCUS

**CI hardening is done and merged to main.** All 10 gating CI checks are green on PR #39/#40. The two failing jobs (Playwright / visual regression) are a pre-existing runner infra problem — `libatk-1.0.so.0` absent, no passwordless sudo — identical to what failed on PR #38. No tests actually ran in either job; they are not a code regression.

### → NEXT ACTION

1. **Confirm prod deploy succeeded** once Docker publish + Deploy finish:
   ```bash
   gh run list --limit 5
   ```
2. **Commit the two modified docs** (already correct):
   ```bash
   git add docs/PROJECT_STATE.md docs/OFF_COURSE_BUGS.md LEFT_OFF.md
   git commit -m "chore: update project state + off-course bug log after CI hardening merge"
   ```
3. **Start W1 — pick up Issue 352** (SEV2 tracker, ~54 items): run `/issue-workflow 352` and read `docs/issues.md` for the prioritised lead items.
   - Top W1 SEV2 candidates: `activity.py` log-injection, `config.py ENV: Literal[...]` hardening, rate-limit bypass on unauthenticated endpoints.
4. **Playwright runner gap** — promote the `OFF_COURSE_BUGS.md` entry into `docs/issues.md` as an infra task.

---

## WHAT WORKS NOW

**Core loop:** upload → ingest → transcribe → clip → render → playback via R2 presigned URLs — verified end-to-end in prod (prior session).

**All 7 W0 SEV1 issues closed, deployed, and verified on prod:**

| Issue | What was fixed | Key file(s) |
|-------|---------------|-------------|
| 345 | Stripe v8 `max_network_retries=3` moved into `StripeClient()` constructor | `billing/stripe_client.py` |
| 346 | React root error boundary (`<RootError />`) + `createRoot` uncaught/recoverable error hooks | `frontend/src/App.tsx`, `main.tsx` |
| 347 | `event_log.py` connection pool pinned to `pool_size=2, max_overflow=3` | `event_log.py`, `docs/DEPLOYMENT.md` |
| 348 | Chat worker uses `AsyncSessionLocal` + GUC; migration `0040` adds subquery RLS to 5 child tables | `worker/tasks.py`, `alembic/versions/0040_rls_chat_child_tables.py` |
| 349 | `_send_notification_async` commits DB before mailer call; `asyncio.wait_for(to_thread(...), timeout=RESEND_TIMEOUT_S)` | `worker/tasks.py`, `config.py`, `.env.example` |
| 350 | `improvement/brief.py` `pause_turn` loop (≤5 rounds); streaming switched to `stream_message`; `max_uses: 5` on `web_search` | `improvement/brief.py` |
| 351 | ruff 21→0, mypy 2→0; `test_stripe_max_retries` updated for v8 attribute path | `tests/`, `routers/thumbnails.py`, `routers/insights.py` |

**CI hardening now also merged (2026-07-02, `2dbd17c` on main):**

| Fix | File(s) |
|-----|---------|
| Billing idempotency — 5 analysis tasks split at billing boundary, no double-charge on retry | `worker/tasks.py` |
| `transcribe_video` SoftTimeLimitExceeded sets `failed` before re-raise | `worker/tasks.py` |
| `knowledge/thumbnails.py` pause_turn loop (≤5 rounds, `stream_message`) | `knowledge/thumbnails.py` |
| mypy 0 errors with pydantic plugin — `improvement/brief.py` unused `[possibly-undefined]` fixed | `improvement/brief.py` |
| cv2/libGL: `importorskip("cv2")` + `@skipif` guards | `tests/test_render.py`, `tests/test_reframe.py` |
| lightgbm/libgomp: `OSError` guard at call site, not import time | `tests/test_preference.py`, `tests/eval/test_efficacy.py` |
| Health test isolation: uninvolved services mocked | `tests/test_health.py` |
| `test_issue_104.py` async event-loop: `asyncio.run()` → `async def` | `tests/test_issue_104.py` |
| ruff format — 48 files | (whole codebase) |

**Prod deploy state (pending — image queued for `2dbd17c`):**
- Previous image: `ghcr.io/reese8272/creatorclip:latest` @ `10d8b74`
- Alembic at `0040 (head)` — no new migrations in this PR
- Layer-0 gates: ruff 0 · mypy 0 (pydantic plugin) · coverage ~79% · bandit clean

---

## THE ARC THAT LED HERE

1. Issue 343 activated the RLS role split (app role = no BYPASSRLS; `AdminSessionLocal` = superuser).
2. Ingest/render loop fixed via migration `0039` — source video retained; audio split to `audio_uri`.
3. 2026-07-01 automated assessment surfaced 7 W0 SEV1 beta-gate issues + ~54 W1 SEV2s.
4. All 7 W0 SEV1s built, tested, committed, merged to main + staging, deployed, migration applied.
5. 2026-07-01/02: full-system verification (Rungs 1+2) on `fix/llm-idempotency-and-test-hardening` found 3 LLM blockers + 25 test failures. Fixed across 6 commits; each CI run exposed one new layer (ruff → mypy → cv2 → lightgbm → health isolation → asyncio.run() → mypy pydantic unused-ignore).
6. PR #39 merged to staging, PR #40 merged to main (2026-07-02 12:38 UTC). Docker publish + deploy queued.

---

## KEY COORDINATES & FACTS

| Item | Value / location |
|------|-----------------|
| Prod VM | `creatorclip-vm` — `147.182.136.107`; SSH via `~/.ssh/id_ed25519`; credentials in 1Password |
| Prod compose dir | `/opt/autoclip/` — `docker-compose.prod.yml` + `.env` |
| Prod .env | `/opt/autoclip/.env` — edited directly on VM; not committed to git |
| Current alembic head | `0040` (`0040_rls_chat_child_tables.py`) — no new migrations in CI-hardening PR |
| origin/main SHA | `2dbd17c` (PR #40 merge, 2026-07-02) |
| origin/staging SHA | `9e5027a` (PR #39 merge, 2026-07-02) |
| CI trigger | Push to `main` → Docker publish → Deploy to production (auto, ~2 min end-to-end) |
| mypy baseline | `docs/assessment/baselines.json` — `"mypy_errors": 0`; confirmed 0 with pydantic plugin |
| Assessment report | `docs/assessment/REPORT.md`; history snapshot: `docs/assessment/history/2026-07-01-REPORT.md` |
| Next issue | Issue 352 (W1 SEV2 tracker) — `docs/issues.md` |
| Stripe webhook secret | `STRIPE_WEBHOOK_SECRET` env var |
| Resend API key | `RESEND_API_KEY` env var (not yet set on prod — email send will no-op until configured) |
| Playwright gap | `libatk-1.0.so.0` missing on runner — logged `docs/OFF_COURSE_BUGS.md` |

---

## CONSTRAINTS & GOTCHAS

- **Push to `main` triggers a prod deploy automatically** — CI: push → Docker publish → Deploy workflow. No manual step needed; be intentional about merging.
- **Playwright + visual regression always fail on the self-hosted runner** — `libatk-1.0.so.0` absent, no passwordless sudo. These are NOT code regressions; no tests execute. Don't chase them. The jobs have failed on every PR including the ones that shipped correctly to prod.
- **mypy with pydantic plugin**: CI installs pydantic via `pydantic-settings==2.6.1` + mypy 1.14.1 (`requirements-dev.txt`). Local mypy without pydantic shows different results. To reproduce CI mypy locally: `python3 -m venv /tmp/v && /tmp/v/bin/pip install mypy==1.14.1 pydantic pydantic-settings && /tmp/v/bin/mypy <sources> --ignore-missing-imports`. `warn_unused_ignores = true` in `pyproject.toml` means any `# type: ignore` that doesn't suppress a real error is itself an error.
- **`asyncio_mode = auto` in `pytest.ini`** — all `async def` tests are managed by pytest-asyncio. Never wrap async logic in `asyncio.run()` inside a test; it closes the event loop and breaks teardown in subsequent tests, surfacing as `RuntimeError: Event loop is closed` in an unrelated test.
- **lightgbm/libgomp**: `pytest.importorskip("lightgbm")` is NOT enough — the package imports fine but `fit()` raises `OSError: libgomp.so.1` at call time. Guard with `try/except OSError: pytest.skip(...)` around the actual call, not the import.
- **`AsyncSessionLocal` vs `AdminSessionLocal`** — app role (RLS enforced) vs superuser (BYPASSRLS). New per-creator queries use `AsyncSessionLocal` with `session.info["creator_id"]` set. Cross-tenant sweeps use `AdminSessionLocal`.
- **`pause_turn` loop** — `max_uses: 5` on `web_search` bounds the loop in both `improvement/brief.py` and `knowledge/thumbnails.py`. Do not remove it.
- **`test_notifications.py` jinja2 dependency** — three tests fail locally without jinja2; pass in Docker. Install `jinja2` locally or use Docker for the full suite.
- **`RESEND_API_KEY` not yet set on prod** — email is a no-op. Set in `/opt/autoclip/.env` when ready.
- **`docs/assessment/REPORT.md` is a 2026-07-01 snapshot** — use `docs/issues.md` for the live issue queue.
- **Untracked files at repo root** (`autoclip.studio.har`, `error.png`, `render loop.png`, `rendered-clip.png`) — debug artifacts, safe to delete.

---

## POINTERS

| Doc | Purpose |
|-----|---------|
| `docs/SOT.md` | Architecture, schema, file structure |
| `docs/issues.md` | Live issue queue — Issue 352 is next |
| `docs/PROJECT_STATE.md` | Session log, what's done |
| `docs/DECISIONS.md` | All architectural deviations |
| `docs/COMPLIANCE.md` | YouTube ToS + data retention |
| `docs/CLIPPING_PRINCIPLES.md` | Named principles the clip engine cites |
| `docs/OFF_COURSE_BUGS.md` | Off-course defect triage (Playwright runner gap logged here) |
| `docs/DEPLOYMENT.md` | Connection budget, deploy runbook, Helm chart |
| Memory dir | `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` |
