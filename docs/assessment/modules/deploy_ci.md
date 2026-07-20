# deploy_ci — assessed 2026-07-20

Slice: `.github/workflows/` (9 workflows), `docker-compose{,.prod,.staging}.yml`, `Dockerfile`,
`render.yaml`, `deploy/charts/creatorclip/`, `scripts/` (operational), `.env.example`.

## Method notes (this run)

- **Off-course bug verification (2026-07-02 SEV2 "rollback is a no-op image swap"): FIXED at
  HEAD.** `docker-compose.prod.yml:7,36,55` now interpolate
  `image: ghcr.io/reese8272/creatorclip:${IMAGE_TAG:-latest}` on app/worker/beat, and
  `deploy.yml:321-338` (`_rollback_and_fail`) re-tags the captured `PREV_IMAGE` digest as
  `:rollback` and rolls out with `IMAGE_TAG=rollback`. The fix landed with Issue 298. The
  `docs/OFF_COURSE_BUGS.md:71` entry is still marked "📋 Open" — stale, see cleanup below.
  Residual (accepted roll-forward posture, not re-filed): rollback swaps the image but not the
  schema; old code runs against the migrated DB. Mitigated by the squawk gate + downgrade
  round-trip lint + the data-bearing staging gate.
- **Module-shadow class sweep** (root `flags.py` vs `scripts/flags.py`, cause of ca3305c):
  checked every `scripts/*.py` name against root modules — `flags` is the only collision, and
  both importers are guarded (`drills.py` documented -m-only + workflow uses
  `python -m scripts.drills`; `scripts/flags.py:24` inserts repo root ahead of `scripts/`).
  The *missing-root-on-sys.path* sibling of this class does exist in three scripts (cleanup
  below).
- Diff-scoped scrutiny of `f70a857..HEAD` churn (deploy.yml staging gate, staging-drills.yml,
  migration-lint downgrade round-trip, drills.py, backup_redis.sh, deploy.sh) done file-by-file.

## Findings

- [SEV1] scripts/setup-runner.sh:58,96 + .github/workflows/ci.yml:33 — the self-hosted runner
  lives ON the prod VM, is in the `docker` group (root-equivalent host control), and owns
  `/opt/autoclip` including the prod `.env` (all secrets), while `ci.yml` triggers on
  `pull_request`. Any code that executes during a PR job — including a malicious transitive
  dependency pulled by `npm ci` / `pip install -r requirements.txt` — can read every prod
  secret and control the prod containers. Bounded today (private solo repo, self-authored
  PRs), but this is a full-prod-compromise supply-chain path with no isolation. | fix: keep the
  prod-VM runner for deploy-track workflows only (deploy.yml, docker-publish.yml,
  staging-drills.yml) and move PR-triggered CI to a second runner OFF the prod VM (already the
  documented second-runner TODO), or at minimum a distinct runner user with no docker-group
  membership and no read access to `/opt/autoclip/.env`. The 2026-06-23 hybrid-CI DECISIONS
  entry weighs billing, not this blast radius — record the residual there either way.
- [SEV2] docker-compose.staging.yml:74,103 — staging app/worker load `env_file: .env`, which is
  the PROD `/opt/autoclip/.env` when run by deploy.yml's gate and staging-drills.yml.
  DATABASE_URL/REDIS_URL are overridden (DB/Redis isolation is real), but everything else
  bleeds: `STORAGE_BACKEND=r2` + prod R2 creds (staging renders/uploads write into the PROD
  media bucket), live `STRIPE_SECRET_KEY`, prod `SENTRY_DSN`/OTel (staging errors pollute prod
  telemetry), prod `TOKEN_ENCRYPTION_KEY`. `scripts/live_smoke.py:36-39` already defines an
  `.env.staging` convention that the compose file ignores. | fix: add explicit `environment:`
  overrides in docker-compose.staging.yml — `STORAGE_BACKEND: local` (or a dedicated
  `creatorclip-staging` bucket), `SENTRY_DSN: ""`, `OTEL_EXPORTER_OTLP_ENDPOINT: ""`, test-mode
  Stripe key — or point `env_file` at `/opt/autoclip/.env.staging`.
