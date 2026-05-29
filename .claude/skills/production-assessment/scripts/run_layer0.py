#!/usr/bin/env python3
"""Layer 0 of the production assessment: the deterministic floor.

Runs ruff, mypy, pytest-cov, bandit, pip-audit, and a skill-freshness check,
compares each result against the committed baselines in
docs/assessment/baselines.json, writes a machine summary to
docs/assessment/_machine.json, prints a human summary, and exits non-zero if any
gate regressed.

This is the part of the assessment that must have perfect recall and cost zero
model context. Claude reads _machine.json, never the raw tool output.

Usage:
    python3 run_layer0.py                  # run all gates, fail on regression
    python3 run_layer0.py --update-baseline  # capture current results as the new floor
    python3 run_layer0.py --require-coverage # treat a skipped coverage run as failure (CI)
    python3 run_layer0.py --gates freshness --require-fresh  # scheduled staleness check
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
ASSESS_DIR = REPO_ROOT / "docs" / "assessment"
BASELINES_PATH = ASSESS_DIR / "baselines.json"
MACHINE_PATH = ASSESS_DIR / "_machine.json"
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"
STALENESS_DAYS = 90  # a skill unverified longer than this is flagged (see docs/SKILL_FRESHNESS.md)

# Source packages to type-check / security-scan. Only existing paths are used.
_CANDIDATE_SOURCES = [
    "routers",
    "youtube",
    "ingestion",
    "dna",
    "clip_engine",
    "preference",
    "knowledge",
    "upload_intel",
    "improvement",
    "worker",
    "billing",
    "auth.py",
    "config.py",
    "crypto.py",
    "db.py",
    "limiter.py",
    "main.py",
    "models.py",
]

DEFAULT_BASELINES = {
    # ruff is already green in CI, so a strict floor of 0 is safe from day one.
    "ruff_issues": 0,
    # The gradual gates start permissive so they never red-wall the existing
    # codebase. Run --update-baseline once to capture reality, then tighten
    # mypy_errors toward 0 and the security gates to 0 as the backlog clears.
    "mypy_errors": 1_000_000,
    "coverage_line_rate": 0.0,  # a floor: coverage must not drop below this
    "bandit_high": 1_000_000,
    "bandit_medium": 1_000_000,
    "pip_audit_vulns": 1_000_000,
}


def _sources() -> list[str]:
    return [s for s in _CANDIDATE_SOURCES if (REPO_ROOT / s).exists()]


def _have(tool: str) -> bool:
    return shutil.which(tool) is not None


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, check=False)


def _load_baselines() -> dict:
    if BASELINES_PATH.exists():
        data = json.loads(BASELINES_PATH.read_text())
        return {**DEFAULT_BASELINES, **data}
    return dict(DEFAULT_BASELINES)


# --- individual gates -------------------------------------------------------
# Each returns a dict: {"status": ok|fail|skipped, "value": ..., "detail": ...}


def gate_ruff() -> dict:
    if not _have("ruff"):
        return {"status": "skipped", "detail": "ruff not installed"}
    proc = _run(["ruff", "check", ".", "--output-format", "json"])
    try:
        issues = len(json.loads(proc.stdout or "[]"))
    except json.JSONDecodeError:
        return {"status": "skipped", "detail": "ruff output unparseable"}
    return {"status": "ok", "value": issues, "metric": "ruff_issues", "compare": "max"}


def gate_mypy() -> dict:
    if not _have("mypy"):
        return {"status": "skipped", "detail": "mypy not installed"}
    proc = _run(["mypy", *_sources(), "--no-error-summary", "--no-color-output"])
    errors = sum(1 for ln in proc.stdout.splitlines() if ": error:" in ln)
    return {"status": "ok", "value": errors, "metric": "mypy_errors", "compare": "max"}


def gate_coverage() -> dict:
    if not _have("pytest"):
        return {"status": "skipped", "detail": "pytest not installed"}
    cov_targets: list[str] = []
    for s in _sources():
        cov_targets += ["--cov", s.removesuffix(".py")]
    xml_out = ASSESS_DIR / "_coverage.xml"
    proc = _run(
        [
            "pytest",
            "-q",
            "--no-header",
            *cov_targets,
            "--cov-report",
            f"xml:{xml_out}",
        ]
    )
    if not xml_out.exists():
        # Most common cause locally: no Redis for the slowapi limiter. Not a
        # failure of the harness — coverage simply could not be measured here.
        tail = "\n".join(proc.stdout.splitlines()[-5:])
        return {"status": "skipped", "detail": f"no coverage.xml; tail: {tail}"}
    rate = float(ET.parse(xml_out).getroot().get("line-rate", "0")) * 100
    xml_out.unlink(missing_ok=True)
    return {
        "status": "ok",
        "value": round(rate, 2),
        "metric": "coverage_line_rate",
        "compare": "min",
    }


def gate_bandit() -> dict:
    if not _have("bandit"):
        return {"status": "skipped", "detail": "bandit not installed"}
    dirs = [s for s in _sources() if not s.endswith(".py")]
    proc = _run(["bandit", "-r", *dirs, "-f", "json", "-q"])
    try:
        results = json.loads(proc.stdout or "{}").get("results", [])
    except json.JSONDecodeError:
        return {"status": "skipped", "detail": "bandit output unparseable"}
    high = sum(1 for r in results if r.get("issue_severity") == "HIGH")
    med = sum(1 for r in results if r.get("issue_severity") == "MEDIUM")
    return {
        "status": "ok",
        "value": {"high": high, "medium": med},
        "metric": "bandit",
        "compare": "split",
    }


def gate_pip_audit() -> dict:
    if not _have("pip-audit"):
        return {"status": "skipped", "detail": "pip-audit not installed"}
    proc = _run(["pip-audit", "-f", "json"])
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {"status": "skipped", "detail": "pip-audit output unparseable"}
    deps = data.get("dependencies", data if isinstance(data, list) else [])
    vulns = sum(len(d.get("vulns", [])) for d in deps)
    return {"status": "ok", "value": vulns, "metric": "pip_audit_vulns", "compare": "max"}


def gate_freshness() -> dict:
    """Flag skills whose `last_verified` frontmatter is older than STALENESS_DAYS.

    Warn-only by default (status 'stale' does not fail the run); the scheduled
    re-verification job runs with --require-fresh to make staleness a hard fail.
    See docs/SKILL_FRESHNESS.md.
    """
    skills = sorted(SKILLS_DIR.glob("*/SKILL.md"))
    if not skills:
        return {"status": "skipped", "detail": "no skills found"}
    today = dt.date.today()
    ages: dict[str, int | None] = {}
    stale: list[str] = []
    for sk in skills:
        name = sk.parent.name
        m = re.search(r"^last_verified:\s*(\d{4}-\d{2}-\d{2})", sk.read_text(), re.MULTILINE)
        if not m:
            ages[name] = None
            stale.append(f"{name} (no last_verified)")
            continue
        age = (today - dt.date.fromisoformat(m.group(1))).days
        ages[name] = age
        if age > STALENESS_DAYS:
            stale.append(f"{name} ({age}d)")
    return {
        "status": "stale" if stale else "ok",
        "value": {"ages_days": ages, "stale": stale, "threshold_days": STALENESS_DAYS},
        "metric": "freshness",
        "compare": "self",
    }


GATES = {
    "ruff": gate_ruff,
    "mypy": gate_mypy,
    "coverage": gate_coverage,
    "bandit": gate_bandit,
    "pip_audit": gate_pip_audit,
    "freshness": gate_freshness,
}


def _evaluate(results: dict, baselines: dict) -> tuple[dict, dict]:
    """Return (status_by_gate, measured_baseline_values)."""
    status: dict[str, str] = {}
    measured: dict[str, float | int] = {}
    for name, res in results.items():
        if res["status"] != "ok":
            status[name] = res["status"]
            continue
        if res.get("compare") == "self":  # gate set its own status (e.g. freshness)
            status[name] = "ok"
            continue
        if res["compare"] == "split":  # bandit: high & medium
            high, med = res["value"]["high"], res["value"]["medium"]
            measured["bandit_high"] = high
            measured["bandit_medium"] = med
            ok = high <= baselines["bandit_high"] and med <= baselines["bandit_medium"]
        elif res["compare"] == "min":  # coverage floor
            measured[res["metric"]] = res["value"]
            ok = res["value"] >= baselines[res["metric"]] - 0.01
        else:  # max ceiling (ruff, mypy, pip-audit)
            measured[res["metric"]] = res["value"]
            ok = res["value"] <= baselines[res["metric"]]
        status[name] = "ok" if ok else "fail"
    return status, measured


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--require-coverage", action="store_true")
    ap.add_argument(
        "--require-fresh",
        action="store_true",
        help="fail if any skill's last_verified is stale (scheduled job)",
    )
    ap.add_argument(
        "--gates",
        default="all",
        help=(
            "comma-separated subset to run (e.g. 'mypy,bandit'); default 'all'. "
            f"choices: {','.join(GATES)}"
        ),
    )
    args = ap.parse_args()

    if args.gates == "all":
        selected = dict(GATES)
    else:
        wanted = [g.strip() for g in args.gates.split(",") if g.strip()]
        unknown = [g for g in wanted if g not in GATES]
        if unknown:
            ap.error(f"unknown gate(s): {', '.join(unknown)}")
        selected = {name: GATES[name] for name in wanted}

    ASSESS_DIR.mkdir(parents=True, exist_ok=True)
    baselines = _load_baselines()
    results = {name: fn() for name, fn in selected.items()}
    status, measured = _evaluate(results, baselines)

    if args.update_baseline:
        new_baselines = {**baselines, **measured}
        BASELINES_PATH.write_text(json.dumps(new_baselines, indent=2) + "\n")
        print(f"Baselines updated → {BASELINES_PATH.relative_to(REPO_ROOT)}")
        print(json.dumps(measured, indent=2))
        return 0

    summary = {
        "generated": dt.datetime.now(dt.UTC).isoformat(),
        "sources": _sources(),
        "baselines": baselines,
        "gates": {name: {**results[name], "gate_status": status[name]} for name in selected},
    }
    MACHINE_PATH.write_text(json.dumps(summary, indent=2) + "\n")

    print("Layer 0 — deterministic gates")
    failed = []
    skipped = []
    for name in selected:
        st = status[name]
        val = results[name].get("value", results[name].get("detail", ""))
        print(f"  {name:10s} {st:8s} {val}")
        if st == "fail":
            failed.append(name)
        elif st == "skipped":
            skipped.append(name)
    print(f"\nWrote {MACHINE_PATH.relative_to(REPO_ROOT)}")

    if args.require_coverage and "coverage" in skipped:
        print(f"FAIL: coverage required but was skipped ({results['coverage'].get('detail')})")
        failed.append("coverage")

    if status.get("freshness") == "stale":
        stale = results["freshness"]["value"]["stale"]
        if args.require_fresh:
            print(f"FAIL: skills stale (--require-fresh): {stale}")
            failed.append("freshness")
        else:
            print(
                f"WARN: skills due for re-verification (>{STALENESS_DAYS}d): "
                f"{stale} — see docs/SKILL_FRESHNESS.md"
            )

    if failed:
        print(f"\nGATES FAILED: {', '.join(sorted(set(failed)))}")
        return 1
    print("\nAll runnable gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
