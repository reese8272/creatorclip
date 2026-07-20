# deploy_ci — assessed 2026-07-20 (post-fix)

Slice: `.github/workflows/` (9 workflows), `docker-compose{,.prod,.staging}.yml`, `Dockerfile`,
`render.yaml`, `deploy/charts/creatorclip/`, `scripts/` (operational), `.env.example`.

Re-assessment after two fix waves merged since the morning run (diff `ca3305c..e92b93a`,
PRs #55/#56/#57). Every prior finding re-verified against HEAD; new-regression sweep done on
the full diff of `.github/`, `scripts/`, `docker-compose.staging.yml`, `.env.example`.

## Prior findings — disposition

- **[SEV1] prod-VM self-hosted runner executing PR code — FIXED (Issue 360, commit fe777a2;
  DECISIONS.md:10697 entry written).** All 12 `ci.yml` jobs and `mutation.yml` now
  `runs-on: ubuntu-latest`. The self-hosted runner is reserved for deploy-track workflows
  only, and all three trigger exclusively from trusted refs: `docker-publish.yml`
  (`push: main` + release), `deploy.yml` (`workflow_run` on main + dispatch),
  `staging-drills.yml` (dispatch-only). `scripts/setup-runner.sh:17-33` now documents the
  security boundary ("never point a pull_request-triggered workflow at this runner") and
  dropped the CI dep pre-install. Live evidence: three GitHub-hosted PR runs passed today
  (PRs #56, #57) and Deploy-to-production succeeded twice — the split works in practice.
  Residual (documented in the ci.yml header): hosted-minutes billing is a dependency again;
  a billing fast-fail must be fixed by paying, not by moving jobs back.
- **[SEV2] staging stack borrowing prod .env values — FIXED with documented residual
  (Issue 361).** `docker-compose.staging.yml:79-94,122-130` now override on both app and
  worker: `STORAGE_BACKEND: local`, `SENTRY_DSN: ""`, `OTEL_EXPORTER_OTLP_ENDPOINT: ""`,
  and `STRIPE_SECRET_KEY`/`STRIPE_WEBHOOK_SECRET` from optional `STAGING_STRIPE_*`
  (test-mode) vars, both added to `.env.example:191-192`. `TOKEN_ENCRYPTION_KEY` is
  deliberately NOT overridden — the inline comment explains the data-bearing
  `staging_postgres_data` volume holds tokens encrypted under the prod key, so a swap would
  break every persisted row. Accepted residual: staging shares the prod Fernet key; the
  compensating control is that the staging stack never runs untrusted code (dispatch/deploy
  refs only, per the SEV1 fix above).
- **[SEV2] deploy.sh `SendEnv=GHCR_TOKEN` silently dropped — FIXED (commit 1e60ded).**
  `scripts/deploy.sh:96-106`: the token now travels over stdin into
  `docker login --password-stdin` in a dedicated pre-step; the heredoc no longer references
  `$GHCR_TOKEN` at all. `GHCR_TOKEN` presence is validated up front (deploy.sh:29-32).
- **[SEV2] deploy.sh false "mirrors deploy.yml exactly" + no rollback + pre-smoke prune —
  FIXED (commit 1e60ded).** Header (deploy.sh:13-17) now states honestly what is and isn't
  mirrored ("NOT an exact mirror: no staging-parity gate, no secret sync; prefer Actions").
  The heredoc now captures `PREV_IMAGE` by RepoDigest before pulling, defines
  `_rollback_and_fail` (pull digest → re-tag `:rollback` → `IMAGE_TAG=rollback` compose up →
  exit 1) invoked on both smoke failures, and `docker image prune -f` moved to AFTER the
  smoke tests — a failed manual deploy can no longer delete its own rollback target.
- **[SEV2] rotate_token_key.py keys on argv — FIXED (commit fe777a2 + 141d17b).** Keys now
  read from `OLD_TOKEN_ENCRYPTION_KEY`/`NEW_TOKEN_ENCRYPTION_KEY` env vars with
  `getpass.getpass()` interactive fallback (scripts/rotate_token_key.py:75-85); argv flags
  removed entirely; `docs/RUNBOOKS.md:194-226` invocations (forward + reverse rotation)
  updated to match.
- **[SEV2] backup_redis.sh sourcing/executing the prod .env — FIXED (commit fe777a2).**
  `scripts/backup_redis.sh:31-45` replaces `set -a && source` with the no-exec `read_env`
  parser (duplicated verbatim from backup_pg.sh, with a comment saying so), exports only
  `BACKUP_ENCRYPTION_KEY` for `openssl -pass env:`, and adds the missing
  `BACKUP_R2_BUCKET != R2_BUCKET` 3-2-1 guard (lines 57-63).
- **[SEV2] activate-rls.yml `.venv/bin/alembic` — FIXED.** All three occurrences are bare
  `alembic` (activate-rls.yml:117,120,131). The password-interpolation secondary is
  mitigated the other way round: a fail-fast charset guard (lines 71-82) rejects passwords
  containing `' | & \` or whitespace before SSH, names-only output. Acceptable — the values
  are self-controlled secrets; injection is now unreachable.
- **[SEV2] staging-drills against `:latest` — FIXED (commit 1e60ded).**
  `staging-drills.yml:47-63` resolves the running prod app container's RepoDigest (same
  inspect as deploy.yml's PREV_IMAGE capture) and passes it as `STAGING_IMAGE`, falling back
  to `:latest` only with an explicit `::warning` when prod isn't running. Runs with
  `working-directory: /opt/autoclip` so the prod-compose `ps` resolves.
- **[cleanup] OFF_COURSE_BUGS rollback entry stale — FIXED.** `docs/OFF_COURSE_BUGS.md:71`
  now reads "✅ Fixed (verified 2026-07-20 assessment...)".

## Findings (still open — all carried-over cleanups; no prior SEV1/SEV2 remains)

- [cleanup] scripts/eval_efficacy.py, scripts/repro_render.py, scripts/repro_ingest_render.py
  — still the only DB-importing scripts without the `sys.path.insert(0, <repo root>)` guard
  that flags.py:24/doctor.py:35 carry; bare `python3 scripts/...` invocations ImportError
  anywhere PYTHONPATH≠/app (same class as the drills flags-shadow). Note f29a2be touched
  eval_efficacy.py without adding it. | fix: add the 2-line guard.
- [cleanup] scripts/drills.py:91,104,126,158 — still reaches into `flags._reset_cache()`
  (private API); and staging-drills.yml:8-11 still claims "the drills restore all state in
  `finally` blocks" while `drill_rate_limit` (drills.py:163-186) leaves the seeded creator's
  daily render buckets consumed (staging-only, self-expiring). | fix: export a public
  `flags.reset_cache()` (or comment the accepted private use) and amend the workflow comment
  to name the rate-limit exception.
- [cleanup] docker-compose.prod.yml:101 — `cloudflare/cloudflared:latest` still the last
  floating third-party tag in prod (inline comment already says to pin). | fix: pin to a
  dated release.
- [cleanup] Dockerfile:57-84 — runtime image still has no `USER` (app/worker/beat run as
  root in-container); Anton TTF (raw.githubusercontent `main`) and BlazeFace model
  (`.../latest/...`) still fetched from mutable URLs at build. | fix: non-root USER stage;
  pin both assets by commit/version URL with checksum verification.
- [cleanup] .env.example — `CC_BASE_URL`/`CC_JWT_SECRET`/`CC_CREATOR_ID` (required by both
  deploy paths' in-container smoke) and `BACKUP_HEALTHCHECK_URL` (read by both backup
  scripts) still undocumented; `IMAGE_TAG` deserves a one-line note. (`STAGING_STRIPE_*` and
  the Opus rates WERE added this wave.) | fix: add the entries with descriptions.

## New-regression sweep of ca3305c..HEAD (deploy/CI paths) — none found

- ci.yml runner move is complete (no job left on `self-hosted`); `permissions: contents:
  read` posture unchanged; the `@visual` grep-invert in the gating Playwright job is correct
  (the non-gating `visual` job owns those tests, Issue 272).
- deploy.sh: bash-only `2> >(grep ...)` stderr filter is safe under the `#!/usr/bin/env
  bash` shebang and preserves ssh's exit status; login credential persists in remote
  `~/.docker/config.json` (same posture as deploy.yml's runner login).
- staging-drills digest pin: a `ghcr.io/...@sha256:` ref is valid for `docker pull` and for
  the `image:` interpolation slot in docker-compose.staging.yml (`${STAGING_IMAGE:-...}`).
- backup_redis `read_env` uses guarded indirect expansion (`${!key:-}`) — safe under
  `set -u`; validation prints names only.
- rotate_token_key: `getpass` in a non-TTY (CI) context raises rather than hangs —
  acceptable for an operator-run script.
- activate-rls charset guard glob patterns are POSIX-correct; values never echoed.

## Verified-good (unchanged from the morning run, re-spot-checked)

- deploy.yml secret sync (env-passed, awk exact-key, `--password-stdin`); staging-parity
  gate (sha-pinned image, in-container alembic current==heads, data-bearing volume kept via
  `stop`); migration gates (squawk per-file `down:rev` with pipefail, downgrade round-trip
  byte-diff, check_downgrades allowlist self-check); backup_pg.sh (streamed, `-pass env:`,
  separate-bucket guard); `python -m scripts.drills` module-shadow fix; render.yaml secrets
  all `sync: false`; Helm chart placeholder-only (parked, Issue 275).

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — no change; rotation single-transaction, backups streamed |
| 2 Concurrency & scale | ok — hosted CI removes the single-runner serialization for PRs; deploy-track concurrency groups unchanged |
| 3 Security & compliance | ok — all 6 prior findings (incl. the SEV1) verified fixed; TOKEN_ENCRYPTION_KEY staging residual documented in-file |
| 4 Clip-quality | n/a (deploy module) |
| 5 Anthropic SDK | n/a (no LLM calls) |
| 6 Cleanliness & typing | 2 findings (missing sys.path guards; private `_reset_cache` + stale workflow comment) |
| 7 Error handling / API | n/a (no routers; scripts exit non-zero correctly) |
| 8 Config & paths | 1 finding (CC_*/BACKUP_HEALTHCHECK_URL/IMAGE_TAG absent from .env.example) |

## Module verdict

clean — all eight SEV1/SEV2 findings from the morning run are verified fixed at HEAD (runner
split live-proven by today's hosted PR runs and two successful deploys; staging env bleed
guarded with the TOKEN_ENCRYPTION_KEY residual explicitly documented; deploy.sh now carries
the real rollback path); only five bounded cleanups remain.
