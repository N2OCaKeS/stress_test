"""AuditAccessMiddleware: 2xx → нет emit, 4xx → http.client_error, 5xx → http.server_error."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from src.middleware.audit_middleware import AuditAccessMiddleware


# Перебиваем `tests/unit/conftest.py::_create_schema` — БД не трогаем.
@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    yield


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(AuditAccessMiddleware)

    @app.get("/api/secret/v1/health")
    async def health():
        return {"ok": True}

    @app.get("/api/secret/v1/ok")
    async def ok():
        return {"ok": True}

    @app.get("/api/secret/v1/bad")
    async def bad():
        raise HTTPException(status_code=400, detail="nope")

    @app.get("/api/secret/v1/unauth")
    async def unauth():
        raise HTTPException(status_code=401, detail="nope")

    @app.get("/api/secret/v1/forbid")
    async def forbid():
        raise HTTPException(status_code=403, detail="nope")

    @app.get("/api/secret/v1/explode")
    async def explode():
        raise HTTPException(status_code=500, detail="boom")

    @app.get("/api/secret/v1/notimpl")
    async def notimpl():
        raise HTTPException(status_code=501, detail="stub")

    return app


async def _call(path: str, calls: list):
    """Запустить запрос с patched audit_service.emit, записывающим вызовы в `calls`."""
    app = _build_app()

    def fake_emit(action, *args, **kwargs):
        calls.append({"action": action, "status": kwargs.get("status"), "details": kwargs.get("details")})

    with patch("src.middleware.audit_middleware.audit_service.emit", side_effect=fake_emit):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            return await ac.get(path)


async def test_health_path_skipped():
    calls: list = []
    resp = await _call("/api/secret/v1/health", calls)
    assert resp.status_code == 200
    assert calls == []


async def test_2xx_does_not_emit():
    calls: list = []
    resp = await _call("/api/secret/v1/ok", calls)
    assert resp.status_code == 200
    assert calls == []


async def test_4xx_emits_http_client_error():
    calls: list = []
    resp = await _call("/api/secret/v1/bad", calls)
    assert resp.status_code == 400
    assert calls and calls[0]["action"] == "http.client_error"
    assert calls[0]["status"] == "failure"
    assert calls[0]["details"]["status_code"] == 400


async def test_401_emits_http_unauthorized():
    calls: list = []
    resp = await _call("/api/secret/v1/unauth", calls)
    assert resp.status_code == 401
    assert calls and calls[0]["action"] == "http.unauthorized"


async def test_403_emits_http_unauthorized():
    calls: list = []
    resp = await _call("/api/secret/v1/forbid", calls)
    assert resp.status_code == 403
    assert calls and calls[0]["action"] == "http.unauthorized"


async def test_5xx_emits_http_server_error():
    calls: list = []
    resp = await _call("/api/secret/v1/explode", calls)
    assert resp.status_code == 500
    assert calls and calls[0]["action"] == "http.server_error"


async def test_501_does_not_emit():
    """501 — заглушка endpoint'а, не действие юзера; не амплифицируем audit."""
    calls: list = []
    resp = await _call("/api/secret/v1/notimpl", calls)
    assert resp.status_code == 501
    assert calls == []
