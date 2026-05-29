import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import psycopg
import redis.asyncio as aioredis
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from config import settings
from limiter import limiter
from observability import (
    RequestIDMiddleware,
    configure_logging,
    metrics_response,
)
from routers import auth as auth_router
from routers import billing as billing_router
from routers import clips as clips_module
from routers import creators as creators_router
from routers import improvement as improvement_router
from routers import review as review_router
from routers import upload_intel as upload_intel_router
from routers import videos as videos_router

configure_logging(json_logs=settings.LOG_JSON)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("CreatorClip starting (env=%s)", settings.ENV)
    yield
    # Close the shared YouTube/Google HTTP client (Issue 72).
    from youtube import _http

    await _http.aclose()
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

app.state.limiter = limiter
# slowapi's handler is typed (Request, RateLimitExceeded) -> Response; Starlette wants
# (Request, Exception). RateLimitExceeded is an Exception subclass, so this is safe.
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
app.add_middleware(SlowAPIMiddleware)

app.include_router(auth_router.router)
app.include_router(billing_router.router)
app.include_router(creators_router.router)
app.include_router(videos_router.router)
app.include_router(clips_module.router)
app.include_router(clips_module.clips_router)
app.include_router(review_router.router)
app.include_router(upload_intel_router.router)
app.include_router(improvement_router.router)

_STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


# Clean, stable URLs for the legal pages — required for Google OAuth verification
# (the privacy policy URL must be public and discoverable) and friendlier than the
# raw /static path. (Tier-1 pre-launch.)
@app.get("/privacy", include_in_schema=False)
async def privacy() -> FileResponse:
    return FileResponse(_STATIC / "privacy.html")


@app.get("/terms", include_in_schema=False)
async def terms() -> FileResponse:
    return FileResponse(_STATIC / "tos.html")


app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Added last → outermost layer, so the correlation id is bound before any other
# middleware runs and the latency metric spans the whole request (Issue 75f).
app.add_middleware(
    RequestIDMiddleware,
    header=settings.REQUEST_ID_HEADER,
    metrics_enabled=settings.METRICS_ENABLED,
)


if settings.METRICS_ENABLED:

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        payload, content_type = metrics_response()
        return Response(content=payload, media_type=content_type)


def _pg_dsn() -> str:
    # psycopg3 expects postgresql://, not the SQLAlchemy postgresql+psycopg:// form
    return settings.DATABASE_URL.replace("postgresql+psycopg://", "postgresql://")


async def _check_postgres() -> bool:
    try:
        async with await psycopg.AsyncConnection.connect(_pg_dsn()) as conn:
            await conn.execute("SELECT 1")
        return True
    except Exception as exc:
        logger.warning("Postgres health check failed: %s", exc)
        return False


async def _check_redis() -> bool:
    try:
        r = aioredis.from_url(settings.REDIS_URL)
        await r.ping()
        await r.aclose()
        return True
    except Exception as exc:
        logger.warning("Redis health check failed: %s", exc)
        return False


@app.get("/health")
async def health() -> dict:
    postgres_ok, redis_ok = await asyncio.gather(_check_postgres(), _check_redis())
    return {
        "status": "ok" if (postgres_ok and redis_ok) else "degraded",
        "postgres": "ok" if postgres_ok else "error",
        "redis": "ok" if redis_ok else "error",
    }
