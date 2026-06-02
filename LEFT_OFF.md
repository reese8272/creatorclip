# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a
> source of truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-06-02 (Issue 122 + /assess post-Issues-120-122)
**Branch:** `main` — HEAD `1d5fe34`
**Sync with `origin/main`:** **1 ahead / 0 behind — NOT YET PUSHED**
**Working tree:** CLEAN
**CI (last 5 runs):** Production health check — all `skipped` (scheduled probe; quality gate runs on push)

---

## CURRENT FOCUS

### Push Issue 122, then sweep the 5 open SEV1s as Issue 123

Issue 122 (persistent activity logging) is committed but not pushed. Before pushing, patch the
one SEV2 found in the /assess that lives in the new code: `POST /api/activity` has no rate
limiter. After pushing, the next session is a focused SEV1 sweep (~80 LOC).

### → NEXT ACTION

**Step 1 — Patch activity endpoint rate limiter before pushing**

```python
# routers/activity.py — add before the route function:
from limiter import limiter
from slowapi.util import get_remote_address

@router.post("", status_code=204, include_in_schema=False)
@limiter.limit("200/minute", key_func=get_remote_address)
async def record_activity(event: ActivityEvent, request: Request) -> None:
```

Then run `.venv/bin/python3 -m pytest tests/test_activity.py -q` to confirm still passing, then push:

```bash
git add routers/activity.py
git commit -m "fix(122): rate-limit POST /api/activity at 200/min by IP"
git push
```

**Step 2 — File + build the SEV1 sweep as Issue 123**

All 5 findings are in `docs/assessment/REPORT.md` under "SEV1 — must fix before production deploy".
Run `/issue-workflow` with this brief:

> Issue 123 — SEV1 sweep from /assess post-Issues-120-122
>
> Fix all 5 open SEV1s:
>
> 1. `routers/insights.py:386–395` — `analyze_performer` constructs `anthropic.Anthropic()` per
>    request with no prompt caching and no rate limit. Move to module-level singleton:
>    `_ANTHROPIC = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=120,
>    max_retries=2)`. Add `cache_control: ephemeral` on system prompt. Add
>    `@limiter.limit("10/hour", key_func=creator_key)` decorator.
>
> 2. `ingestion/transcribe.py:78–87` — `_DEEPGRAM_CLIENT` singleton has no `threading.Lock`.
>    Two threads via `asyncio.to_thread` can double-initialize. Add:
>    `_DEEPGRAM_LOCK = threading.Lock()` at module level; guard init with
>    `with _DEEPGRAM_LOCK: if _DEEPGRAM_CLIENT is None: ...`
>
> 3. `ingestion/transcribe.py:179–186` — `_ASSEMBLYAI_READY` flag has no `threading.Lock`.
>    Same race. Add `_ASSEMBLYAI_LOCK = threading.Lock()`; wrap `if not _ASSEMBLYAI_READY:` block.
>
> 4. `models.py:724–757` — `CreatorInsight` missing composite index on `(creator_id, video_id)`.
>    Add `__table_args__ = (sa.Index("ix_creator_insight_creator_video", "creator_id",
>    "video_id"),)` to the model class; add migration `0020_creator_insight_index`.
>
> 5. `db.py:80–103` — `recreate_engine()` is public with no re-entry guard. Concurrent Celery
>    prefork calls race on the module-global engine references. Add `_engine_recreating: bool =
>    False` flag + guard, or rename to `_recreate_engine` (underscore prefix).

**Step 3 — Locust load test on staging VM (user-side, ~10 min)**

This is the sole remaining structural gate for CONDITIONAL → YES.

```bash
# SSH to prod VM, then:
CC_BASE_URL=http://localhost:8001 \
locust -f /app/tests/perf/locustfile.py --host http://localhost:8001 \
    --users 300 --spawn-rate 20 --run-time 5m --headless \
    --csv /tmp/loadtest && cat /tmp/loadtest_stats.csv
```

Pass: p99 < 500ms on key routes, error rate < 1%, no `QueuePool limit` errors.

---

## WHAT WORKS NOW (do not re-investigate)

### This session (2026-06-02)

- **Issue 122 — Persistent activity logging (committed `1d5fe34`, not yet pushed):**
  - `observability.configure_logging()` now accepts `log_dir`; adds `RotatingFileHandler`
    (10 MB × 5 files, JSON) at `/app/logs/app.log` — readable at `./logs/app.log` on the host
    via the existing `.:/app` Docker volume (no extra mount needed)
  - `POST /api/activity` — fire-and-forget telemetry endpoint; no auth required; logs
    `ui_activity` events via `log_event()`; caps extra keys (10) and string values (200 chars)
  - `static/activity.js` — 40-line IIFE captures click/submit/navigate on all 6 templates
  - `LOG_DIR` added to `config.py` + `.env.example`; `LOG_DIR=""` in test conftest
  - 10 tests pass; full suite 678 passed / 2 skipped
  - Review logs: `tail -f logs/app.log` or `cat logs/app.log | grep '"event":"ui_activity"'`

- **Issue 121 — Video analysis page + dashboard de-emphasis (deployed `a68108c`):**
  - `POST /creators/me/video-analysis` → Celery → Claude streaming via SSE
  - `static/analysis.html` — URL + query form → streaming narrative prose
  - "Analyze a video" primary CTA on dashboard; "Link a video" collapsed to `<details>`

