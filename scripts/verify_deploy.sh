#!/usr/bin/env bash
#
# verify_deploy.sh — Tier-1 production deploy verification (LEFT_OFF NEXT-ACTION #1).
#
# Confirms a deploy actually landed and is healthy: public endpoints respond, the
# legal pages are live (OAuth-verification prerequisite), /docs is disabled in
# prod, and the DB is migrated to the expected alembic head.
#
# Run from anywhere with SSH access to the VM. Everything is overridable:
#   DOMAIN=agenticlip.studio SSH_HOST=creatorclip-vm \
#   DEPLOY_DIR=/opt/autoclip EXPECTED_HEAD=a7b8c9d0e1f2 ./scripts/verify_deploy.sh
#
# Exit 0 = all checks passed; non-zero = at least one failed.

set -u

DOMAIN="${DOMAIN:-agenticlip.studio}"
SSH_HOST="${SSH_HOST:-creatorclip-vm}"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/autoclip}"
EXPECTED_HEAD="${EXPECTED_HEAD:-a7b8c9d0e1f2}"  # 0007_clip_outcome_final
BASE="https://${DOMAIN}"

pass=0
fail=0
ok()   { echo "  ✓ $1"; pass=$((pass + 1)); }
bad()  { echo "  ✗ $1"; fail=$((fail + 1)); }

echo "Verifying deploy at ${BASE} (vm=${SSH_HOST}, dir=${DEPLOY_DIR})"
echo

echo "[1] Public health"
health="$(curl -fsS --max-time 15 "${BASE}/health" 2>/dev/null || true)"
if echo "$health" | grep -q '"status":[[:space:]]*"ok"'; then
  ok "/health → $health"
else
  bad "/health not ok → ${health:-<no response>}"
fi

echo "[2] Legal pages (OAuth-verification prerequisite)"
for path in /privacy /terms; do
  code="$(curl -fsS -o /dev/null -w '%{http_code}' --max-time 15 "${BASE}${path}" 2>/dev/null || true)"
  [ "$code" = "200" ] && ok "${path} → 200" || bad "${path} → ${code:-<no response>}"
done

echo "[3] Metrics endpoint"
code="$(curl -fsS -o /dev/null -w '%{http_code}' --max-time 15 "${BASE}/metrics" 2>/dev/null || true)"
[ "$code" = "200" ] && ok "/metrics → 200" || bad "/metrics → ${code:-<no response>}"

echo "[4] /docs disabled in production"
code="$(curl -fsS -o /dev/null -w '%{http_code}' --max-time 15 "${BASE}/docs" 2>/dev/null || true)"
[ "$code" = "404" ] && ok "/docs → 404 (disabled)" || bad "/docs → ${code} (should be 404 in prod)"

echo "[5] DB migrated to head (${EXPECTED_HEAD})"
current="$(ssh "${SSH_HOST}" "cd ${DEPLOY_DIR} && docker compose exec -T app .venv/bin/alembic current" 2>/dev/null \
  | grep -oE '[0-9a-f]{12}' | head -1 || true)"
if [ "$current" = "$EXPECTED_HEAD" ]; then
  ok "alembic current = ${current}"
else
  bad "alembic current = ${current:-<unreachable>} (expected ${EXPECTED_HEAD}) — check: ssh ${SSH_HOST} 'cd ${DEPLOY_DIR} && docker compose logs --tail 100 app'"
fi

echo
echo "Result: ${pass} passed, ${fail} failed"
[ "$fail" -eq 0 ] && { echo "DEPLOY VERIFIED ✓"; exit 0; } || { echo "DEPLOY NOT VERIFIED ✗"; exit 1; }
