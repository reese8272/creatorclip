"""Unit tests for Issue 83 — Creator stated identity.

Covers the load-bearing pieces of dna/identity.py, dna/conflict.py, and the
identity endpoints in routers/creators.py — without touching real Postgres
(those are in test_identity_integration.py).
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from auth import get_current_creator
from db import get_session
from dna import conflict as conflict_module
from dna import identity as identity_module
from main import app
from models import Creator, CreatorDna, CreatorIdentity
from tests._helpers import override_current_creator

# ── Fixtures ─────────────────────────────────────────────────────────────────


def _make_creator() -> MagicMock:
    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    c.channel_title = "TestChannel"
    c.email = "test@example.com"
    return c


def _make_identity(creator_id: uuid.UUID, **overrides) -> CreatorIdentity:
    """Build a CreatorIdentity ORM object (not committed). Defaults sane; override anything.

    `created_at` is normally populated by SQLAlchemy at INSERT time — we set it
    explicitly here because the unit tests never go through the session, so the
    default factory wouldn't otherwise fire and `_identity_to_dict` would NPE.
    """
    defaults = dict(
        id=uuid.uuid4(),
        creator_id=creator_id,
        version=1,
        niches=["27"],  # Education
        audience_summary="College students learning to invest.",
        content_pillars=None,
        tone_tags=None,
        hard_nos=None,
        mission=None,
        style_sample=None,
        created_at=datetime.now(UTC),
        superseded_at=None,
    )
    defaults.update(overrides)
    return CreatorIdentity(**defaults)


@pytest.fixture(autouse=True)
def cleanup():
    yield
    app.dependency_overrides.clear()


# ── format_for_prompt ────────────────────────────────────────────────────────


def test_format_for_prompt_returns_none_when_no_identity():
    """None in → None out. The brief.py path skips the block entirely when None,
    which is better for prompt-cache hit rate than emitting '(no identity)'."""
    assert identity_module.format_for_prompt(None) is None


def test_format_for_prompt_renders_required_fields():
    cid = uuid.uuid4()
    block = identity_module.format_for_prompt(
        _make_identity(cid, niches=["27", "20"], audience_summary="Devs learning AI.")
    )
    assert block is not None
    assert "CREATOR-STATED IDENTITY" in block
    assert "Education" in block  # niche id "27" → "Education" label
    assert "Gaming" in block  # niche id "20" → "Gaming" label
    assert "Devs learning AI." in block


def test_format_for_prompt_skips_empty_optional_fields():
    """Optional fields that are empty/None must NOT appear as blank lines —
    they would hurt cache hit-rate by introducing per-creator empty markers."""
    cid = uuid.uuid4()
    block = identity_module.format_for_prompt(_make_identity(cid))
    assert block is not None
    assert "Mission" not in block
    assert "Content pillars" not in block
    assert "Style sample" not in block


def test_format_for_prompt_truncates_long_style_sample():
    cid = uuid.uuid4()
    long_sample = "word " * 500  # 2500 chars, well above the 600 limit
    block = identity_module.format_for_prompt(_make_identity(cid, style_sample=long_sample))
    assert block is not None
    # The "Style sample:" line should be present but trimmed.
    line = [line for line in block.split("\n") if line.startswith("- Style sample:")][0]
    assert len(line) < 700  # 600 char cap + "…" + label prefix
    assert line.endswith("…")


# ── validate_* ───────────────────────────────────────────────────────────────


def test_validate_niches_rejects_empty():
    with pytest.raises(ValueError, match="at least one"):
        identity_module.validate_niches([])


def test_validate_niches_rejects_more_than_3():
    with pytest.raises(ValueError, match="at most 3"):
        identity_module.validate_niches(["27", "20", "26", "23"])


def test_validate_niches_rejects_unknown_ids():
    with pytest.raises(ValueError, match="unknown niche"):
        identity_module.validate_niches(["27", "9999"])


def test_validate_niches_dedups_preserve_order():
    assert identity_module.validate_niches(["27", "20", "27"]) == ["27", "20"]


def test_validate_text_strips_and_enforces_max():
    assert identity_module.validate_text("  hi  ", max_chars=10, label="x") == "hi"
    with pytest.raises(ValueError, match="must not be empty"):
        identity_module.validate_text("   ", max_chars=10, label="x")
    with pytest.raises(ValueError, match="10 characters or fewer"):
        identity_module.validate_text("a" * 11, max_chars=10, label="x")


def test_validate_list_dedup_and_caps():
    # Dedup preserves first occurrence order; empty/None drops to None.
    assert identity_module.validate_list(["a", "b", "a"], label="x") == ["a", "b"]
    assert identity_module.validate_list(None, label="x") is None
    assert identity_module.validate_list([], label="x") is None
    assert identity_module.validate_list(["   ", "  "], label="x") is None
    with pytest.raises(ValueError, match="at most 10"):
        identity_module.validate_list([f"x{i}" for i in range(11)], label="x")


# ── conflict detection ──────────────────────────────────────────────────────


def test_detect_returns_none_when_no_identity_or_no_dna():
    cid = uuid.uuid4()
    assert conflict_module.detect(None, None) is None
    dna = MagicMock(spec=CreatorDna, patterns_jsonb={"top_videos": []})
    assert conflict_module.detect(None, dna) is None
    identity = _make_identity(cid)
    assert conflict_module.detect(identity, None) is None


def test_detect_returns_none_when_inferred_patterns_align_with_stated_niche():
    """Stated niche keywords appear in the inferred patterns → no conflict."""
    cid = uuid.uuid4()
    identity = _make_identity(cid, niches=["27"])  # Education
    dna = MagicMock(spec=CreatorDna)
    dna.patterns_jsonb = {
        "top_videos": [
            {"title": "How to teach yourself Python", "hook_text": "Today I'll explain..."},
            {"title": "Lessons learned", "hook_text": "What I learned..."},
        ],
        "bottom_videos": [],
    }
    assert conflict_module.detect(identity, dna) is None


def test_detect_flags_when_stated_niche_misses_inferred_patterns():
    """Creator says Education, top videos read like Gaming → flag a nudge."""
    cid = uuid.uuid4()
    identity = _make_identity(cid, niches=["27"])  # Education
    dna = MagicMock(spec=CreatorDna)
    dna.patterns_jsonb = {
        "top_videos": [
            {"title": "Speedrun World Record Attempt", "hook_text": "Let's go!"},
            {"title": "Boss fight breakdown", "hook_text": "Hardest boss ever..."},
        ],
        "bottom_videos": [],
    }
    nudge = conflict_module.detect(identity, dna)
    assert nudge is not None
    assert nudge.kind == "niche_mismatch"
    assert "Education" in nudge.message


def test_detect_returns_none_when_inferred_patterns_empty():
    """Empty top_videos → can't say there's a conflict. Don't false-flag."""
    cid = uuid.uuid4()
    identity = _make_identity(cid, niches=["27"])
    dna = MagicMock(spec=CreatorDna, patterns_jsonb={"top_videos": [], "bottom_videos": []})
    assert conflict_module.detect(identity, dna) is None


