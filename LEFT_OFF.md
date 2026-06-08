# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a
> source of truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-06-07 (Issue 134 code-complete; Issue 133 deployed earlier this session at `3b15c0b`; Issue 132 deferred — blocked on YouTube API)
**Branch:** `main` — HEAD `3b15c0b` (synced with origin/main as of Issue 133 push) — **uncommitted Issue 134 changes in working tree**
**Working tree:** DIRTY — Issue 134 files staged for next commit (see "Files touched this session")
**CI (most recent green):** Quality Gates ✅ · Integration tests ✅ · CI ✅ · Docker publish ✅ · Deploy ✅ (for `3b15c0b`)

---

## CURRENT FOCUS

### Issue 134 — Filler-word + long-silence removal → code complete, awaiting push

Three endpoints + one Celery task + one new module:
- `GET /clips/{id}/clean-preview` (cheap; no render) — returns the cut list +
  `percent_removed` + warning string for >=30% removals
- `POST /clips/{id}/clean` (20/hour) — 202 + task; Celery `clean_clip` task
  re-renders the existing `render_uri` via `filter_complex` (trim+atrim+concat
  with 5 ms `afade` per splice), uploads to `clips/{id}_clean.mp4`, persists
  `Clip.cleaned_render_uri`
- `POST /clips/{id}/clean/confirm` — atomic swap `render_uri ←
  cleaned_render_uri`; idempotent (200 noop when nothing to swap)

Two-tier filler lexicon: **Tier 1** unconditional (`um`, `uh`, `umm`, …);
**Tier 2** pause-flanked only (`like`, `you know`, `so`, `right`, … —
excised only when the phrase is ≤600 ms AND flanked by an inter-word gap
≥150 ms on at least one side). Silence > 800 ms cut with 150 ms breath tail
each side.

Migration `0021_clip_cleaned_render_uri` adds one nullable TEXT column.

### Issue 133 — Animated caption styles → DEPLOYED at `3b15c0b`

Bold Pop / Gradient Slide / Minimal shipped via `clip_engine/captions.py`
(`pysubs2` + libass). Dockerfile fetches Anton from Google Fonts GitHub raw.
**On next prod render with `subtitle: "bold_pop"` — watch for
`[libass] Glyph not found in font` in worker logs.** Means the Anton fetch
failed during image build and captions are falling back to Open Sans.

### Issue 132 — DEFERRED

Phase-1 research surfaced a hard blocker: YouTube Data API has no chat-replay
endpoint for completed VOD livestreams. Third-party libs (pytchat,
chat-downloader) scrape internal endpoints — violates YouTube ToS §IV.A.
Not buildable inside compliance posture.

### → NEXT ACTION

1. **Review the dirty working tree** for Issue 134. Files touched:
   - `clip_engine/filler.py` (new — ~190 lines)
   - `clip_engine/render.py` (new `render_cleaned_clip_file` helper)
   - `worker/tasks.py` (new `_clean_clip_async` + Celery `clean_clip` wrapper)
   - `routers/clips.py` (new endpoints + `ClipOut.cleaned_render_uri`)
   - `models.py` (`Clip.cleaned_render_uri` column)
   - `alembic/versions/0021_clip_cleaned_render_uri.py` (new)
   - `static/review.html` (clean-pass UI panel)
   - `config.py` + `.env.example` (4 new knobs)
   - `tests/test_filler.py` (new — 24 tests)
   - `docs/DECISIONS.md`, `docs/SOT.md`, `docs/PROJECT_STATE.md`,
     `docs/issues.md`, `LEFT_OFF.md`
2. **Commit + push**. `git push` auto-deploys to prod (self-hosted runner).
3. **After deploy: apply migration 0021 on the prod VM**.
   ```
   docker compose -f docker-compose.prod.yml exec app alembic upgrade head
   ```
   Migration `0020` from a prior session is still pending — this run picks
   up both. Both are plain `add_column`/`create_index` — brief locks only.
4. **First real `/clean` invocation on prod**: watch for ffmpeg errors in
   worker logs. Most likely failure shapes are (a) `Invalid argument` on the
   `-filter_complex_script` arg if a clip has an unusually long keep-range
   list pushing the script past 8KB (no production clip should — but worth
   confirming on the first user-driven invocation), (b) keep-range
   floating-point edge case where `trim=start=X:end=X` slips through and
   ffmpeg refuses to parse the graph.
5. **Next active issue: 135 — Text-based editor.** Depends on 134 (uses the
   same `render_cleaned_clip_file` pipeline plus a transcript-driven
   selection UI).

---

## WHAT WORKS NOW (do not re-investigate)

### Built this session

**Issue 133** (deployed `3b15c0b`):
- `clip_engine/captions.py::build_ass_subtitles` — pysubs2 + libass
- Dockerfile installs Anton + fontconfig
- Style picker rewritten in review.html

**Issue 134** (in working tree):
- `clip_engine/filler.py::detect_cut_segments` — pure function over WhisperX
  words; returns `list[CutSegment]` with `start_s`, `end_s`, `reason`,
  `word`. Plus `merge_adjacent_cuts`, `invert_to_keep_ranges`,
  `percent_removed` helpers.
- `clip_engine/render.py::render_cleaned_clip_file` — writes a
  `filter_complex_script` (`trim`/`atrim`/`setpts`/`asetpts`/5 ms
  `afade in+out` per kept segment, terminated by `concat=n=N:v=1:a=1`) and
  invokes ffmpeg with `-filter_complex_script`. Cleanup in `finally`.
- Celery task `clean_clip` (`worker.tasks._clean_clip_async`) — runs against
  the existing `render_uri` (the burned-in captioned clip), uploads to
  `clips/{id}_clean.mp4`, persists `Clip.cleaned_render_uri`. Idempotent on
  redelivery.
