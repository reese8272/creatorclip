"""Tests for the quarantine marker registration and gating behaviour (Issue 268).

Verifies:
1. The `quarantine` marker is declared in pytest.ini — checked by reading the ini file
   directly (avoids re-invoking pytest as a subprocess, which would re-trigger the
   conftest fail-fast guards and fail in dev environments without Postgres).
2. A test carrying @pytest.mark.quarantine is excluded from the gating lane
   (addopts = -m "not integration and not quarantine") by checking the addopts string.
3. A live quarantined test is present in this file as a structural fixture — it passes
   when run directly and is simply deselected by the default filter.

These tests do NOT require Postgres, Redis, or Docker.
"""

from __future__ import annotations

import pathlib
import re


def _read_pytest_ini() -> str:
    """Return the contents of pytest.ini from the repo root."""
    repo_root = pathlib.Path(__file__).parent.parent
    return (repo_root / "pytest.ini").read_text()


def test_quarantine_marker_is_registered() -> None:
    """The `quarantine` marker must be declared in pytest.ini markers section.

    Reads pytest.ini directly to avoid re-invoking pytest as a subprocess
    (which would trigger the conftest fail-fast Postgres guard in dev environments).
    """
    ini = _read_pytest_ini()
    assert "quarantine" in ini, (
        "The 'quarantine' marker is not declared in pytest.ini. Add it to the markers section."
    )


def test_quarantine_excluded_from_addopts() -> None:
    """The default addopts must exclude the quarantine marker from the gating lane.

    The addopts line in pytest.ini must contain 'not quarantine' so a quarantined
    test is deselected without requiring any extra -m flag on the command line.
    """
    ini = _read_pytest_ini()
    # Match the addopts line — allow any whitespace/quoting.
    addopts_match = re.search(r"^addopts\s*=\s*(.+)$", ini, re.MULTILINE)
    assert addopts_match is not None, "addopts line not found in pytest.ini"
    addopts_value = addopts_match.group(1)
    assert "not quarantine" in addopts_value, (
        f"addopts does not exclude quarantine: {addopts_value!r}. "
        "The gating lane must use '-m \"not integration and not quarantine\"'."
    )


def test_quarantine_marker_lifecycle_docs_present() -> None:
    """The quarantine marker must have a lifecycle comment in pytest.ini.

    A bare marker declaration without lifecycle guidance encourages
    leaving tests quarantined indefinitely. The marker line must include
    the quarantine policy comment.
    """
    ini = _read_pytest_ini()
    # The policy comment in the markers section references the lifecycle.
    assert "Lifecycle" in ini or "lifecycle" in ini, (
        "The quarantine marker in pytest.ini should document its lifecycle "
        "(quarantine → fix → remove marker). Add a comment to the markers section."
    )


# ── Sample quarantined test (never runs in the gating lane) ──────────────────
# This test exists purely to verify the marker mechanism. It is always passing
# when run directly, but is excluded by the default `-m "not quarantine"` filter.
# Real quarantined tests carry an issue reference explaining the known flake.

import pytest  # noqa: E402  (stdlib-then-third-party order; pytest is 3rd party)


@pytest.mark.quarantine
def test_sample_quarantined() -> None:
    """Sample quarantined test — excluded from the gating lane by default addopts.

    Issue 268: demonstrates the quarantine marker lifecycle. In production use,
    replace with a genuinely flaky test referencing the tracking issue, e.g.:
        @pytest.mark.quarantine  # Issue NNN: flaky on slow CI runners
    """
    # Always passes when run directly; only exists to demonstrate deselection.
    assert True
