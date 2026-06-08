# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a
> source of truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-06-07 (Issue 133 code-complete; Issue 132 deferred — blocked on YouTube API; commit + push pending user approval)
**Branch:** `main` — HEAD `5f04046` (synced with origin/main) — **uncommitted Issue 133 changes in working tree**
**Working tree:** DIRTY — Issue 133 files staged for next commit (see "Files touched this session")
**CI (most recent green):** Quality Gates ✅ · Integration tests ✅ · CI ✅ · Docker publish ✅ · Deploy ✅ (for `5f04046`)

---

## CURRENT FOCUS

### Issue 133 — Animated caption styles → code complete, awaiting push

Three styles shipped through `clip_engine/captions.py`:
- **Bold Pop** (MrBeast/Hormozi feel — one word, scale-pop, Anton 95pt centered)
- **Gradient Slide** (per-word indigo→white fade-in, accumulating phrase)
- **Minimal** (plain phrase-level Dialogue, no animation)

ASS files generated via `pysubs2==1.7.3`, burned in via ffmpeg's
`subtitles=…:fontsdir=…` (libass). Dockerfile installs Anton from Google Fonts
GitHub raw + `fonts-open-sans` fallback. Style picker in `static/review.html`
shows the three options.

### Issue 132 — DEFERRED (no longer in the active queue)

Phase-1 research surfaced a hard blocker: YouTube Data API has **no chat-replay
endpoint** for completed VOD livestreams (`liveChatMessages.list` works only
while a broadcast is *active*). Third-party libs (`pytchat`, `chat-downloader`)
work by scraping YouTube's internal `youtubei/v1/live_chat` — violates YouTube
API Services ToS §IV.A. Risk to OAuth verification status is not worth the
feature parity. Full DECISIONS entry: `docs/DECISIONS.md` 2026-06-07.

### → NEXT ACTION

1. **Review the dirty working tree** for Issue 133. Files touched:
   - `clip_engine/captions.py` (new — 280 lines)
   - `clip_engine/render.py` (extended for `transcript_segments` + `subtitles=` filter)
   - `worker/tasks.py::_render_clip_async` (fetches `Transcript` when animated style chosen)
   - `routers/clips.py::RenderStyleIn` (doc-comment update — subtitle key list)
   - `static/review.html` (new style picker options)
   - `tests/test_captions.py` (new — 16 tests)
   - `tests/test_render_style.py` (updated 2 existing tests for the new filter shape)
   - `requirements.txt` (`pysubs2==1.7.3`)
   - `Dockerfile` (fontconfig + Anton fetch + `fc-cache`)
   - `docs/DECISIONS.md` (Issue 133 entry + Issue 132 deferral)
   - `docs/SOT.md`, `docs/PROJECT_STATE.md`, `docs/issues.md` (133 ticked, 132 blocked)
2. **Commit + push**. `git push` auto-deploys to prod (self-hosted runner).
   - Quick smoke check before push: `gh run list --limit 5` to confirm previous
     `5f04046` Deploy actually finished green (it should have hours ago).
3. **After deploy: watch first real render with Bold Pop**. The render pipeline
   has never produced an .ass file in production before — most likely failure
   modes are (a) the Dockerfile font fetch failed during image build → libass
   falls back to Open Sans (captions render but in wrong font), (b) fontconfig
   cache wasn't updated → libass falls back to a system default (worse-looking
   captions). Test path: render a clip with `subtitle: "bold_pop"` and inspect
   the output video.
4. **Next active issue: 134 — Filler word and silence removal.**

---

## WHAT WORKS NOW (do not re-investigate)

### Built this session (Issue 133)

- **`clip_engine/captions.py::build_ass_subtitles(segments, style, clip_start_s, clip_duration_s, out_path)`**
  — Generates ASS file. Returns `None` for unknown styles, empty input, or
  out-of-window clips so the caller silently skips the `subtitles=` filter.
- **ASS file shape**: `PlayResX=1080 / PlayResY=1920`, `ScaledBorderAndShadow=yes`,
  Default Style with Anton + `ScaleX/ScaleY=100` baseline (load-bearing for the
  `\fscx120` Bold Pop pop animation).
- **Bold Pop override**: `{\t(0,80,\fscx120\fscy120)\t(80,160,\fscx100\fscy100)}`,
  one Dialogue per word.
- **Gradient Slide override**: `{\fad(150,0)\c&Hd26a5e&\t(0,300,\c&Hffffff&)}`
  applied to the newest word; prior phrase words stay at Style default white.
- **Brand indigo byte order**: ASS `&Hd26a5e&` (NOT HTML `&H5e6ad2&`) —
  regression test in `tests/test_captions.py` asserts both directions.
- **Worker integration**: `_render_clip_async` checks if
  `style_preset.subtitle in {bold_pop, gradient_slide, minimal}` and, only then,
  loads Transcript via `await session.get(Transcript, video.id)` and threads
  `transcript_segments` into `render_clip_file`.
- **Style picker UI**: dropdown options replaced with the three real styles +
  one-line `title=` tooltip descriptions.

### Lessons banked this session (avoid repeating)

