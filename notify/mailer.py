"""
Transactional email dispatch (Issue 242).

Public API: send(to, template, context, idempotency_key) -> None

Dispatch is controlled by settings.NOTIFY_BACKEND:
  'console'  — renders the template and logs it (default; dev / CI)
  'resend'   — sends via Resend SDK; idempotency_key is forwarded to the
               provider's 24-hour dedup window (256-char limit).

Templates: Jinja2 Environment backed by notify/templates/.
Each template_name must have two files:
  notify/templates/<name>.txt   (plain-text body)
  notify/templates/<name>.html  (HTML body)

Module-level SDK init mirrors the singleton pattern used throughout this
codebase (e.g. dna/brief.py:21 — _ANTHROPIC = Anthropic(...) at import time).
We do NOT call resend.Emails.send at import time; we only assign the api_key,
which is the pattern documented at https://resend.com/docs/send-with-python.
The conditional import means 'resend' never needs to be importable in dev/CI
when NOTIFY_BACKEND='console'.
"""

import logging
import re
from pathlib import Path
from typing import cast

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from config import settings

logger = logging.getLogger(__name__)

# ── Jinja2 environment ──────────────────────────────────────────────────────
_TEMPLATES_DIR = Path(__file__).parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
    # StrictUndefined: any template var the caller forgets raises jinja2.UndefinedError
    # immediately at render time instead of silently rendering to an empty string.
    # This catches missing context keys (e.g. forgotten creator_name, video_title) during
    # development and CI rather than shipping blank subjects / "Hi ," greetings to users.
    undefined=StrictUndefined,
)
# Inject app_url as a Jinja2 global so every template can build absolute links
# (e.g. {{ app_url }}/pricing) without callers remembering to pass it explicitly.
# Pulled from settings.APP_BASE_URL; defaults to http://localhost:8000 in dev.
_jinja_env.globals["app_url"] = settings.APP_BASE_URL

# ── Resend SDK singleton (lazy-imported only when needed) ────────────────────
# import deferred to avoid requiring the 'resend' package in environments that
# use NOTIFY_BACKEND='console' (dev, CI, tests).
_resend_initialised: bool = False


def _init_resend() -> None:
    """Initialise the Resend module-level api_key exactly once.

    Resend's SDK uses a module-level assignment pattern — not a class
    instantiation. Calling this inside the first live send (not at import
    time) keeps the import-time side-effect isolated to console-only envs.
    """
    global _resend_initialised
    if _resend_initialised:
        return
    try:
        import resend

        resend.api_key = settings.RESEND_API_KEY
        _resend_initialised = True
    except ImportError as exc:
        raise RuntimeError(
            "NOTIFY_BACKEND='resend' requires the 'resend' package. "
            "Add resend==2.32.2 to requirements.txt and install it."
        ) from exc


# ── Idempotency key validation ───────────────────────────────────────────────
_IDEMPOTENCY_KEY_MAX_LEN = 256
_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9_\-\.]+$")


def _validate_idempotency_key(key: str) -> None:
    """Raise ValueError for keys that would be silently truncated or rejected by Resend."""
    if len(key) > _IDEMPOTENCY_KEY_MAX_LEN:
        raise ValueError(
            f"idempotency_key must be <= {_IDEMPOTENCY_KEY_MAX_LEN} chars; got {len(key)}"
        )
    if not _IDEMPOTENCY_KEY_RE.match(key):
        raise ValueError(f"idempotency_key must match [A-Za-z0-9_\\-\\.]+; got {key!r}")


# ── Template rendering ───────────────────────────────────────────────────────


def _render(template: str, context: dict) -> tuple[str, str, str]:
    """Return (subject, text_body, html_body) for the named template + context dict.

    Raises jinja2.TemplateNotFound if the paired .txt/.html files are absent.
    Raises jinja2.UndefinedError if the context is missing a required variable
    (enforced by StrictUndefined on the module-level environment).

    Convention: the first line of every .txt template is "Subject: <text>".
    _extract_subject() reads that first raw line for the subject header, then
    _strip_subject_line() removes it from the body so the recipient never sees a
    raw "Subject: ..." meta-line in the message text.  The HTML template has no
    Subject line to strip — it uses a proper <title> tag for preview text instead.
    """
    raw_text: str = _jinja_env.get_template(f"{template}.txt").render(**context)
    subject = _extract_subject(raw_text)
    text_body = _strip_subject_line(raw_text)
    html_body: str = _jinja_env.get_template(f"{template}.html").render(**context)
    return subject, text_body, html_body


