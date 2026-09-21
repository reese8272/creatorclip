#!/usr/bin/env bash
# ui_audit.sh — the hands-off, terminal-only UI verification harness (Issue 542, Lane L33).
#
#   scripts/ui_audit.sh [--rungs mocked,local] [--prod]
#
# Rungs:
#   mocked — Playwright against the Vite dev server with the network-boundary
#            mock (frontend/playwright.config.ts), minus @visual (baselines are
#            CI-only: WSL2/macOS font AA causes false positives).
#   local  — Playwright against the REAL app: uvicorn serving frontend/dist at
#            http://localhost:8000/app/, real pg16+pgvector (creatorclip_e2e),
#            seeded data (seed_staging.py --profile ui), minted cc_session
#            storageState (scripts/mint_storage_state.py). No Celery worker —
#            async states are asserted `queued`, never `done`.
#   --prod — ADDITIONALLY runs the live-site audit (playwright.config.prod.ts)
#            after a storage-state freshness precheck. Never implicit.
#
# Artifacts: frontend/e2e/.audit/report.json + screens/<rung>/<route>-<viewport>.png
# (see frontend/e2e/merge-report.mjs and the /ui-check skill). Exit non-zero iff
# any requested rung failed.

set -u -o pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

RUNGS="mocked,local"
RUN_PROD=0
while [ $# -gt 0 ]; do
  case "$1" in
    --rungs) RUNGS="$2"; shift 2 ;;
    --rungs=*) RUNGS="${1#--rungs=}"; shift ;;
    --prod) RUN_PROD=1; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

# ── Env checks (each failure names its remediation) ───────────────────────────
bold "ui_audit — env checks"

[ -d "$REPO_ROOT/.venv/bin" ] || die ".venv missing — run scripts/dev_session_setup.sh"
export PATH="$REPO_ROOT/.venv/bin:$PATH"

# Node 22 pin (mirrors ci_local.sh — node 26's jsdom/tooling breaks the frontend).
if [ -f "$REPO_ROOT/.nvmrc" ]; then
  NVM_NODE="$HOME/.nvm/versions/node/v$(tr -d 'v[:space:]' < "$REPO_ROOT/.nvmrc")/bin"
  [ -d "$NVM_NODE" ] && export PATH="$NVM_NODE:$PATH"
fi
command -v node >/dev/null || die "node not found — install node $(cat .nvmrc) via nvm"
ok "node $(node --version)"

command -v redis-cli >/dev/null && redis-cli ping >/dev/null 2>&1 \
  || die "Redis not reachable — start it: redis-server --daemonize yes --save '' --appendonly no"
ok "redis"

ls "$HOME/.cache/ms-playwright" 2>/dev/null | grep -q chromium \
  || die "Playwright chromium missing — cd frontend && npx playwright install chromium"
ok "playwright chromium"

if [ ! -f "$REPO_ROOT/frontend/dist/index.html" ]; then
  bold "frontend/dist missing — building"
  npm --prefix frontend run build || die "vite build failed"
fi
ok "frontend/dist"

AUDIT_DIR="$REPO_ROOT/frontend/e2e/.audit"
rm -f "$AUDIT_DIR/events.ndjson" "$AUDIT_DIR"/pw-*.json "$AUDIT_DIR/report.json"
mkdir -p "$AUDIT_DIR"

UVICORN_PID=""
cleanup() { [ -n "$UVICORN_PID" ] && kill "$UVICORN_PID" 2>/dev/null; }
trap cleanup EXIT

E2E_DSN="${CC_E2E_DATABASE_URL:-postgresql+psycopg://creatorclip:dev_password@localhost:5432/creatorclip_e2e}"
PG_START_HINT="start pg16: /home/linuxbrew/.linuxbrew/opt/postgresql@16/bin/pg_ctl -D /home/linuxbrew/.linuxbrew/var/postgresql@16 start (or docker compose up -d postgres)"

declare -A RUNG_EXIT
run_rung() { # $1=rung  $2...=command
  local rung="$1"; shift
  bold "rung: $rung"
  ( cd frontend && PLAYWRIGHT_JSON_OUTPUT_NAME="e2e/.audit/pw-$rung.json" "$@" )
  RUNG_EXIT[$rung]=$?
  [ "${RUNG_EXIT[$rung]}" -eq 0 ] && ok "$rung passed" || printf '  \033[31m✗\033[0m %s failed (exit %s)\n' "$rung" "${RUNG_EXIT[$rung]}"
}

# ── local rung prep: DB → seed → mint → boot ─────────────────────────────────
case ",$RUNGS," in *,local,*)
  bold "local rung prep"
  .venv/bin/python - "$E2E_DSN" <<'PY' || die "pg16/creatorclip_e2e not reachable — $PG_START_HINT"
import sys
import psycopg
url = sys.argv[1].replace("postgresql+psycopg://", "postgresql://")
with psycopg.connect(url, connect_timeout=3) as c:
    ext = c.execute("SELECT count(*) FROM pg_extension WHERE extname='vector'").fetchone()[0]
