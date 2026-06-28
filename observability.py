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
import logging.handlers
import time
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.types import ASGIApp, Message, Receive, Scope, Send

if TYPE_CHECKING:
    # sentry-sdk ships inline types; `before_send` must be typed as its `Event`
    # (a TypedDict), not a bare dict, or mypy rejects the callable at `init()`.
    from sentry_sdk.types import Event

from redact import scrub_dict

# The originating request/correlation id for the current context. "-" until a
# middleware or Celery signal binds a real value, so a log line is never missing
# the field.
# NOTE (Issue 76): safe under the Celery prefork pool — see _task_start_ctx note below.
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

# Header used to carry the id between Celery's publish and run signals.
_CELERY_HEADER = "x_request_id"

# Per-task start timestamp, set in task_prerun and read in task_postrun to derive
# task duration.
#
# NOTE (Issue 76): ContextVars are safe here ONLY under the Celery prefork pool
# (each forked worker process runs exactly one task at a time; its ContextVar state
# is isolated from other processes). This assumption is currently enforced by the
# Celery config — see celery_app.py: worker_pool="prefork", worker_concurrency
# derived from settings. If the pool is ever changed to gevent/eventlet/threads,
# ContextVar isolation no longer holds and a per-task threading.local or explicit
# task-argument passing must replace these module-level vars.
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
# status) cover request latency, traffic, and error rate. Saturation is covered
# by app-level Gauges below (Issue 238) and by Celery task metrics below.
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
# LLM token usage counter — labels aligned to OTel GenAI semconv
# gen_ai.usage.input_tokens / gen_ai.usage.output_tokens (provider/model/kind).
# Labels are intentionally low-cardinality: provider and model are a small finite
# set; kind is 'input' | 'output' | 'cache_read' | 'cache_creation'. creator_id is
# deliberately excluded to prevent cardinality blowup at 10k creators.
LLM_TOKENS_TOTAL = Counter(
    "llm_tokens_total",
    "LLM tokens by provider/model/kind",
    labelnames=("provider", "model", "kind"),
)
RENDER_FAILURES_TOTAL = Counter(
    "render_failures_total",
    "Render pipeline failures",
    labelnames=("task",),
)

# ── Saturation gauges (Issue 238) ────────────────────────────────────────────
# The fourth golden signal — instantaneous resource depth at the app layer.
# These are collected on demand during the /metrics scrape via
# collect_saturation_gauges(engine, redis_client); no new connections are opened.
DB_POOL_CHECKED_OUT = Gauge(
    "db_pool_checked_out_connections",
    "SQLAlchemy pool checked-out connections",
)
CELERY_QUEUE_DEPTH = Gauge(
    "celery_queue_depth",
    "Celery broker queue depth",
    labelnames=("queue",),
)
REDIS_USED_MEMORY_BYTES = Gauge(
    "redis_used_memory_bytes",
    "Redis used memory in bytes",
)


async def collect_saturation_gauges(engine: Any, redis_client: Any) -> None:
    """Read pool/queue/memory depth and update the saturation Gauges.

    Reuses the existing module-level SQLAlchemy engine and Redis singleton —
    zero new connections. On any exception the gauge retains its last value
    so a transient failure never 500s the Prometheus scrape.

    Called from the /metrics route in main.py (Issue 238).
    """
    # SQLAlchemy pool depth — synchronous attribute, no network call.
    try:
        DB_POOL_CHECKED_OUT.set(engine.pool.checkedout())
    except Exception:
        logging.getLogger(__name__).warning("collect_saturation_gauges: pool stat unavailable")

    # Celery queue depth (default queue) — one async LLEN.
    try:
        depth = await redis_client.llen("celery")
        CELERY_QUEUE_DEPTH.labels(queue="celery").set(depth)
    except Exception:
        logging.getLogger(__name__).warning("collect_saturation_gauges: queue LLEN unavailable")

    # Redis used memory — one async INFO memory call.
    try:
        info = await redis_client.info("memory")
        used = info.get("used_memory", 0)
        REDIS_USED_MEMORY_BYTES.set(used)
    except Exception:
        logging.getLogger(__name__).warning("collect_saturation_gauges: Redis INFO unavailable")


