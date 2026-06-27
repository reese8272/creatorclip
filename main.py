import asyncio
import importlib.metadata
import logging
import re as _re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware as _BaseHTTPMiddleware
from starlette.responses import Response as _StarletteResponse

import event_log
from auth import check_not_cross_site
from config import settings
from db import engine
from limiter import limiter
from observability import (
    RequestIDMiddleware,
    collect_saturation_gauges,
    configure_logging,
    init_otel,
    init_sentry,
    instrument_fastapi_app,
    metrics_response,
    request_id_ctx,
)
from routers import activity as activity_router
from routers import analysis as analysis_router
from routers import api_keys as api_keys_router
from routers import auth as auth_router
from routers import billing as billing_router
from routers import chat as chat_router
from routers import clips as clips_module
from routers import creators as creators_router
from routers import export as export_router
from routers import improvement as improvement_router
from routers import insights as insights_router
from routers import logs as logs_router
from routers import notifications as notifications_router
from routers import publications as publications_router
from routers import review as review_router
from routers import tasks as tasks_router
from routers import thumbnails as thumbnails_router
from routers import titles as titles_router
from routers import upload_intel as upload_intel_router
from routers import videos as videos_router

configure_logging(
    json_logs=settings.LOG_JSON, level=settings.log_level_int, log_dir=settings.LOG_DIR
)
init_sentry(
    dsn=settings.SENTRY_DSN,
    environment=settings.sentry_environment,
    release=settings.IMAGE_SHA,
)
# OTel SDK: no-op when OTEL_EXPORTER_OTLP_ENDPOINT is unset (dev/CI safe).
# FastAPI span instrumentation is applied below after `app = FastAPI(...)`.
init_otel(service_name="creatorclip-web")
logger = logging.getLogger(__name__)

# Issue 297: CalVer version from pyproject.toml [project].version via stdlib
# importlib.metadata — no new dependency, available since Python 3.8+.
# Falls back to "dev" in environments where the package is not installed
# (e.g. running `python main.py` directly without `pip install -e .`).
try:
    __version__ = importlib.metadata.version("creatorclip")
except importlib.metadata.PackageNotFoundError:
    __version__ = "dev"

# Module-level singleton for /health Redis probes. Initialized in lifespan so
# every probe reuses the same connection pool instead of calling from_url() and
# opening a fresh pool on each k8s readiness/liveness tick (axis-E SEV2).
_health_redis: aioredis.Redis | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _health_redis
    logger.info("CreatorClip starting (env=%s)", settings.ENV)
    _health_redis = aioredis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        socket_timeout=2.0,
        socket_connect_timeout=2.0,
    )
    yield
    # Close the shared YouTube/Google HTTP client (Issue 72).
    from youtube import _http

    await _http.aclose()
    # Close the Issue-86 progress Redis client cleanly (no Event-loop-is-closed
    # warnings at shutdown; releases the connection pool).
    from worker import progress

    await progress.aclose()
    # Close the health-check Redis singleton.
    if _health_redis is not None:
        await _health_redis.aclose()
    # Close the event-log sink pool (Issue 151).
    await event_log.dispose()
    logger.info("CreatorClip shutdown")


app = FastAPI(
    title="CreatorClip",
    version="0.1.0",
    description=(
        "The only AI editor that truly knows your channel — "
        "it learns your style from your own analytics, adapts as you evolve, "
        "and keeps you ahead of the algorithm. "
        "CreatorClip predicts fit with your style and audience — "
        "it does not promise virality."
    ),
    docs_url="/docs" if settings.ENV == "development" else None,
    redoc_url=None,
    lifespan=lifespan,
)

# OTel FastAPI instrumentation must happen after the app object is created.
# No-op when OTEL_EXPORTER_OTLP_ENDPOINT is unset.
instrument_fastapi_app(app)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]  # SDK/stub typing lag (Issue 78c)
app.add_middleware(SlowAPIMiddleware)

