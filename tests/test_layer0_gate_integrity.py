"""Issue 499 — a Layer-0 static gate may not report a score for work it never did.

``gate_ruff``, ``gate_mypy``, ``gate_bandit`` and ``gate_pip_audit`` inferred
their result purely from stdout. A tool that ran and *failed* — non-zero exit,
empty stdout — parsed to ``{"status": "ok", "value": 0}``, a perfect score
against the strict baseline of 0, and the run printed "All runnable gates
passed" and exited 0. ``--require`` (Issue 479) cannot see this: it only
escalates a ``skipped`` status, never an ``ok``.

The gates are driven through a stubbed ``_run`` rather than real binaries, so
these assert the GATE's logic — the thing that was wrong — hermetically and in
milliseconds. The exit codes and payload shapes below are transcribed from
measurements against the pinned .venv versions (see the table in run_layer0.py).

Two failure modes are pinned, not one:

1. a tool that did not complete must go red, and
2. a tool that completed and *found something* (exit 1) must NOT — the naive
   ``returncode != 0`` fix looks correct and would red-wall CI on the first
   ruff violation or mypy error.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
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

# Gate name -> (gate function attribute, stdout the tool emits when it ran and
# found exactly one thing). Exit 1 + this payload is the "ran fine, has findings"
# state every one of these tools uses.
_STATIC_GATES = {
    "ruff": ("gate_ruff", json.dumps([{"code": "F401", "filename": "x.py"}])),
    "mypy": ("gate_mypy", "x.py:1: error: Name 'y' is not defined\n"),
    "bandit": (
        "gate_bandit",
        json.dumps({"errors": [], "results": [{"issue_severity": "LOW"}]}),
    ),
    "pip_audit": (
        "gate_pip_audit",
        json.dumps({"dependencies": [{"name": "foo", "vulns": [{"id": "GHSA-x"}]}]}),
    ),
}


def _stub_run(monkeypatch, returncode: int, stdout: str = "", stderr: str = "") -> None:
    """Force every gate's subprocess call to return a fixed CompletedProcess."""

    def fake_run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

    monkeypatch.setattr(run_layer0, "_run", fake_run)
    # _have() shells out to probe importability; the gate must reach _run.
    monkeypatch.setattr(run_layer0, "_have", lambda tool: True)


@pytest.mark.parametrize("gate", sorted(_STATIC_GATES))
def test_gate_fails_when_the_tool_did_not_complete(gate: str, monkeypatch) -> None:
    """Exit 2 with empty stdout is the measured shape of every tool's failure.

    THE regression test: before Issue 499 each of these returned
    ``{"status": "ok", "value": 0}`` — a perfect score — for a tool that
    produced nothing at all.
    """
    _stub_run(monkeypatch, returncode=2, stdout="", stderr="fatal: could not run")
    result = getattr(run_layer0, _STATIC_GATES[gate][0])()
    assert result["status"] == "fail", (
        f"{gate} reported {result['status']!r} (value {result.get('value')!r}) for a "
        f"tool that exited 2 with no output — a gate must never score work it did "
        f"not do."
    )
    assert "exit 2" in result["detail"], "the detail must name the exit code for triage"


@pytest.mark.parametrize("gate", sorted(_STATIC_GATES))
def test_gate_still_reports_findings_when_the_tool_completed(gate: str, monkeypatch) -> None:
    """Exit 1 means "ran, found things" — the state the baselines exist to measure.

    Guards against the naive ``returncode != 0`` fix, which would red-wall the
    gate on the first ruff violation or mypy error and make the baselines
    unreachable.
    """
    attr, stdout = _STATIC_GATES[gate]
    _stub_run(monkeypatch, returncode=1, stdout=stdout)
    result = getattr(run_layer0, attr)()
    assert result["status"] == "ok", (
        f"{gate} must stay 'ok' on exit 1 with real output — findings are counted "
        f"against the baseline, not treated as a broken tool. Got {result!r}"
    )


def test_bandit_fails_on_in_band_scan_errors(monkeypatch) -> None:
    """bandit is invisible to any returncode check — errors[] is the only signal.

    Measured: ``bandit -r /no/such/dir`` and a file with a syntax error both exit
    **0** with ``results: []``. Unchecked, an unscannable tree scores a perfect
    0 HIGH / 0 MEDIUM against baselines.json.
    """
    _stub_run(
        monkeypatch,
        returncode=0,
        stdout=json.dumps(
            {
                "errors": [{"filename": "clip_engine/x.py", "reason": "syntax error"}],
                "results": [],
            }
        ),
    )
    result = run_layer0.gate_bandit()
    assert result["status"] == "fail"
    assert "clip_engine/x.py" in result["detail"], "name the unscannable path"


def test_bandit_counts_severities_on_a_clean_run(monkeypatch) -> None:
    """The happy path is unchanged: errors[] empty, severities counted as before."""
    _stub_run(
        monkeypatch,
        returncode=1,
        stdout=json.dumps(
            {
                "errors": [],
                "results": [
                    {"issue_severity": "HIGH"},
                    {"issue_severity": "MEDIUM"},
                    {"issue_severity": "LOW"},
                ],
            }
        ),
    )
    result = run_layer0.gate_bandit()
    assert result["status"] == "ok"
    assert result["value"] == {"high": 1, "medium": 1}


def test_a_failing_gate_survives_evaluation_into_the_run_status() -> None:
    """Pin the wiring the fix depends on: 'fail' must not be re-scored as 'ok'.

    _evaluate() reads baselines for 'ok' gates only; a gate that returns 'fail'
    has to pass through untouched, or the returncode check above is inert.
    """
    results = {"mypy": {"status": "fail", "detail": "mypy did not complete — exit 2"}}
    status, measured = run_layer0._evaluate(results, run_layer0.DEFAULT_BASELINES)
    assert status["mypy"] == "fail"
    assert "mypy_errors" not in measured, "a failed gate must not write a baseline value"
