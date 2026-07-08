"""Задачи VM-менеджера: автозапуск, teardown hub'а и подготовка консоли — по SSH.

Надстройка над базовыми VM-тасками (`tasks/vms.py`): та же управляющая
hub-сессия и sudo NOPASSWD (libvirt/kvm без пароля). Хендлеры:

* `vm.set_autostart` — `virsh autostart <vm>` / `virsh autostart --disable <vm>`
  по флагу `autostart`; callback зеркалит `autostart` в строку ВМ.
* `vms_hub.teardown` — обратная операция к `vms_hub.prepare` (порт старого
  `rm-vms-hub`): гасим и удаляем ВМ отдела (`virsh destroy` +
  `virsh undefine --remove-all-storage`), сносим storage-pool'ы и образы
  (`virsh pool-destroy/undefine` + `rm -rf <pool>`), опционально выпиливаем
  пакеты виртуализации (`apt purge astra-kvm` / `dnf remove`) и мост `br0`.
* `vm.console_prep` — убедиться, что у домена есть VNC-graphics (и, по запросу,
  serial-консоль), и вернуть TCP-порт VNC (`virsh vncdisplay`) для
  websockify/noVNC-прокси.

Длинные операции идут как `astra_update`: без per-команда timeout'а,
durable-retry на уровне `_runner`. Исход докладывается server_service через
internal-callback'и.
"""

from __future__ import annotations

import logging

from src.clients.ssh import SshError
from src.core.constants import (
    VMS_ADDITIONAL_POOL_NAME,
    VMS_DEFAULT_POOL_PATH,
    VMS_POOL_NAME,
    VMS_VNC_DEFAULT_LISTEN,
)
from src.main import broker
from src.services import server_service_client
from src.tasks._runner import run_task
from src.tasks._vms_helpers import (
    open_hub_session,
    parse_display_uri,
    parse_vncdisplay,
    run_hub_cmd,
    validate_iface,
    validate_ip,
    validate_name,
    validate_path,
)

logger = logging.getLogger(__name__)


AUDIT_SAFE_FIELDS_AUTOSTART: set[str] = {"vm_id", "vm_name", "autostart"}
AUDIT_SAFE_FIELDS_TEARDOWN: set[str] = {
    "server_id", "torn_down", "removed_vms", "purged", "bridge_removed",
}
AUDIT_SAFE_FIELDS_CONSOLE: set[str] = {
    "vm_id", "vm_name", "graphics_type", "vnc_port", "spice_port",
    "vnc_listen", "serial_ready",
}


def _host_label(payload: dict) -> str:
    """Адрес hub'а для сообщений об ошибке (до открытия сессии)."""
    return str(
        payload.get("host") or payload.get("hub_host")
        or payload.get("hub_server_id") or payload.get("server_id") or "hub",
    )


# ── vm.set_autostart ─────────────────────────────────────────────────────────


@broker.task("vm.set_autostart")
async def vm_set_autostart(task_id: str) -> None:
    """Включить/выключить автозапуск ВМ при старте hub'а (`virsh autostart`).

    Что делает: заходит на hub по SSH и метит домен на автозапуск libvirtd
    (`virsh autostart <vm>`) либо снимает метку (`virsh autostart --disable
    <vm>`) по флагу `autostart`. Исход докладывает server_service
    (`vms/{id}/state{autostart}`).

    Параметры: `task_id`. Payload — `vm_id`, `vm_name`/`name`, `autostart` (bool,
    исторически `enabled`), hub-блок, `target_department_id`.

    Возвращает: `{vm_id, vm_name, autostart}`.

    Возможные ошибки: `VM_INVALID_ARG` (нет/не-bool `autostart`),
    `VM_AUTOSTART_FAILED` (virsh упал). На ошибке — best-effort
    `vms/{id}/state{error}`.
    """
    async def _impl(payload: dict) -> dict:
        vm_id = payload["vm_id"]
        target_dept = payload.get("target_department_id")
        host_label = _host_label(payload)
        vm_name = validate_name(
            payload.get("vm_name") or payload["name"], host_label, "vm_name",
        )
        enabled = payload.get("autostart")
        if enabled is None:
            enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            raise SshError(
                error_code="VM_INVALID_ARG", host=host_label,
                message="autostart должен быть булевым флагом",
            )

        try:
            session, host = await open_hub_session(payload)
            async with session as ssh:
                cmd = (
                    f"virsh autostart {vm_name}" if enabled
                    else f"virsh autostart --disable {vm_name}"
                )
                await run_hub_cmd(
                    ssh, cmd, host, "VM_AUTOSTART_FAILED",
                    "virsh autostart упал",
                )
        except Exception as exc:
            error_text = getattr(exc, "error_code", type(exc).__name__)
            try:
                await server_service_client.submit_vm_state(
                    vm_id, target_department_id=target_dept,
                    error=str(error_text),
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "vm.set_autostart failed-callback errored vm_id=%s", vm_id,
                    exc_info=True,
                )
            raise

        await server_service_client.submit_vm_state(
            vm_id, target_department_id=target_dept, autostart=enabled,
        )
        return {"vm_id": vm_id, "vm_name": vm_name, "autostart": enabled}

    await run_task(
        task_id,
        audit_action="vm.set_autostart",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_AUTOSTART,
    )


