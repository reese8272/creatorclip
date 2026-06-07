import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import text

from config import settings
from db import engine
from limiter import limiter
from observability import (
    RequestIDMiddleware,
    configure_logging,
    metrics_response,
)
from routers import activity as activity_router
from routers import analysis as analysis_router
from routers import api_keys as api_keys_router
from routers import auth as auth_router
from routers import billing as billing_router
from routers import clips as clips_module
from routers import creators as creators_router
from routers import improvement as improvement_router
from routers import insights as insights_router
from routers import review as review_router
from routers import tasks as tasks_router
from routers import titles as titles_router
from routers import upload_intel as upload_intel_router
from routers import videos as videos_router

configure_logging(json_logs=settings.LOG_JSON, log_dir=settings.LOG_DIR)
logger = logging.getLogger(__name__)

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
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]  # SDK/stub typing lag (Issue 78c)
app.add_middleware(SlowAPIMiddleware)

app.include_router(activity_router.router)
app.include_router(auth_router.router)
app.include_router(api_keys_router.router)
app.include_router(analysis_router.router)
app.include_router(billing_router.router)
app.include_router(creators_router.router)
app.include_router(videos_router.router)
app.include_router(clips_module.router)
app.include_router(clips_module.clips_router)
app.include_router(review_router.router)
app.include_router(upload_intel_router.router)
app.include_router(improvement_router.router)
app.include_router(insights_router.router)
app.include_router(titles_router.router)
app.include_router(tasks_router.router)

_STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


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
    async def metrics(request: Request) -> Response:
        # Gate the scrape surface behind a bearer token when configured (required in
        # production via config fail-fast). Empty token = unauthenticated, for dev or
        # an internal-only network. (Issue 76)
        token = settings.METRICS_TOKEN
        if token:
            auth = request.headers.get("authorization", "")
            if not secrets.compare_digest(auth, f"Bearer {token}"):
                raise HTTPException(status_code=401, detail="Unauthorized")
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


@app.get("/health")
async def health() -> dict:
    postgres_ok, redis_ok = await asyncio.gather(_check_postgres(), _check_redis())
    return {
        "status": "ok" if (postgres_ok and redis_ok) else "degraded",
        "postgres": "ok" if postgres_ok else "error",
        "redis": "ok" if redis_ok else "error",
    }
