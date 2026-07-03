"""Unit tests for scripts/check_downgrades.py (Issue 296).

80/20 contract: a pass-only downgrade is flagged, a raise-only downgrade is
flagged, an allowlisted irreversible migration passes, and a STALE allowlist
entry (real downgrade) fails."""

from pathlib import Path

from scripts.check_downgrades import check

_REAL_DOWNGRADE = '''"""Test migration."""
revision = "0002_real"
down_revision = "0001_noop"


def upgrade() -> None:
    pass


def downgrade() -> None:
    op.drop_column("videos", "extra")
'''

_PASS_ONLY_DOWNGRADE = '''"""Test migration."""
revision = "0001_noop"
down_revision = None


def upgrade() -> None:
    op.execute("UPDATE creators SET onboarding_state = 'active'")


def downgrade() -> None:
    # Not safely reversible.
    pass
'''

_RAISE_DOWNGRADE = '''"""Test migration."""
revision = "0003_raise"
down_revision = "0002_real"


def upgrade() -> None:
    op.execute("SELECT 1")


def downgrade() -> None:
    raise NotImplementedError("irreversible")
'''


def _versions(tmp_path: Path, sources: dict[str, str]) -> Path:
    versions = tmp_path / "versions"
    versions.mkdir()
    for name, source in sources.items():
        (versions / name).write_text(source)
    return versions


def _allowlist(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "DOWNGRADE_EXCEPTIONS"
    path.write_text(content)
    return path


def test_pass_only_downgrade_detected(tmp_path: Path) -> None:
    versions = _versions(tmp_path, {"0001_noop.py": _PASS_ONLY_DOWNGRADE})
    errors = check([versions / "0001_noop.py"], _allowlist(tmp_path, ""), versions)
    assert len(errors) == 1
    assert "no-op or raises" in errors[0]


def test_raise_downgrade_detected(tmp_path: Path) -> None:
    versions = _versions(tmp_path, {"0003_raise.py": _RAISE_DOWNGRADE})
    errors = check([versions / "0003_raise.py"], _allowlist(tmp_path, ""), versions)
    assert len(errors) == 1
    assert "no-op or raises" in errors[0]


def test_allowlisted_irreversible_passes(tmp_path: Path) -> None:
    """Both allowlist forms work: numeric filename prefix and exact revision id."""
    versions = _versions(
        tmp_path,
        {"0001_noop.py": _PASS_ONLY_DOWNGRADE, "0003_raise.py": _RAISE_DOWNGRADE},
    )
    allowlist = _allowlist(
        tmp_path,
        "0001  # backfill not safely reversible\n0003_raise  # dev-only escape\n",
    )
    errors = check([versions / "0001_noop.py", versions / "0003_raise.py"], allowlist, versions)
    assert errors == []


def test_stale_allowlist_entry_fails(tmp_path: Path) -> None:
    """A listed revision whose downgrade() is real must fail — even when the
    file itself is not in the changed set."""
    versions = _versions(
        tmp_path,
        {"0001_noop.py": _PASS_ONLY_DOWNGRADE, "0002_real.py": _REAL_DOWNGRADE},
    )
    allowlist = _allowlist(tmp_path, "0001  # ok\n0002_real  # stale — downgrade is real\n")
    errors = check([], allowlist, versions)
    assert len(errors) == 1
    assert "STALE" in errors[0]
    assert "0002_real" in errors[0]