# ── Endpoint: GET /creators/niches ───────────────────────────────────────────


def test_niches_endpoint_returns_expected_shape():
    """The intake form depends on this — keep it returning {id, label} pairs."""
    client = TestClient(app)
    resp = client.get("/creators/niches")
    assert resp.status_code == 200
    data = resp.json()
    assert "options" in data
    assert len(data["options"]) >= 10
    # Each option is {id, label} and includes Education.
    ids = {o["id"] for o in data["options"]}
    assert "27" in ids
    assert all("id" in o and "label" in o for o in data["options"])


# ── Endpoint: POST /creators/me/identity validation ──────────────────────────


def _build_client_with_creator(creator):
    """TestClient with auth + a no-op session override. Tests that need the
    upsert path to actually run should mock dna.identity.upsert_identity."""

    async def fake_session():
        session = AsyncMock()
        yield session

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = fake_session
    return TestClient(app, raise_server_exceptions=True)


def test_post_identity_rejects_missing_niches(monkeypatch):
    creator = _make_creator()
    client = _build_client_with_creator(creator)
    resp = client.post(
        "/creators/me/identity",
        json={"niches": [], "audience_summary": "anyone"},
    )
    assert resp.status_code == 422


def test_post_identity_rejects_unknown_niche(monkeypatch):
    creator = _make_creator()
    client = _build_client_with_creator(creator)
    resp = client.post(
        "/creators/me/identity",
        json={"niches": ["9999"], "audience_summary": "anyone"},
    )
    assert resp.status_code == 422
    assert "unknown niche" in resp.json()["detail"].lower()


