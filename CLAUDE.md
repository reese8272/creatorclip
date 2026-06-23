# CLAUDE.md — CREATORCLIP Project Rules

These rules govern every session and override default Claude Code behavior where noted.

---

## The One Rule Above All Others

On EVERY non-trivial decision — architecture, library, model, scoring math, security
boundary, UX pattern — we ALWAYS research the current industry standard and best
practice FIRST, and justify any deviation in `docs/DECISIONS.md`. We do not build
from memory. We do not guess. This is enforced in Phase 1 (CHECK) of every issue.

---

## North Star

> **"The only AI editor that truly knows your channel — it learns your style from your own
> analytics, adapts as you evolve, and keeps you ahead of the algorithm."**

Every feature decision is tested against this. Does it deepen the channel-knowledge loop?

---

## Honesty Constraint (must appear in every interface and the system prompt)

AutoClip predicts fit with your style and audience — it does not promise virality.
Every recommendation is an estimate grounded in your own data, not a guarantee. We
comply with the YouTube API Services Terms of Service at all times.

---

## Read Order (Every Session)

Before writing a single line of code, read these files in order:

1. `docs/SOT.md` — current stack, architecture, file structure
2. `docs/PROJECT_STATE.md` — which issues are done, in progress, or blocked
3. `docs/issues.md` — the issue being worked
4. `docs/DECISIONS.md` — any deviations from the PRD already made
5. `docs/COMPLIANCE.md` — YouTube ToS, data retention, privacy posture
6. `docs/CLIPPING_PRINCIPLES.md` — named principles the engine cites

If any are missing or stale, flag it before proceeding.

---

## Project Structure

Canonical layout is enforced. Do not create files outside it without updating
`docs/SOT.md` first.

- Python source at root or in: `routers/`, `youtube/`, `ingestion/`, `dna/`,
  `clip_engine/`, `preference/`, `knowledge/`, `upload_intel/`, `improvement/`, `worker/`
- Frontend assets in `static/`
- All documentation in `docs/`
- Tests in `tests/`, mirroring source structure

---

## Source of Truth Files

| File | Purpose | Updated when |
|------|---------|--------------|
| `docs/PRD.md` | Requirements | Rarely; only on formal scope change |
| `docs/SOT.md` | Architecture | Any time stack / schema / structure changes |
| `docs/DECISIONS.md` | Deviation log | Any decision diverging from PRD or industry standard |
| `docs/PROJECT_STATE.md` | Progress | Every time an issue is completed |
| `docs/issues.md` | Work queue | Check `[ ]` → `[x]` when an issue is done |
| `docs/COMPLIANCE.md` | YouTube ToS + data handling | Any time data classes / retention / scopes change |
| `docs/CLIPPING_PRINCIPLES.md` | Named principles registry | Any time a new principle is cited |
| `docs/OFF_COURSE_BUGS.md` | Incidental-defect log | Any time a bug is found outside the current task's scope |

---

## Off-Course Bugs (stay on-course without brushing things off)

When you discover a bug, fragility, or surprising behavior that is **outside the scope
of the task in flight**, do NOT silently fix it inline and do NOT abandon your current
task to chase it. Instead:

1. **Log it** in `docs/OFF_COURSE_BUGS.md` — one row: date, what you were doing, the
   bug, a severity guess, and where it's tracked. This takes seconds and guarantees it
   is not lost.
2. **Keep going** on the original task. Fix the off-course bug inline ONLY if it blocks
   that task (e.g. it makes the tests un-runnable); otherwise leave it logged.
3. **Triage later**: promote real defects from the log into `docs/issues.md` when
   scheduled, and remove entries that turn out to be non-issues.

This is the explicit guard against the two failure modes: brushing a real bug off, and
letting a side-quest derail the main pipeline.

---

## Issue Workflow — Check → Approve → Build → Review & Assess

One issue at a time. Do not begin Issue N+1 until Issue N clears all four phases.

### Phase 1 — CHECK
Research the industry-standard approach (not memory). Present a brief:

> **Issue N — [title]**
> **Approach:** [specific pattern]
> **Why for this project:** [1–2 sentences]
> **Industry standard checked:** [current best practice confirmed + source]
> **Alternatives ruled out:** [what we considered]
> **Good to go?**

