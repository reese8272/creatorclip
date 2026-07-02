#!/usr/bin/env bash
# scripts/backup_redis.sh — broker snapshot → encrypted copy in the backup R2 bucket (Issue 288)
#
# Pipeline (streamed, same posture as backup_pg.sh — no plaintext file on host disk):
#   redis-cli BGSAVE (wait for completion)  →  tar /data from the container  →  openssl enc
#   (AES-256, -pass env:)  →  aws s3 cp - (R2)
#
# HONEST SCOPE: a broker snapshot is minutes-stale queue state by nature. It is
# belt-and-suspenders only — the real recovery primitive for lost queue entries is that
# pipeline tasks are idempotent and re-enqueueable from DB status rows (see
# docs/RUNBOOKS.md "Redis broker durability & recovery"). Do NOT treat this backup as a
# guarantee that in-flight tasks survive a volume loss.
#
# Required env (same names as backup_pg.sh; from the VM .env or process environment):
#   BACKUP_R2_BUCKET / BACKUP_ENCRYPTION_KEY
#   R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY
# Optional:
#   COMPOSE_FILE (default: <repo>/docker-compose.prod.yml) · ENV_FILE (default: <repo>/.env)
#   BACKUP_HEALTHCHECK_URL  if set, GET on success (dead-man's-switch)
#
# Cron (nightly, offset from the 03:07 PG backup):
#   27 3 * * *  cd /opt/autoclip && ./scripts/backup_redis.sh >> /var/log/creatorclip-backup.log 2>&1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="${COMPOSE_FILE:-${REPO_DIR}/docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-${REPO_DIR}/.env}"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a && source "$ENV_FILE" && set +a
fi

for var in BACKUP_R2_BUCKET BACKUP_ENCRYPTION_KEY R2_ACCOUNT_ID R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY; do
  if [[ -z "${!var:-}" ]]; then
    echo "ERROR: required env var ${var} is not set" >&2
    exit 1
  fi
done

compose() { docker compose -f "$COMPOSE_FILE" "$@"; }

# Trigger a background save and wait for it to land (LASTSAVE advances on completion).
before="$(compose exec -T redis redis-cli LASTSAVE | tr -dc '0-9')"
compose exec -T redis redis-cli BGSAVE > /dev/null
for _ in $(seq 1 60); do
  after="$(compose exec -T redis redis-cli LASTSAVE | tr -dc '0-9')"
  [[ "$after" != "$before" ]] && break
  sleep 1
done
if [[ "${after:-$before}" == "$before" ]]; then
  echo "ERROR: BGSAVE did not complete within 60s" >&2
  exit 1
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
KEY="redis/${STAMP}.tar.gz.enc"

# Tar the whole /data dir (Redis 7 multi-part AOF lives in appendonlydir/ + dump.rdb).
compose exec -T redis sh -c 'cd /data && tar czf - .' \
  | openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:BACKUP_ENCRYPTION_KEY \
  | AWS_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
    aws s3 cp - "s3://${BACKUP_R2_BUCKET}/${KEY}" \
    --endpoint-url "https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com" --only-show-errors

echo "redis backup uploaded: s3://${BACKUP_R2_BUCKET}/${KEY}"

if [[ -n "${BACKUP_HEALTHCHECK_URL:-}" ]]; then
  curl -fsS -m 10 "$BACKUP_HEALTHCHECK_URL" > /dev/null || true
fi