def record_llm_tokens(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> None:
    """Increment LLM_TOKENS_TOTAL for a single LLM call.

    Each token kind (input, output, cache_read, cache_creation) is a separate
    label value so Prometheus can sum or filter by kind in PromQL. Never put
    prompt text or creator_id in labels — only low-cardinality identifiers.

    Token counts are coerced to int. Callers may safely pass the result of
    `getattr(usage, "cache_read_input_tokens", 0)` — the coercion silently
    drops non-integer values (e.g. None) to 0.
    """

    def _to_int(v: Any) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    _in = _to_int(input_tokens)
    _out = _to_int(output_tokens)
    _cr = _to_int(cache_read_tokens)
    _cc = _to_int(cache_creation_tokens)
    LLM_TOKENS_TOTAL.labels(provider=provider, model=model, kind="input").inc(_in)
    LLM_TOKENS_TOTAL.labels(provider=provider, model=model, kind="output").inc(_out)
    if _cr:
        LLM_TOKENS_TOTAL.labels(provider=provider, model=model, kind="cache_read").inc(_cr)
    if _cc:
        LLM_TOKENS_TOTAL.labels(provider=provider, model=model, kind="cache_creation").inc(_cc)


def record_llm_metric(model: str, usage: Any, *, provider: str = "anthropic") -> None:
    """Increment ``llm_tokens_total`` from either Anthropic usage shape (Issue 332).

    The codebase carries token usage in two shapes and the Prometheus counter was
    only being incremented at ~half the LLM call sites — leaving the cost-by-feature
    dashboard blind to the heaviest consumers (scoring, DNA brief, most knowledge
    features). This adapter normalizes both shapes so every LLM module can record
    the metric with one DRY call placed next to its existing billing-ledger write:

      * an Anthropic ``Usage`` object (non-streaming ``.create()`` path) exposing
        ``input_tokens`` / ``output_tokens`` / ``cache_read_input_tokens`` /
        ``cache_creation_input_tokens``;
      * the ``stream_and_emit()`` ``usage`` dict with keys ``input_tokens`` /
        ``output_tokens`` / ``cache_read`` / ``cache_creation``.

    Missing fields coerce to 0 (older SDKs / no cache this call). This only touches
    the metric — billing (``record_llm_usage``) and the per-call token log lines are
    unchanged.
    """
    if isinstance(usage, dict):
        _in = usage.get("input_tokens", 0)
        _out = usage.get("output_tokens", 0)
        _cr = usage.get("cache_read", 0)
        _cc = usage.get("cache_creation", 0)
    else:
        _in = getattr(usage, "input_tokens", 0)
        _out = getattr(usage, "output_tokens", 0)
        _cr = getattr(usage, "cache_read_input_tokens", 0)
        _cc = getattr(usage, "cache_creation_input_tokens", 0)
    record_llm_tokens(provider, model, _in, _out, _cr, _cc)


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
        extra: dict[str, Any] = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED_LOGRECORD and not key.startswith("_")
        }
        payload.update(scrub_dict(extra))
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_event_logger = logging.getLogger("event")


def log_event(event: str, **fields: Any) -> None:
    """Emit one structured business-event log line.

    Use at every load-bearing user-action surface so production debugging is
    `grep event=dna_build_started creator_id=X`, not a Cloudflare-tunnel +
    code-bisect treasure hunt. Fields land as top-level JSON keys in
    JsonLogFormatter mode (i.e. searchable as `creator_id:"..."` in
    aggregators) and as a `key=value` tail in dev text mode.

    Example:
        log_event("dna_build_started", creator_id=str(creator.id), task_id=tid)

    Conventions:
        - `event` is a short snake_case noun_verb (created, fetched, started).
        - Fields should be small primitives — never raw request bodies, tokens,
          or PII. The same JsonLogFormatter that emits these honors the
          existing PII / token-leak compliance rules.

    Issue 88.
    """
    extra = {"event": event, **fields}
    if _event_logger.isEnabledFor(logging.INFO):
        # `event=<name>` also leads the message so dev text mode is greppable.
        msg_parts = [f"event={event}"] + [f"{k}={v}" for k, v in fields.items()]
        _event_logger.info(" ".join(msg_parts), extra=extra)


def configure_logging(
    *,
    json_logs: bool,
    level: int = logging.INFO,
    log_dir: str = "",
    filename: str = "app.log",
) -> None:
    """Install the request-id filter + (optionally) JSON formatting on the root logger.

    When log_dir is non-empty, also writes a rotating JSON file at
    <log_dir>/<filename> (10 MB × 5 files) so logs survive container restarts.
    The .:/app Docker volume makes <log_dir>/app.log readable on the host as
    ./logs/app.log without any extra mount configuration.

    The *filename* parameter lets co-hosted processes write distinct files so
    Python's RotatingFileHandler rotation does not corrupt a shared file
    (bugs.python.org/issue43107). The API process uses the default 'app.log';
    the Celery worker uses 'worker.log' (Issue 239).

    Idempotent: re-running replaces the root handler rather than stacking duplicates.
    """
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    fmt = (
        JsonLogFormatter()
        if json_logs
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s")
    )
    rid_filter = RequestIDLogFilter()

    stream_handler = logging.StreamHandler()
    stream_handler.addFilter(rid_filter)
    stream_handler.setFormatter(fmt)
    root.addHandler(stream_handler)

    if log_dir:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_path / filename,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.addFilter(rid_filter)
        file_handler.setFormatter(JsonLogFormatter())
        root.addHandler(file_handler)


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