### Phase 2 — APPROVE
Wait for explicit confirmation. Capture changed approaches in `docs/DECISIONS.md`.

### Phase 3 — BUILD
- Follow Coding Principles and Production Standards
- Write tests alongside code
- Run full test suite before Phase 4

### Phase 4 — REVIEW & ASSESS

**Automated gates (Layer 0 — must be green before close)**
- [ ] `python3 .claude/skills/production-assessment/scripts/run_layer0.py` passes
      (ruff, mypy, coverage floor, bandit, pip-audit — no regression vs baseline)
- [ ] If coverage dropped, tests added; if a gate baseline moved, justified here

**Resource lifecycle**
- [ ] DB sessions via context manager, guaranteed to close
- [ ] External clients (Anthropic, Voyage, YouTube, storage) module-level singletons
- [ ] Celery tasks idempotent + retry-safe; temp media cleaned up

**Path and config safety**
- [ ] All paths absolute
- [ ] All new config in `.env.example` with description
- [ ] Nothing belonging in `.gitignore` left unignored

**Code cleanliness**
- [ ] No TODO, commented blocks, debug statements
- [ ] No duplicated logic
- [ ] Every new function typed

**Security & compliance (load-bearing)**
- [ ] OAuth tokens read via `decrypt()`; never logged
- [ ] No PII or token in any log line
- [ ] Per-creator isolation enforced on every query
- [ ] YouTube ToS / retention respected; source media purge honored
- [ ] No virality promise anywhere (structural test green)

**Clip-quality correctness**
- [ ] Clip start at the setup, not the aftermath (eval green)
- [ ] Scores cite a named principle from `docs/CLIPPING_PRINCIPLES.md`
- [ ] Ranking reflects DNA + (above threshold) preference model
- [ ] Recency decay actually reweights feedback

**Docs**
- [ ] `docs/SOT.md` updated if stack/schema/structure changed
- [ ] `docs/DECISIONS.md` updated if implementation diverged
- [ ] `docs/CLIPPING_PRINCIPLES.md` / `docs/COMPLIANCE.md` updated as needed

**Close out**
- [ ] All acceptance criteria checked off
- [ ] `docs/PROJECT_STATE.md` updated

---

## Coding Principles

> Invoke `/best-practices` for deep guidance — EVERY non-trivial issue.

**DRY**: Extract any logic used more than once.

**SOLID**: Single Responsibility, Open/Closed, Liskov, Interface Segregation, Dependency
Inversion.

**KISS**: Simplest solution wins. No premature abstractions. >30-line function = probably
split.

**Industry Standard ALWAYS**:
- Research current best practice in Phase 1 of every issue — never build from memory
- FastAPI-idiomatic backend; Celery task patterns; OAuth 2.0 correctness
- Anthropic SDK best practices (prompt caching, web-search tool, structured output, token
  limits) — use `/claude-api` skill for every Anthropic SDK call
- pgvector usage patterns; recency-decayed reranking standards
- Any deviation requires a `docs/DECISIONS.md` entry

---

## Production Standards

- No hardcoded secrets. `.env` only, never committed.
- All config via `pydantic-settings`. Fail fast on missing required.
- `logging` module only — no `print()`.
- Proper HTTP status codes (200, 400, 401, 404, 422, 500).
- Pydantic on every endpoint.
- Error messages safe — no stack traces, no DB errors to client.
- `requirements.txt` pinned with `==`.
- Every log line and every LLM prompt reviewed for token/PII leakage.
- Per-creator isolation enforced on every query.
- No response ever promises virality.

---

## Testing Rules

- Full pytest run before every issue close
- Tests for new behavior written with the code
- 80/20: happy path + load-bearing edges (see global CLAUDE.md)
- `tests/` mirrors source structure
- No DB mocking — use real Postgres (+ pgvector) via docker-compose
- Never hit the live YouTube API in CI — use recorded fixtures
- API-surface end-to-end with FastAPI `TestClient`
- Clip-quality eval harness in `tests/eval/scenarios/*.yaml` — labeled videos +
  expected clip windows (setup-start assertion); runs before every `clip_engine/` change

