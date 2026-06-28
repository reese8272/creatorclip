"""Doc-presence guards for the disaster-recovery batch (Issues 255–258).

These runbooks/decisions are the *only* deliverable for the operational parts of the
batch (escrow, restore drill, R2 lock) — if they silently disappear, the protection
they document is lost. Cheap presence assertions keep them from regressing.
"""

from pathlib import Path

_DOCS = Path(__file__).resolve().parent.parent / "docs"


def test_runbooks_has_disaster_recovery_section() -> None:
    text = (_DOCS / "RUNBOOKS.md").read_text()
    assert "## Disaster Recovery" in text
    # The four failure modes must each be documented.
    assert "Key loss" in text
    assert "Database loss" in text
    assert "Restore drill" in text
    # Re-escrow step folded into key rotation (Issue 255).
    assert "Re-escrow" in text or "re-escrow" in text


def test_secrets_documents_two_leg_escrow() -> None:
    text = (_DOCS / "SECRETS.md").read_text()
    assert "escrow" in text.lower()
    assert "GCP Secret Manager" in text
    assert "TOKEN_ENCRYPTION_KEY" in text


def test_decisions_records_dr_batch() -> None:
    text = (_DOCS / "DECISIONS.md").read_text()
    # Key load-bearing decisions must be on record.
    assert "Object Lock" in text and "Compliance" in text  # Issue 258
    assert "pg_dump" in text  # Issue 256
    assert "circular dependency" in text  # the don't-escrow-the-key-in-the-backup constraint


def test_compliance_lists_backup_bucket() -> None:
    text = (_DOCS / "COMPLIANCE.md").read_text()
    assert "creatorclip-backups" in text
