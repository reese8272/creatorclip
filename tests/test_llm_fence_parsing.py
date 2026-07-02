"""Issue 342 — regression: JSON-returning LLM parsers tolerate a markdown fence.

The live API (`claude-sonnet-4-6`) frequently wraps its JSON in a ```json … ```
fence. The generators previously did a bare ``json.loads(raw)`` and crashed with
``JSONDecodeError: Expecting value: line 1 column 1`` — a failure only the LIVE
API surfaced (the Issue 341 smoke harness caught it; mocked unit tests feed clean
JSON and never did). Every JSON parser now routes through ``extract_json_block``
(and the no-citation clip generators additionally constrain output via
``output_config.format``). These tests feed FENCED input straight to the parsers
and assert they no longer raise — the unit-lane guard that was missing.
"""

from __future__ import annotations

import json


def _fence(obj: object) -> str:
    """Wrap a JSON payload the way the live model does."""
    return f"```json\n{json.dumps(obj)}\n```"


def test_clip_titles_parser_tolerates_fence() -> None:
    from knowledge.clip_titles import _parse_result

    raw = _fence(
        {
            "titles": [
                {"title": "3-Ingredient Pasta", "rationale": "fits DNA", "ctr_signal": "up"}
            ],
            "hook_rewrites": [{"rewrite": "Only 3 things", "rationale": "tighter"}],
        }
    )
    result = _parse_result(raw)  # was JSONDecodeError before Issue 342
    assert result["titles"][0]["title"] == "3-Ingredient Pasta"
    assert result["titles"][0]["ctr_signal"] == "up"


def test_clip_captions_parser_tolerates_fence() -> None:
    from knowledge.clip_captions import _parse_result

    raw = _fence({"options": [{"text": "You only need 3 things", "rationale": "curiosity"}]})
    result = _parse_result(raw)
    assert result["options"][0]["text"] == "You only need 3 things"


def test_clip_explain_parser_tolerates_fence() -> None:
    from knowledge.clip_explain import _parse_result

    raw = _fence(
        {
            "explanation": "Starts at the setup so the payoff lands.",
            "cited_principle": "Clip the setup, not the aftermath",
        }
    )
    result = _parse_result(raw)
    assert result["cited_principle"] == "Clip the setup, not the aftermath"


def test_hooks_parser_tolerates_fence() -> None:
    from knowledge.hooks import parse_hook_report

    raw = _fence(
        {
            "diagnosis": "Hook is slow.",
            "rewrite_suggestion": "Open on the reveal.",
            "honesty_disclaimer": "This is an estimate grounded in your data.",
            "transcript_at_drop": "...and then...",
        }
    )
    result = parse_hook_report(raw)  # web_search path → extract_json_block layer
    assert result["diagnosis"] == "Hook is slow."


def test_chapters_parser_tolerates_fence() -> None:
    from knowledge.chapters import parse_chapters

    raw = _fence(
        {
            "chapters": [{"start_s": 0.0, "title": "Intro"}],
            "description_block": "0:00 Intro",
        }
    )
    result = parse_chapters(raw)
    assert result["chapters"][0]["title"] == "Intro"


def test_extract_json_block_is_the_shared_layer() -> None:
    """Lock the helper's fence-stripping + bare-array handling (scoring's path)."""
    from knowledge.util import extract_json_block

    assert json.loads(extract_json_block('```json\n[{"i": 1}]\n```')) == [{"i": 1}]
    # No fence → returned unchanged so a genuinely malformed payload still raises clearly.
    assert extract_json_block('{"a": 1}') == '{"a": 1}'
