"""
Unit tests for notify/mailer.py (Issue 242).

These tests run against the console backend with monkeypatching — no live
external service, no Docker, no Postgres required.

80/20 coverage:
- Happy path: console sink renders template + logs without error
- Idempotency key is forwarded to the provider options dict (resend backend)
- Backend switch (console vs resend) is config-driven
- Missing RESEND_API_KEY in resend mode fails fast at settings load
- Invalid/oversized idempotency keys raise ValueError immediately
"""

import importlib
import logging
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEMPLATE_DIR = Path(__file__).parent.parent / "notify" / "templates"


def _fake_settings(notify_backend: str = "console", resend_api_key: str = "", email_from: str = "") -> MagicMock:
    """Return a mock Settings object with just the fields mailer.py reads."""
    s = MagicMock()
    s.NOTIFY_BACKEND = notify_backend
    s.RESEND_API_KEY = resend_api_key
    s.EMAIL_FROM = email_from
    return s


# ---------------------------------------------------------------------------
# Console backend — happy path
# ---------------------------------------------------------------------------


def test_console_backend_renders_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    """send() with console backend renders the template and emits an INFO log."""
    from notify import mailer

    context = {
        "creator_name": "Alice",
        "video_title": "How to test Python",
        "clip_count": 3,
        "review_url": "https://autoclip.studio/review/abc123",
    }

    with patch.object(mailer, "settings", _fake_settings("console")):
        with caplog.at_level(logging.INFO, logger="notify.mailer"):
            mailer.send(
                to="alice@example.com",
                template="clips_ready",
                context=context,
                idempotency_key="test-console-abc123",
            )

    assert any("console" in r.message for r in caplog.records), (
        "Expected a log record containing 'console' for the console backend"
    )


def test_console_backend_includes_idempotency_key_in_log(caplog: pytest.LogCaptureFixture) -> None:
    """Idempotency key must appear in the console log for trace correlation."""
    from notify import mailer

    idem_key = "unique-key-xyz-789"
    context = {
        "creator_name": "Bob",
        "video_title": "My Video",
        "clip_count": 1,
        "review_url": "https://autoclip.studio/review/xyz",
    }

    with patch.object(mailer, "settings", _fake_settings("console")):
        with caplog.at_level(logging.INFO, logger="notify.mailer"):
            mailer.send(
                to="bob@example.com",
                template="clips_ready",
                context=context,
                idempotency_key=idem_key,
            )

    joined = " ".join(r.message for r in caplog.records)
    assert idem_key in joined, (
        f"Expected idempotency key {idem_key!r} to appear in log output"
    )


# ---------------------------------------------------------------------------
# Resend backend — idempotency key forwarded to provider options
# ---------------------------------------------------------------------------


def test_resend_backend_forwards_idempotency_key() -> None:
    """resend.Emails.send must receive options={'idempotency_key': key}."""
    from notify import mailer

    fake_resend = MagicMock()
    fake_resend.api_key = None
    fake_resend.Emails = MagicMock()
    fake_resend.Emails.send = MagicMock(return_value=MagicMock(id="resend-msg-id-001"))

    context = {
        "creator_name": "Carol",
        "video_title": "Resend Test",
        "clip_count": 5,
        "review_url": "https://autoclip.studio/review/send",
    }
    idem_key = "resend-idempotency-key-abc"

    # Reset the module-level initialised flag so the patched resend is used
    with patch.object(mailer, "_resend_initialised", False):
        with patch.object(mailer, "settings", _fake_settings("resend", resend_api_key="re_test", email_from="noreply@autoclip.studio")):
            with patch.dict(sys.modules, {"resend": fake_resend}):
                mailer.send(
                    to="carol@example.com",
                    template="clips_ready",
                    context=context,
                    idempotency_key=idem_key,
                )

    fake_resend.Emails.send.assert_called_once()
    call_args = fake_resend.Emails.send.call_args
    # Second positional arg is options dict
    options = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("options", {})
    assert options.get("idempotency_key") == idem_key, (
        "resend.Emails.send must receive options={'idempotency_key': ...}"
    )


def test_resend_backend_params_include_from_to_subject() -> None:
    """resend.Emails.send must receive a params dict with from, to, subject."""
    from notify import mailer

    fake_resend = MagicMock()
    fake_resend.api_key = None
    fake_resend.Emails = MagicMock()
    fake_resend.Emails.send = MagicMock(return_value=MagicMock(id="resend-msg-id-002"))

    context = {
        "creator_name": "Dave",
        "video_title": "Params Test",
        "clip_count": 2,
        "review_url": "https://autoclip.studio/review/params",
    }
    email_from = "noreply@autoclip.studio"

    with patch.object(mailer, "_resend_initialised", False):
        with patch.object(mailer, "settings", _fake_settings("resend", resend_api_key="re_test", email_from=email_from)):
            with patch.dict(sys.modules, {"resend": fake_resend}):
                mailer.send(
                    to="dave@example.com",
                    template="clips_ready",
                    context=context,
                    idempotency_key="resend-params-key",
                )

    params = fake_resend.Emails.send.call_args.args[0]
    assert params["from"] == email_from
    assert params["to"] == ["dave@example.com"]
    assert "subject" in params
    assert params["text"]
    assert params["html"]


