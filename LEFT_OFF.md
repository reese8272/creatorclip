# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-07-30 (Lane L23 MERGED + DEPLOYED — PR #65 → main `4d7f29c`)
**Branch at close:** `main` @ `4d7f29c` (feature branch deleted); all 12 CI checks green
**Prod:** healthy — deploy run 30561428784 succeeded; `/health` all ok; **prod DB head 0049** (migrations 0048+0049 applied); new endpoints live (401 auth-gated, SPA 200)

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

1. **Friend-beta path (the current goal)**: **#26** Google Console — owner adds the buddy's Gmail
   under Audience → Test users (unverified apps hard-block everyone else) and confirms the 4
   scopes match `youtube/oauth.py:46-51`. Then **#28** friend smoke; **#282** uptime monitor is
   still open and beta-critical (droplet must stay up once friends are invited).
2. **Live smoke of the new L23 surfaces on prod** (needs a real upload): long-form editor plays
   source + transcript → drag-create a clip → renders → "Your clips" + Export;
   `/app/review?video_id=X&mode=style` records a style note; after ≥3 tagged/noted feedbacks
   `distill_style_prefs` writes `creator_style_notes` and the Profile DNA card shows "What Chip
   has learned from your reviews". Distillation call check:
   `RUN_LLM_LIVE=1 .venv/bin/pytest -m llm_live tests/test_style_distill.py`.
3. **Small chore queued** (OFF_COURSE 2026-07-30): pytest 8.3.3 advisory PYSEC-2026-1845
   (fix 9.0.3, dev-only) — standalone bump + plugin-compat check; until then Layer-0 pip_audit
   reads 1 vuln vs the 0 baseline. Note: CI ALSO runs `ruff format --check` — run it locally
   before pushing (the local Layer-0 script doesn't).

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

- **This box has no Docker/Postgres** — unit lane mocks DB by design; the integration lane ran
  green in CI (real Postgres) and prod migrated to 0049 via the deploy pipeline.
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
