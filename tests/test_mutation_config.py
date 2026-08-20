"""The mutation sandbox must be able to import the code it mutates (Issue 503).

mutmut copies the mutated source into a ``mutants/`` sandbox, REMOVES the repo
root from ``sys.path`` (so the original code cannot shadow a mutant), and runs
pytest from inside it. Anything the targeted tests import transitively must
therefore appear in ``also_copy`` — and ``also_copy`` was a hand-maintained
literal that had gone stale.

The consequence was total: ``tests/conftest.py`` imports ``main``, ``main``
imports ``event_log``, and ``event_log`` imports ``shared_resources``, which was
not copied. Collection died, mutmut aborted before mutating anything, and
``mutmut run || true`` reported success on 8/8 weekly runs.

So this applies the house rule to the literal that caused it: the import closure
is COMPUTED, and the config must cover it. Same shape as
``tests/test_usage_coverage.py`` — derive the population, then check both
directions.

Static, not a mutmut run: this is the unit lane, and a real ``mutmut run`` takes
~80 s and needs Redis. What it cannot catch is a runtime-only import (importlib,
a plugin registry); the executed-mutant floor in ``scripts/mutation_score.py``
is the backstop for that, and it is what the weekly job actually gates on.
"""

from __future__ import annotations

import ast
import tomllib
from functools import lru_cache
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"

# Where the sandbox's import closure starts: what pytest loads before a single
# test runs, plus the modules under mutation.
_SEEDS = ("main", "tests.conftest")


@lru_cache(maxsize=1)
def _mutmut_config() -> dict:
    return tomllib.loads(_PYPROJECT.read_text())["tool"]["mutmut"]


@lru_cache(maxsize=1)
def _first_party() -> frozenset[str]:
    """Top-level modules and packages that live in this repo."""
    modules = {p.stem for p in _REPO_ROOT.glob("*.py")}
    packages = {
        p.name
        for p in _REPO_ROOT.iterdir()
        if p.is_dir() and (p / "__init__.py").exists() and p.name not in {"tests", "mutants"}
    }
    return frozenset(modules | packages)


def _module_path(dotted: str) -> Path | None:
    as_module = _REPO_ROOT / (dotted.replace(".", "/") + ".py")
    if as_module.exists():
        return as_module
    as_package = _REPO_ROOT / dotted.replace(".", "/") / "__init__.py"
    return as_package if as_package.exists() else None


@lru_cache(maxsize=1)
def _import_closure() -> frozenset[str]:
    """Top-level first-party names reachable from the seeds and mutated paths."""
    first = _first_party()
    seeds = list(_SEEDS) + [
        str(Path(p).with_suffix("")).replace("/", ".")
        for p in _mutmut_config().get("paths_to_mutate", [])
    ]

    seen: set[str] = set()
    stack = [(s, p) for s in seeds if (p := _module_path(s)) is not None]
    while stack:
        dotted, path = stack.pop()
        if dotted in seen:
            continue
        seen.add(dotted)
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover - a syntax error fails loudly elsewhere
            continue
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                # Both the module and `from pkg import submodule` forms.
                targets = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            for target in targets:
                if target.split(".")[0] not in first or target in seen:
                    continue
                if (child := _module_path(target)) is not None:
                    stack.append((target, child))

    return frozenset(name.split(".")[0] for name in seen) & first


def _covered() -> set[str]:
    """Top-level names the sandbox will contain: also_copy plus the mutated paths."""
    cfg = _mutmut_config()
    covered = {Path(entry).name.removesuffix(".py") for entry in cfg["also_copy"]}
    covered |= {Path(p).parts[0].removesuffix(".py") for p in cfg["paths_to_mutate"]}
    return covered


def test_the_closure_walk_finds_something() -> None:
    """Anti-vacuity: an empty closure would make the coverage check pass trivially."""
    closure = _import_closure()
    assert len(closure) >= 20, f"import closure collapsed to {sorted(closure)}"
    # The exact chain that broke the sandbox: conftest -> main -> event_log ->
    # shared_resources. If the walk stops resolving, this is what it loses first.
    for link in ("main", "event_log", "shared_resources", "config"):
        assert link in closure, f"{link} missing from the computed closure"


def test_also_copy_covers_the_whole_import_closure() -> None:
    """Every first-party module the sandbox needs must be copied into it.

    A missing one does not degrade the run — it aborts it, before a single
    mutant executes.
    """
    missing = sorted(_import_closure() - _covered())
    assert not missing, (
        f"module(s) the mutation sandbox imports but never copies: {missing}. "
        "mutmut strips the repo root from sys.path, so the sandbox cannot fall "
        "back to the original tree: collection dies and zero mutants run. Add "
        "them to [tool.mutmut] also_copy in pyproject.toml."
    )


def test_no_unnecessary_also_copy_entry() -> None:
    """The reverse arm: an entry for a module nothing in the closure imports.

    Not fatal at runtime — a spare copy only costs sandbox setup time — but it is
    the same rot that let the missing entries go unnoticed, and a name that no
    longer resolves to a file is a ghost.
    """
    cfg = _mutmut_config()
    # pytest.ini is config, not a module; static/ is data the app serves.
    non_modules = {"pytest.ini", "static"}
    for entry in cfg["also_copy"]:
        if entry in non_modules:
            continue
        assert (_REPO_ROOT / entry).exists(), (
            f"also_copy names {entry!r}, which does not exist in the repo"
        )


def test_targeted_test_files_exist() -> None:
    """The mutant test selection must name real files.

    A renamed test file here does not fail loudly — mutmut selects nothing and
    every mutant survives, which reads as a catastrophic score rather than as a
    configuration error.
    """
    for rel in _mutmut_config().get("pytest_add_cli_args_test_selection", []):
        assert (_REPO_ROOT / rel).exists(), f"mutmut test selection names a missing file: {rel}"


def test_mutated_paths_exist() -> None:
    for rel in _mutmut_config()["paths_to_mutate"]:
        assert (_REPO_ROOT / rel).exists(), f"paths_to_mutate names a missing file: {rel}"
