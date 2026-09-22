"""Tests for POST /api/server/v1/tasks/{id}/cancel.

`worker_client._fetch_task_status_and_meta` и `worker_client.cancel_task`
мочатся через monkeypatch — тестируем endpoint-логику (permission ->
visibility -> cancellable-precondition -> audit), а не реальный cross-DB
UPDATE в dev_server_worker.tasks.
"""

from __future__ import annotations

import pytest

from tests._helpers import assert_error, auth_hdr as _hdr, next_stand_number

BASE = "/api/server/v1"


@pytest.fixture
def fake_worker(monkeypatch):
    """Перехватывает `worker_client._fetch_task_status_and_meta` и `cancel_task`.

    Возвращает dict с двумя ключами:

    * `tasks` — словарь task_id → {status, target_server_id, task_kind}.
      Тест нагрузил сюда «существующие» row'ы; fetch берёт оттуда.
    * `cancel_calls` — список фактических вызовов cancel_task'а.
    """
    state: dict = {"tasks": {}, "cancel_calls": []}

    async def fake_fetch(task_id_value: str):
        return state["tasks"].get(task_id_value)

    async def fake_cancel(*, task_id_value, cancelled_by, cancel_reason):
        state["cancel_calls"].append({
            "task_id": task_id_value,
            "cancelled_by": cancelled_by,
            "cancel_reason": cancel_reason,
        })
        row = state["tasks"].get(task_id_value)
        if row is None:
            return {"found": False}
        prev = row["status"]
        if prev not in ("queued", "running"):
            return {
                "found": True,
                "cancelled": False,
                "previous_status": prev,
                "task_kind": row["task_kind"],
                "target_server_id": row["target_server_id"],
            }
        row["status"] = "cancelled"
        return {
            "found": True,
            "cancelled": True,
            "previous_status": prev,
            "task_kind": row["task_kind"],
            "target_server_id": row["target_server_id"],
        }

    import src.api.v1.endpoints.tasks as tasks_endpoint
    monkeypatch.setattr(
        tasks_endpoint.worker_client, "_fetch_task_status_and_meta", fake_fetch,
    )
    monkeypatch.setattr(
        tasks_endpoint.worker_client, "cancel_task", fake_cancel,
    )
    return state


@pytest.fixture
def captured_audit(monkeypatch):
    """Захватывает `audit_service.emit` из tasks-endpoint'а (и server-svc для cross-dept)."""
    captured: list[dict] = []

    def fake_emit(action, actor_id=None, **kwargs):
        captured.append({"action": action, "actor_id": actor_id, **kwargs})

    import src.services.audit_service as audit_mod
    monkeypatch.setattr(audit_mod, "emit", fake_emit)
    monkeypatch.setattr(
        "src.api.v1.endpoints.tasks.audit_service.emit", fake_emit,
    )
    monkeypatch.setattr(
        "src.services.server.audit_service.emit", fake_emit,
    )
    return captured


def _events(captured: list[dict], action: str) -> list[dict]:
    return [e for e in captured if e["action"] == action]


