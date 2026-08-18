"""The next free issue number must be derived, not remembered (2026-08-18).

Issue 498 item 6 deleted the competing copies of this number from ``LEFT_OFF.md``
and ``docs/PROJECT_STATE.md`` so ``docs/issues.md`` would be the sole authority.
It left the surviving copy as a hand-incremented literal, and it drifted inside
the same session: the line read 498 while issues 498-527 were filed in Lane L30,
so the next issue filed would have collided with #520.

Making one number authoritative did not make it correct. These tests derive it
instead, per the house rule "scan, don't list" (a gate whose scope is a literal
is a vacuous-green generator by construction).
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_DOCS = _REPO_ROOT / "docs"
_ISSUES = _DOCS / "issues.md"

# A filed issue is a markdown heading: "### Issue 527: <title>". Scanning every
# doc rather than a list of registry files is deliberate — a hardcoded file list
# is the exact anti-pattern this guard exists to retire, and it would go stale
# the first time a lane moved to its own file.
_HEADING = re.compile(r"^#{2,4}\s+Issue\s+(\d+)\b", re.MULTILINE)
_DECLARATION = re.compile(r"^-\s*Next free issue number:\s*\*\*(\d+)\*\*", re.MULTILINE)


def _markdown_sources() -> list[Path]:
    return sorted(_DOCS.rglob("*.md")) + [_REPO_ROOT / "LEFT_OFF.md"]


def _max_filed_issue() -> tuple[int, Path]:
    """Highest issue number carrying a heading anywhere in docs/, and its file."""
    best = (0, _ISSUES)
    for path in _DOCS.rglob("*.md"):
        for match in _HEADING.finditer(path.read_text(encoding="utf-8")):
            number = int(match.group(1))
            if number > best[0]:
                best = (number, path)
    return best


def test_next_free_issue_number_is_one_past_the_highest_filed() -> None:
    """The declaration must equal max(filed) + 1, or a new issue collides."""
    declared = _DECLARATION.search(_ISSUES.read_text(encoding="utf-8"))
    assert declared is not None, (
        "docs/issues.md must declare '- Next free issue number: **N**' — it is the "
        "sole authority (Issue 498 item 6) and every session reads it before filing."
    )
    highest, source = _max_filed_issue()
    expected = highest + 1
    assert int(declared.group(1)) == expected, (
        f"docs/issues.md declares next free issue **{declared.group(1)}**, but the "
        f"highest filed issue is #{highest} ({source.relative_to(_REPO_ROOT)}), so it "
        f"must read **{expected}**. Filing against a stale number collides with an "
        f"existing issue — update the declaration at the end of Lane L30."
    )


def test_the_number_is_declared_in_exactly_one_place() -> None:
    """Issue 498 item 6's real requirement, previously enforced by nothing.

    Three files once disagreed (LEFT_OFF said 497, PROJECT_STATE 443, issues.md
    498). The copies were deleted by hand; nothing stopped one coming back.
    """
    declaring = [
        path.relative_to(_REPO_ROOT)
        for path in _markdown_sources()
        if path.exists() and _DECLARATION.search(path.read_text(encoding="utf-8"))
    ]
    assert declaring == [_ISSUES.relative_to(_REPO_ROOT)], (
        f"'Next free issue number' must appear in docs/issues.md and nowhere else "
        f"(Issue 498 item 6). Found in: {declaring}"
    )
