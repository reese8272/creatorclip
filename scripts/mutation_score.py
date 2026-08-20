#!/usr/bin/env python3
"""Report the mutmut score, and FAIL if no mutants were actually run (Issue 503).

The weekly mutation workflow reported success on 8/8 runs having never executed a
single mutant: `mutmut run || true` swallowed a crash during stats collection, and
the follow-up `mutmut results` step printed an empty list.

That empty list is the subtle half. **`mutmut results` prints only SURVIVORS**, so
a run that died before mutating anything produces exactly the same output as a run
that killed every mutant — a blank block under a "Mutation score" heading, which
reads as a perfect score. Neither the exit code nor the report could tell the two
apart, so this reads the counts instead.

Source of truth is `mutants/**/*.py.meta`, whose `exit_code_by_key` has one entry
per mutant that actually ran: a NON-ZERO exit means the tests failed, i.e. the
mutant was KILLED; zero means the tests passed while the code was broken, i.e. it
SURVIVED. (Inverted from the usual reading of an exit code, and worth stating,
because getting it backwards silently reports the complement of the real score.)

Survivors are reported, never fatal — this workflow is REPORT-only by design
(Issue 273) and must never gate a merge. The ONLY failure here is "did not run".

Usage:
    python3 scripts/mutation_score.py [--min N] [--summary PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MUTANTS_DIR = REPO_ROOT / "mutants"


def read_counts(mutants_dir: Path) -> tuple[int, int, int, dict[str, tuple[int, int]]]:
    """Return (checked, killed, survived, per_module{name: (killed, checked)})."""
    checked = killed = 0
    per_module: dict[str, tuple[int, int]] = {}
    for meta_path in sorted(mutants_dir.rglob("*.py.meta")):
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        exits = meta.get("exit_code_by_key", {})
        if not exits:
            continue
        mod_killed = sum(1 for code in exits.values() if code)
        name = str(meta_path.relative_to(mutants_dir)).removesuffix(".meta")
        per_module[name] = (mod_killed, len(exits))
        checked += len(exits)
        killed += mod_killed
    return checked, killed, checked - killed, per_module


def render(checked: int, killed: int, survived: int, per_module: dict) -> str:
    lines = ["## Mutation score (Issues 273, 503)", ""]
    if not checked:
        lines.append("**NO MUTANTS RAN.** See the failure above — this is not a 100% score.")
        return "\n".join(lines)
    lines += [
        f"**{killed / checked * 100:.1f}%** — {killed} killed / {survived} survived "
        f"of {checked} mutants checked.",
        "",
        "| module | killed | checked | score |",
        "|---|---:|---:|---:|",
    ]
    for name, (mk, mc) in sorted(per_module.items()):
        lines.append(f"| `{name}` | {mk} | {mc} | {mk / mc * 100:.1f}% |")
    lines += [
        "",
        "A surviving mutant is a change to the load-bearing core that no test "
        "objected to. Survivors are reported, never gating (Issue 273).",
    ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--min",
        type=int,
        default=1,
        help="minimum mutants that must have RUN; below this the gate failed to gate",
    )
    ap.add_argument("--summary", help="also append the report to this file (GITHUB_STEP_SUMMARY)")
    ap.add_argument("--mutants-dir", default=str(MUTANTS_DIR))
    args = ap.parse_args(argv[1:])

    mutants_dir = Path(args.mutants_dir)
    if not mutants_dir.is_dir():
        print(
            f"FAIL: {mutants_dir} does not exist — mutmut never built its sandbox.", file=sys.stderr
        )
        return 1

    checked, killed, survived, per_module = read_counts(mutants_dir)
    report = render(checked, killed, survived, per_module)
    print(report)
    if args.summary:
        with open(args.summary, "a") as fh:
            fh.write(report + "\n")

    if checked < args.min:
        print(
            f"\nFAIL: only {checked} mutant(s) ran (minimum {args.min}). A weekly "
            "report that cannot report is worse than no report — it says the tests "
            "assert on the core when nothing checked whether they do.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
