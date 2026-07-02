# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-07-02 (end of the W1 wave execution session)
**Branch at close:** `w1/round3` — PR #45 open to main (carries migration `0044_rls_signals`)
**Prod:** three green deploys today (PRs #42/#43/#44); prod at W1-round-2b content, DB head `0043`
**CI/Deploy:** merging PR #45 auto-deploys + applies 0044

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source of truth.

---

## CURRENT FOCUS

**The W1 wave is code-complete.** Everything buildable in W1 (plus the open W0 stragglers) shipped
today across PRs #41–#45: all 13 batches of the Issue-352 SEV2 backlog, Stream-VOD recap Parts A+B
(190/191), feature flags/kill switches (284), data-gate UX (203), session-order + async-SDK refactor
(82a+82b), worker RLS sweep (231), eval residuals (200/202), 148 close-out, 288/286 config.

### → NEXT ACTIONS

1. **Merge PR #45 once CI is green** (same known-red Playwright/visual pair) → deploy applies 0044.
2. **Operator checklist (user, all documented):**
   - Rotate the Anthropic API key that surfaced in-session; update VM + local `.env`
   - Apply the Cloudflare `/auth/*` rate-limit rule + edge verify → `docs/EDGE_SECURITY.md`
   - Install the Redis backup cron (03:27) + run the restart drill → `docs/RUNBOOKS.md`
   - Better Stack status page (#282), #228 live 429 smoke (`scripts/live_smoke.py`)
3. **Promote from OFF_COURSE_BUGS into issues.md:** styled re-render no-op (SEV2); NULLIF GUC
   policy hardening migration (SEV2); LLM E2E Nightly red; Playwright runner gap.
4. **Next wave:** W2 issues (see the Lane×Wave matrix) — 192 (recap UI), 290 (spend caps — now
   unblocked by 284), 245, plus the W1 staging-verify residuals (200 sweep on real data, 198/201).

---

## KEY FACTS / GOTCHAS (delta from previous handoff)

- **Dev box reality UPDATED:** ffmpeg 8.1.2 IS installed; a native PG16+pgvector cluster with both
  roles runs locally (integration lane works: `-m integration`); pytest-randomly is now installed
  locally (was CI-only). The old "no ffmpeg/no Postgres" notes are stale.
- **Unit lane is now hermetic:** conftest pins `STORAGE_BACKEND=local` — before today, `/health`
  unit tests probed the LIVE R2 bucket (developer `.env` leak). Full suite now ~24s (was 60–113s).
- **Migrations:** head `0044`. The 0041 incident: `create_type=False` on generic `sa.Enum` is
  dialect-dependent — always use `postgresql.ENUM` in migrations. Deploy runs migrations with the
  VM's own Python stack (unpinned — version skew vs CI; follow-up: run alembic inside the container).
- **Squawk migration gate is now real** (was structurally unable to fail); `.squawk.toml` excludes
  2 style rules; env.py offline mode emits SET timeouts.
- **Worker sessions:** per-creator tasks use `db.tenant_session(creator_id)` (RLS-enforced);
  `AdminSessionLocal` is an AST-test-pinned 18-site allowlist — adding a site outside it fails
  `tests/test_worker_invariants.py` by design.
- **All LLM calls are AsyncAnthropic now** — structural guards forbid sync clients / to_thread on
  LLM paths (`tests/test_llm_conformance.py`). Voyage stays thread-wrapped (DECISIONS).
- **Flags:** 4 kill switches (llm_generation, render_intake, youtube_publish, signup) — flip via
  `scripts/flags.py`; TTL cache ~30s; tests get env defaults primed per-test in conftest.
- **The shell sometimes lands inside a completed agent's worktree** — `cd` to the repo root before
  merge/branch operations (bit us twice today).

---

## POINTERS

| Doc | Purpose |
|-----|---------|
| `docs/PROJECT_STATE.md` | Today's four session entries (round 1, rounds 2–3, hotfix) |
| `docs/issues.md` | Tracker — W1 statuses flipped with evidence; W2 is next |
| `docs/DECISIONS.md` | Three 2026-07-02 entries (W1 scope + research calls; Squawk gate; rounds 2–3) |
| `docs/OFF_COURSE_BUGS.md` | 7 new entries today — 2 promotable SEV2s |
| `docs/EDGE_SECURITY.md` | NEW — committed Cloudflare edge config + apply/verify steps |
| `docs/RUNBOOKS.md` | + Redis broker durability & recovery section |
| Memory dir | `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` |
