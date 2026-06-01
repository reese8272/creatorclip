# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a
> source of truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-06-01 (Issues 112 + 113; /assess run; Locust staging ready)
**Branch:** `main` — HEAD `486201e`. Only `main` exists.
**Sync with `origin/main`:** **0 ahead / 0 behind — but LARGE UNCOMMITTED WORKING TREE.**
**Working tree:** Dirty — see below. Nothing pushed this session.
**Production:** ✅ **Current on `486201e` (Issue 110 + docs).** Deploy `26732895767` (35s). `autoclip.studio` does NOT yet have Issue 112 or Issue 113.

---

## CURRENT FOCUS

### Commit + push the session's work, then run migration + Locust

This session produced two complete fixes (Issues 112 + 113) and a full `/assess` run —
none of it is committed yet. The staging stack for the Locust load test is live on the
prod VM and is one manual command away from producing axis-A/E results.

### → NEXT ACTION

**Step 1 — Commit and push everything (excluding `publication testing.png`)**

```bash
git add main.py models.py routers/improvement.py tests/test_health.py tests/test_issue_113.py \
    alembic/versions/0016_improvement_brief_unique.py \
    docker-compose.staging.yml \
    tests/perf/seed_staging.py tests/perf/README.md \
    docs/issues.md docs/PROJECT_STATE.md \
    docs/assessment/REPORT.md docs/assessment/history/2026-06-01-post-issue-112-REPORT.md \
    docs/assessment/modules/_root_infra.md docs/assessment/modules/improvement.md \
    docs/assessment/modules/ingestion.md docs/assessment/modules/routers.md \
    docs/assessment/modules/worker.md docs/assessment/modules/billing.md \
    docs/assessment/modules/dna.md
# Do NOT add publication testing.png
git commit -m "feat(112+113): /health pool fix + improvement_briefs UNIQUE constraint + staging Locust infra"
git push
```

Pushing auto-deploys via the self-hosted runner pipeline. The deploy takes ~35–60s.

**Step 2 — Apply migration 0016 in production**

After the deploy finishes (check `gh run list --limit 3`):
```bash
ssh root@147.182.136.107 "docker exec autoclip-app-1 alembic upgrade head"
```
Expected output: `Running upgrade 0015_creator_api_keys -> 0016_improvement_brief_unique`.

**Step 3 — Complete the Locust test (user-side, ~10 min)**

The staging stack is already running on the prod VM with seeded test data.
The only issue is PgBouncer SCRAM auth; switch to the simple (no-PgBouncer) compose:

```bash
# On the prod VM:
docker stop root-app-1 root-pgbouncer-1
docker compose -f /root/docker-compose.simple.yml up -d app
sleep 8 && curl -s http://localhost:8001/health
# Must return: {"status":"ok","postgres":"ok","redis":"ok"}

# Copy the locustfile to the VM host, install locust, and run:
docker cp autoclip-app-1:/app/tests/perf/locustfile.py /root/locustfile.py
pip3 install locust -q

CC_BASE_URL=http://localhost:8001 \
CC_JWT_SECRET=<JWT_SECRET_KEY from .env> \
CC_CREATOR_ID=00000000-1111-2222-3333-444444444444 \
locust -f /root/locustfile.py --host http://localhost:8001 \
    --users 300 --spawn-rate 20 --run-time 5m --headless \
    --csv /tmp/loadtest && cat /tmp/loadtest_stats.csv

# Tear down when done:
docker compose -f /root/docker-compose.simple.yml down -v
docker compose -f /root/docker-compose.staging.yml down -v
```

**Pass criteria (axes A + E):** p99 < 500ms on `GET /videos` + `GET /creators/me`,
error rate < 1%, no `QueuePool limit` or `prepared statement` errors in app logs.

**Step 4 — Record Locust results and update REPORT.md**

Add a Locust results table to `docs/assessment/REPORT.md` (axes A + E) and flip ⚠️ → ✅.
Update `LEFT_OFF.md` to reflect the verdict (CONDITIONAL → YES if Locust passes).

---

## WHAT WORKS NOW (do not re-investigate)

### This session

- **Issue 112** — `/health` connection churn fixed: `_check_postgres` now uses
  `engine.connect()` (SQLAlchemy pool) + `asyncio.timeout(2.0)` instead of fresh
  `psycopg.AsyncConnection`; `_check_redis` uses module-level `_health_redis` singleton
  initialized in lifespan instead of `aioredis.from_url()` per probe. `psycopg` import
  and `_pg_dsn()` removed from `main.py`. 2 regression tests in `tests/test_health.py`.
