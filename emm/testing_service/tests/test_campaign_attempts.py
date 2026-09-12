"""Состав кампании, повторные попытки и миграция старой очереди."""

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import select
from src.db.session import AsyncSessionLocal
from src.models import QueueItem
from tests.conftest import TEST_DATABASE_URL, auth_hdr
from tests.test_queue import (
    CALLBACK_BASE,
    SERVER_SECRET,
    TESTS_BASE,
    _create_stand,
    _create_test_def,
    _get_item,
    _server_hdr,
    configure_internal_keys as configure_internal_keys,
    mock_git_token as mock_git_token,
    mock_server_service as mock_server_service,
    recorded_calls as recorded_calls,
)
from tests.test_test_runs import BASE, _payload, _drive_to_success


async def create_run(client, token, stands):
    response = await client.post(BASE, headers=auth_hdr(token), json=_payload(stands))
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def detail(client, token, run_id):
    response = await client.get(f"{BASE}/{run_id}", headers=auth_hdr(token))
    assert response.status_code == 200, response.text
    return response.json()


async def test_retry_success_replaces_failure_but_preserves_attempt(
    client, admin_token, mock_server_service, configure_internal_keys, mock_git_token
):
    mock_server_service()
    await mock_git_token()
    stand_id, _ = await _create_stand(client, admin_token)
    await _create_test_def(client, admin_token, stand_id)
    run_id = await create_run(client, admin_token, [stand_id])
    initial = await detail(client, admin_token, run_id)
    item = await _get_item(initial["queue_items"][0]["queue_item_id"])
    response = await client.post(
        f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
        headers=_server_hdr("server_service", SERVER_SECRET),
        json={
            "correlation_id": item.id,
            "succeeded": False,
            "failed_step": "restore",
            "error": "temporary failure",
        },
    )
    assert response.status_code == 200, response.text
    waiting = await detail(client, admin_token, run_id)
    assert waiting["progress"] == {"total": 1, "attempts": 2, "preparing": 1}
    current = next(
        attempt for attempt in waiting["queue_items"] if attempt["is_current"]
    )
    retry = await _get_item(current["queue_item_id"])
    assert retry.retry_of_id == item.id
    assert retry.test_run_id == item.test_run_id
    assert retry.test_run_entry_id == item.test_run_entry_id
    assert retry.launch_context == item.launch_context
    await _drive_to_success(client, retry)
    finished = await detail(client, admin_token, run_id)
    assert finished["status"] == "succeeded"
    assert finished["progress"] == {"total": 1, "attempts": 2, "succeeded": 1}
    previous = next(
        attempt for attempt in finished["queue_items"] if not attempt["is_current"]
    )
    assert previous["state"] == "failed"
    assert len(finished["entries"]) == 1


async def test_manifest_survives_catalog_edits_and_deduplicates_stands(
    client, admin_token, mock_server_service
):
    mock_server_service()
    stand_id, _ = await _create_stand(client, admin_token)
    test_id = await _create_test_def(client, admin_token, stand_id)
    run_id = await create_run(client, admin_token, [stand_id, stand_id])
    initial = await detail(client, admin_token, run_id)
    response = await client.patch(
        f"{TESTS_BASE}/{test_id}",
        headers=auth_hdr(admin_token),
        json={"full_name": "Changed later", "pinned_stand_id": None},
    )
    assert response.status_code == 200
    await _create_test_def(client, admin_token, stand_id)
    after = await detail(client, admin_token, run_id)
    assert after["test_run_stands"] == [stand_id]
    assert after["composition_source"] == "pinned_catalog"
    assert after["entries"] == initial["entries"]
    assert len(after["queue_items"]) == 1
    assert after["entries"][0]["test_name"] != "Changed later"