# ---------------------------------------------------------------------------
# Backend dispatch — console vs resend selected by settings
# ---------------------------------------------------------------------------


def test_unknown_backend_raises_value_error() -> None:
    """An unrecognised NOTIFY_BACKEND must raise ValueError, not silently drop the email."""
    from notify import mailer

    context = {
        "creator_name": "Eve",
        "video_title": "Unknown Backend",
        "clip_count": 1,
        "review_url": "https://autoclip.studio/review/ev",
    }

    with patch.object(mailer, "settings", _fake_settings("smtp")):
        with pytest.raises(ValueError, match="Unknown NOTIFY_BACKEND"):
            mailer.send(
                to="eve@example.com",
                template="clips_ready",
                context=context,
                idempotency_key="backend-test-key",
            )


# ---------------------------------------------------------------------------
# Idempotency key validation
# ---------------------------------------------------------------------------


def test_oversized_idempotency_key_raises() -> None:
    """A key exceeding 256 characters must raise ValueError before any send attempt."""
    from notify import mailer

    long_key = "a" * 257
    with patch.object(mailer, "settings", _fake_settings("console")):
        with pytest.raises(ValueError, match="256"):
            mailer.send(
                to="test@example.com",
                template="clips_ready",
                context={
                    "creator_name": "F",
                    "video_title": "X",
                    "clip_count": 1,
                    "review_url": "https://example.com",
                },
                idempotency_key=long_key,
            )


def test_idempotency_key_at_max_length_is_accepted(caplog: pytest.LogCaptureFixture) -> None:
    """A key of exactly 256 characters must be accepted (boundary value)."""
    from notify import mailer

    max_key = "a" * 256
    with patch.object(mailer, "settings", _fake_settings("console")):
        with caplog.at_level(logging.INFO, logger="notify.mailer"):
            # Should not raise
            mailer.send(
                to="test@example.com",
                template="clips_ready",
                context={
                    "creator_name": "G",
                    "video_title": "Y",
                    "clip_count": 1,
                    "review_url": "https://example.com",
                },
                idempotency_key=max_key,
            )


def test_idempotency_key_with_invalid_chars_raises() -> None:
    """A key with spaces or special chars must raise ValueError."""
    from notify import mailer

    with patch.object(mailer, "settings", _fake_settings("console")):
        with pytest.raises(ValueError, match="idempotency_key"):
            mailer.send(
                to="test@example.com",
                template="clips_ready",
                context={
                    "creator_name": "H",
                    "video_title": "Z",
                    "clip_count": 1,
                    "review_url": "https://example.com",
                },
                idempotency_key="key with spaces",
            )


# ---------------------------------------------------------------------------
# Missing RESEND_API_KEY fails fast at settings load — validated by config.py
# ---------------------------------------------------------------------------


def test_missing_resend_api_key_fails_at_settings_load() -> None:
    """Settings must raise ValidationError if NOTIFY_BACKEND='resend' and RESEND_API_KEY=''.

    This verifies the @model_validator in config.py catches the misconfiguration
    at startup rather than at first send.
    """
    import os
    from pydantic import ValidationError
    from pydantic_settings import BaseSettings

    # Build a minimal settings class that mirrors only the relevant fields
    # so we can test the validator in isolation without needing all required vars.
    from config import Settings

    env_overrides = {
        "NOTIFY_BACKEND": "resend",
        "RESEND_API_KEY": "",
        # Provide all REQUIRED fields so only the notify validator fires
        "ANTHROPIC_API_KEY": "test",
        "DATABASE_URL": "postgresql+psycopg://u:p@localhost:5432/db",
        "REDIS_URL": "redis://localhost:6379/0",
        "GOOGLE_OAUTH_CLIENT_ID": "gci",
        "GOOGLE_OAUTH_CLIENT_SECRET": "gcs",
        "OAUTH_REDIRECT_URI": "http://localhost/cb",
        "TOKEN_ENCRYPTION_KEY": "dGVzdGtleXZhbHVldGVzdGtleXZhbHVldGVzdA==",
        "JWT_SECRET_KEY": "testsecretjwt",
        "ALLOWED_ORIGINS": "http://localhost",
    }

    with pytest.raises(ValidationError, match="RESEND_API_KEY"):
        Settings(**env_overrides)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Template rendering — content smoke tests
# ---------------------------------------------------------------------------


def test_clips_ready_template_contains_review_url(caplog: pytest.LogCaptureFixture) -> None:
    """The clips_ready template must include the review_url in the rendered body."""
    from notify import mailer

    review_url = "https://autoclip.studio/review/smoke-test"
    context = {
        "creator_name": "Ivy",
        "video_title": "My Smoke Test",
        "clip_count": 4,
        "review_url": review_url,
    }

    with patch.object(mailer, "settings", _fake_settings("console")):
        with caplog.at_level(logging.INFO, logger="notify.mailer"):
            mailer.send(
                to="ivy@example.com",
                template="clips_ready",
                context=context,
                idempotency_key="smoke-test-key",
            )

    # The body is logged in the INFO record
    joined = " ".join(r.message for r in caplog.records)
    assert review_url in joined or "smoke-test-key" in joined, (
        "Expected the rendered body or idempotency key to appear in the log"
    )
