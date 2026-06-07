"""HTTP-эндпоинты `/internal/lifecycle/*` — auth + happy path + handler error.

Собираем минимальное FastAPI-приложение здесь же (только internal-router +
exception-handler из main.py), чтобы тест не зависел от полного импорта
`src.main` — там потенциально могут жить эндпоинты с FastAPI-несовместимыми
комбинациями `status_code=204 + Body`, ломающие коллекцию.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from src.api.v1.endpoints.internal import router as internal_router
from src.core.config import get_settings
from src.core.exceptions import AppException
from src.dependencies.db import get_db
from src.services import lifecycle_service


_API_KEY = "internal-test-key-do-not-use-anywhere"


def _build_app() -> FastAPI:
    """Минимальный app: internal-роутер + наш envelope-handler."""
    app = FastAPI()

    @app.exception_handler(AppException)
    async def _h(request: Request, exc: AppException):
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "error": "internal_error",
                "error_code": exc.error_code,
                "message": exc.message,
                "details": exc.details,
                "request_id": None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    app.include_router(internal_router, prefix="/api/secret/v1")
    return app


@pytest_asyncio.fixture
async def http_client(adb, monkeypatch):
    # Settings — legacy single-key режим.
    get_settings.cache_clear()
    monkeypatch.setenv("SERVICE_API_KEY", _API_KEY)
    monkeypatch.delenv("SERVICE_API_KEYS", raising=False)

    app = _build_app()

    async def _get_db_override():
        yield adb

    app.dependency_overrides[get_db] = _get_db_override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
        ) as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


_HEADERS_OK = {"Authorization": f"Bearer {_API_KEY}"}


@pytest.mark.asyncio
async def test_user_deleted_requires_bearer(http_client):
    resp = await http_client.post(
        "/api/secret/v1/internal/lifecycle/user-deleted",
        json={"user_id": "usr_x", "actor_id": "usr_a", "actor_username": "a"},
    )
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "INTERNAL_AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_user_deleted_rejects_wrong_key(http_client):
    resp = await http_client.post(
        "/api/secret/v1/internal/lifecycle/user-deleted",
        json={"user_id": "usr_x", "actor_id": "usr_a", "actor_username": "a"},
        headers={"Authorization": "Bearer nope-not-the-key"},
    )
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "INTERNAL_AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_user_deleted_happy_path(http_client):
    """Без cred'ов — summary всё равно 200."""
    resp = await http_client.post(
        "/api/secret/v1/internal/lifecycle/user-deleted",
        json={"user_id": "usr_unknown01", "actor_id": "usr_admin01", "actor_username": "a"},
        headers=_HEADERS_OK,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["blocked_count"] == 0
    assert body["deleted_count"] == 0
    assert body["errors"] == []


@pytest.mark.asyncio
async def test_dept_deleted_happy_path(http_client):
    resp = await http_client.post(
        "/api/secret/v1/internal/lifecycle/dept-deleted",
        json={"dept_id": "dep_unknown01", "actor_id": "usr_admin01", "actor_username": "a"},
        headers=_HEADERS_OK,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["blocked_count"] == 0
    assert body["dept_grants_revoked"] == 0


@pytest.mark.asyncio
async def test_dept_service_access_revoked_other_service_noop(http_client):
    resp = await http_client.post(
        "/api/secret/v1/internal/lifecycle/dept-service-access-revoked",
        json={
            "dept_id": "dep_x01",
            "service": "logging_service",
            "actor_id": "usr_admin01",
            "actor_username": "a",
        },
        headers=_HEADERS_OK,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dept_grants_revoked"] == 0
    assert body["role_acls_revoked"] == 0


@pytest.mark.asyncio
async def test_handler_failure_returns_500(http_client, monkeypatch):
    async def _broken(*_a, **_k):
        return {"blocked_count": 0, "deleted_count": 0, "errors": ["simulated"]}

    monkeypatch.setattr(lifecycle_service, "handle_user_deleted", _broken)
    resp = await http_client.post(
        "/api/secret/v1/internal/lifecycle/user-deleted",
        json={"user_id": "usr_x", "actor_id": "usr_a", "actor_username": "a"},
        headers=_HEADERS_OK,
    )
    assert resp.status_code == 500
    body = resp.json()
    assert body["error_code"] == "LIFECYCLE_HANDLER_FAILED"
    assert "simulated" in str(body["details"])


@pytest.mark.asyncio
async def test_per_caller_key_requires_identity_header(http_client, monkeypatch):
    """Per-caller map включён — без X-Service-Identity 401, с — 200.

    pydantic-settings парсит env-значение для `dict`-field'а как JSON ДО
    нашего @field_validator, поэтому здесь шлём JSON-объект.
    """
    monkeypatch.setenv("SERVICE_API_KEYS", f'{{"auth_service": "{_API_KEY}"}}')
    get_settings.cache_clear()

    # Без идентити-хедера
    resp = await http_client.post(
        "/api/secret/v1/internal/lifecycle/user-deleted",
        json={"user_id": "usr_x", "actor_id": "usr_a", "actor_username": "a"},
        headers={"Authorization": f"Bearer {_API_KEY}"},
    )
    assert resp.status_code == 401

    # С правильным идентити
    resp_ok = await http_client.post(
        "/api/secret/v1/internal/lifecycle/user-deleted",
        json={"user_id": "usr_x", "actor_id": "usr_a", "actor_username": "a"},
        headers={
            "Authorization": f"Bearer {_API_KEY}",
            "X-Service-Identity": "auth_service",
        },
    )
    assert resp_ok.status_code == 200, resp_ok.text