def _sentry_before_send(event: Event, hint: dict[str, Any]) -> Event | None:
    """Scrub PII / secrets from Sentry events before they leave the process.

    Applies scrub_dict (from redact.py, the project's single source of truth for
    the blocklist) to the event's extra dict and request body. This is a
    structural backstop — it catches any field that slips past call-site
    discipline, which is the same pattern JsonLogFormatter uses.

    `event` is sentry's `Event` TypedDict; we mutate it through a plain-dict view
    (`cast`) since scrub_dict is structural and the keys we touch are dynamic.
    """
    data = cast("dict[str, Any]", event)
    if "extra" in data:
        data["extra"] = scrub_dict(data["extra"])
    try:
        req_data = data.get("request", {}).get("data")
        if isinstance(req_data, dict):
            data["request"]["data"] = scrub_dict(req_data)
    except Exception:
        pass
    return event


def init_sentry(
    dsn: str,
    environment: str,
    release: str,
    traces_sample_rate: float = 0.05,
) -> None:
    """Initialise the Sentry SDK with FastAPI + Celery + SQLAlchemy + Redis integrations.

    Call once at process startup — in main.py for the API process and in
    worker/celery_app.py for the Celery worker. When dsn is empty the function
    is a no-op so dev/CI run without any Sentry configuration.

    send_default_pii=False is unconditional — PII must never leave the process.
    before_send applies scrub_dict as a structural backstop.

    GlitchTip (v6, Sentry-protocol compatible) works as a drop-in: set SENTRY_DSN
    to the GlitchTip project DSN URL and this function needs no changes.
    (Issue 281; sources: docs.sentry.io/platforms/python/integrations/fastapi/,
    docs.sentry.io/platforms/python/integrations/celery/)
    """
    if not dsn:
        return
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.redis import RedisIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

    sentry_sdk.init(
        dsn=dsn,
        integrations=[
            FastApiIntegration(),
            CeleryIntegration(),
            SqlalchemyIntegration(),
            RedisIntegration(),
        ],
        traces_sample_rate=traces_sample_rate,
        send_default_pii=False,
        environment=environment,
        release=release or None,
        before_send=_sentry_before_send,
    )


# ── OpenTelemetry (Issue 326) ────────────────────────────────────────────────
# All OTel imports are LAZY (inside init_otel / helpers) so the module never
# touches opentelemetry.* at import time.  When OTEL_EXPORTER_OTLP_ENDPOINT is
# empty the entire block is bypassed — zero OTel packages imported, zero network
# calls — mirroring the init_sentry no-op pattern exactly.

_otel_initialized: bool = False


def _parse_otlp_headers(raw: str) -> dict[str, str]:
    """Convert the OTel-standard 'key=value,key2=value2' header string into a dict.

    This is the format accepted by OTEL_EXPORTER_OTLP_HEADERS and by the
    OTLPSpanExporter / OTLPMetricExporter headers kwarg.

    Args:
        raw: Comma-separated key=value pairs.  Empty string → empty dict.

    Returns:
        A mapping of header names to values.
    """
    if not raw:
        return {}
    headers: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if "=" in pair:
            key, _, value = pair.partition("=")
            headers[key.strip()] = value.strip()
    return headers


