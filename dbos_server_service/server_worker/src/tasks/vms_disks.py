"""Задачи VM-менеджера: диски ВМ и правка cpu/ram — исполнение по SSH на hub'е.

Волна 2 поверх базовых VM-тасок (`tasks/vms.py`): те же управляющая hub-сессия
и sudo NOPASSWD (libvirt/kvm без пароля), гость по `sshpass` (`u`/`1`). Четыре
handler'а:

* `vm.disk_attach` — dir-pool `additional`, `qemu-img create`, `virsh
  attach-disk --persistent --targetbus virtio --serial <vm>_<disk>`; опц.
  mkfs+fstab в госте по `virtio-<serial>`.
* `vm.disk_delete` — `virsh detach-disk --persistent` + `rm` qcow2.
* `vm.disk_resize` — stop ВМ, `qemu-img resize`, start, `growpart`/`resize2fs`
  в госте.
* `vm.update` — stop, `virsh dumpxml` → правка `<vcpu>`/`<memory>` → `virsh
  define` → start → verify `running`.

Длинные операции идут как `astra_update`: без per-команда timeout'а,
durable-retry на уровне `_runner`. Исход докладывается server_service
через internal-callback'и.
"""

from __future__ import annotations

import logging

from src.clients.ssh import SshError
from src.core.constants import VMS_DEFAULT_POOL_PATH
from src.main import broker
from src.services import server_service_client
from src.tasks._runner import run_task
from src.tasks._vms_helpers import (
    ensure_additional_pool,
    guest_ssh,
    map_domstate,
    next_target_dev,
    open_hub_session,
    positive_int,
    resolve_guest_ip,
    run_hub_cmd,
    validate_name,
    validate_path,
    validate_target_dev,
)

logger = logging.getLogger(__name__)


AUDIT_SAFE_FIELDS_DISK: set[str] = {
    "vm_id", "disk_id", "target_dev", "path", "serial", "size_gb", "state",
}
AUDIT_SAFE_FIELDS_UPDATE: set[str] = {
    "vm_id", "vm_name", "cpu", "ram_mb", "power_state",
}


def _host_label(payload: dict) -> str:
    """Адрес hub'а для сообщений об ошибке (до открытия сессии)."""
    return str(
        payload.get("host") or payload.get("hub_host")
        or payload.get("hub_server_id") or "hub",
    )


async def _report_disk_error(
    vm_id: str, disk_id: str, target_dept: str | None, action: str, exc: Exception,
) -> None:
    """Best-effort callback `state='error'` на провал disk-таски."""
    error_text = getattr(exc, "error_code", type(exc).__name__)
    try:
        await server_service_client.submit_vm_disk_state(
            vm_id, disk_id, state="error", target_department_id=target_dept,
            error=str(error_text),
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "%s failed-callback errored vm_id=%s disk_id=%s",
            action, vm_id, disk_id, exc_info=True,
        )


# ── vm.disk_attach ───────────────────────────────────────────────────────────