---

## Architecture Constraints

- Backend: FastAPI + Python 3.12+
- Task queue: Celery + Redis (durable video jobs)
- LLM: Anthropic SDK with prompt caching mandatory; web-search tool for live research; tokens
  logged after every call. Use `/claude-api` skill when writing Anthropic SDK code.
- Embeddings: Voyage AI → pgvector
- DB: PostgreSQL 16 + pgvector + Alembic
- Transcription: WhisperX (word-level) with hosted fallback behind config
- Video: ffmpeg cut + 9:16 active-speaker reframe
- Storage: Cloudflare R2 (S3-compatible); local disk in dev
- Auth: Google OAuth 2.0 (YouTube scopes) + session JWT; tokens Fernet-encrypted
- Preference model: recency-decayed reranker (LightGBM/logistic), not a fine-tuned LLM
- Frontend: vanilla HTML/CSS/JS (review-UI framework is a flagged DECISIONS candidate)
- Containerization: Docker Compose (dev) / Kubernetes (production — **architecture chosen + Helm chart written** at `deploy/charts/creatorclip/`: GKE Autopilot + Cloud SQL PG16 + KEDA, locked in `docs/DECISIONS.md`. Not yet validated on a real cluster — that is **Issue 275**, the deploy-track linchpin.)

Deviations require a `docs/DECISIONS.md` entry before implementation.

---

## Clip-Engine Rules

- The engine clips the SETUP, not the aftermath — backward look from peak in a 60–90s window
- Every clip score cites a named principle from `docs/CLIPPING_PRINCIPLES.md`
- Scoring is against THIS creator's DNA + audience, never a generic virality score
- The preference model weights recent feedback more heavily (exponential recency decay)
- Personalization threshold is communicated honestly; below it, ranking falls back to DNA + signals
- No interface or response ever promises virality

---

## Production Deployment

Target: Kubernetes at 10k+ scale. Docker Compose = dev only.
The Helm chart exists (`deploy/charts/creatorclip/`) and the architecture is locked (GKE Autopilot +
Cloud SQL PG16 + KEDA). It has **never run on K8s** yet — "staging" is currently Docker-Compose on the
prod VM, which makes the scale/pool `[DEC]`s unfalsifiable. **Standing up a real GKE staging cluster +
first Helm deploy is Issue 275** (the linchpin that unblocks Issues 259/261/276–280 verification).
See `docs/issues.md` (Lane **L12_K8S_DEPLOY**) for the deploy roadmap and `docs/DEPLOYMENT.md` for the
chart + pre-deploy checklists.

Dev:
```bash
docker compose up --build
```

Status:
```bash
docker compose ps
docker compose logs --tail 100 app worker
```

---

## Pre-Public-Launch Requirements

- Lock `ALLOWED_ORIGINS` to production domain; disable `/docs`
- Per-creator rate limiting + usage quotas before each LLM/render job
- ✅ YouTube data-retention/refresh fully compliant — Wave-4 Fix 3 (Issue 75b): 30-day partial-staleness purge per ToS §III.E.4.b (`docs/COMPLIANCE.md`)
- `TOKEN_ENCRYPTION_KEY` rotation runbook written
- ✅ Terms of Service + Privacy Policy pages live AND linked — pages existed at `/static/tos.html` + `/static/privacy.html`; Wave-6 Fix B added a footer linking both from every static template (Google OAuth verification gate, Issue 29)
- Google OAuth app verification completed
- ✅ Account deletion (right-to-erasure: token revocation + media purge) — endpoint `DELETE /auth/me` plus the Profile "Danger zone" UI affordance (Issue 158)
- Billing + plan-tier wired (usage-based tiers — pricing research pending)
- Eval harness hardened with adversarial/edge cases

---

## Code Style

- Python: PEP 8, max 100 chars, type hints on every signature
- HTML/JS: vanilla, 2-space indentation
- SQL: uppercase keywords, lowercase identifiers, parameterized queries always
- Comments only when WHY is non-obvious
- Naming: `snake_case` Python, `camelCase` JS, `UPPER_SNAKE` constants
