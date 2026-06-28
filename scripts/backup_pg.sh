#!/usr/bin/env bash
# scripts/backup_pg.sh — nightly encrypted Postgres backup → SEPARATE R2 bucket (Issue 256)
#
# Pipeline (fully streamed — no plaintext dump ever touches host disk):
#   docker compose exec postgres pg_dump  →  gzip  →  openssl enc (AES-256)  →  aws s3 cp - (R2)
#
# Security posture (the #1 trap in backup scripts is secret leakage):
#   - DB credentials NEVER leave the postgres container: pg_dump runs inside it via an
#     exec'd shell that reads the container's own POSTGRES_* env. No DB password is ever
#     present in this host script, its argv, or `ps`.
#   - The dump encryption passphrase is read by openssl via `-pass env:` — from the
#     environment, NEVER from argv (which `ps` and shell history can see) and never echoed.
#   - The R2 credentials are handed to the aws CLI via its env (AWS_ACCESS_KEY_ID/_SECRET),
#     never as CLI flags.
#   - `set -x` is never enabled; required-secret checks report the var NAME, never its value.
#   - The dump carries Fernet *ciphertext* for OAuth tokens, so it is useless without the
#     separately-escrowed TOKEN_ENCRYPTION_KEY (Issue 255). BACKUP_ENCRYPTION_KEY must NOT be
#     escrowed inside the backup it protects (circular dependency — see DECISIONS 2026-06-27).
#
# Retention is enforced by an R2 Lifecycle rule on the backup bucket (Issue 258), NOT by
# client-side deletes — deliberate, so a bug here can never mass-delete backups, and so it
# never collides with the bucket's Object Lock (Compliance mode). This script only uploads.
#
# Required env (from the VM .env or the process environment):
#   BACKUP_R2_BUCKET           SEPARATE bucket from R2_BUCKET (e.g. creatorclip-backups)
#   BACKUP_ENCRYPTION_KEY      openssl symmetric passphrase (never logged)
#   R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY   for the R2 upload
# Optional:
#   COMPOSE_FILE   (default: <repo>/docker-compose.prod.yml)
#   ENV_FILE       (default: <repo>/.env)
#   BACKUP_PREFIX  extra key prefix (Issue 257 reuse passes "predeploy/" for safety dumps)
#   BACKUP_HEALTHCHECK_URL   if set, GET on success for dead-man's-switch alerting
#
# Cron (nightly 03:07 UTC), capturing output for visibility:
#   7 3 * * *  cd /opt/autoclip && ./scripts/backup_pg.sh >> /var/log/creatorclip-backup.log 2>&1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="${COMPOSE_FILE:-$REPO_DIR/docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-$REPO_DIR/.env}"
BACKUP_PREFIX="${BACKUP_PREFIX:-}"

log() { printf '%s  %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { printf '%s  ERROR: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; exit 1; }

# Read a single key from .env WITHOUT executing it (tolerates arbitrary values; never
# sources the file, so a value with shell metacharacters can't run). Env takes precedence.
read_env() {
  local key="$1"
  if [ -n "${!key:-}" ]; then printf '%s' "${!key}"; return; fi
  [ -f "$ENV_FILE" ] || return 0
  # First match on exact key; strip the leading "KEY=" and surrounding quotes/space.
  sed -n "s/^${key}[[:space:]]*=[[:space:]]*//p" "$ENV_FILE" | head -1 | sed -e 's/^"//' -e 's/"$//'
}

BACKUP_R2_BUCKET="$(read_env BACKUP_R2_BUCKET)"
BACKUP_ENCRYPTION_KEY="$(read_env BACKUP_ENCRYPTION_KEY)"
R2_ACCOUNT_ID="$(read_env R2_ACCOUNT_ID)"
R2_ACCESS_KEY_ID="$(read_env R2_ACCESS_KEY_ID)"
R2_SECRET_ACCESS_KEY="$(read_env R2_SECRET_ACCESS_KEY)"
export BACKUP_ENCRYPTION_KEY  # so `openssl -pass env:` can read it

# --- Validate required config (report NAMES only, never values) ---
for v in BACKUP_R2_BUCKET BACKUP_ENCRYPTION_KEY R2_ACCOUNT_ID R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY; do
  [ -n "${!v}" ] || die "$v is not set (check $ENV_FILE or the environment). Aborting — no backup taken."
done
# Guard the 3-2-1 invariant: backups must go to a DIFFERENT bucket than media.
R2_BUCKET="$(read_env R2_BUCKET)"
if [ -n "$R2_BUCKET" ] && [ "$BACKUP_R2_BUCKET" = "$R2_BUCKET" ]; then
  die "BACKUP_R2_BUCKET must differ from R2_BUCKET ('$R2_BUCKET') — a media-bucket mistake must not touch backups."
fi
command -v aws >/dev/null 2>&1 || die "aws CLI not found on PATH (install awscli; backups upload to R2 via S3 API)."

R2_ENDPOINT="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OBJECT="creatorclip-${STAMP}.sql.gz.enc"
DAILY_KEY="${BACKUP_PREFIX}daily/${OBJECT}"

upload() {  # upload <s3-key> ; reads the stream on stdin
  AWS_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" \
  AWS_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
  aws s3 cp - "s3://${BACKUP_R2_BUCKET}/$1" \
    --endpoint-url "$R2_ENDPOINT" --only-show-errors
}

log "Starting backup → s3://${BACKUP_R2_BUCKET}/${DAILY_KEY}"

# Stream: pg_dump (in-container, creds container-side) → gzip → openssl → R2.
# pipefail makes any stage's failure fail the whole script (no silent partial backup).
docker compose -f "$COMPOSE_FILE" exec -T postgres \
  sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges' \
  | gzip -c \
  | openssl enc -aes-256-cbc -salt -pbkdf2 -iter 600000 -pass env:BACKUP_ENCRYPTION_KEY \
  | upload "$DAILY_KEY"

log "Daily backup uploaded: ${DAILY_KEY}"

# On Sundays, also keep a weekly copy (server-side R2 copy — cheap, no re-encrypt/re-upload).
if [ "$(date -u +%u)" = "7" ]; then
  WEEKLY_KEY="${BACKUP_PREFIX}weekly/${OBJECT}"
  AWS_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" \
  AWS_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
  aws s3 cp "s3://${BACKUP_R2_BUCKET}/${DAILY_KEY}" "s3://${BACKUP_R2_BUCKET}/${WEEKLY_KEY}" \
    --endpoint-url "$R2_ENDPOINT" --only-show-errors
  log "Weekly copy created: ${WEEKLY_KEY}"
fi

# Optional dead-man's-switch: ping a healthcheck URL so a SILENT cron failure is alertable.
HC_URL="$(read_env BACKUP_HEALTHCHECK_URL)"
if [ -n "$HC_URL" ]; then
  curl -fsS -m 10 "$HC_URL" >/dev/null 2>&1 || log "WARN: healthcheck ping failed (backup itself succeeded)."
fi

log "BACKUP OK ${DAILY_KEY}"
