"""Unit tests for the executed-count guard (Issue 504).

The guard's whole job is to tell apart two runs that both exit 0: one where the
live tests ran, and one where an empty API-key secret made every test skip. So
the all-skipped case is the load-bearing test here — the happy path is only the
control that proves the guard is not simply always-red.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "assert_tests_executed.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("assert_tests_executed", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


guard = _load_module()


def _junit(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "junit.xml"
    path.write_text(f'<?xml version="1.0" encoding="utf-8"?><testsuites>{body}</testsuites>')
    return path


def _suite(tests: int, skipped: int = 0, failures: int = 0, errors: int = 0) -> str:
    return (
        f'<testsuite name="pytest" tests="{tests}" skipped="{skipped}" '
        f'failures="{failures}" errors="{errors}" />'
    )


def test_a_fully_executed_lane_passes(tmp_path: Path) -> None:
    """Control arm: 9 ran, floor 9 — the guard must not be always-red."""
    report = _junit(tmp_path, _suite(tests=9))
    assert guard.main(["prog", str(report), "9"]) == 0


def test_a_fully_skipped_lane_fails(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """THE defect: an empty ANTHROPIC_API_KEY skips all 9 and pytest exits 0.

    Collection counts cannot catch this — skipif is evaluated after collection,
    so `tests` still reads 9. Only `tests - skipped` separates the two worlds.
    """
    report = _junit(tmp_path, _suite(tests=9, skipped=9))
    assert guard.main(["prog", str(report), "9"]) == 1
    err = capsys.readouterr().err
    assert "EVERY test skipped" in err
    assert "executed=0" in err


def test_a_partially_skipped_lane_below_the_floor_fails(tmp_path: Path) -> None:
    """A lane that quietly loses most of its tests is the same failure, smaller."""
    report = _junit(tmp_path, _suite(tests=9, skipped=7))
    assert guard.main(["prog", str(report), "9"]) == 1


def test_failures_count_as_executed(tmp_path: Path) -> None:
    """A failing live test MADE the call — that is the signal this guard wants.

    The run's own exit code judges the failure; conflating the two would make a
    red lane indistinguishable from an absent one.
    """
    report = _junit(tmp_path, _suite(tests=9, failures=3))
    assert guard.main(["prog", str(report), "9"]) == 0


def test_multiple_testsuite_elements_are_summed(tmp_path: Path) -> None:
    report = _junit(tmp_path, _suite(tests=5) + _suite(tests=4))
    assert guard.main(["prog", str(report), "9"]) == 0


def test_a_missing_report_fails(tmp_path: Path) -> None:
    """No report means the step never got as far as running tests."""
    assert guard.main(["prog", str(tmp_path / "nope.xml"), "1"]) == 1


def test_an_unparseable_report_fails(tmp_path: Path) -> None:
    path = tmp_path / "junit.xml"
    path.write_text("<testsuites>  <not-closed>")
    assert guard.main(["prog", str(path), "1"]) == 1


def test_a_report_without_a_testsuite_fails(tmp_path: Path) -> None:
    """An empty <testsuites/> is not a zero-test pass — it is no evidence."""
    path = tmp_path / "junit.xml"
    path.write_text('<?xml version="1.0"?><testsuites/>')
    assert guard.main(["prog", str(path), "1"]) == 1
