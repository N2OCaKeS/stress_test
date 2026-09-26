"""ВМ-стенды.

* `test_stands.target_type=vm` + `vm_id`: создание (отдел — из карточки ВМ
  server_service), ровно одно из `server_id`/`vm_id`, дубль — 409;
* тест, закреплённый за ВМ-стендом, проходит полный цикл: бронь → откат
  снимка (prepare-for-test по пути ВМ, `target=vm`) → подготовка → claim
  (host гостя) → завершение → освобождение (`testing_done`); все вызовы
  идут в `/internal/vms/{id}/…`, серверный канал не трогается;
* нет снимка нужной версии — отказ при постановке `VM_SNAPSHOT_NOT_FOUND`
  (аналог `ACS_SNAPSHOT_NOT_FOUND`); версия сравнивается по
  `normalized_version` server_service, `{mode}` — по режиму запуска;
* обзор пула: ВМ в смешанном batch-status, признак `target_type=vm`;
* сопоставление снимков для UI.

server_service замокан HTTP-транспортом `mock_server_service` из
`test_queue.py`; ВМ-специфичные пути (карточка ВМ, список снимков,
`vms` в batch-status) дописывает обёртка над его handler'ом.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from src.core.constants import QueueItemState
from src.core.exceptions import DomainValidationError
from src.db.session import AsyncSessionLocal
from src.services import queue as queue_svc
from src.services import server_client
from tests.conftest import auth_hdr as _hdr
from tests.test_queue import (  # noqa: F401 — фикстуры переиспользуются pytest'ом по имени
    CALLBACK_BASE,
    LAUNCH_CTX,
    OS_VERSION_NAME,
    QUEUE_BASE,
    SERVER_SECRET,
    STANDS_BASE,
    WORKER_SECRET,
    _create_test_def,
    _get_item,
    _identity,
    _server_hdr,
    configure_internal_keys,
    mock_git_token,
    mock_server_service,
    recorded_calls,
)


@pytest.fixture
def mock_vm_service(mock_server_service, monkeypatch, recorded_calls):
    """`mock_server_service` + ВМ-пути server_service.

    `snapshots` — список снимков ВМ, как его отдаёт
    `GET /internal/vms/{id}/snapshots` (по умолчанию — снимок версии
    `OS_VERSION_NAME` в компактной записи); `vm_status` — строка ВМ в
    batch-status.
    """
    def _install(*, department_id="dep_a", host="10.7.7.7", snapshots=None, vm_status=None, **kwargs):
        handle = mock_server_service(department_id=department_id, host=host, **kwargs)
        server_client._vm_snapshot_cache.clear()
        base_build = server_client.build_client
        state = {
            "snapshots": snapshots if snapshots is not None else [{
                "snapshot_id": "vms_1", "name": "185", "kind": "os_baseline",
                "version_name": "185", "normalized_version": OS_VERSION_NAME,
                "mode": None, "template": "{version}", "is_current": False,
            }],
            "prepare_bodies": [],
            "stand_setup_calls": [],
        }

        def _build(timeout: float) -> httpx.AsyncClient:
            base_handler = base_build(timeout)._transport.handler

            def handler(request: httpx.Request) -> httpx.Response:
                path = request.url.path
                if request.method == "GET" and (
                    path.startswith("/api/server/v1/vms/") or path.endswith("/snapshots")
                ):
                    recorded_calls.append((request.method, path))
                if request.method == "GET" and path.startswith("/api/server/v1/vms/"):
                    vm_id = path.rsplit("/", 1)[-1]
                    return httpx.Response(200, json={"id": vm_id, "name": f"vm-{vm_id}", "department_id": department_id})
                if request.method == "GET" and path.endswith("/snapshots") and "/internal/vms/" in path:
                    return httpx.Response(200, json={
                        "snapshots": state["snapshots"], "templates": ["{version}", "{version}_{mode}"],
                    })
                if request.method == "POST" and path.endswith("/prepare-for-test") and "/internal/vms/" in path:
                    state["prepare_bodies"].append(json.loads(request.content or b"{}"))
                if request.method == "POST" and path.endswith("/stand-setup") and "/internal/vms/" in path:
                    recorded_calls.append((request.method, path))
                    state["stand_setup_calls"].append((path, json.loads(request.content or b"{}")))
                    return httpx.Response(202, json={
                        "stand_setup_request_id": f"ssr_{len(state['stand_setup_calls'])}", "status": "in_progress",
                    })
                if request.method == "POST" and path.endswith("/batch-status"):
                    body = json.loads(request.content or b"{}")
                    response = base_handler(request)
                    data = response.json() if response.status_code == 200 else {"servers": []}
                    data["vms"] = [
                        {"vm_id": vm_id, "found": True, "ping_reachable": True, "ping_checked_at": None,
                         **(vm_status or {"busy_state": "free", "busy_actor_type": "user"})}
                        for vm_id in body.get("vm_ids", [])
                    ]
                    return httpx.Response(200, json=data)
                return base_handler(request)

            return httpx.AsyncClient(transport=httpx.MockTransport(handler))

        monkeypatch.setattr(server_client, "build_client", _build)
        handle.state = state
        return handle

    return _install


async def _create_vm_stand(client, admin_token, **extra) -> tuple[str, str]:
    vm_id = f"vm_{uuid.uuid4().hex[:10]}"
    resp = await client.post(
        STANDS_BASE, headers=_hdr(admin_token), json={"target_type": "vm", "vm_id": vm_id, **extra},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert (body["target_type"], body["vm_id"], body["server_id"]) == ("vm", vm_id, None)
    return body["id"], vm_id


class TestVmStandCrud:
    async def test_create_resolves_department_from_vm_card(self, client, admin_token, mock_vm_service, recorded_calls):
        mock_vm_service()
        stand_id, vm_id = await _create_vm_stand(client, admin_token)
        assert ("GET", f"/api/server/v1/vms/{vm_id}") in recorded_calls
        card = await client.get(f"{STANDS_BASE}/{stand_id}", headers=_hdr(admin_token))
        assert card.status_code == 200, card.text
        assert card.json()["server"]["id"] == vm_id
        listed = await client.get(f"{STANDS_BASE}?vm_id={vm_id}", headers=_hdr(admin_token))
        assert [s["id"] for s in listed.json()["items"]] == [stand_id]

    async def test_duplicate_vm_rejected(self, client, admin_token, mock_vm_service):
        mock_vm_service()
        _stand_id, vm_id = await _create_vm_stand(client, admin_token)
        resp = await client.post(STANDS_BASE, headers=_hdr(admin_token), json={"target_type": "vm", "vm_id": vm_id})
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "TEST_STAND_DUPLICATE"

    @pytest.mark.parametrize("body", [
        {"target_type": "vm", "server_id": "srv_x"},
        {"target_type": "vm", "vm_id": "vm_x", "server_id": "srv_x"},
        {"target_type": "server", "vm_id": "vm_x"},
        {},
    ])
    async def test_exactly_one_target(self, client, admin_token, mock_vm_service, body):
        mock_vm_service()
        resp = await client.post(STANDS_BASE, headers=_hdr(admin_token), json=body)
        assert resp.status_code == 422, resp.text

    async def test_snapshot_mapping(self, client, admin_token, mock_vm_service):
        mock_vm_service()
        stand_id, vm_id = await _create_vm_stand(client, admin_token)
        resp = await client.get(f"{STANDS_BASE}/{stand_id}/vm-snapshots", headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["vm_id"] == vm_id
        assert body["templates"] == ["{version}", "{version}_{mode}"]
        assert body["snapshots"][0]["normalized_version"] == OS_VERSION_NAME

    async def test_snapshot_mapping_rejects_server_stand(self, client, admin_token, mock_vm_service):
        mock_vm_service()
        resp = await client.post(STANDS_BASE, headers=_hdr(admin_token), json={"server_id": "srv_plain"})
        assert resp.status_code == 201
        mapping = await client.get(f"{STANDS_BASE}/{resp.json()['id']}/vm-snapshots", headers=_hdr(admin_token))
        assert mapping.status_code == 409
        assert mapping.json()["error_code"] == "TEST_STAND_NOT_VM"


class TestVmStandFullCycle:
    async def test_pinned_test_goes_through_vm_channel(
        self, client, admin_token, mock_vm_service, configure_internal_keys, recorded_calls, mock_git_token,
    ):
        handle = mock_vm_service(host="10.7.7.7")
        await mock_git_token()
        stand_id, vm_id = await _create_vm_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        # 1. Бронь + откат снимка (prepare-for-test по пути ВМ).
        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        assert item.state == QueueItemState.PREPARING
        paths = [p for _, p in recorded_calls]
        assert f"/api/server/v1/internal/vms/{vm_id}/snapshots" in paths
        assert f"/api/server/v1/internal/vms/{vm_id}/acquire-for-service" in paths
        assert f"/api/server/v1/internal/vms/{vm_id}/prepare-for-test" in paths
        assert not any("/internal/servers/" in p and not p.endswith("/batch-status") for p in paths)
        assert handle.state["prepare_bodies"][0]["target"] == {"type": "vm", "vm_id": vm_id}
        assert handle.state["prepare_bodies"][0]["mode"] == "orel"

        # 2. Подготовка завершена (callback server_service).
        resp = await client.post(
            f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={"correlation_id": item.id, "succeeded": True},
        )
        assert resp.status_code == 200, resp.text
        assert (await _get_item(item.id)).state == QueueItemState.READY

        # 3. Запуск: claim отдаёт IP гостя, стадия брони → testing.
        recorded_calls.clear()
        claim = await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))
        assert claim.status_code == 200, claim.text
        assert claim.json()["item"]["host"] == "10.7.7.7"
        paths = [p for _, p in recorded_calls]
        assert f"/api/server/v1/internal/vms/{vm_id}/service-status" in paths
        assert f"/api/server/v1/internal/vms/{vm_id}/connection-info" in paths

        # 4. Завершение → очередь пуста → освобождение ВМ в testing_done.
        recorded_calls.clear()
        done = await client.post(
            f"{QUEUE_BASE}/{item.id}/completed",
            headers=_server_hdr("testing_worker", WORKER_SECRET),
            json={"succeeded": True, "exit_code": 0},
        )
        assert done.status_code == 200, done.text
        assert (await _get_item(item.id)).state == QueueItemState.SUCCEEDED
        assert (
            "POST", f"/api/server/v1/internal/vms/{vm_id}/release-for-service-as-done",
        ) in recorded_calls

    async def test_missing_snapshot_rejects_enqueue(self, client, admin_token, mock_vm_service, recorded_calls):
        mock_vm_service(snapshots=[{
            "snapshot_id": "vms_2", "name": "1.7.11.17", "version_name": "1.7.11.17",
            "normalized_version": "1.7.11.17", "mode": None,
        }])
        stand_id, _vm_id = await _create_vm_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        async with AsyncSessionLocal() as db:
            with pytest.raises(DomainValidationError) as exc:
                await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        assert exc.value.error_code == "VM_SNAPSHOT_NOT_FOUND"
        assert exc.value.details["version_name"] == OS_VERSION_NAME
        assert not any(p.endswith("/acquire-for-service") for _, p in recorded_calls)

    async def test_mode_snapshot_must_match_launch_mode(self, client, admin_token, mock_vm_service):
        mock_vm_service(snapshots=[{
            "snapshot_id": "vms_3", "name": f"{OS_VERSION_NAME}_smolensk", "version_name": OS_VERSION_NAME,
            "normalized_version": OS_VERSION_NAME, "mode": "smolensk",
        }])
        stand_id, _vm_id = await _create_vm_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        async with AsyncSessionLocal() as db:
            with pytest.raises(DomainValidationError) as exc:
                await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        assert exc.value.error_code == "VM_SNAPSHOT_NOT_FOUND"
        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(
                db, _identity(), test_id, launch_context={**LAUNCH_CTX, "MODE": "smolensk"},
            )
        assert item.state == QueueItemState.PREPARING


class TestVmStandPoolOverview:
    async def test_vm_stand_in_mixed_batch(self, client, admin_token, mock_vm_service, recorded_calls):
        mock_vm_service(vm_status={"busy_state": "testing", "busy_actor_type": "service",
                                   "busy_service_name": "testing_service"})
        _stand_id, vm_id = await _create_vm_stand(client, admin_token)
        resp = await client.get("/api/testing/v1/pool-overview", headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        stands = {s["vm_id"]: s for s in resp.json()["stands"] if s.get("vm_id")}
        assert stands[vm_id]["target_type"] == "vm"
        assert stands[vm_id]["server_id"] is None
        assert stands[vm_id]["busy_state"] == "testing"