sys.exit(0 if ext else 1)
PY
  ok "pg16 + vector"

  # Stable per-checkout secrets shared by the mint script and the server, so a
  # storageState survives re-runs. Never committed (frontend/e2e/.auth is ignored).
  AUTH_DIR="$REPO_ROOT/frontend/e2e/.auth"
  mkdir -p "$AUTH_DIR"
  [ -f "$AUTH_DIR/e2e_jwt_secret" ] || openssl rand -base64 48 | tr -d '\n' > "$AUTH_DIR/e2e_jwt_secret"
  [ -f "$AUTH_DIR/e2e_fernet_key" ] || .venv/bin/python -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode(),end="")' > "$AUTH_DIR/e2e_fernet_key"
  chmod 600 "$AUTH_DIR/e2e_jwt_secret" "$AUTH_DIR/e2e_fernet_key"
  E2E_JWT_SECRET="$(cat "$AUTH_DIR/e2e_jwt_secret")"
  E2E_FERNET_KEY="$(cat "$AUTH_DIR/e2e_fernet_key")"

  E2E_ENV=(
    "ENV=development"
    "DATABASE_URL=$E2E_DSN"
    "REDIS_URL=${REDIS_URL:-redis://localhost:6379/0}"
    "STORAGE_BACKEND=local"
    "LOCAL_MEDIA_DIR=$AUDIT_DIR/media"
    "NOTIFY_BACKEND=console"
    "GOOGLE_OAUTH_CLIENT_ID=e2e-stub"
    "GOOGLE_OAUTH_CLIENT_SECRET=e2e-stub"
    "OAUTH_REDIRECT_URI=http://localhost:8000/auth/callback"
    "ALLOWED_ORIGINS=http://localhost:8000"
    "ANTHROPIC_API_KEY=e2e-stub"
    "JWT_SECRET_KEY=$E2E_JWT_SECRET"
    "TOKEN_ENCRYPTION_KEY=$E2E_FERNET_KEY"
    "LOG_DIR="
  )

  env "${E2E_ENV[@]}" .venv/bin/alembic upgrade head >/dev/null || die "alembic upgrade head failed on $E2E_DSN"
  ok "migrations at head"

  env "${E2E_ENV[@]}" .venv/bin/python tests/perf/seed_staging.py --profile ui >/dev/null \
    || die "seed_staging.py --profile ui failed"
  ok "ui seed"

  JWT_SECRET_KEY="$E2E_JWT_SECRET" .venv/bin/python scripts/mint_storage_state.py \
    || die "mint_storage_state.py failed"
  ok "storageState minted"

  env "${E2E_ENV[@]}" .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 \
    >"$AUDIT_DIR/uvicorn.log" 2>&1 &
  UVICORN_PID=$!
  for _ in $(seq 1 60); do
    curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1 && break
    kill -0 "$UVICORN_PID" 2>/dev/null || die "uvicorn died on boot — see $AUDIT_DIR/uvicorn.log"
    sleep 1
  done
  curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1 || die "uvicorn never became healthy — see $AUDIT_DIR/uvicorn.log"
  ok "app healthy at http://127.0.0.1:8000"
;; esac

# ── Run the rungs ─────────────────────────────────────────────────────────────
case ",$RUNGS," in *,mocked,*)
  run_rung mocked npx playwright test --grep-invert @visual --reporter=list,json
;; esac

case ",$RUNGS," in *,local,*)
  run_rung local npx playwright test --config=playwright.config.local.ts --reporter=list,json
;; esac

if [ "$RUN_PROD" -eq 1 ]; then
  bold "prod precheck: storage-state freshness"
  node - <<'JS' || die "prod auth stale/missing — re-run: npm --prefix frontend run test:prod:auth (or test:prod:auth:cookie)"
const fs = require('node:fs')
const path = 'frontend/e2e/.auth/prod.json'
if (!fs.existsSync(path)) process.exit(1)
const state = JSON.parse(fs.readFileSync(path, 'utf8'))
const cookie = (state.cookies || []).find((c) => c.name === 'cc_session')
if (!cookie) process.exit(1)
fetch('https://autoclip.studio/auth/me', { headers: { cookie: `cc_session=${cookie.value}` } })
  .then((r) => process.exit(r.ok ? 0 : 1))
  .catch(() => process.exit(1))
JS
  ok "prod auth fresh"
  run_rung prod npm run test:prod -- --reporter=list,json
fi

# ── Fold the report ───────────────────────────────────────────────────────────
bold "report"
ARGS=()
for rung in "${!RUNG_EXIT[@]}"; do ARGS+=("$rung=${RUNG_EXIT[$rung]}"); done
( cd frontend && node e2e/merge-report.mjs "${ARGS[@]}" )
REPORT_EXIT=$?

echo
bold "artifacts: frontend/e2e/.audit/report.json + screens/"
exit "$REPORT_EXIT"
