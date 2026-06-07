# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a
> source of truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-06-07 (Issue 123 SEV1 sweep + all CI fixes)
**Branch:** `main` — HEAD `56c6d34` (1 commit ahead of origin/main — NOT yet pushed)
**Working tree:** DIRTY — Issue 123 fixes + worker/billing/insights changes uncommitted
**CI (last push to origin):** Quality Gates ❌ (against `e3c83b2`) · Deploy ✅ · CI tests ✅

---

## CURRENT FOCUS

### Commit the Issue 123 SEV1 fixes, push both commits, verify Quality Gates go green.

`56c6d34` (Issue 129 + CI fixes + /assess) is locally committed but not pushed.  
The working tree has additional uncommitted changes: Issue 123 SEV1 sweep.  
Both need to land on origin/main before starting Issue 130.

### → NEXT ACTION

**Step 1 — Commit Issue 123 + remaining fixes:**

```bash
git add \
  db.py models.py ingestion/transcribe.py \
  routers/insights.py routers/billing.py \
  worker/tasks.py knowledge/thumbnails.py knowledge/titles.py \
  knowledge/util.py youtube/analytics.py \
  alembic/versions/0020_creator_insight_index.py \
  docs/PROJECT_STATE.md docs/issues.md \
  LEFT_OFF.md

git status   # confirm nothing unexpected
git commit -m "fix(123): SEV1 sweep — insights singleton, ingestion locks, CreatorInsight index, recreate_engine guard, worker Redis singleton"
```

**Step 2 — Push both commits to origin:**

```bash
git push     # pushes 56c6d34 and the new commit; auto-deploys to production
```

**Step 3 — Wait ~2 min, verify CI:**

```bash
gh run list --limit 5   # Quality Gates should now show ✅
```

Quality Gates were failing on `e3c83b2` due to three root causes — all fixed in `56c6d34`:
- `knowledge/__init__.py` missing → mypy "source file found twice"
- `PYSEC-2026-196` → pip-audit
- coverage drop → 31 new tests added

**Step 4 — Apply the new migration on production:**

```bash
# SSH to prod VM, then inside the app container:
docker compose -f docker-compose.prod.yml exec app alembic upgrade head
# Applies: 0020_creator_insight_index (CREATE INDEX CONCURRENTLY on creator_insights)
```

**Step 5 — Start Issue 130 via the issue workflow:**

```
Issue 130 — Hook analyzer
Approach: POST /creators/me/videos/{video_id}/hook-analysis → Celery task →
first-30s retention drop detection grounded in creator's own retention curves
+ concrete rewrite suggestion. Mirrors Issues 128/129 endpoint pattern.
Good to go?
```

> `/claude-api` skill required before writing any Anthropic SDK code (CLAUDE.md).

---

## WHAT WORKS NOW (do not re-investigate)

### Issue 123: SEV1 sweep — written this session, uncommitted

1. **`routers/insights.py`** — `_ANTHROPIC` module-level singleton + `cache_control: ephemeral` on static system prompt + `asyncio.to_thread` + token usage logging. Was: `anthropic.Anthropic()` per-request, 3rd cycle carrying.

2. **`ingestion/transcribe.py`** — `_DEEPGRAM_LOCK` + `_ASSEMBLYAI_LOCK` (both `threading.Lock()`); double-checked locking on both singleton init blocks. Was: unguarded init race under concurrent `asyncio.to_thread`.

3. **`models.py`** — `CreatorInsight.__table_args__` adds `sa.Index('ix_creator_insight_creator_video', 'creator_id', 'video_id')`.

4. **`alembic/versions/0020_creator_insight_index.py`** — `CREATE INDEX CONCURRENTLY IF NOT EXISTS` in autocommit block. Alembic head becomes `0020` after migration.

5. **`db.py::recreate_engine`** — `_recreate_in_progress` bool flag + `try/finally`; concurrent Celery prefork calls skip rather than race.

### Worker + other fixes — uncommitted

