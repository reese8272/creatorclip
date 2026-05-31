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
# This script mirrors the GH Actions deploy.yml exactly so prod behaviour
# is identical regardless of how the deploy was triggered.

set -euo pipefail

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

echo "==> Deploying..."
# Pass GHCR_TOKEN via env so it never appears in the heredoc body (no shell history leakage).
ssh $SSH_OPTS -o SendEnv=GHCR_TOKEN "${USER}@${HOST}" << 'REMOTE'
set -euo pipefail
cd /opt/autoclip

echo "  Authenticating with GHCR..."
echo "${GHCR_TOKEN}" | docker login ghcr.io -u reese8272 --password-stdin 2>&1 | grep -v password

echo "  Pulling latest image..."
docker compose -f docker-compose.prod.yml pull

echo "  Preflight check..."
docker compose -f docker-compose.prod.yml run --rm app python scripts/doctor.py

echo "  Running migrations..."
docker compose -f docker-compose.prod.yml run --rm app alembic upgrade head

echo "  Rolling out..."
docker compose -f docker-compose.prod.yml up -d --remove-orphans
docker image prune -f

echo "  Smoke test..."
STATUS=""
for i in 1 2 3 4 5; do
  BODY=$(docker compose -f docker-compose.prod.yml exec -T app \
    python3 -c "import urllib.request,json; r=urllib.request.urlopen('http://localhost:8000/health',timeout=10); print(r.read().decode())" \
    2>/dev/null || true)
  echo "  Attempt $i: $BODY"
  STATUS=$(echo "$BODY" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('status',''))" 2>/dev/null || true)
  if [ "$STATUS" = "ok" ]; then
    echo "  Deploy healthy."
    break
  fi
  [ $i -lt 5 ] && sleep 10
done
[ "$STATUS" = "ok" ] || { echo "Smoke test failed after 5 attempts"; exit 1; }
REMOTE

echo "==> Wave 5 is live at https://autoclip.studio"
