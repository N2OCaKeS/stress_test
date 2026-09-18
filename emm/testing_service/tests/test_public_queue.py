"""Публичный запуск и retry: допуск, идемпотентность, контекст и СТП."""

import asyncio
import uuid
import pytest
from sqlalchemy import delete
from src.db.session import AsyncSessionLocal
from src.models import TestDefinition as Definition, StpCell, StpTestRun, StpTestCase
from tests.conftest import auth_hdr
from tests.test_queue import (
    _create_stand,
    _create_test_def,
    _get_item,
    LAUNCH_CTX,
    mock_server_service as mock_server_service,
    recorded_calls as recorded_calls,
    configure_internal_keys as configure_internal_keys,
    mock_git_token as mock_git_token,
)
from tests.test_test_runs import _drive_to_success

BASE = "/api/testing/v1/queue-items"


def body(test_id, stand_id, **overrides):
    return {
        "request_id": uuid.uuid4().hex,
        "test_id": test_id,
        "stand_id": stand_id,
        "os_version_id": LAUNCH_CTX["RC"],
        "kernel": LAUNCH_CTX["KERNEL"],
        "debug_mode": True,
        **overrides,
    }


async def seed_stp(test_id, stand_id, rc=LAUNCH_CTX["RC"], mode=None):
    async with AsyncSessionLocal() as db:
        test = await db.get(Definition, test_id)
        case = StpTestCase(
            id=f"case_{uuid.uuid4().hex}",
            code=test.code,
            title="Test",
            zephyr_id="CASE-1",
            created_by="usr_public_queue_test",
        )
        db.add(case)
        run = StpTestRun(
            id=f"stpr_{uuid.uuid4().hex}",
            stand_id=stand_id,
            os_version_id=rc,
            kernel=LAUNCH_CTX["KERNEL"],
            mode=mode or test.mode,
        )
        db.add(run)
        await db.flush()
        cell = StpCell(
            id=f"cell_{uuid.uuid4().hex}",
            stp_test_case_id=case.id,
            stp_test_run_id=run.id,
            status="not_run",
        )
        db.add(cell)
        await db.commit()
        return run.id, cell.id


