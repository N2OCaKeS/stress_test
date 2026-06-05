"""Тесты provision/update/deprovision OS-пользователя на сервере (#13).

Покрывает:
* trigger-dispatch `POST /server-accounts/{id}/provision|update_on_host|
  deprovision` — 202 + task_id, нужный task_kind, права (create/update/delete),
  server_id обязателен и должен быть привязан, decommissioned;
* callback `POST /internal/servers/{id}/accounts/{aid}/provision_status` —
  обновляет `present_on_server`; worker_bot может, reader нет;
* payload несёт login/sudo/группы/shell/home для воркера.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.core.constants import ServerStatus
from src.models import ServerAccountServer

BASE = "/api/server/v1/server-accounts"
BASE_INT = "/api/server/v1/internal"


from tests._helpers import auth_hdr as _hdr  # noqa: E402


@pytest.fixture
def captured_dispatch(monkeypatch):
    """Перехват worker_client.dispatch_task из endpoints/worker_dispatch.py."""
    calls: list[dict] = []
    by_key: dict[str, str] = {}

    async def fake_dispatch(*, db=None, task_kind, target_server_id, payload,
                            created_by, request_id,
                            target_resource_id=None, idempotency_key=None,
                            return_hit=False):
        if idempotency_key is not None and idempotency_key in by_key:
            existing = by_key[idempotency_key]
            return (existing, True) if return_hit else existing
        calls.append({
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "target_resource_id": target_resource_id,
            "payload": payload,
            "idempotency_key": idempotency_key,
        })
        new_id = f"tsk_{task_kind.replace('.', '_')}_fake_{len(calls)}"
        if idempotency_key is not None:
            by_key[idempotency_key] = new_id
        return (new_id, False) if return_hit else new_id

    async def fake_dispatch_with_hit(**kwargs):
        kwargs["return_hit"] = True
        return await fake_dispatch(**kwargs)

    import src.services.worker_client as worker_mod
    monkeypatch.setattr(worker_mod, "dispatch_task", fake_dispatch)
    monkeypatch.setattr(worker_mod, "dispatch_task_with_hit", fake_dispatch_with_hit)
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
        fake_dispatch,
    )
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task_with_hit",
        fake_dispatch_with_hit,
    )
    return calls


# ── Trigger: provision / update_on_host / deprovision ────────────────────────


class TestProvisionDispatch:
    async def test_operator_provisions(
        self, client, operator_token_a, make_server, make_account, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(
            server_id=srv.id, login="ops", has_sudo=True,
            shell="/bin/bash", password="pw1",
        )
        resp = await client.post(
            f"{BASE}/{acc.id}/provision?server_id={srv.id}",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["operation"] == "provision"
        assert body["server_id"] == srv.id
        assert body["task_id"].startswith("tsk_")
        assert len(captured_dispatch) == 1
        call = captured_dispatch[0]
        assert call["task_kind"] == "account.provision"
        assert call["target_resource_id"] == acc.id
        # Атрибуты аккаунта проброшены воркеру.
        assert call["payload"]["login"] == "ops"
        assert call["payload"]["has_sudo"] is True
        assert call["payload"]["shell"] == "/bin/bash"
        assert call["payload"]["target_department_id"] == "dep_a"

    async def test_reader_cannot_provision(
        self, client, reader_token_a, make_server, make_account, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops")
        resp = await client.post(
            f"{BASE}/{acc.id}/provision?server_id={srv.id}",
            headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 403
        assert captured_dispatch == []

    async def test_update_on_host_uses_usermod_task(
        self, client, operator_token_a, make_server, make_account, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops")
        resp = await client.post(
            f"{BASE}/{acc.id}/update_on_host?server_id={srv.id}",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["operation"] == "update"
        assert captured_dispatch[0]["task_kind"] == "account.update_on_host"

    async def test_deprovision_uses_userdel_task_and_remove_home(
        self, client, admin_role_token_a, make_server, make_account, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops")
        resp = await client.post(
            f"{BASE}/{acc.id}/deprovision?server_id={srv.id}&remove_home=true",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["operation"] == "deprovision"
        call = captured_dispatch[0]
        assert call["task_kind"] == "account.deprovision"
        assert call["payload"]["remove_home"] is True

    async def test_reader_cannot_deprovision(
        self, client, reader_token_a, make_server, make_account, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops")
        resp = await client.post(
            f"{BASE}/{acc.id}/deprovision?server_id={srv.id}",
            headers=_hdr(reader_token_a),
        )
        # reader не имеет delete → 403.
        assert resp.status_code == 403
        assert captured_dispatch == []

    async def test_missing_server_id_422(
        self, client, operator_token_a, make_server, make_account, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops")
        resp = await client.post(
            f"{BASE}/{acc.id}/provision", headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 422
        assert captured_dispatch == []

    async def test_server_not_linked_404(
        self, client, operator_token_a, make_server, make_account, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        other = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops")
        resp = await client.post(
            f"{BASE}/{acc.id}/provision?server_id={other.id}",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "ACCOUNT_NOT_FOUND"
        assert captured_dispatch == []

    async def test_cross_dept_account_404(
        self, client, operator_token_b, make_server, make_account, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops")
        resp = await client.post(
            f"{BASE}/{acc.id}/provision?server_id={srv.id}",
            headers=_hdr(operator_token_b),
        )
        # operator_b не видит чужой аккаунт; permission проходит, visibility — 404.
        assert resp.status_code in (403, 404)
        assert captured_dispatch == []

    async def test_decommissioned_409(
        self, client, operator_token_a, make_server, make_account, captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops")
        srv.status = ServerStatus.DECOMMISSIONED
        await db.flush()
        resp = await client.post(
            f"{BASE}/{acc.id}/provision?server_id={srv.id}",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "SERVER_DECOMMISSIONED"
        assert captured_dispatch == []


# ── Callback: POST /internal/.../provision_status ────────────────────────────


@pytest.mark.usefixtures("soft_dept_mode")
class TestProvisionStatusCallback:
    async def test_provision_marks_present_true(
        self, client, worker_bot_token_a, make_server, make_account, db, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        acc = await make_account(server_id=srv.id, login="ops")
        # Сначала уведём связку в drift, чтобы увидеть, что provision её поднимает.
        link = (await db.execute(
            select(ServerAccountServer).where(
                ServerAccountServer.account_id == acc.id,
                ServerAccountServer.server_id == srv.id,
            )
        )).scalar_one()
        link.present_on_server = False
        await db.flush()

        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/accounts/{acc.id}/provision_status",
            headers=_hdr(worker_bot_token_a),
            json={"operation": "provision", "present": True},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["present_on_server"] is True

        await db.commit()
        refreshed = (await db.execute(
            select(ServerAccountServer).where(ServerAccountServer.id == link.id)
        )).scalar_one()
        assert refreshed.present_on_server is True

    async def test_deprovision_marks_present_false(
        self, client, worker_bot_token_a, make_server, make_account, db, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        acc = await make_account(server_id=srv.id, login="ops")
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/accounts/{acc.id}/provision_status",
            headers=_hdr(worker_bot_token_a),
            json={"operation": "deprovision", "present": False},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["present_on_server"] is False

        await db.commit()
        link = (await db.execute(
            select(ServerAccountServer).where(
                ServerAccountServer.account_id == acc.id,
                ServerAccountServer.server_id == srv.id,
            )
        )).scalar_one()
        assert link.present_on_server is False

    async def test_reader_cannot_submit(
        self, client, reader_token_a, make_server, make_account, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        acc = await make_account(server_id=srv.id, login="ops")
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/accounts/{acc.id}/provision_status",
            headers=_hdr(reader_token_a),
            json={"operation": "provision", "present": True},
        )
        assert resp.status_code == 403

    async def test_unlinked_account_404(
        self, client, worker_bot_token_a, make_server, make_account, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        other = await make_server(department_id=dept_a)
        acc = await make_account(server_id=srv.id, login="ops")
        resp = await client.post(
            f"{BASE_INT}/servers/{other.id}/accounts/{acc.id}/provision_status",
            headers=_hdr(worker_bot_token_a),
            json={"operation": "provision", "present": True},
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "ACCOUNT_NOT_FOUND"

    async def test_invalid_operation_422(
        self, client, worker_bot_token_a, make_server, make_account, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        acc = await make_account(server_id=srv.id, login="ops")
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/accounts/{acc.id}/provision_status",
            headers=_hdr(worker_bot_token_a),
            json={"operation": "frobnicate", "present": True},
        )
        assert resp.status_code == 422


# ── Management-session hints in dispatch payload ─────────────────────────────


class TestManagementSessionPayload:
    """is_managed/management_user пробрасываются в payload задачи.

    Worker по ним выбирает сессию: подготовленный сервер → управляющий
    пользователь по ключу; иначе → self-сессия под аккаунтом.
    """

    async def test_managed_server_propagates_hints(
        self, client, operator_token_a, make_server, make_account, captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        srv.is_managed = True
        srv.management_user = "dbos"
        await db.flush()
        acc = await make_account(server_id=srv.id, login="ops", password="pw1")
        resp = await client.post(
            f"{BASE}/{acc.id}/provision?server_id={srv.id}",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        payload = captured_dispatch[0]["payload"]
        assert payload["is_managed"] is True
        assert payload["management_user"] == "dbos"

    async def test_unmanaged_server_defaults_hints(
        self, client, operator_token_a, make_server, make_account, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password="pw1")
        resp = await client.post(
            f"{BASE}/{acc.id}/provision?server_id={srv.id}",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        payload = captured_dispatch[0]["payload"]
        assert payload["is_managed"] is False
        assert payload["management_user"] is None

    async def test_deprovision_propagates_hints(
        self, client, admin_role_token_a, make_server, make_account, captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        srv.is_managed = True
        srv.management_user = "dbos"
        await db.flush()
        acc = await make_account(server_id=srv.id, login="ops", password="pw1")
        resp = await client.post(
            f"{BASE}/{acc.id}/deprovision?server_id={srv.id}",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 202, resp.text
        payload = captured_dispatch[0]["payload"]
        assert payload["is_managed"] is True
        assert payload["management_user"] == "dbos"