- **Issue 112 staging infra** — `docker-compose.staging.yml` with PgBouncer (edoburu/pgbouncer:latest,
  transaction mode); `tests/perf/seed_staging.py` upserts creator + 12 videos + confirmed DNA;
  `tests/perf/README.md` updated with 7-step runbook. Staging stack is LIVE on prod VM with
  seeded data in `staging_postgres_data` volume — do NOT `down -v` before running the test.
- **Issue 113** — `ImprovementBrief` model gets `UniqueConstraint("creator_id", name="uq_improvement_briefs_creator_id")`;
  migration `0016_improvement_brief_unique.py`; `routers/improvement.py` adds
  `await session.flush()` + `except IntegrityError` → rollback + re-query on the
  first-insert race path. 3 tests in `tests/test_issue_113.py`.
- **Issue 114** — False positive: `_ASSEMBLYAI_READY = True` is on line 186 (after both
  `aai.settings.*` assignments), not line 180. Code was already correct. No change needed.
- **Google OAuth** — Already "In production" (screenshot confirmed). Any Google user can
  sign in. The "unverified app" warning screen still appears for sensitive YouTube scopes
  until Google formally approves, but that's a UX issue, not a blocker.
- **3 legacy PNGs deleted** — `Screenshot 2026-05-31 131715.png`, `issue 2.png`,
  `pricing screen.png` removed from repo root.
- **Full /assess run** — New REPORT.md written: CONDITIONAL / 1 BLOCKER (improvement UNIQUE —
  now fixed by Issue 113) / 1 SEV1 (ingestion AssemblyAI — false positive, already correct) /
  19 SEV2 / 5 clean modules (dna joins youtube, upload_intel, billing, preference).
- **Tests:** 632 passed / 2 skipped / 125 deselected. Layer 0: ruff 0 / mypy 0 /
  coverage 76.08% / bandit 0/0 / pip-audit 0.
- **PgBouncer staging issue** — The edoburu/pgbouncer:latest image fails SCRAM-SHA-256
  auth against Postgres 16 when `AUTH_TYPE=md5`. The simple compose
  (`/root/docker-compose.simple.yml`) bypasses pgbouncer and connects the app directly to
  postgres_staging. Use that for the Locust run.

### Longer-standing landmarks
- **Design system** — `static/_design-tokens.css` Linear-style palette.
- **Self-hosted runner deploy pipeline** — both `docker-publish.yml` + `deploy.yml` on
  `self-hosted`. Zero GH-hosted minutes on deploys.
- **OBS companion app surface** — bearer-auth `POST /clips/ingest`, API-key management UI.
- **Walkthrough gate** — first-run creators routed to `/static/walkthrough.html`.
- **Insights endpoint** — `GET /creators/me/insights`.
- **Stripe billing** — checkout with idempotency-key.
- **Issue 110** — `/auth/logout` + `/billing/webhook` rate limits, improvement-brief SKIP
  LOCKED debounce, `_ingest_async` orphan-mp4 cleanup, `_logging` workaround removed.

---

## THE ARC THAT LED HERE

1. Wave 9 (Issues 102–108) closed all SEV1s + 19 SEV2s → post-Wave-9 `/assess` run.
2. Issue 110 closed the top-register from that assess (rate limits, debounce race, orphan-mp4).
3. This session filed + built Issue 112 (Locust gate: /health fix + staging infra).
4. Ran `/assess` — surfaced improvement BLOCKER (missing UNIQUE constraint) and
   ingestion SEV1 (false positive). Ran the fix as Issue 113.
5. Staged the Locust run on the prod VM — PgBouncer SCRAM auth blocked auto-run.
   Simple compose is in place; one manual step remaining.
6. Google OAuth status confirmed: already "In production" — external gate closed.
7. This session's work is uncommitted. Push + alembic upgrade + Locust run = done.

---

## KEY COORDINATES & FACTS

| Item | Value |
|---|---|
| Public URL | `https://autoclip.studio` |
| Production VM | `147.182.136.107` |
| Container image | `ghcr.io/reese8272/creatorclip:latest` |
| Repo | `github.com/reese8272/creatorclip` (NOT `Youtube-Video-AI-Editor` — old name 404s) |
| Self-hosted runner | `autoclip-prod-vm` (`self-hosted,linux,x64,prod`) — systemd service `actions.runner.reese8272-creatorclip.autoclip-prod-vm` |
| Last successful deploy | `26732895767` (Issue 110 docs, commit `486201e`) |
| Alembic head (production) | `0015_creator_api_keys` — migration `0016` is local only, not yet applied |
| Alembic head (local) | `0016_improvement_brief_unique` (uncommitted, not yet in image) |
| Default model | Sonnet 4.6 (1M context) |
| `/assess` REPORT | `docs/assessment/REPORT.md` + `history/2026-06-01-post-issue-112-REPORT.md` |
| Assessment verdict | CONDITIONAL — 0 BLOCKER (Issue 113 fixes it) / 1 SEV1 (false positive) / 19 SEV2 / 5/11 clean |
| Staging stack | Running on prod VM — `docker-compose.simple.yml` at `/root/` is the correct compose |
| Staging seeded creator | UUID `00000000-1111-2222-3333-444444444444` (in `staging_postgres_data` volume) |
| Secret names (NEVER log values) | `STRIPE_SECRET_KEY`, `JWT_SECRET_KEY`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `GOOGLE_OAUTH_CLIENT_SECRET`, `TOKEN_ENCRYPTION_KEY`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `GHCR_TOKEN`, `DEEPGRAM_API_KEY` |
| pip-audit ignores | `pyproject.toml [tool.pip-audit].ignore-vulns` — 6 entries with mandatory reason comments |
| Memory dir | `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` |

