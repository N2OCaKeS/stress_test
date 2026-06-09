"""HTTP-эндпоинты `/internal/reencrypt_outbox/*` — auth + flow.

Минимальный FastAPI-app, чтобы не зависеть от полного импорта `src.main`.
Проверяем:

* 401 без bearer'а / на чужом ключе;
* 200 с валидным bearer'ом для всех трёх endpoint'ов;
* end-to-end seed → process → status переводит legacy row в done.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.api.v1.endpoints.secrets_migration import router as outbox_router
from src.core.config import get_settings
from src.core.exceptions import AppException
from src.dependencies.db import get_db
from src.repositories import credentials as repo
from src.services import secrets_service


_API_KEY = "reencrypt-outbox-test-key"


def _build_app() -> FastAPI:
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

    app.include_router(outbox_router, prefix="/api/secret/v1")
    return app


@pytest_asyncio.fixture
async def http_client(adb, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("SERVICE_API_KEY", _API_KEY)
    monkeypatch.delenv("SERVICE_API_KEYS", raising=False)

    # Чистим обе таблицы — savepoint в `adb` откатит.
    await adb.execute(text("DELETE FROM reencrypt_outbox_entries"))
    await adb.execute(text("DELETE FROM credentials"))
    await adb.flush()

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


_HEADERS_OK = {
    "Authorization": f"Bearer {_API_KEY}",
    "X-Service-Identity": "rotation_runner",
}


async def _make_cred(adb, cred_id: str) -> None:
    aad = secrets_service.aad_for_credential(cred_id)
    blob = secrets_service.encrypt("payload-" + cred_id[-4:], aad=aad)
    await repo.create(
        adb,
        id=cred_id,
        name="ep_" + cred_id[-4:],
        service="jira",
        scope="personal",
        owner_user_id="usr_ep_owner",
        owner_dept_id=None,
        owner_user_dept_id="dep_ep",
        login=None,
        secret_encrypted=blob,
        status="active",
        created_by="usr_ep_owner",
    )


def _bump_to_v3(monkeypatch) -> None:
    monkeypatch.setenv(
        "SECRET_ENCRYPTION_KEY__v2",
        os.environ["SECRET_ENCRYPTION_KEY"],
    )
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY_VERSION", "3")
    get_settings.cache_clear()  # type: ignore[attr-defined]


# ── auth ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_seed_requires_bearer(http_client):
    resp = await http_client.post("/api/secret/v1/internal/reencrypt_outbox/seed")
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "INTERNAL_AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_process_rejects_wrong_key(http_client):
    resp = await http_client.post(
        "/api/secret/v1/internal/reencrypt_outbox/process",
        headers={"Authorization": "Bearer nope-not-the-key"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_status_requires_bearer(http_client):
    resp = await http_client.get("/api/secret/v1/internal/reencrypt_outbox/status")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_per_caller_map_requires_identity_header(http_client, monkeypatch):
    """SERVICE_API_KEYS включён — без X-Service-Identity 401, с — 200."""
    monkeypatch.setenv(
        "SERVICE_API_KEYS",
        f'{{"rotation_runner": "{_API_KEY}"}}',
    )
    get_settings.cache_clear()  # type: ignore[attr-defined]

    # Без identity-хедера
    resp = await http_client.get(
        "/api/secret/v1/internal/reencrypt_outbox/status",
        headers={"Authorization": f"Bearer {_API_KEY}"},
    )
    assert resp.status_code == 401

    # С правильным
    resp_ok = await http_client.get(
        "/api/secret/v1/internal/reencrypt_outbox/status",
        headers=_HEADERS_OK,
    )
    assert resp_ok.status_code == 200


# ── flow ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_empty(http_client):
    resp = await http_client.get(
        "/api/secret/v1/internal/reencrypt_outbox/status",
        headers=_HEADERS_OK,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"pending": 0, "done": 0, "error": 0, "total": 0}


@pytest.mark.asyncio
async def test_seed_then_status(http_client, adb, monkeypatch):
    for i in range(2):
        await _make_cred(adb, f"cred_endpoint_seed_{i}")
    await adb.flush()
    _bump_to_v3(monkeypatch)

    resp = await http_client.post(
        "/api/secret/v1/internal/reencrypt_outbox/seed", headers=_HEADERS_OK,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["inserted"] == 2
    assert body["scanned"] == 2
    assert body["active_version"] == 3

    resp2 = await http_client.get(
        "/api/secret/v1/internal/reencrypt_outbox/status", headers=_HEADERS_OK,
    )
    assert resp2.status_code == 200
    assert resp2.json()["pending"] == 2


@pytest.mark.asyncio
async def test_full_flow_seed_process_status(http_client, adb, monkeypatch):
    """End-to-end: 3 credential под v2 → bump → seed → process → done=3."""
    for i in range(3):
        await _make_cred(adb, f"cred_endpoint_flow_{i}")
    await adb.flush()
    _bump_to_v3(monkeypatch)

    seed_resp = await http_client.post(
        "/api/secret/v1/internal/reencrypt_outbox/seed", headers=_HEADERS_OK,
    )
    assert seed_resp.status_code == 200
    assert seed_resp.json()["inserted"] == 3

    proc_resp = await http_client.post(
        "/api/secret/v1/internal/reencrypt_outbox/process",
        headers=_HEADERS_OK,
        params={"batch_size": 100},
    )
    assert proc_resp.status_code == 200, proc_resp.text
    proc_body = proc_resp.json()
    assert proc_body["processed"] == 3
    assert proc_body["errors"] == 0

    status_resp = await http_client.get(
        "/api/secret/v1/internal/reencrypt_outbox/status", headers=_HEADERS_OK,
    )
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["done"] == 3
    assert body["pending"] == 0
    assert body["total"] == 3


@pytest.mark.asyncio
async def test_process_query_clamps_batch_size(http_client):
    """batch_size > 1000 валидируется на уровне Query."""
    resp = await http_client.post(
        "/api/secret/v1/internal/reencrypt_outbox/process",
        headers=_HEADERS_OK,
        params={"batch_size": 5000},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_process_with_explicit_target_version_body(http_client, adb, monkeypatch):
    """seed принимает target_version в теле — overrides settings."""
    await _make_cred(adb, "cred_endpoint_tv")
    await adb.flush()
    _bump_to_v3(monkeypatch)

    resp = await http_client.post(
        "/api/secret/v1/internal/reencrypt_outbox/seed",
        headers=_HEADERS_OK,
        json={"target_version": 3},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["active_version"] == 3
    assert body["inserted"] == 1
