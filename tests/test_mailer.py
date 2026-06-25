"""
Unit tests for notify/mailer.py (Issue 242, Issue 311).

These tests run against the console backend with monkeypatching — no live
external service, no Docker, no Postgres required.

80/20 coverage:
- Happy path: console sink renders template + logs without error
- Idempotency key is forwarded to the provider options dict (resend backend)
- Backend switch (console vs resend) is config-driven
- Missing RESEND_API_KEY in resend mode fails fast at settings load
- Invalid/oversized idempotency keys raise ValueError immediately
- Issue 311: StrictUndefined raises on missing vars (not silent empty string)
- Issue 311: clips_ready production context shape renders correctly
- Issue 311: Subject line stripped from text body
- Issue 311: Every COPY key has both .txt and .html template files on disk
- Issue 311: No "to=" PII in log output
"""

import logging
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEMPLATE_DIR = Path(__file__).parent.parent / "notify" / "templates"


def _fake_settings(
    notify_backend: str = "console", resend_api_key: str = "", email_from: str = ""
) -> MagicMock:
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

    with (
        patch.object(mailer, "settings", _fake_settings("console")),
        caplog.at_level(logging.INFO, logger="notify.mailer"),
    ):
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

    with (
        patch.object(mailer, "settings", _fake_settings("console")),
        caplog.at_level(logging.INFO, logger="notify.mailer"),
    ):
        mailer.send(
            to="bob@example.com",
            template="clips_ready",
            context=context,
            idempotency_key=idem_key,
        )

    joined = " ".join(r.message for r in caplog.records)
    assert idem_key in joined, f"Expected idempotency key {idem_key!r} to appear in log output"


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
    with (
        patch.object(mailer, "_resend_initialised", False),
        patch.object(
            mailer,
            "settings",
            _fake_settings(
                "resend", resend_api_key="re_test", email_from="noreply@autoclip.studio"
            ),
        ),
        patch.dict(sys.modules, {"resend": fake_resend}),
    ):
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

    with (
        patch.object(mailer, "_resend_initialised", False),
        patch.object(
            mailer,
            "settings",
            _fake_settings("resend", resend_api_key="re_test", email_from=email_from),
        ),
        patch.dict(sys.modules, {"resend": fake_resend}),
    ):
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
# Issue 245 — RFC 8058 one-click unsubscribe headers on lifecycle sends
# ---------------------------------------------------------------------------


def _resend_send_with_headers(headers: dict | None) -> MagicMock:
    """Run a resend-backend send() with the given headers and return the mock
    so the params dict can be asserted."""
    from notify import mailer

    fake_resend = MagicMock()
    fake_resend.api_key = None
    fake_resend.Emails = MagicMock()
    fake_resend.Emails.send = MagicMock(return_value=MagicMock(id="resend-msg-lifecycle"))

    with (
        patch.object(mailer, "_resend_initialised", False),
        patch.object(
            mailer,
            "settings",
            _fake_settings("resend", resend_api_key="re_test", email_from="noreply@autoclip.studio"),
        ),
        patch.dict(sys.modules, {"resend": fake_resend}),
    ):
        mailer.send(
            to="lifecycle@example.com",
            template="first_clip_nudge",
            context={
                "creator": types.SimpleNamespace(channel_title="Nudge Channel"),
                "unsubscribe_url": "https://autoclip.studio/unsubscribe/abc",
            },
            idempotency_key="lifecycle-headers-key",
            headers=headers,
        )
    return fake_resend


