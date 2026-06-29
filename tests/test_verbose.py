"""Tests for the full-content verbose logging sink (docs/DECISIONS.md 2026-06-29).

Covers the production hard-gate, the no-op-when-disabled contract, the
non-scrubbing formatter, the LLM request/response helpers, and the
configure_logging wiring.
"""

from __future__ import annotations

import json
import logging

import pytest

import observability
import verbose
from config import settings
from observability import JsonLogFormatter, configure_logging


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def capture_verbose():
    """Attach a capturing handler to the `verbose` logger and yield it."""
    vlogger = logging.getLogger(verbose.VERBOSE_LOGGER_NAME)
    prior_level = vlogger.level
    prior_handlers = list(vlogger.handlers)
    prior_propagate = vlogger.propagate
    for h in prior_handlers:
        vlogger.removeHandler(h)
    cap = _Capture()
    vlogger.addHandler(cap)
    vlogger.setLevel(logging.INFO)
    vlogger.propagate = False
    try:
        yield cap
    finally:
        vlogger.removeHandler(cap)
        for h in prior_handlers:
            vlogger.addHandler(h)
        vlogger.setLevel(prior_level)
        vlogger.propagate = prior_propagate


# ── Production hard-gate ──────────────────────────────────────────────────────
def test_enabled_when_flag_on_and_not_production(monkeypatch):
    monkeypatch.setattr(settings, "VERBOSE_LOGGING", True)
    monkeypatch.setattr(settings, "ENV", "development")
    assert settings.verbose_logging_enabled is True
    assert verbose.verbose_enabled() is True


def test_hard_gated_off_in_production_even_when_flag_on(monkeypatch):
    monkeypatch.setattr(settings, "VERBOSE_LOGGING", True)
    monkeypatch.setattr(settings, "ENV", "production")
    assert settings.verbose_logging_enabled is False


def test_disabled_when_flag_off(monkeypatch):
    monkeypatch.setattr(settings, "VERBOSE_LOGGING", False)
    monkeypatch.setattr(settings, "ENV", "development")
    assert settings.verbose_logging_enabled is False


# ── No-op when disabled ───────────────────────────────────────────────────────
def test_vlog_is_noop_when_disabled(monkeypatch, capture_verbose):
    monkeypatch.setattr(settings, "VERBOSE_LOGGING", False)
    monkeypatch.setattr(settings, "ENV", "development")
    verbose.vlog("anything", secret_prompt="should not appear")
    assert capture_verbose.records == []


def test_vlog_emits_full_content_when_enabled(monkeypatch, capture_verbose):
    monkeypatch.setattr(settings, "VERBOSE_LOGGING", True)
    monkeypatch.setattr(settings, "ENV", "staging")
    verbose.vlog("render_event", cmd=["ffmpeg", "-i", "x.mp4"], stderr="boom")
    assert len(capture_verbose.records) == 1
    rec = capture_verbose.records[0]
    assert rec.event == "render_event"
    assert rec.cmd == ["ffmpeg", "-i", "x.mp4"]
    assert rec.stderr == "boom"


# ── LLM helpers carry raw content ─────────────────────────────────────────────
def test_vlog_llm_request_and_response_capture_content(monkeypatch, capture_verbose):
    monkeypatch.setattr(settings, "VERBOSE_LOGGING", True)
    monkeypatch.setattr(settings, "ENV", "development")

    verbose.vlog_llm_request(
        "dna_brief",
        model="claude-x",
        max_tokens=100,
        system="you are a helper",
        messages=[{"role": "user", "content": "raw prompt text"}],
    )

    class _Block:
        type = "text"
        text = "raw model answer"

    class _Usage:
        input_tokens = 5
        output_tokens = 7
        cache_read_input_tokens = 1
        cache_creation_input_tokens = 0

    class _Msg:
        model = "claude-x"
        stop_reason = "end_turn"
        content = [_Block()]
        usage = _Usage()

    verbose.vlog_llm_response("dna_brief", response=_Msg())

    assert len(capture_verbose.records) == 2
    req, resp = capture_verbose.records
    assert req.event == "llm_request"
    assert req.system == "you are a helper"
    assert req.messages[0]["content"] == "raw prompt text"
    assert resp.event == "llm_response"
    assert resp.text == "raw model answer"
    assert resp.usage["input_tokens"] == 5
    assert resp.stop_reason == "end_turn"


# ── Non-scrubbing formatter ───────────────────────────────────────────────────
def _format(record_extra: dict, *, scrub: bool) -> dict:
    fmt = JsonLogFormatter(scrub=scrub)
    rec = logging.LogRecord("verbose", logging.INFO, __file__, 0, "msg", (), None)
    for k, v in record_extra.items():
        setattr(rec, k, v)
    return json.loads(fmt.format(rec))


def test_raw_formatter_keeps_sensitive_keys():
    out = _format({"token": "sk-secret", "messages": "raw prompt"}, scrub=False)
    assert out["token"] == "sk-secret"
    assert out["messages"] == "raw prompt"


def test_default_formatter_still_scrubs():
    out = _format({"token": "sk-secret"}, scrub=True)
    assert out["token"] != "sk-secret"


# ── configure_logging wiring ──────────────────────────────────────────────────
def test_configure_logging_wires_verbose_sink_non_propagating():
    vlogger = logging.getLogger(verbose.VERBOSE_LOGGER_NAME)
    try:
        configure_logging(json_logs=True, log_dir="", verbose=True)
        assert vlogger.handlers, "verbose logger should have a handler when verbose=True"
        assert vlogger.propagate is False
        # The wired handler must use the NON-scrubbing formatter.
        fmts = [h.formatter for h in vlogger.handlers if isinstance(h.formatter, JsonLogFormatter)]
        assert fmts and all(f._scrub is False for f in fmts)
    finally:
        for h in list(vlogger.handlers):
            vlogger.removeHandler(h)


def test_configure_verbose_logger_is_idempotent():
    """Re-running does not stack duplicate handlers (mirrors configure_logging)."""
    vlogger = logging.getLogger(verbose.VERBOSE_LOGGER_NAME)
    try:
        observability._configure_verbose_logger("", "app.log")
        first = len(vlogger.handlers)
        observability._configure_verbose_logger("", "app.log")
        assert len(vlogger.handlers) == first
    finally:
        for h in list(vlogger.handlers):
            vlogger.removeHandler(h)
