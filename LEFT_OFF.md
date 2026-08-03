# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-08-03 (Lane **L25 Batch A** shipped to production)
**Branch:** `main` @ `40a53d3` — **working tree clean**, `0 0` vs `origin/main`
**staging:** `0 0` vs `main` (a 314-commit drift was closed this session)
**Prod DB head:** **`0050`** (was 0049 — `0050_poster_frames` applied by the deploy's staging gate)

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source of truth.

---

## CURRENT FOCUS

**Start Lane L25 Batch B (Issues 389–392) — "make it an application, not a webpage."**
Batch A is merged, deployed and verified live; Batch B is unblocked and there is nothing left to
finish behind it.

### → NEXT ACTION

1. **Read the queue first.** `docs/issues.md` → `# Batch B` (389–392). Each brief carries
   what/why/in-repo-evidence/industry-standard-with-links; the evidence line numbers were accurate as
   of 2026-08-03 but **Batch A moved a lot of frontend code — re-grep before trusting any `file:line`
   in a Batch B brief.**
2. **Respect the hard sequencing chain.** `docs/PROJECT_STATE.md` records it:
   **#386 → #390 → #391 → #393.** #386 is DONE, so **#390 (Timeline v2) is next and can start now** —
   it attaches to the `VideoPlayer` primitive shipped this session.
   **#389 (app shell) is independent** and is the cheapest visible win in the batch.
3. **Branch off `main`, not `staging`.** `docs/BRANCHING.md` documents `feature/* → staging → main`,
   but the last three lanes (PRs #67, #68, #69) went **lane-branch → `main`** directly, and `staging`
   is only kept in sync afterwards. Follow the practice, not the doc, unless you intend to fix the doc.
   Suggested: `git checkout -b wave/l25-batch-b`.
4. **Run local gates on Node 22**, not the system default (see CONSTRAINTS):
   `export PATH=/home/reese/.nvm/versions/node/v22.17.1/bin:$PATH`
5. **Before any `clip_engine/` change**, the eval harness must stay green:
   `.venv/bin/python -m pytest tests/test_clip_engine.py -q`

**No blocker.** Nothing is half-finished; there is no in-flight failure to resume.

---

## WHAT WORKS NOW (verified this session — do not re-investigate)

| Gate | Value |
|---|---|
| Backend pytest | **2516 passed**, 64 skipped, 173 deselected (2480 at W5) |
| Frontend vitest | **409 passed / 64 files** (354/55 at W5) |
| ruff · ruff format · mypy | 0 · clean · 0 |
| coverage | **83.2%** (floor 83.00) |
| module floors | **clip_engine 92.72** (floor 91.0, up from 92.51) · preference 90.45 · crypto/limiter/auth 100.0 |
| bandit · pip-audit | 0/0 · 0 |
| Playwright | **52 passed** desktop+mobile; **axe 20/20** (10 routes × 2 projects) |
| CI on `b24aade` | **12/12 jobs**, incl. Visual regression + Integration tests |
| Deploy | Docker publish ✓ → **Staging gate (data-bearing DB) ✓** → Deploy → autoclip.studio ✓ |
| Live | `GET /health` **200** — postgres/redis/storage all `ok`; `/app/login` 200 |

### Batch A shipped (all six, zero unchecked acceptance boxes)

**384** icon system · **385** six Radix primitives · **400a** token/elevation foundation ·
**386** `VideoPlayer` · **388** de-debug surfaces · **400b** composition pass · **387** poster frames.
Full detail in `docs/PROJECT_STATE.md` (top entry) and four `docs/DECISIONS.md` entries.

### Proven this session — do NOT re-derive

- **#387's integration lane PASSED in CI.** The RLS-enforced cross-creator cases for the two poster
  endpoints could not run locally (no Docker) but are green on the runner. Migration `0050` also ran
  against the **persistent data-bearing staging DB** in the deploy gate, so the schema is proven on
  real data, not just a fresh test schema.
- **#385 builds SIX primitives, not the seven its brief specced.** Slider / DropdownMenu / Popover
  have **zero call sites**; RadioGroup (not on the list, but with a live consumer) replaced them. The
  acceptance criteria in `docs/issues.md` were amended. Their first real consumers are #390 / #394 /
  #396 / #398 — **build them when you get there, not before.**
- **Radix Select THROWS on `value=""`.** `components/ui/select.tsx` owns a `__none__` sentinel and
  translates both ways, so call sites keep passing `''`. Do not "simplify" it away.
- **`--shadow-inset` never composed with `--shadow-sm`** (same Tailwind namespace, both write
  `--tw-shadow`). Now `--inset-shadow-highlight` in v4's inset namespace. Verified in the built CSS.
- **Seven colour tokens were dead**, incl. `App.tsx`'s crash-recovery screen written entirely in
  shadcn defaults the project never declared. All fixed; `src/test/design-tokens.contract.test.ts`
  now makes it a test failure.
- **axe's caption rule lands in `results.incomplete`, not `violations`,** for a `<track>`-less video —
  measured, so a caption-less `VideoPlayer` does not trip the a11y gate.

---

## THE ARC THAT LED HERE

1. **W0–W5 (previous sessions)** closed every buildable issue in the old tracker; the queue emptied.
2. **2026-08-03, tracker reset.** The 7,096-line `docs/issues.md` had stopped working as a queue.
   Archived verbatim to `docs/issues-archive-2026-08-03.md`; rebuilt around **Lane L25 — Editor &
   Craft (384–405)** in five batches from a review of the editor/presentation layer against the 2026
   field. Finding: the engine is beta-ready and differentiated; the surface is not.
3. **This session built all of Batch A** (384–388 + 400) sequentially on `wave/l25-batch-a`.
4. **A toolchain outage interrupted it** — Homebrew npm could not install anything. Diagnosed,
   repaired, and logged (see CONSTRAINTS).
5. **Merged as PR #69 → `main` (`40a53d3`)**, deployed to production, `staging` re-synced, lane branch
   deleted local and remote.

---

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Repo | `github.com/reese8272/creatorclip` |
| Production | `https://autoclip.studio` — VM + docker-compose + Cloudflare tunnel (**not** Render) |
| Branches | `main`, `staging` only — both at `40a53d3` |
| Last PR | **#69** — L25 Batch A (merged 2026-08-03) |
| Deploy chain | push to `main` → `docker-publish.yml` → `deploy.yml` (staging gate → prod) |
| Baseline regen | `gh workflow run ci.yml -f update_snapshots=true --ref <branch>` → `gh run download <id> -n visual-baselines-<sha>` |
| Node for local gates | `/home/reese/.nvm/versions/node/v22.17.1/bin` (**`frontend/.nvmrc` = 22**) |
| Python | `.venv/bin/python` (system `python3` lacks pydantic) |
| Redis for unit lane | `redis-server --daemonize yes --save '' --appendonly no` |
| Layer 0 | `.venv/bin/python .claude/skills/production-assessment/scripts/run_layer0.py` |
| Alembic head | `0050_poster_frames` |
| New R2 prefix | `posters/{creator_id}/…` — creator-scoped so `DELETE /auth/me` reaches it |
| New Beat task | `backfill-video-posters-hourly` → `worker.tasks.backfill_video_posters` |
| Secrets | `.env` on the VM; `TOKEN_ENCRYPTION_KEY` rotation in `docs/RUNBOOKS.md`. **Names only — never values.** |

---

## CONSTRAINTS & GOTCHAS

- **Merging to `main` deploys to production.** No confirmation step beyond the staging gate.
- **Use Node 22 locally.** `brew reinstall node` this session moved the system Node to **26.5.1**,
  under which **jsdom stops exposing a global `localStorage`** and `Walkthrough.test.tsx` fails —
  pre-existing, passes on 22, and 22 is what CI's three `node-version` jobs pin.
- **Homebrew npm 11.5.2 on Node 24 could not install anything** — exited 1 with an *empty* error and
  left a half-extracted `node_modules`. Fixed by `brew reinstall node`. **Never try
  `npm install -g npm@…` as the repair; it destroys the brew npm.** Full diagnosis + every ruled-out
  hypothesis: `~/.claude/ISSUES_LOG.md` **ISSUE-2026-08-03-01**. A silent npm exit-1 is a *toolchain*
  signal — reproduce in a scratch dir before touching the project's lockfile.
- **Visual baselines MUST come from `ubuntu-latest`**, never locally (WSL2 anti-aliasing).
  **`--update-snapshots` only rewrites snapshots that FAIL** the `maxDiffPixelRatio: 0.01` comparison,
  so an unchanged PNG in the artifact is expected, not a bug — verify by diffing artifacts, don't guess.
- **CI runs `ruff format --check`, which the local Layer-0 script does not.** Run
  `ruff format --check .` before pushing — it was this session's only CI lint failure.
- **Integration tests cannot run on this box** (no Docker) — CI-verified only.
- **The unit lane needs a live Redis** (the rate limiter has no in-memory fallback).
- **Four structural gates now exist** in `frontend/src/test/` (`sourceScan.ts`): no glyph icons, no
  native form controls, no `<video controls>`, no undeclared colour token. They read the **source
  tree**, so a new emoji / `<select>` / `<video controls>` fails `npm test`, not just review.
  They use `import.meta.glob(?raw)` + the TypeScript AST **deliberately** — `node:fs` fails `tsc -b`
  (no `@types/node` in `tsconfig.app.json`) and a regex false-positives on ~2,600 box-drawing
  characters in comment banners.
- **`docs/UI.md` is the design SoT; `frontend/src/index.css` is the implementation SoT.** They were
  reconciled this session — when they disagree, fix the mismatch, don't fork.
- **The honesty constraint is load-bearing and structurally tested.** "Predicts fit, does not promise
  virality" and the estimate-not-guarantee wording must stay verbatim on every tool surface.
- **A failed visual-regression run uploads no diff artifact** — you cannot see what changed from the
  run alone. Workaround is the regen-and-compare above.

---

## OPEN, LOGGED, NOT FIXED

Canonical list is `docs/OFF_COURSE_BUGS.md`. The ones most likely to matter next:

1. **`/settings` has pre-existing serious contrast failures** (2.14–2.54:1) from the 2026-06-23 "Soon"
   preview rows — decorative disabled mocks under `pointer-events-none opacity-50`, which halves
   contrast while leaving fake controls in the accessibility tree. **This is why `settings` is the one
   dense route still outside the axe gate.** Likely a one-attribute fix (`aria-hidden` on the mock).
   Worth promoting to a real issue.
2. **#387's ffmpeg poster chain is asserted against mocked `_run` only.** The staging gate proved the
   *schema*, not frame extraction. It needs one pass over a genuinely awkward real file — a VFR screen
   recording, an `.mkv` with a broken index, or a source shorter than the seek offset — which is
   exactly what the seek-0 fallback exists for. **Do this during the next staging soak.**
3. **`clips/` is not creator-scoped**, so `DELETE /auth/me`'s `clips/{creator_id}/` prefix purge
   matches nothing — a live right-to-erasure gap. Flagged, not fixed (needs an object migration).
   `posters/` deliberately does not replicate the pattern.
4. **Review-queue badge counts rendered clips, not shortlisted-unreviewed ones** (needs a backend field).
5. **`App.tsx`'s `*` route silently redirects typos to `/dashboard`** instead of 404ing.
6. **`ORIGINALITY_SIMILARITY_THRESHOLD=0.92` is UNVALIDATED** — a silent advisory means "unmeasured",
   not "clean".

**Operator punch-list** (no code can close these — `docs/GO_LIVE.md` is canonical): **#29** Google
OAuth verification · **#26/#28** friend beta · **#282** uptime monitor (still open, beta-critical) ·
**#255** off-box key escrow · **`MAILING_ADDRESS` is deliberately unset** — `config.py` skips all
lifecycle email while empty, because CAN-SPAM requires a real postal address. **Do not "fix" this in
code**; whatever is set becomes public in every footer.

---

## POINTERS

| Doc | Purpose |
|---|---|
| `docs/issues.md` | Work queue — **Batch A ✅ done; Batch B (389–392) is next** |
| `docs/PROJECT_STATE.md` | Progress log — top entry is the Batch A close-out |
| `docs/DECISIONS.md` | Top four entries are this session (387, 400a, 385, 384) |
| `docs/UI.md` | Design system — **new Elevation + Hierarchy sections** |
| `docs/SOT.md` | Stack, schema, structure — primitive layer + `poster_uri` documented |
| `docs/COMPLIANCE.md` | ToS/retention — **new poster-frame row + sharpened audio row** |
| `docs/OFF_COURSE_BUGS.md` | Logged-not-fixed defects |
| `docs/GO_LIVE.md` | Canonical launch scorecard |
| `docs/BRANCHING.md` | Promotion model (**note the practice/doc divergence above**) |
| `~/.claude/ISSUES_LOG.md` | Cross-project incident log — **ISSUE-2026-08-03-01** is the npm outage |
| Memory dir | `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` |
