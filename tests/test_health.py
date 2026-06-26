import main as main_module


def test_health_postgres_probe_uses_engine_not_raw_psycopg():
    """Regression: _check_postgres must route through the SQLAlchemy pool.

    The old implementation called psycopg.AsyncConnection.connect() directly,
    opening a fresh OS connection per k8s readiness/liveness probe × N replicas.
    Under load that churn defeats the PgBouncer sizing math (Issue 112, axis E).
    Removing the direct psycopg import is the structural proof the fix holds.
    """
    assert not hasattr(main_module, "psycopg"), (
        "main.py must not import psycopg at module scope — "
        "use engine.connect() for health probes, not psycopg.AsyncConnection.connect()"
    )


def test_health_redis_singleton_initialized(client):
    """The health-check Redis singleton is set once in lifespan startup.

    TestClient runs through the full lifespan, so _health_redis must be
    non-None by the time any /health call is served. A None here means the
    lifespan initialization was dropped, which would make every Redis probe
    return False regardless of Redis availability.
    """
    assert main_module._health_redis is not None


def test_health_returns_200(client):
    response = client.get("/health")
    assert response.status_code == 200


def test_health_response_shape(client):
    data = client.get("/health").json()
    assert "status" in data
    assert "postgres" in data
    assert "redis" in data
    assert "storage" in data  # Gap 5: object-storage reachability is now probed
    assert data["status"] in ("ok", "degraded")
    assert data["postgres"] in ("ok", "error")
    assert data["redis"] in ("ok", "error")
    assert data["storage"] in ("ok", "error")


def test_health_storage_ok_in_local_backend(client):
    """With STORAGE_BACKEND=local (the unit-test default) the storage probe is a
    no-op success — we never degrade a non-r2 box over object storage."""
    data = client.get("/health").json()
    assert data["storage"] == "ok"


def test_health_status_reflects_services(client):
    """Status is 'ok' only when every probed service reports 'ok'."""
    data = client.get("/health").json()
    if data["postgres"] == "ok" and data["redis"] == "ok" and data["storage"] == "ok":
        assert data["status"] == "ok"
    else:
        assert data["status"] == "degraded"
