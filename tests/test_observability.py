"""Tests for the observability layer (Issue 75f): correlation ids, structured
logs, golden-signal metrics, and Celery propagation. DB-free."""

import json
import logging

from celery import signals
from fastapi.testclient import TestClient

from observability import (
    _CELERY_HEADER,
    CELERY_TASKS_TOTAL,
    JsonLogFormatter,
    RequestIDLogFilter,
    _valid_request_id,
    configure_logging,
    install_celery_observability,
    request_id_ctx,
)


# ── request-id validation ────────────────────────────────────────────────────
def test_valid_request_id_accepts_sane_inbound():
    assert _valid_request_id("trace-abc-123") == "trace-abc-123"


def test_valid_request_id_mints_on_missing_or_bad():
    assert len(_valid_request_id(None)) == 32  # uuid4 hex
    assert len(_valid_request_id("")) == 32
    assert len(_valid_request_id("x" * 500)) == 32  # over the length bound
    assert len(_valid_request_id("bad\ninjected")) == 32  # non-printable → minted


# ── structured logging ───────────────────────────────────────────────────────
def test_log_filter_injects_current_request_id():
    token = request_id_ctx.set("rid-42")
    try:
        record = logging.LogRecord("t", logging.INFO, "f", 1, "msg", (), None)
        assert RequestIDLogFilter().filter(record) is True
        assert record.request_id == "rid-42"
    finally:
        request_id_ctx.reset(token)


def test_json_formatter_emits_request_id_and_extras():
    record = logging.LogRecord("mylogger", logging.WARNING, "f", 1, "hello %s", ("world",), None)
    record.request_id = "rid-7"
    record.creator_id = "creator-9"  # an `extra` field
    out = json.loads(JsonLogFormatter().format(record))
    assert out["level"] == "WARNING"
    assert out["logger"] == "mylogger"
    assert out["request_id"] == "rid-7"
    assert out["message"] == "hello world"
    assert out["creator_id"] == "creator-9"


def test_configure_logging_is_idempotent():
    configure_logging(json_logs=True)
    configure_logging(json_logs=True)
    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JsonLogFormatter)
    # restore text mode so other tests' captured logs stay readable
    configure_logging(json_logs=False)


# ── HTTP middleware (against the real app) ───────────────────────────────────
def test_middleware_mints_and_echoes_request_id(client: TestClient):
    resp = client.get("/")
    assert resp.status_code == 200
    rid = resp.headers.get("X-Request-ID")
    assert rid and len(rid) == 32


def test_middleware_respects_inbound_request_id(client: TestClient):
    # Target a non-redirecting route: `/` 302s to the SPA once built (Issue 85g),
    # and following it would drop the inbound header on the second hop.
    resp = client.get("/static/login.html", headers={"X-Request-ID": "upstream-trace-001"})
    assert resp.headers.get("X-Request-ID") == "upstream-trace-001"


def test_metrics_endpoint_exposes_golden_signals(client: TestClient):
    client.get("/")  # generate at least one observation
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    assert "http_request_duration_seconds" in resp.text


# ── Celery propagation ───────────────────────────────────────────────────────
def test_celery_signals_propagate_request_id():
    install_celery_observability()

    # publish: the current request id is stamped onto the outgoing task headers
    token = request_id_ctx.set("rid-publish")
    try:
        headers: dict = {}
        signals.before_task_publish.send(sender="t", headers=headers)
        assert headers[_CELERY_HEADER] == "rid-publish"
    finally:
        request_id_ctx.reset(token)

    # prerun: the worker binds the id carried on the task request
    class _Req:
        pass

    req = _Req()
    setattr(req, _CELERY_HEADER, "rid-publish")

    class _Task:
        name = "worker.tasks.demo"
        request = req

    request_id_ctx.set("-")
    signals.task_prerun.send(sender="t", task=_Task())
    assert request_id_ctx.get() == "rid-publish"

    # postrun: golden signals recorded, then the id cleared so it can't leak
    before = CELERY_TASKS_TOTAL.labels(task="worker.tasks.demo", state="SUCCESS")._value.get()
    signals.task_postrun.send(sender="t", task=_Task(), state="SUCCESS")
    after = CELERY_TASKS_TOTAL.labels(task="worker.tasks.demo", state="SUCCESS")._value.get()
    assert after == before + 1
    assert request_id_ctx.get() == "-"


def test_metrics_open_when_no_token(client: TestClient):
    # Default test config leaves METRICS_TOKEN unset → scrape allowed (dev/internal).
    assert client.get("/metrics").status_code == 200


def test_metrics_requires_bearer_token_when_set(client: TestClient, monkeypatch):
    monkeypatch.setattr("config.settings.METRICS_TOKEN", "scrape-secret")
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.get("/metrics", headers={"Authorization": "Bearer scrape-secret"})
    assert ok.status_code == 200