---

## CONSTRAINTS & GOTCHAS (next session: read before acting)

- **Large uncommitted working tree.** Everything from this session is local only. Do NOT
  assume the code on the prod VM matches the local code until after Step 1 push + deploy.
- **Pushing to `main` auto-deploys.** Self-hosted runner picks up Docker publish, then
  `workflow_run` triggers Deploy. No staging gate. Each push = a production cut.
- **Migration 0016 is NOT yet applied to production.** `improvement_briefs` currently has
  no UNIQUE constraint in production. The `IntegrityError` handler in
  `routers/improvement.py` is already in the local code but not yet deployed. After push,
  run `docker exec autoclip-app-1 alembic upgrade head` on the prod VM.
- **Staging stack is still alive.** `root-app-1`, `root-pgbouncer-1`, `root-postgres_staging-1`,
  `root-redis_staging-1`, `root-worker-1` are all running on the prod VM. Tear down with
  `docker compose -f /root/docker-compose.simple.yml down -v && docker compose -f /root/docker-compose.staging.yml down -v` after the Locust run.
- **PgBouncer staging issue (known).** The original `docker-compose.staging.yml` fails SCRAM
  auth between PgBouncer and Postgres 16. Use `/root/docker-compose.simple.yml` instead —
  it bypasses PgBouncer and connects directly to `postgres_staging:5432`.
- **`publication testing.png` is untracked in the repo root.** Do NOT commit it. The user
  shared it as a chat screenshot; it is not a project asset.
- **Issue 114 was a false positive.** The ingestion AssemblyAI SEV1 (`_ASSEMBLYAI_READY = True`
  setting before init checks) was a subagent line-number misread. Line 186 is correct.
  Do NOT try to fix `ingestion/transcribe.py:179-186` — the code is already right.
- **`tests/_helpers.py::override_current_creator`** must be used instead of
  `lambda: creator` in ALL test dependency overrides for `get_current_creator`.
- **`BriefQueuedOut` stays standalone** — `task_id: str | None` is LSP-incompatible with
  `TaskQueuedOut`'s `str`. Do not subclass it.
- **CI / Quality / Integration on hosted runners fast-fail.** Intentional — informational
  only; don't gate deploys.
- **`LOCAL_MEDIA_DIR` validator** relaxed (Issue 110 hotfix): only fails fast in
  production when `STORAGE_BACKEND=local`. Do NOT revert.
- **OAuth tokens are Fernet-encrypted at rest.** Read via `decrypt()`; never log.
- **Per-creator isolation on every query.** Missing `WHERE creator_id = ...` is a BLOCKER.
- **`YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS=30`** is a ToS upper bound. Do NOT increase.

---

## POINTERS

- `docs/SOT.md` — current stack, file structure, schema
- `docs/PROJECT_STATE.md` — every issue's status + session log (Issue 112 + 113 at top)
- `docs/issues.md` — issue backlog (Issue 112 + 113 filed; 109/111 are natural next items)
- `docs/DECISIONS.md` — deviation log
- `docs/COMPLIANCE.md` — YouTube ToS, retention, privacy posture
- `docs/CLIPPING_PRINCIPLES.md` — named principles registry
- `docs/OFF_COURSE_BUGS.md` — incidental defect log
- `docs/assessment/REPORT.md` — current `/assess` verdict (post-issue-112-113, CURRENT)
- `docs/assessment/history/2026-06-01-post-issue-112-REPORT.md` — immutable snapshot
- `tests/_helpers.py` — `override_current_creator(creator)` — use this in all test dep overrides
- `routers/_schemas.py` — `TaskQueuedOut` base for 3 of 4 `*QueuedOut` schemas
- `CLAUDE.md` — project rules; the One Rule is non-negotiable
- Memory: `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/MEMORY.md`
