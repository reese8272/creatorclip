"""The published eval number must never drift from reality (Issue 378).

The landing page states a scenario count and an all-pass claim. That is a PUBLIC
claim about our own product on a page a Google reviewer reads, so it cannot be a
hand-maintained string that silently rots when someone adds a scenario file.

These tests fail CI if the page and the harness disagree. Adding a scenario is
supposed to force a page edit — that is the point, not an inconvenience.
"""

import glob
import os
import re

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LANDING = os.path.join(_REPO_ROOT, "static", "landing.html")
_SCENARIOS_DIR = os.path.join(_REPO_ROOT, "tests", "eval", "scenarios")


def _scenario_files() -> list[str]:
    return sorted(glob.glob(os.path.join(_SCENARIOS_DIR, "*.yaml")))


def _landing_html() -> str:
    with open(_LANDING, encoding="utf-8") as fh:
        return fh.read()


def test_published_scenario_count_matches_the_harness() -> None:
    """The count on the landing page must equal the real number of scenario files."""
    actual = len(_scenario_files())
    html = _landing_html()

    claims = re.findall(r"(\d+)\s+adversarial evaluation scenarios", html)
    assert claims, "landing page no longer states an adversarial-scenario count"
    for claim in claims:
        assert int(claim) == actual, (
            f"Landing page claims {claim} eval scenarios but {actual} exist in "
            f"{_SCENARIOS_DIR}. Update static/landing.html — this is a public claim."
        )

    all_pass = re.findall(r"all\s+(\d+)\s+pass", html)
    for claim in all_pass:
        assert int(claim) == actual, (
            f"Landing page claims 'all {claim} pass' but {actual} scenarios exist."
        )


def test_named_scenarios_on_the_landing_page_actually_exist() -> None:
    """Any scenario named as an example must be a real file.

    Naming a scenario that does not exist would be fabricated evidence on a page
    whose entire argument is 'this claim is checkable'.
    """
    stems = {os.path.splitext(os.path.basename(p))[0] for p in _scenario_files()}
    html = _landing_html()
    named = set(re.findall(r"<code>([a-z0-9_]+)</code>", html))
    unknown = {n for n in named if n not in stems}
    assert not unknown, (
        f"Landing page names scenario(s) that do not exist: {sorted(unknown)}. "
        f"Available: {sorted(stems)}"
    )


def test_landing_states_the_no_pooled_aggregate_position() -> None:
    """The page must keep stating WHY we publish no cross-creator number.

    Asserting the disclaimer is present, rather than banning phrases: the words
    "pooled across creators" legitimately appear inside the disclaimer itself, so
    a substring blocklist cannot tell a claim from its denial (it failed exactly
    that way when first written). Pinning the commitment is the durable check —
    if someone deletes the ToS paragraph in order to publish a pooled figure,
    this fails.
    """
    # Strip tags and collapse whitespace first: the copy contains inline emphasis
    # ("do <em>not</em> publish"), so a raw substring match on the HTML misses it.
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", _landing_html())).lower()
    html = text
    assert "do not publish" in html and "pooled across creators" in html, (
        "the landing page no longer states that we publish no pooled cross-creator "
        "aggregate — required by YouTube Developer Policies III.E.2"
    )
    assert "content owner" in html, "the ToS basis for the position is no longer stated"


@pytest.mark.parametrize("path", _scenario_files())
def test_every_scenario_file_is_readable_yaml(path: str) -> None:
    """The published claim is 'the scenarios live in the codebase as readable files'.

    An unreadable or empty scenario would make that claim false.
    """
    with open(path, encoding="utf-8") as fh:
        body = fh.read()
    assert body.strip(), f"{path} is empty"
    assert "scenario:" in body, f"{path} has no scenario key"


def test_scenario_floor_is_pinned_in_two_places() -> None:
    """Issue 477: SCENARIO_FLOOR guards deletion but nothing guarded LOWERING it
    in the same commit as a scenario removal. Pinning the exact value here means
    a floor change must touch two files — visible in any diff. Raise BOTH
    together when adding scenarios; lowering is never allowed (the ratchet)."""
    from tests.test_clip_engine import SCENARIO_FLOOR

    assert SCENARIO_FLOOR == 23, (
        f"SCENARIO_FLOOR is {SCENARIO_FLOOR}, but this pin says 23. If you ADDED "
        "scenarios, raise both values in the same commit. If this fails because "
        "the floor went DOWN, that is exactly the regression this test exists to stop."
    )
