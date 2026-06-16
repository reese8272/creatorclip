# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a
> source of truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-06-16 (Issues 139–142 — shipped + verified live; PR open; one CI gate red)
**Branch:** `issue-139-142-sweep` — HEAD `ac231d4`, **5 ahead / 0 behind** `origin/main`
**Working tree:** clean except this `LEFT_OFF.md` (ready to commit)
**PR:** [#20](https://github.com/reese8272/creatorclip/pull/20) → base `main`, OPEN
**CI on the PR:** `CI` ✅ · `Coverage floor` ✅ · **`Quality Gates` ❌ — `pip_audit` (8 advisories) only.** Advisory-DB drift, **not** from this branch (it changed zero deps; would fail `main` too). See NEXT ACTION #1.

---

## CURRENT FOCUS

**The 4 code items from the "what's genuinely left to deploy" audit are DONE, pushed, and the harness + load test were run live against staging. PR #20 is open. The only thing between here and a green, mergeable PR is the `pip_audit` gate (pre-existing advisory drift). After that, merging is a deploy decision for the user.**

### → NEXT ACTION

1. **Clear the `pip_audit` gate** (the only red gate). Enumerate live:
   ```bash
   PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pip_audit
   ```
   These are advisories disclosed since the Issue-107 ignore-list pass, outside the
   `[tool.pip-audit] ignore-vulns` list in `pyproject.toml`, **none in the `anthropic` tree**.
   Known offenders (re-confirm versions): `python-multipart`, `cryptography`, `pytest` (dev),
   `starlette` (→1.x is a **major, FastAPI-coupled** bump — do NOT casually bundle it here).
   **Recommended (scope call — confirm with user):** safe patch-bump `python-multipart` +
   `cryptography` (re-test Fernet decrypt round-trip in `crypto.py`), add the `pytest`/`starlette`
   CVE IDs to the ignore list with justification, defer `starlette` 1.x to its own tracked issue.
2. **Merge PR #20 — user's call. ⚠️ Merging `main` auto-deploys to prod AND runs `alembic upgrade head`, which applies migration `0024_video_origin_enum` on the prod DB.** Only merge when intended.
3. **External / not-code (no longer blocked by anything in this repo):** Google OAuth app
   verification (the real public-launch long-pole), Issue 27 quota-increase request, v1.0.0 tag.

---

## WHAT WORKS NOW (don't re-investigate)

- **Issues 139/140/141/142 complete + verified.** 974 non-integration tests pass (+7); ruff 0 / mypy 0 on touched files. Migration 0024 applied cleanly on **real Postgres** (staging) — `alembic current` → `0024 (head)`.
- **The LLM harness ran 10/10 PASS live against staging** (`scripts/llm_harness.py`), including the Issue-139 regression: `linked_video_visible_non_clippable — origin=link clippable=False` and `queue_source_less_409 — 409`.
- **Locust load test executed — axes A + E CLOSED** (`docs/assessment/REPORT.md`). 300u/180s across 13 creators, ~138 req/s: **zero 500s / timeouts / pool-exhaustion**, p99 680ms, `/health` 0% fail. This was the standing `/assess` CONDITIONAL gate.
- **`TOKEN_ENCRYPTION_KEY` rotation runbook written** (`docs/DEPLOYMENT.md`) — the other standing pre-launch item.
- **Prod runtime hardening verified** (via SSH): `ENV=production`, `ALLOWED_ORIGINS=https://autoclip.studio`, `OAUTH_REDIRECT_URI=…/auth/callback`, `/docs` → 404, `/health` ok. Confirms `autoclip.studio` is the live domain (validated Issue 141).
- **Staging is LIVE and hookable** — `ssh creatorclip-vm`, compose project `cc139` on `:8001` (isolated from prod). Re-run the harness anytime per `docs/STAGING_ACCESS.md`.
- **`yt-dlp` was researched and deliberately REJECTED** (violates YouTube API Services ToS even for own content; risks OAuth verification). The compliant Option A is shipped — **do not revisit wiring yt-dlp.**

## THE ARC THAT LED HERE

1. User asked "be real, what's left to be deployable?" → audit found the app feature-complete + already live on `autoclip.studio`, with 4 code loose ends + ops items.
2. Planned 4 issues (139 linked-video SEV1, 140 cache marker, 141 domain, 142 LLM harness). User chose yt-dlp for 139 → research showed ToS violation → user pivoted to **compliant Option A**.
3. Shipped all 4; pushed `issue-139-142-sweep`; opened PR #20.
4. User: "open the PR and do the load test and anything else." → Repaired staging (PgBouncer md5→scram bug), redeployed from branch, ran harness 10/10, ran Locust (axes A+E closed), wrote rotation runbook, verified prod hardening.
5. Close-out: only the `pip_audit` gate is red (advisory drift, not this branch).

## KEY COORDINATES & FACTS

| Item | Value |
|---|---|
| Repo | `/home/reese/workspace/Youtube-Video-AI-Editor` |
| Branch / HEAD | `issue-139-142-sweep` / `ac231d4` (5 ahead of `origin/main`) |
| PR | #20 — https://github.com/reese8272/creatorclip/pull/20 |
| New migration | `alembic/versions/0024_video_origin_enum.py` (backfills `origin` from `source_uri`) |
| Open CI blocker | `Quality Gates` → static gates → `pip_audit fail (8)` (advisory drift) |
| Staging | VM `creatorclip-vm` (`root@147.182.136.107`), compose project `cc139`, app `:8001`, image `creatorclip:staging` (NEVER reuse prod's `:latest` tag) |
| Staging seeded creator | `00000000-1111-2222-3333-444444444444` (+12 load-test creators in staging DB only) |
| Harness / runbook | `scripts/llm_harness.py` · `docs/STAGING_ACCESS.md` |
| Prod | `autoclip.studio` via Cloudflare tunnel; containers `autoclip-*`; host `:8000` is NOT prod-mapped (probe prod via the tunnel) |
| Test runner (local) | `.venv/bin/python -m pytest -m "not integration" -q` (needs Redis up; no local Postgres) |
| Deploy trigger | merge to `main` → `Deploy to production` (auto `alembic upgrade head`) |
| Secrets | by NAME only — see `docs/SECRETS.md` (`TOKEN_ENCRYPTION_KEY`, `TOKEN_ENCRYPTION_KEY_PREVIOUS`, `JWT_SECRET_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_OAUTH_CLIENT_ID/SECRET`, `STRIPE_*`, `VOYAGE_API_KEY`) — never write values |

## CONSTRAINTS & GOTCHAS

- **Merging PR #20 auto-deploys to prod AND runs migration 0024 on the prod DB.** Only merge when intended + gates green.
- **`pip_audit` red is advisory drift, not a regression** — this branch changed zero deps (`git diff origin/main -- requirements*.txt pyproject.toml` is empty). It still blocks a clean merge.
- **🔑 ROTATE THE GITHUB PAT** — it was embedded in the VM's git remote URL (`/opt/autoclip/src`) and surfaced in command output this session.
- **Do NOT wire `yt-dlp`** — it's a deliberate compliance rejection (DECISIONS/COMPLIANCE 2026-06-16). Clipping needs an uploaded source file (Google Takeout / original export).
- **Migration 0024 limitation (accepted):** pre-existing linked rows (`source_uri` NULL) backfill to `origin=catalog` and stay hidden — unrecoverable from old data. Forward-looking only.
- **Staging shares the VM with live prod.** Load tests cause CPU contention (load avg hit ~3.4/4); keep runs bounded and verify prod via the **tunnel** (`https://autoclip.studio/health`), not host `:8000`.
- **Local env: no Docker/Postgres.** Use `.venv` + a running Redis (`redis-server --daemonize yes --save '' --appendonly no`). Integration tests + coverage are CI-authoritative.
- **CLAUDE.md One Rule** holds for every non-trivial decision: research current industry standard first; log deviations in `docs/DECISIONS.md`.

## POINTERS (sources of truth — this file is NOT one)

- `docs/PROJECT_STATE.md` — issue progress (Issues 139–142 entries current) · `docs/issues.md` — work queue
- `docs/assessment/REPORT.md` — verdict + the new Locust axes-A+E section · `docs/assessment/history/` — snapshots
- `docs/DECISIONS.md` — design decisions (2026-06-16: Issue 139 yt-dlp ToS rejection, Issue 140 cache marker)
- `docs/OFF_COURSE_BUGS.md` — incidental defects (incl. the PgBouncer scram fix, both linked-video entries resolved)
- `docs/STAGING_ACCESS.md` — the LLM-harness hook-in runbook · `docs/DEPLOYMENT.md` — incl. the new token-rotation runbook
- `docs/SOT.md` · `docs/COMPLIANCE.md` · `docs/CLIPPING_PRINCIPLES.md` · `docs/SECRETS.md` · `CLAUDE.md`
- Memory: `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/MEMORY.md`