1. **`fonts.google.com/download?family=…` is gated through CDN that demands
   browser headers** — fragile in CI. Use
   `raw.githubusercontent.com/google/fonts/main/ofl/<family>/<file>.ttf`
   for direct TTF fetches in Dockerfiles.
2. **ASS color byte order is `&HBBGGRR&`** — reversed from HTML hex. The easy
   mistake (writing the HTML byte order) silently ships a wrong-color caption
   that looks "kind of right" in thumbnails. Always pin both directions in
   a regression test.
3. **`Style.ScaleX/ScaleY` MUST be 100 baseline** for the Bold Pop pop
   animation — if a future style has `scalex=80`, `\t(\fscx120)` multiplies
   from 80, not 100, and the pop lands at the wrong size.
4. **YouTube Data API has NO chat-replay endpoint for completed VODs.**
   Don't research it again — this conclusion is now in `docs/DECISIONS.md`.

### Test count + Layer 0

- **840 passed / 2 skipped** (up from 821 at the start of this session — +19).
- Layer 0 gates (locally with `.venv/bin/python -m ruff`): ruff 0 · mypy 0 ·
  freshness ok. CI runs the full Layer 0 including coverage/bandit/pip-audit.

---

## THE ARC THAT LED HERE

1. Competitive intelligence → Issues 127–136 filed ROI-ordered.
2. Issue 127 (sentence-boundary cuts): deployed at `2ae7ad6`.
3. Issue 128 (title optimizer): deployed at `e3c83b2`.
4. Issue 129 (thumbnail concepts): deployed at `56c6d34`.
5. Issue 123 (SEV1 sweep + CI fixes): deployed at `e454bdb`.
6. Issues 130 + 131 (hook analyzer + chapter markers): deployed at `51b73de`.
7. Docs close-out for 130 + 131: `246ef9e` + `5f04046`.
8. **This session**: Issue 132 deferred (API blocker); **Issue 133** code-complete
   in working tree, pending commit + push.

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
| Local HEAD | `5f04046` (synced with origin/main; Issue 133 uncommitted) |
| Alembic head (local) | `0020_creator_insight_index` |
| Alembic head (prod) | `0019_clip_style_preset` (0020 still pending — apply on prod when convenient) |
| Issues 128/129/123/130/131/133 | ✅ Deployed (130/131 in `51b73de`; 133 pending push) |
| Issue 132 | ⛔ Deferred — blocked on API availability |
| Issue 134 | 🔲 Not started (next) |
| Test count | 840 passed / 2 skipped |
| Default model (Issues 128–131) | `claude-haiku-4-5-20251001` |
| Secret names (never log values) | `STRIPE_SECRET_KEY`, `JWT_SECRET_KEY`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `GOOGLE_OAUTH_CLIENT_SECRET`, `TOKEN_ENCRYPTION_KEY`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `GHCR_TOKEN`, `DEEPGRAM_API_KEY` |

---

## CONSTRAINTS & GOTCHAS

- **`git push` auto-deploys to production** via self-hosted runner. Verify
  `git status` and test count before pushing.
- **Production migration `0020` still pending.** Run `alembic upgrade head` on
  the prod VM when convenient — until then, `creator_insights` queries do full
  table scans. Command:
  `docker compose -f docker-compose.prod.yml exec app alembic upgrade head`.
- **First post-Issue-133 production render — watch for libass font fallback.**
  If the Anton fetch failed during image build, captions render in Open Sans
  instead. Pattern in logs: `[libass] Glyph not found in font ...`.
- **`/claude-api` skill is mandatory** before writing any Anthropic SDK code
  (CLAUDE.md One Rule). Issue 134 (filler word removal) is mostly transcript +
  ffmpeg work — the LLM gate may not trigger, but check.
- **`ruff check` AND `ruff format --check` are both CI gates** — always run
  before pushing. CI's ruff is 0.15.15; venv's ruff at `.venv/bin/python -m ruff`
  matches CI. System `python3.12 -m ruff` is 0.15.14 (laxer — do not rely on it).
- **Rate-limit test pollution (local only):** if
  `test_improvement_post_handles_concurrent_insert_race` fails with 429, run
  `redis-cli del "LIMITS:LIMITER/testclient//creators/me/improvement-brief/10/1/hour"`.
- **psycopg3 + Alembic + `CREATE INDEX CONCURRENTLY`** does not work. Use plain
  `op.create_index()` even on tables that may be large in prod.
- **YouTube chat-replay is permanently blocked** (Issue 132 lesson). Do not
  reopen unless Google publishes an official replay endpoint.

---

## POINTERS

- `docs/SOT.md` — current stack, file structure (just updated for Issue 133)
- `docs/PROJECT_STATE.md` — every issue's status + session log (Issue 133 entry added)
- `docs/issues.md` — backlog (127–131 ✅, 132 ⛔ deferred, 133 ✅, 134–136 queued)
- `docs/DECISIONS.md` — deviation log (2026-06-07 entries for Issue 133 + Issue 132 deferral)
- `docs/assessment/REPORT.md` — latest /assess verdict + ranked register
- `docs/COMPLIANCE.md` — YouTube ToS, data retention, privacy posture
- `CLAUDE.md` — project rules; the One Rule is non-negotiable
- AutoMem index: `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/MEMORY.md`
