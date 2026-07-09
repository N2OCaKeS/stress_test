"""Периодическая проба статуса ВМ — зеркало серверного `power.status`.

Как у серверов снимаются три независимых сигнала (ping / ssh / ipmi), так у ВМ
снимаются три своих: питание домена, ping гостя и доступность SSH гостя. Все
три меряются через управляющую SSH-сессию к hub'у (ВМ живёт на нём):

* **power** — `virsh domstate <domain>` в `qemu:///session` управляющей учётки,
  результат нормализуется `map_domstate` (running→on, shut off→off и т.д.);
* **ping** — `ping -c1` до LAN-адреса гостя, выполняется НА hub'е (у него есть
  маршрут к bridge-ВМ);
* **ssh** — TCP-проба порта SSH гостя, тоже с hub'а.

Bridge-ВМ несёт статический LAN-адрес — он приходит в payload либо читается из
`domifaddr`. NAT/SLIRP-ВМ (адрес 10.0.2.x) из LAN недостижима, а её адрес виден
только qemu-guest-agent'у: для такой ВМ ping/ssh молча дают `reachable=False`
(это не ошибка), а питание всё равно снимается по domstate.

Read-only проба: недоступный гость / отсутствие адреса не роняют таску. Итог
уходит в server_service тем же callback'ом состояния ВМ, что и `vm.power`
(`submit_vm_state`), только с полями ping/ssh/power_state.
"""

from __future__ import annotations

import logging

from src.core.config import get_settings
from src.main import broker
from src.services import server_service_client
from src.tasks._runner import run_task
from src.tasks._reachability import _parse_ping_latency
from src.tasks._vms_helpers import (
    LIBVIRT_SESSION_ENV,
    map_domstate,
    open_hub_session,
    parse_domifaddr,
    validate_ip,
    validate_name,
)

logger = logging.getLogger(__name__)

# power_state — публичное поле; ping/ssh reachability — диагностика, не секрет,
# поэтому пускаем их в audit details (как у серверного power.status).
AUDIT_SAFE_FIELDS_STATUS: set[str] = {
    "vm_id", "vm_name", "power_state",
    "ping_reachable", "ssh_reachable",
}

# Порт SSH гостя по умолчанию — образы поднимают sshd на 22.
_GUEST_SSH_PORT = 22


async def _guest_ip_best_effort(ssh, name: str, payload: dict) -> str | None:
    """IP гостя для сетевых проб: payload > domifaddr. None, если адреса нет.

    В отличие от `resolve_guest_ip`, отсутствие адреса НЕ ошибка: у SLIRP-ВМ
    lease-таблицы нет, а из LAN она всё равно недостижима — отдаём None, и
    ping/ssh просто помечаются недоступными.
    """
    raw = payload.get("guest_ip") or payload.get("ip_address")
    if raw:
        try:
            return validate_ip(str(raw), name).split("/")[0]
        except Exception:  # noqa: BLE001 — кривой адрес не должен ронять пробу
            return None
    for source in ("lease", "agent"):
        try:
            _rc, out, _err = await ssh.run(
                f"{LIBVIRT_SESSION_ENV} virsh domifaddr {name} --source {source}",
            )
        except Exception:  # noqa: BLE001
            continue
        parsed = parse_domifaddr(out)
        if parsed:
            return parsed
    return None


async def _probe_power(ssh, name: str) -> str:
    """Снять питание домена через `virsh domstate`. Любой сбой → `unknown`."""
    try:
        _rc, out, _err = await ssh.run(
            f"{LIBVIRT_SESSION_ENV} virsh domstate {name}",
        )
    except Exception:  # noqa: BLE001 — read-only проба не должна падать
        return "unknown"
    return map_domstate(out)


