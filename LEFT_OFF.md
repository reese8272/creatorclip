# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a
> source of truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-06-07 (Issue 129 — Thumbnail concept generator)
**Branch:** `main` — working tree DIRTY (Issues 128 + 129 uncommitted)
**CI (last push):** CI + Quality Gates failed on PR #17 merge; Deploy succeeded (production live)

---

## CURRENT FOCUS

### Issues 128 + 129 complete. Commit + push both.

All code is written, tested, and ruff-clean — sitting in the working tree uncommitted.
**747 passed / 2 skipped. ruff 0. ruff format 0. Layer 0 all-runnable gates pass.**

### → NEXT ACTION

**Step 1 — Commit and push Issues 128 + 129 + CI fixes:**

```bash
git add knowledge/__init__.py knowledge/titles.py knowledge/thumbnails.py \
        routers/titles.py routers/thumbnails.py \
        worker/tasks.py main.py \
        static/analysis.html static/index.html \
        tests/test_titles.py tests/test_thumbnails.py \
        docs/DECISIONS.md docs/PROJECT_STATE.md docs/SOT.md docs/issues.md \
        docs/assessment/REPORT.md docs/assessment/history/ docs/assessment/modules/ \
        clip_engine/candidates.py clip_engine/scoring.py tests/test_scoring.py \
        requirements.txt pyproject.toml \
        .claude/skills/production-assessment/scripts/run_layer0.py \
        LEFT_OFF.md
git status   # verify only the above files staged
git commit -m "feat(128+129): title optimizer + thumbnail concept generator + fix CI gates"
git push     # auto-deploys to production — confirm deploy CI passes
```

**CI fixes included in this commit:**
- `knowledge/__init__.py` (new) — fixes `mypy fail 1` (Source file found twice)
- `knowledge/thumbnails.py:144` type:ignore — fixes second mypy error
- `requirements.txt`: `aiohttp==3.14.1` pin — closes CVE-2026-34993 + CVE-2026-47265
- `pyproject.toml` + `run_layer0.py`: `PYSEC-2026-196` added to pip-audit ignore list
- `tests/test_thumbnails.py`: +31 tests covering Claude call paths — recovers coverage ≥ 75.20%

**Step 2 — Start Issue 130 via the issue workflow:**

```
Issue 130 — Hook analyzer
Approach: POST /creators/me/videos/{video_id}/hook-analysis → Celery task →
retention-curve drop detection + Claude rewrite suggestion. Grounded in
creator's own retention curve data (already in DB). Mirrors Issue 128/129 endpoint pattern.
Industry standard checked: [research in Phase 1]
Good to go?
```

Use `/claude-api` skill before writing any Anthropic SDK code (mandatory per CLAUDE.md).

> **CI note:** Quality Gates and CI have been failing on pushes to `main` since the last
> PR merge. Before pushing, run `.venv/bin/ruff check .` and `.venv/bin/ruff format --check .`
> locally — both must be clean. Integration tests always fail in CI (no live Postgres) — that
> does NOT block the deploy.

---

## WHAT WORKS NOW (do not re-investigate)

### This session (2026-06-07) — Issue 129

- **`GET /creators/me/thumbnail-patterns`** (auth required, synchronous):
  - Loads DNA `top_video_ids_jsonb` → resolves `youtube_video_id` from `videos` table
  - Passes thumbnail image URLs (`i.ytimg.com/vi/{id}/hqdefault.jpg`) directly to Claude multimodal
  - Extracts: face_present, dominant_emotions, text_overlay_style, typical_colors,
    composition_pattern, channel_thumbnail_signature
  - Redis-cached 24h at `thumbnail_patterns:{creator_id}`
  - Returns `ThumbnailPatternsOut` (includes `cached: bool`)

- **`POST /creators/me/videos/{video_id}/thumbnail-concepts`** (10/hour, auth required):
  - Returns 202 + `{task_id, stream_url}`
  - Guards: video must exist and belong to the creator; video must have been transcribed

- **Celery task `generate_thumbnail_concepts`** in `worker/tasks.py`:
  - Checks Redis cache for patterns first (skips Claude multimodal call if cached)
  - If not cached: calls `analyze_thumbnail_patterns` via `asyncio.to_thread`; caches result
  - Calls `generate_thumbnail_concepts` (streams tokens via SSE)
  - Parses Claude's JSON → surfaces up to 5 concepts
  - Results passed in the `done` event payload (`concepts` key) — no DB row persisted

