# AUDIT_BRIEF — orientation for an external code review

**Audience:** an experienced engineer doing a code-quality second look.
**Time to productive:** ~45 minutes if you read this first.
**Written:** 2026-08-15.

> **Read this file, then do a cold pass.** There is a second file,
> `docs/AUDIT_KNOWN_ISSUES.md`, listing everything we already suspect. **Please don't open
> it until you've formed your own view.** We specifically want the things our priors are
> blinding us to, and that only works once. After your first pass, read it — it'll stop you
> writing up items that are already filed, and the overlap tells us how good our own
> instincts are.

---

## 1. What this is, in one paragraph

AutoClip turns a creator's long-form YouTube videos into ranked, render-ready 9:16 short
clips. The differentiator is that it scores clips against **that channel's own analytics**
— a versioned "Creator DNA" profile built from retention curves, demographics and top/bottom
performers — rather than a generic virality model. It's live at `autoclip.studio`, deployed
from `main` on a single DigitalOcean VM behind a Cloudflare tunnel. Current stage is a
**locked ≤100-user private beta** (`docs/DECISIONS.md`, 2026-06-26); the 10k-scale
Kubernetes track is explicitly descoped and its Helm chart has never run on a cluster.

**Naming, so it doesn't confuse you:** the repo and Python package are `creatorclip`; the
product and domain are `AutoClip`. Same thing. A rename is in flight and incomplete.

### Scale

| | |
|---|---|
| Python | ~134,400 lines / 483 files |
| TypeScript/React | ~35,300 lines / 271 files |
| Backend tests | 260 test modules; **3,164 passing / 0 failing** on this tree (2026-08-15) |
| Routers / endpoints | 24 routers, ~120 endpoints |
| Celery tasks | 41 |
| Alembic migrations | 62 (head `0062`) |
| Docs | 5.1 MB — `DECISIONS.md` alone is 13,018 lines |

Solo-authored, heavily AI-assisted, high velocity. That combination produces a specific
risk profile, covered in §5 and §6.

### One hard product constraint

**Nothing in this product may promise or imply virality.** It predicts *fit with the
creator's own style and audience*. This is enforced by structural tests, not just style
guidance — if you propose copy or an API field that implies guaranteed reach, it will fail
CI, and that's intended. Same for YouTube API ToS compliance (`docs/COMPLIANCE.md`).

---

## 2. Getting it running — read this before you run anything

**⚠️ Use the project virtualenv. Not system Python.** This has burned two sessions badly
enough that it's the first gotcha in every handoff doc.

```bash
.venv/bin/python -m pytest -q          # backend, default unit lane
scripts/ci_local.sh --fast             # what the pre-push hook runs (prefixes PATH with .venv/bin)
```

System `python3.12` on this box carries **fastapi 0.115.4** against the pinned **0.137.1**,
and its `mypy` can't import the `pydantic` plugin. The consequences are worse than a crash,
because they're *quiet*: mypy aborts before checking anything and the Layer-0 gate counts
zero error lines and reports **`ok 0`** — a vacuous pass. `pip-audit` reports ~77 phantom
CVEs, and the suite shows phantom failures. A previous session filed real bug reports off
that bad data and had to retract them.

### Test lanes

`pytest.ini` sets an **exclusionary** default: `-m "not integration and not quarantine and
not llm_live and not render_env and not transcription_live"`.

| Lane | How to run | Needs |
|---|---|---|
| unit (default) | `pytest -q` | Redis (hard requirement — the limiter has no in-memory fallback, by design) |
| integration | `pytest -m integration` | real Postgres 16 + pgvector; **runs on every PR in CI** |
| render_env | `pytest -m render_env` | real ffmpeg + mediapipe |
| llm_live / transcription_live | nightly cron | real Anthropic / Deepgram keys |

Frontend: `cd frontend && npm ci && npm test`. Node 22.17.1 (`.nvmrc`) — **node 26 breaks
jsdom**.

Everything you need is in the repo. **You do not need production access, and please don't
ask for it** — no credentials are required to review this.

---

## 3. Architecture in one page

**Stack:** FastAPI + Python 3.12 · Celery + Redis · PostgreSQL 16 + pgvector · Alembic ·
Anthropic SDK (Sonnet 4.6 reasoning / Haiku 4.5 classify — no Opus) · Voyage AI embeddings ·
Deepgram nova-3 transcription · ffmpeg render · Cloudflare R2 storage · React 19 + Vite +
Tailwind v4 SPA served under `/app/*`.

**The pipeline** (Celery, `worker/tasks.py`):

```
ingest_video → transcribe_video → analyze_video_context → build_signals
   → generate_clips → generate_clip_metadata → render_clip → publish_to_youtube
```

**Module map:**

