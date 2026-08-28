"""Issue 525 — a zero-clip video must not be told its clips are ready.

`clips_ready` fired unconditionally after clip generation. `clip_count` was
always in the payload; nothing branched on it. So a video that produced no
clips emailed *"Your 0 clips from "<title>" are ready for review."* with a
"Review your clips" CTA into an empty queue, and wrote an in-app row saying
"We found candidate clips from your video." — with a delivery row committed
`status=sent` for it.

Zero clips is a DESIGNED outcome: the five-code `skip_reason` taxonomy exists
for it, the dashboard has a "Why no clips?" link, and Issue 458 deliberately
made it more common. The terminal SSE event one line earlier was already honest
("Generated 0 clip(s)."); only the outbound notification lied.
"""

from __future__ import annotations

import pytest

from notify import copy as notify_copy

# ── Copy exists for the zero case, and is honest ─────────────────────────────


def test_zero_case_event_has_its_own_copy() -> None:
    assert "no_clips_found" in notify_copy.COPY, (
        "the zero-clip case needs its own event type — reusing clips_ready with "
        "clip_count=0 is what produced 'Your 0 clips are ready' (Issue 525)"
    )


def test_zero_case_copy_never_claims_clips_were_found() -> None:
    """The honesty assertion, stated as a property so the wording can change."""
    entry = notify_copy.COPY["no_clips_found"]
    blob = f"{entry['subject']} {entry['body']}".lower()
    assert "are ready" not in blob
    assert "we found candidate clips" not in blob
    assert "review your clips" not in blob


def test_zero_case_copy_makes_no_virality_promise() -> None:
    """The standing product constraint applies to new copy too."""
    entry = notify_copy.COPY["no_clips_found"]
    blob = f"{entry['subject']} {entry['body']}".lower()
    for banned in ("viral", "guarantee", "guaranteed"):
        assert banned not in blob


# ── The email templates render, and carry the reason ─────────────────────────


def _render(template: str, context: dict):
    from notify import mailer

    return mailer._render(template, context)


def _zero_context() -> dict:
    return {
        "creator_name": "Sam",
        "video_title": "My Stream",
        "skip_reason": "no_positive_signal",
        "skip_reason_label": "No positive signal anywhere in the video",
        "dashboard_url": "https://autoclip.studio/app/dashboard",
    }


def test_zero_case_email_renders_and_explains_why() -> None:
    """StrictUndefined means a missing context var raises — this pins the contract."""
    subject, text, html = _render("no_clips_found", _zero_context())
    assert subject
    for body in (text, html):
        assert "No positive signal anywhere in the video" in body, (
            "the creator must be told WHY there were no clips — the API already "
            "computes this reason (Issue 525 AC)"
        )
        assert "0 clip" not in body


def test_zero_case_email_uses_the_current_brand() -> None:
    """The rebrand (Issue 488) missed notify/templates/; new copy must not add to it."""
    _, text, html = _render("no_clips_found", _zero_context())
    assert "CreatorClip" not in text
    assert "CreatorClip" not in html


def test_clips_ready_still_renders_for_a_real_count() -> None:
    """The success path is unchanged — the fix is a branch, not a rewrite."""
    subject, text, _ = _render(
        "clips_ready",
        {
            "creator_name": "Sam",
            "video_title": "My Stream",
            "clip_count": 3,
            "review_url": "https://autoclip.studio/app/review",
        },
    )
    assert subject
    assert "3 clip" in text


# ── The in-app row ───────────────────────────────────────────────────────────


def test_inapp_row_for_zero_case_does_not_claim_clips_were_found() -> None:
    import uuid

    from worker.tasks import _build_inapp_notification

    notif = _build_inapp_notification(uuid.uuid4(), "no_clips_found", {})
    assert notif.kind == "no_clips_found"
    blob = f"{notif.title} {notif.body}".lower()
    assert "we found candidate clips" not in blob
    assert "are ready" not in blob


