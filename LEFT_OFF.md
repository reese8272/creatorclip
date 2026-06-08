# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a
> source of truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-06-07 (Issue 135 code-complete; Issues 133 + 134 deployed earlier this session)
**Branch:** `main` — HEAD `63be6a1` (synced with origin/main) — **uncommitted Issue 135 changes in working tree**
**Working tree:** DIRTY — Issue 135 files staged for next commit
**CI (most recent green):** Quality Gates ✅ · Integration tests ✅ · CI ✅ · Docker publish ✅ · Deploy ✅ (for `63be6a1`)

---

## CURRENT FOCUS

### Issue 135 — Text-based transcript editor → code complete, awaiting push

Descript-style word-selection editor in `static/review.html`. Selected
word ranges queue as cuts; confirm batch-renders via the same
`render_cleaned_clip_file` pipeline shipped in Issue 134. Result lands
on `Clip.cleaned_render_uri` and uses the existing `/clean/confirm`
swap path — same UX flow as Issue 134's filler-removal pass.

Key deviations from spec (logged in `docs/DECISIONS.md`):
- **D1**: dropped the 24 h `EDITOR_ORIGINAL_RETENTION_HOURS` purge —
  reuse Issue 134's `cleaned_render_uri` side-by-side pattern instead.
  Original `render_uri` is never modified.
- **D2**: added hard caps (≥5 s kept, ≤85 % removed) the spec didn't
  mention; soft 40 % warning stays as a UI band.
- **D4**: fixed a **latent bug from Issue 134** — `render_cleaned_clip_file`
  applied a constant 5 ms `afade` per splice; a kept segment shorter than
  10 ms would request a fade longer than half-segment and crash ffmpeg.
  Now `afade_s = min(0.005, seg_dur / 2.0)`.

### Issues 133 + 134 + ruff-pin → DEPLOYED earlier this session

- `3b15c0b` — Issue 133 (animated caption styles)
- `f133983` — Issue 134 (filler + silence clean pass)
- `b319f7b` + `63be6a1` — ruff format fix + CI ruff pinned to 0.15.15

**Prod migration auto-applied by the deploy workflow** — there is no
manual `alembic upgrade head` step. The Issue 134 LEFT_OFF mistakenly
flagged this as pending; the deploy workflow already runs `alembic
upgrade head` before container rollout. `clips.cleaned_render_uri`
column verified live; `/clean-preview` returned 401 (auth gate — route
is reachable).

### Issue 132 — DEFERRED

YouTube Data API has no chat-replay endpoint; third-party scrapers
violate ToS §IV.A. Not buildable inside compliance posture.

### → NEXT ACTION

1. **Review the dirty working tree** for Issue 135. Files touched:
   - `clip_engine/edits.py` (new — ~155 lines)
   - `clip_engine/render.py` (afade guard fix)
   - `worker/tasks.py` (`edit_clip` task + `_edit_clip_async`)
   - `routers/clips.py` (GET `/transcript` + POST `/cuts` endpoints)
   - `static/editor.js` (new — ~280 lines)
   - `static/review.html` (editor panel + styles + script tag + unmount hook)
   - `tests/test_edits.py` (new — 25 tests)
   - `docs/DECISIONS.md`, `docs/SOT.md`, `docs/PROJECT_STATE.md`,
     `docs/issues.md`, `LEFT_OFF.md`
2. **Commit + push**. `git push` auto-deploys to prod (deploy workflow
   handles migrations automatically — no manual step).
3. **First real `/cuts` invocation on prod** — watch worker logs for:
   - `ffmpeg ... atrim` errors on sub-frame keep ranges (should be
     impossible — the validator floors at 0.04 s and the afade guard
     handles short segments — but worth confirming).
   - `getSelection()` JS errors in browser console if a user's selection
     crosses paragraph blocks or starts/ends on whitespace text-nodes —
     the `_selectionToWordIndices` walker handles both cases but real-
     browser selection rarely matches synthetic tests.
4. **Next active issue: 136 — UI upgrade (dark editor mode + marketing
   hero).** Two-part visual upgrade — review.html dark theme + landing
   page hero. UI work only; no backend changes expected.

---

## WHAT WORKS NOW (do not re-investigate)

### Built this session

**Issue 133** (deployed `3b15c0b`): pysubs2 + libass animated captions.

**Issue 134** (deployed `f133983`): filler+silence clean pass with
preview + side-by-side confirm.

**Issue 135** (in working tree):
- `clip_engine/edits.py::validate_user_cuts(segments, clip_duration_s)`
  — pure function returning `ValidatedEdit(cut_segments, keep_ranges,
  kept_duration_s, percent_removed)`. Raises
  `CutValidationError(code=...)` for any safety-invariant violation.
- `clip_engine.render.render_cleaned_clip_file` — afade guard
  `afade_s = min(0.005, seg_dur / 2.0)` (Issue 134 latent-bug fix).
- `worker.tasks.edit_clip` Celery task + `_edit_clip_async` — uploads
  to `clips/{id}_edit.mp4`, persists `Clip.cleaned_render_uri`.
- `GET /clips/{id}/transcript` (60/hour) + `POST /clips/{id}/cuts`
  (20/hour) router endpoints.
- `static/editor.js` — word-span DOM + native `getSelection()` snapped
  on `mouseup` + localStorage cut queue + per-cut × removal + one-level
  undo + batch-on-confirm + side-by-side preview + reuse of
  `/clean/confirm` swap.

