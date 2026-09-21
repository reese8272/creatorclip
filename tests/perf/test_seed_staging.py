"""Tests for tests/perf/seed_staging.py (Issue 540).

Unit lane: argparse profile selection never touches the network, and the
default profile's DSN resolution is unchanged. Integration lane: `--profile
ui` is idempotent against the real local pg16 box — run twice, same row
counts, all three triage piles present.
"""

import os
import uuid

import psycopg
import pytest

from tests.perf.seed_staging import (
    _CREATOR_ID,
    _db_url,
    _parse_args,
    _seed,
    _seed_ui,
    _uid,
)

# ── argparse / DSN resolution — no network ──────────────────────────────────


def test_default_profile_when_flag_omitted():
    args = _parse_args(["postgresql://x/y"])
    assert args.profile == "default"
    assert args.database_url == "postgresql://x/y"


def test_ui_profile_selected_explicitly():
    args = _parse_args(["--profile", "ui", "postgresql://x/y"])
    assert args.profile == "ui"


def test_positional_database_url_still_works_alone():
    args = _parse_args(["postgresql://creatorclip:secret@localhost:5433/creatorclip_staging"])
    assert args.database_url == "postgresql://creatorclip:secret@localhost:5433/creatorclip_staging"


def test_db_url_converts_sqlalchemy_scheme():
    args = _parse_args(["postgresql+psycopg://x:y@localhost/db"])
    assert _db_url(args) == "postgresql://x:y@localhost/db"


def test_db_url_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@localhost/db")
    args = _parse_args([])
    assert _db_url(args) == "postgresql://x:y@localhost/db"


def test_uid_is_deterministic():
    assert _uid("clip", "a1") == _uid("clip", "a1")
    assert _uid("clip", "a1") != _uid("clip", "a2")
    # Real UUIDs, not opaque strings — every ui-profile FK depends on this.
    uuid.UUID(_uid("clip", "a1"))


# ── idempotency against a real local pg16 (skipped if unreachable) ──────────

_E2E_DSN = os.environ.get(
    "CC_E2E_DSN", "postgresql://creatorclip:dev_password@localhost:5432/creatorclip_e2e"
)


def _e2e_available() -> bool:
    try:
        with psycopg.connect(_E2E_DSN, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


@pytest.mark.integration
@pytest.mark.skipif(not _e2e_available(), reason="local pg16 creatorclip_e2e not reachable")
def test_seed_ui_profile_is_idempotent_against_real_db():
    with psycopg.connect(_E2E_DSN, autocommit=False) as conn:
        conn.execute("SELECT set_config('app.creator_id', %s, false)", (str(_CREATOR_ID),))
        video_ids = _seed(conn)
        _seed_ui(conn, video_ids)
        conn.commit()

        counts_1 = _table_counts(conn)

        _seed_ui(conn, video_ids)
        conn.commit()
        counts_2 = _table_counts(conn)

    assert counts_1 == counts_2
    piles = counts_1["triage_piles"]
    assert {"pending", "kept", "dropped"} <= set(piles)


def _table_counts(conn: psycopg.Connection) -> dict:
    tables = [
        "clips",
        "transcripts",
        "clip_edit_documents",
        "summaries",
        "notifications",
        "notification_deliveries",
        "clip_outcomes",
        "clip_publications",
        "creator_insights",
        "retention_curves",
    ]
    counts = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}  # noqa: S608
    piles = {row[0] for row in conn.execute("SELECT DISTINCT triage FROM clips").fetchall()}
    counts["triage_piles"] = piles
    return counts