@pytest.mark.parametrize("event_type", ["clips_ready", "no_clips_found"])
def test_every_clip_outcome_event_has_inapp_copy(event_type: str) -> None:
    """A new event type registered in one map and not the other falls back to
    generic copy — silently, which is how this class of defect ships."""
    import uuid

    from worker.tasks import _build_inapp_notification

    notif = _build_inapp_notification(uuid.uuid4(), event_type, {})
    assert notif.title, f"{event_type} has no in-app title"
    assert notif.body, f"{event_type} has no in-app body"


# ── The production guard (Issue 525 AC #2) ───────────────────────────────────
#
# Measured on prod 2026-08-18: ENV=production, NOTIFY_BACKEND=console, 17
# delivery rows, every one status='sent', not one delivered. WARN rather than
# raise, deliberately — prod has no RESEND_API_KEY, so raising would fail the
# next deploy's boot. The hard reject is filed separately, gated on Resend.


def _prod_settings(monkeypatch, backend: str) -> None:
    """Set the minimum env for ENV=production to construct.

    The production validator raises on the first missing required value, and the
    NOTIFY_BACKEND warning sits after those checks — so without a complete env
    the warning is never reached and the test would pass or fail for the wrong
    reason. These are fake values; nothing here contacts a real service.
    """
    for key, value in {
        "ENV": "production",
        "NOTIFY_BACKEND": backend,
        "STORAGE_BACKEND": "r2",
        "STRIPE_WEBHOOK_SECRET": "whsec_test_not_real",
        "STRIPE_SECRET_KEY": "sk_test_not_real",
        "R2_ACCOUNT_ID": "test-account",
        "R2_ACCESS_KEY_ID": "test-key-id",
        "R2_SECRET_ACCESS_KEY": "test-secret",
        "R2_BUCKET": "test-bucket",
        "LOCAL_MEDIA_DIR": "/tmp/media",
        "APP_BASE_URL": "https://autoclip.studio",
        "ALLOWED_ORIGINS": "https://autoclip.studio",
    }.items():
        monkeypatch.setenv(key, value)


def test_console_backend_in_production_raises(monkeypatch) -> None:
    """Issue 528 (upgrades the Issue-525 warning): with Resend provisioned, a
    non-delivering notify backend in production is an unmissable startup
    failure — the console backend delivers nothing while rows still end
    'sent', which is exactly how 17 undelivered rows stayed invisible."""
    import pytest as _pytest

    _prod_settings(monkeypatch, "console")
    from config import Settings

    with _pytest.raises(ValueError, match="NOTIFY_BACKEND must be 'resend'"):
        Settings()


def test_resend_backend_in_production_constructs_cleanly(monkeypatch, caplog) -> None:
    import logging

    _prod_settings(monkeypatch, "resend")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key_not_real")
    # EMAIL_FROM is separately required with the resend backend (Issue 352):
    # posting `"from": ""` makes Resend 422 every transactional email.
    monkeypatch.setenv("EMAIL_FROM", "hello@autoclip.studio")
    from config import Settings

    with caplog.at_level(logging.WARNING):
        settings = Settings()
    assert settings.NOTIFY_BACKEND == "resend"
    warnings = " ".join(r.getMessage() for r in caplog.records)
    assert "NOTIFY_BACKEND" not in warnings


# ── The delivery row records which backend handled it (AC #3) ────────────────


def test_delivery_row_has_a_handled_by_column() -> None:
    """`status` alone cannot tell a real delivery from a console no-op — both
    write 'sent'. This column is what makes console-era rows identifiable."""
    from models import NotificationDelivery

    assert hasattr(NotificationDelivery, "handled_by")


def test_handled_by_is_nullable_so_historical_rows_stay_untouched() -> None:
    """By decision: the 17 pre-existing rows keep NULL rather than being
    backfilled or re-sent. NULL honestly means 'written before this existed'."""
    from models import NotificationDelivery

    assert NotificationDelivery.__table__.c.handled_by.nullable is True
