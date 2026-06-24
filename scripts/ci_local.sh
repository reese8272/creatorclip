#!/usr/bin/env bash
# Local CI — run the gates that don't need Docker, right here, before a push.
#
# Layer 1 of CreatorClip's hybrid CI/CD (DECISIONS.md 2026-06-23, "hybrid self-hosted
# + local CI/CD"). Instant terminal feedback on the fast gates, zero GitHub-hosted
# minutes. The Docker-dependent gates (integration, eval, playwright, migration-lint,
# docker-build) run on the self-hosted runner in ci.yml — this box has no Docker/PG.
#
# RATCHET, not red-wall. The repo carries pre-existing gate debt (ruff-format drift,
# the 10-item eslint baseline — see docs/OFF_COURSE_BUGS.md). Blocking on that would
# force --no-verify on every push, so the formatting/lint gates check only the files
# you CHANGED (vs origin/main): touch a file → it must be clean; untouched debt is
# left to a dedicated cleanup. The always-green static gates (ruff check, mypy,
# bandit) and the frontend test/build run in full.
#
# Gate logic is reused, not reimplemented: mypy/bandit/pip-audit delegate to
# run_layer0.py — the same baseline-aware aggregator ci.yml uses.
#
# Usage:
#   scripts/ci_local.sh           # --fast (pre-push default)
#   scripts/ci_local.sh --full    # adds pip-audit (network) + coverage
#   scripts/ci_local.sh --help
#
# Exit 1 if any RUN gate fails; skips (absent tool/service, no changed files) are OK.
# Bypass the pre-push hook with: git push --no-verify   (or CI_LOCAL_SKIP=1)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

PROFILE="fast"
case "${1:-}" in
  --full) PROFILE="full" ;;
  --fast|"") PROFILE="fast" ;;
  -h|--help) sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) echo "Unknown arg: $1 (use --fast | --full | --help)" >&2; exit 2 ;;
esac

[ -d "$REPO_ROOT/.venv/bin" ] && export PATH="$REPO_ROOT/.venv/bin:$PATH"
LAYER0=".claude/skills/production-assessment/scripts/run_layer0.py"

# ── Change base: what are we about to push? Diff against origin/main, falling back
#    gracefully so a missing remote ref never errors the hook. ───────────────────
BASE="$(git merge-base origin/main HEAD 2>/dev/null || true)"
[ -z "$BASE" ] && BASE="$(git rev-parse HEAD~1 2>/dev/null || true)"
changed() {  # changed() <pathspec...> → tracked files changed since BASE (added/copied/modified/renamed)
  [ -z "$BASE" ] && return 0
  git diff --name-only --diff-filter=ACMR "$BASE" -- "$@" 2>/dev/null
}

declare -a PASS=() FAILED=() SKIPPED=()
pass() { PASS+=("$1");    printf '  \033[32m✓\033[0m %s\n' "$1"; }
fail() { FAILED+=("$1");  printf '  \033[31m✗\033[0m %s\n' "$1"; }
skip() { SKIPPED+=("$1"); printf '  \033[33m∅\033[0m %s — %s\n' "$1" "$2"; }
hdr()  { printf '\n\033[1m▶ %s\033[0m\n' "$1"; }

# ── ruff check — full tracked tree (mirrors CI's clean checkout; excludes untracked
#    scratch like tests/eval/). Currently green; a regression here blocks. ────────
gate_ruff_check() {
  hdr "ruff check (tracked files)"
  command -v ruff >/dev/null 2>&1 || { skip "ruff check" "ruff not installed (.venv?)"; return; }
  local files; files="$(git ls-files -- '*.py')"
  if printf '%s\n' "$files" | xargs ruff check; then pass "ruff check"; else fail "ruff check"; fi
}

# ── ruff format — RATCHET on changed files only (43-file pre-existing drift is left
#    to a dedicated cleanup; see OFF_COURSE_BUGS.md). ─────────────────────────────
gate_ruff_format() {
  hdr "ruff format --check (changed files)"
  command -v ruff >/dev/null 2>&1 || { skip "ruff format" "ruff not installed"; return; }
  local files; files="$(changed '*.py' | grep -vE '^tests/eval/' || true)"
  [ -z "$files" ] && { skip "ruff format" "no changed .py files"; return; }
  if printf '%s\n' "$files" | xargs ruff format --check; then pass "ruff format (changed)"
  else echo "  fix: ruff format $(echo "$files" | tr '\n' ' ')"; fail "ruff format (changed)"; fi
}

