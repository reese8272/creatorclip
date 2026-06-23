"""Tests for the observability layer (Issue 75f, 233, 237, 239): correlation ids,
structured logs, golden-signal metrics, redaction backstop, LLM token counters,
and Celery propagation. DB-free."""

import json
import logging
from pathlib import Path

import pytest
from celery import signals
from fastapi.testclient import TestClient

from observability import (
    _CELERY_HEADER,
    CELERY_TASKS_TOTAL,
    LLM_TOKENS_TOTAL,
    JsonLogFormatter,
    RequestIDLogFilter,
    _valid_request_id,
    configure_logging,
    install_celery_observability,
    record_llm_tokens,
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
    # Use /health (stable GET 200) — Issue 226 retired the legacy static pages so
    # GET / now returns 404, making /health the most reliable probe route.
    resp = client.get("/health")
    assert resp.status_code == 200
    rid = resp.headers.get("X-Request-ID")
    assert rid and len(rid) == 32


def test_middleware_respects_inbound_request_id(client: TestClient):
    # Use /health — legacy /static/login.html was retired by Issue 226.
    resp = client.get("/health", headers={"X-Request-ID": "upstream-trace-001"})
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


# ── Issue 233: JsonLogFormatter redaction backstop ───────────────────────────
_SENSITIVE_KEYS = [
    "email",
    "token",
    "secret",
    "password",
    "authorization",
    "cookie",
    "session",
    "jwt",
    "bearer",
    "api_key",
    "refresh",
    "credential",
]


@pytest.mark.parametrize("key", _SENSITIVE_KEYS)
def test_json_formatter_redacts_sensitive_key(key: str) -> None:
    """JsonLogFormatter must replace blocklisted key values with '[redacted]'."""
    record = logging.LogRecord("t", logging.INFO, "f", 1, "msg", (), None)
    record.request_id = "-"
    setattr(record, key, "super-secret-value")
    out = json.loads(JsonLogFormatter().format(record))
    assert out[key] == "[redacted]", f"key '{key}' must be redacted in formatter output"


def test_json_formatter_passes_benign_keys_through() -> None:
    """Safe keys (creator_id, task_id, bool flag) must not be redacted."""
    record = logging.LogRecord("t", logging.INFO, "f", 1, "msg", (), None)
    record.request_id = "-"
    record.creator_id = "uuid-abc"  # type: ignore[attr-defined]
    record.task_id = "tid-123"  # type: ignore[attr-defined]
    record.is_retry = True  # type: ignore[attr-defined]
    out = json.loads(JsonLogFormatter().format(record))
    assert out["creator_id"] == "uuid-abc"
    assert out["task_id"] == "tid-123"
    assert out["is_retry"] is True


# ── Issue 237: LLM token counters ────────────────────────────────────────────
def test_record_llm_tokens_increments_input_and_output() -> None:
    before_in = LLM_TOKENS_TOTAL.labels(
        provider="anthropic", model="test-model", kind="input"
    )._value.get()
    before_out = LLM_TOKENS_TOTAL.labels(
        provider="anthropic", model="test-model", kind="output"
    )._value.get()

    record_llm_tokens(
        provider="anthropic",
        model="test-model",
        input_tokens=100,
        output_tokens=50,
    )

    assert (
        LLM_TOKENS_TOTAL.labels(
            provider="anthropic", model="test-model", kind="input"
        )._value.get()
        == before_in + 100
    )
    assert (
        LLM_TOKENS_TOTAL.labels(
            provider="anthropic", model="test-model", kind="output"
        )._value.get()
        == before_out + 50
    )


def test_record_llm_tokens_increments_cache_kinds() -> None:
    before_read = LLM_TOKENS_TOTAL.labels(
        provider="anthropic", model="cache-model", kind="cache_read"
    )._value.get()
    before_create = LLM_TOKENS_TOTAL.labels(
        provider="anthropic", model="cache-model", kind="cache_creation"
    )._value.get()

    record_llm_tokens(
        provider="anthropic",
        model="cache-model",
        input_tokens=10,
        output_tokens=5,
        cache_read_tokens=200,
        cache_creation_tokens=300,
    )

    assert (
        LLM_TOKENS_TOTAL.labels(
            provider="anthropic", model="cache-model", kind="cache_read"
        )._value.get()
        == before_read + 200
    )
    assert (
        LLM_TOKENS_TOTAL.labels(
            provider="anthropic", model="cache-model", kind="cache_creation"
        )._value.get()
        == before_create + 300
    )


def test_record_llm_tokens_skips_zero_cache_kinds() -> None:
    """Zero cache values must not create label combinations (no cardinality pollution)."""
    before_read = LLM_TOKENS_TOTAL.labels(
        provider="anthropic", model="zero-cache-model", kind="cache_read"
    )._value.get()

    record_llm_tokens(
        provider="anthropic",
        model="zero-cache-model",
        input_tokens=1,
        output_tokens=1,
        cache_read_tokens=0,
        cache_creation_tokens=0,
    )

    # The label was already created by the _value.get() call above (prometheus-client
    # creates labels on first access); what matters is it did NOT increment.
    assert (
        LLM_TOKENS_TOTAL.labels(
            provider="anthropic", model="zero-cache-model", kind="cache_read"
        )._value.get()
        == before_read
    )


# ── Issue 239: worker durable log sink ───────────────────────────────────────
def test_configure_logging_writes_rotating_file(tmp_path: Path) -> None:
    """configure_logging with log_dir creates a rotating JSON file with request_id."""
    configure_logging(json_logs=True, log_dir=str(tmp_path))
    token = request_id_ctx.set("test-rid-239")
    try:
        logging.getLogger("test.file").info("hello from file sink")
    finally:
        request_id_ctx.reset(token)

    log_file = tmp_path / "app.log"
    assert log_file.exists(), "app.log must be created when log_dir is set"
    line = log_file.read_text(encoding="utf-8").strip().splitlines()[-1]
    data = json.loads(line)
    assert data["request_id"] == "test-rid-239"
    assert data["message"] == "hello from file sink"
    configure_logging(json_logs=False)  # restore for remaining tests


def test_configure_logging_respects_filename(tmp_path: Path) -> None:
    """filename='worker.log' must create worker.log, not app.log — proves no interleave."""
    configure_logging(json_logs=True, log_dir=str(tmp_path), filename="worker.log")
    logging.getLogger("test.worker").info("worker line")
    configure_logging(json_logs=False)

    assert (tmp_path / "worker.log").exists(), "worker.log must be created when filename is set"
    assert not (tmp_path / "app.log").exists(), "app.log must NOT be created when filename differs"