@broker.task("vm.disk_attach")
async def vm_disk_attach(task_id: str) -> None:
    """Подключить дополнительный диск к ВМ на hub'е.

    Что делает: поднимает dir-pool `additional` (`<pool>/additional_disk`),
    создаёт qcow2 нужного размера (`qemu-img create`), выбирает свободный
    virtio-таргет (`virsh domblklist --details`) и цепляет диск
    (`virsh attach-disk --persistent --targetbus virtio --serial <vm>_<disk>
    --subdriver qcow2`). Если задан `fs` — форматирует диск в госте
    (`mkfs.<fs>` по `/dev/disk/by-id/virtio-<serial>`); если задан `mount` —
    добавляет запись в `/etc/fstab` и монтирует. Исход докладывает
    server_service (`vms/{id}/disks/{disk_id}/state`).

    Параметры: `task_id`. Payload — `vm_id`, `disk_id`, `vm_name`/`name`,
    `size_gb`, опц. `disk_name` (деф. `disk_id`), `serial` (деф.
    `<vm_name>_<disk_name>`), `fs`, `mount`, `guest_ip`/`ip_address`,
    `storage_pool_path`, hub-блок.

    Возвращает: `{vm_id, disk_id, target_dev, path, serial, size_gb, state}`.

    Возможные ошибки: `VM_INVALID_ARG`, `VM_DISK_POOL_FAILED`,
    `VM_DISK_ATTACH_FAILED`, `VM_DISK_NO_FREE_SLOT`, `VM_GUEST_NO_IP`. На любой
    ошибке — best-effort `state='error'`.
    """
    async def _impl(payload: dict) -> dict:
        vm_id = payload["vm_id"]
        disk_id = payload["disk_id"]
        target_dept = payload.get("target_department_id")
        host_label = _host_label(payload)
        vm_name = validate_name(
            payload.get("vm_name") or payload["name"], host_label, "vm_name",
        )
        size_gb = positive_int(payload["size_gb"], host_label, "size_gb")
        disk_name = validate_name(
            str(payload.get("disk_name") or disk_id), host_label, "disk_name",
        )
        pool_path = validate_path(
            payload.get("storage_pool_path", VMS_DEFAULT_POOL_PATH), host_label,
        )
        serial = validate_name(
            str(payload.get("serial") or f"{vm_name}_{disk_name}"),
            host_label, "serial",
        )
        fs = payload.get("fs")
        mount = payload.get("mount")
        if fs:
            fs = validate_name(str(fs), host_label, "fs")
        if mount:
            mount = validate_path(str(mount), host_label)

        target_dev = ""
        path = ""
        try:
            session, host = await open_hub_session(payload)
            async with session as ssh:
                pool_dir = await ensure_additional_pool(ssh, host, pool_path)
                path = f"{pool_dir}/{serial}.qcow2"
                await run_hub_cmd(
                    ssh, f"qemu-img create -f qcow2 {path} {size_gb}G", host,
                    "VM_DISK_ATTACH_FAILED", "qemu-img create упал",
                )
                _rc, blk_out, _err = await ssh.run(
                    f"virsh domblklist {vm_name} --details", sudo=True,
                )
                target_dev = next_target_dev(blk_out, host)
                await run_hub_cmd(
                    ssh,
                    f"virsh attach-disk {vm_name} {path} {target_dev} "
                    f"--persistent --targetbus virtio --serial {serial} "
                    "--subdriver qcow2",
                    host, "VM_DISK_ATTACH_FAILED", "virsh attach-disk упал",
                )
                if fs:
                    guest_ip = await resolve_guest_ip(ssh, host, vm_name, payload)
                    dev = f"/dev/disk/by-id/virtio-{serial}"
                    await run_hub_cmd(
                        ssh, guest_ssh(guest_ip, f"mkfs.{fs} -F {dev}", sudo=True),
                        host, "VM_DISK_ATTACH_FAILED", "mkfs в госте упал",
                    )
                    if mount:
                        fstab = f"{dev} {mount} {fs} defaults 0 2"
                        guest_cmd = (
                            f"bash -c 'mkdir -p {mount}; "
                            f"grep -q virtio-{serial} /etc/fstab || "
                            f'echo "{fstab}" >> /etc/fstab; '
                            "mount -a'"
                        )
                        await run_hub_cmd(
                            ssh, guest_ssh(guest_ip, guest_cmd, sudo=True), host,
                            "VM_DISK_ATTACH_FAILED",
                            "монтирование диска в госте упало",
                        )
        except Exception as exc:
            await _report_disk_error(vm_id, disk_id, target_dept, "vm.disk_attach", exc)
            raise

        await server_service_client.submit_vm_disk_state(
            vm_id, disk_id, state="ready", target_department_id=target_dept,
            target_dev=target_dev, path=path, serial=serial, size_gb=size_gb,
        )
        return {
            "vm_id": vm_id,
            "disk_id": disk_id,
            "target_dev": target_dev,
            "path": path,
            "serial": serial,
            "size_gb": size_gb,
            "state": "ready",
        }

    await run_task(
        task_id,
        audit_action="vm.disk_attach",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_DISK,
    )


# ── vm.disk_delete ───────────────────────────────────────────────────────────


@broker.task("vm.disk_delete")
async def vm_disk_delete(task_id: str) -> None:
    """Отключить и удалить диск ВМ на hub'е.

    Что делает: `virsh detach-disk <vm> <target> --persistent` (отвязать из
    конфига домена), затем `rm -f <path>` (снести qcow2 из пула). Исход
    докладывает server_service (`vms/{id}/disks/{disk_id}/state{deleted}`).

    Параметры: `task_id`. Payload — `vm_id`, `disk_id`, `vm_name`/`name`,
    `target_dev` (`vd<x>`), `path`, hub-блок.

    Возвращает: `{vm_id, disk_id, state}`.

    Возможные ошибки: `VM_INVALID_ARG`, `VM_DISK_DELETE_FAILED`. На ошибке —
    best-effort `state='error'`.
    """
    async def _impl(payload: dict) -> dict:
        vm_id = payload["vm_id"]
        disk_id = payload["disk_id"]
        target_dept = payload.get("target_department_id")
        host_label = _host_label(payload)
        vm_name = validate_name(
            payload.get("vm_name") or payload["name"], host_label, "vm_name",
        )
        target_dev = validate_target_dev(payload["target_dev"], host_label)
        path = validate_path(payload["path"], host_label)

        try:
            session, host = await open_hub_session(payload)
            async with session as ssh:
                await run_hub_cmd(
                    ssh, f"virsh detach-disk {vm_name} {target_dev} --persistent",
                    host, "VM_DISK_DELETE_FAILED", "virsh detach-disk упал",
                )
                await ssh.run(f"rm -f {path}", sudo=True)
        except Exception as exc:
            await _report_disk_error(vm_id, disk_id, target_dept, "vm.disk_delete", exc)
            raise

        await server_service_client.submit_vm_disk_state(
            vm_id, disk_id, state="deleted", target_department_id=target_dept,
        )
        return {"vm_id": vm_id, "disk_id": disk_id, "state": "deleted"}

    await run_task(
        task_id,
        audit_action="vm.disk_delete",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_DISK,
    )


