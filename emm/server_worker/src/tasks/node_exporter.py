"""Установка node_exporter для Grafana-метрик.

Две цели:

* `server.install_node_exporter` — прямой managed SSH на сервер;
* `vm.install_node_exporter` — guest SSH через VMS-hub.

Скрипт повторяемый: пересоздаёт compose-файл и systemd-unit, включает сервис и
стартует контейнер `prom/node-exporter` на host network (`:9100`).
"""

from __future__ import annotations

from src.main import broker
from src.services import ssh_client
from src.tasks._account_helpers import resolve_ssh_creds
from src.tasks._node_exporter_helpers import (
    AUDIT_SAFE_FIELDS,
    install_node_exporter as _install_node_exporter,
)
from src.tasks._runner import run_task
from src.tasks._target_runner import DirectRunner, GuestHopRunner
from src.tasks._vm_prepare_helpers import (
    _shred_temp_key,
    choose_guest_connector,
    load_guest_key,
)
from src.tasks._vms_helpers import (
    open_hub_session,
    resolve_guest_ip,
    validate_name,
)

@broker.task("server.install_node_exporter")
async def server_install_node_exporter(task_id: str) -> None:
    """Установить node_exporter на managed-сервере."""

    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        target_dept = payload.get("target_department_id")
        creds = await resolve_ssh_creds(
            payload,
            server_id,
            account_id=None,
            target_dept=target_dept,
            is_managed=True,
        )
        ssh_client.apply_session_hints(creds, payload)
        await ssh_client.attach_management_creds(creds, server_id)
        session = ssh_client.build_session(creds, server_id)
        async with session as ssh:
            result = await _install_node_exporter(DirectRunner(ssh), ssh.host)
        result["server_id"] = server_id
        return result

    await run_task(
        task_id,
        audit_action="server.node_exporter_installed",
        audit_target_type="server",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS,
    )


@broker.task("vm.install_node_exporter")
async def vm_install_node_exporter(task_id: str) -> None:
    """Установить node_exporter в госте ВМ через VMS-hub."""

    async def _impl(payload: dict) -> dict:
        vm_id = payload["vm_id"]
        host_label = str(
            payload.get("host") or payload.get("hub_host")
            or payload.get("hub_server_id") or "hub"
        )
        vm_name = validate_name(
            payload.get("vm_name") or payload["name"], host_label, "vm_name",
        )
        session, host = await open_hub_session(payload)
        async with session as ssh:
            guest_ip = await resolve_guest_ip(ssh, host, vm_name, payload)
            mgmt_user, key_path = await load_guest_key(ssh, host, payload)
            connect = choose_guest_connector(guest_ip, mgmt_user, key_path)
            runner = GuestHopRunner(ssh, connect, host=host)
            try:
                result = await _install_node_exporter(runner, guest_ip)
            finally:
                if key_path:
                    await _shred_temp_key(ssh, key_path)
        result.update({"vm_id": vm_id, "vm_name": vm_name})
        return result

    await run_task(
        task_id,
        audit_action="vm.node_exporter_installed",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS,
    )
