#!/usr/bin/env bash
# One-time: point git at the tracked hooks in .githooks/ so the pre-push local-CI
# gate is active. Re-run after a fresh clone. Idempotent.
set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"
git config core.hooksPath .githooks
chmod +x .githooks/* scripts/ci_local.sh 2>/dev/null || true
echo "✓ core.hooksPath → .githooks (pre-push local CI active)"
echo "  bypass once with: git push --no-verify"