# ── CSRF Fetch-Metadata dependency (Issue 230) ───────────────────────────────
# Applied globally — check_not_cross_site() no-ops on safe methods (GET/HEAD),
# API-key (Authorization: Bearer) paths, non-browser clients (absent header),
# and when CSRF_FETCH_METADATA_ENABLED=False (test/dev default).
app.router.dependencies.append(Depends(check_not_cross_site))

app.include_router(activity_router.router)
app.include_router(auth_router.router)
app.include_router(api_keys_router.router)
app.include_router(analysis_router.router)
app.include_router(billing_router.router)
app.include_router(creators_router.router)
app.include_router(videos_router.router)
app.include_router(clips_module.router)
app.include_router(clips_module.clips_router)
app.include_router(publications_router.router)
app.include_router(review_router.router)
app.include_router(upload_intel_router.router)
app.include_router(improvement_router.router)
app.include_router(export_router.router)
app.include_router(insights_router.router)
app.include_router(logs_router.router)
app.include_router(chat_router.router)
app.include_router(thumbnails_router.router)
app.include_router(titles_router.router)
app.include_router(tasks_router.router)
app.include_router(notifications_router.router)
app.include_router(notifications_router.unsubscribe_router)

_STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC), name="static")

# ── React SPA (incremental migration → cutover — docs/DECISIONS.md 2026-06-17/18)
# The Vite build (frontend/dist, base=/app/) is served under /app/*: hashed
# assets via the StaticFiles mount; every other /app path returns the SPA shell
# so React Router (basename=/app) owns client routing.
#
# Issue 85g soft cutover: once the bundle is built, `/` REDIRECTS to the SPA
# (`/app/dashboard`) — the React app is now the primary surface. A fresh checkout
# with no build still boots on the legacy index, so dev/CI without a frontend
# build is unaffected (the same `_SPA_BUILT` gate used since adoption). The
# legacy `static/*.html` pages remain served (now unlinked) as rollback
# insurance; full retirement is a staging-verified follow-up.
_SPA_DIST = Path(__file__).parent / "frontend" / "dist"
_SPA_INDEX = _SPA_DIST / "index.html"
_SPA_BUILT = _SPA_INDEX.is_file()


@app.get("/", include_in_schema=False)
async def index() -> Response:
    if _SPA_BUILT:
        return RedirectResponse(url="/app/dashboard", status_code=302)
    # Legacy static/index.html has been retired (Issue 226: XSS surface removal).
    # When the SPA bundle is not built (dev/CI without a Node build), return 404
    # rather than serving the now-absent legacy page.
    raise HTTPException(status_code=404, detail="Not found")


if _SPA_BUILT:
    app.mount("/app/assets", StaticFiles(directory=_SPA_DIST / "assets"), name="spa-assets")

    @app.get("/app", include_in_schema=False)
    @app.get("/app/{spa_path:path}", include_in_schema=False)
    async def spa(spa_path: str = "") -> FileResponse:
        # Serve real build artifacts that live at the SPA root — public/ assets
        # copied into dist/ by Vite (chip/*.png, favicon, robots.txt). Without
        # this the catch-all returned index.html for every non-/assets path, so
        # those files came back as HTML and rendered as broken images. Client
        # routes (e.g. "dashboard") aren't files, so they fall through to the
        # SPA shell. The candidate is confined to _SPA_DIST to block traversal.
        if spa_path:
            candidate = (_SPA_DIST / spa_path).resolve()
            if candidate.is_file() and candidate.is_relative_to(_SPA_DIST.resolve()):
                return FileResponse(candidate)
        return FileResponse(_SPA_INDEX)
else:  # pragma: no cover - only hit when the SPA bundle has not been built
    logger.warning("SPA bundle not found at %s; /app routes disabled until built", _SPA_DIST)


# ── Cache-busting middleware (2026-06-07 follow-up to Issue 136) ───────
#
# Cloudflare aggressively caches `/static/*.css` for hours, so a CSS-only
# UI deploy invisibly serves the old stylesheet long after the container
# rolls. Append `?v=<STATIC_VERSION>` to every static reference in served
# HTML so each deploy invalidates the CDN automatically (CSS path changes
# → Cloudflare treats it as a new asset).
#
# Rewrites only `text/html` responses; existing `?v=` references are left
# alone so a future build pipeline can opt out of the rewrite for a
# specific asset by hard-coding its own version.
_STATIC_CACHEBUST_RE = _re.compile(rb'((?:href|src)=")(/static/[^"?]+\.(?:css|js))(")')