async def test_concurrent_launch_is_idempotent(
    client, admin_token, mock_server_service, recorded_calls
):
    mock_server_service()
    stand_id, _ = await _create_stand(client, admin_token)
    test_id = await _create_test_def(client, admin_token, stand_id)
    payload = body(test_id, stand_id)
    first, second = await asyncio.gather(
        *[
            client.post(BASE, headers=auth_hdr(admin_token), json=payload)
            for _ in range(2)
        ]
    )
    assert first.status_code == second.status_code == 201, (first.text, second.text)
    assert first.json()["id"] == second.json()["id"]
    assert sum(path.endswith("/prepare-for-test") for _, path in recorded_calls) == 1
    response = await client.post(
        BASE, headers=auth_hdr(admin_token), json={**payload, "kernel": "different"}
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "REQUEST_ID_CONFLICT"


async def test_launch_rejects_other_department_and_missing_role(
    client, admin_token, no_role_token, make_token, mock_server_service
):
    mock_server_service()
    stand_id, _ = await _create_stand(client, admin_token)
    test_id = await _create_test_def(client, admin_token, stand_id)
    other = make_token(
        department_id="dep_other", service_roles={"testing_service": ["admin"]}
    )
    for token in (other, no_role_token):
        response = await client.post(
            BASE, headers=auth_hdr(token), json=body(test_id, stand_id)
        )
        assert response.status_code == 403
    response = await client.post(BASE, json=body(test_id, stand_id))
    assert response.status_code == 401


async def test_normal_requires_stp_and_does_not_allow_context_injection(
    client, admin_token, mock_server_service
):
    mock_server_service()
    stand_id, _ = await _create_stand(client, admin_token)
    test_id = await _create_test_def(client, admin_token, stand_id)
    payload = body(test_id, stand_id, debug_mode=False)
    response = await client.post(BASE, headers=auth_hdr(admin_token), json=payload)
    assert response.status_code == 422
    # СТП для этого стенда/РЦ/ядра/режима ещё не генерировалась вовсе —
    # добавлять некуда, это отличается от "СТП есть, теста в ней нет" (§E2).
    assert response.json()["error_code"] == "STP_RUN_NOT_FOUND"
    response = await client.post(
        BASE,
        headers=auth_hdr(admin_token),
        json={**payload, "test_run_id": "run_injected"},
    )
    assert response.status_code == 422
    await seed_stp(test_id, stand_id)
    response = await client.post(BASE, headers=auth_hdr(admin_token), json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["test_run_id"] is None
    assert "launch_context" not in response.json()
    assert "creds_stash_key" not in response.json()


async def test_retry_preserves_context_and_rejects_fork(
    client, admin_token, mock_server_service, configure_internal_keys, mock_git_token
):
    mock_server_service()
    await mock_git_token()
    stand_id, _ = await _create_stand(client, admin_token)
    test_id = await _create_test_def(client, admin_token, stand_id)
    response = await client.post(
        BASE, headers=auth_hdr(admin_token), json=body(test_id, stand_id)
    )
    item = await _get_item(response.json()["id"])
    endpoint = f"{BASE}/{item.id}/retry"
    response = await client.post(
        endpoint, headers=auth_hdr(admin_token), json={"request_id": uuid.uuid4().hex}
    )
    assert response.status_code == 409
    await _drive_to_success(client, item)
    payload = {"request_id": uuid.uuid4().hex}
    responses = await asyncio.gather(
        *[
            client.post(endpoint, headers=auth_hdr(admin_token), json=payload)
            for _ in range(2)
        ]
    )
    assert [r.status_code for r in responses] == [201, 201]
    assert responses[0].json()["id"] == responses[1].json()["id"]
    retry = await _get_item(responses[0].json()["id"])
    assert retry.retry_of_id == item.id
    assert retry.launch_context == item.launch_context
    assert retry.stand_id == item.stand_id and retry.debug_mode
    response = await client.post(
        endpoint, headers=auth_hdr(admin_token), json={"request_id": uuid.uuid4().hex}
    )
    assert response.status_code == 409


@pytest.mark.parametrize("debug", [False, True])
async def test_stp_result_is_updated_only_for_normal_launch(
    client,
    admin_token,
    mock_server_service,
    configure_internal_keys,
    mock_git_token,
    debug,
):
    mock_server_service()
    await mock_git_token()
    stand_id, _ = await _create_stand(client, admin_token)
    test_id = await _create_test_def(client, admin_token, stand_id)
    _, cell_id = await seed_stp(test_id, stand_id)
    response = await client.post(
        BASE,
        headers=auth_hdr(admin_token),
        json=body(test_id, stand_id, debug_mode=debug),
    )
    assert response.status_code == 201, response.text
    await _drive_to_success(client, await _get_item(response.json()["id"]))
    async with AsyncSessionLocal() as db:
        cell = await db.get(StpCell, cell_id)
        assert cell.status == ("not_run" if debug else "pass")
        assert (cell.queue_item_id is None) == debug


async def test_not_in_stp_carries_existing_run_id_for_add_to_stp_prompt(
    client, admin_token, mock_server_service
):
    """Случай (a) §E2: СТП для этого контекста уже сгенерирована (для другого
    теста того же стенда), просто наш тест в неё не входит — `details.
    stp_test_run_id` должен указывать на неё, чтобы UI знал, куда добавлять."""
    mock_server_service()
    stand_id, _ = await _create_stand(client, admin_token)
    test_with_stp = await _create_test_def(client, admin_token, stand_id)
    test_without_stp = await _create_test_def(client, admin_token, stand_id)
    run_id, _cell_id = await seed_stp(test_with_stp, stand_id)

    response = await client.post(
        BASE, headers=auth_hdr(admin_token),
        json=body(test_without_stp, stand_id, debug_mode=False),
    )
    assert response.status_code == 422
    payload = response.json()
    assert payload["error_code"] == "TEST_NOT_IN_STP"
    assert payload["details"]["stp_test_run_id"] == run_id


async def test_removed_stp_test_is_not_given_to_worker(
    client, admin_token, mock_server_service, configure_internal_keys
):
    from tests.test_readiness import make_ready
    from tests.test_queue import QUEUE_BASE, WORKER_SECRET, _server_hdr

    mock_server_service()
    stand_id, _ = await _create_stand(client, admin_token)
    test_id = await _create_test_def(client, admin_token, stand_id)
    _, cell_id = await seed_stp(test_id, stand_id)
    response = await client.post(
        BASE,
        headers=auth_hdr(admin_token),
        json=body(test_id, stand_id, debug_mode=False),
    )
    item = await _get_item(response.json()["id"])
    await make_ready(client, item)
    async with AsyncSessionLocal() as db:
        await db.execute(delete(StpCell).where(StpCell.id == cell_id))
        await db.commit()
    response = await client.post(
        f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET)
    )
    assert response.json()["item"] is None
    assert (await _get_item(item.id)).error.startswith("TEST_NOT_IN_STP")


async def test_list_separates_campaigns_and_departments(
    client, admin_token, make_token, mock_server_service
):
    mock_server_service()
    stand_id, _ = await _create_stand(client, admin_token)
    test_id = await _create_test_def(client, admin_token, stand_id)
    await client.post(BASE, headers=auth_hdr(admin_token), json=body(test_id, stand_id))
    from tests.test_campaign_attempts import create_run

    await create_run(client, admin_token, [stand_id])
    for kind in ("standalone", "campaign"):
        response = await client.get(
            BASE, headers=auth_hdr(admin_token), params={"kind": kind}
        )
        assert response.status_code == 200
        assert response.json()["total"] == 1
        assert (response.json()["items"][0]["test_run_id"] is None) == (
            kind == "standalone"
        )
    other = make_token(
        department_id="dep_other", service_roles={"testing_service": ["admin"]}
    )
    response = await client.get(BASE, headers=auth_hdr(other), params={"kind": "all"})
    assert response.json()["total"] == 0


async def test_guest_reads_department_queue_items(
    client, admin_token, guest_token, mock_server_service
):
    """Список очереди отдела не требует роли admin — читает и guest.

    Важно для колокола уведомлений: он опрашивает эту ручку от лица любого
    сотрудника отдела, не только тех, у кого есть права на управление
    очередью.
    """
    mock_server_service()
    stand_id, _ = await _create_stand(client, admin_token)
    test_id = await _create_test_def(client, admin_token, stand_id)
    await client.post(BASE, headers=auth_hdr(admin_token), json=body(test_id, stand_id))

    response = await client.get(BASE, headers=auth_hdr(guest_token), params={"kind": "all"})
    assert response.status_code == 200
    assert response.json()["total"] == 1


async def test_launch_and_retry_use_the_tests_own_mode(
    client, admin_token, mock_server_service, configure_internal_keys, mock_git_token
):
    """MODE в launch_context — свойство теста, не параметр запроса (§ mode_switch)."""
    mock_server_service()
    await mock_git_token()
    stand_id, _ = await _create_stand(client, admin_token)
    test_id = await _create_test_def(client, admin_token, stand_id, mode="smolensk")
    await seed_stp(test_id, stand_id)

    response = await client.post(
        BASE, headers=auth_hdr(admin_token), json=body(test_id, stand_id, debug_mode=False)
    )
    assert response.status_code == 201, response.text
    item = await _get_item(response.json()["id"])
    assert item.launch_context["MODE"] == "smolensk"

    await _drive_to_success(client, item)
    retry = await client.post(
        f"{BASE}/{item.id}/retry",
        headers=auth_hdr(admin_token),
        json={"request_id": uuid.uuid4().hex},
    )
    assert retry.status_code == 201, retry.text
    retry_item = await _get_item(retry.json()["id"])
    assert retry_item.launch_context["MODE"] == "smolensk"


async def test_campaign_retry_keeps_campaign_and_saved_stand(
    client, admin_token, mock_server_service, configure_internal_keys, mock_git_token
):
    from tests.test_campaign_attempts import create_run, detail
    from src.models import TestRun

    mock_server_service()
    await mock_git_token()
    stand_id, _ = await _create_stand(client, admin_token)
    test_id = await _create_test_def(client, admin_token, stand_id)
    await seed_stp(test_id, stand_id, rc="osv_1.8.5")
    run_id = await create_run(client, admin_token, [stand_id])
    data = await detail(client, admin_token, run_id)
    source = await _get_item(data["queue_items"][0]["queue_item_id"])
    await _drive_to_success(client, source)
    async with AsyncSessionLocal() as db:
        test = await db.get(Definition, test_id)
        test.pinned_stand_id = None
        await db.commit()
    response = await client.post(
        f"{BASE}/{source.id}/retry",
        headers=auth_hdr(admin_token),
        json={"request_id": uuid.uuid4().hex},
    )
    assert response.status_code == 201, response.text
    retry = await _get_item(response.json()["id"])
    assert retry.stand_id == source.stand_id
    assert retry.test_run_id == run_id
    assert retry.test_run_entry_id == source.test_run_entry_id
    async with AsyncSessionLocal() as db:
        assert (await db.get(TestRun, run_id)).status == "running"
