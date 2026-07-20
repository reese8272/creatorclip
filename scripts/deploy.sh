#!/usr/bin/env bash
# Manual deploy to autoclip.studio — use when GH Actions is unavailable.
#
# Usage:
#   GHCR_TOKEN=ghp_xxx VPS_USER=root VPS_SSH_KEY=~/.ssh/id_ed25519 ./scripts/deploy.sh
#
# Required env vars:
#   GHCR_TOKEN   — GitHub Personal Access Token with read:packages scope
#   VPS_USER     — SSH user on the VPS (default: root)
#   VPS_SSH_KEY  — path to SSH private key (default: ~/.ssh/id_ed25519)
#   VPS_PORT     — SSH port (default: 22)
#
# This script follows deploy.yml's core sequence (capture rollback image →
# pull → doctor → safety dump → migrate → verify head → roll out → smoke →
# auto-rollback on smoke failure → prune) but is NOT an exact mirror: it has
# no staging-parity gate and no GitHub-secret sync. Prefer re-running the GH
# Actions deploy whenever Actions is available.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="147.182.136.107"
USER="${VPS_USER:-root}"
PORT="${VPS_PORT:-22}"
KEY="${VPS_SSH_KEY:-$HOME/.ssh/id_ed25519}"
COMPOSE_FILE="docker-compose.prod.yml"
REMOTE_DIR="/opt/autoclip"

if [[ -z "${GHCR_TOKEN:-}" ]]; then
  echo "ERROR: GHCR_TOKEN is not set. Export it before running this script." >&2
  exit 1
fi

SSH_OPTS="-i $KEY -p $PORT -o StrictHostKeyChecking=no -o BatchMode=yes"

echo "==> Copying compose file to server..."
scp $SSH_OPTS "$COMPOSE_FILE" "${USER}@${HOST}:${REMOTE_DIR}/${COMPOSE_FILE}"

# Ship the backup script so the pre-migration safety dump (Issue 257) can run on the
# VM host (it calls `docker compose exec` + `aws`, so it must run on the host, not in
# a container). Mirrors deploy.yml, where the self-hosted runner already has the repo.
echo "==> Copying backup script to server..."
ssh $SSH_OPTS "${USER}@${HOST}" "mkdir -p ${REMOTE_DIR}/scripts"
scp $SSH_OPTS "${SCRIPT_DIR}/backup_pg.sh" "${USER}@${HOST}:${REMOTE_DIR}/scripts/backup_pg.sh"

echo "==> Authenticating with GHCR..."
# The token travels over stdin into `docker login --password-stdin` — never on
# argv, never in the heredoc body, and never via SendEnv (default sshd AcceptEnv
# only passes LANG/LC_*, so `-o SendEnv=GHCR_TOKEN` silently dropped the var and
# the remote login aborted under `set -u`). The login credential persists in the
# remote ~/.docker/config.json for the deploy session below.
# The stderr filter drops docker's 3-line "password stored unencrypted" warning
# (cosmetic noise on every manual deploy) without touching ssh's exit status.
printf '%s\n' "$GHCR_TOKEN" | ssh $SSH_OPTS "${USER}@${HOST}" \
  "docker login ghcr.io -u reese8272 --password-stdin" \
  2> >(grep -vE 'unencrypted|credential|docs\.docker\.com' >&2 || true)

echo "==> Deploying..."
ssh $SSH_OPTS "${USER}@${HOST}" << 'REMOTE'
set -euo pipefail
cd /opt/autoclip

# Capture the digest of the CURRENT running image before pulling the new one, so
# there is an immutable rollback target if the smoke test fails (mirrors
# deploy.yml's Issue 271 capture). Empty on a first-ever deploy.
echo "  Capturing pre-pull image for rollback..."
CID=$(docker compose -f docker-compose.prod.yml ps -q app 2>/dev/null || true)
PREV_IMAGE=""
if [ -n "$CID" ]; then
  PREV_IMAGE=$(docker inspect --format='{{index .RepoDigests 0}}' "$CID" 2>/dev/null || true)
fi
echo "  Previous image: ${PREV_IMAGE:-<none — first deploy>}"