| Path | Lines | What |
|---|---|---|
| `worker/` | 8,692 | Celery tasks + beat sweeps. **`tasks.py` alone is 7,179 lines.** |
| `routers/` | 10,733 | HTTP surface. `clips.py` 2,893 · `videos.py` 1,479 · `insights.py` 1,080 |
| `clip_engine/` | 8,365 | candidate detection, windowing, scoring, ranking, ffmpeg render, reframe, captions |
| `knowledge/` | 3,021 | LLM features: titles, hooks, thumbnails, chapters, explanations |
| `youtube/` | 2,084 | OAuth, Data API, Analytics API, publish, quota |
| `preference/` | 1,527 | recency-decayed reranker (LightGBM/logistic — deliberately not a fine-tuned LLM) |
| `dna/` | 1,520 | the versioned per-creator style profile |
| `billing/` | 1,269 | Stripe checkout, minute-pack ledger, spend guard, refunds |
| `chat/` | 1,193 | agentic assistant scoped to the creator's own data |
| `ingestion/` | 1,002 | audio extraction, transcription, peak/signal detection |

**The core domain rule**, if you read nothing else about the product: the engine clips
**the setup, not the aftermath**. On detecting a high-signal moment (laughter, retention
spike, volume jump) it looks *backward* 75 s for the setup, so the viewer lands in context.
`clip_engine/candidates.py:22`, `WINDOW_S = 75.0`. (`CLAUDE.md` cites this as
`clip_engine/window.py` — that file exists but is the signal-array builder and does not
define the constant. First of several doc-drift items; see `AUDIT_KNOWN_ISSUES.md` §H.)
Every clip score must cite a named principle
from `docs/CLIPPING_PRINCIPLES.md`. This is guarded by 32 YAML geometry scenarios in
`tests/eval/scenarios/` with a ratcheting floor and a 100%-pass-rate gate.

**Docs worth knowing:** `docs/SOT.md` (architecture) · `docs/PIPELINE.md` (flow) ·
`docs/GO_LIVE.md` (**the canonical launch scorecard — when docs disagree, this one wins**) ·
`docs/DECISIONS.md` (every deviation and why) · `LEFT_OFF.md` (session handoff; explicitly
*not* a source of truth).

---

## 4. How this repo stores its debt — the thing that will mislead you

**There is essentially one `TODO` comment in the entire codebase.** Do not read that as a
clean tree. It's policy (`CLAUDE.md`): defects are never left as inline comments. They go to

- **`docs/issues.md`** — 113 issue briefs, ~34 with unchecked acceptance criteria
- **`docs/OFF_COURSE_BUGS.md`** — 136 rows, ~51 still open, severity-rated

A grep-based debt hunt will find nothing and tell you the wrong story. If you want the real
backlog, those two files *are* it.

The flip side: **a lot of what looks like a defect here was decided on purpose**, and the
reasoning is written down. Before filing something structural, it's worth a quick
`grep -i "<topic>" docs/DECISIONS.md`. Not to discourage you from disagreeing — several of
those decisions deserve challenge, and §6 asks for exactly that — just so your writeup can
engage with the stated reasoning rather than rediscover it.

---

## 5. Conventions that look like smells but are deliberate

Each of these is documented. **You are welcome to argue any of them** — several are load
-bearing bets we'd like a second opinion on — but please argue them as decisions, not
oversights.

| Looks like | Actually |
|---|---|
| Rate limiter and LLM spend guard **fail open** on Redis errors | Deliberate (`limiter.py:18-49`, `billing/spend_guard.py:104-119`). A Redis outage removes rate limits *and* cost caps simultaneously. We know. See question 5 in §8. |
| Session JWTs are **non-revocable** | Accepted 60-minute exposure window (`auth.py:67-90`). Logout only clears the cookie. |
| 53 tests marked `@pytest.mark.skip` | Dead-page residue from Issue 226 (the legacy `static/*.html` app was retired for the React SPA). Not hidden failures — but they *should* be deleted rather than skipped, and that's a fair finding. |
| Stripe uses `RequestsClient`, not `HTTPXClient` | `HTTPXClient(timeout=…)` defaults `allow_sync_methods=False` and caused a **10-week total checkout outage**. Do not revert. `billing/stripe_client.py:40-54`. |
| Enormous "incident archaeology" comments | Intentional. They record real production incidents and are usually the best documentation in the file. |
| `docs/` is larger than the source | Also intentional. This is a single-maintainer project; the docs are the continuity mechanism. |

---

## 6. The one failure mode we keep hitting — please hunt for the next one

This is the highest-value thing you can do with your time.

Three times, a subsystem was **completely dead for weeks** while every signal we had said
green. A fourth instance was found while preparing this brief:

