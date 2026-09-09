"""Smoke tests для /health и /ready — проверяем, что скелет сервиса собран.

Гоняются против реальных Postgres+Redis (`tests/docker-compose.test.yml`),
без моков — это единственный способ реально подтвердить, что сервис
собирается и стартует, а не просто импортируется.
"""

import pytest


@pytest.mark.asyncio
async def test_health_ok(client):
    response = await client.get("/api/testing/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "timestamp" in body


@pytest.mark.asyncio
async def test_ready_pings_database_and_redis(client):
    response = await client.get("/api/testing/v1/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    assert body["db"] is True
    assert body["redis_connected"] is True
    assert "audit_dropped_429_total" in body


@pytest.mark.asyncio
async def test_unknown_endpoint_returns_404(client):
    response = await client.get("/api/testing/v1/does-not-exist")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_security_headers_present(client):
    response = await client.get("/api/testing/v1/health")
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert (
        response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    )
    csp = response.headers.get("Content-Security-Policy", "")
    assert "frame-ancestors 'none'" in csp
    assert "default-src 'none'" in csp
    assert "X-Request-ID" in response.headers


@pytest.mark.asyncio
async def test_request_id_echoed_back(client):
    response = await client.get(
        "/api/testing/v1/health",
        headers={"X-Request-ID": "req_test_12345"},
    )
    assert response.headers["X-Request-ID"] == "req_test_12345"
