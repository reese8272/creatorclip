"""Structural guard: a generator without a DNA brief must not claim channel grounding.

The defect this locks out (fixed 2026-08-14). Seven prompt builders shared the
line ``dna_text = (dna_brief or "No DNA profile available yet.")`` and injected
that placeholder as a ``CREATOR DNA PROFILE:`` system block — directly beneath
static instructions telling the model to rank "for THIS channel" and to make
``based_on_pattern`` "reference the channel's actual observed patterns, not
generic advice". Nothing branched on the placeholder. Python then appended a
disclaimer saying the output was "grounded in your channel data".

So a creator with no DNA profile received ranked, rationale-bearing, CTR-signalled
output generated from nothing but a clip transcript, labelled as personalized.
That is a correctness bug and — given the product's north star is "the only AI
editor that truly knows your channel" — an honesty bug.

These tests are deliberately structural rather than example-based: they iterate
the builders so a NEW generator that reintroduces the placeholder fails here
without anyone remembering to add a case.
"""

import pytest

from knowledge.util import UNGROUNDED_DISCLAIMER, dna_system_block, grounding_disclaimer

# (module path, callable name, kwargs that vary only by dna_brief)
_BUILDERS = [
    ("knowledge.clip_titles", {"channel_title": "C", "clip_transcript": "some words"}),
    ("knowledge.clip_captions", {"channel_title": "C", "clip_hook": "some words"}),
    (
        "knowledge.clip_explain",
        {
            "channel_title": "C",
            "clip_principle": "Audience-fit over generic virality",
            "clip_score": 0.5,
            "clip_start_s": 0.0,
            "clip_end_s": 30.0,
            "clip_transcript": "some words",
        },
    ),
    (
        "knowledge.clip_metadata",
        {
            "channel_title": "C",
            "context_summary": None,
            "clip_payloads": [
                {"clip_id": "c1", "rank": 1, "window_text": "w", "opening_text": "o"}
            ],
        },
    ),
    (
        "knowledge.titles",
        {
            "channel_title": "C",
            "stated_identity": None,
            "video_title": "T",
            "transcript_summary": "s",
        },
    ),
]

_A_BRIEF = "This creator makes long-form basketball breakdowns for a US audience. " * 8


def _system_blocks(result) -> list[dict]:
    """First element of the builder's tuple is always the system-block list."""
    return result[0]


@pytest.mark.parametrize("module_path,kwargs", _BUILDERS)
def test_builder_omits_dna_block_entirely_when_there_is_no_brief(module_path, kwargs):
    import importlib

    build = importlib.import_module(module_path)._build_request
    blocks = _system_blocks(build(dna_brief=None, **kwargs))
    joined = "\n".join(b.get("text", "") for b in blocks)

    assert not any("CREATOR DNA PROFILE" in b.get("text", "") for b in blocks), (
        f"{module_path} still emits a DNA block with no brief — omit it instead"
    )
    assert "No DNA profile available yet" not in joined, (
        f"{module_path} reintroduced the placeholder: it tells the model there is no "
        "profile while the static instructions above still command it to cite one"
    )


@pytest.mark.parametrize("module_path,kwargs", _BUILDERS)
def test_builder_includes_dna_block_when_a_brief_exists(module_path, kwargs):
    """The complement — proves the guard above isn't passing vacuously."""
    import importlib

    build = importlib.import_module(module_path)._build_request
    blocks = _system_blocks(build(dna_brief=_A_BRIEF, **kwargs))

    assert any("CREATOR DNA PROFILE" in b.get("text", "") for b in blocks), (
        f"{module_path} dropped the DNA block even though a brief was supplied"
    )


def test_dna_system_block_returns_none_for_absent_or_blank_brief():
    assert dna_system_block("static", None) is None
    assert dna_system_block("static", "") is None
    assert dna_system_block("static", "   \n ") is None
    block = dna_system_block("static", "a real brief")
    assert block is not None and "a real brief" in block["text"]


def test_cache_marker_is_not_written_for_an_absent_brief():
    """A 1h cache write bills at 2× base input. An omitted block must not trigger one.

    The floor gate already declined to mark a placeholder-only prefix, but the
    block was still SENT; returning None means there is nothing to mark at all.
    """
    assert dna_system_block("x" * 100_000, None) is None


def test_disclaimer_tracks_grounding_and_never_claims_channel_data_when_ungrounded():
    grounded = "These suggestions are estimates grounded in your channel data."
    assert grounding_disclaimer(grounded, is_grounded=True) == grounded

    ungrounded = grounding_disclaimer(grounded, is_grounded=False)
    assert ungrounded == UNGROUNDED_DISCLAIMER
    assert "grounded in your channel" not in ungrounded.lower()
    # CLAUDE.md honesty constraint applies to the fallback copy too.
    for banned in ("viral", "guarantee", "guaranteed", "will get"):
        assert banned not in ungrounded.lower()


