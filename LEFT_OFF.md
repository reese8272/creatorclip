# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a
> source of truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-06-08 (post-cache-bust deploy; Issue-136 soft-aesthetic redirect awaiting one manual CDN purge)
**Branch:** `main` — HEAD `5e72bea` (synced with origin/main; 0 ahead / 0 behind)
**Working tree:** CLEAN
**CI (most recent):** Quality Gates ✅ · CI ✅ · Docker publish ✅ · Deploy ✅ for `5e72bea`. **Integration tests ❌** — pre-existing flake `tests/test_worker_pipeline.py::test_poll_clip_outcomes_uses_per_creator_median` (`assert None is False` / "Event loop is closed"); not caused by recent work and does not block deploy.

---

## CURRENT FOCUS

**One manual step is needed to surface the Issue-136 soft aesthetic on prod, and one pre-existing test flake is open.** Everything else is shipped + verified.

### → NEXT ACTION

1. **One-time Cloudflare CDN purge** (you, manually — the connected Cloudflare MCP can't see the autoclip.studio zone, so I can't do this from here):
   - https://dash.cloudflare.com → `autoclip.studio` zone → **Caching → Configuration → Purge Everything** (or "Custom Purge" the five URLs below).
   - Surgical purge URLs:
     ```
     https://autoclip.studio/
     https://autoclip.studio/static/hero.css
     https://autoclip.studio/static/editor-layout.css
     https://autoclip.studio/static/_design-tokens.css
     https://autoclip.studio/static/index.html
     ```
   - Propagates in ~10 s. Hard-refresh (Cmd/Ctrl+Shift+R) to bust your local browser cache.
   - After this you'll see the gradient-text H1, aurora glow, pill-shaped URL form, and CSS-only preview card on the hero.
   - **You should never need to do this again** — see WHAT WORKS NOW item #1.
2. **Verify the cache-bust is wired post-deploy:**
   ```bash
   curl -sS https://autoclip.studio/ | grep -o '/static/hero.css?v=[^"]*' | head -1
   ```
   Expected output is `/static/hero.css?v=sha-<git-sha>` (where `<git-sha>` is `5e72bea` or newer). If it's still bare `/static/hero.css`, the deploy didn't pick up the `STATIC_VERSION` build-arg — check `gh run view <docker-publish-run-id>` for the build-arg line.
3. **Optional follow-up — fix the integration flake.** `tests/test_worker_pipeline.py::test_poll_clip_outcomes_uses_per_creator_median` sporadically fails on the integration lane with `assert None is False` + `RuntimeError: Event loop is closed`. Not blocking deploy. Worth a focused fix when there's quiet time.
4. **Optional polish — record a real demo MP4 for the hero.** The current `.hero-demo` is a CSS-only stylized card (mock browser chrome + two scored clip thumbnails). A 30 s autoplaying muted loop of the real product would replace it; the markup hook is in place — just swap the inner content in `static/index.html` for a `<video>` element and ship the MP4 to `/static/demo-hero.mp4`.
5. **Or: pick up a Phase 3 backlog item.** Thumbnail rendering (DALL-E / SD), vision signals (MediaPipe), auto-publish to Shorts, multi-platform export, OBS hot-key clipping. All listed under "Phase 3 Backlog (post-production)" in `docs/issues.md`.

---

## WHAT WORKS NOW (do not re-investigate)

### 1. Static-asset cache-busting (`5e72bea`) — permanent fix

Cloudflare's 4-hour `max-age=14400` on `/static/*.css` was making CSS-only UI deploys invisible for hours after the container rolled. Now:

- **`config.STATIC_VERSION`** (default `"dev"`) — set at image-build time via `Dockerfile ARG GIT_SHA=dev → ENV STATIC_VERSION=$GIT_SHA`.
- **`StaticCacheBustMiddleware`** in `main.py` rewrites every `text/html` response, appending `?v=$STATIC_VERSION` to every `/static/*.css` and `/static/*.js` `href`/`src`. Existing `?v=…` references are preserved so a future pipeline can opt out per-asset.
- **`.github/workflows/docker-publish.yml`** passes `--build-arg GIT_SHA=sha-${{ github.sha }}` to docker build.
- Every push → new SHA → new CSS URL → Cloudflare treats it as a brand-new asset and serves fresh from origin. The 4-hour TTL still applies but only to the URL that won't be requested again.
- Tests pinned in `tests/test_static.py::test_static_cachebust_middleware_*` (3 new tests).

