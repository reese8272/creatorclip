# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-08-12 · **Branch:** `docs/close-out-455` @ `41012fc` · working tree has
**4 uncommitted audit-deliverable files** (see NEXT ACTION 1) · branch is **1 ahead of
`origin/main`** (the open docs PR; its CI passed 2026-08-12 21:43).
**Prod:** `https://autoclip.studio`, alembic **`0058 (head)`** — no migrations this session.
The previously-in-flight `c138e93` deploy **landed** (Deploy to production run: success, 21:45 UTC).

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source
> of truth.

---

## CURRENT FOCUS

**A full clipping-integrity audit was completed and filed as Lane L29 (Issues 456–482) — the next
session's job is to BUILD those fixes, starting with the five SEV1s.** The audit was
assessment-only: zero production code changed; every filed finding survived adversarial
verification plus hand adjudication (repros rerun) of all SEV1s.

**Read these two files before writing any code:**
1. `docs/assessment/CLIPPING_INTEGRITY_2026-08-12.md` — verdicts, evidence, the 88-row
   disposition table (what was confirmed / refuted / already-tracked / deliberately-accepted).
2. `docs/issues.md` **Lane L29** — 27 self-contained issue bodies with evidence, root cause,
   fix direction, and acceptance boxes. Repro details are embedded in the issue bodies
   (the audit session's scratchpad scripts are ephemeral and already gone).

## → NEXT ACTION

1. **Commit + push the audit deliverables on THIS branch** (they belong in the open docs PR):
   `docs/assessment/CLIPPING_INTEGRITY_2026-08-12.md` (new), `docs/issues.md` (Lane L29 +
   next-free→483), `docs/assessment/modules/clip_engine.md` + `preference.md` (cross-ref lines).
   Ship via the PR — **never direct to `main`** (ci.yml has no push trigger; zero gates run).
2. **Start Lane L29.** Recommended order (each issue still gets its Phase-1 CHECK per CLAUDE.md):
   - **467** (Punch-in emits invalid ffmpeg — feature has never worked) and **472** (feedback
     `skip` after "Save trim" erases the training label) — small, surgical, highest
     user-harm-to-effort ratio.
   - **479** (CI per-module coverage floors + diff-cover have NEVER run — `run_layer0.py:527`
     deletes `_coverage.xml` between ci.yml's two invocations) — one-line-ish fix, restores two
     supposedly-active gates for every subsequent PR. Do it early so the rest of the lane is
     actually gated.
   - **456** (backward snap can ship the peak outside the clip) and **466** (overlay-band sampler
     broken on >~500 s sources) — the two big engine SEV1s; need real CHECK phases.
   - **476** (LLM scorer evaluated nowhere) — largest design surface; research eval patterns first.
3. **Issue 448's live drill window expires 2026-08-13 19:23 UTC** (source purge). Given Issue 466
   (the sampler the drill depends on is broken on this 1617 s source), letting it lapse is now the
   defensible default — the owner already deferred it once. Decide, don't drift.
4. **Billing live-proof is still owed** (carried from the previous handoff): buy the smallest pack
   on prod for real; minutes must credit; `docs/GO_LIVE.md` billing gate stays **RED** until then.
   Expect Issue 454 (tab-scoped intent id) on a second Buy click — filed, not built.

### Local gate incantations

```bash
export PATH=/home/reese/.nvm/versions/node/v22.17.1/bin:$PATH && hash -r   # node 26 = 35 phantom jsdom fails
redis-cli ping || redis-server --daemonize yes --save '' --appendonly no
# backend:  .venv/bin/python -m pytest -m "not integration" -p no:langsmith -q
#           audit baseline at 41012fc: 2975 passed / 0 failed / 64 skipped (60.6 s)
# frontend: run from frontend/ — npx vitest run; AND `npm run build` (tsc -b type-checks TESTS;
#           `npx tsc --noEmit` does NOT and has lied before)
# Layer 0:  PATH="$PWD/.venv/bin:$PATH" .venv/bin/python .claude/skills/production-assessment/scripts/run_layer0.py
# eval:     any clip_engine/ change → tests/test_clip_engine.py (SCENARIO_FLOOR=23, 24+1 fixtures)
```

---

## WHAT WORKS NOW (verified — do not re-investigate)

**The audit itself (2026-08-12, commit `41012fc`):**
- Baseline measured before any finding: backend **2975/0**, eval **61 passed** (24 scenarios,
  100 %), Layer 0 green (clip_engine 93.03 vs floor 91.0, preference 90.24 vs 88.0).
- **Verdicts:** selection core / mechanical pipeline / learning loop = **SOUND-WITH-CAVEATS**;
  **eval/test integrity = COMPROMISED** (the green dashboard overstates what is proven).
- All five SEV1s were re-adjudicated by hand — repros rerun (456, 466, 467) or full code trace
  re-read (472, 476). The essential repro inputs are embedded in each issue body.
- **Do NOT re-derive these — they were REFUTED with evidence** (disposition table): the
  sentence-snap "clamp-order wrongful drop" (C1-10 — unreachable defensive code) and the
  "float-noise setup_start_s" (C1-11 — a 0.5-grid peak −0.1 is already 2 dp-exact). Ten more
  items carry material corrections (e.g. diarization does NOT collapse on the no-utterance
  fallback; PIPELINE.md line drift is 65–855 lines, not 1500+).
- **Six items are DECISIONS-accepted — do not re-file or "fix" them:** word-level snap tripwire
  (2026-08-05 r.1), NMS-before-snap ordering (2026-08-07), containment-threshold inertness
  (2026-08-07 D.1), skip-reason taxonomy (Issue 217), mutmut 3-module scope (Issue 273),
  style-notes third-block placement (Issue 371).

**From previous sessions, still true:** Issues 451/452/453/455 shipped and deployed (billing
transport = `stripe.RequestsClient`; **never** revert to `HTTPXClient` — two stacked defects,
documented in `billing/stripe_client.py` and DECISIONS 2026-08-12). Issues 26, 445, 448 (code
inert, flag off), 450 shipped; prod alembic `0058`.

---

## THE ARC THAT LED HERE

1. Post-#84 handoff: billing outage fixed in code, live purchase proof still owed.
2. Owner asked for a full integrity assessment of the auto-clipping capabilities — all four
   dimensions (selection core, mechanical pipeline, learning loop, eval integrity), multi-agent.
3. Audit ran: 3 exploration agents → deterministic baseline → 12-agent workflow (ledger, 4
   confirmers, 4 hunters, 3 adversarial verifiers) → hand adjudication of every SEV1.
4. 87 verdicts distilled: 72 confirmed-new → 27 issues filed (5 SEV1 / 21 SEV2 / 1 roll-up) as
   Lane L29; 6 already tracked; 6 deliberate; 2 refuted. Report + lane written; docs-only diff.
5. One full-suite re-run failure post-filing was root-caused to the kernel OOM-killing ffmpeg
   (memory-starved WSL2 box, other sessions) — **not a regression**; logged as
   ISSUE-2026-08-12-01 in `~/.claude/ISSUES_LOG.md`.

---

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Repo | `github.com/reese8272/creatorclip` · prod = VM (`ssh creatorclip-vm`, standing permission), `/opt/autoclip`, docker-compose, Cloudflare tunnel |
| Prod containers | `autoclip-app-1`, `autoclip-worker-1`, `autoclip-beat-1`, `autoclip-render-worker-1` (no compose file in `/opt/autoclip` — use `docker exec`/`docker logs` directly) |
| Deploy chain | push to `main` → Docker publish → Deploy to production. **A failed image build silently SKIPS deploy** — `gh run list` after every push |
| Audit report | `docs/assessment/CLIPPING_INTEGRITY_2026-08-12.md` (audited commit `41012fc`) |
| Lane L29 | `docs/issues.md` — Issues **456–482**; batches: A geometry 456-460 · B scoring 461-465 · C pipeline 466-471 · D learning loop 472-475 · E eval/CI 476-482 |
| Next free issue number | **483** |
| Audited video | `7e988321-2265-4e22-85bd-0e9ffd583f84` — **source purges 2026-08-13 19:23 UTC** |
| Creator | Backboard Media `eb9af967-5d2f-4063-a05e-9f4f070ce840` |
| Live flags (VM `.env`) | `ACTIVE_SPEAKER_REFRAME_ENABLED=true` · `CAMERA_REGION_DETECT_ENABLED=true` · `REFRAME_MIN_MAPPING_CONFIDENCE=0.2` · **`OVERLAY_BAND_DETECT_ENABLED` absent → false** |
| Frozen fixtures | `tests/fixtures/superchat/` (36 frames) + `tests/fixtures/reframe_seats/` (12 — **used by ZERO tests**; wiring them is part of Issue 478). Only reproductions once the source purges |
| Secrets | `.env` on the VM — names only, never values |

---

## CONSTRAINTS & GOTCHAS

- **Ship via a PR, never a direct commit to `main`** — `ci.yml` has no `push` trigger; a direct
  commit runs zero gating jobs.
- **Two CI gates you think are protecting you are NOT (until Issue 479 lands):** per-module
  coverage floors and diff-cover post "skipped" → exit 0 in CI. The global 83 % floor IS enforced.
- **The eval harness passing proves less than it looks** (until Issue 477): two scenarios assert
  nothing; the runner ignores unknown expectation keys; setup-before-peak is opt-in.
- **ffmpeg tests on this box can be OOM-killed** when other sessions eat the 7.6 GiB WSL2 VM: the
  failure reads as a generic "ffmpeg failed" with a truncated stderr and **returncode −9**. Check
  `free -h` / `dmesg | grep -i oom` BEFORE debugging (ISSUE-2026-08-12-01 in the global log).
- **A green test suite proved nothing about billing** — every billing test mocks the transport.
  Only a real settled purchase flips `docs/GO_LIVE.md`. `scripts/doctor.py --full` green-lights
  Stripe without exercising our client (logged OFF_COURSE 2026-08-12).
- **RLS blindness on prod:** queries with `app.creator_id` unset return zero rows, no error.
  `creators` is the exempt bootstrap; `SELECT set_config('app.creator_id', <uuid>, false)` first.
- **A celery-direct re-render CANNOT re-render** (worker skips clips with `render_uri`);
  `POST /clips/{id}/render` owns the reset, surfaced in the UI since Issue 451.
- **Never detect overlay bands on a RENDERED clip** — burned captions are themselves a band.
  Detect on the source. (And per Issue 466, the source-side sampler itself is broken > ~500 s.)
- **`mapping.confidence` is a margin ratio, not correctness** — do not gate on it (450).
- **`onAdvance` in `YourCall` serves BOTH verdicts and plain skip** — and per **Issue 472** the
  Skip button also writes a training-retracting feedback row; don't "clean up" that area without
  reading 472 first.
- **Do not "restore" things DECISIONS deliberately removed:** EMA smoothing (436), camera-region
  height ceiling (439), speaker-following on `face_pan` (440), coordinators/pronouns in the
  weak-opener list (441), consensus-median re-validation (443), a `triage=` filter on
  `GET /videos/{id}/clips` (444), the shortlist as a FILTER (445), `stripe.HTTPXClient` (455) —
  **plus the six audit-classified deliberate items listed in WHAT WORKS NOW.**
- **Migrations:** data-manipulating migrations need an `if context.is_offline_mode():` branch
  (CI renders with `alembic upgrade --sql`, no connection).
- `docs/assessment/modules/clip_engine.md` carries stale line refs / floor values — its header now
  says so; trust the 2026-08-12 report for current facts.
- Owner sometimes powers the droplet off intentionally — check before treating prod-down as an
  incident.

---

## OPEN, LOGGED, NOT FIXED

Canonical list: `docs/issues.md` + `docs/OFF_COURSE_BUGS.md`. Top of the stack:

- **Lane L29 (456–482)** — the audit lane; nothing built yet. Five SEV1s: 456, 466, 467, 472, 476.
- **Issue 454** — tab-scoped checkout intent id; will surface the moment billing works.
- **Issue 449** — snap_start pause exemption bypasses the weak-opener guard (related to, but
  distinct from, 456 — read both before touching `sentence_snap.py`).
- **Issues 442, 446, 447** — background style accepted-never-applied; render erasure gap
  (Issue 471 extends it); Keep-pile finish line.
- Owed live drills: 448 re-render (expiring, see NEXT ACTION 3), 444 idempotency, 437
  failure-path, 427 frame check, 424–426 Playwright baselines.

---

## POINTERS

| Doc | Purpose |
|---|---|
| `docs/assessment/CLIPPING_INTEGRITY_2026-08-12.md` | **The audit** — verdicts, evidence, disposition table |
| `docs/issues.md` | Work queue — Lane L29 is the active front; next free number **483** |
| `docs/GO_LIVE.md` | Go/no-go scorecard — billing still RED pending a real purchase |
| `docs/DECISIONS.md` | Deviation log — check before "fixing" anything the audit classified deliberate |
| `docs/PROJECT_STATE.md` | Issue close-outs |
| `docs/SOT.md` | Architecture |
| `docs/OFF_COURSE_BUGS.md` | Incidental-defect log |
| `~/.claude/ISSUES_LOG.md` | Cross-project solved-problem log (grep FIRST on weird failures) |
| Memory dir | `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` — see `project_clipping_integrity_audit.md` |
