"""Smoke tests for the health/ready endpoints — verify the test stack itself works."""

import pytest


@pytest.mark.asyncio
async def test_health_ok(client):
    response = await client.get("/api/server/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "timestamp" in body


@pytest.mark.asyncio
async def test_ready_pings_database(client):
    response = await client.get("/api/server/v1/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"


@pytest.mark.asyncio
async def test_unknown_endpoint_returns_404(client):
    response = await client.get("/api/server/v1/does-not-exist")
    assert response.status_code == 404