async def _probe_ping(ssh, guest_ip: str, timeout: int) -> bool:
    """`ping -c1` до гостя с hub'а. True — ответил. Ошибки → False."""
    try:
        rc, out, _err = await ssh.run(
            f"ping -c 1 -W {timeout} {guest_ip}",
        )
    except Exception:  # noqa: BLE001
        return False
    if rc == 0:
        # latency не пишем в БД (у ВМ нет колонок latency), но парсим для лога.
        latency = _parse_ping_latency((out or "").encode("utf-8", "replace"))
        if latency is not None:
            logger.debug("vm.status ping %s: %sms", guest_ip, latency)
        return True
    return False


async def _probe_ssh(ssh, guest_ip: str, port: int, timeout: int) -> bool:
    """TCP-проба SSH-порта гостя с hub'а (bash /dev/tcp). True — порт открыт."""
    probe = (
        f"timeout {timeout} bash -c "
        f"'exec 3<>/dev/tcp/{guest_ip}/{port}' 2>/dev/null"
    )
    try:
        rc, _out, _err = await ssh.run(probe)
    except Exception:  # noqa: BLE001
        return False
    return rc == 0


@broker.task("vm.status")
async def vm_status(task_id: str) -> None:
    """Снять три сигнала статуса ВМ: питание (domstate) + ping + ssh гостя.

    Что делает: заходит на hub по управляющей SSH-сессии, читает
    `virsh domstate <vm>` (питание), определяет LAN-адрес гостя (payload либо
    `domifaddr`) и с hub'а пингует его и пробит SSH-порт. Все три сигнала
    независимы; недоступность гостя не роняет пробу. Итог докладывает
    server_service (`vms/{id}/state` с power_state/ping_reachable/ssh_reachable).

    Параметры: `task_id`. Payload — `vm_id`, `hub_host` (str ip), `vm_name`/
    `name`, опц. `guest_ip`/`ip_address` (LAN-адрес гостя), `guest_ssh_port`,
    management-хинты.

    Возвращает: `{vm_id, vm_name, power_state, ping_reachable, ssh_reachable}`.

    NAT/SLIRP-ВМ без LAN-адреса → ping/ssh reachable=False (не ошибка), питание
    всё равно снимается. Read-only: недоступный BMC/гость не переводит таску в
    retry.

    Связано с: `vm.status` audit action; периодик `vms.status_sweep`.
    """
    async def _impl(payload: dict) -> dict:
        vm_id = payload["vm_id"]
        target_dept = payload.get("target_department_id")
        host_label = str(
            payload.get("host") or payload.get("hub_host")
            or payload.get("hub_server_id") or "hub",
        )
        vm_name = validate_name(
            payload.get("vm_name") or payload["name"], host_label, "vm_name",
        )
        settings = get_settings()
        ping_timeout = max(1, int(round(
            settings.power_reachability_ping_timeout_seconds,
        )))
        tcp_timeout = max(1, int(round(
            settings.power_reachability_tcp_timeout_seconds,
        )))
        ssh_port = payload.get("guest_ssh_port") or _GUEST_SSH_PORT

        session, _host = await open_hub_session(payload)
        async with session as ssh:
            power_state = await _probe_power(ssh, vm_name)
            guest_ip = await _guest_ip_best_effort(ssh, vm_name, payload)
            if guest_ip:
                ping_reachable = await _probe_ping(ssh, guest_ip, ping_timeout)
                ssh_reachable = await _probe_ssh(
                    ssh, guest_ip, int(ssh_port), tcp_timeout,
                )
            else:
                # SLIRP/NAT либо гость ещё без адреса — из LAN недостижим.
                ping_reachable = False
                ssh_reachable = False

        await server_service_client.submit_vm_state(
            vm_id, target_department_id=target_dept,
            power_state=power_state,
            ping_reachable=ping_reachable,
            ssh_reachable=ssh_reachable,
        )
        return {
            "vm_id": vm_id,
            "vm_name": vm_name,
            "power_state": power_state,
            "ping_reachable": ping_reachable,
            "ssh_reachable": ssh_reachable,
        }

    await run_task(
        task_id,
        audit_action="vm.status",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_STATUS,
    )
