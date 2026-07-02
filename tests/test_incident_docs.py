"""Doc-presence guards for the cost-observability batch (Issues 283/292).

The incident-response front door and the monthly cost-review procedure are
docs-only deliverables — cheap keyword assertions keep them from silently
regressing (same pattern as tests/test_dr_docs.py).
"""

import json
from pathlib import Path

_DOCS = Path(__file__).resolve().parent.parent / "docs"


def test_incident_response_has_severity_ladder() -> None:
    text = (_DOCS / "INCIDENT_RESPONSE.md").read_text()
    assert "SEV1" in text and "SEV2" in text and "SEV3" in text
    assert "Act now" in text
    assert "Same day" in text
    assert "Backlog" in text


def test_incident_response_solo_responder_and_index() -> None:
    text = (_DOCS / "INCIDENT_RESPONSE.md").read_text()
    # Solo-responder escalation: explicitly no rotation; future paging lever named.
    assert "no on-call rotation" in text
    assert "Grafana Cloud IRM" in text
    # Comms templates present.
    assert "Status-page post" in text
    assert "Affected-creator email" in text
    # The index must point at the real runbooks.
    assert "docs/RUNBOOKS.md" in text or "`docs/RUNBOOKS.md`" in text
    assert "Personal Data Breach Response" in text
    assert "Disaster Recovery" in text
    assert "docs/runbooks/194-youtube-publish.md" in text


def test_runbooks_has_monthly_cost_review() -> None:
    text = (_DOCS / "RUNBOOKS.md").read_text()
    assert "## Monthly Cost Review" in text
    # The two documented ledger queries.
    assert "SUM(cost_estimate)" in text
    assert "tokens_in + tokens_out" in text
    assert "GROUP BY period" in text
    assert "GROUP BY creator_id" in text  # top-5 isolation sanity check
    # The other two COGS lines.
    assert "DigitalOcean invoice" in text
    assert "R2" in text and "Metrics" in text


def test_llm_cost_panel_json_is_valid_and_targets_counter() -> None:
    panel = json.loads((_DOCS / "dashboards" / "llm-cost-panel.json").read_text())
    exprs = [t["expr"] for t in panel["targets"]]
    assert "sum by (provider) (increase(llm_cost_usd_total[1d]))" in exprs