def _strip_subject_line(text: str) -> str:
    """Remove the leading 'Subject: ...' line (and the blank line after it) from a .txt body.

    _extract_subject() already reads that first line to build the email subject header.
    This companion helper strips it from the body so the rendered text the recipient
    sees never starts with a raw "Subject:" meta-line.

    If the first non-empty line does not start with "Subject:" the text is returned
    unchanged — templates that omit the convention work without penalty.
    """
    lines = text.splitlines(keepends=True)
    # Find the first non-blank line
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped:
            if stripped.lower().startswith("subject:"):
                # Drop this Subject line; also drop one immediately-following blank line
                # so the greeting starts at the top rather than leaving an extra blank.
                remaining = lines[idx + 1 :]
                if remaining and remaining[0].strip() == "":
                    remaining = remaining[1:]
                return "".join(remaining)
            # First non-blank line is NOT a Subject line — return as-is
            break
    return text


# ── Public send API ──────────────────────────────────────────────────────────


def send(
    *,
    to: str,
    template: str,
    context: dict,
    idempotency_key: str,
) -> None:
    """Render and send a transactional email.

    Args:
        to: Recipient email address. Must be a valid RFC 5321 address.
        template: Template name (without extension). Paired .txt + .html
                  files must exist under notify/templates/.
        context: Template variables passed verbatim to Jinja2 render().
        idempotency_key: Provider-level dedup key (≤256 alphanumeric/._-).
                         The Resend backend forwards this to the API's 24-hour
                         dedup window; the console backend logs it for tracing.

    Returns:
        None. Raises on template-render errors, invalid keys, or Resend
        API errors (let callers / Celery retry logic handle them).
    """
    _validate_idempotency_key(idempotency_key)
    subject, text_body, html_body = _render(template, context)

    if settings.NOTIFY_BACKEND == "console":
        _send_console(template=template, text_body=text_body, idempotency_key=idempotency_key)
    elif settings.NOTIFY_BACKEND == "resend":
        _send_resend(
            to=to,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            idempotency_key=idempotency_key,
        )
    else:
        raise ValueError(
            f"Unknown NOTIFY_BACKEND={settings.NOTIFY_BACKEND!r}. "
            "Supported values: 'console', 'resend'."
        )


def _send_console(
    *,
    template: str,
    text_body: str,
    idempotency_key: str,
) -> None:
    """Log the email to stdout (dev / CI sink). Never calls any external service."""
    # Recipient email intentionally omitted to avoid PII in log sinks.
    logger.info(
        "notify.mailer [console] template=%s idempotency_key=%s body_preview=%.120r",
        template,
        idempotency_key,
        text_body,
    )


def _send_resend(
    *,
    to: str,
    subject: str,
    text_body: str,
    html_body: str,
    idempotency_key: str,
) -> None:
    """Send via the Resend SDK with provider-level idempotency.

    SDK pattern: resend.Emails.send(params_dict, options_dict) where
    options={'idempotency_key': key} threads the key to the 24-hour
    dedup window on the provider side. This is the documented API shape
    from https://resend.com/docs/dashboard/emails/idempotency-keys .
    """
    _init_resend()
    import resend  # already guaranteed importable by _init_resend

    params = cast(
        resend.Emails.SendParams,
        {
            "from": settings.EMAIL_FROM,
            "to": [to],
            "subject": subject,
            "text": text_body,
            "html": html_body,
        },
    )
    options = cast(resend.Emails.SendOptions, {"idempotency_key": idempotency_key})
    response = resend.Emails.send(params, options)
    # Recipient email omitted from log to avoid PII in log sinks.
    logger.info(
        "notify.mailer [resend] resend_id=%s idempotency_key=%s",
        getattr(response, "id", None),
        idempotency_key,
    )


def _extract_subject(text_body: str) -> str:
    """Extract the first non-blank line from the text body as the email subject.

    Conventions: templates place 'Subject: <subject text>' on the first line,
    or fall back to using the first non-empty line verbatim.
    """
    for line in text_body.splitlines():
        stripped = line.strip()
        if stripped:
            if stripped.lower().startswith("subject:"):
                return stripped[len("subject:") :].strip()
            return stripped
    return "(no subject)"