async def test_enqueue_error_remains_in_composition_and_prevents_false_success(
    client, admin_token, mock_server_service, configure_internal_keys, mock_git_token
):
    mock_server_service()
    await mock_git_token()
    stand_id, _ = await _create_stand(client, admin_token)
    await _create_test_def(client, admin_token, stand_id)
    broken = await _create_test_def(client, admin_token, stand_id)
    response = await client.patch(
        f"{TESTS_BASE}/{broken}",
        headers=auth_hdr(admin_token),
        json={"readiness": "broken"},
    )
    assert response.status_code == 200
    run_id = await create_run(client, admin_token, [stand_id])
    initial = await detail(client, admin_token, run_id)
    assert len(initial["entries"]) == 2
    assert initial["progress"]["total"] == 2
    assert initial["progress"]["failed"] == 1
    assert (
        next(entry for entry in initial["entries"] if entry["test_id"] == broken)[
            "enqueue_error_code"
        ]
        == "TEST_REQUIRES_DEBUG"
    )
    await _drive_to_success(
        client, await _get_item(initial["queue_items"][0]["queue_item_id"])
    )
    finished = await detail(client, admin_token, run_id)
    assert finished["status"] == "partially_failed"
    assert finished["progress"] == {
        "total": 2,
        "attempts": 1,
        "succeeded": 1,
        "failed": 1,
    }


async def test_migration_preserves_independent_roots_and_retry_context(
    client, admin_token, mock_server_service
):
    mock_server_service()
    stand_id, _ = await _create_stand(client, admin_token)
    await _create_test_def(client, admin_token, stand_id)
    run_id = await create_run(client, admin_token, [stand_id])
    initial = await detail(client, admin_token, run_id)
    first_id = initial["queue_items"][0]["queue_item_id"]
    async with AsyncSessionLocal() as db:
        first = await db.get(QueueItem, first_id)
        first.state = "failed"
        data = dict(
            stand_id=first.stand_id,
            test_id=first.test_id,
            launch_context=first.launch_context,
            test_run_id=run_id,
            state="succeeded",
            created_by=first.created_by,
        )
        db.add(
            QueueItem(
                id="qi_migration_retry", retry_of_id=first.id, is_retry=True, **data
            )
        )
        db.add(QueueItem(id="qi_migration_independent", **data))
        await db.commit()

    def migrate(*args):
        subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL, "PYTHONPATH": "."},
            check=True,
            capture_output=True,
        )

    migrate("downgrade", "c8a4f2d913e0")
    try:
        migrate("upgrade", "head")
        after = await detail(client, admin_token, run_id)
        assert after["composition_source"] == "legacy_queue"
        assert after["status"] == "succeeded"
        assert len(after["entries"]) == 2
        assert after["progress"] == {"total": 2, "attempts": 3, "succeeded": 2}
        async with AsyncSessionLocal() as db:
            items = {
                item.id: item
                for item in (await db.execute(select(QueueItem))).scalars()
            }
        assert (
            items[first_id].test_run_entry_id
            == items["qi_migration_retry"].test_run_entry_id
        )
        assert (
            items[first_id].test_run_entry_id
            != items["qi_migration_independent"].test_run_entry_id
        )
    finally:
        migrate("upgrade", "head")


async def test_stand_is_frozen_before_enqueue(
    client, admin_token, mock_server_service, monkeypatch
):
    from sqlalchemy import update
    from src.models import TestDefinition
    from src.services import queue as queue_svc

    mock_server_service()
    stand_id, _ = await _create_stand(client, admin_token)
    test_id = await _create_test_def(client, admin_token, stand_id)
    original_enqueue = queue_svc.enqueue

    async def edit_then_enqueue(db, identity, queued_test_id, **kwargs):
        await db.execute(
            update(TestDefinition)
            .where(TestDefinition.id == test_id)
            .values(pinned_stand_id=None, full_name="Changed during scheduling")
        )
        await db.commit()
        return await original_enqueue(db, identity, queued_test_id, **kwargs)

    monkeypatch.setattr(queue_svc, "enqueue", edit_then_enqueue)
    run_id = await create_run(client, admin_token, [stand_id])
    result = await detail(client, admin_token, run_id)
    assert result["queue_items"][0]["stand_id"] == stand_id
    assert result["entries"][0]["test_name"] != "Changed during scheduling"
    assert result["entries"][0]["enqueue_error_code"] is None
