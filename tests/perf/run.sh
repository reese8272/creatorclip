#!/usr/bin/env bash
#
# run.sh — one command to verify the BLOCKER fix (Issue 58) under real load.
#
# Brings up Postgres + PgBouncer(transaction mode) + Redis + the app (DB traffic
# routed THROUGH PgBouncer), migrates, seeds a realistic creator, runs Locust,
# then scans the app logs for the prepared-statement failure signature. That
# signature appearing = the prepare_threshold=None fix regressed.
#
#   ./tests/perf/run.sh                      # default 200 users / 20 spawn / 3m
#   USERS=400 RUN_TIME=5m ./tests/perf/run.sh
#   NO_TEARDOWN=1 ./tests/perf/run.sh        # leave the stack up for inspection
#
# Requires: docker (compose v2) + a Locust on PATH (or set LOCUST=.venv/bin/locust).

set -euo pipefail

cd "$(dirname "$0")"
COMPOSE=(docker compose -f docker-compose.perf.yml)
APP_URL="http://localhost:58000"
CREATOR_ID="00000000-0000-0000-0000-0000000000ff"  # keep in sync with seed.py
JWT_SECRET="perf-jwt-secret-at-least-32-bytes-long"

USERS="${USERS:-200}"
SPAWN_RATE="${SPAWN_RATE:-20}"
RUN_TIME="${RUN_TIME:-3m}"
LOCUST="${LOCUST:-locust}"

teardown() {
  if [ "${NO_TEARDOWN:-0}" = "1" ]; then
    echo "NO_TEARDOWN=1 — leaving the stack up. Tear down with:"
    echo "  ${COMPOSE[*]} down -v"
  else
    echo "Tearing down…"
    "${COMPOSE[@]}" down -v >/dev/null 2>&1 || true
  fi
}
trap teardown EXIT

# A valid Fernet key (urlsafe-base64 of 32 bytes) without needing a Python import.
fernet_key() { openssl rand -base64 32 2>/dev/null | tr '+/' '-_' || head -c32 /dev/urandom | base64 | tr '+/' '-_'; }

echo "[0/6] Writing .env.perf"
cat > .env.perf <<EOF
ENV=development
ANTHROPIC_API_KEY=perf-not-real
VOYAGE_API_KEY=perf-not-real
REDIS_URL=redis://redis:6379/0
GOOGLE_OAUTH_CLIENT_ID=perf-not-real
GOOGLE_OAUTH_CLIENT_SECRET=perf-not-real
OAUTH_REDIRECT_URI=http://localhost:58000/auth/callback
TOKEN_ENCRYPTION_KEY=$(fernet_key)
JWT_SECRET_KEY=${JWT_SECRET}
ALLOWED_ORIGINS=http://localhost:58000
STORAGE_BACKEND=local
EOF

echo "[1/6] Building + starting Postgres, PgBouncer (transaction mode), Redis"
"${COMPOSE[@]}" up -d --build postgres pgbouncer redis
"${COMPOSE[@]}" up --wait postgres pgbouncer redis

echo "[2/6] Migrating (direct to Postgres — 0006 uses CREATE INDEX CONCURRENTLY)"
"${COMPOSE[@]}" run --rm migrate

echo "[3/6] Seeding a realistic creator"
"${COMPOSE[@]}" run --rm seed

echo "[4/6] Starting the app (DB → PgBouncer:6432)"
"${COMPOSE[@]}" up -d --wait app

echo "[5/6] Load test: ${USERS} users, spawn ${SPAWN_RATE}/s, ${RUN_TIME}"
CC_BASE_URL="$APP_URL" CC_JWT_SECRET="$JWT_SECRET" CC_CREATOR_ID="$CREATOR_ID" \
  "$LOCUST" -f locustfile.py --host "$APP_URL" \
    --users "$USERS" --spawn-rate "$SPAWN_RATE" --run-time "$RUN_TIME" \
    --headless --csv perf_results || true

echo "[6/6] Scanning app logs for the prepared-statement BLOCKER signature"
logs="$("${COMPOSE[@]}" logs app 2>/dev/null || true)"
if echo "$logs" | grep -iE "prepared statement .* does not exist|InvalidSqlStatementName|DuplicatePreparedStatement" >/dev/null; then
  echo
  echo "RESULT: ✗ BLOCKER REGRESSED — prepared-statement errors under PgBouncer transaction pooling:"
  echo "$logs" | grep -iE "prepared statement .* does not exist|InvalidSqlStatementName|DuplicatePreparedStatement" | head -5
  echo "Fix: ensure db.py keeps connect_args={'prepare_threshold': None}."
  exit 1
fi

echo
echo "RESULT: ✓ No prepared-statement errors under transaction pooling — Issue 58 fix holds."
echo "Percentile tables: tests/perf/perf_results_stats.csv (+ _stats_history.csv)."
echo "Review the p95/p99 + failure columns against tests/perf/README.md before scaling the beta."