def test_post_identity_happy_path_calls_upsert(monkeypatch):
    creator = _make_creator()
    saved = _make_identity(
        creator.id,
        version=3,
        niches=["27"],
        audience_summary="College students learning AI.",
    )

    async def fake_upsert(session, cid, **kwargs):
        # Assert the router passed the validated payload through cleanly.
        assert cid == creator.id
        assert kwargs["niches"] == ["27"]
        assert kwargs["audience_summary"] == "College students learning AI."
        return saved

    monkeypatch.setattr(identity_module, "upsert_identity", fake_upsert)
    client = _build_client_with_creator(creator)

    resp = client.post(
        "/creators/me/identity",
        json={
            "niches": ["27"],
            "audience_summary": "College students learning AI.",
            "tone_tags": ["calm", "plainspoken"],
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["version"] == 3
    assert body["niches"] == ["27"]


# ── Endpoint: GET /creators/me/identity returns conflict line ────────────────


def test_get_identity_includes_conflict_nudge_when_detector_flags(monkeypatch):
    creator = _make_creator()
    cid = creator.id
    identity = _make_identity(cid, niches=["27"])
    fake_dna = MagicMock(spec=CreatorDna)
    fake_dna.patterns_jsonb = {
        "top_videos": [{"title": "Speedrun gameplay", "hook_text": "let's go"}],
        "bottom_videos": [],
    }

    async def fake_get_current(session, the_cid):
        assert the_cid == cid
        return identity

    async def fake_get_active(session, the_cid):
        return fake_dna

    monkeypatch.setattr(identity_module, "get_current", fake_get_current)
    # routers/creators.py imports get_active inside the handler — patch the
    # source module so the late import sees the patched function. Use the
    # fully-qualified `import dna.profile as dna_profile` so the local `dna`
    # variable in this test scope (if any) is not shadowed by the module.
    import dna.profile as dna_profile_module

    monkeypatch.setattr(dna_profile_module, "get_active", fake_get_active)

    client = _build_client_with_creator(creator)
    resp = client.get("/creators/me/identity")
    assert resp.status_code == 200
    body = resp.json()
    assert body["identity"]["version"] == 1
    assert body["conflict"] is not None
    assert "Education" in body["conflict"]


def test_get_identity_no_conflict_when_no_identity(monkeypatch):
    creator = _make_creator()

    async def fake_get_current(session, the_cid):
        return None

    monkeypatch.setattr(identity_module, "get_current", fake_get_current)
    client = _build_client_with_creator(creator)
    resp = client.get("/creators/me/identity")
    assert resp.status_code == 200
    assert resp.json() == {"identity": None, "conflict": None}


# ── brief.py wiring ─────────────────────────────────────────────────────────


async def test_generate_brief_includes_identity_block_with_cache_breakpoint(monkeypatch):
    """Issue 224 update: stated_identity is now in the user turn (not a system block)
    so the model receives it from the user role, not as trusted operator instructions.
    System blocks are now: (0) instructions (no cache_control — Issue 315), (1) volatile
    corpus. stated_identity must appear JSON-encoded in the user message content."""
    import json

    from dna import brief as brief_module

    captured: dict = {}

    class _FakeResponse:
        class usage:  # noqa: N801
            input_tokens = 0
            output_tokens = 0
            cache_read_input_tokens = 0
            cache_creation_input_tokens = 0

        class _Block:
            type = "text"
            text = "Brief body."

        content = [_Block()]

    async def fake_create(**kwargs):
        captured["system"] = kwargs["system"]
        captured["messages"] = kwargs["messages"]
        return _FakeResponse()

    monkeypatch.setattr(brief_module._ANTHROPIC.messages, "create", fake_create)

    identity_block = "CREATOR-STATED IDENTITY:\n- Niche(s): Education\n- Audience: devs"
    await brief_module.generate_brief(
        patterns={"top_videos": []},
        channel_title="TestChannel",
        stated_identity=identity_block,
    )

    system = captured["system"]
    messages = captured["messages"]

    # Issue 224: system has exactly 2 blocks — instructions and corpus.
    assert len(system) == 2, (
        "Issue 224: stated_identity was moved to the user turn. "
        "System must have exactly 2 blocks (instructions + corpus)."
    )
    assert "expert YouTube channel analyst" in system[0]["text"]
    # No cache_control on any block — prefix below 1024-token floor (Issue 315).
    assert "cache_control" not in system[0], (
        "Issue 315: dna/brief.py static block must have no cache_control — "
        "prefix ~570–650 tokens is below Sonnet 4.6's 1024-token cacheable floor."
    )
    # Corpus is last and uncached.
    assert system[1]["text"].startswith("CREATOR PERFORMANCE DATA:")
    assert "cache_control" not in system[1]

    # stated_identity must appear JSON-encoded in the user turn, not in any system block.
    user_content = messages[0]["content"]
    assert "creator_stated_identity" in user_content, (
        "Issue 224: user turn must contain the wrap_untrusted label for stated_identity."
    )
    assert json.dumps(identity_block) in user_content, (
        "Issue 224: stated_identity must be JSON-encoded in the user turn."
    )
    # Must NOT appear raw in any system block.
    for block in system:
        assert identity_block not in block["text"], (
            "Issue 224: stated_identity must not appear in any system block."
        )


async def test_generate_brief_skips_identity_block_when_none(monkeypatch):
    """No identity → just 2 system blocks (instructions + corpus). No cache_control
    on any block (Issue 315 — prefix ~570–650 tokens, below Sonnet 4.6's 1024-token
    cacheable floor; an inert marker charges the write-premium with zero reads)."""
    from dna import brief as brief_module

    captured: dict = {}

    class _FakeResponse:
        class usage:  # noqa: N801
            input_tokens = 0
            output_tokens = 0
            cache_read_input_tokens = 0
            cache_creation_input_tokens = 0

        class _Block:
            type = "text"
            text = "Brief body."

        content = [_Block()]

    async def fake_create(**kwargs):
        captured["system"] = kwargs["system"]
        return _FakeResponse()

    monkeypatch.setattr(brief_module._ANTHROPIC.messages, "create", fake_create)

    await brief_module.generate_brief(
        patterns={"top_videos": []},
        channel_title="TestChannel",
        stated_identity=None,
    )

    system = captured["system"]
    assert len(system) == 2
    # No cache_control on any block — prefix below 1024-token floor (Issue 315).
    assert "cache_control" not in system[0], (
        "Issue 315: dna/brief.py static block must have no cache_control — "
        "~570–650 tokens is below Sonnet 4.6's 1024-token cacheable floor."
    )
    assert system[1]["text"].startswith("CREATOR PERFORMANCE DATA:")