### 2. Shipped this session

| Commit | What |
|---|---|
| `3b15c0b` | Issue 133 — animated caption styles (Bold Pop / Gradient Slide / Minimal) via pysubs2 + libass |
| `f133983` | Issue 134 — filler + silence clean pass with side-by-side `cleaned_render_uri` confirm-swap |
| `7af18b2` | Issue 135 — Descript-style text-based transcript editor (word-spans + localStorage cut queue) |
| `030f987` | Post-Issue-135 `/assess` audit fixes — 6 SEV1s + the cross-cutting routers `task.delay()` axis-B sweep |
| `f5aea4f` | Issue 136 — dark editor 3-pane Grid + pre-auth marketing hero (`data-allow-anonymous` gate) |
| `3b51610` | Issue 136 aesthetic redirect — soft radii ladder, aurora gradients, glassmorphism drawer + glow form |
| `5badf16` | Docs: LEFT_OFF refresh (your commit) |
| `5e72bea` | `?v=<sha>` cache-busting middleware + Dockerfile build-arg + docker-publish workflow build-args |

### 3. Production state (verified)

- Public URL `https://autoclip.studio/` returns 200. Hero markup ships correctly to logged-out visitors (`<body data-allow-anonymous>`, `.hero-demo-clip` × 2 in HTML).
- `alembic_version` on prod is at head `0021_clip_cleaned_render_uri`. Deploy workflow auto-runs `alembic upgrade head` before container rollout.
- `Clip.cleaned_render_uri` column exists; `/clean` and `/cuts` both 409 on collision (audit fix A1).
- The Issue-136 hero CSS is **served** correctly — just **cached** by Cloudflare with the old contents from the `f5aea4f` deploy. The one-time purge above fixes it; the cache-bust prevents recurrence.

### 4. `/assess` verdict

Last run snapshot: `docs/assessment/REPORT.md` (and `docs/assessment/history/2026-06-07-post-issue-135-REPORT.md`). Verdict was CONDITIONAL → effectively YES after the audit-fix sweep landed (`030f987`). All 6 SEV1s closed; the axis-B `task.delay()` cross-cutting fix is in. The one remaining gate is the deferred Locust 300-user load test from Issue 112 to close scale-checklist axes A + E with evidence.

### 5. Test count + Layer 0

- **899 passed / 2 skipped** (default lane).
- Integration lane has the pre-existing flake noted above.
- Layer 0 gates: ruff 0 · mypy 0 · coverage ≥ 75.20 % · bandit 0/0 · pip-audit 0 · freshness ok.

---

## THE ARC THAT LED HERE

1. Started by picking up Issue 132 → API blocker → deferred.
2. Built Issues 133, 134, 135 back-to-back; all shipped.
3. Ran `/assess` → caught 6 SEV1s + a cross-cutting axis-B violation; closed all of them in `030f987`.
4. Built Issue 136 (dark editor + marketing hero) → user feedback "doesn't look different" → applied aesthetic redirect (softer / rounded / aurora / glass) in `3b51610`.
5. User reported changes still not showing → diagnosed: Cloudflare 4-hour CDN cache on `/static/*.css`. Tried to purge via MCP — Cloudflare MCP has 0 zones visible on this account, can't help.
6. Shipped permanent fix in `5e72bea`: `?v=<sha>` cache-busting middleware so future pushes auto-invalidate the CDN. One manual purge still needed for the *current* stale cache.

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
| Local HEAD | `5e72bea` (synced with origin/main) |
| Alembic head | `0021_clip_cleaned_render_uri` (auto-applied by deploy workflow) |
| Issues 127–131, 133–136 | ✅ Shipped |
| Issue 132 | ⛔ Deferred — YouTube API has no chat-replay endpoint (`docs/DECISIONS.md`) |
| Test count | 899 passed / 2 skipped (default lane) |
| Ruff version (local + CI pinned) | `0.15.15` |
| Default LLM model (analysis features) | `claude-haiku-4-5-20251001` |
| Cloudflare MCP account scope | `Reesepludwick@gmail.com`'s account; **0 zones visible** — `autoclip.studio` is on a different CF account |
| New cache-bust setting | `STATIC_VERSION` (config.py + .env.example) — read by `StaticCacheBustMiddleware` in `main.py` |
| Secret names (never log values) | `STRIPE_SECRET_KEY`, `JWT_SECRET_KEY`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `GOOGLE_OAUTH_CLIENT_SECRET`, `TOKEN_ENCRYPTION_KEY`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `GHCR_TOKEN`, `DEEPGRAM_API_KEY` |

