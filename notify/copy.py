"""
Per-event copy strings for transactional notifications (Issue 244).

Each event type has a subject (used as the email subject) and a body that appears
in both the email plain-text body and the in-app notification center.

Honesty constraint (CLAUDE.md): no copy promises virality.  Every message is
framed around the creator's own data and the action they need to take.

Usage in Jinja2 templates::

    from notify.copy import COPY
    subject = COPY["clips_ready"]["subject"]
    body    = COPY["clips_ready"]["body"]

The dict keys match the ``event_type`` argument passed to ``send_notification``.
Templates may override these defaults with dynamic context variables (e.g.
``video_title``, ``clip_count``) — this module provides the static fallbacks.
"""

from __future__ import annotations

# ── Canonical copy strings ────────────────────────────────────────────────────
# Keys must be stable identifiers that match the event_type strings used in
# send_notification.delay() call sites and in _build_inapp_notification().
# Add a new key here whenever a new event type is introduced.

COPY: dict[str, dict[str, str]] = {
    "clips_ready": {
        "subject": "Your clips are ready to review",
        "body": (
            "We found candidate clips from your video based on your channel's style "
            "and audience data. AutoClip predicts fit with your content — it does not "
            "promise virality."
        ),
    },
    "dna_built": {
        "subject": "Your channel DNA profile is ready",
        "body": (
            "We've built your channel DNA profile from your top and bottom performers. "
            "Review and confirm it so AutoClip can personalise clip scoring for your channel."
        ),
    },
    "refund_issued": {
        "subject": "Your minutes have been refunded",
        "body": (
            "We could not process your video. Your minutes have been automatically refunded "
            "to your balance. No action is required on your part."
        ),
    },
    "reauth_required": {
        "subject": "Reconnect your YouTube account",
        "body": (
            "Your YouTube connection needs to be refreshed. Reconnect your account so "
            "AutoClip can keep your analytics and catalog up to date."
        ),
    },
    "trial_ending": {
        "subject": "Your free trial is ending soon",
        "body": (
            "Your free trial is ending soon. Top up your minutes at /pricing to keep "
            "processing videos and reviewing clips."
        ),
    },
    "balance_low": {
        "subject": "Your minutes balance is running low",
        "body": (
            "You have a few minutes left. Add more at /pricing to keep processing videos."
        ),
    },
    "catalog_sync_done": {
        "subject": "Your video catalog has been updated",
        "body": "Your YouTube catalog has been synced successfully.",
    },
    "welcome": {
        "subject": "Welcome to AutoClip",
        "body": (
            "AutoClip predicts fit with your style and audience — it does not promise "
            "virality. Every recommendation is an estimate grounded in your own data. "
            "Connect your first video to get started."
        ),
    },
}
