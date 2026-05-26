def test_health_returns_200(client):
    response = client.get("/health")
    assert response.status_code == 200


def test_health_response_shape(client):
    data = client.get("/health").json()
    assert "status" in data
    assert "postgres" in data
    assert "redis" in data
    assert data["status"] in ("ok", "degraded")
    assert data["postgres"] in ("ok", "error")
    assert data["redis"] in ("ok", "error")


def test_health_status_reflects_services(client):
    """Status is 'ok' only when both services report 'ok'."""
    data = client.get("/health").json()
    if data["postgres"] == "ok" and data["redis"] == "ok":
        assert data["status"] == "ok"
    else:
        assert data["status"] == "degraded"