def test_thumbnail_prompt_forbids_inventing_a_pattern_when_none_were_observed():
    """The highest-severity case: a fabricated citation of channel data.

    `worker.tasks._generate_thumbnail_concepts_async` used to replace a missing
    analysis with an all-"unknown" dict and feed it to the model as fact, while
    the static rules demanded `based_on_pattern` "reference the channel's actual
    observed patterns, not generic advice". The UI renders the result as
    "Based on: {based_on_pattern}". `patterns=None` now means "we never looked".
    """
    from knowledge.thumbnails import _build_concepts_request

    system, _, _ = _build_concepts_request(
        channel_title="C",
        dna_brief=None,
        patterns=None,
        transcript_hook="",
        stated_identity=None,
    )
    joined = "\n".join(b["text"] for b in system)
    assert "NONE OBSERVED" in joined
    assert "Do not invent" in joined
    # And the static rule must not still issue a bare, unconditional command to
    # cite a real pattern — that would contradict the block above it.
    assert "UNLESS" in joined


def test_thumbnail_prompt_uses_real_patterns_when_they_exist():
    """Complement — proves the guard above isn't passing vacuously."""
    from knowledge.thumbnails import _build_concepts_request

    system, _, _ = _build_concepts_request(
        channel_title="C",
        dna_brief=None,
        patterns={
            "face_present": "always",
            "dominant_emotions": ["surprise"],
            "text_overlay_style": "bold_caps",
            "typical_colors": "high-contrast orange and navy",
            "composition_pattern": "subject left, text right",
            "channel_thumbnail_signature": "Bold face thumbnails.",
        },
        transcript_hook="",
        stated_identity=None,
    )
    joined = "\n".join(b["text"] for b in system)
    assert "NONE OBSERVED" not in joined
    assert "bold_caps" in joined


@pytest.mark.parametrize(
    "module_path",
    ["knowledge.clip_titles", "knowledge.clip_captions", "knowledge.clip_explain"],
)
def test_parse_result_swaps_the_disclaimer_when_ungrounded(module_path):
    """The three generators that return a disclaimer to the client must swap it."""
    import importlib

    mod = importlib.import_module(module_path)
    payloads = {
        "knowledge.clip_titles": '{"titles":[{"title":"T","rationale":"r","ctr_signal":"up"}],'
        '"hook_rewrites":[{"rewrite":"x","rationale":"r"}]}',
        "knowledge.clip_captions": '{"options":[{"text":"T","rationale":"r"}]}',
        "knowledge.clip_explain": '{"explanation":"e",'
        '"cited_principle":"Audience-fit over generic virality"}',
    }
    raw = payloads[module_path]

    assert mod._parse_result(raw, is_grounded=True)["disclaimer"] == mod.DISCLAIMER
    assert mod._parse_result(raw, is_grounded=False)["disclaimer"] == UNGROUNDED_DISCLAIMER


# ── Per-clip transcript grounding ─────────────────────────────────────────────
#
# Issue 414 fixed per-clip windowing for POST /clips/{id}/title-suggestions but
# was never applied to its three siblings. Until 2026-08-14, caption-hooks,
# explanation, and the chat `suggest_clip_titles` tool all called
# extract_transcript_text(whole_video, N) — the video's OPENING, truncated — so a
# rank-8 clip at 42:00 got overlay text and an explanation written about minute 0
# while the prompt claimed to describe that clip.


def _clip_grounding_sources() -> dict[str, str]:
    """Source of each route/tool body that grounds an LLM call in a clip transcript."""
    import inspect

    import chat.tools as chat_tools
    import routers.clips as clips

    return {
        "clips.get_clip_title_suggestions": inspect.getsource(clips.get_clip_title_suggestions),
        "clips.get_clip_caption_hooks": inspect.getsource(clips.get_clip_caption_hooks),
        "clips.get_clip_explanation": inspect.getsource(clips.get_clip_explanation),
        "chat._suggest_clip_titles": inspect.getsource(chat_tools._suggest_clip_titles),
    }


def _code_only(src: str) -> str:
    """Drop comment lines — the fix's own comments name the banned call."""
    return "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))


@pytest.mark.parametrize("name", list(_clip_grounding_sources()))
def test_clip_llm_calls_use_the_clips_own_window_not_the_whole_video(name):
    src = _code_only(_clip_grounding_sources()[name])
    assert "extract_transcript_window(" in src, (
        f"{name} must ground its LLM call in the clip's own [setup_start_s ?? start_s, "
        "end_s] window"
    )
    assert "extract_transcript_text(" not in src, (
        f"{name} uses extract_transcript_text — that is the WHOLE video truncated to "
        "its opening, so every clip is described using minute 0"
    )


def test_extract_transcript_window_actually_isolates_a_late_clip():
    """Behavioural complement to the source-level guard above."""
    from knowledge.util import extract_transcript_window

    segments = {
        "segments": [
            {"start": 0.0, "end": 5.0, "text": "intro words at the very beginning"},
            {"start": 2520.0, "end": 2525.0, "text": "the late clip payload"},
        ]
    }
    late = extract_transcript_window(segments, 2515.0, 2530.0)
    assert "late clip payload" in late
    assert "intro words" not in late
