"""Doc-presence guards for the go/no-go launch scorecard (Issue 303).

docs/GO_LIVE.md is the canonical launch ledger — the single answer to "are we
ready to open to outside creators?". These keyword assertions (the
test_dr_docs.py pattern) keep the scorecard, its stage structure, the deploy-gate
issue references, the abort criterion, and the three source-list pointers from
silently regressing.
"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DOCS = _ROOT / "docs"


def test_go_live_exists_with_both_stage_headings() -> None:
    text = (_DOCS / "GO_LIVE.md").read_text()
    assert "## Stage A — ≤100-user private beta" in text
    assert "## Stage B — public launch (Issue #30)" in text


def test_go_live_references_every_deploy_gate_issue() -> None:
    """The L18 deploy-gate chain (#24→#25→#26→#28→#29→#30) must all appear —
    they are the ordered spine of the launch."""
    text = (_DOCS / "GO_LIVE.md").read_text()
    for issue_id in (24, 25, 26, 28, 29, 30):
        assert f"#{issue_id}" in text, f"deploy-gate issue #{issue_id} missing"


def test_go_live_has_abort_criterion_and_sign_off() -> None:
    text = (_DOCS / "GO_LIVE.md").read_text()
    assert "## Abort / rollback criterion" in text
    assert ":rollback" in text  # the #298/#271 image-rollback mechanism
    assert "INCIDENT_RESPONSE.md" in text  # run-time SEV1 abort path
    assert "## Sign-off" in text
    assert "Reese" in text
    # The dry-run AC deferral (approved) must be stated, not silently dropped.
    assert "deferred to the Issue-30 runway" in text


def test_source_gate_lists_point_at_go_live() -> None:
    """The three older gate lists must defer status to GO_LIVE.md instead of
    tri-plicating it (the disagreement that motivated Issue 303)."""
    for path in (
        _ROOT / "CLAUDE.md",
        _DOCS / "COMPLIANCE.md",
        _DOCS / "PROJECT_STATE.md",
    ):
        assert "docs/GO_LIVE.md" in path.read_text(), f"{path.name} lacks the pointer"
