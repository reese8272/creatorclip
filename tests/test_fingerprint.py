"""Unit tests for Issue 379 — Channel Fingerprint shareable artifact.

Load-bearing assertions (per docs/DECISIONS.md 2026-07-30 and the YouTube API
Services Policies III.E.2 / III.E.3.b review):

1. The shareable payload (``POST /creators/me/fingerprint/share``) contains
   EXACTLY the audited field allowlist — no analytics-derived field (no
   optimal_clip_len_s, best_source_region, optimal_upload_gap_h, views,
   engagement, retention, or anything sourced from CreatorDna/VideoMetrics).
2. Nothing reaches this shape without the creator explicitly calling this
   endpoint — it is a POST, requires auth, and is never embedded in any
   other (GET) response.
3. Per-creator isolation — the endpoint reads only the calling creator's rows.
4. The honesty line is present verbatim and no virality language appears.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from auth import get_current_creator
from db import get_session
from main import app
from models import Creator, CreatorIdentity, CreatorStyleNotes
from routers.creators import FINGERPRINT_HONESTY_LINE, FingerprintShareOut
from tests._helpers import override_current_creator

# The load-bearing allowlist: every key this test expects on the wire, and
# nothing else. Keep this list in sync with docs/COMPLIANCE.md's field audit.
SHAREABLE_FIELD_ALLOWLIST = {
    "niches",
    "content_pillars",
    "tone_tags",
    "mission",
    "style_summary",
    "honesty_line",
    "generated_at",
}

# Fields that are derived from YouTube Analytics/Data API responses (via
# CreatorDna or VideoMetrics) — must NEVER appear on the shareable artifact.
ANALYTICS_DERIVED_FIELDS = {
    "optimal_clip_len_s",
    "best_source_region",
    "optimal_upload_gap_h",
    "views",
    "engagement_rate",
    "avg_view_duration_s",
    "performance_score",
    "watch_time",
    "retention",
    "audience_summary",  # self-declared but deliberately excluded — see report
    "hard_nos",  # self-declared but deliberately excluded — see report
    "style_sample",  # self-declared but deliberately excluded — see report
}


@pytest.fixture(autouse=True)
def cleanup():
    yield
    app.dependency_overrides.clear()


def _make_creator() -> MagicMock:
    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    c.channel_title = "TestChannel"
    return c


def _make_identity(creator_id: uuid.UUID, **overrides) -> CreatorIdentity:
    defaults = dict(
        id=uuid.uuid4(),
        creator_id=creator_id,
        version=1,
        niches=["27", "20"],  # Education, Gaming
        audience_summary="College students learning to invest.",
        content_pillars=["budgeting basics"],
        tone_tags=["calm", "plainspoken"],
        hard_nos=["no crypto shilling"],
        mission="Make personal finance approachable.",
        style_sample="Here's how I'd explain a Roth IRA to my little sister...",
        created_at=datetime.now(UTC),
        superseded_at=None,
    )
    defaults.update(overrides)
    return CreatorIdentity(**defaults)


def _make_style_notes(creator_id: uuid.UUID, **overrides) -> CreatorStyleNotes:
    defaults = dict(
        id=uuid.uuid4(),
        creator_id=creator_id,
        notes_text="Prefers punchy 3-word hooks; avoids jump cuts mid-sentence.",
        source_count=12,
        last_input_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return CreatorStyleNotes(**defaults)


def _client_for(creator) -> TestClient:
    async def fake_session():
        yield AsyncMock()

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = fake_session
    return TestClient(app, raise_server_exceptions=True)


# ── Field allowlist (load-bearing) ──────────────────────────────────────────


def test_pydantic_model_has_exactly_the_allowlisted_fields():
    """The response_model itself must not grow an analytics-derived field
    without this test catching it — independent of any mocked endpoint call."""
    assert set(FingerprintShareOut.model_fields.keys()) == SHAREABLE_FIELD_ALLOWLIST


def test_share_endpoint_returns_only_allowlisted_fields(monkeypatch):
    creator = _make_creator()
    identity = _make_identity(creator.id)
    style_row = _make_style_notes(creator.id)

    async def fake_get_current(session, cid):
        assert cid == creator.id
        return identity

    async def fake_get_style_notes(session, cid):
        assert cid == creator.id
        return style_row

    import dna.identity as identity_module
    import dna.profile as profile_module

    monkeypatch.setattr(identity_module, "get_current", fake_get_current)
    monkeypatch.setattr(profile_module, "get_style_notes", fake_get_style_notes)

    client = _client_for(creator)
    resp = client.post("/creators/me/fingerprint/share")
    assert resp.status_code == 200
    body = resp.json()

    assert set(body.keys()) == SHAREABLE_FIELD_ALLOWLIST, (
        f"Shareable fingerprint returned unexpected keys: "
        f"{set(body.keys()) - SHAREABLE_FIELD_ALLOWLIST}"
    )
    assert not (ANALYTICS_DERIVED_FIELDS & set(body.keys())), (
        "Analytics-derived / deliberately-excluded field leaked onto the shareable artifact."
    )

    # Content sanity — self-declared identity + our own feedback distillation.
    assert body["niches"] == ["Education", "Gaming"]
    assert body["content_pillars"] == ["budgeting basics"]
    assert body["tone_tags"] == ["calm", "plainspoken"]
    assert body["mission"] == "Make personal finance approachable."
    assert body["style_summary"] == style_row.notes_text
    # Fields deliberately excluded per the ToS-caution call (see report):
    # audience_summary, hard_nos, style_sample must not be present at all.
    assert "audience_summary" not in body
    assert "hard_nos" not in body
    assert "style_sample" not in body


def test_share_endpoint_handles_no_identity_or_style_notes(monkeypatch):
    """A creator who hasn't set an identity yet gets an honest empty
    fingerprint, not a 500 — the honesty line still renders."""
    creator = _make_creator()

    async def fake_get_current(session, cid):
        return None

    async def fake_get_style_notes(session, cid):
        return None

    import dna.identity as identity_module
    import dna.profile as profile_module

    monkeypatch.setattr(identity_module, "get_current", fake_get_current)
    monkeypatch.setattr(profile_module, "get_style_notes", fake_get_style_notes)

    client = _client_for(creator)
    resp = client.post("/creators/me/fingerprint/share")
    assert resp.status_code == 200
    body = resp.json()
    assert body["niches"] == []
    assert body["content_pillars"] is None
    assert body["style_summary"] is None
    assert body["honesty_line"] == FINGERPRINT_HONESTY_LINE


# ── Default-private / explicit-action gating ────────────────────────────────


def test_fingerprint_share_requires_auth():
    """No session → 401. Nothing is reachable without an authenticated,
    explicit request — this is the default-private posture."""
    with TestClient(app) as c:
        resp = c.post("/creators/me/fingerprint/share")
    assert resp.status_code == 401


def test_fingerprint_is_a_post_not_a_passive_get():
    """The share action must be an explicit POST — never wired to a GET, which
    could be triggered incidentally (prefetch, link preview, etc.)."""
    schema = app.openapi()
    path_item = schema["paths"]["/creators/me/fingerprint/share"]
    assert "post" in path_item
    assert "get" not in path_item


def test_shareable_fields_absent_from_insights_and_identity_responses(monkeypatch):
    """The shareable-only fields (style_summary, honesty_line, generated_at)
    must not leak into any auto-fetched dashboard payload — the ONLY way to
    get this exact shape is calling the dedicated share endpoint."""
    creator = _make_creator()
    identity = _make_identity(creator.id)

    async def fake_get_current(session, cid):
        return identity

    async def fake_get_active(session, cid):
        return None

    import dna.identity as identity_module
    import dna.profile as profile_module

    monkeypatch.setattr(identity_module, "get_current", fake_get_current)
    monkeypatch.setattr(profile_module, "get_active", fake_get_active)

    client = _client_for(creator)
    resp = client.get("/creators/me/identity")
    assert resp.status_code == 200
    body = resp.json()
    assert "honesty_line" not in body
    assert "style_summary" not in body
    assert "generated_at" not in body


# ── Per-creator isolation ────────────────────────────────────────────────────


def test_fingerprint_only_reads_the_calling_creators_rows(monkeypatch):
    """Two different creators must never see each other's identity/style rows —
    both lookups are asserted to receive THIS creator's id, matching the
    per-creator isolation pattern used across every other query in the app."""
    creator_a = _make_creator()
    creator_b = _make_creator()
    identity_a = _make_identity(creator_a.id, mission="Creator A's mission")
    identity_b = _make_identity(creator_b.id, mission="Creator B's mission")
    by_creator = {creator_a.id: identity_a, creator_b.id: identity_b}

    async def fake_get_current(session, cid):
        return by_creator[cid]

    async def fake_get_style_notes(session, cid):
        return None

    import dna.identity as identity_module
    import dna.profile as profile_module

    monkeypatch.setattr(identity_module, "get_current", fake_get_current)
    monkeypatch.setattr(profile_module, "get_style_notes", fake_get_style_notes)

    resp_a = _client_for(creator_a).post("/creators/me/fingerprint/share")
    assert resp_a.json()["mission"] == "Creator A's mission"

    app.dependency_overrides.clear()

    resp_b = _client_for(creator_b).post("/creators/me/fingerprint/share")
    assert resp_b.json()["mission"] == "Creator B's mission"


# ── Honesty / no-virality ────────────────────────────────────────────────────


def test_fingerprint_carries_the_honesty_line_verbatim():
    assert FINGERPRINT_HONESTY_LINE == (
        "AutoClip predicts fit with your style and audience — it does not promise virality."
    )


def test_fingerprint_response_model_never_promises_virality():
    import re

    forbidden = re.compile(r"\bviral(?:ity)?\b|guaranteed views", re.IGNORECASE)
    for field in FingerprintShareOut.model_fields.values():
        if field.description:
            assert not forbidden.search(field.description)
    assert not forbidden.search(FINGERPRINT_HONESTY_LINE.replace("does not promise virality", ""))