# ── vm.disk_resize ───────────────────────────────────────────────────────────


@broker.task("vm.disk_resize")
async def vm_disk_resize(task_id: str) -> None:
    """Увеличить диск ВМ на hub'е.

    Что делает: гасит ВМ (`virsh destroy` — qemu-img не трогает занятый образ),
    растит qcow2 (`qemu-img resize <path> <G>G`), поднимает ВМ обратно
    (`virsh start`) и растягивает раздел/ФС в госте
    (`growpart /dev/<target_dev> 1` + `resize2fs /dev/<target_dev>1`). Порядок
    именно такой: growpart/resize2fs исполняются в госте, поэтому идут ПОСЛЕ
    старта. Исход докладывает server_service
    (`vms/{id}/disks/{disk_id}/state{ready, size_gb}`).

    Параметры: `task_id`. Payload — `vm_id`, `disk_id`, `vm_name`/`name`,
    `path`, `size_gb`, опц. `target_dev` (деф. `vda`), `guest_ip`/`ip_address`,
    hub-блок.

    Возвращает: `{vm_id, disk_id, size_gb, power_state, state}`.

    Возможные ошибки: `VM_INVALID_ARG`, `VM_DISK_RESIZE_FAILED`,
    `VM_GUEST_NO_IP`. На ошибке — best-effort `state='error'`.
    """
    async def _impl(payload: dict) -> dict:
        vm_id = payload["vm_id"]
        disk_id = payload["disk_id"]
        target_dept = payload.get("target_department_id")
        host_label = _host_label(payload)
        vm_name = validate_name(
            payload.get("vm_name") or payload["name"], host_label, "vm_name",
        )
        size_gb = positive_int(payload["size_gb"], host_label, "size_gb")
        path = validate_path(payload["path"], host_label)
        target_dev = validate_target_dev(
            str(payload.get("target_dev") or "vda"), host_label,
        )

        try:
            session, host = await open_hub_session(payload)
            async with session as ssh:
                # stop: qemu-img resize отказывается работать с занятым образом.
                await ssh.run(f"virsh destroy {vm_name}", sudo=True)
                await run_hub_cmd(
                    ssh, f"qemu-img resize {path} {size_gb}G", host,
                    "VM_DISK_RESIZE_FAILED", "qemu-img resize упал",
                )
                await run_hub_cmd(
                    ssh, f"virsh start {vm_name}", host,
                    "VM_DISK_RESIZE_FAILED", "ВМ не поднялась после resize",
                )
                guest_ip = await resolve_guest_ip(ssh, host, vm_name, payload)
                grow = (
                    f"bash -c 'growpart /dev/{target_dev} 1 || true; "
                    f"resize2fs /dev/{target_dev}1 || true'"
                )
                await ssh.run(guest_ssh(guest_ip, grow, sudo=True), sudo=True)
                _rc, dom_out, _err = await ssh.run(
                    f"virsh domstate {vm_name}", sudo=True,
                )
                power_state = map_domstate(dom_out)
        except Exception as exc:
            await _report_disk_error(vm_id, disk_id, target_dept, "vm.disk_resize", exc)
            raise

        await server_service_client.submit_vm_disk_state(
            vm_id, disk_id, state="ready", target_department_id=target_dept,
            size_gb=size_gb,
        )
        return {
            "vm_id": vm_id,
            "disk_id": disk_id,
            "size_gb": size_gb,
            "power_state": power_state,
            "state": "ready",
        }

    await run_task(
        task_id,
        audit_action="vm.disk_resize",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_DISK,
    )


# ── vm.update ────────────────────────────────────────────────────────────────


