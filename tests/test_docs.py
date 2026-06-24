"""
Tests for documentation completeness — load-bearing compliance documents.

These tests pin that required runbook sections and compliance clauses exist in
documentation files so they cannot silently regress between edits.
"""

import pathlib

_REPO_ROOT = pathlib.Path(__file__).parent.parent


# ── Issue 253: GDPR Art. 33/34 breach-notification runbook ───────────────────


def test_runbooks_has_personal_data_breach_section():
    """Issue 253: docs/RUNBOOKS.md must contain a 'Personal Data Breach Response'
    section covering the Art. 33 72-hour supervisory-authority notification window."""
    src = (_REPO_ROOT / "docs" / "RUNBOOKS.md").read_text()
    assert "Personal Data Breach" in src, (
        "docs/RUNBOOKS.md must contain a 'Personal Data Breach' section "
        "(Issue 253 — GDPR Art. 33/34 runbook requirement)."
    )


def test_runbooks_breach_section_references_72_hours():
    """Issue 253: the breach runbook must cite the 72-hour notification clock
    (GDPR Art. 33(1)) so on-call engineers know the deadline."""
    src = (_REPO_ROOT / "docs" / "RUNBOOKS.md").read_text()
    assert "72" in src, (
        "docs/RUNBOOKS.md must reference the 72-hour GDPR Art. 33 notification "
        "deadline (Issue 253)."
    )


def test_runbooks_breach_section_references_art_33():
    """Issue 253: the breach runbook must explicitly reference GDPR Art. 33 so
    engineers understand the legal basis for the 72-hour notification."""
    src = (_REPO_ROOT / "docs" / "RUNBOOKS.md").read_text()
    assert "Art. 33" in src or "Article 33" in src, (
        "docs/RUNBOOKS.md must reference GDPR Art. 33 in the breach-response section (Issue 253)."
    )


def test_runbooks_breach_section_has_owner_escalation_marker():
    """Issue 253: the breach runbook must include a named-owner / escalation
    contact marker. The human must fill this in before production."""
    src = (_REPO_ROOT / "docs" / "RUNBOOKS.md").read_text()
    # Accepted forms: DPO, Data Protection Officer, or a filled-in contact
    assert "DPO" in src or "Data Protection Officer" in src or "reesepludwick@gmail.com" in src, (
        "docs/RUNBOOKS.md breach section must include a named owner or DPO contact "
        "placeholder (Issue 253 — must be a real person before production)."
    )
