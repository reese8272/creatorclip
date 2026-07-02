"""Tests for the observability layer (Issue 75f, 233, 237, 238, 239, 281): correlation ids,
structured logs, golden-signal metrics, saturation gauges, redaction backstop, LLM token
counters, Sentry before_send scrub, and Celery propagation. DB-free."""

import json
import logging
from pathlib import Path

import pytest
from celery import signals
from fastapi.testclient import TestClient

from observability import (
    _CELERY_HEADER,
    CELERY_QUEUE_DEPTH,
    CELERY_TASKS_TOTAL,
    DB_POOL_CHECKED_OUT,
    LLM_TOKENS_TOTAL,
    REDIS_USED_MEMORY_BYTES,
    JsonLogFormatter,
    RequestIDLogFilter,
    _sentry_before_send,
    _valid_request_id,
    collect_saturation_gauges,
    configure_logging,
    install_celery_observability,
    record_llm_metric,
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
        LLM_TOKENS_TOTAL.labels(provider="anthropic", model="test-model", kind="input")._value.get()
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


# ── Issue 332: record_llm_metric dual-shape adapter ──────────────────────────
class _Usage:
    """Stand-in for an Anthropic Usage object (attribute access)."""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_record_llm_metric_from_usage_object() -> None:
    before = LLM_TOKENS_TOTAL.labels(
        provider="anthropic", model="obj-model", kind="input"
    )._value.get()
    usage = _Usage(
        input_tokens=70,
        output_tokens=20,
        cache_read_input_tokens=11,
        cache_creation_input_tokens=0,
    )
    record_llm_metric("obj-model", usage)
    assert (
        LLM_TOKENS_TOTAL.labels(provider="anthropic", model="obj-model", kind="input")._value.get()
        == before + 70
    )
    assert (
        LLM_TOKENS_TOTAL.labels(
            provider="anthropic", model="obj-model", kind="cache_read"
        )._value.get()
        == 11
    )


def test_record_llm_metric_from_stream_dict() -> None:
    before = LLM_TOKENS_TOTAL.labels(
        provider="anthropic", model="dict-model", kind="output"
    )._value.get()
    usage = {
        "input_tokens": 5,
        "output_tokens": 9,
        "cache_read": 3,
        "cache_creation": 4,
    }
    record_llm_metric("dict-model", usage)
    assert (
        LLM_TOKENS_TOTAL.labels(
            provider="anthropic", model="dict-model", kind="output"
        )._value.get()
        == before + 9
    )
    assert (
        LLM_TOKENS_TOTAL.labels(
            provider="anthropic", model="dict-model", kind="cache_creation"
        )._value.get()
        == 4
    )


def test_record_llm_metric_tolerates_missing_fields() -> None:
    # Older SDK / no-cache call: missing attrs coerce to 0, no crash.
    before = LLM_TOKENS_TOTAL.labels(
        provider="anthropic", model="sparse-model", kind="input"
    )._value.get()
    record_llm_metric("sparse-model", _Usage(input_tokens=2, output_tokens=1))
    assert (
        LLM_TOKENS_TOTAL.labels(
            provider="anthropic", model="sparse-model", kind="input"
        )._value.get()
        == before + 2
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


# ── Issue 238: saturation gauges ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_collect_saturation_gauges_sets_non_negative_values() -> None:
    """collect_saturation_gauges must update all three Gauges to non-negative values
    using only the injected engine/redis objects — no real connections opened.
    """
    from unittest.mock import AsyncMock, MagicMock

    # Fake engine: pool.checkedout() returns 2.
    fake_pool = MagicMock()
    fake_pool.checkedout.return_value = 2
    fake_engine = MagicMock()
    fake_engine.pool = fake_pool

    # Fake async Redis: llen=5, info returns used_memory=1024.
    fake_redis = AsyncMock()
    fake_redis.llen.return_value = 5
    fake_redis.info.return_value = {"used_memory": 1024}

    await collect_saturation_gauges(fake_engine, fake_redis)

    assert DB_POOL_CHECKED_OUT._value.get() >= 0
    assert CELERY_QUEUE_DEPTH.labels(queue="celery")._value.get() >= 0
    assert REDIS_USED_MEMORY_BYTES._value.get() >= 0

    # Exact values from the mock.
    assert DB_POOL_CHECKED_OUT._value.get() == 2
    assert CELERY_QUEUE_DEPTH.labels(queue="celery")._value.get() == 5
    assert REDIS_USED_MEMORY_BYTES._value.get() == 1024


@pytest.mark.asyncio
async def test_collect_saturation_gauges_tolerates_redis_failure() -> None:
    """If Redis raises, the gauge retains its last value and no exception propagates."""
    from unittest.mock import AsyncMock, MagicMock

    fake_pool = MagicMock()
    fake_pool.checkedout.return_value = 1
    fake_engine = MagicMock()
    fake_engine.pool = fake_pool

    fake_redis = AsyncMock()
    fake_redis.llen.side_effect = ConnectionError("Redis down")
    fake_redis.info.side_effect = ConnectionError("Redis down")

    # Must not raise.
    await collect_saturation_gauges(fake_engine, fake_redis)


# ── Issue 281: Sentry before_send scrub ──────────────────────────────────────


def test_sentry_before_send_scrubs_token_in_extra() -> None:
    """_sentry_before_send must scrub token fields from event['extra'] before
    the event leaves the process. This is the structural PII-safety backstop.
    """
    event: dict = {
        "extra": {"token": "super-secret-token-xyz", "creator_id": "abc-123"},
        "request": {"data": {}},
    }
    result = _sentry_before_send(event, {})
    assert result is not None
    assert result["extra"]["token"] == "[redacted]", "token must be redacted in extra"
    assert result["extra"]["creator_id"] == "abc-123", "safe key must pass through"


def test_sentry_before_send_scrubs_request_data() -> None:
    """_sentry_before_send must scrub request body data (e.g. password in a POST)."""
    event: dict = {
        "extra": {},
        "request": {"data": {"password": "hunter2", "username": "alice"}},
    }
    result = _sentry_before_send(event, {})
    assert result is not None
    assert result["request"]["data"]["password"] == "[redacted]"
    assert result["request"]["data"]["username"] == "alice"


def test_sentry_before_send_passes_event_with_no_sensitive_fields() -> None:
    """Events with no sensitive fields must be passed through unchanged."""
    event: dict = {
        "extra": {"creator_id": "uuid-abc", "task_id": "tid-123"},
        "request": {"data": {"action": "build_dna"}},
    }
    result = _sentry_before_send(event, {})
    assert result is not None
    assert result["extra"]["creator_id"] == "uuid-abc"
    assert result["request"]["data"]["action"] == "build_dna"


# ── Issue 326: init_otel no-op + idempotency ────────────────────────────────


def test_init_otel_is_noop_when_endpoint_empty() -> None:
    """init_otel must be a strict no-op when OTEL_EXPORTER_OTLP_ENDPOINT is unset.

    Specifically:
    - It must return without importing any opentelemetry.* module.
    - It must not set the _otel_initialized flag (so a later configured call
      still works).
    - It must raise nothing.

    This mirrors the init_sentry no-op contract.
    """
    import sys
    import unittest.mock as _mock

    import observability

    # Reset the idempotency flag to ensure a clean slate for this test.
    original_flag = observability._otel_initialized
    observability._otel_initialized = False
    try:
        # Record which OTel modules were in sys.modules before the call.
        otel_modules_before = {k for k in sys.modules if k.startswith("opentelemetry")}

        with _mock.patch("config.settings") as mock_settings:
            mock_settings.OTEL_EXPORTER_OTLP_ENDPOINT = ""
            observability.init_otel()

        # Must not have imported any new opentelemetry.* module.
        otel_modules_after = {k for k in sys.modules if k.startswith("opentelemetry")}
        new_otel_imports = otel_modules_after - otel_modules_before
        assert not new_otel_imports, (
            f"init_otel with empty endpoint must import no OTel modules; got: {new_otel_imports}"
        )

        # The idempotency flag must remain False so a later configured call works.
        assert observability._otel_initialized is False, (
            "_otel_initialized must stay False when endpoint is empty"
        )
    finally:
        observability._otel_initialized = original_flag


def test_init_otel_idempotent_when_called_twice() -> None:
    """Calling init_otel twice must not double-instrument.

    The _otel_initialized guard must cause the second call to return immediately
    without calling any instrumentor a second time.
    """
    import unittest.mock as _mock

    import observability

    original_flag = observability._otel_initialized
    # Pre-set the flag to simulate an already-initialised state.
    observability._otel_initialized = True
    try:
        with _mock.patch("config.settings") as mock_settings:
            mock_settings.OTEL_EXPORTER_OTLP_ENDPOINT = "https://otel.example.com/otlp"
            # If the guard is missing, this would try to import OTel packages and
            # fail (they are not installed in the unit-test environment).  With
            # the guard, it returns immediately with no imports.
            observability.init_otel()  # must not raise
    finally:
        observability._otel_initialized = original_flag


def test_instrument_fastapi_app_is_noop_when_otel_not_initialised() -> None:
    """instrument_fastapi_app must be a no-op when _otel_initialized is False."""
    import unittest.mock as _mock

    import observability

    original_flag = observability._otel_initialized
    observability._otel_initialized = False
    try:
        fake_app = _mock.MagicMock()
        # Must not raise and must not call FastAPIInstrumentor.
        observability.instrument_fastapi_app(fake_app)
        # FastAPIInstrumentor would have been imported and called if the guard failed.
        # The absence of an ImportError (OTel not installed) proves the guard held.
    finally:
        observability._otel_initialized = original_flag


def test_parse_otlp_headers_empty_string() -> None:
    """_parse_otlp_headers('') must return an empty dict."""
    from observability import _parse_otlp_headers

    assert _parse_otlp_headers("") == {}


def test_parse_otlp_headers_single_pair() -> None:
    """_parse_otlp_headers parses a single key=value pair."""
    from observability import _parse_otlp_headers

    result = _parse_otlp_headers("Authorization=Basic dXNlcjpwYXNz")
    assert result == {"Authorization": "Basic dXNlcjpwYXNz"}


def test_parse_otlp_headers_multiple_pairs() -> None:
    """_parse_otlp_headers parses comma-separated key=value pairs."""
    from observability import _parse_otlp_headers

    result = _parse_otlp_headers("Authorization=Basic abc,X-Scope-OrgID=1234")
    assert result == {"Authorization": "Basic abc", "X-Scope-OrgID": "1234"}


def test_parse_otlp_headers_ignores_malformed_pair() -> None:
    """_parse_otlp_headers skips pairs with no '=' sign rather than raising."""
    from observability import _parse_otlp_headers

    result = _parse_otlp_headers("Authorization=Bearer tok,BadPair")
    # BadPair has no '=' so it is skipped; Authorization is parsed.
    assert result == {"Authorization": "Bearer tok"}


# ── Issue 337: Bug 1 regression — _sentry_before_send non-dict extra ─────────


def test_sentry_before_send_non_dict_extra_list_no_crash() -> None:
    """_sentry_before_send must NOT crash when event['extra'] is a list (not a dict).

    The Sentry SDK populates extra from arbitrary exception context; it is not
    always a dict.  The isinstance guard ensures scrub_dict is only called on
    actual dict payloads.
    """
    event: dict = {
        "extra": ["context-a", "context-b"],  # list, not dict
        "request": {"data": {}},
    }
    result = _sentry_before_send(event, {})
    assert result is not None
    # The list must be left intact — we only scrub dicts.
    assert result["extra"] == ["context-a", "context-b"]


def test_sentry_before_send_non_dict_extra_string_no_crash() -> None:
    """_sentry_before_send must NOT crash when event['extra'] is a plain string."""
    event: dict = {"extra": "some-string-context"}
    result = _sentry_before_send(event, {})
    assert result is not None
    assert result["extra"] == "some-string-context"


# ── Issue 337: Bug 2 regression — JsonLogFormatter reserved output key clobber ──


def test_json_formatter_reserved_output_key_ts_not_clobbered() -> None:
    """An extra field named 'ts' must NOT clobber the formatter's structured 'ts' key.

    Before the fix, payload.update(scrub_dict(extra)) ran after the reserved
    payload keys were set, so an extra 'ts' overwrote the ISO timestamp.
    """
    record = logging.LogRecord("t", logging.INFO, "f", 1, "msg", (), None)
    record.request_id = "-"
    record.ts = "evil-timestamp"  # type: ignore[attr-defined]
    out = json.loads(JsonLogFormatter().format(record))
    # The real formatter timestamp must be present and not overwritten.
    assert out["ts"] != "evil-timestamp", "'ts' extra must not clobber the log envelope"
    # The ISO-format timestamp must be parseable.
    assert "T" in out["ts"]


def test_json_formatter_reserved_output_key_level_not_clobbered() -> None:
    """An extra field named 'level' must NOT clobber the formatter's level key."""
    record = logging.LogRecord("t", logging.WARNING, "f", 1, "msg", (), None)
    record.request_id = "-"
    record.level = "FAKE"  # type: ignore[attr-defined]
    out = json.loads(JsonLogFormatter().format(record))
    assert out["level"] == "WARNING", "'level' extra must not clobber the log envelope"


def test_json_formatter_reserved_output_key_logger_not_clobbered() -> None:
    """An extra field named 'logger' must NOT clobber the formatter's logger key."""
    record = logging.LogRecord("real-logger", logging.INFO, "f", 1, "msg", (), None)
    record.request_id = "-"
    record.logger = "fake-logger"  # type: ignore[attr-defined]
    out = json.loads(JsonLogFormatter().format(record))
    assert out["logger"] == "real-logger", "'logger' extra must not clobber the log envelope"


# ── Issue 337: parametrized full-blocklist suite across all three sinks ───────

_FULL_BLOCKLIST_KEYS = [
    "token",
    "email",
    "secret",
    "password",
    "authorization",
    "cookie",
    "session",
    "jwt",
    "bearer",
    "api_key",
    "raw_key",
    "refresh",
    "access_key",
    "credential",
]


@pytest.mark.parametrize("key", _FULL_BLOCKLIST_KEYS)
def test_all_sinks_redact_blocklist_key(key: str) -> None:
    """Each blocklist key must be scrubbed in JsonLogFormatter, event_log._redact,
    and _sentry_before_send — asserting all three sinks in one parametrized suite
    prevents the blocklist from diverging silently."""
    from event_log import _redact

    # ── Sink 1: JsonLogFormatter ───────────────────────────────────────────────
    record = logging.LogRecord("t", logging.INFO, "f", 1, "msg", (), None)
    record.request_id = "-"
    setattr(record, key, "sensitive-value")
    fmt_out = json.loads(JsonLogFormatter().format(record))
    assert fmt_out[key] == "[redacted]", f"JsonLogFormatter must redact key '{key}'"

    # ── Sink 2: event_log._redact ──────────────────────────────────────────────
    el_out = _redact({key: "sensitive-value", "creator_id": "uuid-safe"})
    assert el_out is not None
    assert el_out[key] == "[redacted]", f"event_log._redact must redact key '{key}'"
    assert el_out["creator_id"] == "uuid-safe"

    # ── Sink 3: _sentry_before_send ───────────────────────────────────────────
    event: dict = {"extra": {key: "sensitive-value", "creator_id": "uuid-safe"}}
    sentry_out = _sentry_before_send(event, {})
    assert sentry_out is not None
    assert sentry_out["extra"][key] == "[redacted]", (
        f"_sentry_before_send must redact key '{key}' in extra"
    )
    assert sentry_out["extra"]["creator_id"] == "uuid-safe"


def test_all_sinks_preserve_non_sensitive_key() -> None:
    """A safe key (creator_id) must pass through all three sinks unchanged."""
    from event_log import _redact

    record = logging.LogRecord("t", logging.INFO, "f", 1, "msg", (), None)
    record.request_id = "-"
    record.creator_id = "uuid-abc"  # type: ignore[attr-defined]
    fmt_out = json.loads(JsonLogFormatter().format(record))
    assert fmt_out["creator_id"] == "uuid-abc"

    el_out = _redact({"creator_id": "uuid-abc"})
    assert el_out is not None
    assert el_out["creator_id"] == "uuid-abc"

    event: dict = {"extra": {"creator_id": "uuid-abc"}}
    sentry_out = _sentry_before_send(event, {})
    assert sentry_out is not None
    assert sentry_out["extra"]["creator_id"] == "uuid-abc"


# ── Issue 337: record_llm_tokens None / non-int cache coercion ────────────────


def test_record_llm_tokens_none_cache_coerced_to_zero() -> None:
    """None cache fields must coerce to 0 and not increment the counter."""
    before_read = LLM_TOKENS_TOTAL.labels(
        provider="anthropic", model="none-cache-model", kind="cache_read"
    )._value.get()
    before_create = LLM_TOKENS_TOTAL.labels(
        provider="anthropic", model="none-cache-model", kind="cache_creation"
    )._value.get()

    record_llm_tokens(
        provider="anthropic",
        model="none-cache-model",
        input_tokens=5,
        output_tokens=3,
        cache_read_tokens=None,  # type: ignore[arg-type]
        cache_creation_tokens=None,  # type: ignore[arg-type]
    )

    assert (
        LLM_TOKENS_TOTAL.labels(
            provider="anthropic", model="none-cache-model", kind="cache_read"
        )._value.get()
        == before_read
    ), "None cache_read_tokens must not increment"
    assert (
        LLM_TOKENS_TOTAL.labels(
            provider="anthropic", model="none-cache-model", kind="cache_creation"
        )._value.get()
        == before_create
    ), "None cache_creation_tokens must not increment"


def test_record_llm_tokens_non_int_str_cache_coerced_to_zero() -> None:
    """Non-numeric string cache fields must coerce to 0 without raising."""
    before_read = LLM_TOKENS_TOTAL.labels(
        provider="anthropic", model="str-cache-model", kind="cache_read"
    )._value.get()

    record_llm_tokens(
        provider="anthropic",
        model="str-cache-model",
        input_tokens=1,
        output_tokens=1,
        cache_read_tokens="x",  # type: ignore[arg-type]
        cache_creation_tokens="y",  # type: ignore[arg-type]
    )

    assert (
        LLM_TOKENS_TOTAL.labels(
            provider="anthropic", model="str-cache-model", kind="cache_read"
        )._value.get()
        == before_read
    ), "Non-numeric string cache_read_tokens must coerce to 0"


# ── Issue 337: /metrics 200 when collect_saturation_gauges raises ─────────────


def test_metrics_returns_200_when_collect_saturation_raises(
    client: TestClient, monkeypatch
) -> None:
    """The /metrics route must return 200 even when collect_saturation_gauges raises.

    collect_saturation_gauges is internally defensive, but the route wraps the
    outer call too so any unexpected error never 500s the Prometheus scrape.
    """
    import main as main_module

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated gauge failure")

    monkeypatch.setattr(main_module, "collect_saturation_gauges", _boom)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "http_request_duration_seconds" in resp.text


def test_sentry_init_is_noop_when_dsn_empty() -> None:
    """init_sentry must be a no-op when SENTRY_DSN is empty — no SDK initialization."""
    import sys
    import types
    import unittest.mock as _mock

    from observability import init_sentry

    # When dsn is empty, init_sentry must return immediately without touching sentry_sdk.
    # We verify this by patching the lazy import inside init_sentry using sys.modules so
    # it works even when sentry-sdk is not installed in the current environment.
    fake_sdk = types.ModuleType("sentry_sdk")
    fake_sdk.init = _mock.MagicMock()
    # Patch all sub-integration modules sentry_sdk imports.
    with _mock.patch.dict(sys.modules, {"sentry_sdk": fake_sdk}):
        init_sentry(dsn="", environment="test", release="abc123")
    fake_sdk.init.assert_not_called()


# ── Issue 352: recursive scrub_dict + bounded metric-label cardinality ─────────


def test_scrub_dict_recursive_nested_payload() -> None:
    """scrub_dict must redact sensitive keys inside nested dicts and lists, keep
    benign nested values, and leave flat-dict behavior unchanged."""
    from redact import _MAX_SCRUB_DEPTH, scrub_dict

    out = scrub_dict(
        {
            "token": "top-secret",
            "user": {"email": "a@b.com", "id": "u1", "auth": {"jwt": "xyz"}},
            "events": [{"session": "s1", "kind": "click"}],
            "plain": 7,
        }
    )
    assert out["token"] == "[redacted]"
    assert out["user"]["email"] == "[redacted]"
    assert out["user"]["id"] == "u1"
    assert out["user"]["auth"]["jwt"] == "[redacted]"
    assert out["events"][0]["session"] == "[redacted]"
    assert out["events"][0]["kind"] == "click"
    assert out["plain"] == 7

    # Depth bound: a payload nested past the cap is conservatively redacted wholesale.
    deep: dict = {"leaf": "v"}
    for _ in range(_MAX_SCRUB_DEPTH + 2):
        deep = {"nested": deep}
    node = scrub_dict(deep)
    for _ in range(_MAX_SCRUB_DEPTH):
        node = node["nested"]
        if node == "[redacted]":
            break
    assert node == "[redacted]", "over-deep subtree must collapse to [redacted]"


def test_middleware_unmatched_path_uses_bounded_metric_label(client: TestClient) -> None:
    """404s (no matched route) must be labelled with the constant '__unmatched__',
    never the raw path — a scanner minting /<uuid> paths must not explode the
    Prometheus series (Issue 352)."""
    raw = "/no-such-route/123e4567-e89b-12d3-a456-426614174000"
    resp = client.get(raw)
    assert resp.status_code == 404
    scrape = client.get("/metrics").text
    assert raw not in scrape, "raw unmatched path must never appear as a metric label"
    assert "__unmatched__" in scrape