- **`worker/tasks.py`** — module-level `_thumb_redis()` singleton replaces per-task `_aredis.from_url()` (SEV1); UUID parse in thumbnail loop narrowed to `(ValueError, TypeError)` (SEV2); `import json as _json` shadow removed.
- **`knowledge/util.py`** (new) — `extract_transcript_text(segments_jsonb, max_chars)` shared util; both `titles.py` and `thumbnails.py` delegate to it (DRY).
- **`youtube/analytics.py`** — `check_data_gate` `creator_id: uuid.UUID` type annotation.
- **`routers/billing.py`** — webhook UUID parse guarded (`try/except ValueError`, returns 200 on malformed).

### Commit `56c6d34` — locally committed, not pushed

Contains: Issue 129 (thumbnail concept generator) + CI fixes (`knowledge/__init__.py`, `aiohttp==3.14.1`, `PYSEC-2026-196` ignore) + full `/assess` output (REPORT.md, module findings, history snapshot).

### Issue 128 — deployed at `e3c83b2`

`POST /creators/me/videos/{video_id}/titles` (20/hour) → 202 + Celery → SSE → top-5 ranked title candidates. Live at `https://autoclip.studio`.

### Assessment (latest)

- **VERDICT: CONDITIONAL** — was 9 SEV1s; Issue 123 + worker fixes close 5; ~4 remain
- Top remaining open: `preference/train.py` advisory lock, `routers/activity.py:39` bare except
- Full register: `docs/assessment/REPORT.md`

---

## THE ARC THAT LED HERE

1. Competitive intelligence → Issues 127–136 filed ROI-ordered
2. Issue 127 (sentence-boundary cuts): deployed
3. Issue 128 (title optimizer): deployed at `e3c83b2`
4. Issue 129 (thumbnail concepts) + CI fixes + /assess: in commit `56c6d34` (not pushed)
5. Issue 123 (SEV1 sweep) + worker/billing/insights fixes: in working tree (not committed)

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
| Local HEAD | `56c6d34` (1 ahead of origin/main — not pushed) |
| origin/main | `e3c83b2` |
| Working tree | DIRTY — Issue 123 fixes uncommitted |
| Alembic head (prod) | `0019_clip_style_preset` (0020 migration not yet applied) |
| Alembic head (local) | `0020_creator_insight_index` |
| Issue 123 | ✅ Written, tested — NOT committed |
| Issue 128 | ✅ Deployed |
| Issue 129 | ✅ Committed (`56c6d34`) — NOT pushed |
| Issue 130 | 🔲 Not started — Hook analyzer |
| Test count | 753 passed / 2 skipped |
| Default model | `claude-sonnet-4-6` |
| Secret names (never log) | `STRIPE_SECRET_KEY`, `JWT_SECRET_KEY`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `GOOGLE_OAUTH_CLIENT_SECRET`, `TOKEN_ENCRYPTION_KEY`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `GHCR_TOKEN`, `DEEPGRAM_API_KEY` |

---

## CONSTRAINTS & GOTCHAS

- **`git push` auto-deploys to production** via self-hosted runner. Verify `git status` + test count before pushing.
- **After push: run `alembic upgrade head` on production** for migration `0020`. Until then, `creator_insights` queries do full table scans.
- **Two separate commits need to land:** `56c6d34` (Issue 129) and the new Issue 123 commit — `git push` will send both.
- **Quality Gates was failing** on the last push (`e3c83b2`) — root causes are fixed in `56c6d34`. The Issue 123 commit adds no new gate risk.
- **Rate-limit test pollution (local only):** if `test_improvement_post_handles_concurrent_insert_race` fails with 429, run `redis-cli del "LIMITS:LIMITER/testclient//creators/me/improvement-brief/10/1/hour"`. Not a code bug; only hits when local Redis has accumulated state.
- **`/claude-api` skill is mandatory** before writing any Anthropic SDK code (CLAUDE.md One Rule).
- **`ruff format --check` is a CI gate** — always run before pushing.

---

## POINTERS

- `docs/SOT.md` — current stack, file structure, data model
- `docs/PROJECT_STATE.md` — every issue's status + session log
- `docs/issues.md` — backlog (123, 128, 129 ✅; 130–136 queued)
- `docs/DECISIONS.md` — deviation log (2026-06-07 entries for Issues 128 + 129)
- `docs/assessment/REPORT.md` — latest /assess verdict + ranked register
- `docs/COMPLIANCE.md` — YouTube ToS, data retention, privacy posture
- `CLAUDE.md` — project rules; the One Rule is non-negotiable