def init_otel(service_name: str | None = None) -> None:
    """Initialise the OpenTelemetry SDK with OTLP/HTTP exporters for Grafana Cloud.

    Call once at process startup — in main.py for the API process and in
    worker/celery_app.py for the Celery worker.  When
    OTEL_EXPORTER_OTLP_ENDPOINT is empty the function is a strict no-op: it
    imports nothing, sets no global state, and makes no network calls.  This
    preserves the dev/CI offline guarantee.

    All opentelemetry.* imports are inside this function (lazy).  The function
    is idempotent — a second call is silently skipped so a misconfigured boot
    sequence cannot double-instrument.

    Auto-instrumentation registered here:
    - CeleryInstrumentor       — task publish/run spans
    - SQLAlchemyInstrumentor   — global engine patch (safe with recreate_engine)
    - RedisInstrumentor        — Redis command spans
    - HTTPXClientInstrumentor  — outbound calls (Anthropic, Deepgram, Voyage, YouTube)
    - BotocoreInstrumentor     — R2/S3 object operations
    - AnthropicInstrumentor    — LLM token spans (OpenLLMetry; content capture OFF)

    FastAPI is instrumented separately via instrument_fastapi_app(app) because
    it requires the FastAPI application object, which is created after startup.

    Args:
        service_name: The ``service.name`` OTel resource attribute for this
            process.  Defaults to ``settings.OTEL_SERVICE_NAME`` ("creatorclip").
            Pass "creatorclip-web" from main.py and "creatorclip-worker" from
            celery_app.py so Grafana Cloud separates the two signal streams.

    (Issue 326; Grafana Cloud OTLP/HTTP; opentelemetry-sdk 1.43.0)
    """
    global _otel_initialized
    if _otel_initialized:
        return

    # Lazy import so config is not pulled in at module level.
    from config import settings

    endpoint = settings.OTEL_EXPORTER_OTLP_ENDPOINT
    if not endpoint:
        return  # OTel disabled — strict no-op, no imports, no side effects.

    # Mark as initialised *before* the heavy imports so a re-entrant call from a
    # signal handler (unlikely but possible) doesn't race past the guard.
    _otel_initialized = True

    import logging as _logging
    import os as _os

    from opentelemetry import metrics as _metrics
    from opentelemetry import trace as _trace
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

    _svc = service_name or settings.OTEL_SERVICE_NAME
    resource = Resource(attributes={SERVICE_NAME: _svc})
    headers = _parse_otlp_headers(settings.OTEL_EXPORTER_OTLP_HEADERS)

    # ── Traces ────────────────────────────────────────────────────────────────
    # ParentBased sampler: respects the parent context (so cross-service
    # propagation works) and falls back to TraceIdRatioBased for root spans.
    sampler = ParentBased(root=TraceIdRatioBased(settings.OTEL_TRACES_SAMPLE_RATE))
    tracer_provider = TracerProvider(resource=resource, sampler=sampler)
    # OTLPSpanExporter auto-appends /v1/traces to the base endpoint.
    span_exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers)
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    _trace.set_tracer_provider(tracer_provider)

    # ── Metrics ───────────────────────────────────────────────────────────────
    # Push path only — prometheus-client stays for local /metrics scraping.
    # Do NOT create a second prometheus-client bridge here; that would
    # double-count. OTel metrics are independent spans/instruments.
    if settings.OTEL_METRICS_ENABLED:
        metric_exporter = OTLPMetricExporter(endpoint=endpoint, headers=headers)
        reader = PeriodicExportingMetricReader(metric_exporter)
        meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
        _metrics.set_meter_provider(meter_provider)

    # ── Logs (optional, default OFF) ─────────────────────────────────────────
    # The preferred logs path on Render is a syslog drain to Grafana Cloud
    # Loki (no code, see docs/RENDER_DEPLOY.md §12).  Enable this handler path
    # only when in-process log shipping is explicitly preferred.
    if settings.OTEL_LOGS_ENABLED:
        from opentelemetry._logs import set_logger_provider
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

        log_exporter = OTLPLogExporter(endpoint=endpoint, headers=headers)
        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
        set_logger_provider(logger_provider)
        _otel_log_handler = LoggingHandler(level=_logging.NOTSET, logger_provider=logger_provider)
        _logging.getLogger().addHandler(_otel_log_handler)

    # ── Auto-instrumentation ──────────────────────────────────────────────────
    from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
    from opentelemetry.instrumentation.celery import CeleryInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    CeleryInstrumentor().instrument()
    # Global SQLAlchemy patch — do NOT pass engine=... here.  The Celery worker
    # calls db.recreate_engine() in worker_process_init, producing a fresh engine
    # per-process.  The global patch intercepts every engine created after this
    # point, which is the correct behaviour for both the API and worker processes.
    SQLAlchemyInstrumentor().instrument()
    RedisInstrumentor().instrument()
    HTTPXClientInstrumentor().instrument()
    BotocoreInstrumentor().instrument()

    # Anthropic (OpenLLMetry) — the instrumentor reads TRACELOOP_TRACE_CONTENT
    # to decide whether to capture prompt/completion text in span attributes.
    # We MUST keep this OFF unconditionally: prompts can contain creator tokens
    # and personal data (PII/token no-leak boundary, docs/COMPLIANCE.md).
    _os.environ.setdefault("TRACELOOP_TRACE_CONTENT", "false")
    from opentelemetry.instrumentation.anthropic import AnthropicInstrumentor

    AnthropicInstrumentor().instrument()


def instrument_fastapi_app(app: Any) -> None:
    """Attach FastAPI auto-instrumentation to an already-created FastAPI app.

    Must be called AFTER ``app = FastAPI(...)`` and AFTER ``init_otel()``.  If
    OTel was not initialised (empty endpoint) this function is a no-op so it is
    safe to call unconditionally from main.py.

    Args:
        app: The FastAPI application instance.

    (Issue 326)
    """
    if not _otel_initialized:
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)


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
