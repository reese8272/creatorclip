# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a
> source of truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-06-07 (Issues 130 + 131 deployed)
**Branch:** `main` — HEAD `51b73de` (synced with origin/main)
**Working tree:** CLEAN (docs about to be committed)
**CI (last push):** Quality Gates ✅ · Integration tests ✅ · CI ✅ · Docker publish ✅ · Deploy ✅

---

## CURRENT FOCUS

Issues 130 (hook analyzer) and 131 (auto chapter markers) are live in production.
Issue 132 (YouTube Live Chat spike detection) is next in the queue.

### → NEXT ACTION

**Issue 132 — YouTube Live Chat spike detection** (via the issue workflow):

```
Issue 132 — YouTube Live Chat spike detection
Approach: youtube/chat.py fetches live-chat replay → per-minute density signal
merged into Signals.timeline_jsonb["chat_spike_timeline"] → new named principle
"Audience Reaction Spike" weighted in clip_engine/candidates.py.
Good to go?
```

> `/claude-api` skill required before writing any Anthropic SDK code (CLAUDE.md).

---

## WHAT HAPPENED THIS SESSION

Both Issues 130 + 131 built and deployed in one session, with CI debugging.

### Issue 130 — Hook analyzer (knowledge/hooks.py)

`POST /creators/me/videos/{video_id}/hook-analysis`:
- Returns **200** + `{status:"no_data"}` synchronously when no `RetentionCurve` rows exist
  (cheap COUNT check, avoids spawning a Celery task for nothing).
- Returns **202** + `task_id` + `stream_url` when retention data exists.

Celery `analyze_hook` task:
1. Fetches the target video's curve + up to 20 other creator videos' curves.
2. Uses `numpy.interp` to lerp both onto a 1-second grid.
3. Takes per-second median across other videos as the creator baseline.
4. Earliest second where target falls >10pp below median = `retention_drop_at_s`.
5. Calls Claude Haiku 4.5 with `web_search` (1–2 searches) + cached DNA brief →
   `HookReport` (retention_drop_at_s, retention_at_drop, transcript_at_drop, diagnosis,
   rewrite_suggestion, honesty_disclaimer).

Pydantic `HookAnalysisOut` is a **union response model** (all fields optional) so the
OpenAPI schema documents both 200 and 202 shapes truthfully — needed because the
structural test `test_every_documented_json_route_declares_response_model` requires it.

### Issue 131 — Auto chapter markers (knowledge/chapters.py)

`POST /creators/me/videos/{video_id}/chapters` → 202 + task. Celery task:
1. Reads `Signals.timeline_jsonb["silences"]`, filters silences ≥2s as candidates.
2. Enforces 1-per-3-minutes density cap + min 4 chapters (fills evenly for short videos).
3. Always starts at 0:00.
4. Calls Claude Haiku 4.5 with single cached system block (no DNA — chapters describe
   segment content, not channel voice) → ChapterList with `description_block` ready-to-paste.

UI panel includes a copy-to-clipboard button for the description block.

### CI debugging notes (for future reference)

This session went through 5 CI iterations to get green:
1. **Coverage gap** (74.4% → 75.2%): added unit tests for prompt-assembly helpers + early-return branches in the async helpers.
2. **Ruff 0.15.15 stricter than 0.15.14**: caught unused `Signals`/`VideoMetrics` imports + an unsorted import block in worker/tasks.py that local ruff (0.15.14) didn't flag.
3. **Test failures**: 3 assertions were too tight (drop detection at 7s, not 8s) + the response_model gap.
4. **Migration `0020`**: separate prior session, but worth remembering — `op.create_index` works, `CREATE INDEX CONCURRENTLY` + `op.execute("COMMIT")` does not work with psycopg3.

### Production migration note

Still applies from last session: `alembic upgrade head` must be run on the prod VM to
apply migration `0020_creator_insight_index` (separate from this session's work but
not yet executed in production).

```bash
docker compose -f docker-compose.prod.yml exec app alembic upgrade head
```

---

## WHAT WORKS NOW (do not re-investigate)

### Deployed issues

| Issue | Status | HEAD at deploy |
|---|---|---|
| Issue 128 — title optimizer | ✅ Deployed | `e3c83b2` |
| Issue 129 — thumbnail concepts | ✅ Deployed | `56c6d34` |
| Issue 123 — SEV1 sweep | ✅ Deployed | `e454bdb` |
| Issue 130 — hook analyzer | ✅ Deployed | `51b73de` |
| Issue 131 — chapter markers | ✅ Deployed | `51b73de` |
| Issue 132 — chat spike detection | 🔲 Not started | — |

### Test count

821 passed / 2 skipped (up from 753).

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
| Local HEAD | `51b73de` (synced) |
| Alembic head (local) | `0020_creator_insight_index` |
| Alembic head (prod) | `0019_clip_style_preset` (0020 still pending on prod) |
| CI state | All green ✅ |
| Default model (issues 130/131) | `claude-haiku-4-5-20251001` |
| Secret names (never log) | `STRIPE_SECRET_KEY`, `JWT_SECRET_KEY`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `GOOGLE_OAUTH_CLIENT_SECRET`, `TOKEN_ENCRYPTION_KEY`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `GHCR_TOKEN`, `DEEPGRAM_API_KEY` |

---

## CONSTRAINTS & GOTCHAS

- **`git push` auto-deploys to production** via self-hosted runner. Verify `git status` + test count before pushing.
- **Production migration `0020` pending:** Run `alembic upgrade head` on prod when convenient.
- **`/claude-api` skill is mandatory** before writing any Anthropic SDK code (CLAUDE.md One Rule).
- **`ruff format --check` is a CI gate** — always run before pushing.
- **CI ruff version (0.15.15) is stricter than local (0.15.14)** — run `python3.12 -m ruff check .` from project root before pushing to catch issues local CI doesn't.
- **Coverage floor is 75.20%** — adding new modules requires proportional test coverage. Test the prompt-assembly helpers + early-return branches via mocked `AdminSessionLocal`; full Claude-call paths are not realistically testable.
- **Rate-limit test pollution (local only):** if `test_improvement_post_handles_concurrent_insert_race` fails with 429, run `redis-cli del "LIMITS:LIMITER/testclient//creators/me/improvement-brief/10/1/hour"`.
- **psycopg3 + Alembic + CONCURRENTLY**: doesn't work. Use plain `op.create_index()` even though the table may have rows in prod — accept the brief lock.

---

## POINTERS

- `docs/SOT.md` — current stack, file structure, data model
- `docs/PROJECT_STATE.md` — every issue's status + session log
- `docs/issues.md` — backlog (130, 131 ✅; 132–136 queued)
- `docs/DECISIONS.md` — deviation log (2026-06-07 entries for Issues 128, 129, 130, 131)
- `docs/assessment/REPORT.md` — latest /assess verdict + ranked register
- `docs/COMPLIANCE.md` — YouTube ToS, data retention, privacy posture
- `CLAUDE.md` — project rules; the One Rule is non-negotiable
