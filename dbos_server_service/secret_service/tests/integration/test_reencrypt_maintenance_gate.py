"""Maintenance-gate: 503 REENCRYPT_IN_PROGRESS во время force-окна.

Проверяем через реальный app + middleware:

* force активен → произвольный endpoint отдаёт 503 с error_code
  REENCRYPT_IN_PROGRESS, заголовком Retry-After и телом
  {retry_after, eta_seconds, remaining};
* /health и /ready в force-окне доступны (k8s probe нельзя ронять);
* status-эндпоинт (admin migration_status) в force-окне доступен и показывает
  force_active/mode;
* после снятия force запросы снова проходят.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from src.db.session import AsyncSessionLocal
from src.services import reencrypt_state_service
from tests.integration.conftest import auth_header


_CREDS_PATH = "/api/secret/v1/credentials"
_HEALTH_PATH = "/api/secret/v1/health"
_READY_PATH = "/api/secret/v1/ready"
_STATUS_PATH = "/api/secret/v1/admin/encryption/migration_status"


@pytest_asyncio.fixture(autouse=True)
async def _reset_force_state():
    """Гейт-флаг живёт в singleton-строке, которую clean_db не трогает —
    снимаем force после каждого теста, чтобы не залочить остальную сьюту."""
    yield
    async with AsyncSessionLocal() as session:
        await reencrypt_state_service.clear_force(session)
    reencrypt_state_service.invalidate_cache()


async def _enter_force(db, *, remaining: int) -> None:
    await reencrypt_state_service.enter_force(db, remaining=remaining)
    reencrypt_state_service.invalidate_cache()


@pytest.mark.asyncio
async def test_force_blocks_regular_request(client, db, identity_factory):
    await _enter_force(db, remaining=100)
    token = identity_factory(department_id="dep_gate")

    resp = await client.get(_CREDS_PATH, headers=auth_header(token))
    assert resp.status_code == 503
    body = resp.json()
    assert body["error_code"] == "REENCRYPT_IN_PROGRESS"
    assert "Retry-After" in resp.headers
    assert int(resp.headers["Retry-After"]) >= 15
    assert body["retry_after"] >= 15
    assert body["remaining"] == 100
    assert body["eta_seconds"] >= 0


@pytest.mark.asyncio
async def test_health_and_ready_bypass_gate(client, db):
    await _enter_force(db, remaining=100)
    assert (await client.get(_HEALTH_PATH)).status_code == 200
    assert (await client.get(_READY_PATH)).status_code == 200


@pytest.mark.asyncio
async def test_status_endpoint_bypasses_gate(client, db, identity_factory):
    await _enter_force(db, remaining=100)
    token = identity_factory(platform_role="account_admin")

    resp = await client.get(_STATUS_PATH, headers=auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["force_active"] is True
    assert body["mode"] == "force"


@pytest.mark.asyncio
async def test_gate_lifts_after_force_cleared(client, db, identity_factory):
    await _enter_force(db, remaining=100)
    token = identity_factory(department_id="dep_gate")
    assert (await client.get(_CREDS_PATH, headers=auth_header(token))).status_code == 503

    async with AsyncSessionLocal() as session:
        await reencrypt_state_service.clear_force(session)
    reencrypt_state_service.invalidate_cache()

    # Уже не 503 (реальный код зависит от прав, но gate пропускает).
    resp = await client.get(_CREDS_PATH, headers=auth_header(token))
    assert resp.status_code != 503
