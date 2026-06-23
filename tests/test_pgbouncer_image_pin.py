"""
Grep-assert tests for Issue 264 — PgBouncer image reconciliation.

Guards:
- Only edoburu/pgbouncer appears as the image in values.yaml,
  values.prod.yaml, and docker-compose.staging.yml (no bitnami).
- Every pgbouncer image reference is digest-pinned (@sha256:).
- All three files reference the same digest (single source of truth).
- SOT.md no longer says TOKEN_ENCRYPTION_KEY rotation runbook is "not yet written".
"""

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

VALUES_YAML = REPO_ROOT / "deploy/charts/creatorclip/values.yaml"
VALUES_PROD = REPO_ROOT / "deploy/charts/creatorclip/values.prod.yaml"
STAGING_COMPOSE = REPO_ROOT / "docker-compose.staging.yml"
SOT_MD = REPO_ROOT / "docs/SOT.md"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"


def _read(path: Path) -> str:
    return path.read_text()


def _pgbouncer_image_lines(content: str) -> list[str]:
    """Return non-comment lines that contain a pgbouncer image reference."""
    result = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "pgbouncer" in stripped.lower() and (
            "image:" in stripped or "image =" in stripped
        ):
            result.append(line)
    return result


class TestPgBouncerImageUnified:
    """All manifest files must reference the same, digest-pinned edoburu image."""

    def _collect_digests(self) -> list[str]:
        digests = []
        for path in (VALUES_YAML, VALUES_PROD, STAGING_COMPOSE):
            content = _read(path)
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "edoburu/pgbouncer" in stripped and "@sha256:" in stripped:
                    # Extract the digest
                    at_idx = stripped.find("@sha256:")
                    digest = stripped[at_idx:].split('"')[0].split("'")[0].strip()
                    digests.append(digest)
        return digests

    def test_no_bitnami_pgbouncer_image_lines(self) -> None:
        for path in (VALUES_YAML, VALUES_PROD, STAGING_COMPOSE):
            for line in _read(path).splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "image:" in stripped and "bitnami/pgbouncer" in stripped:
                    raise AssertionError(
                        f"bitnami/pgbouncer is commercial-only; found in {path.name}: {line!r}"
                    )

    def test_all_pgbouncer_images_are_digest_pinned(self) -> None:
        for path in (VALUES_YAML, VALUES_PROD, STAGING_COMPOSE):
            content = _read(path)
            image_lines = _pgbouncer_image_lines(content)
            for line in image_lines:
                assert "@sha256:" in line, (
                    f"pgbouncer image in {path.name} must be digest-pinned (@sha256:): {line!r}"
                )

    def test_all_digest_references_match(self) -> None:
        """All digest references across the three manifests must be identical."""
        digests = self._collect_digests()
        assert len(digests) >= 2, (
            f"Expected at least 2 digest-pinned pgbouncer image refs, found {len(digests)}"
        )
        unique = set(digests)
        assert len(unique) == 1, (
            f"All pgbouncer image digests must match; found diverging refs: {unique}"
        )

    def test_staging_compose_uses_edoburu(self) -> None:
        content = _read(STAGING_COMPOSE)
        assert "edoburu/pgbouncer" in content, (
            "docker-compose.staging.yml pgbouncer service must use edoburu/pgbouncer (Issue 264)"
        )
        # Old floating tag should be gone
        assert "edoburu/pgbouncer:1.23.1-p3" not in content, (
            "Old floating tag edoburu/pgbouncer:1.23.1-p3 must be replaced with digest-pinned ref"
        )


class TestSOTTokenRotationContradictionFixed:
    """SOT.md must no longer say the TOKEN_ENCRYPTION_KEY runbook is 'not yet written'."""

    def test_sot_no_longer_says_runbook_not_written(self) -> None:
        content = _read(SOT_MD)
        # The old text was: 'TOKEN_ENCRYPTION_KEY rotation runbook not yet written'
        assert "rotation runbook not yet written" not in content, (
            "SOT.md must not say TOKEN_ENCRYPTION_KEY rotation runbook 'not yet written' "
            "— the runbook exists at docs/RUNBOOKS.md (Issue 264)"
        )

    def test_sot_references_runbooks_md(self) -> None:
        content = _read(SOT_MD)
        # After the fix, SOT should mention RUNBOOKS.md at least once in any TOKEN section.
        # (The file has multiple TOKEN_ENCRYPTION_KEY references; we check globally.)
        assert "RUNBOOKS.md" in content, (
            "SOT.md must reference docs/RUNBOOKS.md for TOKEN_ENCRYPTION_KEY rotation (Issue 264)"
        )


class TestClaudeMdPreLaunchTokenRotation:
    """CLAUDE.md Pre-Public-Launch section must mark token-rotation runbook as done."""

    def test_token_rotation_marked_done(self) -> None:
        content = _read(CLAUDE_MD)
        # Find the Pre-Public-Launch section
        section_idx = content.find("Pre-Public-Launch")
        assert section_idx != -1
        section = content[section_idx : section_idx + 1000]
        # Must not contain the bare "TOKEN_ENCRYPTION_KEY rotation runbook written"
        # (without a checkmark) as an outstanding item
        assert "- `TOKEN_ENCRYPTION_KEY` rotation runbook written" not in section, (
            "CLAUDE.md must mark token-rotation runbook as done (✅) — Issue 264"
        )
        # Must contain the completed (✅) version
        assert "✅" in section and "TOKEN_ENCRYPTION_KEY" in section, (
            "CLAUDE.md Pre-Public-Launch must mark token-rotation as ✅ complete"
        )
