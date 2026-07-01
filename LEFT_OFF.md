# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-07-01  
**Branch:** main (post-merge; all feature work committed + merged to main + staging)  
**Working tree:** clean after merge  

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source of truth.

---

## CURRENT FOCUS

**All 7 W0 SEV1 beta-gate issues (345–351) are DONE and merged to main.**  
The paid beta is code-unblocked. One deploy step remains.

### → NEXT ACTIONS

1. **Run the migration on prod:** `alembic upgrade head` on the prod VM — applies `0040_rls_chat_child_tables` (child-table RLS for `video_metrics`, `retention_curves`, `transcripts`, `clip_outcomes`, `chat_messages`). Issue 348 is inactive at the DB layer until this runs.
2. **Set `RESEND_TIMEOUT_S=10`** in the prod `.env` / Render env vars (new config var from Issue 349).
3. **Smoke-test prod** — run the Issue 341 harness or a manual clip-create + improvement-brief flow to confirm everything is healthy after the migration.
4. **Start W1:** pick up **Issue 352** (the SEV2 tracker) — read `docs/issues.md` Issue 352 for the prioritised lead items (top: `activity.py` log-injection SEV2 and `config.py ENV: Literal[...]`).

---

## WHAT WORKS NOW

**Core loop verified end-to-end in prod:** upload → ingest → render → playback + R2 presigned URLs.

**W0 SEV1s 345–351 all done (committed, merged to main + staging):**

| Issue | What was fixed | Key file(s) |
|-------|---------------|-------------|
| 345 | Stripe v8 `max_network_retries=3` in `StripeClient()` constructor (was a module-global no-op) | `billing/stripe_client.py` |
| 346 | React root error boundary (`<RootError />` on root route); `createRoot` `onUncaughtError`/`onRecoverableError` | `frontend/src/App.tsx`, `main.tsx` |
| 347 | `event_log.py` pool pinned to `pool_size=2, max_overflow=3`; fleet budget updated | `event_log.py`, `docs/DEPLOYMENT.md` |
| 348 | Chat worker uses `AsyncSessionLocal` + GUC; migration `0040` adds subquery RLS to 5 child tables | `worker/tasks.py:4364`, `alembic/versions/0040_rls_chat_child_tables.py` |
| 349 | `_send_notification_async` commits DB before mailer; `asyncio.to_thread` + `wait_for(timeout=RESEND_TIMEOUT_S)` | `worker/tasks.py`, `config.py`, `.env.example` |
| 350 | `improvement/brief.py` `pause_turn` loop (up to 5 rounds); streaming uses `stream_message`; `max_uses: 5` on `web_search` | `improvement/brief.py` |
| 351 | ruff 21→0, mypy 2→0; `test_stripe_max_retries` updated for v8 attribute path | `tests/`, `routers/thumbnails.py`, `routers/insights.py` |

**Layer-0 gates:** ruff 0 · mypy 0 · coverage ~79% · bandit clean.

---

## THE ARC THAT LED HERE

1. Issue 343 activated RLS role split (app role = no BYPASSRLS; `AdminSessionLocal` = superuser for cross-tenant sweeps).
2. Ingest/render loop fixed (migration 0039 — retain source video; split audio to `audio_uri`).
3. 2026-07-01 automated assessment surfaced 7 W0 SEV1s and ~54 W1 SEV2s.
4. All 7 W0 SEV1s fixed in one session; committed and merged to main + staging.

---

## KEY COORDINATES & FACTS

| Item | Value / location |
|------|-----------------|
| Prod VM | `creatorclip-vm` — Cloudflare tunnel; SSH key in 1Password |
| Migration to run | `alembic upgrade head` → `0040_rls_chat_child_tables` |
| New prod env var | `RESEND_TIMEOUT_S=10` — must be added to prod env |
| Main CI trigger | Push to `main` → GitHub Actions → Docker publish → Deploy to production (auto) |
| Assessment report | `docs/assessment/REPORT.md`, history: `docs/assessment/history/2026-07-01-REPORT.md` |
| Next issue | Issue 352 (SEV2 tracker, ~54 items) — see `docs/issues.md` |
| Stripe webhook secret | `STRIPE_WEBHOOK_SECRET` env var |
| Resend API key | `RESEND_API_KEY` env var |

---

## CONSTRAINTS & GOTCHAS

- **Push to `main` triggers a prod deploy** — CI is wired: push → Docker publish → Deploy workflow runs automatically.
- **Migration `0040` NOT yet applied on prod** — child-table RLS (Issue 348) is code-live but the DB policies don't exist yet. `alembic upgrade head` on the VM is step 1.
- **`AsyncSessionLocal` vs `AdminSessionLocal`** — app-role (no BYPASSRLS) vs superuser (BYPASSRLS). Don't swap them. Any new per-creator query must use `AsyncSessionLocal` with `session.info["creator_id"]` set; cross-tenant worker sweeps use `AdminSessionLocal`.
- **`pause_turn` loop in `improvement/brief.py`** — do not remove `max_uses: 5` from the web_search tool definition; without it the loop can run to the server's internal limit.
- **`test_notifications.py` jinja2 dependency** — three pre-existing tests (`TestSendNotificationLifecycleOptOut`, `TestSendNotificationTransactionalAlwaysOn`, `TestSendNotificationDedupeShortCircuit`) fail locally without jinja2. They pass in Docker (jinja2 is in `requirements.txt`). The two NEW tests in `TestSendNotificationCommitBeforeMailer` use `sys.modules` stubbing and pass without jinja2.
- **`docs/assessment/REPORT.md` is the 2026-07-01 snapshot** — use `docs/issues.md` for the authoritative live issue queue, not the report.

---

## POINTERS

| Doc | Purpose |
|-----|---------|
| `docs/SOT.md` | Architecture, schema, file structure |
| `docs/issues.md` | Live issue queue — Issue 352 is next |
| `docs/PROJECT_STATE.md` | Session log, what's done |
| `docs/DECISIONS.md` | All architectural deviations |
| `docs/COMPLIANCE.md` | YouTube ToS + data retention |
| `docs/CLIPPING_PRINCIPLES.md` | Named principles the engine cites |
| `docs/OFF_COURSE_BUGS.md` | Off-course defect triage queue |
| `docs/DEPLOYMENT.md` | Connection budget, Helm chart, deploy runbook |
| Memory dir | `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` |
