"""Security baseline regression guards (Issue 107).

Enforces two invariants that prevent silent drift in the pip-audit ignore lists:

1. Every ID in run_layer0.py::PIP_AUDIT_IGNORES has a non-empty comment in the
   source explaining WHY it is accepted-risk.

2. The ignore list in pyproject.toml [tool.pip-audit] and the Python-side
   PIP_AUDIT_IGNORES set are identical — so `pip-audit` invoked directly (e.g. by
   a developer) applies the same ignores as the CI harness.

These tests catch the most dangerous form of ignore-list rot: an ID that is added
silently with no rationale, or an ID that exists on one side but not the other.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_RUN_LAYER0 = (
    _REPO_ROOT / ".claude" / "skills" / "production-assessment" / "scripts" / "run_layer0.py"
)
_PYPROJECT = _REPO_ROOT / "pyproject.toml"


def _load_pip_audit_ignores_from_script() -> set[str]:
    """Parse PIP_AUDIT_IGNORES out of run_layer0.py using the AST (not import).

    Importing the script would execute module-level side effects and require the
    full production environment. AST parsing is hermetic and fast.
    """
    tree = ast.parse(_RUN_LAYER0.read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "PIP_AUDIT_IGNORES"
            and isinstance(node.value, ast.Set)
        ):
            return {
                elt.value
                for elt in node.value.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            }
    raise AssertionError(
        f"PIP_AUDIT_IGNORES set not found in {_RUN_LAYER0}. "
        "If the variable was renamed, update this test."
    )


def _load_pip_audit_ignores_from_toml() -> list[str]:
    """Read [tool.pip-audit].ignore-vulns from pyproject.toml."""
    with _PYPROJECT.open("rb") as fh:
        config = tomllib.load(fh)
    return config.get("tool", {}).get("pip-audit", {}).get("ignore-vulns", [])


def test_pip_audit_ignore_lists_are_in_sync() -> None:
    """PIP_AUDIT_IGNORES in run_layer0.py must equal [tool.pip-audit].ignore-vulns
    in pyproject.toml.

    The harness applies ignores via CLI flags (from the Python set); the TOML stanza
    applies ignores when pip-audit is invoked directly. They must stay identical so
    developers and CI see the same result.
    """
    script_ids = _load_pip_audit_ignores_from_script()
    toml_ids = set(_load_pip_audit_ignores_from_toml())

    only_in_script = script_ids - toml_ids
    only_in_toml = toml_ids - script_ids

    assert not only_in_script, (
        f"IDs in run_layer0.py::PIP_AUDIT_IGNORES but NOT in "
        f"pyproject.toml [tool.pip-audit].ignore-vulns: {sorted(only_in_script)}. "
        f"Add them to pyproject.toml with a reason comment, or remove them from both."
    )
    assert not only_in_toml, (
        f"IDs in pyproject.toml [tool.pip-audit].ignore-vulns but NOT in "
        f"run_layer0.py::PIP_AUDIT_IGNORES: {sorted(only_in_toml)}. "
        f"Add them to the Python set, or remove them from both."
    )


def test_run_layer0_pip_audit_ignores_have_comments() -> None:
    """Every ID in PIP_AUDIT_IGNORES must have a non-empty comment nearby in the
    source explaining WHY it is accepted-risk.

    An uncommented ignore entry is a maintenance hazard: future developers cannot
    judge whether the rationale still applies.

    Convention: the comment must appear on the same line as the ID string OR on
    the line immediately preceding it (i.e. within 2 raw lines of the string literal).
    """
    source = _RUN_LAYER0.read_text()
    ignore_ids = _load_pip_audit_ignores_from_script()

    for vuln_id in ignore_ids:
        # Find the character offset of the ID in the source.
        idx = source.find(f'"{vuln_id}"')
        assert idx != -1, (
            f'"{vuln_id}" not found as a string literal in {_RUN_LAYER0}. '
            "This test may need to be updated if quoting style changed."
        )
        # Grab the 2 lines preceding + the line containing the ID to check for a comment.
        preceding_text = source[:idx]
        lines_before = preceding_text.splitlines()[-2:] if preceding_text.splitlines() else []
        line_with_id = source[idx:].split("\n", 1)[0]
        context = "\n".join(lines_before + [line_with_id])
        assert re.search(r"#\s*\S", context), (
            f'"{vuln_id}" in PIP_AUDIT_IGNORES has no comment explaining why it is '
            f"accepted-risk. Add a # comment on the same or preceding line. "
            f"Context seen:\n{context}"
        )


def test_pyproject_pip_audit_ignores_have_comments() -> None:
    """Every ID in pyproject.toml [tool.pip-audit].ignore-vulns must have a
    non-empty # comment in the raw TOML source.

    TOML parsers discard comments, so this test reads the raw text and checks
    that each ID string literal has a comment on the same or immediately
    preceding line.
    """
    toml_text = _PYPROJECT.read_text()
    toml_ids = _load_pip_audit_ignores_from_toml()

    for vuln_id in toml_ids:
        idx = toml_text.find(f'"{vuln_id}"')
        assert idx != -1, (
            f'"{vuln_id}" not found as a string literal in {_PYPROJECT}. '
            "This test may need updating if quoting style changed."
        )
        preceding_text = toml_text[:idx]
        lines_before = preceding_text.splitlines()[-2:] if preceding_text.splitlines() else []
        line_with_id = toml_text[idx:].split("\n", 1)[0]
        context = "\n".join(lines_before + [line_with_id])
        assert re.search(r"#\s*\S", context), (
            f'"{vuln_id}" in pyproject.toml [tool.pip-audit].ignore-vulns has no '
            f"# comment explaining why it is accepted-risk. "
            f"Context seen:\n{context}"
        )
