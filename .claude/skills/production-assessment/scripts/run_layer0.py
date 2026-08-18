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
    python3 run_layer0.py --gates coverage,module_coverage,diff_cover \
        --require coverage,module_coverage,diff_cover  # CI coverage job (Issue 479):
        # one invocation so the shared _coverage.xml exists for all three, and a
        # skip of ANY of them is a hard failure, never a green "skipped".
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


# Tools that must run INSIDE the interpreter being assessed, not whichever
# same-named binary happens to sit earlier on PATH. Discovered 2026-07-30: the
# `pip-audit` on PATH was `~/.local/bin/pip-audit` (shebang `#!/usr/bin/python3`),
# so the gate audited the USER'S SYSTEM PYTHON — 200 deps / 103 vulns — instead
# of the project venv's 171 deps / 1 vuln. The gate had been reporting ~96-103
# phantom vulnerabilities that have nothing to do with this codebase, and a
# previous session mis-diagnosed the same symptom as "venv staleness".
# `python -m <module>` binds every one of these to `sys.executable`.
_PYTHON_MODULE_TOOLS: dict[str, str] = {
    "pytest": "pytest",
    "mypy": "mypy",
    "bandit": "bandit",
    "pip-audit": "pip_audit",
    "ruff": "ruff",
}


def _tool_cmd(tool: str) -> list[str]:
    """Argv prefix for a tool, preferring `sys.executable -m <module>`."""
    module = _PYTHON_MODULE_TOOLS.get(tool)
    if module is not None:
        return [sys.executable, "-m", module]
    return [tool]