class TestTaskCancelHappy:
    """Happy path: admin cancels pending task."""

    async def test_admin_cancels_pending_task(
        self, client, admin_role_token_a, make_server, fake_worker, captured_audit,
    ):
        srv = await make_server(department_id="dep_a")
        fake_worker["tasks"]["tsk_pending_1"] = {
            "status": "queued",
            "target_server_id": srv.id,
            "task_kind": "power.on",
        }
        resp = await client.post(
            f"{BASE}/tasks/tsk_pending_1/cancel",
            headers=_hdr(admin_role_token_a),
            json={"reason": "wrong server"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["task_id"] == "tsk_pending_1"
        assert body["status"] == "cancelled"
        assert body["previous_status"] == "queued"
        assert body["cancel_reason"] == "wrong server"
        assert body["cancelled_by"]

        # cancel_task был вызван с теми аргументами
        assert len(fake_worker["cancel_calls"]) == 1
        call = fake_worker["cancel_calls"][0]
        assert call["task_id"] == "tsk_pending_1"
        assert call["cancel_reason"] == "wrong server"
        assert call["cancelled_by"]

        # success-аудит
        ev = _events(captured_audit, "task.cancelled")
        assert len(ev) == 1
        assert ev[0]["status"] == "success"
        assert ev[0]["allowed"] is True
        d = ev[0]["details"]
        assert d["task_id"] == "tsk_pending_1"
        assert d["previous_status"] == "queued"
        assert d["task_kind"] == "power.on"
        assert d["target_server_id"] == srv.id
        assert d["cancel_reason"] == "wrong server"

    async def test_cancel_running_task(
        self, client, admin_role_token_a, make_server, fake_worker, captured_audit,
    ):
        srv = await make_server(department_id="dep_a")
        fake_worker["tasks"]["tsk_running_1"] = {
            "status": "running",
            "target_server_id": srv.id,
            "task_kind": "inventory.sync",
        }
        resp = await client.post(
            f"{BASE}/tasks/tsk_running_1/cancel",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["previous_status"] == "running"
        assert body["cancel_reason"] is None

    async def test_no_body_is_allowed(
        self, client, admin_role_token_a, make_server, fake_worker,
    ):
        srv = await make_server(department_id="dep_a")
        fake_worker["tasks"]["tsk_no_body"] = {
            "status": "queued",
            "target_server_id": srv.id,
            "task_kind": "power.off",
        }
        resp = await client.post(
            f"{BASE}/tasks/tsk_no_body/cancel",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200, resp.text


class TestTaskCancelConflict:
    """Terminal status → 409 TASK_NOT_CANCELLABLE."""

    @pytest.mark.parametrize("terminal", ["succeeded", "failed", "cancelled"])
    async def test_terminal_returns_409(
        self,
        client,
        admin_role_token_a,
        make_server,
        fake_worker,
        captured_audit,
        terminal,
    ):
        srv = await make_server(department_id="dep_a")
        fake_worker["tasks"]["tsk_done"] = {
            "status": terminal,
            "target_server_id": srv.id,
            "task_kind": "power.on",
        }
        resp = await client.post(
            f"{BASE}/tasks/tsk_done/cancel",
            headers=_hdr(admin_role_token_a),
        )
        body = assert_error(resp, 409, "TASK_NOT_CANCELLABLE")

        # failure-аудит с reason=not_cancellable
        ev = _events(captured_audit, "task.cancelled")
        assert len(ev) == 1
        assert ev[0]["status"] == "failure"
        assert ev[0]["details"]["reason"] == "not_cancellable"
        assert ev[0]["details"]["previous_status"] == terminal


class TestTaskCancelPermission:
    """`(task, cancel)` нужен явно — operator/reader не имеют."""

    async def test_operator_cannot_cancel(
        self, client, operator_token_a, make_server, fake_worker, captured_audit,
    ):
        srv = await make_server(department_id="dep_a")
        fake_worker["tasks"]["tsk_p"] = {
            "status": "queued",
            "target_server_id": srv.id,
            "task_kind": "power.on",
        }
        resp = await client.post(
            f"{BASE}/tasks/tsk_p/cancel",
            headers=_hdr(operator_token_a),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")
        assert fake_worker["cancel_calls"] == []
        # denied-аудит
        ev = _events(captured_audit, "task.cancelled")
        assert len(ev) == 1
        assert ev[0]["status"] == "denied"
        assert ev[0]["details"]["reason"] == "permission_denied"

    async def test_reader_cannot_cancel(
        self, client, reader_token_a, make_server, fake_worker,
    ):
        srv = await make_server(department_id="dep_a")
        fake_worker["tasks"]["tsk_p2"] = {
            "status": "queued",
            "target_server_id": srv.id,
            "task_kind": "power.on",
        }
        resp = await client.post(
            f"{BASE}/tasks/tsk_p2/cancel",
            headers=_hdr(reader_token_a),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")
        assert fake_worker["cancel_calls"] == []

    async def test_no_token_returns_401(
        self, client, fake_worker,
    ):
        resp = await client.post(f"{BASE}/tasks/tsk_anon/cancel")
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")
        assert fake_worker["cancel_calls"] == []


class TestTaskCancelNotFound:
    """Несуществующая task → 404."""

    async def test_missing_task_returns_404(
        self, client, admin_role_token_a, fake_worker, captured_audit,
    ):
        resp = await client.post(
            f"{BASE}/tasks/tsk_nope/cancel",
            headers=_hdr(admin_role_token_a),
        )
        assert_error(resp, 404, "TASK_NOT_FOUND")
        ev = _events(captured_audit, "task.cancelled")
        assert len(ev) == 1
        # Permission прошёл, row отсутствует — visibility-404 → failure.
        assert ev[0]["status"] == "failure"
        assert ev[0]["allowed"] is True
        assert ev[0]["details"]["reason"] == "task_not_found"

    async def test_cross_dept_target_returns_404(
        self, client, admin_token_b, make_server, fake_worker, captured_audit,
    ):
        """Admin из dep_b пытается отменить task на сервере dep_a → 404 masking."""
        srv = await make_server(department_id="dep_a")
        fake_worker["tasks"]["tsk_xdept"] = {
            "status": "queued",
            "target_server_id": srv.id,
            "task_kind": "power.on",
        }
        resp = await client.post(
            f"{BASE}/tasks/tsk_xdept/cancel",
            headers=_hdr(admin_token_b),
        )
        assert_error(resp, 404, "TASK_NOT_FOUND")
        # cancel_task не вызывался — visibility check сработал ДО mutate
        assert fake_worker["cancel_calls"] == []
        ev = _events(captured_audit, "task.cancelled")
        assert len(ev) == 1
        # Permission прошёл, target невидим — visibility-404 → failure.
        assert ev[0]["status"] == "failure"
        assert ev[0]["allowed"] is True
        assert "cross_dept" in ev[0]["details"]["reason"]


class TestTaskCancelVmCreateCleanup:
    """Отмена queued vm.create снимает осиротевшую placeholder-строку ВМ.

    Реальный VM-row кладём в БД (через `db`), fetch/cancel мочим — проверяем,
    что endpoint удаляет строку ВМ только на `queued` vm.create и не трогает
    её на `running` или на не-vm.create.
    """

    async def _make_creating_vm(self, db, make_server):
        from src.models import Vm
        from src.utils.ids import vm_id as new_id

        hub = await make_server(department_id="dep_a")
        vm = Vm(
            id=new_id(),
            name="vm-orphan",
            number=next_stand_number(),
            hub_server_id=hub.id,
            department_id="dep_a",
            status="free",
            cpu=4, ram_mb=8192, disk_gb=100,
            busy_state="creating",
            network_mode="nat",
        )
        db.add(vm)
        await db.flush()
        return hub, vm

    async def test_queued_vm_create_deletes_vm_row(
        self, client, admin_role_token_a, make_server, fake_worker, captured_audit, db,
    ):
        from src.repositories import vm as vm_repo

        hub, vm = await self._make_creating_vm(db, make_server)
        fake_worker["tasks"]["tsk_vmc"] = {
            "status": "queued",
            "target_server_id": hub.id,
            "task_kind": "vm.create",
            "target_resource_id": vm.id,
        }
        resp = await client.post(
            f"{BASE}/tasks/tsk_vmc/cancel",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200, resp.text
        # placeholder-строка ВМ снята
        assert await vm_repo.get_by_id(db, vm.id) is None
        ev = _events(captured_audit, "task.cancelled")
        assert len(ev) == 1
        assert ev[0]["status"] == "success"
        assert ev[0]["details"]["vm_cleaned_up"] is True

    async def test_running_vm_create_keeps_vm_row(
        self, client, admin_role_token_a, make_server, fake_worker, captured_audit, db,
    ):
        from src.repositories import vm as vm_repo

        hub, vm = await self._make_creating_vm(db, make_server)
        fake_worker["tasks"]["tsk_vmr"] = {
            "status": "running",
            "target_server_id": hub.id,
            "task_kind": "vm.create",
            "target_resource_id": vm.id,
        }
        resp = await client.post(
            f"{BASE}/tasks/tsk_vmr/cancel",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200, resp.text
        # частично собранная ВМ не трогается
        assert await vm_repo.get_by_id(db, vm.id) is not None
        ev = _events(captured_audit, "task.cancelled")
        assert "vm_cleaned_up" not in ev[0]["details"]

    async def test_queued_non_vm_create_keeps_vm_row(
        self, client, admin_role_token_a, make_server, fake_worker, captured_audit, db,
    ):
        from src.repositories import vm as vm_repo

        hub, vm = await self._make_creating_vm(db, make_server)
        # Задача не vm.create, target_resource_id указывает на строку ВМ —
        # cleanup всё равно не должен её тронуть.
        fake_worker["tasks"]["tsk_other"] = {
            "status": "queued",
            "target_server_id": hub.id,
            "task_kind": "vm.power",
            "target_resource_id": vm.id,
        }
        resp = await client.post(
            f"{BASE}/tasks/tsk_other/cancel",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200, resp.text
        assert await vm_repo.get_by_id(db, vm.id) is not None
        ev = _events(captured_audit, "task.cancelled")
        assert "vm_cleaned_up" not in ev[0]["details"]

    async def test_queued_vm_create_missing_vm_is_idempotent(
        self, client, admin_role_token_a, make_server, fake_worker, captured_audit,
    ):
        srv = await make_server(department_id="dep_a")
        # ВМ-строки нет (уже удалена reconcile'ом) — cleanup не падает.
        fake_worker["tasks"]["tsk_vmgone"] = {
            "status": "queued",
            "target_server_id": srv.id,
            "task_kind": "vm.create",
            "target_resource_id": "vm_does_not_exist",
        }
        resp = await client.post(
            f"{BASE}/tasks/tsk_vmgone/cancel",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200, resp.text
        ev = _events(captured_audit, "task.cancelled")
        assert ev[0]["details"]["vm_cleaned_up"] is False


class TestTaskCancelValidation:
    """Pydantic-валидация тела."""

    async def test_reason_too_long_returns_422(
        self, client, admin_role_token_a, make_server, fake_worker,
    ):
        srv = await make_server(department_id="dep_a")
        fake_worker["tasks"]["tsk_v"] = {
            "status": "queued",
            "target_server_id": srv.id,
            "task_kind": "power.on",
        }
        resp = await client.post(
            f"{BASE}/tasks/tsk_v/cancel",
            headers=_hdr(admin_role_token_a),
            json={"reason": "a" * 513},
        )
        assert_error(resp, 422, "VALIDATION_ERROR")
