"""Допуск по ручному статусу каталога и смена статуса ожидающего теста."""

import pytest
from sqlalchemy import select

from src.core.exceptions import DomainValidationError
from src.db.session import AsyncSessionLocal
from src.models import QueueItem
from src.repositories import stp_cell as stp_cell_repo
from src.repositories import stp_test_case as stp_test_case_repo
from src.repositories import stp_test_run as stp_test_run_repo
from src.services import creds_stash, queue as queue_svc
from src.utils.ids import stp_cell_id, stp_test_case_id, stp_test_run_id
from tests.conftest import auth_hdr
from tests.test_queue import (
    CALLBACK_BASE,
    QUEUE_BASE,
    SERVER_SECRET,
    TESTS_BASE,
    WORKER_SECRET,
    LAUNCH_CTX,
    _create_stand,
    _create_test_def,
    _get_item,
    _identity,
    _server_hdr,
    configure_internal_keys as configure_internal_keys,
    mock_git_token as mock_git_token,
    mock_server_service as mock_server_service,
    recorded_calls as recorded_calls,
)
from tests.test_test_runs import _create_test_def_with_known_code


async def change_status(client, token, test_id, readiness):
    response = await client.patch(
        f"{TESTS_BASE}/{test_id}",
        headers=auth_hdr(token),
        json={"readiness": readiness},
    )
    assert response.status_code == 200, response.text


async def make_ready(client, item):
    response = await client.post(
        f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
        headers=_server_hdr("server_service", SERVER_SECRET),
        json={
            "correlation_id": item.id,
            "succeeded": True,
            "test_username": "u",
            "test_password": "p",
            "test_ssh_private_key": "key",
        },
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("readiness", ["ready", "review", "broken", "development"])
@pytest.mark.parametrize("debug", [False, True])
async def test_launch_admission(
    client, admin_token, mock_server_service, recorded_calls, readiness, debug
):
    mock_server_service()
    stand_id, _ = await _create_stand(client, admin_token)
    test_id = await _create_test_def(client, admin_token, stand_id)
    await change_status(client, admin_token, test_id, readiness)
    recorded_calls.clear()
    async with AsyncSessionLocal() as db:
        if readiness != "ready" and not debug:
            with pytest.raises(DomainValidationError) as error:
                await queue_svc.enqueue(
                    db, _identity(), test_id, launch_context=LAUNCH_CTX
                )
            assert error.value.error_code == "TEST_REQUIRES_DEBUG"
            assert not recorded_calls
            assert not (await db.execute(select(QueueItem))).scalars().all()
        else:
            item = await queue_svc.enqueue(
                db,
                _identity(),
                test_id,
                launch_context=LAUNCH_CTX,
                debug_mode=debug,
                stand_id=stand_id,
            )
            assert item.state == "preparing"


@pytest.mark.parametrize("status", [None, "draft", "blocked", "typo"])
async def test_invalid_status_rejected(client, admin_token, status):
    response = await client.post(
        TESTS_BASE,
        headers=auth_hdr(admin_token),
        json={"code": "status.invalid", "full_name": "Invalid", "readiness": status},
    )
    assert response.status_code == 422
    test_id = await _create_test_def(client, admin_token, None)
    response = await client.patch(
        f"{TESTS_BASE}/{test_id}",
        headers=auth_hdr(admin_token),
        json={"readiness": status},
    )
    assert response.status_code == 422


async def test_new_test_defaults_to_development(client, admin_token):
    response = await client.post(
        TESTS_BASE,
        headers=auth_hdr(admin_token),
        json={"code": "status.new", "full_name": "New"},
    )
    assert response.status_code == 201
    assert response.json()["readiness"] == "development"
    response = await client.patch(
        f"{TESTS_BASE}/{response.json()['id']}",
        headers=auth_hdr(admin_token),
        json={"full_name": "Renamed"},
    )
    assert response.status_code == 200
    assert response.json()["readiness"] == "development"


async def test_status_changed_before_claim_skips_test_without_retry(
    client, admin_token, mock_server_service, configure_internal_keys, recorded_calls
):
    mock_server_service()
    stand_id, _ = await _create_stand(client, admin_token)
    test_id = await _create_test_def(client, admin_token, stand_id)
    async with AsyncSessionLocal() as db:
        item = await queue_svc.enqueue(
            db, _identity(), test_id, launch_context=LAUNCH_CTX
        )
    await make_ready(client, item)
    ready = await _get_item(item.id)
    await change_status(client, admin_token, test_id, "review")
    response = await client.post(
        f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET)
    )
    assert response.status_code == 200
    assert response.json()["item"] is None
    rejected = await _get_item(item.id)
    assert rejected.state == "failed"
    assert rejected.failed_step == "launch_guard"
    assert rejected.started_at is None
    assert await creds_stash.pop_creds(ready.creds_stash_key) is None
    async with AsyncSessionLocal() as db:
        assert len((await db.execute(select(QueueItem))).scalars().all()) == 1
    assert any(path.endswith("/release-for-service") for _, path in recorded_calls)


