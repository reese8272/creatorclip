"""Regression tests for the Layer-0 per-module coverage gate (Issues 368, 501).

Two layers, because the gate has failed in two different places.

**The resolution rule** (Issue 368) — the part that silently mis-gated
load-bearing modules. Two real defects motivated these:

1. Under the old multi-root ``--cov`` invocation, ``clip_engine``/``preference``
   files flattened into package "." and resolved to ``None``, so their floors
   sat at 0.0 and enforced nothing.
2. ``auth`` matched BOTH root ``auth.py`` (100.0) and ``routers/auth.py`` (93.3)
   via the suffix fallback, and the winner depended on XML iteration order.

**The fail-closed posture** (Issue 501) — resolving correctly is worthless if an
unresolvable module is then waved through. These run the real
``gate_module_coverage`` against ``fixtures/coverage_report_real_shape.xml``, a
trimmed but genuine pytest-cov document. A hand-built element tree cannot
falsify this gate: the defect lives in the gap between the shapes coverage.py
actually emits and the shapes the resolver anticipates, and a fixture written
from the same assumptions as the code shares its blind spot.

Regenerating the fixture after a coverage.py upgrade or a package rename: run
the coverage gate (``run_layer0.py --gates coverage``) to produce
``docs/assessment/_coverage.xml``, then copy across the ``<package>`` elements
named in ``MODULE_COVERAGE_FLOORS`` plus ``routers/auth.py``, dropping
``<lines>`` and ``<sources>``. Keep the header comment's provenance current, and
keep it free of double hyphens — they are illegal inside an XML comment.
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

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "coverage_report_real_shape.xml"


def _xml(body: str) -> ET.Element:
    return ET.fromstring(f'<coverage line-rate="0.77">{body}</coverage>')


def _run_gate_against(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, xml_bytes: bytes) -> dict:
    """Run the real gate_module_coverage over a coverage document.

    ASSESS_DIR is module-level, so it is redirected rather than the report being
    written into the repo's own docs/assessment/.
    """
    monkeypatch.setattr(run_layer0, "ASSESS_DIR", tmp_path)
    (tmp_path / "_coverage.xml").write_bytes(xml_bytes)
    return run_layer0.gate_module_coverage()


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


def test_no_floor_sits_at_an_unenforceable_zero() -> None:
    """Guard the floors dict itself: no module may sit at an unenforceable 0.0.

    Issue 368's root cause was two floors silently reading 0.0 for a YEAR of
    assessment runs while appearing configured.
    """
    assert run_layer0.MODULE_COVERAGE_FLOORS, "floors dict must not be empty"
    zero = [k for k, v in run_layer0.MODULE_COVERAGE_FLOORS.items() if v <= 0.0]
    assert not zero, f"floors at 0.0 enforce nothing: {zero}"


# ── Resolution against a REAL report, and the fail-closed posture (Issue 501) ──


def _real_report() -> ET.Element:
    return ET.parse(_FIXTURE).getroot()


def test_every_floored_module_resolves_against_a_real_coverage_report() -> None:
    """Every floor must resolve to a NUMBER in a real pytest-cov document.

    This is the test whose old namesake asserted nothing about resolution — it
    only checked the floors dict was non-zero, so it would have stayed green
    through the exact defect it was named for. The fixture is a real report
    (see its header), not a hand-built element tree, because the defect lives in
    the gap between the shapes coverage.py actually emits and the shapes the
    resolver anticipates.
    """
    unresolved = {
        mod: run_layer0._module_line_rate(_real_report(), mod)
        for mod in run_layer0.MODULE_COVERAGE_FLOORS
    }
    missing = [m for m, rate in unresolved.items() if rate is None]
    assert not missing, f"floored module(s) unresolvable in a real report: {missing}"


def test_gate_passes_on_the_real_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Control arm: with every module resolvable and above its floor, ok."""
    result = _run_gate_against(monkeypatch, tmp_path, _FIXTURE.read_bytes())
    assert result["status"] == "ok"
    assert result["_failures"] == []


def test_gate_fails_closed_when_a_floored_module_cannot_be_measured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Issue 501: an unresolvable floored module is a FAILURE, not a shrug.

    Reproduces Issue 368's shape — `clip_engine` flattened out of its own package
    — against the real report. Before the fix the loop `continue`d, `_failures`
    stayed empty, the gate evaluated `ok`, and `--require` accepted it.
    """
    doc = _real_report()
    packages = doc.find("packages")
    assert packages is not None
    for pkg in list(packages):
        if pkg.get("name") == "clip_engine":
            packages.remove(pkg)

    result = _run_gate_against(monkeypatch, tmp_path, ET.tostring(doc))

    assert result["status"] == "ok", "gate ran; the verdict is carried by _failures"
    assert result["_failures"], "an unmeasurable floor must not evaluate green"
    assert any("clip_engine" in f for f in result["_failures"])
    assert result["value"]["rates"]["clip_engine"] is None
