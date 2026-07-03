"""Detect no-op / irreversible Alembic downgrades (Issue 296).

CI's migration-lint job runs this over the CHANGED migration files: a migration
whose ``downgrade()`` is pass-only/empty or unconditionally raises is flagged as
irreversible and fails the gate — UNLESS its revision is listed in
``alembic/DOWNGRADE_EXCEPTIONS`` (one revision per line, ``# rationale`` after it).

The allowlist is itself checked for staleness on every run: an entry whose
migration's ``downgrade()`` is actually real (does work) fails the gate, so the
exceptions file can never silently outlive the migrations it excuses.

Standalone usage:
    python3 scripts/check_downgrades.py [alembic/versions/00NN_*.py ...]

With no file arguments only the allowlist staleness check runs (CI passes the
changed files explicitly; an empty change set has nothing new to classify).
Exit code 0 = clean, 1 = violation found.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
VERSIONS_DIR = _REPO_ROOT / "alembic" / "versions"
EXCEPTIONS_FILE = _REPO_ROOT / "alembic" / "DOWNGRADE_EXCEPTIONS"

_REVISION_RE = re.compile(r"^revision\s*=\s*[\"']([^\"']+)[\"']", re.M)


def load_exceptions(path: Path) -> dict[str, str]:
    """Parse the allowlist file → {revision: rationale}. Missing file = empty."""
    if not path.exists():
        return {}
    entries: dict[str, str] = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        revision, _, rationale = stripped.partition("#")
        entries[revision.strip()] = rationale.strip()
    return entries


def parse_revision(source: str) -> str | None:
    """Extract the module-level ``revision = "..."`` id from migration source."""
    match = _REVISION_RE.search(source)
    return match.group(1) if match else None


def downgrade_is_real(source: str, filename: str) -> bool:
    """True if downgrade() does actual work (not pass-only/empty/raise-only)."""
    tree = ast.parse(source, filename=filename)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "downgrade":
            return any(
                not isinstance(stmt, ast.Pass | ast.Raise)
                and not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))
                for stmt in node.body
            )
    return False  # no downgrade() at all — as irreversible as a pass-only one


def _rel(path: Path) -> str:
    """Repo-relative display path when possible (tests pass tmp paths outside it)."""
    try:
        return str(path.relative_to(_REPO_ROOT))
    except ValueError:
        return str(path)


def _matches(entry: str, revision: str, path: Path) -> bool:
    """An allowlist entry matches by exact revision id or filename number prefix."""
    return entry == revision or path.name.startswith(f"{entry}_")


def check(
    files: list[Path],
    exceptions_path: Path = EXCEPTIONS_FILE,
    versions_dir: Path = VERSIONS_DIR,
) -> list[str]:
    """Return a list of violation messages (empty = clean)."""
    exceptions = load_exceptions(exceptions_path)
    errors: list[str] = []

    for path in files:
        source = path.read_text()
        revision = parse_revision(source)
        if revision is None:
            errors.append(f"{path}: cannot parse a `revision = ...` id")
            continue
        if downgrade_is_real(source, str(path)):
            continue
        if any(_matches(entry, revision, path) for entry in exceptions):
            continue
        errors.append(
            f"{path}: downgrade() is a no-op or raises — irreversible migration. "
            f"Either implement a real downgrade or add `{revision}  # rationale` "
            f"to {_rel(exceptions_path)} as a reviewed exception."
        )

    # Staleness: every allowlist entry must point at a migration whose
    # downgrade is genuinely irreversible.
    all_migrations = sorted(versions_dir.glob("*.py"))
    for entry in exceptions:
        matched = [
            p for p in all_migrations if _matches(entry, parse_revision(p.read_text()) or "", p)
        ]
        if not matched:
            errors.append(
                f"{_rel(exceptions_path)}: entry `{entry}` matches no "
                f"migration in {_rel(versions_dir)} — remove it."
            )
            continue
        for path in matched:
            if downgrade_is_real(path.read_text(), str(path)):
                errors.append(
                    f"{_rel(exceptions_path)}: STALE entry `{entry}` — "
                    f"{path.name} has a real downgrade(); remove it from the allowlist."
                )

    return errors


def main(argv: list[str]) -> int:
    files = [Path(arg) for arg in argv]
    errors = check(files)
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        return 1
    print(f"check_downgrades: OK ({len(files)} migration file(s) checked, allowlist clean)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