async def test_changed_queued_test_does_not_restore_and_next_test_continues(
    client,
    admin_token,
    mock_server_service,
    configure_internal_keys,
    mock_git_token,
    recorded_calls,
):
    mock_server_service()
    await mock_git_token()
    stand_id, _ = await _create_stand(client, admin_token)
    ids = [await _create_test_def(client, admin_token, stand_id) for _ in range(3)]
    items = []
    for test_id in ids:
        async with AsyncSessionLocal() as db:
            items.append(
                await queue_svc.enqueue(
                    db, _identity(), test_id, launch_context=LAUNCH_CTX
                )
            )
    await change_status(client, admin_token, ids[1], "broken")
    await make_ready(client, items[0])
    claim = await client.post(
        f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET)
    )
    assert claim.json()["item"]["queue_item_id"] == items[0].id
    recorded_calls.clear()
    response = await client.post(
        f"{QUEUE_BASE}/{items[0].id}/completed",
        headers=_server_hdr("testing_worker", WORKER_SECRET),
        json={"succeeded": True, "exit_code": 0},
    )
    assert response.status_code == 200
    assert (await _get_item(items[1].id)).failed_step == "launch_guard"
    assert (await _get_item(items[2].id)).state == "preparing"
    assert sum(path.endswith("/prepare-for-test") for _, path in recorded_calls) == 1


async def test_failure_does_not_change_catalog_status(
    client, admin_token, mock_server_service, configure_internal_keys, mock_git_token
):
    mock_server_service()
    await mock_git_token()
    stand_id, _ = await _create_stand(client, admin_token)
    test_id = await _create_test_def(client, admin_token, stand_id)
    async with AsyncSessionLocal() as db:
        item = await queue_svc.enqueue(
            db, _identity(), test_id, launch_context=LAUNCH_CTX
        )
    await make_ready(client, item)
    await client.post(
        f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET)
    )
    response = await client.post(
        f"{QUEUE_BASE}/{item.id}/completed",
        headers=_server_hdr("testing_worker", WORKER_SECRET),
        json={"succeeded": False, "exit_code": 1},
    )
    assert response.status_code == 200
    assert (await _get_item(item.id)).state == "failed"
    response = await client.get(
        f"{TESTS_BASE}/{test_id}", headers=auth_hdr(admin_token)
    )
    assert response.json()["readiness"] == "ready"


async def test_campaign_reports_non_working_test_as_enqueue_error(
    client, admin_token, mock_server_service
):
    # Не debug — нужен реальный readiness-гейт, поэтому тест заранее заведён в
    # СТП этого контекста: иначе он упал бы на STP-гейте раньше, чем дойдёт
    # до проверки readiness внутри `queue.enqueue`.
    mock_server_service()
    stand_id, _ = await _create_stand(client, admin_token)
    test_id, code = await _create_test_def_with_known_code(client, admin_token, stand_id)
    await change_status(client, admin_token, test_id, "development")
    async with AsyncSessionLocal() as db:
        case = await stp_test_case_repo.create(db, {
            "id": stp_test_case_id(), "code": code, "title": code, "zephyr_id": f"BT-{code}",
        })
        run = await stp_test_run_repo.create(db, {
            "id": stp_test_run_id(), "os_version_id": "osv_1",
            "mode": "orel", "kernel": "6.1.0", "stand_id": stand_id,
            "zephyr_test_run_key": f"BT-R-{code}", "zephyr_folder_path": "/stress_test",
        })
        await stp_cell_repo.create(db, {
            "id": stp_cell_id(), "stp_test_case_id": case.id, "stp_test_run_id": run.id,
        })
        await db.commit()
    response = await client.post(
        "/api/testing/v1/test-runs",
        headers=auth_hdr(admin_token),
        json={
            "os_version_id": "osv_1",
            "kernel": "6.1.0",
            "test_run_stands": [stand_id],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["enqueue_errors"][0]["error_code"] == "TEST_REQUIRES_DEBUG"
    async with AsyncSessionLocal() as db:
        assert not (await db.execute(select(QueueItem))).scalars().all()
