# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a
> source of truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-06-07 (Issue 136 shipped + aesthetic redirect applied; Creator-Studio queue 127–136 closed)
**Branch:** `main` — HEAD `3b51610` (synced with origin/main; 0 ahead / 0 behind)
**Working tree:** CLEAN
**CI (most recent):** Quality Gates / CI / Docker publish / Integration tests **all in progress** for `3b51610` at handoff time. Verify with `gh run list --limit 5`. Deploy for the prior `f5aea4f` succeeded; once `3b51610` finishes Docker publish the auto-Deploy will land it on prod.

---

## CURRENT FOCUS

**No active issue.** The Creator-Studio expansion queue (Issues 127–136) is fully
closed: 127 / 128 / 129 / 130 / 131 / 133 / 134 / 135 / 136 ✅ shipped, 132 ⛔ deferred
(YouTube has no chat-replay API; logged in `docs/DECISIONS.md`). What remains in
`docs/issues.md` is the **Phase 3 Backlog (post-production)** — explicitly deferred
until the product is live and stable.

### → NEXT ACTION (pick one)

1. **Confirm `3b51610` deployed clean.** The softer-aesthetic redirect is in
   flight at handoff time.
   ```bash
   gh run list --limit 5
   curl -sS -o /dev/null -w "%{http_code}\n" https://autoclip.studio/static/hero.css
   ```
   Hard-refresh `https://autoclip.studio/` to see the gradient-text H1 + aurora
   backdrop + pill-shaped URL form. If the visuals still read wrong, iterate on
   `static/hero.css` + `static/editor-layout.css` — the user wants softer / more
   "futuristic" than the Linear-locked Issue-99 direction. **Banked preference
   in `docs/DECISIONS.md` ("2026-06-07 — Issue 136 redirect").**
2. **Record a real demo MP4 for the hero** (optional polish). Currently the
   `.hero-demo` card is CSS-only (mock browser chrome + two scored clip thumbnails)
   because the original `/static/demo-hero.mp4` was 404'ing. A real 30 s muted
   autoplaying loop can swap into the same `.hero-demo` shell — see
   `docs/DECISIONS.md` D6 of the original Issue-136 entry.
3. **Pick up a Phase 3 backlog item** if the user wants to extend the product:
   thumbnail rendering (DALL-E / SD), vision signals (MediaPipe), auto-publish
   to Shorts, multi-platform export, OBS hot-key clipping. All listed in
   `docs/issues.md` under "Phase 3 Backlog (post-production)."
4. **Fix the pre-existing flaky integration test** —
   `tests/test_worker_pipeline.py::test_poll_clip_outcomes_uses_per_creator_median`.
   `assert None is False` + `RuntimeError: Event loop is closed`. Not caused by
   any 2026-06-07 work; fails sporadically on the integration-tests CI lane only.
   Default-lane unit tests are green (896 passed / 2 skipped at handoff time).

---

## WHAT WORKS NOW (do not re-investigate)

### Shipped this session

| Commit | What |
|---|---|
| `3b15c0b` | Issue 133 — animated caption styles (Bold Pop / Gradient Slide / Minimal) via pysubs2 + libass |
| `f133983` | Issue 134 — filler + silence clean pass with side-by-side `cleaned_render_uri` confirm-swap |
| `7af18b2` | Issue 135 — Descript-style text-based transcript editor (word-spans + localStorage cut queue) |
| `030f987` | Post-Issue-135 `/assess` audit fixes — 6 SEV1s + the cross-cutting routers `task.delay()` axis-B sweep |
| `f5aea4f` | Issue 136 — dark editor 3-pane Grid + pre-auth marketing hero (`data-allow-anonymous` gate) |
| `3b51610` | Issue 136 aesthetic redirect — soft radii ladder, aurora gradients, glassmorphism drawer + glow-on-focus form |

### Production state (assumed current; verify before acting)

- **Public URL** `https://autoclip.studio/` returns 200 with the pre-auth hero
  visible to logged-out visitors. All Issue 136 assets (`hero.css`,
  `editor-layout.css`, `_design-tokens.css`, `auth.js`) are served from
  `/static/`.
- **`alembic_version`** on prod is at head `0021_clip_cleaned_render_uri`. The
  deploy workflow auto-runs `alembic upgrade head` before container rollout —
  there is no manual migration step.
- **`Clip.cleaned_render_uri`** column exists; the `/clean` and `/cuts`
  endpoints both 409 when it's already populated (audit fix A1) — the worker no
  longer silently no-ops on collision.

### `/assess` verdict snapshot

Last run: `docs/assessment/REPORT.md` ("2026-06-07 post-Issue-135"). Verdict:
**CONDITIONAL → effectively YES** after the audit-fix sweep landed (`030f987`).
All 6 SEV1s closed; the axis-B `task.delay()` cross-cutting fix is in. The only
remaining gate is the deferred Locust 300-user load test from Issue 112 to
close scale-checklist axes A + E with evidence. Module verdicts + register
detail in `docs/assessment/modules/*.md` and snapshot in
`docs/assessment/history/2026-06-07-post-issue-135-REPORT.md`.

### Test count + Layer 0

- **896 passed / 2 skipped** (default lane). Integration lane has the
  pre-existing flake noted above.
- Layer 0 gates: ruff 0 · mypy 0 · coverage ≥ 75.20 % · bandit 0/0 ·
  pip-audit 0 · freshness ok.