# ── vms_hub.teardown ─────────────────────────────────────────────────────────


def _purge_packages_cmd(os_family: str) -> str:
    """Команда выпиливания пакетов виртуализации hub'а (apt|dnf).

    apt — purge мета-пакета `astra-kvm` (тянет за собой qemu/libvirt) +
    autoremove осиротевших зависимостей; dnf — remove qemu/libvirt.
    """
    if os_family == "apt":
        return (
            "sh -c 'DEBIAN_FRONTEND=noninteractive apt-get purge -y astra-kvm && "
            "DEBIAN_FRONTEND=noninteractive apt-get autoremove -y'"
        )
    return "dnf remove -y qemu-kvm libvirt"


async def _teardown_pool(ssh, name: str) -> None:
    """Best-effort снести storage-pool: pool-destroy + pool-undefine.

    Оба идут без проверки кода возврата — пул может быть уже неактивен или
    отсутствовать (повторный teardown), это не ошибка. Файлы образов сносит
    отдельный `rm -rf` пути пула в вызывающем коде.
    """
    await ssh.run(f"virsh pool-destroy {name}", sudo=True)
    await ssh.run(f"virsh pool-undefine {name}", sudo=True)


async def _remove_bridge(ssh, os_family: str) -> None:
    """Best-effort снять мост `br0`, поднятый при prepare (ОСТОРОЖНО).

    Убираем ifupdown drop-in (apt) либо NetworkManager-соединения (dnf) и
    гасим/удаляем сам линк. Всё без проверки кода возврата: teardown вызывается
    при выводе сервера из роли hub'а, адресацию физического NIC оператор
    восстанавливает отдельно.
    """
    if os_family == "apt":
        await ssh.run("rm -f /etc/network/interfaces.d/dbos-br0.cfg", sudo=True)
    else:
        await ssh.run("nmcli con delete br0", sudo=True)
        await ssh.run("nmcli con delete dbos-br0-slave", sudo=True)
    await ssh.run(
        "sh -c 'ip link set br0 down 2>/dev/null; "
        "ip link delete br0 type bridge 2>/dev/null || true'",
        sudo=True,
    )


