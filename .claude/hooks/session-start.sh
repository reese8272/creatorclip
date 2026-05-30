#!/bin/bash
# SessionStart hook — provision the test environment for Claude-on-web sessions.
#
# Runs ONLY in the remote (web) container. Local dev uses docker-compose and CI uses
# GitHub Actions service containers, so we must not apt-install or start system
# services on a developer's machine. Synchronous + idempotent: it delegates to
# scripts/dev_session_setup.sh (venv+deps, Redis, Postgres+pgvector+migrations,
# ffmpeg) so `pytest` and the Layer-0 gates can always run once the session starts.
# See docs/OFF_COURSE_BUGS.md (2026-05-29) for the Redis-down incident that motivated it.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

bash "${CLAUDE_PROJECT_DIR:-.}/scripts/dev_session_setup.sh"