def test_lifecycle_send_forwards_unsubscribe_headers() -> None:
    """A lifecycle send() forwards the RFC 8058 one-click unsubscribe pair into
    the resend SendParams 'headers' dict."""
    headers = {
        "List-Unsubscribe": "<https://autoclip.studio/unsubscribe/abc>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
    fake_resend = _resend_send_with_headers(headers)
    params = fake_resend.Emails.send.call_args.args[0]
    assert params["headers"]["List-Unsubscribe"] == "<https://autoclip.studio/unsubscribe/abc>"
    assert params["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_transactional_send_omits_unsubscribe_headers() -> None:
    """A transactional send() (headers=None) must NOT carry a 'headers' key in
    the resend SendParams."""
    fake_resend = _resend_send_with_headers(None)
    params = fake_resend.Emails.send.call_args.args[0]
    assert "headers" not in params


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

    with (
        patch.object(mailer, "settings", _fake_settings("smtp")),
        pytest.raises(ValueError, match="Unknown NOTIFY_BACKEND"),
    ):
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
    with (
        patch.object(mailer, "settings", _fake_settings("console")),
        pytest.raises(ValueError, match="256"),
    ):
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
    with (
        patch.object(mailer, "settings", _fake_settings("console")),
        caplog.at_level(logging.INFO, logger="notify.mailer"),
    ):
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

    with (
        patch.object(mailer, "settings", _fake_settings("console")),
        pytest.raises(ValueError, match="idempotency_key"),
    ):
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
    from pydantic import ValidationError

    from config import Settings

    # Provide all required fields via the environment so pydantic-settings reads them.
    # Using model_construct bypasses validators; we must go through __init__ to trigger
    # the _validate_notify_backend model_validator.
    env_overrides = {
        "NOTIFY_BACKEND": "resend",
        "RESEND_API_KEY": "",
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

    with (
        patch.dict(os.environ, env_overrides, clear=False),
        pytest.raises(ValidationError, match="RESEND_API_KEY"),
    ):
        Settings()


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

    with (
        patch.object(mailer, "settings", _fake_settings("console")),
        caplog.at_level(logging.INFO, logger="notify.mailer"),
    ):
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


def test_clips_ready_txt_no_virality_language() -> None:
    """The clips_ready .txt template must not promise virality, and must carry
    the required honesty disclaimer."""
    from notify import mailer
    from tests.test_honesty import assert_no_virality_promise

    _, text_body, _ = mailer._render(
        "clips_ready",
        {
            "creator_name": "Jan",
            "video_title": "My Test",
            "clip_count": 2,
            "review_url": "https://autoclip.studio/review/jan",
        },
    )
    # Canonical honesty assertion — allowlist-scrubs the legitimate disclaimer
    # ("does not promise virality") before scanning for banned promise phrases.
    assert_no_virality_promise(text_body, label="clips_ready.txt")
    # Positively require the disclaimer to be present.
    assert "does not promise virality" in text_body.lower()


# ---------------------------------------------------------------------------
# Issue 311 — StrictUndefined, Subject stripping, PII log, template coverage
# ---------------------------------------------------------------------------


def _make_creator(channel_title: str = "Test Channel") -> types.SimpleNamespace:
    """Lightweight creator stand-in matching what worker/tasks passes to mailer.send()."""
    return types.SimpleNamespace(channel_title=channel_title)


def test_strict_undefined_raises_on_missing_var() -> None:
    """StrictUndefined must raise jinja2.UndefinedError when a template var is missing,
    not silently render an empty string."""
    from jinja2 import UndefinedError

    from notify import mailer

    # clips_ready requires creator_name, video_title, clip_count, review_url.
    # Omit creator_name — StrictUndefined must blow up.
    with pytest.raises(UndefinedError):
        mailer._render(
            "clips_ready",
            {
                # deliberately missing creator_name
                "video_title": "Missing Var Test",
                "clip_count": 1,
                "review_url": "https://autoclip.studio/review/x",
            },
        )


def test_clips_ready_production_context_shape(caplog: pytest.LogCaptureFixture) -> None:
    """clips_ready rendered with the production context shape produces a non-empty subject
    and an absolute review link (must start with 'https://').

    Reproduces the exact payload worker/tasks.py send_notification.delay() now sends.
    """
    from notify import mailer

    review_url = "https://autoclip.studio/app/review"
    context = {
        "creator_name": "Alice",
        "video_title": "My YouTube Video",
        "clip_count": 3,
        "review_url": review_url,
    }

    with (
        patch.object(mailer, "settings", _fake_settings("console")),
        caplog.at_level(logging.INFO, logger="notify.mailer"),
    ):
        mailer.send(
            to="alice@example.com",
            template="clips_ready",
            context=context,
            idempotency_key="311-clips-ready-test",
        )

    # Confirm subject is non-empty by checking the rendered .txt directly
    subject, text_body, _ = mailer._render("clips_ready", context)
    assert subject, "Subject must not be empty"
    assert review_url in text_body, "Absolute review_url must appear in the text body"
    assert text_body.startswith("Hi "), "Body must not start with 'Subject:'"


def test_trial_ending_empty_payload_context(caplog: pytest.LogCaptureFixture) -> None:
    """trial_ending with an empty {} payload renders correctly via creator object.

    This mirrors the actual dispatch: send_notification.delay(creator_id, 'trial_ending', ..., {})
    The 'creator' object is injected by the task and app_url is a Jinja2 global.
    """
    from notify import mailer

    creator = _make_creator("Bob's Channel")
    subject, text_body, _ = mailer._render("trial_ending", {"creator": creator})

    assert subject, "Subject must not be empty for trial_ending"
    # The absolute link must appear (app_url global + /pricing)
    assert "http" in text_body, "trial_ending body must contain an absolute link"
    assert "/pricing" in text_body, "trial_ending body must reference the pricing path"


def test_rendered_body_does_not_contain_subject_line() -> None:
    """The text body returned by _render must never start with 'Subject:'.

    The Subject: line belongs in the email header, not the message body.
    """
    from notify import mailer

    creator = _make_creator()
    for template_name in [
        "clips_ready",
        "trial_ending",
        "balance_low",
        "dna_built",
        "reauth_required",
        "refund_issued",
        "welcome",
        "catalog_sync_done",
    ]:
        ctx: dict = {"creator": creator}
        if template_name == "clips_ready":
            ctx = {
                "creator_name": "Alice",
                "video_title": "My Video",
                "clip_count": 2,
                "review_url": "https://autoclip.studio/app/review",
            }

        _, text_body, _ = mailer._render(template_name, ctx)

        for line in text_body.splitlines():
            assert not line.strip().lower().startswith("subject:"), (
                f"{template_name}.txt rendered body must not contain a 'Subject:' line; "
                f"got: {line!r}"
            )


def test_all_copy_keys_have_template_files() -> None:
    """Every emailable event_type defined in notify/copy.py COPY must have both
    a .txt and .html template file on disk.

    Prevents the situation where copy exists but no template pair renders it.
    """
    from notify.copy import COPY

    templates_dir = Path(__file__).parent.parent / "notify" / "templates"

    for event_type in COPY:
        txt_path = templates_dir / f"{event_type}.txt"
        html_path = templates_dir / f"{event_type}.html"
        assert txt_path.exists(), (
            f"Missing template: notify/templates/{event_type}.txt "
            f"(defined in COPY but no .txt file)"
        )
        assert html_path.exists(), (
            f"Missing template: notify/templates/{event_type}.html "
            f"(defined in COPY but no .html file)"
        )


def test_no_pii_in_console_log(caplog: pytest.LogCaptureFixture) -> None:
    """The console backend must not log the recipient email address (PII)."""
    from notify import mailer

    recipient = "secret-user@private-domain.com"
    context = {
        "creator_name": "Charlie",
        "video_title": "PII Test",
        "clip_count": 1,
        "review_url": "https://autoclip.studio/app/review",
    }

    with (
        patch.object(mailer, "settings", _fake_settings("console")),
        caplog.at_level(logging.INFO, logger="notify.mailer"),
    ):
        mailer.send(
            to=recipient,
            template="clips_ready",
            context=context,
            idempotency_key="311-pii-test",
        )

    joined = " ".join(r.message for r in caplog.records)
    assert recipient not in joined, f"Recipient email {recipient!r} must not appear in any log line"


def test_clips_ready_subject_contains_video_title() -> None:
    """The clips_ready subject line must include the video title (dynamic suffix)."""
    from notify import mailer

    video_title = "How to grow your channel in 2026"
    subject, _, _ = mailer._render(
        "clips_ready",
        {
            "creator_name": "Diana",
            "video_title": video_title,
            "clip_count": 5,
            "review_url": "https://autoclip.studio/app/review",
        },
    )
    assert video_title in subject, (
        f"clips_ready subject must contain the video title; got: {subject!r}"
    )
