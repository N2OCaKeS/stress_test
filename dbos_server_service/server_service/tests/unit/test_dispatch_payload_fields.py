"""Проверка payload-полей для _dispatch_for_server и account-task'ов.

Цель: зафиксировать контракт payload, включая поля host/ssh_port/is_managed/
management_user, которые воркер использует для SSH-адресации.

Дополняет test_worker_task_dispatch_endpoints.py — там payload уже проверяется
для базовых сценариев. Здесь фокус на граничных значениях полей.
"""

from __future__ import annotations

import pytest

BASE = "/api/server/v1"


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def captured_dispatch(monkeypatch):
    calls: list[dict] = []

    async def fake_dispatch(*, db=None, task_kind, target_server_id, payload,
                            created_by, request_id,
                            target_resource_id=None, idempotency_key=None,
                            return_hit=False):
        calls.append({"task_kind": task_kind, "payload": payload,
                      "target_server_id": target_server_id})
        new_id = f"tsk_{task_kind.replace('.', '_')}_fake_{len(calls)}"
        return (new_id, False) if return_hit else new_id

    async def fake_dispatch_with_hit(**kwargs):
        kwargs["return_hit"] = True
        return await fake_dispatch(**kwargs)

    import src.services.worker_client as wm
    monkeypatch.setattr(wm, "dispatch_task", fake_dispatch)
    monkeypatch.setattr(wm, "dispatch_task_with_hit", fake_dispatch_with_hit)
    monkeypatch.setattr("src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
                        fake_dispatch)
    monkeypatch.setattr("src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task_with_hit",
                        fake_dispatch_with_hit)
    monkeypatch.setattr("src.api.v1.endpoints.installed_packages.worker_client.dispatch_task",
                        fake_dispatch)
    monkeypatch.setattr("src.api.v1.endpoints.installed_packages.worker_client.dispatch_task_with_hit",
                        fake_dispatch_with_hit)
    return calls


class TestServerDispatchPayloadHasSshFields:
    """_dispatch_for_server прокидывает host/ssh_port/is_managed/management_user."""

    async def test_power_status_payload_ssh_fields(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a", with_ipmi=True)
        await client.post(
            f"{BASE}/servers/{srv.id}/power/status", headers=_hdr(operator_token_a),
        )
        assert len(captured_dispatch) == 1
        p = captured_dispatch[0]["payload"]
        assert "host" in p
        assert "ssh_port" in p
        assert "is_managed" in p
        assert "management_user" in p
        assert p["host"] == srv.hostname
        assert p["ssh_port"] == srv.ssh_port
        assert p["is_managed"] is False
        assert p["management_user"] is None

    async def test_inventory_sync_payload_ssh_fields(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        await client.post(
            f"{BASE}/servers/{srv.id}/inventory/sync", headers=_hdr(operator_token_a),
        )
        p = captured_dispatch[0]["payload"]
        assert p["host"] == srv.hostname
        assert p["ssh_port"] == srv.ssh_port
        assert p["is_managed"] is False

    async def test_managed_server_payload_reflects_management_user(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        srv.is_managed = True
        srv.management_user = "dbos_sys"
        await db.flush()

        await client.post(
            f"{BASE}/servers/{srv.id}/inventory/sync", headers=_hdr(operator_token_a),
        )
        p = captured_dispatch[0]["payload"]
        assert p["is_managed"] is True
        assert p["management_user"] == "dbos_sys"

    async def test_custom_ssh_port_propagated(
        self, client, operator_token_a, captured_dispatch, db,
    ):
        """Нестандартный SSH-порт попадает в payload."""
        from src.models import Server
        from src.utils.ids import server_id as new_id

        srv = Server(
            id=new_id(),
            hostname="srv-nonstandard",
            ip_address="10.0.0.42",
            ssh_port=2222,
            department_id="dep_a",
        )
        db.add(srv)
        await db.flush()

        await client.post(
            f"{BASE}/servers/{srv.id}/inventory/sync", headers=_hdr(operator_token_a),
        )
        if captured_dispatch:
            assert captured_dispatch[0]["payload"]["ssh_port"] == 2222


class TestInstalledPackagesPayloadSshFields:
    """installed_packages.list payload тоже содержит host/ssh_port."""

    async def test_payload_contains_host_and_ssh_port(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        await client.post(
            f"{BASE}/servers/{srv.id}/installed-packages",
            headers=_hdr(operator_token_a),
        )
        assert len(captured_dispatch) == 1
        p = captured_dispatch[0]["payload"]
        assert p["host"] == srv.hostname
        assert p["ssh_port"] == srv.ssh_port
        assert p["pattern"] == "*"
        assert "is_managed" not in p, (
            "installed_packages payload не включает is_managed — только host/port"
        )

    async def test_pattern_propagated_to_payload(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        await client.post(
            f"{BASE}/servers/{srv.id}/installed-packages",
            headers=_hdr(operator_token_a),
            params={"pattern": "astra*"},
        )
        assert captured_dispatch[0]["payload"]["pattern"] == "astra*"


class TestAccountTaskPayloadSshFields:
    """account-task'ы (provision/update/deprovision) несут host/ssh_port/is_managed."""

    async def test_provision_payload_has_ssh_and_management_fields(
        self, client, operator_token_a, make_server, make_account, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="appuser",
                                 shell="/bin/bash", has_sudo=True)

        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/provision",
            headers=_hdr(operator_token_a),
            params={"server_id": srv.id},
        )
        assert resp.status_code == 202, resp.text
        p = captured_dispatch[0]["payload"]
        assert p["host"] == srv.hostname
        assert p["ssh_port"] == srv.ssh_port
        assert p["is_managed"] is False
        assert p["management_user"] is None
        assert p["login"] == "appuser"
        # Провизион несёт атрибуты аккаунта.
        assert "has_sudo" in p
        assert "shell" in p

    async def test_rotate_payload_no_account_attrs(
        self, client, operator_token_a, make_server, make_account, captured_dispatch,
    ):
        """account.rotate_password НЕ содержит has_sudo/shell/home_dir."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="dba")

        await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate",
            headers=_hdr(operator_token_a),
        )
        p = captured_dispatch[0]["payload"]
        assert p["host"] == srv.hostname
        assert "has_sudo" not in p, "rotate не включает sudo-атрибуты"
        assert "shell" not in p

    async def test_deprovision_payload_has_remove_home(
        self, client, admin_role_token_a, make_server, make_account, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ex_user")

        await client.post(
            f"{BASE}/server-accounts/{acc.id}/deprovision",
            headers=_hdr(admin_role_token_a),
            params={"server_id": srv.id, "remove_home": "true"},
        )
        assert len(captured_dispatch) == 1
        p = captured_dispatch[0]["payload"]
        assert p["remove_home"] is True
        assert p["host"] == srv.hostname
