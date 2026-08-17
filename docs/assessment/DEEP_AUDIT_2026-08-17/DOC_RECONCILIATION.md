# Doc Reconciliation + Audit Self-Audit

**Date:** 2026-08-17 · **Scope:** (1) docs vs. code, (2) the audit's own citations
**Method:** every claim below was re-verified against the tree at HEAD by this agent. Read-only;
`.venv/bin/python` only. Nothing was trusted from the prior phases without re-checking.

---

# JOB 1 — Docs vs. code

## 1.1 Verdict on `architecture-map.md` B4 (the 9 claimed SOT divergences)

I re-verified all nine independently. **All nine stand.** Three need correction — B4 *understated*
two of them and mis-scoped one. Details in the table; the corrections are called out inline.

| B4 # | Verdict | Correction needed |
|---|---|---|
| 1 — "no Opus" | **CONFIRMED** | none |
| 2 — transcription table self-contradiction | **CONFIRMED, understated** | It is a *three*-way contradiction inside SOT (lines 18, 52, 640), and `.env.example:96` sides with the wrong one — see D3, which is a live defect, not a doc bug |
| 3 — Render-vs-VM trail | **CONFIRMED** | `render.yaml` is **inert**, not armed (phase-1 refutation upheld — see note under D5) |
| 4 — K8s-shaped config in a non-K8s deploy | **CONFIRMED** | PgBouncer *is* present in `docker-compose.staging.yml`; it is absent only from prod (`grep -c pgbouncer docker-compose.prod.yml` → 0) |
| 5 — RLS understated in SOT | **CONFIRMED** | none |
| 6 — `routers/` list incomplete | **CONFIRMED, understated** | B4 lists 6 omissions. There are **8**. It missed `billing.py` and `insights.py` — i.e. the entire payments surface is absent from the architecture doc |
| 7 — `tests/` block ~7% complete | **CONFIRMED** | Precisely: 20 test files listed vs **260** on disk |
| 8 — data-model tables missing | **CONFIRMED, understated** | B4 names 5 missing tables. There are **6**. It missed `minute_packs` — the table a creator's purchase writes to |
| 9 — pipeline diagram stale | **CONFIRMED** | none |

## 1.2 The reconciliation table

Severity is judged against the actual system: **≤100-user private beta, one VM.** The test applied
is — does it cost money, lose creator data, mislead a creator, or cause a future session to
*undo correct work*? The last one is the dominant harm class here.