def _rewrite_static(body: bytes, version: str) -> bytes:
    suffix = f"?v={version}".encode()
    return _STATIC_CACHEBUST_RE.sub(lambda m: m.group(1) + m.group(2) + suffix + m.group(3), body)


class StaticCacheBustMiddleware(_BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        ctype = response.headers.get("content-type", "")
        # Only rewrite full HTML responses; skip 304s (empty body) and non-HTML.
        if not ctype.startswith("text/html") or response.status_code == 304:
            return response
        chunks: list[bytes] = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        body = b"".join(chunks)
        body = _rewrite_static(body, settings.STATIC_VERSION)
        headers = dict(response.headers)
        headers.pop("content-length", None)
        # Strip ETag/Last-Modified so browsers cannot use conditional GETs to
        # bypass the middleware with a 304 that serves stale HTML (and therefore
        # stale ?v= version strings pointing at CDN-cached old CSS/JS).
        # Cache-Control: no-store prevents the browser from caching HTML at all,
        # so every navigation fetches a fresh copy and the rewrite always runs.
        headers.pop("etag", None)
        headers.pop("last-modified", None)
        headers["cache-control"] = "no-store"
        return _StarletteResponse(
            content=body,
            status_code=response.status_code,
            headers=headers,
            media_type=ctype,
        )


# ── Security-headers middleware (Issue 229 — OWASP Secure Headers Project) ───
#
# Appends baseline security headers to every response. Registered BEFORE
# StaticCacheBustMiddleware so it runs as the inner layer — its headers are set
# first on the response, then CacheBust (outer) only pops content-length/etag/
# last-modified, leaving the security headers intact.
#
# CSP uses frame-ancestors 'none' for structural clickjacking defence (supersedes
# X-Frame-Options for supporting browsers; both are set for defence-in-depth per
# OWASP). HSTS is only emitted in production to avoid breaking non-TLS dev hosts.
# CSP_EXTRA_SOURCES appends additional allowed origins for CDN fonts/analytics.
#
# Source: https://owasp.org/www-project-secure-headers/

_CSP_BASE = (
    "default-src 'self'; "
    "form-action 'self'; "
    "base-uri 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "upgrade-insecure-requests"
)


def _build_csp() -> str:
    """Return the Content-Security-Policy value, optionally extended with CSP_EXTRA_SOURCES."""
    extra = settings.CSP_EXTRA_SOURCES.strip()
    if extra:
        return f"{_CSP_BASE}; {extra}"
    return _CSP_BASE


class SecurityHeadersMiddleware(_BaseHTTPMiddleware):
    """Appends OWASP Secure Headers Project baseline to every response.

    HSTS is only emitted in production (ENV=production) to avoid breaking
    non-TLS dev/staging hosts.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = _build_csp()
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if settings.ENV == "production":
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(StaticCacheBustMiddleware)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Backend request telemetry (Issue 151) ────────────────────────────────────
# One event_logs row per real request — the "what was done" half of the
# click→action trail. Registered before RequestIDMiddleware so that (being
# inner) the correlation id is already bound when it runs; the creator id is
# read after call_next, by which point the auth dependency has stashed it on
# request.state. Static assets, the SPA shell, health/metrics probes, and the
# activity/logs endpoints themselves are skipped as noise/recursion. record_event
# is best-effort (never raises); it is awaited so reads are read-after-write
# consistent — a high-throughput async queue is the documented scale path.
_LOG_SKIP_PREFIXES = (
    "/static",
    "/app",
    "/metrics",
    "/health",
    "/favicon",
    "/api/activity",
    "/api/logs",
)


@app.middleware("http")
async def _log_request_events(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    path = request.url.path
    if request.method != "OPTIONS" and not path.startswith(_LOG_SKIP_PREFIXES):
        status = response.status_code
        # request.state.creator_id is set only when the endpoint ran the
        # get_current_creator dependency; fall back to the signed cookie so
        # http_request events on routes that don't depend on auth are still
        # attributed to the logged-in creator (Issue 151 fix).
        from auth import creator_id_from_cookie

        creator_id = getattr(request.state, "creator_id", None) or creator_id_from_cookie(request)
        await event_log.record_event(
            source="backend",
            event="http_request",
            creator_id=creator_id,
            level="error" if status >= 500 else "warn" if status >= 400 else "info",
            request_id=request_id_ctx.get(),
            page=path,
            target=request.method,
            status_code=status,
            duration_ms=int((time.perf_counter() - start) * 1000),
        )
    return response


# Added last → outermost layer, so the correlation id is bound before any other
# middleware runs and the latency metric spans the whole request (Issue 75f).
app.add_middleware(
    RequestIDMiddleware,
    header=settings.REQUEST_ID_HEADER,
    metrics_enabled=settings.METRICS_ENABLED,
)


if settings.METRICS_ENABLED:

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request) -> Response:
        # Gate the scrape surface behind a bearer token when configured (required in
        # production via config fail-fast). Empty token = unauthenticated, for dev or
        # an internal-only network. (Issue 76)
        token = settings.METRICS_TOKEN
        if token:
            auth = request.headers.get("authorization", "")
            if not secrets.compare_digest(auth, f"Bearer {token}"):
                raise HTTPException(status_code=401, detail="Unauthorized")
        # Collect saturation gauges before rendering the scrape payload (Issue 238).
        # Reuses the existing module-level engine + _health_redis singleton — zero
        # new connections. On any error the gauge retains its last value.
        if _health_redis is not None:
            await collect_saturation_gauges(engine, _health_redis)
        payload, content_type = metrics_response()
        return Response(content=payload, media_type=content_type)


async def _check_postgres() -> bool:
    # Probe via the SQLAlchemy pool (engine.connect) so /health stays inside
    # the pre-warmed connection pool. The old psycopg.AsyncConnection.connect
    # path opened a fresh connection per k8s probe × N replicas — sustained
    # churn outside the pool that defeats the PgBouncer sizing math (Issue 112).
    try:
        async with asyncio.timeout(2.0):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning("Postgres health check failed: %s", exc)
        return False


async def _check_redis() -> bool:
    # Reuse the module-level singleton; from_url() on every probe creates a
    # new pool each call — same axis-E churn as the old Postgres path.
    if _health_redis is None:
        return False
    try:
        async with asyncio.timeout(2.0):
            await _health_redis.ping()
        return True
    except Exception as exc:
        logger.warning("Redis health check failed: %s", exc)
        return False


async def _check_storage() -> bool:
    """Probe object storage reachability.

    With STORAGE_BACKEND=local (dev) there's nothing remote to reach, so the
    check is a no-op success — we never degrade a dev box over storage. With
    STORAGE_BACKEND=r2 a misconfigured/unreachable bucket would otherwise stay
    invisible until a creator's upload silently FAILs in the worker pipeline;
    a HEAD on the bucket surfaces it at /health instead (Gap 5). boto3 is sync,
    so the HEAD runs in a worker thread under a hard timeout.
    """
    if settings.STORAGE_BACKEND != "r2":
        return True
    try:
        from worker.storage import _r2

        async with asyncio.timeout(3.0):
            await asyncio.to_thread(_r2().head_bucket, Bucket=settings.R2_BUCKET)
        return True
    except Exception as exc:
        logger.warning("Storage (R2) health check failed: %s", exc)
        return False


@app.get("/health")
async def health() -> dict:
    postgres_ok, redis_ok, storage_ok = await asyncio.gather(
        _check_postgres(), _check_redis(), _check_storage()
    )
    return {
        "status": "ok" if (postgres_ok and redis_ok and storage_ok) else "degraded",
        "postgres": "ok" if postgres_ok else "error",
        "redis": "ok" if redis_ok else "error",
        "storage": "ok" if storage_ok else "error",
        # Issue 297: expose the running CalVer so `curl /health` answers
        # "what version is live" — the standard observability touchpoint for
        # incident triage and rollback targeting.
        "version": __version__,
    }
