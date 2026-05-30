"""Observability primitives: correlation ids, structured logs, golden-signal metrics.

Issue 75(f). One request id flows end-to-end:

    inbound HTTP  ──▶  RequestIDMiddleware sets request_id_ctx, echoes the header
                       │
                       ├─ every log record gets `request_id` (RequestIDLogFilter)
                       ├─ HTTP latency/traffic/errors → Prometheus (the same middleware)
                       │
    task.delay()  ──▶  before_task_publish stamps the id onto the task headers
                       │
    worker run    ──▶  task_prerun binds it into the worker's context; task_postrun
                       clears it — so worker logs carry the originating request id.

The correlation layer is hand-rolled (ContextVar + ASGI middleware + logging
filter + Celery signals) rather than pulling in `asgi-correlation-id`: it is the
same documented pattern in ~60 lines we control, adding zero dependency/CVE
surface. Metrics use the canonical `prometheus-client`. See docs/DECISIONS.md
(2026-05-29, observability).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextvars import ContextVar
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# The originating request/correlation id for the current context. "-" until a
# middleware or Celery signal binds a real value, so a log line is never missing
# the field.
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

# Header used to carry the id between Celery's publish and run signals.
_CELERY_HEADER = "x_request_id"

# Per-task start timestamp, set in task_prerun and read in task_postrun to derive
# task duration (each worker process runs one task at a time → a ContextVar is safe).
_task_start_ctx: ContextVar[float] = ContextVar("task_start", default=0.0)

# Reserved LogRecord attribute names — anything not in here is emitted as an
# `extra` field in JSON mode.
_RESERVED_LOGRECORD = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()) | {
    "message",
    "asctime",
    "request_id",
    "taskName",
}


# ── Metrics (golden signals) ─────────────────────────────────────────────────
# Latency (histogram) + traffic/errors (the histogram's _count, labelled by
# status) cover request latency, traffic, and error rate. Saturation is observed
# at the infra layer (pool/queue depth) and via Celery task metrics below.
HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    labelnames=("method", "path", "status"),
)
CELERY_TASK_DURATION = Histogram(
    "celery_task_duration_seconds",
    "Celery task execution time in seconds",
    labelnames=("task", "state"),
)
CELERY_TASKS_TOTAL = Counter(
    "celery_tasks_total",
    "Celery tasks by terminal state",
    labelnames=("task", "state"),
)


# ── Structured logging ───────────────────────────────────────────────────────
class RequestIDLogFilter(logging.Filter):
    """Inject the current `request_id` onto every record so all logs are correlatable."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        return True


class JsonLogFormatter(logging.Formatter):
    """Emit one JSON object per record, including request_id and any `extra` fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOGRECORD and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(*, json_logs: bool, level: int = logging.INFO) -> None:
    """Install the request-id filter + (optionally) JSON formatting on the root logger.

    Idempotent: re-running replaces the root handler rather than stacking duplicates.
    """
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.addFilter(RequestIDLogFilter())
    if json_logs:
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s")
        )
    root.addHandler(handler)


# ── Request-id helpers ───────────────────────────────────────────────────────
def _valid_request_id(value: str | None) -> str:
    """Accept a sane upstream id; otherwise mint one. Bounds length to stop log injection."""
    if value and 0 < len(value) <= 200 and value.isprintable():
        return value
    return uuid.uuid4().hex


# ── HTTP middleware (pure ASGI — avoids BaseHTTPMiddleware pitfalls) ──────────
class RequestIDMiddleware:
    """Bind a per-request correlation id, echo it back, and record golden-signal metrics."""

    def __init__(self, app: ASGIApp, *, header: str, metrics_enabled: bool) -> None:
        self.app = app
        self.header = header
        self.header_lower = header.lower().encode("latin-1")
        self.metrics_enabled = metrics_enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inbound = None
        for raw_key, raw_value in scope.get("headers", []):
            if raw_key == self.header_lower:
                inbound = raw_value.decode("latin-1")
                break
        request_id = _valid_request_id(inbound)
        token = request_id_ctx.set(request_id)

        status_holder = {"status": 500}
        header_bytes = self.header.encode("latin-1")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = message.setdefault("headers", [])
                headers.append((header_bytes, request_id.encode("latin-1")))
            await send(message)

        start = time.perf_counter()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if self.metrics_enabled:
                # Label by route template (not the raw path) to keep cardinality bounded.
                route = scope.get("route")
                path = getattr(route, "path", None) or scope.get("path", "unknown")
                HTTP_REQUEST_DURATION.labels(
                    method=scope.get("method", "-"),
                    path=path,
                    status=str(status_holder["status"]),
                ).observe(time.perf_counter() - start)
            request_id_ctx.reset(token)


def metrics_response() -> tuple[bytes, str]:
    """Render the Prometheus exposition payload + its content type for a /metrics route."""
    return generate_latest(), CONTENT_TYPE_LATEST


# ── Celery propagation ───────────────────────────────────────────────────────
def _stamp_request_id(headers: dict[str, Any] | None = None, **_: Any) -> None:
    if headers is not None:
        headers[_CELERY_HEADER] = request_id_ctx.get()


def _bind_request_id(task: Any = None, **_: Any) -> None:
    request = getattr(task, "request", None)
    incoming = getattr(request, _CELERY_HEADER, None) if request is not None else None
    request_id_ctx.set(incoming or uuid.uuid4().hex)
    _task_start_ctx.set(time.perf_counter())


def _record_task_and_clear(task: Any = None, state: str | None = None, **_: Any) -> None:
    name = getattr(task, "name", None) or "unknown"
    label_state = state or "UNKNOWN"
    started = _task_start_ctx.get()
    if started:
        CELERY_TASK_DURATION.labels(task=name, state=label_state).observe(
            time.perf_counter() - started
        )
    CELERY_TASKS_TOTAL.labels(task=name, state=label_state).inc()
    request_id_ctx.set("-")
    _task_start_ctx.set(0.0)


def install_celery_observability() -> None:
    """Wire signal handlers that carry the request id across the publish→run boundary
    and record Celery task golden signals (duration + terminal-state counts).

    `weak=False` is required: Celery connects receivers weakly by default, so a
    handler held by no other reference would be garbage-collected and never fire.
    """
    from celery import signals

    signals.before_task_publish.connect(_stamp_request_id, weak=False)
    signals.task_prerun.connect(_bind_request_id, weak=False)
    signals.task_postrun.connect(_record_task_and_clear, weak=False)
