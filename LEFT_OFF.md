# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-08-04 (night) · **Branch:** `main` @ the Issue-413 close-out commit (after `5c6861d`) · **working tree clean**
**Trunk:** `main` and `staging` synced at close-out. No open PRs, no live feature branches.
**Prod:** `https://autoclip.studio` `GET /health` → **200**; Lexend live (413 auto-deploys, push-to-main → docker-publish → deploy).
Prod DB head **`0052_clip_edit_documents`** — unchanged by 413 (frontend-only, no migrations).

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source of truth.

---

## CURRENT FOCUS

**Issue 413 — app typeface swapped to Lexend — is BUILT, GATED, and COMMITTED direct-to-main**
(user's explicit call: no PR). All non-mono text is Lexend; Geist Sans + Inter removed; Geist Mono
stays for data. Chosen by the creator from an 11-face specimen against the live palette
(`docs/DECISIONS.md` 2026-08-04). Batches A/B remain 100% closed; Batch C not started yet.

### → NEXT ACTION

**The 413 tail is CLOSED** — baselines regenerated (all-mode, fonts force-loaded), committed
(`5c6861d`), plain CI dispatch 30960808300 = 12/12 green incl. visual, prod deployed + healthy,
staging synced. Two process fixes landed en route (`f7393c6` fonts.ready wait in `smoke.spec.ts`;
`82eae9a` `--update-snapshots=all` in `ci.yml`).

1. Resume the queue: **Issue 393 — client-side cut preview** (Batch C) via `/issue-workflow`.
   ```bash
   git checkout main && git pull --ff-only
   git checkout -b feat/393-cut-preview
   ```
2. **Before any local gate run:**
   ```bash
   export PATH=/home/reese/.nvm/versions/node/v22.17.1/bin:$PATH
   redis-cli ping || redis-server --daemonize yes --save '' --appendonly no
   ```
   and run every frontend command **from `frontend/`** (npx from repo root silently uses a CACHED
   npx Playwright and dies with `ERR_INTERNAL_ASSERTION` — bit the A/B wave, twice).

---

## WHAT WORKS NOW (verified on the Issue 413 finish state — do not re-investigate)

| Gate | Value |
|---|---|
| Layer 0 (incl. full pytest via coverage) | `ruff 0 · mypy 0 · coverage 83.38 · bandit 0/0 · pip_audit 0` — all green |
| module floors | clip_engine 92.61 · preference 90.45 · crypto/limiter/auth 100.0 |
| Frontend vitest (node 22) | **600 passed / 83 files** on the Lexend state |
| `tsc -b` · lint · build | clean · 0 errors (1 pre-existing warning in `useStageStream.ts`, logged off-course) · clean; `dist/` ships Lexend + Geist Mono only |
| Playwright visual | **6/6 true-Lexend baselines committed (`5c6861d`)**; plain dispatch 30960808300 green against them. Captures now await `document.fonts.ready`; regen dispatches use `--update-snapshots=all` |
| Live prod | `/health` 200 after the 413 auto-deploys; Lexend live at autoclip.studio |

**Layer 0 must run as `.venv/bin/python …/run_layer0.py`** — bare `python3` audits the SYSTEM
interpreter and reports ~102 phantom pip-audit vulns (re-confirmed this session; the script's own
header documents it).

**Integration/RLS tests still need Docker (absent here) — CI-verified only; say so plainly.**

### The close-out wave, in one table

| Shipped | What it was | PR |
|---|---|---|
| **407** | Resume-from-cache conflict was INVERTED — "Keep my edits" destroyed the unsaved work; roles now structural (`ConflictInfo.kind`), branch now tested | #74 |
| **408** | `extra="forbid"` on the 5 paid/render models; the "segments REJECTED" pin now proves rejection | #74 |
| **409** | e2e mock models edit-document (stateful CAS revision); persistence spec; loud catch-all; malformed-body = load error | #74 |
| **410** | Short-form timeline says "Waveform unavailable…" visibly (one shared string) | #74 |
| **411** | `/settings` passes axe + joined the gate; `opacity-50` → muted tokens (aria-hidden alone was NOT enough — measured) | #74 |
| **387 proofs** | Poster RLS cases WRITTEN into `tests/test_isolation.py` (CI-verified); real-ffmpeg awkward-file pass 5/5 locally | #74 |
| **412** | Presentation balance: untitled labels, Insights framing, CardHeader, Review column A, mono audit, empty-state hints | #75 |
| Bookkeeping | Batch A merge record back-filled; phantom baselines fixed; OFF_COURSE 32/38 closed; DECISIONS back-filled 386/388/400b | #74 |

---

## FACTS WORTH NOT RE-DERIVING (this wave)

- **`ConflictInfo.serverDoc` is ALWAYS the server's document; `present` is ALWAYS mine.** The 407
  inversion happened because the cache-resume branch stuffed the local doc into `serverDoc`. The
  `kind: 'remote' | 'resumed'` field drives the copy. A seeded-dirty-cache test pins the branch.
- **The e2e mock's `editDocSeed` is an option fixture defaulting to empty-at-revision-0** — a
  visual no-op by design. Specs needing a stored document override it (`editor-persistence.spec.ts`
  is the example). `unmatchedGets` exposes every GET the mock had no modelled answer for;
  `/api/notifications` and `/clips/c1/download` currently fall through by accepted choice (DECISIONS).
- **`extra="forbid"` is scoped to the paid/render models, deliberately NOT repo-wide** —
  `PreferencesPatch` relies on absent-field tri-state semantics by documented design.
- **axe checks visible text for contrast regardless of `aria-hidden`** (WCAG 1.4.3 — low-vision
  sighted users). "Just aria-hide the low-contrast mock" is a disproven theory; carry disabled-ness
  with muted tokens at full opacity.
- **The `-m integration` deselection ledger is how you prove a CI-only test ran**: unit-lane
  deselected count went 173 → 175 exactly when the two integration-marked tests were added, and the
  integration job selected+passed them. Cheaper and stronger than grepping -q logs.
- **jsdom + fake timers + unmount flushes = cross-test time bombs.** A test that leaves a pending
  debounced save at unmount fires it during cleanup AFTER `afterEach` restored real `fetch`; the
  failed request arms a REAL 1s retry that fires a stray PUT into whichever test runs next. Drain
  the scheduler before a test ends (the undo/redo test is the worked example).
- **Playwright fixture functions REQUIRE the object-destructuring pattern** — for a
  dependency-less fixture, `async ({}, use)` plus a scoped `eslint-disable no-empty-pattern` with a
  why-comment is the correct form (the alternatives all break Playwright's dependency parsing).
- **The Review mascot's green/red digits are decorative floating binary, not counters** — they only
  read as data in a static screenshot. Left alone on purpose; don't "label" them.

---

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Repo | `github.com/reese8272/creatorclip` |
| Production | `https://autoclip.studio` — VM + docker-compose + Cloudflare tunnel (**not** Render) |
| Branches | `main`, `staging` — in sync. All feature branches merged and deleted |
| Last landed | **Issue 413** (Lexend swap, direct-to-main by user's call) · PRs: **#75** (412) · **#74** (407–411 + 387 proofs) · #73/#71 (391) · #72 (406) · #70 (Batch B pt 1) · #69 (Batch A) |
| Staging sync | after any merge to `main`: `git push origin origin/main:staging` (`docs/BRANCHING.md`) |
| Deploy chain | push to `main` → `docker-publish.yml` → `deploy.yml` (staging gate → prod) — **so the 413 push auto-deploys** |
| Baseline regen | `gh workflow run ci.yml -f update_snapshots=true --ref main` → `gh run download <id> -n visual-baselines-<sha> -D frontend/e2e/` — **REQUIRED for 413** (NEXT ACTION 1) |
| Node for local gates | `/home/reese/.nvm/versions/node/v22.17.1/bin` (**`frontend/.nvmrc` = 22**) |
| Python | `.venv/bin/python` (system `python3` lacks pydantic) |
| Redis for unit lane | `redis-server --daemonize yes --save '' --appendonly no` |
| Layer 0 | `.venv/bin/python .claude/skills/production-assessment/scripts/run_layer0.py` — **run from repo root** |
| Alembic head | **`0052_clip_edit_documents`** — unchanged this wave; next is `0053`, re-check `alembic heads` first |
| Active lane | **L25 Batch C** — 393–397, 401. A ✅ B ✅ · 407–412 ✅ · 413 ✅ (baseline-regen tail open) |
| Assessment screenshots | committed at repo root (`Editor.png`, `Review.png`, …) — Issue 412's evidence anchors |
| Secrets | `.env` on the VM; rotation runbook in `docs/RUNBOOKS.md`. **Names only — never values.** |

---

## CONSTRAINTS & GOTCHAS (carried forward + new)

**Process traps that actually bit a session**

- **Run frontend commands from `frontend/`** — including `npx playwright`: from the repo root it
  resolves a CACHED npx Playwright (`~/.npm/_npx/...`) and dies with `ERR_INTERNAL_ASSERTION` /
  "test() called here" errors that look like spec bugs. Bit this wave twice.
- **Chain gates with `&&`, never `;`** — a `; echo OK` swallowed an eslint failure and let a commit
  land red this wave (caught and amended within minutes, but the pattern is the lesson).
- **Integration seeders CAN be smoke-checked locally without Docker** — construct the ORM objects
  in a plain script (needs the fake-env prefix; `DATABASE_URL` must use `postgresql+psycopg://`,
  not asyncpg, which isn't installed).
- **`ruff format` reflows code between an Edit and the next Edit** — re-read before editing the same region.
- **Check `git ls-remote` / `git fetch --prune` before assuming trunk state.**
- **Backticks in `git commit -m` trigger command substitution** — heredoc.
- **CI runs `ruff format --check`** — run it before pushing.
- **Visual baselines come from `ubuntu-latest` only** (WSL2 anti-aliasing); a local WSL2 visual
  pass is suggestive, not proof — CI's visual job is the arbiter (it agreed, both PRs).

**Code invariants (see also FACTS above)**

- Six structural gates in `frontend/src/test/` (glyphs, native controls, native video, colour
  tokens, synthetic waveform, local cut storage) — source-tree AST scans; violations fail `npm test`.
- The honesty constraint is load-bearing and structurally tested; `HONESTY_STATEMENT` in
  `ToolStatusBar.tsx` is the single definition. User-visible copy is pinned by tests — don't "tidy".
- Radix Select throws on `value=""` — `__none__` sentinel in `components/ui/select.tsx` stays.
- Postgres GUC: always `NULLIF(current_setting('app.creator_id', true), '')::uuid`. Every tenant
  table now uses it (0052 repaired the last two).
- `min-h-0` at every level of a flex chain; `grid-rows-[minmax(0,1fr)]` is the grid analogue.
- React `onWheel` is passive — `addEventListener(…, {passive:false})` for zoom.
- `CardHeader` now renders description FULL-WIDTH below the title/aside row (412) — don't move it
  back beside a `shrink-0` aside.

---

## OPEN, LOGGED, NOT FIXED

Canonical list is `docs/OFF_COURSE_BUGS.md`. **Closed this wave:** `/settings` contrast (411),
the Review action-row stale row (388 flip), 387's integration + ffmpeg items. What remains, most
likely to matter first:

1. **Prod migrations run with NO pre-migration safety dump** — `BACKUP_R2_BUCKET` unset (Issue
   256). Matters for the first migration that drops or rewrites data; set it before writing `0053`
   if that migration touches existing rows.
2. **`clips/` is not creator-scoped** → `DELETE /auth/me`'s `clips/{creator_id}/` purge matches
   nothing — live right-to-erasure gap.
3. **Roving tabindex on the timeline** — named, accepted follow-up (1 + 2N tab stops).
4. **Review-queue badge counts rendered clips**, not shortlisted-unreviewed (needs a backend field).
5. **`App.tsx`'s `*` route silently redirects typos to `/dashboard`.**
6. **`ORIGINALITY_SIMILARITY_THRESHOLD=0.92` is UNVALIDATED.**
7. **The export collector omits ~10 tables** and nothing fails when one is forgotten (hand-written
   dict, no registry).
8. **The e2e mock's known unmodeled GETs**: `/api/notifications`, `/clips/c1/download` — visible in
   every run's teardown report now; model them when a spec first needs them.

**Operator punch-list** (no code closes these — `docs/GO_LIVE.md` is canonical): **#29** Google
OAuth verification · **#26/#28** friend beta · **#282** uptime monitor · **#255** off-box key
escrow · `MAILING_ADDRESS` deliberately unset (CAN-SPAM) — do not "fix" in code.

---

## POINTERS

| Doc | Purpose |
|---|---|
| `docs/issues.md` | Work queue — **A ✅ B ✅ · 407–413 ✅ (413 = Lexend swap; snapshot box open). Active: Batch C (393–397, 401)** |
| `docs/PROJECT_STATE.md` | Progress log — top entry is the 2026-08-04 close-out wave (PRs #74 + #75) |
| `docs/DECISIONS.md` | Top entries: the wave's four decisions + retroactive 386/388/400b back-fills |
| `docs/SOT.md` | Stack, schema, structure |
| `docs/COMPLIANCE.md` | ToS/retention — peaks (392) + clip-edit-documents (391) rows |
| `docs/UI.md` | Design system — mono = timecodes/IDs/code; CardHeader layout per 412 |
| `docs/OFF_COURSE_BUGS.md` | Logged-not-fixed defects |
| `docs/GO_LIVE.md` | Canonical launch scorecard (operator punch-list) |
| `docs/BRANCHING.md` · `docs/MIGRATIONS.md` · `docs/CLIPPING_PRINCIPLES.md` | Promotion model · migration templates · principles registry |
| `~/.claude/ISSUES_LOG.md` | Cross-project incident log |
| Memory dir | `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` |