# Mirrors deploy.yml's _rollback_and_fail. PREV_IMAGE is a repo DIGEST ref
# (ghcr.io/...@sha256:...) which can't sit in the compose tag slot, so re-tag it
# locally as :rollback and select it via the ${IMAGE_TAG:-latest} interpolation
# in docker-compose.prod.yml. Rollback is a safety net, not a success signal —
# the deploy still exits 1.
_rollback_and_fail() {
  echo "  Auto-rolling back to previous image: ${PREV_IMAGE}"
  if [ -n "${PREV_IMAGE}" ]; then
    docker pull "${PREV_IMAGE}" || true
    docker tag "${PREV_IMAGE}" ghcr.io/reese8272/creatorclip:rollback || true
    docker compose -f docker-compose.prod.yml down --timeout 30 || true
    IMAGE_TAG=rollback docker compose -f docker-compose.prod.yml up -d || true
    echo "  Rollback complete. Verify with: curl http://localhost:8000/health"
  else
    echo "  No previous image captured (first deploy?) — manual recovery required (docs/RUNBOOKS.md)."
  fi
  exit 1
}

echo "  Pulling latest image..."
docker compose -f docker-compose.prod.yml pull

echo "  Preflight check..."
docker compose -f docker-compose.prod.yml run --rm app python scripts/doctor.py

# Pre-migration safety dump (Issue 257) — mirrors deploy.yml. Take an encrypted
# pg_dump BEFORE alembic so a bad migration has an undo. If backups are configured
# a dump failure aborts the deploy (set -e); if not yet configured (Issue 256), skip
# with a warning rather than block the deploy.
if grep -qE '^BACKUP_R2_BUCKET=.+' .env 2>/dev/null; then
  echo "  Taking pre-migration safety dump (predeploy/)..."
  BACKUP_PREFIX=predeploy/ \
  COMPOSE_FILE=/opt/autoclip/docker-compose.prod.yml \
  ENV_FILE=/opt/autoclip/.env \
  bash /opt/autoclip/scripts/backup_pg.sh
else
  echo "  WARNING: No BACKUP_R2_BUCKET configured — skipping pre-migration dump (Issue 256 not yet activated). Migrating WITHOUT a safety dump."
fi

echo "  Running migrations..."
docker compose -f docker-compose.prod.yml run --rm app alembic upgrade head

# Verify the DB actually reached head. A faulty env.py once made `alembic upgrade`
# exit 0 while silently rolling back every migration (2026-06-24 outage: prod sat at
# 0027 while code expected 0034 → new-signup /auth/callback 500s). Fail the deploy if
# current != head so a silent no-op can never ship again. Revision ids are the leading
# token of each command's stdout (e.g. "0034 (head)"); stderr is dropped.
echo "  Verifying DB is at head..."
CUR_REV=$(docker compose -f docker-compose.prod.yml run --rm -T app alembic current 2>/dev/null \
  | grep -oE '^[0-9a-f]+' | head -1)
HEAD_REV=$(docker compose -f docker-compose.prod.yml run --rm -T app alembic heads 2>/dev/null \
  | grep -oE '^[0-9a-f]+' | head -1)
if [ -z "$CUR_REV" ] || [ "$CUR_REV" != "$HEAD_REV" ]; then
  echo "Migration verification FAILED: current='${CUR_REV:-none}' head='${HEAD_REV:-none}'." >&2
  echo "The upgrade likely no-op'd (check alembic/env.py transaction handling)." >&2
  exit 1
fi
echo "  Migrations confirmed at head ($CUR_REV)."

echo "  Rolling out..."
docker compose -f docker-compose.prod.yml up -d --remove-orphans

echo "  Smoke test — /health gate..."
STATUS=""
for i in 1 2 3 4 5; do
  BODY=$(docker compose -f docker-compose.prod.yml exec -T app \
    python3 -c "import urllib.request,json; r=urllib.request.urlopen('http://localhost:8000/health',timeout=10); print(r.read().decode())" \
    2>/dev/null || true)
  echo "  Attempt $i: $BODY"
  STATUS=$(echo "$BODY" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('status',''))" 2>/dev/null || true)
  if [ "$STATUS" = "ok" ]; then
    echo "  /health healthy."
    break
  fi
  [ $i -lt 5 ] && sleep 10
done
[ "$STATUS" = "ok" ] || { echo "Smoke test /health FAILED after 5 attempts"; _rollback_and_fail; }

echo "  Smoke test — critical journey (llm_harness --flow core)..."
# Requires CC_BASE_URL, CC_JWT_SECRET, CC_CREATOR_ID to be set in the deploy .env.
# On a non-zero exit the harness prints which REQUIRED step failed; the caller exits 1.
docker compose -f docker-compose.prod.yml exec -T app \
  python3 scripts/llm_harness.py --flow core \
  || { echo "Critical journey smoke FAILED — see harness output above"; _rollback_and_fail; }

# Prune only AFTER the smoke tests pass — deploy.yml deliberately moved prune
# post-smoke so a failed deploy never deletes the image needed to roll back.
echo "  Pruning dangling images..."
docker image prune -f
REMOTE

echo "==> CreatorClip is live at https://autoclip.studio"
