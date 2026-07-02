# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-07-01  
**Branch:** main @ `10d8b74` — synced with origin/main (0 ahead, 0 behind)  
**Working tree:** `LEFT_OFF.md` modified (uncommitted); untracked debug artifacts (HAR/PNG — ignore)  
**CI/Deploy:** all green — Docker publish + Deploy to production both `success` for `10d8b74`

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source of truth.

---

## CURRENT FOCUS

**W0 SEV1 beta-gate is fully closed.** All 7 Issues (345–351) are done, merged to main + staging, deployed to prod, migration applied, env var set. The paid beta is unblocked.

### → NEXT ACTION

1. **Start W1 — pick up Issue 352** (SEV2 tracker, ~54 items): run `/issue-workflow 352` and read `docs/issues.md` for the prioritised lead items.
   - Top W1 SEV2 candidates: `activity.py` log-injection, `config.py ENV: Literal[...]` hardening, rate-limit bypass on unauthenticated endpoints.
2. Optionally smoke-test prod first: run `scripts/live_smoke.py` against the live tunnel to confirm the render/clip loop is healthy post-deploy.

---

## WHAT WORKS NOW

**Core loop:** upload → ingest → transcribe → clip → render → playback via R2 presigned URLs — verified end-to-end in prod.

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

**Prod deploy state (as of 2026-07-01 ~21:45 UTC):**
- Image `ghcr.io/reese8272/creatorclip:latest` @ `10d8b74` running on `autoclip-vm`
- Alembic at `0040 (head)` — child-table RLS policies active in DB
- `RESEND_TIMEOUT_S=10` appended to `/opt/autoclip/.env`
- Layer-0 gates: ruff 0 · mypy 0 · coverage ~79% · bandit clean

---

## THE ARC THAT LED HERE

1. Issue 343 activated the RLS role split (app role = no BYPASSRLS; `AdminSessionLocal` = superuser).
2. Ingest/render loop fixed via migration `0039` — source video retained; audio split to `audio_uri`.
3. 2026-07-01 automated assessment surfaced 7 W0 SEV1 beta-gate issues + ~54 W1 SEV2s.
4. All 7 W0 SEV1s built, tested, committed, merged to main + staging, deployed, migration applied.
5. `LEFT_OFF.md` updated; session closed cleanly.

---

## KEY COORDINATES & FACTS

| Item | Value / location |
|------|-----------------|
| Prod VM | `creatorclip-vm` — `147.182.136.107`; SSH via `~/.ssh/id_ed25519`; credentials in 1Password |
| Prod compose dir | `/opt/autoclip/` — `docker-compose.prod.yml` + `.env` |
| Prod .env | `/opt/autoclip/.env` — edited directly on VM; not committed to git |
| Current alembic head | `0040` (`0040_rls_chat_child_tables.py`) |
| CI trigger | Push to `main` → Docker publish → Deploy to production (auto, ~2 min end-to-end) |
| Assessment report | `docs/assessment/REPORT.md`; history snapshot: `docs/assessment/history/2026-07-01-REPORT.md` |
| Next issue | Issue 352 (W1 SEV2 tracker) — `docs/issues.md` |
| Stripe webhook secret | `STRIPE_WEBHOOK_SECRET` env var |
| Resend API key | `RESEND_API_KEY` env var (not yet set on prod — email send will no-op until configured) |

---

## CONSTRAINTS & GOTCHAS

- **Push to `main` triggers a prod deploy automatically** — CI: push → Docker publish → Deploy workflow. No manual step needed; be intentional about merging.
- **`AsyncSessionLocal` vs `AdminSessionLocal`** — app role (RLS enforced) vs superuser (BYPASSRLS). Any new per-creator query must use `AsyncSessionLocal` with `session.info["creator_id"]` set. Cross-tenant worker sweeps use `AdminSessionLocal`. Swapping them silently bypasses or breaks RLS.
- **`pause_turn` loop in `improvement/brief.py`** — `max_uses: 5` on the `web_search` tool definition bounds the loop. Do not remove it; without it the server's internal limit applies and the loop may not terminate cleanly.
- **`test_notifications.py` jinja2 dependency** — three tests (`TestSendNotificationLifecycleOptOut`, `TestSendNotificationTransactionalAlwaysOn`, `TestSendNotificationDedupeShortCircuit`) fail locally without jinja2. They pass in Docker (jinja2 is in `requirements.txt`). The `TestSendNotificationCommitBeforeMailer` tests use `sys.modules` stubbing and pass without jinja2.
- **`RESEND_API_KEY` is not yet set on prod** — email delivery will no-op silently. Set it in `/opt/autoclip/.env` when Resend is ready to go live.
- **`docs/assessment/REPORT.md` is a 2026-07-01 snapshot** — use `docs/issues.md` for the live authoritative issue queue.
- **Untracked files at repo root** (`autoclip.studio.har`, `error.png`, `render loop.png`, `rendered-clip.png`) — debug artifacts from an earlier session, not committed. Safe to delete locally if no longer needed.

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
| `docs/OFF_COURSE_BUGS.md` | Off-course defect triage queue |
| `docs/DEPLOYMENT.md` | Connection budget, deploy runbook, Helm chart |
| Memory dir | `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` |