- **Full /assess run — REPORT.md fully refreshed:**
  - VERDICT: CONDITIONAL — 0 BLOCKERs, 5 SEV1s, ~12 SEV2s
  - 7 of 12 modules clean: analysis ✅, billing ✅, dna ✅, preference ✅, upload_intel ✅
  - New ingestion SEV1s (threading races) are the only genuinely NEW findings
  - Full register: `docs/assessment/REPORT.md`
  - Snapshot: `docs/assessment/history/2026-06-02-post-issues-120-122-REPORT.md`

### Longer-standing landmarks (verified, do not re-check)

- **Previous BLOCKER fixed:** `improvement_briefs UNIQUE(creator_id)` — migration 0016 applied to prod
- **Production live:** `https://autoclip.studio` healthy
- **Alembic head:** `0019_clip_style_preset` — prod and local in sync
- **RLS** on 12 tenant-owned tables; `creators` table deliberately exempt (CI test should exist)
- **Self-hosted runner deploy pipeline:** push to `main` → Docker publish → deploy (no staging gate)
- **Stripe billing, OBS API key surface, walkthrough gate, design system** — all deployed

---

## THE ARC THAT LED HERE

1. Issues 113–119 UX wave + Issue 120 DNA caps built and deployed last session.
2. Production incident: YouTube token expiry mid-catalog-sync on long channels — fixed (`01d6de7`).
3. This session: /assess surfaced 5 SEV1s (2 new ingestion threading races; 3 carry-forwards).
4. Issue 122 built: persistent log files so tester sessions (like brother's) survive container restarts.
5. /assess re-run with full 12-module sweep; REPORT.md refreshed; committed but not pushed.

---

## KEY COORDINATES & FACTS

| Item | Value |
|---|---|
| Public URL | `https://autoclip.studio` |
| Production VM | `147.182.136.107` |
| Container image | `ghcr.io/reese8272/creatorclip:latest` |
| Repo | `github.com/reese8272/creatorclip` |
| Self-hosted runner | systemd `actions.runner.reese8272-creatorclip.autoclip-prod-vm` on prod VM |
| HEAD (not pushed) | `1d5fe34` |
| Alembic head | `0019_clip_style_preset` |
| Next migration | `0020_creator_insight_index` (Issue 123 step 4) |
| Activity log path | `./logs/app.log` on host (inside container: `/app/logs/app.log`) |
| /assess verdict | CONDITIONAL — 0 BLOCKERs / 5 SEV1s — `docs/assessment/REPORT.md` |
| Issue 123 scope | 5 SEV1s — ingestion lock (×2), insights singleton, CreatorInsight index, recreate_engine |
| Default model | `claude-sonnet-4-6` (Sonnet 4.6 1M) |
| Memory dir | `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` |
| Secret names (never log) | `STRIPE_SECRET_KEY`, `JWT_SECRET_KEY`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `GOOGLE_OAUTH_CLIENT_SECRET`, `TOKEN_ENCRYPTION_KEY`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `GHCR_TOKEN`, `DEEPGRAM_API_KEY` |

---

## CONSTRAINTS & GOTCHAS (next session: read before acting)

- **Commit `1d5fe34` is NOT PUSHED.** Patch `routers/activity.py` rate limiter (Step 1 above) before pushing — the endpoint is currently unratelimited.
- **Pushing `main` auto-deploys to production.** Self-hosted runner: Docker publish → deploy. No staging gate. Each push = a production cut.
- **`ruff format --check` is a CI gate; Layer 0 only runs `ruff check`.** Always run `ruff format .` before pushing or the CI lint step fails even if `ruff check` passed locally.
- **Integration tests always fail in CI** — need live Postgres; CI doesn't provision one. Does NOT block deploy. Do not change CI config to fix this.
- **`analyze_performer` endpoint has no rate limiter** (Issue 123 SEV1 #1). Until Issue 123 ships, any authenticated creator can exhaust the Anthropic quota.
- **Ingestion threading races** (Issue 123 SEV1 #2–3) only manifest under concurrent workers (`asyncio.to_thread` on the same Celery worker process). Not visible in unit tests.
- **`CreatorInsight` full-table-scan** (Issue 123 SEV1 #4) will worsen silently as AI insights accumulate. No index yet.
- **`tests/_helpers.py::override_current_creator`** must be used instead of `lambda: creator` in ALL test dependency overrides for `get_current_creator`.
- **`LOCAL_MEDIA_DIR` validator is relaxed** (Issue 110 hotfix): only fails fast in production when `STORAGE_BACKEND=local`. Do NOT revert.
- **OAuth tokens Fernet-encrypted at rest.** Read via `decrypt()`; never log.
- **Per-creator `WHERE` on every query.** Missing filter = BLOCKER (RLS is backstop, not substitute).
- **`YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS=30`** is a YouTube ToS hard limit. Do NOT increase.

---

## POINTERS

- `docs/SOT.md` — current stack, file structure, data model
- `docs/PROJECT_STATE.md` — every issue's status + session log
- `docs/issues.md` — backlog (Issue 122 closed; Issue 123 is the next item)
- `docs/DECISIONS.md` — deviation log
- `docs/COMPLIANCE.md` — YouTube ToS, retention, privacy posture
- `docs/CLIPPING_PRINCIPLES.md` — named principles registry
- `docs/assessment/REPORT.md` — current `/assess` verdict (post Issues 120–122)
- `CLAUDE.md` — project rules; the One Rule is non-negotiable
- Memory: `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/MEMORY.md`
