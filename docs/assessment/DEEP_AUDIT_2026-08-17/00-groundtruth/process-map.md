# Ground truth — the actual development process and tooling

**Produced:** 2026-08-17, Phase 0 of the deep standards audit. Trunk = `main`, HEAD `1def133`.
**Purpose:** factual map of how work flows from idea to production, so it can be judged against
current standard. **Reality as-is, no recommendations.**

---

## 1. CI/CD

### Workflow inventory (`.github/workflows/`)

| File | Lines | Trigger | Runner |
|---|---|---|---|
| `ci.yml` | 768 | `pull_request: [main]`, `workflow_dispatch` | ubuntu-latest |
| `deploy.yml` | 383 | `workflow_run` (after Docker publish), `workflow_dispatch` | **self-hosted (prod VM)** |
| `docker-publish.yml` | 106 | `push: [main]`, `release: published` | self-hosted |
| `staging-drills.yml` | 102 | `workflow_dispatch` only | self-hosted |
| `mutation.yml` | 63 | weekly cron Mon 07:00 UTC | ubuntu-latest |
| `llm-e2e-nightly.yml` | 151 | nightly cron 03:00 UTC | ubuntu-latest |
| `freshness.yml` | 30 | quarterly cron | ubuntu-latest |
| `health-check.yml` | 80 | **`workflow_dispatch` only** (schedule deliberately removed) | ubuntu-latest |
| `activate-rls.yml` | 276 | dispatch only (one-time, idempotent) | — |

`ci.yml` has **no `push` trigger** — deliberate (`ci.yml:28-31`), on the premise that every path to
`main` is a PR.

### The 8 required checks (BLOCKING)

Documented verbatim at `docs/BRANCHING.md:100-127`, applied 2026-08-15:

1. `Lint (ruff)` — `ruff check .` + `ruff format --check .`, ruff pinned `0.15.15` (`ci.yml:57-70`)
2. `Unit tests (pytest)` — bare `pytest` (default lane) + a hard `render_env` lane with real ffmpeg
   + mediapipe + BlazeFace, guarded by a `-ge 7` collect-only count (`ci.yml:72-141`)
3. `Integration tests (postgres + redis)` — `alembic upgrade head` then `pytest -m integration`
   against pgvector/pg16 (`ci.yml:143-201`)
4. `Coverage floor (pytest-cov ratchet)` — one invocation of `run_layer0.py --gates
   coverage,module_coverage,diff_cover --require coverage,module_coverage,diff_cover`,
   `fetch-depth: 0` (`ci.yml:203-253`)
5. `Types + SAST + deps (mypy, bandit, pip-audit)` — `run_layer0.py --gates
   ruff,mypy,bandit,pip_audit,freshness` (`ci.yml:419-435`)
6. `Docker build (smoke test)` — buildx, `push: false` (`ci.yml:753-768`)
7. `Playwright (smoke + a11y)` — Chromium-only, `--grep-invert "@visual"` (`ci.yml:567-604`)
8. `eval/clip-quality` — a **commit status**, not a job, posted by the `eval` job to
   `context.payload.pull_request?.head?.sha` (`ci.yml:459-565`)

### NOT required — advisory only (failure does not block merge)

- **`Frontend (lint, test, build)`** (`ci.yml:437-457`) — eslint + vitest + build. **The entire
  92-file frontend unit suite is not a merge gate.**
- **`Migration lint (Squawk)`** (`ci.yml:255-417`) — squawk lint, online downgrade round-trip with
  byte-identical `pg_dump` diff, `scripts/check_downgrades.py`. **All migration-safety machinery is
  advisory at the merge gate.**
- **`Visual regression`** (`ci.yml:606-664`) — the in-file comment says *"GATING since 2026-07-29"*
  but it is **not in the required-contexts list**. Direct contradiction between code comment and
  protection config.
- **`Flake detection`** — explicitly `continue-on-error: true` (`ci.yml:666-751`).

### Protection settings

