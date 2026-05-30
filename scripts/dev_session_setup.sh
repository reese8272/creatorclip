#!/usr/bin/env bash
# CreatorClip — make an ephemeral container test-ready.
#
# Idempotent: safe to run on every session start (it's wired as the Claude-on-web
# SessionStart hook in .claude/settings.json) and safe to run by hand locally. Each
# step no-ops when its target is already satisfied, so re-runs are cheap. Designed
# to degrade gracefully — in GitHub Actions (Redis/Postgres provided as services)
# the "start" steps simply observe they're already reachable.
#
# What it guarantees when it succeeds:
#   - a Python 3.12 venv at ./.venv with requirements.txt + requirements-dev.txt
#   - a live Redis (the rate limiter has no in-memory fallback — the suite needs it)
#   - a live Postgres 16 + pgvector, with the creatorclip role/db + migrations applied
#   - ffmpeg on PATH (clip render / audio extract)
#
# Why this exists: a mid-session Redis death once surfaced as ~25 opaque 500s
# instead of a clear "Redis down". The conftest guard (tests/conftest.py) makes
# that legible; this script makes it not happen in the first place. See
# docs/OFF_COURSE_BUGS.md (2026-05-29).

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

log() { printf '[dev-setup] %s\n' "$*"; }
warn() { printf '[dev-setup] WARN: %s\n' "$*" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }

# Run a command as root whether or not we already are (apt / postgres need it).
as_root() {
  if [ "$(id -u)" -eq 0 ]; then "$@"; elif have sudo; then sudo "$@"; else
    warn "need root for: $* (no sudo) — skipping"; return 1
  fi
}

DB_URL_DEFAULT="postgresql+psycopg://creatorclip:dev_password@localhost:5432/creatorclip"
PG_PASSWORD="${POSTGRES_PASSWORD:-dev_password}"

ensure_ffmpeg() {
  if have ffmpeg; then return 0; fi
  log "installing ffmpeg…"
  as_root apt-get update -qq >/dev/null 2>&1
  as_root apt-get install -y -qq ffmpeg >/dev/null 2>&1 && log "ffmpeg installed" \
    || warn "ffmpeg install failed (render/audio integration tests will skip)"
}

ensure_venv() {
  if [ ! -x .venv/bin/python ]; then
    local py=python3.12
    have "$py" || py=python3
    log "creating venv with $py…"
    "$py" -m venv .venv || { warn "venv creation failed"; return 1; }
  fi
  # Install only when the dev toolchain is missing, so re-runs are fast.
  if ! .venv/bin/python -c "import pytest, ruff" >/dev/null 2>&1; then
    log "installing dependencies (this is the slow first-run step)…"
    .venv/bin/pip install -q -U pip setuptools wheel >/dev/null 2>&1
    .venv/bin/pip install -q -r requirements.txt -r requirements-dev.txt \
      || { warn "dependency install failed"; return 1; }
  fi
  log "venv ready"
}

ensure_redis() {
  if have redis-cli && redis-cli ping >/dev/null 2>&1; then log "redis already up"; return 0; fi
  if ! have redis-server; then
    log "installing redis…"
    as_root apt-get update -qq >/dev/null 2>&1
    as_root apt-get install -y -qq redis-server >/dev/null 2>&1 \
      || { warn "redis install failed"; return 1; }
  fi
  # No persistence: this is a throwaway test cache, not a data store.
  redis-server --daemonize yes --save "" --appendonly no >/dev/null 2>&1
  for _ in 1 2 3 4 5; do redis-cli ping >/dev/null 2>&1 && { log "redis up"; return 0; }; sleep 1; done
  warn "redis did not come up"; return 1
}

ensure_postgres() {
  # In CI/local a Postgres on localhost:5432 may already be provided — use it.
  if pg_isready -q 2>/dev/null; then log "postgres already up"; else
    if ! have pg_ctlcluster; then
      log "postgres-16 server not installed — skipping (unit suite does not need it)"
      return 0
    fi
    # pgvector is required by migration 0001/0006.
    if [ ! -f /usr/share/postgresql/16/extension/vector.control ]; then
      log "installing pgvector…"
      as_root apt-get update -qq >/dev/null 2>&1
      as_root apt-get install -y -qq postgresql-16-pgvector >/dev/null 2>&1 \
        || warn "pgvector install failed (integration tests will fail at CREATE EXTENSION)"
    fi
    log "starting postgres-16 cluster…"
    as_root pg_ctlcluster 16 main start >/dev/null 2>&1
    for _ in 1 2 3 4 5; do pg_isready -q 2>/dev/null && break; sleep 1; done
    pg_isready -q 2>/dev/null || { warn "postgres did not come up"; return 1; }
  fi

  # Role + database (idempotent).
  as_root -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='creatorclip'" 2>/dev/null \
    | grep -q 1 || as_root -u postgres psql -qc \
    "CREATE ROLE creatorclip LOGIN PASSWORD '${PG_PASSWORD}' SUPERUSER;" >/dev/null 2>&1
  as_root -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='creatorclip'" 2>/dev/null \
    | grep -q 1 || as_root -u postgres createdb -O creatorclip creatorclip >/dev/null 2>&1

  # Apply migrations to head. alembic reads env via config (fail-fast), so supply a
  # full test env for this invocation only — these are throwaway dev values.
  if [ -x .venv/bin/alembic ]; then
    log "applying migrations…"
    ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-test-key}" \
    DATABASE_URL="${DATABASE_URL:-$DB_URL_DEFAULT}" \
    REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}" \
    GOOGLE_OAUTH_CLIENT_ID="${GOOGLE_OAUTH_CLIENT_ID:-test}" \
    GOOGLE_OAUTH_CLIENT_SECRET="${GOOGLE_OAUTH_CLIENT_SECRET:-test}" \
    OAUTH_REDIRECT_URI="${OAUTH_REDIRECT_URI:-http://localhost:8000/auth/callback}" \
    JWT_SECRET_KEY="${JWT_SECRET_KEY:-test-jwt-secret-32-bytes-minimum-!}" \
    ALLOWED_ORIGINS="${ALLOWED_ORIGINS:-http://localhost:8000}" \
    TOKEN_ENCRYPTION_KEY="${TOKEN_ENCRYPTION_KEY:-$(.venv/bin/python -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')}" \
      .venv/bin/alembic upgrade head >/dev/null 2>&1 \
      && log "migrations at head" || warn "alembic upgrade failed (integration tests may fail)"
  fi
}

log "preparing test environment in $REPO_ROOT"
ensure_venv
ensure_redis
ensure_ffmpeg
ensure_postgres
log "done — run: .venv/bin/python -m pytest -q"
# Never fail the session on a degraded optional step; the conftest guard is the
# hard gate for the one service the unit suite truly requires (Redis).
exit 0
