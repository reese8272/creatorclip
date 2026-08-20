#!/usr/bin/env python3
"""Assert a pytest run actually EXECUTED tests, not merely exited 0 (Issue 504).

A lane gated on an external credential exits 0 in two very different worlds:
every test passed, or every test ``skipif``-skipped because the credential was
empty. ``${{ secrets.X }}`` expands to ``""`` when the secret is unset, renamed
or blanked, so the second world is one Settings-page edit away — and it looked
identical to the first on the dashboard while ``docs/GO_LIVE.md`` went on citing
the nightly as proof the external APIs were live.

Collection counts cannot separate the two: ``skipif`` is evaluated after
collection, so a fully-skipped lane still reports its full ``--collect-only``
count. That is why this reads the JUnit report and counts what RAN.

Executed means: collected, minus skipped, minus deselected. Failures and errors
count as executed — they made the call and got an answer, which is exactly the
signal this guard exists to confirm. The run's own exit code judges those.

Usage:
    python3 scripts/assert_tests_executed.py <junit.xml> <minimum>
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def executed_count(junit_xml: Path) -> tuple[int, dict[str, int]]:
    """Return (executed, breakdown) for a pytest JUnit XML report."""
    root = ET.parse(junit_xml).getroot()
    # pytest emits <testsuites><testsuite .../></testsuites>; older/other tools
    # emit a bare <testsuite>. Handle both rather than assuming a shape.
    suites = list(root.iter("testsuite"))
    if not suites:
        raise ValueError(f"{junit_xml}: no <testsuite> element — not a pytest JUnit report")

    totals = {"tests": 0, "skipped": 0, "failures": 0, "errors": 0}
    for suite in suites:
        for key in totals:
            totals[key] += int(suite.get(key, "0"))
    return totals["tests"] - totals["skipped"], totals


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    junit_xml, minimum = Path(argv[1]), int(argv[2])

    if not junit_xml.exists():
        print(
            f"FAIL: {junit_xml} not found — the test step did not produce a report.",
            file=sys.stderr,
        )
        return 1

    try:
        executed, totals = executed_count(junit_xml)
    except (ET.ParseError, ValueError) as exc:
        print(f"FAIL: {junit_xml} unreadable — {exc}", file=sys.stderr)
        return 1

    detail = (
        f"executed={executed} (collected={totals['tests']}, skipped={totals['skipped']}, "
        f"failures={totals['failures']}, errors={totals['errors']}) minimum={minimum}"
    )
    if executed < minimum:
        print(f"FAIL: lane did not execute enough tests — {detail}", file=sys.stderr)
        if totals["skipped"] == totals["tests"] and totals["tests"]:
            print(
                "      EVERY test skipped. The usual cause is an empty API-key secret; "
                "a renamed pytest marker is the other.",
                file=sys.stderr,
            )
        return 1

    print(f"OK: {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