@broker.task("vms_hub.teardown")
async def vms_hub_teardown(task_id: str) -> None:
    """Разобрать VMS-hub (обратная к `vms_hub.prepare`, порт `rm-vms-hub`).

    Что делает: заходит на hub по SSH под управляющей учёткой и по порядку —
    (1) гасит и удаляет перечисленные домены отдела (`virsh destroy` +
    `virsh undefine --remove-all-storage --snapshots-metadata`), (2) сносит
    storage-pool'ы `additional` и `vms` (`virsh pool-destroy/undefine`) и
    удаляет каталог пула с образами (`rm -rf <pool>`), (3) при
    `purge_packages` выпиливает пакеты виртуализации (`apt purge astra-kvm` /
    `dnf remove`), (4) при `remove_bridge` снимает мост `br0`. Исход докладывает
    server_service (`servers/{id}/vms-hub-teardown`).

    Параметры: `task_id`. Payload — `server_id`/`hub_server_id`, `host`/`hub_host`,
    `os_family` (`apt`|`dnf`), опц. `vms` (список имён доменов отдела),
    `storage_pool_path` (деф. `/vms`), `purge_packages` (bool),
    `remove_bridge` (bool), hub-блок, `target_department_id`.

    Возвращает: `{server_id, torn_down, removed_vms, purged, bridge_removed}`.

    Возможные ошибки: `VM_INVALID_ARG`, `VMS_HUB_TEARDOWN_FAILED`. На любой
    ошибке — best-effort `servers/{id}/vms-hub-teardown{torn_down:false, error}`.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload.get("hub_server_id") or payload.get("server_id")
        target_dept = payload.get("target_department_id")
        host_label = _host_label(payload)
        os_family = payload.get("os_family", "apt")
        if os_family not in ("apt", "dnf"):
            raise SshError(
                error_code="VM_INVALID_ARG", host=host_label,
                message=f"os_family {os_family!r} должен быть apt или dnf",
            )
        pool_path = validate_path(
            payload.get("storage_pool_path", VMS_DEFAULT_POOL_PATH), host_label,
        )
        vm_names = [
            validate_name(str(n), host_label, "vm_name")
            for n in (payload.get("vms") or [])
            if isinstance(n, str) and n.strip()
        ]
        purge = payload.get("purge_packages") is True
        remove_bridge = payload.get("remove_bridge") is True
        if remove_bridge and payload.get("phy_if"):
            # phy_if необязателен для сноса br0, но если задан — валидируем
            # (защита от подстановки метасимволов, симметрия prepare).
            validate_iface(payload["phy_if"], host_label)

        removed_vms: list[str] = []
        try:
            session, host = await open_hub_session(payload)
            async with session as ssh:
                for name in vm_names:
                    # destroy может отдать non-zero на уже выключенном домене —
                    # это не ошибка, глушим.
                    await ssh.run(f"virsh destroy {name}", sudo=True)
                    await run_hub_cmd(
                        ssh,
                        f"virsh undefine {name} --remove-all-storage "
                        "--snapshots-metadata",
                        host, "VMS_HUB_TEARDOWN_FAILED",
                        f"не удалось удалить домен {name}",
                        ok=(0, 1),
                    )
                    removed_vms.append(name)
                # Пулы: сперва data-диски, затем основной пул образов.
                await _teardown_pool(ssh, VMS_ADDITIONAL_POOL_NAME)
                await _teardown_pool(ssh, VMS_POOL_NAME)
                await ssh.run(f"rm -rf {pool_path}", sudo=True)
                if purge:
                    await run_hub_cmd(
                        ssh, _purge_packages_cmd(os_family), host,
                        "VMS_HUB_TEARDOWN_FAILED",
                        "выпиливание пакетов виртуализации упало",
                    )
                if remove_bridge:
                    await _remove_bridge(ssh, os_family)
        except Exception as exc:
            error_text = getattr(exc, "error_code", type(exc).__name__)
            try:
                await server_service_client.submit_vms_hub_torn_down(
                    server_id, torn_down=False, target_department_id=target_dept,
                    removed_vms=removed_vms, error=str(error_text),
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "vms_hub.teardown failed-callback errored server_id=%s",
                    server_id, exc_info=True,
                )
            raise

        # server_service чистит карточки ВМ и снимает роль hub'а синхронно при
        # dispatch'е, так что финальный ack — best-effort: хост уже разобран,
        # ронять успешную таску из-за недоступного (или ещё не заведённого)
        # callback-эндпоинта смысла нет.
        try:
            await server_service_client.submit_vms_hub_torn_down(
                server_id, torn_down=True, target_department_id=target_dept,
                removed_vms=removed_vms,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "vms_hub.teardown torn-down ack errored server_id=%s",
                server_id, exc_info=True,
            )
        return {
            "server_id": server_id,
            "torn_down": True,
            "removed_vms": removed_vms,
            "purged": purge,
            "bridge_removed": remove_bridge,
        }

    await run_task(
        task_id,
        audit_action="vms_hub.teardown",
        audit_target_type="server",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_TEARDOWN,
    )


# ── vm.console_prep ──────────────────────────────────────────────────────────


def _has_graphics_type(xml: str, gfx_type: str) -> bool:
    """Есть ли у домена graphics нужного типа (`vnc`|`spice`) по dumpxml."""
    lowered = (xml or "").lower()
    return "graphics" in lowered and gfx_type in lowered


def _has_serial(xml: str) -> bool:
    """Есть ли у домена serial-устройство/консоль (по dumpxml)."""
    lowered = (xml or "").lower()
    return "<serial" in lowered or "<console" in lowered


@broker.task("vm.console_prep")
async def vm_console_prep(task_id: str) -> None:
    """Подготовить консоль ВМ: vnc/spice-graphics (+ serial) и вернуть порт.

    Что делает: заходит на hub по SSH, читает `virsh dumpxml <vm>`; если у
    домена нет graphics запрошенного типа — добавляет его (`virt-xml
    --add-device --graphics type=<vnc|spice>,listen=<listen>,port=-1`, autoport);
    если запрошена serial-консоль и её нет — добавляет `--serial pty`. Затем
    читает TCP-порт дисплея: для vnc — `virsh vncdisplay <vm>` (`5900 + display`),
    для spice — `virsh domdisplay --type spice <vm>` (реальный порт из URI).
    Порт дисплея докладывает server_service полем `graphics_port` в
    `vms/{id}/state`; server_service кладёт его в `vms.graphics_port` и в
    консольный токен, по которому прокси проксирует vnc/spice.

    Параметры: `task_id`. Payload — `vm_id`, `vm_name`/`name`, опц. `graphics`
    (`vnc`|`spice`, деф. vnc), `vnc_listen` (адрес прослушивания, деф. `0.0.0.0`),
    `serial` (bool, деф. True), hub-блок, `target_department_id`.

    Возвращает: `{vm_id, vm_name, graphics_type, vnc_port, spice_port,
    vnc_listen, serial_ready}` (порт неиспользуемого типа — `None`).

    Возможные ошибки: `VM_INVALID_ARG`, `VM_CONSOLE_PREP_FAILED`. На любой
    ошибке — best-effort `vms/{id}/state{error}`.
    """
    async def _impl(payload: dict) -> dict:
        vm_id = payload["vm_id"]
        target_dept = payload.get("target_department_id")
        host_label = _host_label(payload)
        vm_name = validate_name(
            payload.get("vm_name") or payload["name"], host_label, "vm_name",
        )
        listen = validate_ip(
            str(payload.get("vnc_listen") or VMS_VNC_DEFAULT_LISTEN), host_label,
        )
        graphics = payload.get("graphics", "vnc")
        if graphics not in ("vnc", "spice"):
            raise SshError(
                error_code="VM_INVALID_ARG", host=host_label,
                message=f"graphics {graphics!r} должен быть vnc или spice",
            )
        want_serial = payload.get("serial", True) is not False

        vnc_port: int | None = None
        spice_port: int | None = None
        try:
            session, host = await open_hub_session(payload)
            async with session as ssh:
                _rc, xml, _err = await ssh.run(
                    f"virsh dumpxml {vm_name}", sudo=True,
                )
                if not _has_graphics_type(xml, graphics):
                    await run_hub_cmd(
                        ssh,
                        f"virt-xml {vm_name} --add-device "
                        f"--graphics type={graphics},listen={listen},port=-1",
                        host, "VM_CONSOLE_PREP_FAILED",
                        f"не удалось добавить {graphics}-graphics домену",
                    )
                if want_serial and not _has_serial(xml):
                    await run_hub_cmd(
                        ssh,
                        f"virt-xml {vm_name} --add-device --serial pty",
                        host, "VM_CONSOLE_PREP_FAILED",
                        "не удалось добавить serial-консоль домену",
                    )
                if graphics == "spice":
                    _rc, disp_out, _err = await ssh.run(
                        f"virsh domdisplay --type spice {vm_name}", sudo=True,
                    )
                    spice_port = parse_display_uri(disp_out)
                else:
                    _rc, vnc_out, _err = await ssh.run(
                        f"virsh vncdisplay {vm_name}", sudo=True,
                    )
                    vnc_port = parse_vncdisplay(vnc_out)
        except Exception as exc:
            error_text = getattr(exc, "error_code", type(exc).__name__)
            try:
                await server_service_client.submit_vm_state(
                    vm_id, target_department_id=target_dept,
                    error=str(error_text),
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "vm.console_prep failed-callback errored vm_id=%s", vm_id,
                    exc_info=True,
                )
            raise

        graphics_port = spice_port if graphics == "spice" else vnc_port
        await server_service_client.submit_vm_state(
            vm_id, target_department_id=target_dept,
            graphics_port=graphics_port,
        )
        return {
            "vm_id": vm_id,
            "vm_name": vm_name,
            "graphics_type": graphics,
            "vnc_port": vnc_port,
            "spice_port": spice_port,
            "vnc_listen": listen,
            "serial_ready": want_serial,
        }

    await run_task(
        task_id,
        audit_action="vm.console_prep",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_CONSOLE,
    )
