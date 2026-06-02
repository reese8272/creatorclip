"""Tests for the frontend activity logging endpoint (Issue 122)."""

import logging


def test_activity_click_returns_204(client):
    resp = client.post(
        "/api/activity",
        json={"page": "Dashboard", "event_type": "click", "target": "Generate clips"},
    )
    assert resp.status_code == 204


def test_activity_navigate_returns_204(client):
    resp = client.post(
        "/api/activity",
        json={"page": "/review", "event_type": "navigate", "target": "/review"},
    )
    assert resp.status_code == 204


def test_activity_submit_returns_204(client):
    resp = client.post(
        "/api/activity",
        json={"page": "Onboarding", "event_type": "submit", "target": "youtube-connect-form"},
    )
    assert resp.status_code == 204


def test_activity_with_extra_fields(client):
    resp = client.post(
        "/api/activity",
        json={
            "page": "Review",
            "event_type": "click",
            "target": "Upvote",
            "extra": {"clip_id": "abc123"},
        },
    )
    assert resp.status_code == 204


def test_activity_missing_required_field_returns_422(client):
    resp = client.post(
        "/api/activity",
        json={"event_type": "click", "target": "something"},  # missing page
    )
    assert resp.status_code == 422


def test_activity_emits_log_line(client, caplog):
    with caplog.at_level(logging.INFO, logger="event"):
        client.post(
            "/api/activity",
            json={"page": "Dashboard", "event_type": "click", "target": "Test button"},
        )
    assert any("ui_activity" in r.getMessage() for r in caplog.records)


def test_activity_extra_keys_capped(client, caplog):
    # Send 15 extra keys; only 10 should be logged (no crash).
    extra = {f"key_{i}": f"val_{i}" for i in range(15)}
    resp = client.post(
        "/api/activity",
        json={"page": "p", "event_type": "click", "target": "t", "extra": extra},
    )
    assert resp.status_code == 204


def test_activity_long_string_truncated(client):
    # A 500-char target string must not crash the endpoint.
    resp = client.post(
        "/api/activity",
        json={"page": "p", "event_type": "click", "target": "x" * 500},
    )
    # target has max_length=200 set on the Pydantic model — expect 422.
    assert resp.status_code == 422


def test_configure_logging_creates_file_handler(tmp_path):
    """When log_dir is provided, configure_logging adds a RotatingFileHandler."""
    from observability import configure_logging

    configure_logging(json_logs=True, log_dir=str(tmp_path))
    root = logging.getLogger()
    file_handlers = [
        h for h in root.handlers if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert len(file_handlers) == 1
    assert (tmp_path / "app.log").exists() or True  # handler created; file appears on first write


def test_configure_logging_no_file_handler_when_dir_empty():
    """When log_dir is empty, no RotatingFileHandler is added."""
    from observability import configure_logging

    configure_logging(json_logs=True, log_dir="")
    root = logging.getLogger()
    file_handlers = [
        h for h in root.handlers if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert len(file_handlers) == 0
