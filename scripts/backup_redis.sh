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

# Read a single key from .env WITHOUT executing it (duplicated verbatim from
# backup_pg.sh — sourcing the prod .env runs any value containing $(...) as shell
# and exports every secret; this parses only the vars we need). Env takes precedence.
read_env() {
  local key="$1"
  if [ -n "${!key:-}" ]; then printf '%s' "${!key}"; return; fi
  [ -f "$ENV_FILE" ] || return 0
  sed -n "s/^${key}[[:space:]]*=[[:space:]]*//p" "$ENV_FILE" | head -1 | sed -e 's/^"//' -e 's/"$//'
}

BACKUP_R2_BUCKET="$(read_env BACKUP_R2_BUCKET)"
BACKUP_ENCRYPTION_KEY="$(read_env BACKUP_ENCRYPTION_KEY)"
R2_ACCOUNT_ID="$(read_env R2_ACCOUNT_ID)"
R2_ACCESS_KEY_ID="$(read_env R2_ACCESS_KEY_ID)"
R2_SECRET_ACCESS_KEY="$(read_env R2_SECRET_ACCESS_KEY)"
BACKUP_HEALTHCHECK_URL="$(read_env BACKUP_HEALTHCHECK_URL)"
export BACKUP_ENCRYPTION_KEY  # so `openssl -pass env:` can read it

# Validate required config (report NAMES only, never values).
for var in BACKUP_R2_BUCKET BACKUP_ENCRYPTION_KEY R2_ACCOUNT_ID R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY; do
  if [[ -z "${!var:-}" ]]; then
    echo "ERROR: required env var ${var} is not set" >&2
    exit 1
  fi
done

# Guard the 3-2-1 invariant (same as backup_pg.sh): backups must go to a
# DIFFERENT bucket than media, so a media-bucket mistake can never touch backups.
R2_BUCKET="$(read_env R2_BUCKET)"
if [[ -n "$R2_BUCKET" && "$BACKUP_R2_BUCKET" == "$R2_BUCKET" ]]; then
  echo "ERROR: BACKUP_R2_BUCKET must differ from R2_BUCKET ('$R2_BUCKET')" >&2
  exit 1
fi

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