- Three router endpoints + `ClipOut.cleaned_render_uri` field.
- Migration `0021` (one nullable TEXT column).
- Review-html clean panel with strikethrough preview + warning band +
  side-by-side cleaned-version player + confirm/discard buttons.

### Lessons banked this session (avoid repeating)

1. **The Tier-2 filler lexicon needs the pause-flank guard or it eats verbs.**
   Test pinned: "I like this" must survive; "and, like, impossible" must not.
2. **`-filter_complex_script` (not inline `-filter_complex`)** is the correct
   shape for any cut count ≥4. Inline scales linearly with cut count and
   risks shell arg limits on Windows builds.
3. **`acrossfade` doesn't fit a multi-segment `concat` graph topology** —
   per-segment `afade=in:d=0.005`/`afade=out:d=0.005` is simpler and
   audibly identical.
4. **The `invert_to_keep_ranges` filter MUST drop zero-width segments** —
   a cut starting at `clip_start_s` would otherwise emit a `(0, 0)` keep
   range that ffmpeg rejects at parse time. Test pinned.
5. **All Issue-134 endpoints filter by `Clip.creator_id == creator.id`** —
   matches the existing render endpoint pattern; no new isolation surface.

### Test count + Layer 0

- **864 passed / 2 skipped** (up from 840 at the start of this session — +24).
- Layer 0 gates (locally with `.venv/bin/python -m ruff`): ruff 0 · mypy 0 ·
  freshness ok. CI runs the full Layer 0 including coverage/bandit/pip-audit.

---

## THE ARC THAT LED HERE

1. Competitive intelligence → Issues 127–136 filed ROI-ordered.
2. Issues 127, 128, 129, 123, 130/131 — all deployed in prior sessions.
3. Issue 133 (animated caption styles): deployed at `3b15c0b` (this session).
4. Issue 132 (live-chat spike): deferred (this session) — API blocker.
5. **This session, continued**: Issue 134 — filler+silence removal —
   code-complete in working tree, pending commit + push.

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
| Local HEAD | `3b15c0b` (synced with origin; Issue 134 uncommitted) |
| Alembic head (local) | `0021_clip_cleaned_render_uri` |
| Alembic head (prod) | `0019_clip_style_preset` (0020 + 0021 pending — apply on prod after deploy) |
| Issues deployed | 127 ✅ 128 ✅ 129 ✅ 123 ✅ 130/131 ✅ 133 ✅ |
| Issue 132 | ⛔ Deferred — blocked on API availability |
| Issue 134 | ✅ Code-complete, push pending |
| Issue 135 | 🔲 Not started (next) |
| Test count | 864 passed / 2 skipped |
| Default model (Issues 128–131) | `claude-haiku-4-5-20251001` |
| Secret names (never log values) | `STRIPE_SECRET_KEY`, `JWT_SECRET_KEY`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `GOOGLE_OAUTH_CLIENT_SECRET`, `TOKEN_ENCRYPTION_KEY`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `GHCR_TOKEN`, `DEEPGRAM_API_KEY` |

---

## CONSTRAINTS & GOTCHAS

- **`git push` auto-deploys to production** via self-hosted runner. Verify
  `git status` and test count before pushing.
- **Production migrations `0020` AND `0021` pending.** Run after this deploy:
  `docker compose -f docker-compose.prod.yml exec app alembic upgrade head`.
  Until 0021 is applied, `Clip.cleaned_render_uri` writes will fail and the
  worker `clean_clip` task will raise — first user-driven `/clean` POST on
  prod will hit this if the migration step is skipped.
- **YouTube chat-replay is permanently blocked** (Issue 132 lesson). Do not
  reopen unless Google publishes an official replay endpoint.
- **First post-Issue-133 production render — watch for libass font fallback**
  (Anton fetch failure during image build). Pattern in logs:
  `[libass] Glyph not found in font ...`.
- **`/claude-api` skill is mandatory** before writing any Anthropic SDK code
  (CLAUDE.md One Rule). Issue 135 (text-based editor) is mostly UI work over
  the existing transcript + clean-pass pipeline — the LLM gate may not
  trigger, but check.
- **`ruff check` AND `ruff format --check` are both CI gates** — always run
  before pushing. CI's ruff is 0.15.15; venv's ruff at
  `.venv/bin/python -m ruff` matches CI. System `python3.12 -m ruff` is
  0.15.14 (laxer — do not rely on it).
- **Rate-limit test pollution (local only):** if
  `test_improvement_post_handles_concurrent_insert_race` fails with 429, run
  `redis-cli del "LIMITS:LIMITER/testclient//creators/me/improvement-brief/10/1/hour"`.
- **psycopg3 + Alembic + `CREATE INDEX CONCURRENTLY`** does not work. Use
  plain `op.create_index()` even on tables that may be large in prod.

---

## POINTERS

- `docs/SOT.md` — current stack, file structure (Issue 134: filler.py added)
- `docs/PROJECT_STATE.md` — every issue's status + session log (Issue 134 entry added)
- `docs/issues.md` — backlog (127–131 ✅, 132 ⛔, 133 ✅, 134 ✅, 135–136 queued)
- `docs/DECISIONS.md` — deviation log (2026-06-07 entries for Issues 132, 133, 134)
- `docs/assessment/REPORT.md` — latest /assess verdict + ranked register
- `docs/COMPLIANCE.md` — YouTube ToS, data retention, privacy posture
- `CLAUDE.md` — project rules; the One Rule is non-negotiable
- AutoMem index: `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/MEMORY.md`