| | What was broken | How long | The green signal that hid it |
|---|---|---|---|
| 1 | YouTube catalog sync imported **nothing, for any creator** | **7 weeks** | HTTP 200 + `"Synced N video(s)"` logged over a dead path. The API `fields=` spec omitted `kind`; the parser filtered on `kind`; every item was silently dropped. |
| 2 | Stripe checkout raised on **every** call | **10 weeks** | Tests green. `doctor.py --full` reported "stripe auth ok" because it probed Stripe with a raw `httpx.get`, never the app's own client. |
| 3 | Per-module coverage floors + diff-cover **never ran in CI** | **~7 weeks** | `_coverage.xml` was deleted between two invocations, so both gates returned `"skipped"` with exit 0 while printing *"All runnable gates passed"*. |
| 4 | **Bandit has never scanned 8,277 lines**, including `crypto.py`, `auth.py`, `main.py`, `config.py`. mypy has never checked 4,093 of them. | since inception | `bandit high 0 / medium 0`, permanently green over files it never opened. Details and repro in `docs/AUDIT_KNOWN_ISSUES.md`. |

The pattern is always the same shape: **an intermediate layer reports success without
exercising the thing it claims to verify.** A passing test, a success log line, a green
pipeline, and a clean gate are each, on their own, compatible with a totally dead feature.

Two more instances of the same family are already logged in `docs/OFF_COURSE_BUGS.md`
(a staging drill that passed vacuously when its counter was already exhausted; the
`doctor.py` probe above), which suggests we are finding these one at a time by accident
rather than by looking.

**Nobody has ever swept for this class systematically.** If you find instance #5, that is
worth more to us than any number of style findings.

---

## 7. Where we're least certain

Offered as *uncertainty*, not suspicion — we're deliberately not telling you what we think
is wrong until after your pass.

1. **`worker/tasks.py`, 7,179 lines.** The single biggest maintenance object in the tree.
2. **The erasure / GDPR path** (`routers/auth.py`, `worker/erasure.py`). Right-to-erasure
   spans Postgres, R2 media, YouTube token revocation and telemetry.
3. **LLM cost accounting** (`billing/ledger.py`, `worker/anthropic_stream.py`). Every call
   site must bill; several have historically not.
4. **Tenant isolation.** Three layers: Postgres RLS, an explicit `creator_id` predicate
   (`routers/_owned.py`), and app-level checks. Whether all three are actually *active* in
   the deployed configuration is worth your scepticism.
5. **`clip_engine/` scoring and ranking correctness** — the actual product quality surface.
6. **Frontend/backend contract drift** — 271 TS files against 120 endpoints.

---

## 8. What we'll ask you at the end

Flagged up front so you can take notes as you go.

1. **Where would you not trust the test suite?** 3,164 passing tests and an 83% coverage
   floor have three times failed to notice a fully dead feature. Which green signals here
   are hollow?
2. **If you had to bet on where the next multi-week silent outage is hiding, where?**
3. **What would you delete?** 134k lines of Python for a ≤100-user beta is a lot.
4. **Is `worker/tasks.py` at 7,179 lines actually a problem?** We want a real answer, not
   "it's too big" — either a concrete split with named seams, or an explicit "leave it, it's
   41 largely independent task functions and splitting buys nothing."
5. **What's the blast radius of the fail-open decisions?** (§5, row 1.) At 100 users, still
   the right call?
6. **Rank the modules by where you'd expect the next bug.** We'll compare against our own
   ranking — the disagreements are the interesting part.
7. **What did the docs tell you that turned out not to be true?** Doc drift is a known
   hazard here and you're the only one who can see it cold.

### Findings format

Severity-ranked, each with a `file:line` and a **concrete failure scenario** (inputs/state →
wrong behaviour). That lets us triage straight into `docs/issues.md`. If something is a
judgement call rather than a defect, please say so — we'd rather have a short list of things
you're confident about than a long list padded with maybes.

---

## 9. What not to spend time on

- **Formatting and style.** ruff + ruff-format are gating at zero; it's settled.
- **The Kubernetes / Helm track** (`deploy/charts/`). Descoped for v1, never deployed, still
  full of placeholder values. Known.
- **The unfunded editor backlog** (`docs/issues.md` Lane L25 Batches C/D/E — B-roll,
  multi-track timeline, transitions). Filed, deliberately not built.
- **The `docs/` prose itself**, except where it contradicts the code — that part we want.
- **Rewriting the SPA.** The React migration is recent and settled.

---

## 10. Next

Do your cold pass. Then open **`docs/AUDIT_KNOWN_ISSUES.md`** for everything we already
know about, and treat the delta as your real output.

Thanks — genuinely. The most useful thing here is an outside eye on a codebase whose author
has read every line too many times to see it.
