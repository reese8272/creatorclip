"""Regression tests for the Layer-0 per-module coverage gate (Issue 368).

Only the resolution rule is tested — that is the part that silently mis-gated
load-bearing modules. Two real defects motivated these:

1. Under the old multi-root ``--cov`` invocation, ``clip_engine``/``preference``
   files flattened into package "." and resolved to ``None``, so their floors
   sat at 0.0 and enforced nothing.
2. ``auth`` matched BOTH root ``auth.py`` (100.0) and ``routers/auth.py`` (93.3)
   via the suffix fallback, and the winner depended on XML iteration order.
"""

import importlib.util
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / ".claude"
    / "skills"
    / "production-assessment"
    / "scripts"
    / "run_layer0.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("run_layer0", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


run_layer0 = _load_module()


def _xml(body: str) -> ET.Element:
    return ET.fromstring(f'<coverage line-rate="0.77">{body}</coverage>')


def test_package_match_resolves_a_package_module() -> None:
    """clip_engine must resolve from <package>, not report None (defect 1)."""
    root = _xml('<packages><package name="clip_engine" line-rate="0.9251"/></packages>')
    assert run_layer0._module_line_rate(root, "clip_engine") == pytest.approx(92.51)


def test_exact_filename_beats_suffix_match() -> None:
    """`auth` must pin to ROOT auth.py even when routers/auth.py is listed FIRST.

    This is the ordering-dependence that made the gate non-deterministic: the old
    first-match-wins loop returned whichever the XML happened to list first.
    """
    root = _xml(
        '<packages><package name="."><classes>'
        '<class filename="routers/auth.py" line-rate="0.933"/>'
        '<class filename="auth.py" line-rate="1"/>'
        "</classes></package></packages>"
    )
    assert run_layer0._module_line_rate(root, "auth") == pytest.approx(100.0)


def test_ambiguous_suffix_only_match_returns_none_rather_than_guessing() -> None:
    """Two suffix candidates and no exact match -> unknown, never a coin flip."""
    root = _xml(
        '<packages><package name="."><classes>'
        '<class filename="routers/auth.py" line-rate="0.933"/>'
        '<class filename="youtube/auth.py" line-rate="0.5"/>'
        "</classes></package></packages>"
    )
    assert run_layer0._module_line_rate(root, "auth") is None


def test_unique_suffix_match_still_resolves() -> None:
    """A single nested candidate is unambiguous, so it is still usable."""
    root = _xml(
        '<packages><package name="."><classes>'
        '<class filename="pkg/crypto.py" line-rate="0.88"/>'
        "</classes></package></packages>"
    )
    assert run_layer0._module_line_rate(root, "crypto") == pytest.approx(88.0)


def test_every_floored_module_is_resolvable_in_principle() -> None:
    """Guard the floors dict itself: no module may sit at an unenforceable 0.0.

    Issue 368's root cause was two floors silently reading 0.0 for a YEAR of
    assessment runs while appearing configured.
    """
    assert run_layer0.MODULE_COVERAGE_FLOORS, "floors dict must not be empty"
    zero = [k for k, v in run_layer0.MODULE_COVERAGE_FLOORS.items() if v <= 0.0]
    assert not zero, f"floors at 0.0 enforce nothing: {zero}"