`strict: true` · `required_linear_history: true` · `allow_force_pushes: false` ·
`enforce_admins: true` · **`required_pull_request_reviews: null` — no code review of any kind is
required**, by design (solo maintainer cannot self-approve; `docs/BRANCHING.md:129-131`).

### Ways CI can pass vacuously

- **`eval/clip-quality` on a non-`clip_engine` PR** posts `state: 'success'` with description
  "Skipped — no clip_engine/ or tests/eval/ changes" (`ci.yml:553-565`). Intentional and documented,
  but the required context is green without executing on most PRs. (Mitigated: the same scenario
  tests also live in `tests/test_clip_engine.py`, inside the required Unit lane.)
- **`static-gates` runs `run_layer0.py` WITHOUT `--require`.** `run_layer0.py:576-605`: a `skipped`
  gate is only converted to failure if named in `--require`. `gate_mypy`/`gate_bandit`/
  `gate_pip_audit`/`gate_ruff` return `{"status": "skipped"}` when the tool is missing **or when its
  output is unparseable** (`run_layer0.py:157-164, 223-231, 268-280`). Unparseable JSON → gate
  silently skipped → job prints "All runnable gates passed" → exit 0. The coverage job was hardened
  for exactly this class (Issue 479, which had made per-module and diff-cover gates silently no-op
  from 2026-06-23 to 2026-08-12); **`static-gates` still has the un-hardened shape.**
- **mypy/bandit only scan an allowlist.** `_CANDIDATE_SOURCES` (`run_layer0.py:43-62`) covers
  `routers, youtube, ingestion, dna, clip_engine, preference, knowledge, upload_intel, improvement,
  worker, billing, auth.py, config.py, crypto.py, db.py, limiter.py, main.py, models.py`. **Never
  type-checked or SAST-scanned:** `api_key.py`, `flags.py`, `event_log.py`, `observability.py`,
  `redact.py`, `verbose.py`, `shared_resources.py`, `chat/`, `media/`, `notify/`, `analysis/`,
  `scripts/`. (`ruff check .` does cover the whole tree.)
- **mypy excludes `tests/`** (`pyproject.toml:44`) — 3,444 tests are untyped territory despite
  `disallow_untyped_defs = true`.
- **Baselines are comparison thresholds, not absolutes.** `docs/assessment/baselines.json`:
  `ruff_issues 0`, `mypy_errors 0`, `coverage_line_rate 83.00`, `bandit_high 0`, `bandit_medium 0`,
  `pip_audit_vulns 0`. Code defaults are `1_000_000` for the gradual gates — the committed file is
  what makes them real.
- **ffmpeg install in `unit`/`integration`/`coverage`/`eval` is soft** (`|| echo "::warning::..."`,
  `ci.yml:96-104`). Only `render_env` hard-installs and hard-fails.
- **`migration-lint` and `eval` use `dorny/paths-filter`;** when paths don't match, all steps are
  `if: false` and the job reports success.

### Meta-gate (non-standard, and good)

`tests/test_ci_config.py` (22 tests) asserts properties **of the CI/CD YAML itself** — deploy runs
self-hosted; the pre-migration dump precedes alembic; prod `needs: deploy-staging`; staging is
sha-pinned never `:latest`; the coverage job runs all three gates in one required invocation;
`render_env` is hard and guarded; the eval commit status targets PR head sha; `ci_local.sh` doesn't
override the marker lane; staging/prod compose images match.

---

## 2. Local gates — what actually runs before a push

- `.git/hooks/` is empty. Hooks come from `git config core.hooksPath` → `.githooks`.
- **`.githooks/pre-push`** (25 lines) — the only hook. Drains stdin, honors `CI_LOCAL_SKIP=1`, then
  `exec scripts/ci_local.sh --fast`.