@broker.task("vm.update")
async def vm_update(task_id: str) -> None:
    """Изменить cpu/ram ВМ на hub'е.

    Что делает: читает `virsh domstate` и, если ВМ запущена, гасит её
    (`virsh destroy`); дампит XML (`virsh dumpxml > /tmp/<vm>.xml`); правит
    `<vcpu>` и `<memory>`/`<currentMemory>` (RAM в KiB = `ram_mb*1024`; unit у
    `<memory>` опущен — libvirt по умолчанию читает KiB); переопределяет домен
    (`virsh define`), поднимает (`virsh start`) и проверяет `running`. Исход
    докладывает server_service (`vms/{id}/state{power_state}`, снятие
    lifecycle-lock).

    Параметры: `task_id`. Payload — `vm_id`, `vm_name`/`name`, хотя бы одно из
    `cpu`/`ram_mb`, hub-блок.

    Возвращает: `{vm_id, vm_name, cpu, ram_mb, power_state}`.

    Возможные ошибки: `VM_INVALID_ARG` (не задано ни cpu, ни ram_mb),
    `VM_UPDATE_FAILED` (dumpxml/define/start/verify упали). На ошибке —
    best-effort `vms/{id}/state{error}` + снятие lock.
    """
    async def _impl(payload: dict) -> dict:
        vm_id = payload["vm_id"]
        target_dept = payload.get("target_department_id")
        host_label = _host_label(payload)
        vm_name = validate_name(
            payload.get("vm_name") or payload["name"], host_label, "vm_name",
        )
        cpu = payload.get("cpu")
        ram_mb = payload.get("ram_mb")
        if cpu is None and ram_mb is None:
            raise SshError(
                error_code="VM_INVALID_ARG", host=host_label,
                message="vm.update требует хотя бы одно из cpu/ram_mb",
            )
        if cpu is not None:
            cpu = positive_int(cpu, host_label, "cpu")
        if ram_mb is not None:
            ram_mb = positive_int(ram_mb, host_label, "ram_mb")

        try:
            session, host = await open_hub_session(payload)
            async with session as ssh:
                _rc, dom_out, _err = await ssh.run(
                    f"virsh domstate {vm_name}", sudo=True,
                )
                if map_domstate(dom_out) == "on":
                    await ssh.run(f"virsh destroy {vm_name}", sudo=True)
                xml_path = f"/tmp/{vm_name}.xml"
                await run_hub_cmd(
                    ssh, f"sh -c 'virsh dumpxml {vm_name} > {xml_path}'", host,
                    "VM_UPDATE_FAILED", "virsh dumpxml упал",
                )
                if cpu is not None:
                    await run_hub_cmd(
                        ssh,
                        f"sed -i -E 's#<vcpu[^>]*>[0-9]+</vcpu>#"
                        f"<vcpu>{cpu}</vcpu>#' {xml_path}",
                        host, "VM_UPDATE_FAILED", "правка vcpu в XML упала",
                    )
                if ram_mb is not None:
                    kib = ram_mb * 1024
                    await run_hub_cmd(
                        ssh,
                        f"sed -i -E 's#<memory[^>]*>[0-9]+</memory>#"
                        f"<memory>{kib}</memory>#' {xml_path}",
                        host, "VM_UPDATE_FAILED", "правка memory в XML упала",
                    )
                    await run_hub_cmd(
                        ssh,
                        f"sed -i -E 's#<currentMemory[^>]*>[0-9]+</currentMemory>#"
                        f"<currentMemory>{kib}</currentMemory>#' {xml_path}",
                        host, "VM_UPDATE_FAILED",
                        "правка currentMemory в XML упала",
                    )
                await run_hub_cmd(
                    ssh, f"virsh define {xml_path}", host,
                    "VM_UPDATE_FAILED", "virsh define упал",
                )
                await run_hub_cmd(
                    ssh, f"virsh start {vm_name}", host,
                    "VM_UPDATE_FAILED", "ВМ не поднялась после update",
                )
                _rc, dom_out2, _err2 = await ssh.run(
                    f"virsh domstate {vm_name}", sudo=True,
                )
                power_state = map_domstate(dom_out2)
                if power_state != "on":
                    raise SshError(
                        error_code="VM_UPDATE_FAILED", host=host,
                        message=(
                            f"ВМ {vm_name} не в состоянии running после update "
                            f"(domstate={power_state})"
                        ),
                    )
        except Exception as exc:
            error_text = getattr(exc, "error_code", type(exc).__name__)
            try:
                await server_service_client.submit_vm_state(
                    vm_id, target_department_id=target_dept,
                    status=None, error=str(error_text), clear_busy_state=True,
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "vm.update failed-callback errored vm_id=%s", vm_id,
                    exc_info=True,
                )
            raise

        await server_service_client.submit_vm_state(
            vm_id, target_department_id=target_dept,
            power_state=power_state, clear_busy_state=True,
        )
        return {
            "vm_id": vm_id,
            "vm_name": vm_name,
            "cpu": cpu,
            "ram_mb": ram_mb,
            "power_state": power_state,
        }

    await run_task(
        task_id,
        audit_action="vm.update",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_UPDATE,
    )