---

## CONSTRAINTS & GOTCHAS

- **`git push` auto-deploys to production** via self-hosted runner. The deploy workflow ALSO auto-runs `alembic upgrade head` before container rollout — no manual migration step.
- **Verify origin/main is current before any work.** `git rev-list --left-right --count origin/main...HEAD` should be `0 0`.
- **Cloudflare cache is still hot on five URLs** until the manual purge runs (see NEXT ACTION #1). After that one purge, the cache-bust middleware handles every future deploy automatically.
- **The Cloudflare MCP cannot purge `autoclip.studio`** — the connected account (`Reesepludwick@gmail.com`'s) has 0 zones visible. Either reconnect the MCP to the correct account or do purges via the dashboard.
- **Banked aesthetic preference**: user wants softer / rounded / "futuristic" on marketing + editor surfaces; sharp Linear-utility is retained for data-dense pages (dashboard tables, insights, profile). Two radius ladders coexist in `static/_design-tokens.css` — don't tear one out for the other. See `docs/DECISIONS.md` "2026-06-07 — Issue 136 redirect."
- **`/clean` + `/cuts` share `Clip.cleaned_render_uri`** as the destination slot. Both endpoints 409 when it's already set; the UI is responsible for prompting confirm-or-discard before triggering the other.
- **Pre-existing integration flake**: `tests/test_worker_pipeline.py::test_poll_clip_outcomes_uses_per_creator_median` — sporadically fails (`assert None is False` + "Event loop is closed"). Not blocking deploy.
- **YouTube chat-replay is permanently blocked** (Issue 132). Don't reopen unless Google publishes an official replay endpoint.
- **`/claude-api` skill is mandatory** before writing any Anthropic SDK code (CLAUDE.md One Rule).
- **CI ruff is pinned to 0.15.15** in `.github/workflows/ci.yml` — bump in lockstep with `.venv` when ready.
- **psycopg3 + Alembic + `CREATE INDEX CONCURRENTLY`** does not work. Use plain `op.create_index()`.
- **Rate-limit test pollution (local only)**: if `test_improvement_post_handles_concurrent_insert_race` fails with 429, run `redis-cli del "LIMITS:LIMITER/testclient//creators/me/improvement-brief/10/1/hour"`.

---

## POINTERS

- `docs/SOT.md` — current stack + file structure
- `docs/PROJECT_STATE.md` — every issue's status + session log
- `docs/issues.md` — backlog (Creator-Studio queue 127–136 closed; Phase 3 Backlog items deferred until post-production)
- `docs/DECISIONS.md` — deviation log. Recent: Issue 136 redirect (aesthetic), Issue 136 D1–D7 (original), Audit fixes A1–A6, Issue 135 D1–D6, Issues 132/133/134
- `docs/assessment/REPORT.md` — latest `/assess` verdict + ranked register (snapshot in `docs/assessment/history/`)
- `docs/COMPLIANCE.md` — YouTube ToS, data retention, privacy posture
- `CLAUDE.md` — project rules; the One Rule (research-then-build) is non-negotiable
- AutoMem index: `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/MEMORY.md`
