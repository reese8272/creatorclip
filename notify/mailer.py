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

from jinja2 import Environment, FileSystemLoader, select_autoescape

from config import settings

logger = logging.getLogger(__name__)

# ── Jinja2 environment ──────────────────────────────────────────────────────
_TEMPLATES_DIR = Path(__file__).parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)

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
        import resend  # type: ignore[import-untyped]

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
            f"idempotency_key must be <= {_IDEMPOTENCY_KEY_MAX_LEN} chars; "
            f"got {len(key)}"
        )
    if not _IDEMPOTENCY_KEY_RE.match(key):
        raise ValueError(
            "idempotency_key must match [A-Za-z0-9_\\-\\.]+; "
            f"got {key!r}"
        )


# ── Template rendering ───────────────────────────────────────────────────────

def _render(template: str, context: dict) -> tuple[str, str]:
    """Return (text_body, html_body) for the named template + context dict.

    Raises jinja2.TemplateNotFound if the paired .txt/.html files are absent.
    """
    text_body: str = _jinja_env.get_template(f"{template}.txt").render(**context)
    html_body: str = _jinja_env.get_template(f"{template}.html").render(**context)
    return text_body, html_body


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
    text_body, html_body = _render(template, context)

    if settings.NOTIFY_BACKEND == "console":
        _send_console(to=to, template=template, text_body=text_body, idempotency_key=idempotency_key)
    elif settings.NOTIFY_BACKEND == "resend":
        _send_resend(to=to, text_body=text_body, html_body=html_body, idempotency_key=idempotency_key)
    else:
        raise ValueError(
            f"Unknown NOTIFY_BACKEND={settings.NOTIFY_BACKEND!r}. "
            "Supported values: 'console', 'resend'."
        )


def _send_console(
    *,
    to: str,
    template: str,
    text_body: str,
    idempotency_key: str,
) -> None:
    """Log the email to stdout (dev / CI sink). Never calls any external service."""
    logger.info(
        "notify.mailer [console] to=%s template=%s idempotency_key=%s body_preview=%.120r",
        to,
        template,
        idempotency_key,
        text_body,
    )


def _send_resend(
    *,
    to: str,
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
    import resend  # type: ignore[import-untyped]  # already guaranteed importable by _init_resend

    params: dict = {
        "from": settings.EMAIL_FROM,
        "to": [to],
        "subject": _extract_subject(text_body),
        "text": text_body,
        "html": html_body,
    }
    options: dict = {"idempotency_key": idempotency_key}
    response = resend.Emails.send(params, options)
    logger.info(
        "notify.mailer [resend] to=%s resend_id=%s idempotency_key=%s",
        to,
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
                return stripped[len("subject:"):].strip()
            return stripped
    return "(no subject)"