---

## THE ARC THAT LED HERE

1. Competitive intelligence → Issues 127–136 filed ROI-ordered.
2. Issues 127, 128, 129, 123, 130/131 shipped in prior sessions.
3. **This session, in order**: kicked off Issue 132 → API blocker → deferred.
   Built Issues 133, 134, 135 back-to-back. Ran `/assess` → caught 6 SEV1s
   (clean/edit collision, RLS stamps on two worker helpers, inert Haiku cache
   markers, OAuth caller-session commit) + a cross-cutting axis-B violation
   (~16 `task.delay()` calls inside `async def`). Closed all of them.
   Built Issue 136 (dark editor + hero) → user feedback "doesn't look
   different" → applied an aesthetic redirect (softer / rounded /
   futuristic).

---

## KEY COORDINATES & FACTS

| Item | Value |
|---|---|
| Public URL | `https://autoclip.studio` |
| Production VM | `147.182.136.107` (compose at `/opt/autoclip`) |
| Container image | `ghcr.io/reese8272/creatorclip:latest` |
| Repo | `github.com/reese8272/creatorclip` |
| Self-hosted runner | systemd `actions.runner.reese8272-creatorclip.autoclip-prod-vm` on prod VM |
| Current branch | `main` |
| Local HEAD | `3b51610` (synced with origin/main) |
| Alembic head | `0021_clip_cleaned_render_uri` (both local + prod; auto-applied by deploy workflow) |
| Issues 127–131, 133–136 | ✅ Shipped |
| Issue 132 | ⛔ Deferred — YouTube API has no chat-replay endpoint (`docs/DECISIONS.md`) |
| Test count | 896 passed / 2 skipped (default lane) |
| Ruff version (local + CI pinned) | `0.15.15` |
| Default LLM model (analysis features) | `claude-haiku-4-5-20251001` |
| Secret names (never log values) | `STRIPE_SECRET_KEY`, `JWT_SECRET_KEY`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `GOOGLE_OAUTH_CLIENT_SECRET`, `TOKEN_ENCRYPTION_KEY`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `GHCR_TOKEN`, `DEEPGRAM_API_KEY` |

---

## CONSTRAINTS & GOTCHAS

- **`git push` auto-deploys to production** via self-hosted runner. The deploy
  workflow ALSO auto-runs `alembic upgrade head` before container rollout —
  there is no manual migration step. Verify CI before pushing.
- **Verify `gh run list --limit 5` before assuming `3b51610` is live.** At
  handoff time the run is still in progress; the deploy auto-fires when Docker
  publish completes.
- **Hard-refresh after a deploy** — `_design-tokens.css` / `hero.css` /
  `editor-layout.css` are aggressively cached by browsers and Cloudflare. If
  the aesthetic looks unchanged, that's almost certainly cache.
- **Banked aesthetic preference**: user wants softer / rounded / "futuristic" on
  marketing + editor surfaces; sharp Linear-utility is retained for data-dense
  pages (dashboard tables, insights, profile). Two radius ladders coexist in
  `static/_design-tokens.css` — don't tear one out for the other. See
  `docs/DECISIONS.md` "2026-06-07 — Issue 136 redirect."
- **`/clean` + `/cuts` share `Clip.cleaned_render_uri`** as the destination
  slot. Both endpoints 409 when it's already set; the UI is responsible for
  prompting the user to confirm-or-discard before triggering the other.
- **Pre-existing integration flake**:
  `tests/test_worker_pipeline.py::test_poll_clip_outcomes_uses_per_creator_median`
  — sporadically fails with `assert None is False` + `RuntimeError: Event loop
  is closed`. Not blocking deploy. Worth a focused fix in a quiet moment.
- **YouTube chat-replay is permanently blocked** (Issue 132). Don't reopen
  unless Google publishes an official replay endpoint.
- **`/claude-api` skill is mandatory** before writing any Anthropic SDK code
  (CLAUDE.md One Rule).
- **CI ruff is pinned to 0.15.15** in `.github/workflows/ci.yml` — bump in
  lockstep with `.venv` when ready.
- **psycopg3 + Alembic + `CREATE INDEX CONCURRENTLY`** does not work. Use
  plain `op.create_index()`.
- **Rate-limit test pollution (local only)**: if
  `test_improvement_post_handles_concurrent_insert_race` fails with 429, run
  `redis-cli del "LIMITS:LIMITER/testclient//creators/me/improvement-brief/10/1/hour"`.

---

## POINTERS

- `docs/SOT.md` — current stack + file structure (Issue 136 edits + editor-layout.css + hero.css are listed)
- `docs/PROJECT_STATE.md` — every issue's status + session log (Issue 136 + audit-fix entries are the most recent)
- `docs/issues.md` — backlog. Creator-Studio queue 127–136 closed; Phase 3 Backlog items deferred until post-production
- `docs/DECISIONS.md` — deviation log. Recent: Issue 136 redirect (aesthetic), Issue 136 D1–D7 (original), Audit fixes A1–A6, Issue 135 D1–D6, Issues 132/133/134
- `docs/assessment/REPORT.md` — latest `/assess` verdict + ranked register (snapshot in `docs/assessment/history/`)
- `docs/COMPLIANCE.md` — YouTube ToS, data retention, privacy posture
- `CLAUDE.md` — project rules; the One Rule (research-then-build) is non-negotiable
- AutoMem index: `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/MEMORY.md`
