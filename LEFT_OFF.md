# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-07-30 (Lane L23 — standalone Review & Editor tools — COMPLETE)
**Branch at close:** `feat/standalone-review-editor` @ `6c69bba` + close-out docs commit — **5+ commits ahead of `origin/main`, not pushed, no PR yet**
**Prod:** healthy at `a50b332` (Deploys 1+2, 2026-07-29); prod DB head **0047** — this branch adds migrations **0048 + 0049** (not applied anywhere yet)

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source of truth.

---

## CURRENT FOCUS

**Lane L23 (Issues 369–373) is fully built and committed** on `feat/standalone-review-editor`:
Review and Editor are now standalone tools — direct access + upload-in-place (369), full-source
player + searchable transcript (372), creator-made clips via drag-select + honest per-clip export
(373), video-level style reviews (370), and style-preference distillation feeding scoring + DNA
briefs (371). One commit per issue. Suites at close: **backend 2377 / frontend 309**;
ruff/mypy/tsc/eslint clean; Layer 0 green except a NEW pytest dev-dep advisory (see gotchas).

### → NEXT ACTIONS

1. **Push + PR to main**: `git push -u origin feat/standalone-review-editor` → `gh pr create`
   (base main). Merge to main = Docker publish → staging gate → prod deploy — **owner authorizes**.
   The GATING Playwright visual job may need baseline regen (long-form editor layout changed):
   `gh workflow run ci.yml -f update_snapshots=true` → commit artifact (never from WSL2).
2. **On deploy**: `alembic upgrade head` applies **0048 (video_feedback) + 0049
   (creator_style_notes)** — new-table + RLS, metadata-cheap. Run the integration lane
   (`pytest -m integration`, incl. new `test_video_feedback_integration.py`) on a box with
   docker-compose Postgres.
3. **Live smoke of the new surfaces**: long-form editor plays source + transcript → drag-create a
   clip → renders → "Your clips" + Export; `/app/review?video_id=X&mode=style` records a style
   note; after ≥3 tagged/noted feedbacks `distill_style_prefs` writes `creator_style_notes` and the
   Profile DNA card shows "What Chip has learned from your reviews". Distillation LLM call:
   `RUN_LLM_LIVE=1 .venv/bin/pytest -m llm_live tests/test_style_distill.py`.
4. **Beta path (unchanged, still open)**: #26 Google-console test users · #282 uptime monitor
   (beta-critical) · #28 friend smoke — see the 2026-07-29 PROJECT_STATE entry.
5. **Small chore queued** (OFF_COURSE 2026-07-30): pytest 8.3.3 advisory PYSEC-2026-1845
   (fix 9.0.3, dev-only) — standalone bump + plugin-compat check; until then Layer-0 pip_audit
   reads 1 vuln vs the 0 baseline.

## WHAT WORKS NOW (don't re-investigate)

- **Per-issue commits**: `4064e59` (369) → `6452168` (372) → `aad357f` (373) → `60badbe` (370) →
  `6c69bba` (371); each closed with full suites green.
- **Cache-safe style injection is CI-pinned** (`tests/test_style_distill.py`): the cached DNA block
  is byte-identical with/without style notes — third-system-block design; never concatenate notes
  into `brief_text` (DECISIONS 2026-07-30).
- **Creator clips excluded from ClipImpression logging** (IPS integrity) — `tests/test_create_clip.py`.
- **Billing sweep extended**: usage-coverage AST registries cover `preference/style_distill.py` /
  `_distill_style_prefs_async` — CI fails on any future unbilled path.
- Clip mocks now need `style_preset` (new `ClipOut.aspect` derivation) and worker tests stubbing
  `dna.profile.get_active` must also stub `get_style_notes` — all existing files already updated.
- Core loop + gates from the 2026-07-29 ready-pass unchanged (see that PROJECT_STATE entry).

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Prod | `autoclip.studio` — droplet 147.182.136.107 (`ssh creatorclip-vm`), `/opt/autoclip`, compose prod file |
| Prod DB head | `0047` (branch adds `0048_video_feedback`, `0049_creator_style_notes`) |
| New endpoints | `GET /videos/{id}/stream` · `GET /videos/{id}/transcript` · `POST /videos/{id}/clips` · `POST+GET /videos/{id}/feedback` · `GET /creators/me/dna` + `style_notes` |
| New modules | `preference/style_distill.py` · `routers/video_review.py` · `frontend/src/components/{landing/*, review/StyleReview.tsx, editor/FullTranscriptPanel.tsx}` |
| New config keys | `ANTHROPIC_MODEL_STYLE_DISTILL` · `STYLE_DISTILL_MIN_NEW` · `STYLE_DISTILL_MAX_ROWS` · `STYLE_NOTES_MAX_CHARS` (documented in `.env.example`) |
| New Celery task | `distill_style_prefs` (flag/spend/lock/debounce-gated; Haiku-billed) |
| Tracker | `docs/issues.md` Lane `L23_STANDALONE_TOOLS` — 369–373 DONE, ACs checked (371's eval AC honestly annotated) |

## CONSTRAINTS & GOTCHAS

- **This box has no Docker/Postgres** — integration lane + alembic upgrades were NOT run here
  (unit lane mocks DB by design). Run where compose exists.
- **Do not merge style notes into `brief_text` at scoring time** — invalidates the 1h prompt cache
  on every re-distillation (test-pinned; the whole point of the third-block design).
- **`/videos/{id}/stream` serves the SOURCE** — subject to the 72h retention purge; the UI shows an
  honest expired card. Don't "fix" a dead player by silently extending retention (COMPLIANCE).
- Any merge to main = prod deploy pipeline; owner authorizes.
- Local toolchain: `.venv/bin/*` only; visual baselines only via the CI dispatch flow;
  `gh pr edit` GraphQL bug → use `gh api repos/.../pulls/N -X PATCH`.

## POINTERS

| Doc | Purpose |
|-----|---------|
| `docs/SOT.md` | Stack/schema/structure (updated: 0048/0049, new modules/endpoints/components) |
| `docs/PROJECT_STATE.md` | 2026-07-30 entries (369, then L23-complete) — top of file |
| `docs/issues.md` | Lane L23 statuses + AC annotations |
| `docs/DECISIONS.md` | 2026-07-30 L23 entry — every design call + CHECK doc links |
| `docs/COMPLIANCE.md` | New data classes: video_feedback, creator_style_notes |
| `docs/OFF_COURSE_BUGS.md` | 2026-07-30 pytest-advisory chore |
| Memory dir | `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` |
