"""Прогон всех ядер и сохранение истории ротированного лога."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from src.db.session import AsyncSessionLocal
from src.models import QueueItem, TestLog as Log
from src.services import server_client, test_log, log_rotation
from tests.conftest import auth_hdr
from tests.test_queue import (
    _create_stand, _create_test_def,
    mock_server_service as mock_server_service, recorded_calls as recorded_calls,
)


async def test_campaign_uses_every_catalog_kernel(client, admin_token, mock_server_service, monkeypatch):
    mock_server_service()
    stand, _ = await _create_stand(client, admin_token)
    await _create_test_def(client, admin_token, stand)
    monkeypatch.setattr(server_client, "get_os_version", AsyncMock(return_value={"kernels": ["6.1.1-1-generic", "6.1.1-1-lowlatency"]}))
    response = await client.post("/api/testing/v1/test-runs", headers=auth_hdr(admin_token), json={"os_version_id": "osv_test", "test_run_stands": [stand], "debug": True})
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["kernels"] == ["6.1.1-1-generic", "6.1.1-1-lowlatency"]
    detail = (await client.get(f"/api/testing/v1/test-runs/{run['id']}", headers=auth_hdr(admin_token))).json()
    assert len(detail["entries"]) == len(detail["queue_items"]) == 2
    assert {entry["kernel"] for entry in detail["entries"]} == set(run["kernels"])
    assert {item["kernel"] for item in detail["queue_items"]} == set(run["kernels"])
    assert detail["progress"]["total"] == 2


async def test_rotated_log_keeps_attempt_result(client, admin_token, mock_server_service):
    mock_server_service()
    stand, _ = await _create_stand(client, admin_token)
    test_id = await _create_test_def(client, admin_token, stand)
    async with AsyncSessionLocal() as db:
        item = QueueItem(id="qi_rotation_history", stand_id=stand, test_id=test_id, state="succeeded", launch_context={"RC": "os1", "KERNEL": "k1"}, created_by="usr_test")
        db.add(item)
        await db.commit()
        log = await test_log.append_chunk(db, item.id, "Русский лог")
        log.created_at = datetime.now(timezone.utc) - timedelta(days=90)
        log.protected = False
        await db.commit()
    endpoint = "/api/testing/v1/queue-items"
    params = {"attempt_id": "qi_rotation_history", "os_version_id": "os1", "kernel": "k1"}
    before = (await client.get(endpoint, headers=auth_hdr(admin_token), params=params)).json()
    assert before["items"][0]["log_status"] == "available"
    async with AsyncSessionLocal() as db:
        assert await log_rotation.enforce_monthly_retention(db, 30) == 1
        assert await db.get(Log, log.id) is None
    after = (await client.get(endpoint, headers=auth_hdr(admin_token), params=params)).json()
    assert after["items"][0]["state"] == "succeeded"
    assert after["items"][0]["log_status"] == "rotated"
    assert after["total"] == 1


async def test_stp_only_os_discovers_kernels_and_all_modes(
    client, admin_token, dept_a, mock_server_service, monkeypatch,
):
    """Без явного `mode` /stp/generate обходит оба режима — но каждый тест
    попадает только в прогон своего собственного режима (§ mode fixed on test)."""
    from tests.test_stp import _create_test_def_for_dept, _seed_integration_settings, _seed_stp_test_case
    from src.services import stp, secret_client, zephyr_client
    mock_server_service()
    stand, _ = await _create_stand(client, admin_token, department_id=dept_a)
    _, code_orel = await _create_test_def_for_dept(client, admin_token, stand, dept_a)
    _, code_smolensk = await _create_test_def_for_dept(client, admin_token, stand, dept_a, mode="smolensk")
    await _seed_integration_settings(dept_a)
    await _seed_stp_test_case(code_orel, zephyr_id="BT-T1")
    await _seed_stp_test_case(code_smolensk, zephyr_id="BT-T2")
    discover = AsyncMock(return_value=["6.1.1-1-generic", "6.1.1-1-lowlatency"])
    monkeypatch.setattr(server_client, "resolve_os_kernels", discover)
    monkeypatch.setattr(server_client, "get_os_version", AsyncMock(return_value={"name": "1.7.1.44"}))
    monkeypatch.setattr(stp, "_filter_by_changelog", AsyncMock(side_effect=lambda db, tests, rc, scope: tests))
    monkeypatch.setattr(secret_client, "reveal_credential", AsyncMock(return_value=("test_user", "test_token")))
    create = AsyncMock(side_effect=["BT-R1", "BT-R2", "BT-R3", "BT-R4"])
    monkeypatch.setattr(zephyr_client, "create_test_run", create)
    monkeypatch.setattr(zephyr_client, "resolve_user_key", AsyncMock(return_value="test_user"))
    response = await client.post(
        "/api/testing/v1/stp/generate", headers=auth_hdr(admin_token),
        json={"os_version_id": "1.7.1.44", "scope": "full"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["errors"] == []
    assert {(run["kernel"], run["mode"]) for run in response.json()["test_runs"]} == {
        (kernel, mode) for kernel in discover.return_value for mode in ["orel", "smolensk"]
    }
    assert create.await_count == 4
    discover.assert_awaited_once_with("1.7.1.44")
