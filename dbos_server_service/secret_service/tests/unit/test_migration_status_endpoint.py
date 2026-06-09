"""HTTP-эндпоинт `GET /internal/migration_status` — auth + happy path.

Тестируем гейт для ротационного скрипта: до lazy re-encrypt'а
`remaining_legacy > 0`, после — обнуляется и `migrated_pct == 100`.

Сборка app минималистична — только ops_router и envelope-handler, ровно
как в `test_internal_endpoints.py`.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.api.v1.endpoints.internal import ops_router
from src.core.config import get_settings
from src.core.exceptions import AppException
from src.dependencies.db import get_db
from src.repositories import credentials as repo
from src.services import secrets_service


_API_KEY = "migration-status-test-key"


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

    app.include_router(ops_router, prefix="/api/secret/v1")
    return app


@pytest_asyncio.fixture
async def http_client(adb, monkeypatch):
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


async def _make_cred(adb, cred_id: str, blob: str) -> None:
    await repo.create(
        adb,
        id=cred_id,
        name="mig_" + cred_id[-4:],
        service="jira",
        scope="personal",
        owner_user_id="usr_mig_owner",
        owner_dept_id=None,
        owner_user_dept_id="dep_mig",
        login=None,
        secret_encrypted=blob,
        status="active",
        created_by="usr_mig_owner",
    )


@pytest.mark.asyncio
async def test_migration_status_requires_bearer(http_client):
    resp = await http_client.get("/api/secret/v1/internal/migration_status")
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "INTERNAL_AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_migration_status_rejects_wrong_key(http_client):
    resp = await http_client.get(
        "/api/secret/v1/internal/migration_status",
        headers={"Authorization": "Bearer nope"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_migration_status_empty_table(http_client, adb):
    # Чистим credentials в текущем savepoint'е — другие тесты могли создать.
    await adb.execute(text("DELETE FROM credentials"))
    await adb.flush()

    resp = await http_client.get(
        "/api/secret/v1/internal/migration_status", headers=_HEADERS_OK,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["active_version"] == 2
    assert body["total_rows"] == 0
    assert body["by_version"] == {}
    assert body["remaining_legacy"] == 0
    assert body["migrated_pct"] == 100.0


@pytest.mark.asyncio
async def test_migration_status_counts_versions(http_client, adb, monkeypatch):
    """3 v2-row + 1 v3-row при активной v3: remaining_legacy=3, migrated_pct=25."""
    await adb.execute(text("DELETE FROM credentials"))
    await adb.flush()

    aad = secrets_service.aad_for_credential("dummy")  # AAD не валидируется в SELECT'е
    # Под текущей дефолтной v2 — 3 строки.
    for i in range(3):
        await _make_cred(adb, f"cred_mig_v2_{i}", secrets_service.encrypt("x", aad=aad))

    # Бампим активную версию до v3 (мастер тот же, доступен и под v2-legacy).
    import os as _os

    monkeypatch.setenv(
        "SECRET_ENCRYPTION_KEY__v2",
        _os.environ["SECRET_ENCRYPTION_KEY"],
    )
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY_VERSION", "3")
    get_settings.cache_clear()

    # 1 строка под v3.
    await _make_cred(adb, "cred_mig_v3_0", secrets_service.encrypt("x", aad=aad))
    await adb.flush()

    resp = await http_client.get(
        "/api/secret/v1/internal/migration_status", headers=_HEADERS_OK,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["active_version"] == 3
    assert body["total_rows"] == 4
    assert body["by_version"] == {"2": 3, "3": 1}
    assert body["remaining_legacy"] == 3
    assert body["migrated_pct"] == 25.0


@pytest.mark.asyncio
async def test_migration_status_all_migrated(http_client, adb):
    """Все строки на актуальной версии → remaining_legacy=0, pct=100."""
    await adb.execute(text("DELETE FROM credentials"))
    await adb.flush()
    aad = secrets_service.aad_for_credential("dummy")
    for i in range(2):
        await _make_cred(adb, f"cred_mig_done_{i}", secrets_service.encrypt("y", aad=aad))
    await adb.flush()

    resp = await http_client.get(
        "/api/secret/v1/internal/migration_status", headers=_HEADERS_OK,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["active_version"] == 2
    assert body["total_rows"] == 2
    assert body["by_version"] == {"2": 2}
    assert body["remaining_legacy"] == 0
    assert body["migrated_pct"] == 100.0