def _have(tool: str) -> bool:
    """True if the tool is importable/runnable by the assessed interpreter.

    Checked via the same argv the gate will actually use, so availability and
    execution can never disagree about WHICH tool is meant.
    """
    module = _PYTHON_MODULE_TOOLS.get(tool)
    if module is not None:
        probe = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0:
            return True
        # Fall through: a tool may be a standalone binary in this environment.
    return shutil.which(tool) is not None


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a gate command, rewriting a bare tool name to the assessed interpreter."""
    if cmd and cmd[0] in _PYTHON_MODULE_TOOLS:
        module = _PYTHON_MODULE_TOOLS[cmd[0]]
        probe = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0:
            cmd = _tool_cmd(cmd[0]) + cmd[1:]
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, check=False)


# Exit codes that mean the tool RAN. Measured 2026-08-18 (Issue 499) against the
# pinned .venv versions — ruff 0.15.15, mypy 1.14.1, bandit 1.9.4, pip-audit 2.10.0:
#
#   tool        0                     1                 2
#   ruff        clean                 violations found  error, stdout EMPTY
#   mypy        no errors             errors found      fatal, stdout EMPTY
#   bandit      no issues *or a       issues found      (not observed)
#               totally failed scan*
#   pip-audit   no vulns              vulns found       error, stdout EMPTY
#
# `returncode != 0` is therefore the WRONG check: 1 is the normal "ran, found
# things" state that the baselines exist to measure, and rejecting it would
# red-wall every gate the moment a single finding exists. Anything OUTSIDE this
# set means the tool did not complete, and its empty stdout then parses to a
# perfect score of 0 against a strict baseline of 0 — a gate that reports green
# over work it never did.
#
# *bandit is invisible to any returncode check — a scan of a nonexistent path or
# an unparseable file exits 0 with `results: []` and a populated `errors[]`. That
# array is the only signal, which is why gate_bandit checks it separately.
_COMPLETED_EXIT_CODES = {0, 1}


def _incomplete(proc: subprocess.CompletedProcess[str]) -> str | None:
    """Return a reason if the tool did not complete, else None (Issue 499)."""
    if proc.returncode in _COMPLETED_EXIT_CODES:
        return None
    output = (proc.stderr or proc.stdout or "").strip()
    return f"exit {proc.returncode}: {output[-300:] or 'no output'}"


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
    if (why := _incomplete(proc)) is not None:
        return {"status": "fail", "detail": f"ruff did not complete — {why}"}
    try:
        issues = len(json.loads(proc.stdout or "[]"))
    except json.JSONDecodeError:
        return {"status": "skipped", "detail": "ruff output unparseable"}
    return {"status": "ok", "value": issues, "metric": "ruff_issues", "compare": "max"}


def gate_mypy() -> dict:
    if not _have("mypy"):
        return {"status": "skipped", "detail": "mypy not installed"}
    proc = _run(["mypy", *_sources(), "--no-error-summary", "--no-color-output"])
    if (why := _incomplete(proc)) is not None:
        # The exact shape that produced a vacuous `ok 0` for three sessions: mypy
        # cannot load the pydantic plugin under the wrong interpreter, aborts with
        # exit 2 and no stdout, and zero lines contain ": error:".
        return {"status": "fail", "detail": f"mypy did not complete — {why}"}
    errors = sum(1 for ln in proc.stdout.splitlines() if ": error:" in ln)
    return {"status": "ok", "value": errors, "metric": "mypy_errors", "compare": "max"}


def gate_coverage() -> dict:
    if not _have("pytest"):
        return {"status": "skipped", "detail": "pytest not installed"}
    # SINGLE source root (Issue 368). Passing one --cov per source package emitted
    # one <source> root each, and coverage.py relativizes every file against the
    # first matching root — so files under a root that is ITSELF a package (e.g.
    # clip_engine/) flattened to bare filenames and landed in package ".", making
    # _module_line_rate("clip_engine") return None and its floor unenforceable.
    # Reproduced 2026-07-30: packages were {".", "preference", "youtube"} with
    # clip_engine absent entirely. With a single "." root every file relativizes
    # to pkg/file.py, so package names are stable and every floor is enforceable.
    # tests/ and alembic/ are already excluded by [tool.coverage.run] omit in
    # pyproject.toml, so "." does not widen the measured set to test code.
    xml_out = ASSESS_DIR / "_coverage.xml"
    # Remove any stale XML from a previous run BEFORE producing a fresh one, and
    # never remove it afterwards (Issue 479). The old shape — produce here, unlink
    # at the end of main() — silently broke ci.yml's split invocation: invocation 1
    # (--gates coverage) deleted the XML on exit, so invocation 2
    # (--gates module_coverage,diff_cover) found nothing and both gates reported
    # "skipped" with exit 0. Per-module floors and the patch gate never ran in CI
    # from 2026-06-23 to 2026-08-12. Cleanup-at-producer-start makes the script
    # order-independent across invocations; the file is gitignored.
    xml_out.unlink(missing_ok=True)
    proc = _run(
        [
            "pytest",
            "-q",
            "--no-header",
            "--cov",
            ".",
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
    if (why := _incomplete(proc)) is not None:
        return {"status": "fail", "detail": f"bandit did not complete — {why}"}
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {"status": "skipped", "detail": "bandit output unparseable"}
    # bandit reports a failed scan IN-BAND and still exits 0 (measured: a missing
    # path or a file it cannot parse yields `results: []` + a populated errors[]).
    # Unchecked, an unscannable tree scores a perfect 0 HIGH / 0 MEDIUM.
    if errors := data.get("errors", []):
        named = ", ".join(str(e.get("filename", "?")) for e in errors[:3])
        more = f" (+{len(errors) - 3} more)" if len(errors) > 3 else ""
        return {
            "status": "fail",
            "detail": f"bandit could not scan {len(errors)} path(s): {named}{more}",
        }
    results = data.get("results", [])
    high = sum(1 for r in results if r.get("issue_severity") == "HIGH")
    med = sum(1 for r in results if r.get("issue_severity") == "MEDIUM")
    return {
        "status": "ok",
        "value": {"high": high, "medium": med},
        "metric": "bandit",
        "compare": "split",
    }


# Accepted-risk CVEs: advisories with no clean fix in our compatible range. Each
# entry is justified in docs/DECISIONS.md (Issue 107, "pip-audit CVE triage")
# and MUST stay in lockstep with pyproject.toml [tool.pip-audit].ignore-vulns.
# Revisit on every dependency bump — an ID drops off this list the moment a
# compatible fix ships.
PIP_AUDIT_IGNORES = {
    # Local /tmp predictable-name priv/DoS. Fixed only in pytest 9, but
    # pytest-asyncio<0.25 caps pytest<9 — a test-stack cascade, not a runtime
    # exposure (dev/CI only). Lift when the test stack is bumped together.
    "GHSA-6w46-j5rx-g56g",
    # NOTE (Issue 143, 2026-06-17): starlette PYSEC-2026-161 was lifted from this
    # list — the starlette 1.x migration (FastAPI 0.137.1 + starlette 1.3.1) shipped,
    # so it now has a real fix in our compatible range. See docs/DECISIONS.md.
    # pip CVEs (dev/build-time only — pip is not a runtime dep; these require
    # installing a maliciously crafted wheel/tar, a supply-chain vector, not a
    # production runtime vulnerability). Fix versions require pip 25.3–26.1.2;
    # pip itself is not in requirements.txt and is managed by the venv/CI toolchain.
    # Re-evaluate when the venv is rebuilt or pip is explicitly pinned.
    "GHSA-4xh5-x5gv-qwph",  # CVE-2025-8869  — symlink check on tar extraction
    "GHSA-6vgw-5pg2-w6jp",  # CVE-2026-1703  — wheel path traversal
    "GHSA-58qw-9mgm-455v",  # CVE-2026-3219  — tar+ZIP concatenation confusion
    "GHSA-jp4c-xjxw-mgf9",  # CVE-2026-6357  — post-install self-update import
    "PYSEC-2026-196",  # pip supply-chain CVE (fix: pip>=26.1.2) — same dev/build-time rationale; not a runtime dep
}


def gate_pip_audit() -> dict:
    if not _have("pip-audit"):
        return {"status": "skipped", "detail": "pip-audit not installed"}
    cmd = ["pip-audit", "-f", "json"]
    for vuln_id in sorted(PIP_AUDIT_IGNORES):
        cmd += ["--ignore-vuln", vuln_id]
    proc = _run(cmd)
    if (why := _incomplete(proc)) is not None:
        # The named live consequence, reproduced 2026-08-18: with the index
        # unreachable pip-audit exits 2 with empty stdout, which parsed to
        # "0 vulnerabilities" on a REQUIRED check. A dependency audit that
        # cannot reach its advisory source has not audited anything.
        return {"status": "fail", "detail": f"pip-audit did not complete — {why}"}
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


# ── Per-module coverage floors (Issue 269) ────────────────────────────────────
# Load-bearing modules that must not silently lose coverage. Floors are set
# conservatively (0.0 on first introduction) so they never red-wall the existing
# codebase. Run --update-baseline after a full green coverage run to capture
# actual rates, then tighten these values as coverage improves.
#
# Keys must match the package/module path segment as it appears in the coverage.xml
# <package name="..."> or <class filename="..."> attributes produced by pytest-cov.
MODULE_COVERAGE_FLOORS: dict[str, float] = {
    # Ratcheted 2026-07-30 (Issue 368) off the now-deterministic single-root
    # measurement, ~1.5pt margin. These two sat at 0.0 — i.e. unenforceable —
    # because the multi-root --cov flattened their files into package "." and
    # _module_line_rate returned None. They were never low-coverage; they were
    # unmeasured. clip_engine is additionally gated by the eval harness.
    "clip_engine": 91.0,  # measured 92.51
    "preference": 88.0,  # measured 89.64
    # crypto/limiter measured 100.0 (unchanged from the 2026-07-29 ratchet).
    "crypto": 99.0,  # crypto.py
    "limiter": 99.0,  # limiter.py
    # NOTE (Issue 368): this key means ROOT auth.py (measured 100.0), NOT
    # routers/auth.py (93.3). Both matched the old suffix fallback and the
    # winner depended on XML ordering, so the 2026-07-29 "auth 93.3" reading
    # was routers/auth.py. Exact-match now pins it to root auth.py; the floor
    # is raised accordingly. Add "routers/auth.py" as its own entry if that
    # module needs its own floor — it is not covered by this one.
    "auth": 99.0,  # root auth.py, measured 100.0
}


def _module_line_rate(xml_root: ET.Element, module_key: str) -> float | None:
    """Extract the line-rate for a given module/package from a coverage.xml root.

    Tries package-level first (for packages), then class-level filename match (for
    single-file modules like crypto.py). Returns None if the module is not found.
    """
    # Package match: <package name="clip_engine" ...>
    for pkg in xml_root.iter("package"):
        if pkg.get("name", "") == module_key or pkg.get("name", "").endswith(f".{module_key}"):
            rate = pkg.get("line-rate")
            return float(rate) * 100 if rate is not None else None
    # Class/file match for single-file modules: <class filename="crypto.py" ...>.
    # EXACT match wins over a suffix match, and the suffix fallback is only used
    # when it is UNAMBIGUOUS (Issue 368). Both rules are load-bearing: "auth"
    # matches root `auth.py` (100.0) AND `routers/auth.py` (93.3), so the old
    # first-match-wins loop returned whichever the XML happened to list first —
    # the floor silently changed meaning between runs. The floors in
    # MODULE_COVERAGE_FLOORS name TOP-LEVEL modules, so exact is the correct
    # reading; an ambiguous suffix returns None (reported "unknown") rather than
    # guessing, because a wrong rate here silently mis-gates a load-bearing module.
    target_file = f"{module_key}.py"
    suffix_matches: list[str] = []
    for cls in xml_root.iter("class"):
        filename = cls.get("filename", "")
        rate = cls.get("line-rate")
        if filename == target_file:
            return float(rate) * 100 if rate is not None else None
        if filename.endswith(f"/{target_file}") and rate is not None:
            suffix_matches.append(rate)
    if len(suffix_matches) == 1:
        return float(suffix_matches[0]) * 100
    return None


def gate_module_coverage() -> dict:
    """Per-module coverage floors for load-bearing modules (Issue 269).

    Parses the coverage.xml generated by gate_coverage and checks each module in
    MODULE_COVERAGE_FLOORS against its floor. Reports failures per-module.
    Skipped if coverage.xml is absent (coverage gate itself was skipped).
    """
    xml_out = ASSESS_DIR / "_coverage.xml"
    if not xml_out.exists():
        return {"status": "skipped", "detail": "coverage.xml not found — run coverage gate first"}
    try:
        root = ET.parse(xml_out).getroot()
    except ET.ParseError as e:
        return {"status": "skipped", "detail": f"coverage.xml unparseable: {e}"}

    failures: list[str] = []
    rates: dict[str, float | None] = {}
    for mod, floor in MODULE_COVERAGE_FLOORS.items():
        rate = _module_line_rate(root, mod)
        rates[mod] = rate
        if rate is None:
            # Module not found in coverage report — could be untouched in this run.
            # Don't fail: coverage may not have measured it (e.g. unit run without
            # that module on the call path). Log as unknown.
            continue
        if rate < floor - 0.01:
            failures.append(f"{mod}: {rate:.1f}% < floor {floor:.1f}%")

    return {
        "status": "ok",
        "value": {"rates": rates, "floors": MODULE_COVERAGE_FLOORS, "failures": failures},
        "metric": "module_coverage",
        "compare": "self",
        "_failures": failures,  # used in _evaluate
    }


def gate_diff_cover() -> dict:
    """Patch/diff coverage gate: changed lines must be >= 80% covered (Issue 269).

    Uses diff-cover to compute coverage for lines changed vs origin/main. Requires
    the coverage.xml to exist (run gate_coverage first) and fetch-depth: 0 in CI
    (shallow clones produce empty diffs). Skipped if diff-cover is not installed or
    coverage.xml is absent.
    """
    if not _have("diff-cover"):
        return {"status": "skipped", "detail": "diff-cover not installed"}
    xml_out = ASSESS_DIR / "_coverage.xml"
    if not xml_out.exists():
        return {"status": "skipped", "detail": "coverage.xml not found — run coverage gate first"}

    proc = _run(
        [
            "diff-cover",
            str(xml_out),
            "--compare-branch=origin/main",
            "--fail-under=80",
            "--quiet",
        ]
    )
    # diff-cover exits 0 if coverage >= --fail-under, non-zero otherwise.
    # Parse the last line for the coverage percentage if available.
    output = (proc.stdout or "") + (proc.stderr or "")
    import re as _re

    match = _re.search(r"(\d+(?:\.\d+)?)\s*%", output)
    patch_rate = float(match.group(1)) if match else None

    return {
        "status": "ok",
        "value": patch_rate if patch_rate is not None else "n/a",
        "metric": "diff_cover",
        "compare": "self",
        "_exit_code": proc.returncode,
        "_detail": output.strip()[-300:] if output.strip() else "no output",
    }


GATES = {
    "ruff": gate_ruff,
    "mypy": gate_mypy,
    "coverage": gate_coverage,
    "module_coverage": gate_module_coverage,
    "diff_cover": gate_diff_cover,
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
        if res.get("compare") == "self":
            # Gate sets its own pass/fail logic via _failures or _exit_code.
            if name == "module_coverage":
                failures = res.get("_failures", [])
                status[name] = "fail" if failures else "ok"
            elif name == "diff_cover":
                exit_code = res.get("_exit_code", 0)
                status[name] = "fail" if exit_code != 0 else "ok"
            else:
                # e.g. freshness — handled below
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
        "--require",
        default="",
        metavar="GATES",
        help=(
            "comma-separated gates that MUST evaluate: a 'skipped' status on any of "
            "them becomes a hard failure (Issue 479 — CI must never green-light a "
            "gate that did not run). --require-coverage is shorthand for including "
            "'coverage'."
        ),
    )
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

    required = {g.strip() for g in args.require.split(",") if g.strip()}
    if args.require_coverage:
        required.add("coverage")
    unknown_required = required - GATES.keys()
    if unknown_required:
        ap.error(f"unknown gate(s) in --require: {', '.join(sorted(unknown_required))}")
    not_selected = required - selected.keys()
    if not_selected:
        ap.error(f"--require names gates not in --gates: {', '.join(sorted(not_selected))}")

    ASSESS_DIR.mkdir(parents=True, exist_ok=True)
    baselines = _load_baselines()
    results = {name: fn() for name, fn in selected.items()}
    # _coverage.xml is NOT removed here (Issue 479): gate_coverage clears any stale
    # copy before regenerating, so leaving it on disk is safe (and gitignored) while
    # end-of-run cleanup is exactly what made ci.yml's second invocation find nothing.
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

    for name in sorted(required):
        if name in skipped:
            print(f"FAIL: {name} required but was skipped ({results[name].get('detail')})")
            failed.append(name)

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