# ── mypy + bandit (+ pip-audit on --full) via the canonical aggregator ──────────
gate_static() {
  local gates="mypy,bandit"; [ "$PROFILE" = "full" ] && gates="mypy,bandit,pip_audit"
  hdr "static gates (run_layer0: $gates)"
  command -v python3 >/dev/null 2>&1 || { skip "static" "python3 not found"; return; }
  if python3 "$LAYER0" --gates "$gates"; then pass "static ($gates)"; else fail "static ($gates)"; fi
}

# ── pytest unit — needs Redis (limiter) AND Postgres (conftest session guard). Skip
#    with a loud note when either is absent; the self-hosted CI always has both. ──
gate_unit() {
  hdr "pytest -m 'not integration'"
  command -v pytest >/dev/null 2>&1 || { skip "pytest unit" "pytest not installed"; return; }
  redis-cli ping >/dev/null 2>&1 || { skip "pytest unit" "Redis down on :6379 — CI covers it"; return; }
  if ! python3 -c "import socket;socket.create_connection(('localhost',5432),2).close()" 2>/dev/null; then
    skip "pytest unit" "Postgres down on :5432 — runs on self-hosted CI"; return; fi
  if pytest -m "not integration" -q; then pass "pytest unit"; else fail "pytest unit"; fi
}

# ── coverage floor (full profile only) ──────────────────────────────────────────
gate_coverage() {
  [ "$PROFILE" = "full" ] || return 0
  hdr "coverage floor (run_layer0: coverage)"
  redis-cli ping >/dev/null 2>&1 || { skip "coverage" "Redis down"; return; }
  python3 -c "import socket;socket.create_connection(('localhost',5432),2).close()" 2>/dev/null \
    || { skip "coverage" "Postgres down — CI covers it"; return; }
  if python3 "$LAYER0" --gates coverage --require-coverage; then pass "coverage"; else fail "coverage"; fi
}

# ── frontend: eslint RATCHET (changed .ts/.tsx only — the 10-item baseline stays
#    out of the way), then vitest + production build in full (both green). ────────
gate_frontend() {
  hdr "frontend: eslint (changed), vitest, build"
  command -v npm >/dev/null 2>&1 || { skip "frontend" "npm not installed"; return; }
  [ -d frontend/node_modules ] || { skip "frontend" "node_modules absent (npm --prefix frontend ci)"; return; }
  local ok=1

  # eslint only on changed frontend sources (strip the frontend/ prefix for cwd=frontend)
  local fe; fe="$(changed 'frontend/**/*.ts' 'frontend/**/*.tsx' | sed 's#^frontend/##' || true)"
  if [ -n "$fe" ]; then
    if (cd frontend && printf '%s\n' "$fe" | xargs npx eslint); then pass "eslint (changed)"; else ok=0; fail "eslint (changed)"; fi
  else
    skip "eslint" "no changed frontend .ts/.tsx"
  fi

  if npm --prefix frontend test;      then pass "vitest";        else ok=0; fail "vitest"; fi
  if npm --prefix frontend run build; then pass "frontend build"; else ok=0; fail "frontend build"; fi
  return 0
}

printf '\033[1mCreatorClip local CI — profile: %s  (base: %s)\033[0m\n' "$PROFILE" "${BASE:0:9}"
gate_ruff_check
gate_ruff_format
gate_static
gate_unit
gate_coverage
gate_frontend

printf '\n\033[1m── Summary ──\033[0m\n'
printf '  passed:  %s%s\n' "${#PASS[@]}"    "$([ ${#PASS[@]}    -gt 0 ] && echo " (${PASS[*]})")"
printf '  skipped: %s%s\n' "${#SKIPPED[@]}" "$([ ${#SKIPPED[@]} -gt 0 ] && echo " (${SKIPPED[*]})")"
printf '  failed:  %s%s\n' "${#FAILED[@]}"  "$([ ${#FAILED[@]}  -gt 0 ] && echo " (${FAILED[*]})")"

if [ "${#FAILED[@]}" -gt 0 ]; then
  printf '\n\033[31mLOCAL CI FAILED\033[0m — fix the above, or bypass with: git push --no-verify\n'; exit 1
fi
printf '\n\033[32mLocal CI passed.\033[0m Docker-only gates (integration, eval, playwright) run on the self-hosted runner.\n'
exit 0