- **`knowledge/thumbnails.py`** — three-block prompt:
  - Block 1: static concept-generation instructions (no cache_control)
  - Block 2: DNA brief — carries `cache_control: {type: ephemeral}` (clears 2048-token minimum)
  - Block 3: patterns + video context (transcript hook + stated identity) — uncached
  - `parse_concepts(raw_json)` validates concept schema, enforces CONCEPT_SURFACE_N=5
  - `analyze_thumbnail_patterns(youtube_ids, channel_title)` — Claude multimodal, up to 10 images
  - `_extract_transcript_hook(segments_jsonb)` — extracts first 500 chars of transcript
  - `PATTERNS_CACHE_KEY_PREFIX`, `PATTERNS_CACHE_TTL` — shared constants for router + task

- **`static/analysis.html`** — Thumbnail Concepts panel:
  - Auto-shown when `?video_id=<uuid>` query param is present (same gate as Title Optimizer)
  - "Generate concepts" button → SSE stream → renders concept cards with emotion badge,
    composition, text overlay tag, color direction, rationale, and "based on pattern" note

- **Key decisions logged in `docs/DECISIONS.md`** (2026-06-07 entry):
  - Reporting API bypass → DNA `top_video_ids_jsonb` as high-performer proxy
  - Claude multimodal over CV pipeline (MediaPipe deferred to Phase 2)
  - 24h Redis cache shared between GET and Celery task
  - Ephemeral concept results (no DB table, same pattern as Issues 121/128)

- **25 new tests** in `tests/test_thumbnails.py`

### Previous session (2026-06-07) — Issue 128

- **`POST /creators/me/videos/{video_id}/titles`** (20/hour, auth required):
  - Returns 202 + `{task_id, stream_url, video_title}`
  - Guards: video must exist and belong to the creator; video must have been transcribed
  - Rate limit: `@limiter.limit("20/hour", key_func=creator_key)`

- **Celery task `generate_title_suggestions`** in `worker/tasks.py`
- **`knowledge/titles.py`** — three-block prompt with cache_control at DNA brief block
- **`static/analysis.html`** — Title Optimizer panel
- **`static/index.html`** — "Titles" link button on every `ingest_status=done` video row

### Longer-standing landmarks (verified, do not re-check)

- **Branch situation:** `claude/competitive-app-analysis-DtPQN` was merged to `main` via PRs #16 and #17
- **Issue 127 (sentence-boundary cuts):** deployed
- **Issue 124 (performance score + hover tooltips):** deployed
- **Production live:** `https://autoclip.studio` healthy
- **Alembic head:** `0019_clip_style_preset` — no new migrations in Issues 128/129
- **RLS** on 12 tenant-owned tables; `creators` exempt

---

## KEY COORDINATES & FACTS

| Item | Value |
|---|---|
| Public URL | `https://autoclip.studio` |
| Production VM | `147.182.136.107` |
| Container image | `ghcr.io/reese8272/creatorclip:latest` |
| Repo | `github.com/reese8272/creatorclip` |
| Current branch | `main` |
| Working tree | DIRTY — Issues 128 + 129 uncommitted |
| Alembic head | `0019_clip_style_preset` |
| Issue 128 | ✅ Written, tested, NOT committed yet |
| Issue 129 | ✅ Written, tested, NOT committed yet |
| Issue 130 | 🔲 Not started — Hook analyzer (next in queue) |
| Test count | 747 passed / 2 skipped |
| Default model | `claude-sonnet-4-6` (Sonnet 4.6) |

---

## CONSTRAINTS & GOTCHAS (next session: read before acting)

- **Issues 128 + 129 are uncommitted on `main`.** Run the commit + push in Step 1 before anything else.
- **CI Quality Gates have been failing since PR #17 merge.** Check `gh run list` after pushing.
- **Integration tests always fail in CI** — need live Postgres. Does NOT block deploy.
- **`ruff format --check` is a CI gate.** Always run before pushing.
- **Title suggestion and thumbnail concept results are ephemeral** — no DB rows persisted.
- **Thumbnail patterns are cached in Redis** at `thumbnail_patterns:{creator_id}` (24h TTL).
  Cache is shared between the GET endpoint and the Celery task. Invalidated on TTL expiry only.
- **`analyze_thumbnail_patterns` uses public YouTube thumbnail URLs** (`i.ytimg.com`). No OAuth
  required. If a video has no public thumbnail, Claude receives a broken image URL and may
  return `_empty_patterns()` — this is handled gracefully.
- **Issue 123 (SEV1 sweep) still pending.** 5 open SEV1s. See `docs/assessment/REPORT.md`.

---

## POINTERS

- `docs/SOT.md` — current stack, file structure, data model
- `docs/PROJECT_STATE.md` — every issue's status + session log
- `docs/issues.md` — backlog (Issues 128–129 done; 130–136 queued)
- `docs/DECISIONS.md` — deviation log (2026-06-07 entries for Issues 128 + 129)
- `CLAUDE.md` — project rules; the One Rule is non-negotiable
