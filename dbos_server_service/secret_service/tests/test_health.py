"""Smoke tests для /health и /ready — проверяем, что скелет сервиса собран."""

import pytest


@pytest.mark.asyncio
async def test_health_ok(client):
    response = await client.get("/api/secret/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "timestamp" in body


@pytest.mark.asyncio
async def test_ready_pings_database(client):
    response = await client.get("/api/secret/v1/ready")
    assert response.status_code == 200
    body = response.json()
    # БД мокается в conftest._mock_db_connect, SELECT 1 + COUNT'ы возвращают
    # MagicMock'и; счётчики падают в except → degraded + zeros.
    assert body["status"] in {"ok", "degraded"}
    assert isinstance(body["db"], bool)
    assert "secrets_total" in body
    assert "blocked_total" in body
    assert "redis_connected" in body
    assert "audit_dropped_429_total" in body


@pytest.mark.asyncio
async def test_unknown_endpoint_returns_404(client):
    response = await client.get("/api/secret/v1/does-not-exist")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_security_headers_present(client):
    response = await client.get("/api/secret/v1/health")
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert "X-Request-ID" in response.headers


@pytest.mark.asyncio
async def test_request_id_echoed_back(client):
    response = await client.get(
        "/api/secret/v1/health",
        headers={"X-Request-ID": "req_test_12345"},
    )
    assert response.headers["X-Request-ID"] == "req_test_12345"
