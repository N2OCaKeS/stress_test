"""Цели пробинга: `GET /internal/probe-targets`.

Замена частым sweep'ам (`power.sweep`/`vms.status_sweep`): вместо диспатча
`power.status`/`vm.status` на каждую цель эндпоинт просто перечисляет серверы и
ВМ, а воркер снимает сигналы сам из фоновых probe-циклов. Проверяем состав
целей (серверные + ВМ поля), пропуск ВМ на списанном hub'е, cap-усечение и
worker_bot-грант.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from src.core.config import get_settings
from src.core.constants import ServerStatus
from tests._helpers import assert_error, auth_hdr as _hdr

BASE_INT = "/api/server/v1/internal"


@pytest_asyncio.fixture
async def make_vm(db, make_server):
    """ВМ в БД, привязанная к hub-серверу (создаётся, если не передан)."""
    from src.models import Vm
    from src.utils.ids import vm_id as new_id

    async def _factory(
        *, hub=None, department_id: str = "dep_a",
        ip_address: str | None = None,
    ):
        if hub is None:
            hub = await make_server(department_id=department_id)
        vm = Vm(
            id=new_id(),
            name=f"vm-{uuid.uuid4().hex[:6]}",
            hub_server_id=hub.id,
            department_id=department_id,
            ip_address=ip_address,
        )
        db.add(vm)
        await db.flush()
        await db.refresh(vm)
        return vm

    return _factory


@pytest.mark.usefixtures("soft_dept_mode")
class TestProbeTargets:
    async def test_lists_servers_and_vms(
        self, client, worker_bot_token_a, make_server, make_vm, db,
    ):
        hub = await make_server(department_id="dep_a")
        vm_bridge = await make_vm(hub=hub, ip_address="10.177.103.101")
        vm_nat = await make_vm(hub=hub)  # без IP
        await db.flush()

        resp = await client.get(
            f"{BASE_INT}/probe-targets", headers=_hdr(worker_bot_token_a),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Серверная цель — hub тоже перечислен (он же сервер).
        server_ids = {s["server_id"] for s in body["servers"]}
        assert hub.id in server_ids
        hub_target = next(s for s in body["servers"] if s["server_id"] == hub.id)
        assert hub_target["host"] == str(hub.ip_address)
        assert hub_target["ssh_port"] == hub.ssh_port
        assert hub_target["department_id"] == "dep_a"
        assert "is_managed" in hub_target and "management_user" in hub_target

        # ВМ-цели: обе перечислены; guest_ip только у bridge-ВМ.
        by_vm = {v["vm_id"]: v for v in body["vms"]}
        assert set(by_vm) == {vm_bridge.id, vm_nat.id}
        assert by_vm[vm_bridge.id]["guest_ip"] == "10.177.103.101"
        assert by_vm[vm_nat.id]["guest_ip"] is None
        assert by_vm[vm_bridge.id]["hub_server_id"] == hub.id
        assert by_vm[vm_bridge.id]["hub_host"] == str(hub.ip_address)
        assert by_vm[vm_bridge.id]["vm_name"] == vm_bridge.name
        assert body["servers_truncated"] is False
        assert body["vms_truncated"] is False

    async def test_skips_vm_on_decommissioned_hub(
        self, client, worker_bot_token_a, make_server, make_vm, db,
    ):
        live_hub = await make_server(department_id="dep_a")
        vm_live = await make_vm(hub=live_hub, ip_address="10.177.103.101")
        dead_hub = await make_server(department_id="dep_a")
        dead_hub.status = ServerStatus.DECOMMISSIONED
        await make_vm(hub=dead_hub)
        await db.flush()

        resp = await client.get(
            f"{BASE_INT}/probe-targets", headers=_hdr(worker_bot_token_a),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # ВМ на живом hub'е перечислена, на списанном — нет.
        assert {v["vm_id"] for v in body["vms"]} == {vm_live.id}
        # Списанный hub-сервер сам тоже исключён из серверных целей.
        assert dead_hub.id not in {s["server_id"] for s in body["servers"]}

    async def test_truncates_over_cap(
        self, client, worker_bot_token_a, make_server, make_vm, db, monkeypatch,
    ):
        hub = await make_server(department_id="dep_a")
        await make_vm(hub=hub)
        await make_vm(hub=hub)
        await make_vm(hub=hub)
        await db.flush()
        monkeypatch.setattr(get_settings(), "auto_inventory_fanout_max", 1)

        resp = await client.get(
            f"{BASE_INT}/probe-targets", headers=_hdr(worker_bot_token_a),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["vms_truncated"] is True
        assert len(body["vms"]) <= 1

    async def test_empty_when_no_resources(self, client, worker_bot_token_a):
        resp = await client.get(
            f"{BASE_INT}/probe-targets", headers=_hdr(worker_bot_token_a),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["servers"] == []
        assert body["vms"] == []

    async def test_admin_forbidden(self, client, admin_role_token_a):
        resp = await client.get(
            f"{BASE_INT}/probe-targets", headers=_hdr(admin_role_token_a),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_no_token_401(self, client):
        resp = await client.get(f"{BASE_INT}/probe-targets")
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")