### Lessons banked this session (avoid repeating)

1. **The Issue 134 afade was a latent bug** — caught while building
   Issue 135's sub-frame floor. Constant `afade_s` requires every kept
   segment to be ≥ 2 × afade. The fix `afade_s = min(default, seg_dur/2)`
   is the principled form.
2. **`getSelection()` over `<span data-…>`** with a literal space
   text-node between spans (NOT inside) is the canonical 2026 pattern.
   `<button>` per word breaks native text selection; `contenteditable`
   mutation events are unreliable for timestamp sync.
3. **`Clip.cleaned_render_uri` is shared by Issue 134 + 135.** A clip
   can only be in one "pending edit" state at a time. The UI should
   communicate this when both panels are visible — currently the user
   can clobber a pending clean by applying a transcript edit, which
   silently overwrites. Future Issue: surface a "you have a pending
   clean — discard or confirm before editing" affordance.
4. **CI ruff is now pinned to 0.15.15** — same as `.venv`. No more
   format drift between local + CI. Bump in lockstep when ready.
5. **Deploy workflow auto-runs `alembic upgrade head`** — never
   manually run migrations on prod. (My Issue 134 LEFT_OFF was wrong
   about this; correcting here.)

### Test count + Layer 0

- **889 passed / 2 skipped** (up from 864 at the start of this session — +25).
- Layer 0: ruff 0 · mypy 0 · freshness ok.

---

## THE ARC THAT LED HERE

1. Competitive intelligence → Issues 127–136 filed ROI-ordered.
2. Issues 127, 128, 129, 123, 130/131 — all deployed in prior sessions.
3. Issue 133 (animated caption styles): deployed `3b15c0b`.
4. Issue 132 (live-chat spike): deferred — API blocker.
5. Issue 134 (filler+silence removal): deployed `f133983`.
6. **This session, continued**: Issue 135 (text-based editor) —
   code-complete in working tree, pending commit + push.

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
| Local HEAD | `63be6a1` (synced; Issue 135 uncommitted) |
| Alembic head | `0021_clip_cleaned_render_uri` (both local + prod) |
| **Migration mechanism** | **Deploy workflow runs `alembic upgrade head` automatically before container rollout — no manual step.** |
| Issues deployed | 127 ✅ 128 ✅ 129 ✅ 123 ✅ 130/131 ✅ 133 ✅ 134 ✅ |
| Issue 132 | ⛔ Deferred — blocked on API availability |
| Issue 135 | ✅ Code-complete, push pending |
| Issue 136 | 🔲 Not started (next) |
| Test count | 889 passed / 2 skipped |
| Ruff version (local + CI pinned) | `0.15.15` |
| Default LLM model (analysis features) | `claude-haiku-4-5-20251001` |
| Secret names (never log values) | `STRIPE_SECRET_KEY`, `JWT_SECRET_KEY`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `GOOGLE_OAUTH_CLIENT_SECRET`, `TOKEN_ENCRYPTION_KEY`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `GHCR_TOKEN`, `DEEPGRAM_API_KEY` |

---

## CONSTRAINTS & GOTCHAS

- **`git push` auto-deploys to production** via self-hosted runner.
  Deploy workflow also auto-applies migrations (`alembic upgrade head`).
- **`Clip.cleaned_render_uri` is shared between Issue 134's clean pass
  and Issue 135's text editor.** A clip in one pending-edit state will
  be clobbered by the other. Future Issue should surface a "discard or
  confirm first" affordance in the UI.
- **`/cuts` validator is strict by design.** 5 s kept / 85 % removed
  hard caps — anything below/above returns 422 with structured
  `{code, message}` body. UI should surface `code` not just `message`.
- **First post-Issue-133 production render** — still watch for
  `[libass] Glyph not found` worker logs from Anton font fetch
  failures during image build.
- **CI ruff is pinned to 0.15.15** in `.github/workflows/ci.yml` — same
  as `.venv`. Bump in lockstep with `pip install ruff==<new>` when
  ready.
- **YouTube chat-replay is permanently blocked** (Issue 132 lesson).
- **`/claude-api` skill is mandatory** before writing any Anthropic SDK
  code (CLAUDE.md One Rule). Issue 136 is UI-only — gate likely won't
  trigger but check.
- **psycopg3 + Alembic + `CREATE INDEX CONCURRENTLY`** does not work.
  Use plain `op.create_index()`.
- **Rate-limit test pollution (local only)**: if
  `test_improvement_post_handles_concurrent_insert_race` fails with
  429, run
  `redis-cli del "LIMITS:LIMITER/testclient//creators/me/improvement-brief/10/1/hour"`.

---

## POINTERS

- `docs/SOT.md` — current stack + file structure (Issue 135: edits.py + editor.js added)
- `docs/PROJECT_STATE.md` — every issue's status + session log (Issue 135 entry added)
- `docs/issues.md` — backlog (127–131 ✅, 132 ⛔, 133 ✅, 134 ✅, 135 ✅, 136 queued)
- `docs/DECISIONS.md` — deviation log (2026-06-07 entries for Issues 132, 133, 134, 135 D1–D6)
- `docs/assessment/REPORT.md` — latest /assess verdict + ranked register
- `docs/COMPLIANCE.md` — YouTube ToS, data retention, privacy posture
- `CLAUDE.md` — project rules; the One Rule is non-negotiable
- AutoMem index: `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/MEMORY.md`