| Doc | Line | Claim | Reality | Sev | Exact replacement text |
|---|---|---|---|---|---|
| **D1** `docs/SOT.md` | 16 | "…**no Opus** — see docs/DECISIONS.md (Issue 221)." | `config.py:117,141,145` set `ANTHROPIC_MODEL_SCORING`, `ANTHROPIC_MODEL_VIDEO_CONTEXT`, `ANTHROPIC_MODEL_CLIP_METADATA` = `claude-opus-5`. Reversed by owner directive at `docs/DECISIONS.md:962-964` (2026-08-05): "$5/$25 per MTok, ≈2× Sonnet — ~$0.12/video total". SOT never updated. | **High** | `…**`claude-opus-5`** for the three clip-quality calls — scoring, video-context, clip-metadata (owner directive, DECISIONS 2026-08-05 §6; ≈2× Sonnet, ~$0.12/video, env-overridable). This supersedes the Issue-221 "no Opus" position.` |
| **D2** `docs/SOT.md` | 52 | env table: `` `TRANSCRIPTION_BACKEND` \| No \| `whisperx` (default) `` | `config.py:228` → `TRANSCRIPTION_BACKEND: str = "deepgram"`. SOT contradicts itself **three ways**: line 18 says Deepgram is default, line 52 says whisperx, line 640 (pipeline diagram) says "WhisperX word-level". Line 126 also says "WhisperX or hosted". | **Medium** | line 52 → `` `deepgram` (default) \| `whisperx` \| `assemblyai` ``; line 126 → `# Deepgram nova-3 default; WhisperX/AssemblyAI selectable`; line 640 → `Deepgram nova-3 word-level (WhisperX/AssemblyAI selectable)` |
| **D3** `.env.example` | 96 | `TRANSCRIPTION_BACKEND=whisperx` | **Not a doc bug — a live defect.** Copying `.env.example` to `.env` selects whisperx. `whisperx` is **not in `requirements.txt`** (only a commented `pip install git+…` note at `requirements.txt:161-162`), so `ingestion/transcribe.py:91` → `_transcribe_whisperx` → `import whisperx` (`:368`) → `ModuleNotFoundError` on **every ingestion job** on a fresh checkout. Verified by me. | **Medium** | `TRANSCRIPTION_BACKEND=deepgram           # deepgram (default) \| whisperx (self-host, requires the git+ install at requirements.txt:161) \| assemblyai` |
| **D4** `docs/DEPLOYMENT.md` | 162-163 | "`/health` … degrades to non-`ok` + **503** when a backing service is down" | `main.py:553-567` returns a bare `dict`. FastAPI serialises it **200** unconditionally; only the body changes to `"status":"degraded"`. Verified by reading the handler — there is no `Response`, no `status_code`, no `HTTPException`. **This is load-bearing**: the same section (`:155-159`) instructs configuring the Cloudflare monitor with **Expected codes `200`** plus a body match on `"status":"ok"` — so the runbook is internally inconsistent, and an operator who configures only the status-code check gets a green monitor through a total Redis/Postgres/R2 outage. | **High** | `endpoint returns `{"status":"ok","postgres":"ok","redis":"ok","storage":"ok","version":…}` and **always answers HTTP 200** — a backing-service failure changes only `status` to `"degraded"`. **The response-body match on `"status":"ok"` is therefore the only thing that detects an outage; a status-code-only monitor will never fire.** (See Issue 24 / GO_LIVE.)` |
| **D5** `docs/DECISIONS.md` | 2541, 2563 | Two 2026-06-24 entries establish **Render** as the beta host. | Reversed 3 days later at `:2395` (2026-06-27, Issue 326): *"the live app does **not** run on Render — it runs on…"*. Neither superseded entry is marked. `render.yaml` (219 lines) still ships at repo root. **Do not read this as "render.yaml is armed"** — the phase-1 `autoDeployTrigger` finding was **REFUTED** and stays refuted: a Blueprint is inert unless linked in the Render dashboard, `autoDeployTrigger: commit` is Render's documented default, and retention was deliberately decided at `:2395`. The defect is *ambiguity*, not exposure. | **High** | Prepend to both `:2541` and `:2563`: `> **STATUS: SUPERSEDED 2026-06-27 by the Issue-326 entry below (§2395). CreatorClip's beta runs on the DigitalOcean VM (docker-compose.prod.yml + Cloudflare Tunnel). Render was never used — the Render Postgres is empty and unmigrated. `render.yaml` is retained as a documented fallback blueprint and is inert unless a Blueprint is linked in the Render dashboard.**` |
| **D6** `CLAUDE.md` | 236 | "a fixed 75 s backward look from the peak (`WINDOW_S = 75.0`, **clip_engine/window.py**)" | `WINDOW_S = 75.0` is at **`clip_engine/candidates.py:22`**. `clip_engine/window.py` exists but is 72 lines of signal-array construction (`build_signal_array`, `RESOLUTION_S`) and contains no `WINDOW_S`. **The same wrong pointer is in `docs/SOT.md:140`** (`window.py  # Fixed 75 s backward context window (WINDOW_S = 75.0)`) — CLAUDE.md inherited it from SOT. | **Medium** | CLAUDE.md:236 → `…(`WINDOW_S = 75.0`, `clip_engine/candidates.py:22`)`; SOT.md:140 → `│   ├── window.py               # Signal-array construction for peak detection (build_signal_array, RESOLUTION_S)` and add `│   ├── candidates.py           # Peak detection + fixed 75 s backward setup look (WINDOW_S = 75.0) + skip_reason taxonomy` |
| **D7** `docs/PIPELINE.md` | 86 | "No clips → `skip_reason` (`source_unavailable \| low_energy \| high_silence \| diverse_peaks`)." | **Three of the four values do not exist.** `clip_engine/candidates.py:37-41` defines exactly: `no_signal_above_threshold`, `no_positive_signal`, `insufficient_retention_data`, `source_unavailable`, `all_candidates_suppressed_by_nms`. `grep -rn "low_energy\|high_silence\|diverse_peaks"` over all `.py/.ts/.tsx` → **0 hits**. This is a documented **API enum** (surfaced at `routers/clips.py` as `ClipListOut.skip_reason` and rendered by the dashboard "Why no clips?" link). Verified by me; not in the prior audit data. | **High** | `No clips → `skip_reason`, one of `source_unavailable \| no_signal_above_threshold \| no_positive_signal \| insufficient_retention_data \| all_candidates_suppressed_by_nms` (priority order + rationale in `clip_engine/candidates.py:225-266`).` |
| **D8** `docs/MIGRATIONS.md` | 102-114 | Rule 4's "✅ Correct — bounded batches" template: `UPDATE clips SET new_col = old_col WHERE new_col IS NULL LIMIT 1000` | **Not valid PostgreSQL.** Verified against the primary source (`postgresql.org/docs/current/sql-update.html`): the UPDATE synopsis has no LIMIT, and the docs state verbatim *"While there is no `LIMIT` clause for `UPDATE`, it is possible to get a similar effect through the use of a Common Table Expression and a self-join."* A copy-paste of this template raises a syntax error mid-migration. | **High** | Replace the SQL with the form the PG docs prescribe: `"WITH batch AS (SELECT ctid FROM clips WHERE new_col IS NULL LIMIT 1000) "` `"UPDATE clips SET new_col = old_col FROM batch WHERE clips.ctid = batch.ctid"` |
| **D9** `docs/SOT.md` | 112-116 | "there is no central clients.py… Anthropic in dna/brief.py, clip_engine/scoring.py, chat/runner.py, chat/intake.py, knowledge/\*, analysis/brief.py, improvement/brief.py, routers/insights.py… **Each sets timeout + max_retries.**" | 17 production `AsyncAnthropic(` sites exist (19 incl. tests/scripts) — measured. SOT's list names 15 and omits **`analysis/video_context.py`** and **`preference/style_distill.py`**. And "Each sets timeout + max_retries" is **false**: `preference/style_distill.py:31` is `AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)` with neither. SOT states as a settled rule the exact invariant that is already broken. | **Medium** | `…singletons in the modules that use them (Issue 37): dna/brief.py, clip_engine/scoring.py, chat/runner.py, chat/intake.py, knowledge/\*, analysis/brief.py, analysis/video_context.py, improvement/brief.py, preference/style_distill.py, routers/insights.py, routers/thumbnails.py (17 sites). **Every site MUST set `timeout` + `max_retries`; `tests/test_llm_conformance.py` enforces this over a hand-maintained module list, so new modules are not covered until added.**` |
| **D10** `docs/SOT.md` | 196-212 | `routers/` file listing (16 entries) | 25 files on disk. Missing: **`billing.py`**, **`insights.py`**, `api_keys.py`, `export.py`, `video_review.py`, `_schemas.py`, `_enqueue.py`, `_owned.py`. `billing.py` is the entire Stripe/minute-pack HTTP surface; `_owned.py`/`_enqueue.py` are the two seams the architecture depends on for tenant isolation and enqueue safety. | **Medium** | Add the 8 missing rows, minimally: `billing.py # Stripe Checkout + webhook + balance/ledger endpoints`, `insights.py # /creators/me/insights + analyze-performer`, `export.py # GDPR Art.15/20 data export (API-only, no UI — Issue 495)`, `api_keys.py`, `video_review.py`, `_owned.py # fetch+ownership in one query, 404 for missing AND foreign`, `_enqueue.py # single enqueue + SSE-ownership seam (19 endpoints)`, `_schemas.py` |
| **D11** `docs/SOT.md` | ~340-560 | Data-model section documents 33 tables | `models.py` declares **39** `__tablename__`s. Six appear **nowhere in the file**: `minute_packs`, `clip_impressions`, `creator_api_keys`, `data_exports`, `feature_flags`, `summaries`. Note the asymmetry: `minute_deductions` *is* documented (SOT:539) but `minute_packs` — the row a purchase creates — is not. | **Medium** | Add the six. Minimum: `minute_packs # purchased minute balance (Stripe Checkout → grant); the buy side of minute_deductions`, `feature_flags # DB-row kill switches, 30s TTL cache, fail-open (flags.py, Issue 284)`, `data_exports # GDPR Art.15/20 export jobs (migration 0027, RLS-gated)`, `creator_api_keys`, `clip_impressions`, `summaries` |
| **D12** `docs/SOT.md` | 275-295 | `tests/` block lists 20 test files | **260** `test_*.py` modules on disk, plus `tests/perf`, `tests/fixtures`, `tests/preference`, `tests/ingestion`, `tests/scripts`, `tests/eval`. The listing is ~8% complete and reads as exhaustive. | **Low** | Replace the enumeration with: `├── tests/  # ~260 modules mirroring source structure. Two lanes: default unit (mocks DB/Redis) and `-m integration` (real PG+pgvector via compose). Sub-packages: eval/ (clip-quality scenarios), preference/, ingestion/, perf/, fixtures/, scripts/. **Do not enumerate — this drifts. Use `find tests -name 'test_*.py'`.**` |
| **D13** `docs/SOT.md` | 630-660 | Pipeline ASCII diagram: Ingest → Transcribe → Signals → Candidates → Score → Rank → Render | The real chain (`worker/tasks.py::start_pipeline`, and correctly documented at `docs/PIPELINE.md:26-28`) is `ingest_video → transcribe_video → **analyze_video_context** (Issue 415) → build_signals → generate_clips → render_video_clips`. The diagram also omits the `merge` (Issue 416 hybrid LLM∪signal merge) and `sentence_snap` stages inside `generate_clips`, and the batched clip-metadata pass (Issue 417). | **Low** | Insert a `Video ctx` box between Transcribe and Signals (`whole-video LLM pass, Issue 415 — proposes ≤4 clip moments; never fails the chain`) and change the `Candidates` box to `Candidates → Snap → Merge  detect peaks → back 75 s → sentence-snap → merge LLM moments ∪ signal peaks (NMS)` |
| **D14** `CLAUDE.md` | 53-54 | "Python source at root or in: `routers/`, `youtube/`, `ingestion/`, `dna/`, `clip_engine/`, `preference/`, `knowledge/`, `upload_intel/`, `improvement/`, `worker/`" — under a heading that says "Canonical layout is **enforced**." | 15 Python packages exist. Missing: **`billing/`**, **`chat/`**, **`analysis/`**, **`notify/`**, **`media/`**. This list is a machine input — `run_layer0.py::_CANDIDATE_SOURCES` gaps are attributed to it at `docs/issues.md` (Issue 497). | **Medium** | `- Python source at root or in: `routers/`, `youtube/`, `ingestion/`, `dna/`, `clip_engine/`, `preference/`, `knowledge/`, `upload_intel/`, `improvement/`, `worker/`, `billing/`, `chat/`, `analysis/`, `notify/`, `media/` (15 packages — keep in sync with `ls -d */`; `run_layer0.py::_CANDIDATE_SOURCES` reads from this list)` |
| **D15** `CLAUDE.md` | 55 | "Frontend assets in `static/`" | Contradicted 172 lines later by `CLAUDE.md:227`: React+TS under `/app/*`, "the legacy vanilla `static/*.html` app pages were retired (Issue 226); only `tos`/`privacy`/`accessibility` + shared CSS/JS remain". | **Low** | `- Frontend source in `frontend/src/` (React+TS, served under `/app/*`). `static/` holds only the public legal pages (`tos`, `privacy`, `accessibility`) + shared CSS/JS.` |
| **D16** `CLAUDE.md` | 120 | Phase-4 gate: ``python3 .claude/skills/production-assessment/scripts/run_layer0.py`` | `python3` is the system interpreter without the project venv; the rule that this must be `.venv/bin/python` lives only in `LEFT_OFF.md:199` and the resulting phantom-result incident is logged at `docs/OFF_COURSE_BUGS.md:156`. `grep -n venv CLAUDE.md` → nothing. The mandated close-out gate instructs the interpreter that produces false results. | **High** | `- [ ] `.venv/bin/python .claude/skills/production-assessment/scripts/run_layer0.py` passes — **`.venv/bin/python`, never bare `python3`**: the system interpreter lacks the project deps and reports phantom "all green" (OFF_COURSE_BUGS.md:156)` |
| **D17** `README.md` | 27 | "it pulls your YouTube Analytics (retention curves, **demographics**, activity windows)… **then builds** a versioned channel 'DNA' profile" | `Demographics` is written (`youtube/analytics.py:426-431`) and purged (`worker/tasks.py:4391`) and **read by nothing**. Verified: `grep -rni demographic` over `dna/ knowledge/ routers/ clip_engine/ upload_intel/ improvement/ preference/ chat/ frontend/src/` → **0 hits**. Contrast `AudienceActivity`, which *is* read by `dna/builder.py:17`, `chat/tools.py:348`, `routers/upload_intel.py:45`, `routers/publications.py:242`. | **Medium** | `it pulls your YouTube Analytics (retention curves, activity windows) and Data API metadata, then builds a versioned channel "DNA" profile…` — and either wire demographics into a feature or stop collecting it (see D18) |
| **D18** `docs/SOT.md` | 685 | "**PII minimization:** store only what features need; demographics aggregated." | The first clause is violated by the second's own subject: `demographics` is collected from a YouTube Analytics scope, stored, backed up (`docs/COMPLIANCE.md:104` — carried in the encrypted `pg_dump`), and consumed by zero features. **Correction to the audit brief:** the claim that "README/walkthrough/SOT/COMPLIANCE all say demographics feeds the DNA" does **not** hold. `docs/COMPLIANCE.md:97,185` and `static/privacy.html:81-82` are **accurate** (collection + aggregation only, no DNA claim) and need no change. Only **README:27** implies it feeds the DNA, and only **SOT:685** asserts a minimisation principle the schema breaks. | **Medium** | Either (a) delete the table + the `yt-analytics` demographics fetch and drop the row from COMPLIANCE:97, or (b) change SOT:685 to `**PII minimization:** demographics are stored aggregated-only and are currently **collected but unused** — see Issue <n>. Every other analytics table feeds a named feature.` |
| **D19** `README.md` | 46 | "Production hardening — … **Prometheus metrics**, Sentry, and OpenTelemetry tracing." | `/metrics` auto-disables in production when `METRICS_TOKEN` is unset (`config.py:1145-1149`), and it is unset (`docs/OFF_COURSE_BUGS.md:128`). There is no scraper service in `docker-compose.prod.yml`. Per the verified F3 verdict, even restoring the token would not fix it: `prometheus_client` runs single-process (`PROMETHEUS_MULTIPROC_DIR` unset), so 6 of 12 metric families live only in the worker/render-worker/beat containers and `llm_cost_usd_total` would silently under-report by the majority. | **Medium** | `- **Production hardening** — per-creator Postgres Row-Level Security, rate limiting, structured logging with PII/token redaction, Sentry, and OpenTelemetry tracing. (A Prometheus `/metrics` endpoint exists for local development; it is token-gated and not scraped in production.)` |
| **D20** `db.py` | 35, 38-40 | Comments size the pool against "the 25-conn **PgBouncer sidecar** (docs/DEPLOYMENT.md)" and disable prepared statements for "PgBouncer in transaction-pooling mode" | `grep -c pgbouncer docker-compose.prod.yml` → **0**. PgBouncer exists only in `docker-compose.staging.yml`. So production runs `prepare_threshold=None` (giving up psycopg3 server-side prepares) and `_POOL_SIZE=15` sized to a component that is not deployed there. This is B4 #4, verified — with the correction that staging *does* have it. | **Low** | Append to the comment block: `# NOTE 2026-08-17: PgBouncer is deployed in docker-compose.staging.yml ONLY. Production (docker-compose.prod.yml) connects to Postgres directly, so prepare_threshold=None is currently a no-cost precaution rather than a requirement. Keep it: staging must exercise the prod-shaped connection path.` |
| **D21** repo root | — | `CLAUDE.md:50`: "Canonical layout is enforced. Do not create files outside it." | Two untracked stray directories sit at repo root and no gate notices: `{{pkgetc}}/cert.pem` (a broken symlink into a non-existent `{{etc}}/`, from a botched template expansion, 2026-08-03) and `React app visual review/design_handoff_autoclip_redesign/` (2026-06-24). Both are `git`-untracked, so they are invisible to every CI check. | **Low** | Delete both, and add to `.gitignore`: `{{*}}/` — no replacement text needed |

### 1.3 Docs that are *correct* — worth stating

`docs/PIPELINE.md` is the most accurate doc in the set: the task chain at `:26-28` names
`analyze_video_context`, the transcription row at `:58` correctly says "Deepgram nova-3 default",
and `:80` cites `clip_engine/candidates.py` for `WINDOW_S` **without** repeating SOT's wrong
`window.py` pointer. Its only defect is D7. `docs/COMPLIANCE.md` is accurate on demographics.
`CLAUDE.md:222` is correct on the transcription default where `docs/SOT.md:52` is wrong.

**The pattern: the newest docs are right and the mandated-read-order docs are stale.** `CLAUDE.md`
requires reading `SOT.md` **first**, before writing a line of code — and `SOT.md` is the single most
divergent file in the repo (13 of the 21 rows above).

### 1.4 The mechanism (why this keeps happening)

There is **exactly one** test in the repo that asserts anything about `docs/SOT.md`'s accuracy:
`tests/test_pgbouncer_image_pin.py:99-115`, which checks that one historical sentence about the
`TOKEN_ENCRYPTION_KEY` runbook is no longer present. It asserts nothing about the stack table, the
model registry, the file tree, or the schema. `tests/test_incident_docs.py` pins only
`RUNBOOKS.md` / `INCIDENT_RESPONSE.md` substrings.

So: the docs are a hand-maintained mirror of the code with ~3 lines of automated coverage, and
`CLAUDE.md` designates that mirror as the *first* input to every session. Every divergence above is
a live trap for the next agent — D1 in particular would cause a session to "correct" a deliberate
owner directive back to Sonnet, and D16 instructs the interpreter that already produced one
phantom-green incident. **This is a plausible generator of the "one baby snag after another"
pattern, and it is cheap to close:** four of these (D1, D2, D6, D14) are one-line source-scanning
tests of the shape the repo already ships in `frontend/src/test/sourceScan.ts`.

---

# JOB 2 — Auditing the audit

## 2.1 Mechanical citation check

Script: `/tmp/claude-1000/…/scratchpad/cite_check2.py` (run with `.venv/bin/python`). It extracts
every `path:line` (and `path:line-line` / `path:line,line`) token from all 18 reports, filters URL
fragments and version pins, then resolves each path through a ladder (exact → unique suffix →
unique basename) and bounds-checks **every** cited line against the file's real length.

```
reports scanned          : 18
citations extracted      : 909
RESOLVE (file + all lines in range) : 906
  exact path as written  : 657
  basename/suffix shorthand (e.g. "ci.yml:457" → .github/workflows/ci.yml) : 230
  out-of-repo but real   : 7   (~/.claude/ISSUES_LOG.md ×5, .venv slowapi extension.py ×2)
  audit's own untracked files (process-map.md, d08…, d09…, d10…) : 12
FAIL                     : 3
```

**The full list of citations that do not resolve — all 3:**

| Report | Citation | Problem | Does it invalidate the finding? |
|---|---|---|---|
| `mA-gates-exitcodes.md:120` | `.github/workflows/freshness.yml:29-31` | File has **30** lines | **No.** The cited content (`run_layer0.py --gates freshness --require-fresh`) is at 29-30. Range end overshoots by 1. |
| `mA-gates-exitcodes.md:208` | `tests/test_layer0_module_coverage.py:86-95` | File has **94** lines | **No.** `test_every_floored_module_is_resolvable_in_principle` is at line 86, as claimed. Overshoot by 1. |
| `mC-probes-and-scripts.md:292` | `main.py:553-575` | File has **567** lines | **No.** The `health` handler is exactly 553-567. Overshoot by 8. |

**One precision issue, not a failure:** `d05-clip-engine.md:192` cites bare `efficacy.py:262`, and two
files share that basename. It resolves correctly by elimination — `tests/eval/efficacy.py` is only 46
lines, and `preference/efficacy.py:262` reads *"# Ranking 2 — generic signal (cold-start scorer, no
DNA/preference)"*, which is exactly what the finding claims.

**One cosmetic issue:** `mE-config-flags-external.md:52` writes a literal elision —
`docs/assessment/.../01-domains/d08-deploy-observability.md:153` — which is unresolvable as written.

### Verdict on the mechanical layer

**909 extracted, 906 resolve, 3 fail, 0 findings invalidated.** All three failures are range-**end**
overshoots where the content at the range **start** is correct. Zero citations point at a
non-existent file. For a 5,400-line corpus written across 18 independent agent runs, this is a clean
result — the audit does not commit the sin it is auditing.

## 2.2 Hand spot-check — do the resolving citations *say* what the findings claim?

23 checks, weighted to the heaviest findings (every HIGH-severity phase-1 finding's primary
evidence, plus both CONFIRMED findings, plus the whole Render supersession trail). Each was opened
at the cited line and read.

| # | Citation | Finding it carries | Verdict |
|---|---|---|---|
| 1 | `main.py:197` | `spa-route-never-tested` | ✅ Exact — `_SPA_BUILT = _SPA_INDEX.is_file()` |
| 2 | `frontend/src/lib/api.ts:82` | `closed-loop-type-contract` | ✅ Exact — `return (await resp.json()) as T` |
| 3 | `limiter.py:129-133` | `limiter-fails-closed-500` | ✅ Supports — `Limiter(key_func=…, storage_uri=…, storage_options=…)`, no `swallow_errors`, no `in_memory_fallback_enabled` |
| 4 | `docs/DECISIONS.md:2634-2636` | same | ✅ Supports — "a Redis stall degrades to **fail-open**", i.e. the decision is the opposite of the code |
| 5 | `main.py:553-567` | `health-200-during-outage` | ✅ Exact — bare `dict` return, `"degraded"` in body, no status code |
| 6 | `docs/DEPLOYMENT.md:162-163` | same | ✅ Exact — "degrades to non-`ok` + 503" |
| 7 | `.github/workflows/mutation.yml:48,52,53-58` | `mutation-gate-never-ran` | ✅ Exact — `mutmut run \|\| true`; `mutmut results \| tee`; `{ … } >> "$GITHUB_STEP_SUMMARY"` |
| 8 | `pyproject.toml:6` | `calver-release-dead` | ✅ Exact — `version = "2026.6.0"` |
| 9 | `main.py:83-85` | same | ✅ Exact — `except PackageNotFoundError: __version__ = "dev"` |
| 10 | `.github/workflows/deploy.yml:365-370` | `prod-smoke-silent-skip` | ✅ Exact — `WARNING: CC_JWT_SECRET not set — skipping critical journey smoke.` |
| 11 | `billing/stripe_client.py:93-116` | `async-payment-silent-loss` (CONFIRMED) | ✅ Supports — params dict has mode/line_items/urls/metadata and **no** `payment_method_types` |
| 12 | `routers/billing.py:245-260` | same | ✅ Exact — `if event["type"] != "checkout.session.completed": return {"status":"ignored"}`; the comment at :256 names `async_payment_succeeded` with no branch for it |
| 13 | `preference/model.py:206` | `lgbm-degenerate-ramp` | ✅ Supports — `lgb.LGBMClassifier(n_estimators=100, learning_rate=0.1, verbosity=-1)`; no `min_child_samples`/`num_leaves` override, so LightGBM's default `min_child_samples=20` applies |
| 14 | `config.py:602` | same | ✅ Exact — `PERSONALIZATION_THRESHOLD_LABELS: int = 20` |
| 15 | `tests/preference/test_rerank_eval.py:105-131` | `rerank-eval-knife-edge` (CONFIRMED) | ✅ Supports — asserts `label_count == 2 * threshold`, `isinstance(_model, LGBMClassifier)`, `preference_weight == PREFERENCE_WEIGHT_CAP` |
| 16 | `scripts/backup_pg.sh:66-69` | `backups-zero-percent-armed` | ✅ Exact — `for v in BACKUP_R2_BUCKET …; do [ -n "${!v}" ] \|\| die …` |
| 17 | `docs/RUNBOOKS.md:648` | same | ✅ Exact — `- [ ] measured **RTO** recorded here: ________` |
| 18 | `observability.py:786-788` | `business-metrics-exported-nowhere` | ✅ Exact — "Do NOT create a second prometheus-client bridge here; that would double-count." |
| 19 | `CLAUDE.md:120` | `claude-md-wrong-interpreter` | ✅ Exact — `python3 .claude/skills/production-assessment/scripts/run_layer0.py` |
| 20 | `docs/DECISIONS.md:2541 / :2563 / :2395 / :277` | `decisions-not-adrs` (CONFIRMED) | ✅ All four exact — two Render entries (06-24), the reversal (06-27, "the live app does **not** run on Render"), and the heading-parenthetical supersession pattern at :277 |
| 21 | `.github/workflows/ci.yml:606-609` + `docs/BRANCHING.md:100-127` | `advisory-gates-free-to-require` | ✅ Both exact — the comment says "GATING since 2026-07-29" and the required-contexts array contains 8 entries, none of which is `visual`, `Frontend (lint, test, build)` or `migration-lint` |
| 22 | `preference/style_distill.py:31` | `conformance-registry-drift` | ✅ Exact — `AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)`, no timeout, no max_retries |
| 23 | `youtube/_redis.py:32` | `spend-guard-redis-no-timeout` | ✅ Exact — `redis.from_url(settings.REDIS_URL, decode_responses=True)`, no socket timeouts |

**Mismatches found: 0.** No finding needs to be pulled for an unsupported citation.

**One methodological note, in the audit's favour.** `d08`'s HIGH finding
`cf-healthcheck-not-on-free-plan` cites `docs/EDGE_SECURITY.md:87` — which says *"On the Free plan
the only lever that exempts traffic from it is an IP Access Rule"*. That line establishes the zone
is on the Free plan; it does **not** say Health Checks are unavailable. The report does not conflate
them: it cites `developers.cloudflare.com/health-checks/` separately for the availability claim.
That is the correct separation of an in-repo fact from an external fact, and it is what let me
verify both halves independently.

## 2.3 External source spot-check — 10 URLs + 1 local dependency check

| Source | Attached claim | Verdict |
|---|---|---|
| `developers.cloudflare.com/health-checks/` | "Standalone Health Checks: Free = **No**" (d08 F1, HIGH) | ✅ **Exact.** Fetched availability table: Free `No`, Pro `Yes` (10), Business `Yes` (50), Enterprise `Yes` (1,000) |
| `docs.stripe.com/checkout/fulfillment(.md?payment-ui=stripe-hosted)` | Stripe requires handling `async_payment_succeeded` + checking `payment_status` (d09, CONFIRMED) | ✅ **Exact.** The page's own sample handler is `if event['type'] == 'checkout.session.completed' \|\| event['type'] == 'checkout.session.async_payment_succeeded'`, and step 4 of `fulfill_checkout` is "Check the `payment_status` property" |
| `postgresql.org/docs/current/sql-update.html` | (my own D8 verification) | ✅ **Exact.** Synopsis has no LIMIT; docs state *"there is no `LIMIT` clause for `UPDATE`… use a Common Table Expression and a self-join"* |
| `github.com/fastapi/full-stack-fastapi-template` | "Actively maintained — last commit **2026-08-17T08:38Z**… **There is no `services/`**" (d01's load-bearing "no service layer is correct" verdict) | ✅ **Exact, to the minute.** Verified via `gh api`: last commit `2026-08-17T08:38:41Z`; `backend/app` contains `api/ core/ crud.py models.py alembic/ utils.py …` and **no** `services/` |
| `12factor.net/config` | Factor III excludes internal application config (d01 `config-algorithm-constants`) | ✅ **Exact.** *"This definition of 'config' does not include internal application config, such as `config/routes.rb` in Rails"* |
| `research.google/pubs/state-of-mutation-testing-at-google/` | Mutation testing at scale; diff-based surfacing (d10) | ✅ Real (ICSE 2018 SEIP, Petrovic & Ivanković); supports the diff-based/arid-lines and developer-attention claims |
| `redbeat.readthedocs.io/en/latest/intro.html` | "RedBeat writes **nothing** to disk — schedule and lock live in Redis" (d03 #7; kills the `stat /tmp/celerybeat-schedule` liveness probe) | ✅ Supports. *"RedBeat is a Celery Beat Scheduler that stores the scheduled tasks and runtime metadata in Redis"*, *"Shared data store; Beat isn't tied to a single drive or machine"*, distributed lock in Redis |
| `zerometric.net/…/cloudflare-zero-trust-free-plan-limits-2026/` | "Cloudflare **Access** free tier = 50 seats" (d08 background) | ✅ Real, dated 2026-07-18, supports the 50-seat claim. **Note:** it says nothing about Health Checks — correctly, the report does not use it for that |
| `zylos.ai/research/2026-02-20-graceful-degradation-ai-agent-systems/` | Circuit breakers / fallback chains for LLM apps (d04 resilience) | ✅ Real, dated as cited, substantive; supports the CLOSED→OPEN→HALF-OPEN and model-fallback recommendations |
| `qaskills.sh/blog/checkly-playwright-synthetic-monitoring-guide` | "Monitoring as Code is the 2026 mainstream shape" (d10 #2) | ✅ Real and substantive (not filler) — code examples, `npx checkly test/deploy`, honest DIY-GitHub-Actions alternative section |
| *(local)* `anthropic==0.105.2` default timeout | d01/d04: "default is `Timeout(connect=5.0, read=600, write=600)`", `max_retries=2` — the basis for the 30-min worker-slot-hang scenario | ✅ **Exact.** `.venv/bin/python` → `DEFAULT_TIMEOUT = Timeout(connect=5.0, read=600, write=600, pool=600)`, `DEFAULT_MAX_RETRIES = 2` |

**External sources that do not support their claim: 0.** No dead links, no hallucinated pages, no
misattributions. The two domains that looked most like AI content farms (`zerometric.net`,
`zylos.ai`) both resolved to real, dated, substantive articles that support exactly the narrow
claims they are attached to.

## 2.4 Verdict on the audit's own reliability

| Layer | Result |
|---|---|
| File:line citations | 909 extracted · **906 resolve** · 3 range-end overshoots · **0 pointing at a non-existent file** |
| Hand-verified content match (23 heaviest) | **23/23 support the claim** · 0 mismatches · 0 findings to pull |
| External sources (10 URLs + 1 local) | **11/11 real, current, and supporting** |

**The audit's citations are sound.** The one thing it is *not* immune to — and the verified phase-2
verdicts already demonstrate this at scale — is **over-scoping and over-rating** findings whose
mechanical core is correct. Of the 9 unverified HIGHs re-checked, **9/9 came back CORRECTED and 6/9
were downgraded** (5 → medium, 2 → low, incl. E7 which duplicated a phase-1 item its own preamble
promised not to re-report). That failure mode is about *judgment*, not evidence, and it is the one
to keep discounting when reading the final report — not the citations.

---

## Appendix — reproduction

```bash
cd /home/reese/workspace/Youtube-Video-AI-Editor
.venv/bin/python /tmp/claude-1000/-home-reese-workspace-Youtube-Video-AI-Editor/\
46598cfa-a84b-427f-9ae1-4077cf6f6647/scratchpad/cite_check2.py out.json
```

Key one-liners behind Job 1:

```bash
grep -n "TRANSCRIPTION_BACKEND" config.py .env.example        # deepgram vs whisperx
grep -rn "WINDOW_S" clip_engine/*.py                          # candidates.py:22, not window.py
grep -n "^SKIP_REASON" clip_engine/candidates.py              # the 5 real codes
grep -rn "low_energy\|high_silence\|diverse_peaks" --include=*.py --include=*.tsx .   # 0 hits
grep -rni demographic dna/ knowledge/ routers/ clip_engine/ chat/ frontend/src/       # 0 hits
grep -c pgbouncer docker-compose.prod.yml                     # 0
grep -rn "AsyncAnthropic(" --include=*.py . | grep -v tests | grep -v scripts | wc -l # 17
grep -oP '__tablename__ = "\K[a-z_]+' models.py | wc -l        # 39
ls routers/*.py | wc -l                                        # 25
find tests -name "test_*.py" | wc -l                           # 260
```