- [SEV2] scripts/deploy.sh:31,45 — `ssh -o SendEnv=GHCR_TOKEN` only transmits the variable if
  the VM's sshd has `AcceptEnv GHCR_TOKEN` (default config accepts only `LANG LC_*`); the
  remote heredoc runs `set -euo pipefail`, so an untransmitted `${GHCR_TOKEN}` aborts at line 50
  — the documented manual-fallback deploy most likely fails at GHCR login
  (needs-runtime-confirmation on the VM's sshd_config). | fix: stop relying on SendEnv — pipe
  the token over stdin, e.g. `printf '%s' "$GHCR_TOKEN" | ssh ... 'cat > /tmp/.ghcr && docker
  login ghcr.io -u reese8272 --password-stdin < /tmp/.ghcr && rm /tmp/.ghcr; ...'` (or restructure
  so login reads the piped stdin directly).
- [SEV2] scripts/deploy.sh:13,94 — header claims it "mirrors the GH Actions deploy.yml exactly";
  it doesn't: no staging gate, no PREV_IMAGE capture, no auto-rollback, and `docker image prune
  -f` runs BEFORE the smoke test (deploy.yml:379-383 deliberately moved prune after smoke to
  preserve the rollback target). A failed manual deploy prunes its own rollback image and has
  no recovery path. | fix: port the PREV_IMAGE capture + `:rollback` re-tag + post-smoke prune
  from deploy.yml into the heredoc; until then, correct the header so an operator under
  incident pressure knows the guarantees differ.
- [SEV2] scripts/rotate_token_key.py:73-74 — `--old-key`/`--new-key` put both Fernet keys on
  argv, visible in `ps` on the shared VM and persisted in shell history; backup_pg.sh:12-15
  exists specifically to avoid this pattern for its passphrase. | fix: read
  `OLD_TOKEN_ENCRYPTION_KEY`/`NEW_TOKEN_ENCRYPTION_KEY` from the environment (or
  `getpass.getpass()` prompts) and update the RUNBOOKS invocation.
- [SEV2] scripts/backup_redis.sh:31-34 — `set -a && source "$ENV_FILE"` EXECUTES the prod .env
  as shell (a value containing `$(...)` runs code) and exports every secret into the process
  environment; backup_pg.sh:50-56 built the no-exec `read_env` helper to prevent exactly this,
  and backup_redis.sh (same Issue family, 288) regressed it. Also missing backup_pg.sh:70-73's
  `BACKUP_R2_BUCKET != R2_BUCKET` guard. | fix: extract `read_env` (and the bucket guard) into
  a shared sourced snippet or duplicate them verbatim; drop the `source`.
- [SEV2] .github/workflows/activate-rls.yml:106,109,119 — the sanity step calls
  `.venv/bin/alembic` inside the app container, but the image has no `.venv` (deps live in
  `/root/.local`; deploy.yml correctly calls bare `alembic`). A re-run of this
  idempotent-by-design workflow aborts at step 0 with "history does not mention
  0010_rls_policies". Latent (activation already done 2026-06-30), but this is the documented
  re-activation/verification tool. | fix: `.venv/bin/alembic` → `alembic` (3 occurrences).
  Secondary (cleanup-grade): lines 131-132/192-210 interpolate `APP_PW`/`MIGRATE_PW` into SQL
  and `sed` replacement text — a password containing `'`, `|`, or `&` breaks or injects; use
  `psql -v pw=...` variables and a charset check.
- [SEV2] .github/workflows/staging-drills.yml:47-53 — drills run against `:latest` on the claim
  ":latest == what prod runs", which is false in exactly the scenario drills exist for: after an
  auto-rollback, prod runs `:rollback` while `:latest` IS the bad image — the drills would then
  green-stamp behavior prod doesn't run; a concurrent main-push can also swap `:latest`
  mid-workflow. | fix: resolve the prod app container's RepoDigest (same `docker inspect
  --format='{{index .RepoDigests 0}}'` as deploy.yml:270) and pass that as STAGING_IMAGE,
  falling back to `:latest` only when prod isn't running.
- [cleanup] docs/OFF_COURSE_BUGS.md:71 — the 2026-07-02 rollback entry is still "📋 Open" but
  the fix shipped (see Method notes) | fix: mark ✅ Fixed 2026-07-02 (Issue 298), citing
  docker-compose.prod.yml `${IMAGE_TAG:-latest}` + the `:rollback` re-tag.
- [cleanup] scripts/eval_efficacy.py, scripts/repro_render.py:17, scripts/repro_ingest_render.py
  — the only DB-importing scripts WITHOUT the `sys.path.insert(0, <repo root>)` guard that
  flags.py/doctor.py/rotate_token_key.py/reapply_erasures.py/llm_e2e.py/live_smoke.py all carry;
  their documented bare invocations (`python3 scripts/eval_efficacy.py`) ImportError anywhere
  PYTHONPATH≠/app (same failure class as the drills flags-shadow) | fix: add the 2-line guard.
- [cleanup] scripts/drills.py:92,104,127,158 — reaches into `flags._reset_cache()` (private
  API); and staging-drills.yml:8-11 claims "the drills restore all state in finally blocks" but
  drill_rate_limit (scripts/drills.py:163-186) leaves the seeded creator's daily render buckets
  consumed (staging-only, self-expiring) | fix: export a public `flags.reset_cache()` (or accept
  the private use with a comment) and amend the workflow comment to name the rate-limit
  exception.
- [cleanup] docker-compose.prod.yml:101 — `cloudflare/cloudflared:latest` is the last floating
  third-party tag in prod (autoheal and pgbouncer are pinned; the inline comment already says
  to pin) | fix: pin to a dated release (e.g. `cloudflare/cloudflared:2026.x.y`).
- [cleanup] Dockerfile:57-84 — runtime image has no `USER`; app/worker/beat run as root in the
  container (standard beta hardening gap; `pip install --user` under /root needs a small move
  to make a non-root user work). Also Anton TTF (raw.githubusercontent `main`) and the
  BlazeFace model (`.../latest/...`) are fetched from mutable URLs at build — non-reproducible
  layers, failure-tolerated but content-unpinned | fix: add a non-root USER stage; pin both
  assets by commit/version URL (checksum-verify the model).
- [cleanup] .env.example — `CC_BASE_URL`/`CC_JWT_SECRET`/`CC_CREATOR_ID` (required in the prod
  .env by scripts/deploy.sh:112-117's in-container smoke) and `BACKUP_HEALTHCHECK_URL`
  (read by both backup scripts) are undocumented; `IMAGE_TAG` (deploy-time compose interpolation)
  deserves a one-line note | fix: add the four entries with descriptions.

## Verified-good (load-bearing claims traced, no finding)

- deploy.yml secret sync (lines 192-257): values passed via `env:` not interpolation, awk
  exact-key rewrite, guarded no-blank semantics, nothing echoed; GHCR login via
  `--password-stdin` everywhere.
- Staging-parity gate (Issue 298): sha-pinned image (never `:latest`), in-container alembic with
  current==heads assertion, persistent data-bearing volume kept via `stop` not `down`.
- Migration gates: squawk per-file `down:rev` render with `set -o pipefail` (off-by-one fixed),
  online downgrade round-trip with byte-diffed schema dumps, check_downgrades.py allowlist with
  staleness self-check — coherent and hard to hollow out.
- backup_pg.sh: fully streamed, creds container-side, `-pass env:`, separate-bucket guard,
  server-side lifecycle retention — the strongest file in the slice.
- `python -m scripts.drills` fix (ca3305c) is correct: -m keeps cwd (/app) at sys.path[0], so
  `import flags` resolves to the root module; drills' DB/Redis writes are confined to the
  ccstage stack's own postgres/redis services.
- ci.yml / all workflows: `permissions: contents: read` default (least privilege), escalated
  per-job only where needed (eval statuses, publish packages).
- render.yaml: secrets all `sync: false`, no values committed; VERBOSE_LOGGING=true is the
  documented 2026-06-29 beta DECISION with the launch-off note inline. Helm chart holds only
  placeholders; parked behind Issue 275 (descoped for v1).

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — sessions via context managers (rotate/drills/flags CLI); backups streamed, no temp plaintext; single-transaction rotation |
| 2 Concurrency & scale | ok — single-runner serialization is documented + mitigated (concurrency groups); RedBeat confirmed in worker config (prod compose beat is safe without `-S`) |
| 3 Security & compliance | 6 findings (SEV1 runner blast radius; staging env bleed; SendEnv token; argv keys; sourced .env; RLS workflow) |
| 4 Clip-quality | n/a (deploy module) |
| 5 Anthropic SDK | n/a (no LLM calls; doctor probes models.list only) |
| 6 Cleanliness & typing | 3 findings (private `_reset_cache`, missing sys.path guards, backup_redis DRY regression folded into its SEV2) |
| 7 Error handling / API | n/a (no routers; scripts exit non-zero correctly, secrets scrubbed from doctor output) |
| 8 Config & paths | 2 findings (stale `.venv/bin/alembic` path; CC_*/BACKUP_HEALTHCHECK_URL absent from .env.example) |

## Module verdict

NEEDS-WORK — the deploy pipeline's core safety mechanisms (staging gate, rollback, migration
lint, backups) are genuinely strong and the flagged off-course rollback bug is verified fixed,
but the prod-VM self-hosted runner running PR code with docker-group access to all prod secrets
is an unmitigated full-compromise path, and the staging stack silently borrows prod's live
Stripe/R2/Sentry credentials.
