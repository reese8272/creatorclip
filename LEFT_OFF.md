# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a
> source of truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-06-07 (CI green — all gates pass)
**Branch:** `main` — HEAD `e454bdb` (synced with origin/main)
**Working tree:** CLEAN
**CI (last push):** Quality Gates ✅ · Integration tests ✅ · CI ✅ · Deploy ✅

---

## CURRENT FOCUS

All pending work from the last handoff is done. Ready to start Issue 130.

### → NEXT ACTION

**Issue 130 — Hook analyzer** (via the issue workflow):

```
Issue 130 — Hook analyzer
Approach: POST /creators/me/videos/{video_id}/hook-analysis → Celery task →
first-30s retention drop detection grounded in creator's own retention curves
+ concrete rewrite suggestion. Mirrors Issues 128/129 endpoint pattern.
Good to go?
```

> `/claude-api` skill required before writing any Anthropic SDK code (CLAUDE.md).

---

## WHAT HAPPENED THIS SESSION

### CI was failing on coverage gate

After the two Issue 123 commits (`56c6d34`, `2ae7ad6`) landed on main, Quality Gates
was failing with coverage at 74.4% vs the 75.2% baseline. Three fixes in sequence:

1. **`tests/test_analyze_performer.py`** — 8 new tests covering all code paths of
   `analyze_performer` (routers/insights.py) — invalid UUID, 404, cache hit, LLM
   success, empty-content fallback, LLM error. Coverage → 75.12%.
2. **`tests/test_billing.py`** — `test_webhook_malformed_creator_id_returns_ignored`
   covers the new `except ValueError` guard added in Issue 123. Coverage → ≥ 75.2%.
3. **`alembic/versions/0020_creator_insight_index.py`** — Changed `CREATE INDEX
   CONCURRENTLY` (via `op.execute("COMMIT")` hack) to `op.create_index()`, which
   runs inside Alembic's transaction block and works with psycopg3. Integration
   tests were failing because the COMMIT hack doesn't work with psycopg3.

### Production migration note

Migration `0020` now uses a plain `CREATE INDEX` instead of `CREATE INDEX
CONCURRENTLY`. If the production `creator_insights` table has grown large before
this migration runs, there will be a brief table lock. The table contains AI-generated
insights (low write volume) so this is acceptable.

**Apply on production:**
```bash
# SSH to prod VM, then inside the app container:
docker compose -f docker-compose.prod.yml exec app alembic upgrade head
```

---

## WHAT WORKS NOW (do not re-investigate)

### Issue 123: SEV1 sweep — committed, pushed, deployed

1. **`routers/insights.py`** — `_ANTHROPIC` module-level singleton + `cache_control: ephemeral` on static system prompt + `asyncio.to_thread` + token usage logging.
2. **`ingestion/transcribe.py`** — `_DEEPGRAM_LOCK` + `_ASSEMBLYAI_LOCK` (threading.Lock); double-checked locking on both singleton init blocks.
3. **`models.py`** — `CreatorInsight.__table_args__` adds composite index `ix_creator_insight_creator_video`.
4. **`alembic/versions/0020_creator_insight_index.py`** — plain `op.create_index()`. Alembic head is `0020`.
5. **`db.py::recreate_engine`** — `_recreate_in_progress` bool flag + `try/finally`; concurrent Celery prefork calls skip rather than race.

### Worker + billing fixes

- **`worker/tasks.py`** — module-level `_thumb_redis()` singleton.
- **`knowledge/util.py`** — `extract_transcript_text(segments_jsonb, max_chars)` shared util.
- **`routers/billing.py`** — webhook UUID parse guarded (`try/except ValueError`).
- **`youtube/analytics.py`** — `check_data_gate` `creator_id: uuid.UUID` type annotation.

### Deployed issues

| Issue | Status | HEAD at deploy |
|---|---|---|
| Issue 128 — title optimizer | ✅ Deployed | `e3c83b2` |
| Issue 129 — thumbnail concepts | ✅ Deployed | `56c6d34` (now `e454bdb`) |
| Issue 123 — SEV1 sweep | ✅ Deployed | `e454bdb` |
| Issue 130 — hook analyzer | 🔲 Not started | — |

---

## KEY COORDINATES & FACTS

| Item | Value |
|---|---|
| Public URL | `https://autoclip.studio` |
| Production VM | `147.182.136.107` |
| Container image | `ghcr.io/reese8272/creatorclip:latest` |
| Repo | `github.com/reese8272/creatorclip` |
| Self-hosted runner | systemd `actions.runner.reese8272-creatorclip.autoclip-prod-vm` on prod VM |
| Current branch | `main` |
| Local HEAD | `e454bdb` (synced with origin/main) |
| Alembic head (local + prod, after migration) | `0020_creator_insight_index` |
| Issue 123 | ✅ Committed + deployed |
| Issue 128 | ✅ Deployed |
| Issue 129 | ✅ Committed + deployed |
| Issue 130 | 🔲 Not started — Hook analyzer |
| CI state | All green ✅ |
| Default model | `claude-sonnet-4-6` |
| Secret names (never log) | `STRIPE_SECRET_KEY`, `JWT_SECRET_KEY`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `GOOGLE_OAUTH_CLIENT_SECRET`, `TOKEN_ENCRYPTION_KEY`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `GHCR_TOKEN`, `DEEPGRAM_API_KEY` |

---

## CONSTRAINTS & GOTCHAS

- **`git push` auto-deploys to production** via self-hosted runner. Verify `git status` + test count before pushing.
- **Production migration pending:** Run `alembic upgrade head` on prod to apply `0020`. Until then, `creator_insights` queries do full table scans.
- **`/claude-api` skill is mandatory** before writing any Anthropic SDK code (CLAUDE.md One Rule).
- **`ruff format --check` is a CI gate** — always run before pushing.
- **Rate-limit test pollution (local only):** if `test_improvement_post_handles_concurrent_insert_race` fails with 429, run `redis-cli del "LIMITS:LIMITER/testclient//creators/me/improvement-brief/10/1/hour"`.

---

## POINTERS

- `docs/SOT.md` — current stack, file structure, data model
- `docs/PROJECT_STATE.md` — every issue's status + session log
- `docs/issues.md` — backlog (123, 128, 129 ✅; 130–136 queued)
- `docs/DECISIONS.md` — deviation log (2026-06-07 entries for Issues 128 + 129)
- `docs/assessment/REPORT.md` — latest /assess verdict + ranked register
- `docs/COMPLIANCE.md` — YouTube ToS, data retention, privacy posture
- `CLAUDE.md` — project rules; the One Rule is non-negotiable
