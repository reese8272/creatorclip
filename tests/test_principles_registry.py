"""The principles registry is intentionally duplicated in four code sites; the
DOC was the one uncovered copy (Issue 482). This parses docs/CLIPPING_PRINCIPLES.md
and pins every code copy against it, so adding a principle in one place without
the others — or editing the doc's canonical names — fails loudly.
"""

import pathlib
import re

_REPO_ROOT = pathlib.Path(__file__).parent.parent


def _doc_principles() -> set[str]:
    text = (_REPO_ROOT / "docs" / "CLIPPING_PRINCIPLES.md").read_text()
    names = re.findall(r"^\|\s*\d+\s*\|\s*\*\*(.+?)\*\*\s*\|", text, re.M)
    assert names, "no principle rows parsed from docs/CLIPPING_PRINCIPLES.md"
    return set(names)


def test_doc_registry_matches_every_code_copy():
    from analysis.video_context import CLIPPING_PRINCIPLES
    from clip_engine.scoring import _PRINCIPLES
    from knowledge.clip_explain import VALID_PRINCIPLES
    from tests.test_clip_engine import NAMED_PRINCIPLES

    doc = _doc_principles()
    copies = {
        "clip_engine/scoring._PRINCIPLES": set(_PRINCIPLES),
        "analysis/video_context.CLIPPING_PRINCIPLES": set(CLIPPING_PRINCIPLES),
        "knowledge/clip_explain.VALID_PRINCIPLES": set(VALID_PRINCIPLES),
        "tests/test_clip_engine.NAMED_PRINCIPLES": set(NAMED_PRINCIPLES),
    }
    for site, names in copies.items():
        assert names == doc, (
            f"{site} diverges from docs/CLIPPING_PRINCIPLES.md.\n"
            f"  only in code: {sorted(names - doc)}\n"
            f"  only in doc:  {sorted(doc - names)}\n"
            "The registry is deliberately duplicated — update ALL copies AND the "
            "doc in one commit."
        )


def test_scoring_schema_enum_is_built_from_the_registry():
    """The 461 structured-output enum must be derived, never hand-listed."""
    from clip_engine.scoring import _OUTPUT_SCHEMA, _PRINCIPLES

    enum = _OUTPUT_SCHEMA["properties"]["scores"]["items"]["properties"]["principle"]["enum"]
    assert set(enum) == set(_PRINCIPLES)
