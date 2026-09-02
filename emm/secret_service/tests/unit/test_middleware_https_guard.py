"""HTTPSRequiredMiddleware: cleartext-HTTP в prod → 403, dev → пропуск."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.middleware.https_guard import HTTPSRequiredMiddleware


# Перебиваем `tests/unit/conftest.py::_create_schema` — БД не трогаем.
@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    yield


def _build_app(*, app_env: str) -> FastAPI:
    app = FastAPI()
    app.add_middleware(HTTPSRequiredMiddleware, app_env=app_env)

    @app.get("/api/secret/v1/health")
    async def health():
        return {"ok": True}

    @app.get("/api/secret/v1/credentials")
    async def listing():
        return {"items": []}

    return app


async def test_production_cleartext_http_rejected():
    app = _build_app(app_env="production")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/secret/v1/credentials")
    assert resp.status_code == 403
    body = resp.json()
    assert body["error_code"] == "HTTPS_REQUIRED"


async def test_production_health_bypasses_https_guard():
    app = _build_app(app_env="production")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/secret/v1/health")
    assert resp.status_code == 200


async def test_production_x_forwarded_proto_https_passes():
    app = _build_app(app_env="production")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/secret/v1/credentials",
            headers={"X-Forwarded-Proto": "https"},
        )
    assert resp.status_code == 200


async def test_production_xfp_last_token_wins():
    """`https, http` → последний = http → reject."""
    app = _build_app(app_env="production")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/secret/v1/credentials",
            headers={"X-Forwarded-Proto": "https, http"},
        )
    assert resp.status_code == 403


async def test_staging_also_enforces_https():
    app = _build_app(app_env="staging")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/secret/v1/credentials")
    assert resp.status_code == 403


async def test_dev_passes_cleartext():
    app = _build_app(app_env="dev")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/secret/v1/credentials")
    assert resp.status_code == 200


async def test_local_passes_cleartext():
    app = _build_app(app_env="local")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/secret/v1/credentials")
    assert resp.status_code == 200


async def test_test_env_passes_cleartext():
    app = _build_app(app_env="test")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/secret/v1/credentials")
    assert resp.status_code == 200