- **There is no pre-commit hook and no `.pre-commit-config.yaml`.** Nothing gates a commit.
- **`scripts/ci_local.sh`** (163 lines), `--fast` (default) / `--full`:
  - `ruff check` — full tracked tree, blocking
  - `ruff format --check` — **ratchet: changed files vs `origin/main` only** (43-file pre-existing
    drift deliberately excluded)
  - `run_layer0.py --gates mypy,bandit` (`+pip_audit` on `--full`) — **no `--require`**, same
    vacuous-skip path as CI
  - `pytest -q` bare — **skipped entirely if `redis-cli ping` fails** ("Redis down — CI covers it")
  - coverage — `--full` only, also Redis-gated
  - frontend — eslint **ratchet on changed `.ts/.tsx` only** (10-item baseline knowingly excluded),
    then full `vitest` + `npm run build`; **entire block skipped if `frontend/node_modules` is
    absent**
  - Node version honored from `.nvmrc` if the nvm path exists (node 26's jsdom breaks vitest)
- **Escape hatches are documented and first-class:** `git push --no-verify`, `CI_LOCAL_SKIP=1`.
- `scripts/dev_session_setup.sh` is invoked by `.claude/hooks/session-start.sh` **only when
  `CLAUDE_CODE_REMOTE=true`** — a no-op on the developer's machine.

**Gap:** every local gate degrades to "skipped, still exit 0" when a dependency is missing (Redis
down, node_modules absent, tool not installed), and the summary prints `Local CI passed.` **The
failure mode is silence, not red.**

---

## 3. Test strategy

### Python

`pytest.ini` — `asyncio_mode = auto`, `filterwarnings = error::RuntimeWarning`, and an
**exclusionary** default:
```
addopts = -m "not integration and not quarantine and not llm_live and not render_env and not transcription_live"
```

| Lane | Collected |
|---|---|
| default (gating) | **3,234** |
| total collected | 3,444 (210 deselected) |
| `integration` | ~191 |
| `llm_live` | 10 |
| `render_env` | 7 |
| `transcription_live` | 1 |
| `quarantine` | **1** |

260 test files, 3,068 `def test_*` definitions. Grep counts across `tests/`:
`@pytest.mark.skip` **70**, `@pytest.mark.skipif` **11**, `xfail` **4**, `@pytest.mark.quarantine`
**3 decorators / 1 collected test**.

Marker semantics are documented inline in `pytest.ini:8-25`, including the lesson that `render-env`
(hyphenated) could never be applied as a decorator and the lane sat at 0 selected tests from
introduction.

### Eval harness

`tests/eval/` — `efficacy.py`, `metrics.py`, `test_efficacy.py`, `test_metrics.py`, `scenarios/`
with **33 entries** (32 YAML geometry scenarios + a `ranking/` subdir). Runner in
`tests/test_clip_engine.py`: `test_eval_scenario` (parametrized), `test_eval_scenario_pass_rate`
(asserts **1.0**), `test_eval_scenario_count_floor` (`>= SCENARIO_FLOOR`, anti-hollowing),
`test_eval_scenario_no_unapproved_skip_markers` (regex-scans YAML for `skip|xfail`).

`scripts/eval_efficacy.py` is a separate, **manual, DB-connected** personalization harness
(NDCG/MRR: random vs generic-signal vs DNA+preference). **Not wired to any workflow.**

Goldens: `scripts/record_scoring_goldens.py` + `tests/test_scoring_goldens.py`.

### Mutation testing

`mutmut` scoped via `pyproject.toml [tool.mutmut] paths_to_mutate`, run weekly,
**`mutmut run || true`** — report-only, explicitly kept out of the required set
(`mutation.yml:5-9`).

### Frontend

- **vitest** — config inline in `frontend/vite.config.ts` (jsdom, `restoreMocks: true`,
  `globals: false`). **92 test files against 260 source files. There is NO `coverage` block — no
  frontend coverage measurement and no frontend coverage floor anywhere.**
- Structural/contract tests as a category: `src/test/no-glyph-icons.test.ts`,
  `no-native-video-controls`, `no-local-cut-storage`, `design-tokens.contract.test.ts`,
  `no-synthetic-waveform`, `no-native-form-controls`.
- **Playwright** — `frontend/playwright.config.ts`: `testDir: './e2e'`, `forbidOnly: !!CI`,
  `retries: CI ? 1 : 0`, projects `desktop` (1440×900) + `mobile` (Pixel 5), webServer = Vite dev
  server, backend mocked at the network boundary (`e2e/fixtures/mock-api.ts`). Specs: `smoke`,
  `a11y` (`@axe-core/playwright`), `review`, `editor-persistence`, `tool-shell`. Visual baselines in
  `e2e/__snapshots__/`, `maxDiffPixelRatio: 0.01`; must be regenerated on ubuntu-latest via
  `gh workflow run ci.yml -f update_snapshots=true`.
- **`frontend/playwright.config.prod.ts`** + `e2e/prod/` — a live-site audit against real prod with
  real auth (`npm run test:prod`). **Not wired to any workflow — manual only.**

---

## 4. Claude Code process assets

`.claude/` in-repo is 13 files:

```
.claude/settings.json          # allow Bash(git push:*); SessionStart hook
.claude/settings.local.json    # allow Bash(git rev-list *)
.claude/hooks/session-start.sh # remote-only (CLAUDE_CODE_REMOTE=true) → scripts/dev_session_setup.sh
.claude/commands/assess.md
.claude/commands/issue-workflow.md   # "SYNCED COPY - do not edit here"; canonical is ~/.claude/commands/
.claude/skills/best-practices/SKILL.md
.claude/skills/production-assessment/SKILL.md
                              /rubric.md, /scale-checklist.md, /report-template.md, /subagent-contract.md
                              /scripts/run_layer0.py     ← load-bearing: CI calls this
.claude/worktrees/            # empty
```

**There is no `.claude/workflows/` directory and no `issue-wave.js` harness** in this repo or under
`~/.claude/` (a memory index entry claims one; it is not present).

**Skills:**
- **`production-assessment`** (`last_verified: 2026-05-29`) — three layers. Layer 0 =
  `run_layer0.py` (deterministic: ruff/mypy/coverage/module-coverage/diff-cover/bandit/pip-audit/
  freshness vs `docs/assessment/baselines.json`, writes `docs/assessment/_machine.json`). Layer 1 =
  parallel per-module subagents → `docs/assessment/modules/<module>.md`. Layer 2 = verdict in
  `docs/assessment/REPORT.md` + snapshot to `history/`. Stated principle: *"Tools provide
  exhaustiveness. Claude provides judgment. Never ask Claude to be exhaustive."*
- **`best-practices`** (`last_verified: 2026-05-29`) — evergreen; operationalizes the One Rule.
- Freshness of both is machine-enforced quarterly by `freshness.yml` (>90 days = hard fail). In
  `static-gates` the same gate is **warn-only**.

**User-level (`~/.claude/`)** — 26 commands (`issue-workflow`, `code-review`, `assess`, `tdd`,
`grill-me`, `struggle-first`, `rubber-duck`, `post-mortem`, `doc-check`, `prd-to-issues`,
`write-a-prd`, `close-out`, the `production-*` family…), 7 agents, 4 skills, and
`~/.claude/scripts/sync-issue-workflow.sh`.

**The process itself** is codified in `CLAUDE.md:95-161` — **CHECK → APPROVE → BUILD → REVIEW &
ASSESS**, one issue at a time, Phase 2 requiring explicit human approval, Phase 4 a ~30-item manual
checklist with `run_layer0.py` as the only automated item.

---

## 5. Deployment — how a change reaches autoclip.studio

**Push-to-deploy, fully automated, with one blocking pre-prod gate.**

```
feature/<issue> --PR--> main            (8 required CI checks; rebase/squash only)
      |
      +--> push to main triggers docker-publish.yml (self-hosted)
             build linux/amd64 -> ghcr.io/reese8272/creatorclip:{latest, sha-<sha>, semver}
             + git tag + GitHub Release
      |
      +--> workflow_run success triggers deploy.yml (self-hosted, environment: production)
             job 1: deploy-staging  "Staging gate (data-bearing DB)"
             job 2: deploy          "Deploy -> autoclip.studio"   needs: deploy-staging
```

**Staging gate** (`deploy.yml:31-147`, `docs/DEPLOYMENT.md:279-304`) — deploys the **exact `sha-`
image under test** (never `:latest`) as compose project `ccstage`, runs `alembic upgrade head`
in-container with a `current == heads` assertion against a **persistent, data-bearing**
`staging_postgres_data` volume, seeds fixtures idempotently, runs `scripts/llm_harness.py --flow
core`, then stops app/worker keeping the volume. The volume's persistence *is* the mechanism — CI's
fresh-DB bootstrap cannot catch data-dependent migration failures (motivating incident 2026-07-02).
Break-glass: `workflow_dispatch` with `skip_staging=true`; the `!cancelled()` guard is load-bearing.

**Prod sequence** (`deploy.yml:149-383`) — sync GitHub secrets into `/opt/autoclip/.env` (guarded:
an unset secret never blanks a VM value) → GHCR login → **capture `PREV_IMAGE` RepoDigest** →
pull → `python scripts/doctor.py` preflight → **pre-migration encrypted `pg_dump` to R2**
(`predeploy/` prefix) → `alembic upgrade head` → `docker compose up -d` → **two-phase smoke**
(`/health` ×5, then `llm_harness.py --flow core`) → `docker image prune`.

**Rollback:** smoke failure re-pulls `PREV_IMAGE`, re-tags `:rollback`, relaunches via
`${IMAGE_TAG:-latest}`, then **still exits 1** so the deploy reports failed. Prune runs only after a
green smoke. Guard: empty `PREV_IMAGE` (first deploy) = manual recovery. Documented limits
(`docs/DEPLOYMENT.md:306-334`): single-VM image rollback, not blue-green; **~50 s window where the
broken image serves traffic**. Manual fallback `scripts/deploy.sh` SSHes to `147.182.136.107` and
mirrors the sequence but has **no staging-parity gate and no secret sync**.

**Migrations** (`docs/MIGRATIONS.md`, 247 lines): expand→backfill→contract across separate deploys;
`CREATE INDEX CONCURRENTLY` outside transactions; `NOT VALID` then `VALIDATE`; bounded backfill
loops; additive-only in the deploy that ships new code. Templates A–D. Enforced by Squawk
(`.squawk.toml`) + downgrade round-trip + `scripts/check_downgrades.py` with an
`alembic/DOWNGRADE_EXCEPTIONS` allowlist — **all in the non-required `migration-lint` job.** Policy
is roll-forward-first; `alembic downgrade` is break-glass.

**`render.yaml` (219 lines) is a parallel, unused path** — a Render Blueprint for a "beta-only
host". Prod today is Docker Compose on a DigitalOcean droplet. It contains `VERBOSE_LOGGING=true` +
`VERBOSE_LOGGING_ALLOW_PROD=true` with an inline warning that raw prompts/transcripts/PII will
appear in the log stream.

**`deploy/charts/` (Helm/GKE)** — `docs/DEPLOYMENT.md:33-38`: *"the architecture is decided and the
Helm chart is written, but it has NEVER run on a real cluster."*

---

## 6. Observability

**Instrumented** (`observability.py`, ~1,100 lines):
- **Prometheus** (`prometheus-client==0.25.0`) — 12 metrics: `HTTP_REQUEST_DURATION`,
  `CELERY_TASK_DURATION`, `CELERY_TASKS_TOTAL`, `BEAT_LOCK_SKIPS_TOTAL`, `LLM_TOKENS_TOTAL`,
  `LLM_COST_USD_TOTAL`, `RENDER_FAILURES_TOTAL`, `DB_POOL_CHECKED_OUT`, `CELERY_QUEUE_DEPTH`,
  `REDIS_USED_MEMORY_BYTES`, `R2_BYTES_STORED`, `R2_OBJECTS`. `/metrics` **auto-disables in
  production if `METRICS_TOKEN` is unset** (`config.py:1144-1150`).
- **Sentry** (`sentry-sdk==2.32.0`) — FastAPI/Celery/SQLAlchemy/Redis integrations, `before_send`
  PII scrubber (`observability.py:611-676`). No-op on empty DSN.
- **OpenTelemetry** (SDK 1.43.0 + 8 instrumentations incl. `opentelemetry-instrumentation-anthropic`)
  — fully lazy; `init_otel` returns immediately on empty `OTEL_EXPORTER_OTLP_ENDPOINT`.
- **Structured logging** — `JsonLogFormatter`, `RequestIDMiddleware` + `RequestIDLogFilter`,
  request-id propagation into Celery, `log_event()` dual-rail (log line + `event_log` DB row),
  `redact.py`, `verbose.py`. `IMAGE_SHA` stamped into `.env` on every deploy.

**Actually watched in production:**
- **Cloudflare Health Checks** on `/health` — the only continuous uptime signal
  (`docs/DEPLOYMENT.md:145-172`). Set up manually in the CF dashboard; **no config-as-code in the
  repo.**
- Deploy-time smoke (localhost `/health` + critical-journey harness), per deploy.
- `scripts/doctor.py` (526 lines) — env/secret validator + live probes for Postgres, Redis,
  Anthropic, Voyage, Deepgram, R2, Stripe; deploy preflight and manual.
- `scripts/live_smoke.py`, `llm_harness.py`, `drills.py`, `clip_audit.py`, `r2_inspect.py` — all
  manual.

**Gaps** (self-reported in `docs/GO_LIVE.md:77-85`):
- *"Are logs/metrics/traces + error tracking live?"* → **CODE-GREEN, external verify pending.**
  Sentry/OTel secrets are guarded in `deploy.yml:209-212`; until those GitHub secrets are set the
  sync is a no-op and **both stay dormant**. Nothing in the repo proves they are set.
- *"Independent status page + uptime monitoring?"* → **OPEN**, re-opened 2026-08-13.
- *"Will we hear about cost blowouts?"* → **OPEN**. `docs/dashboards/llm-cost-panel.json` is a
  single Grafana **panel** spec. Grep for `alert` across `docs/dashboards/` returns **zero hits —
  there are no alert rules anywhere in the repo.**
- `health-check.yml`'s schedule "silently died 2026-06-17 and nobody noticed".
- **No paging/on-call.** `docs/INCIDENT_RESPONSE.md` is an explicit **SOLO-RESPONDER** model.
  `health-check.yml:71-79` contains the placeholder *"In production, replace this step with your
  alerting integration."*

---

## 7. Environment / config management

- **`config.py` — 1,208 lines, ~213 settings** on one `Settings(BaseSettings)` class,
  `extra="ignore"`. Contains a substantial `_validate` block that hard-fails or auto-degrades in
  production (disabling `/metrics` without `METRICS_TOKEN`, rejecting `STORAGE_BACKEND=local`).
- **`.env.example` — 434 lines, 212 `KEY=` entries**, heavily annotated.
- **`flags.py` — 183 lines**, 4 runtime kill switches (`llm_generation`, `youtube_publish`,
  `render_intake`, `signup`). Two-tier: DB `feature_flags` row (flipped via `scripts/flags.py`, no
  deploy) → env default `FLAG_<KEY>_ENABLED` → hard default `True`. 30 s in-process TTL cache.
  **Fail-open** by design. Every flip audited on both telemetry rails with actor + reason.
- **Drift controls that exist:** `doctor.py` validates format/presence/live-reachability per secret
  as a deploy preflight; `deploy.yml` pins `STORAGE_BACKEND=r2` authoritatively and adds `LOG_LEVEL`
  if absent; `tests/test_ci_config.py::test_staging_prod_compose_parity`; `tests/test_backup_config.py`.

**Gaps:**
- **No test asserts `.env.example` covers `config.py`.** Grepping `tests/` for `env.example` returns
  one hit, in `tests/test_beat_ha.py`. The `CLAUDE.md:126` checklist item *"All new config in
  `.env.example` with description"* is a **manual human check only** — 213 settings vs 212
  documented entries is coincidence, not enforcement.
- **Infra secrets are VM-managed and out of band.** `deploy.yml:180-182`: `DATABASE_URL`,
  `REDIS_URL`, `JWT_SECRET_KEY`, `TOKEN_ENCRYPTION_KEY`, OAuth creds "remain VM-managed and are
  intentionally not listed here" — they live only in `/opt/autoclip/.env`, edited by hand. No secret
  store, no versioning, no drift detection.
- **Three environments, three config mechanisms:** local `.env`; staging =
  `docker-compose.staging.yml` reading the *same* `/opt/autoclip/.env`; prod =
  `docker-compose.prod.yml` + synced `.env`. `render.yaml` is a fourth, unused, encoding different
  values (verbose PII logging on).
- A stray committed `.env` (712 bytes) sits at the repo root alongside `.env.example`.

---

## 8. Consolidated gap list — stages with no gate, no signal, or human-only checks

| Stage | Reality |
|---|---|
| **Commit** | No gate at all. No pre-commit hook, no `.pre-commit-config.yaml`. |
| **Push** | `pre-push` → `ci_local.sh --fast`. Bypassable (`--no-verify`, `CI_LOCAL_SKIP=1`). Every gate degrades to a silent skip + exit 0 when Redis/node_modules/tooling is absent. `ruff format` and `eslint` are changed-files ratchets over acknowledged debt. |
| **Code review** | **None.** `required_pull_request_reviews: null`. All human review is the maintainer reviewing AI-generated code in-session; `/code-review`, `/simplify` are advisory skills, not gates. |
| **PR gate** | 8 required checks. **Frontend unit tests (92 files), migration lint + downgrade round-trip, visual regression, and mutation testing are all non-blocking.** `eval/clip-quality` reports green without running on most PRs. `static-gates` can pass vacuously on tool-skip. mypy/bandit skip ~11 modules and all of `tests/`. |
| **Coverage** | Global floor 83%, per-module floors on 5 modules only (`clip_engine` 91, `preference` 88, `crypto`/`limiter`/`auth` 99), diff-cover `--fail-under=80`. **Zero frontend coverage measurement.** |
| **Merge → prod** | Fully automatic. **No human approval step, no deploy window, no canary, no percentage rollout.** GitHub `environment: production` exists but no required reviewers are configured. |
| **Pre-prod verification** | Staging gate is real and data-bearing — the strongest gate in the pipeline. `docs/DEPLOYMENT.md:121-141` "Gate 2 — Manual smoke test" is a 6-item human checklist that nothing enforces. |
| **Post-deploy** | `/health` + `llm_harness --flow core`; ~50 s window of broken image before rollback. Auto-rollback exists and correctly still exits non-zero. |
| **Production monitoring** | One Cloudflare Health Check on `/health`, configured by hand outside the repo. Sentry + OTel are code-complete but **unverified as live**. **Zero alert rules in the repo.** No status page. Solo-responder, no paging. |
| **Config drift** | Prevented only by `doctor.py` at deploy time and a manual `CLAUDE.md` checklist item. No `.env.example`↔`config.py` parity test. Infra secrets hand-edited on the VM. |

**Habits worth naming as strengths:** `tests/test_ci_config.py` gating the CI config itself;
anti-hollowing guards on the eval harness (count floor + skip-marker scan); the quarantine marker
with a documented lifecycle and a hard prohibition on rerun-as-gate; `docs/DECISIONS.md` and
`docs/OFF_COURSE_BUGS.md` recording *why* each gate has the exact shape it has, including several
past vacuous-pass bugs.
