"""Задачи VM-менеджера: bootstrap управления ВМ и настройка сети — по SSH.

Надстройка над базовыми VM-тасками. Та же управляющая hub-сессия (sudo NOPASSWD,
libvirt/kvm без пароля), гость — по `sshpass` (`u`/`1`), пока не переведён на
управляющую пару. Хендлеры:

* `vm.prepare` — зеркало серверного `server.prepare`, но на госте: заходим
  дефолтными кредами образа (`u`/`1`), заводим управляющего пользователя,
  кладём ему публичный ключ (сгенерён server_service, приехал в dispatch-stash),
  ставим пароль, даём sudo NOPASSWD, хардим sshd, проверяем вход по ключу и
  только после подтверждения удаляем базовую учётку `u`. Порядок безопасный —
  доступ не теряем до успешной проверки нового входа.
* `vm.set_network` — перевод ВМ на статику в боевом LAN (bridge `br0`) или в
  приватную NAT-подсеть хаба (host-only мост `natbr0`, `192.168.100.0/24`). Оба
  режима работают одинаково: пишем статику гостю (в живом госте по SSH
  переписываем `/etc/network/interfaces` — порт `provision.sh`/`static_ip.sh`;
  если гость по SSH недостижим — фолбэк через `virt-customize`, offline-правка
  qcow2-диска), переводим NIC домена на нужный мост (`virt-xml`) и рестартуем.
  Отличие NAT — детерминированный статик-адрес подсети natbr0 (`_nat_static_ip`,
  шлюз/DNS `192.168.100.1`); мост natbr0 на хабе гарантируется `_setup_nat_bridge`
  перед переводом. NAT-гость сидит на реальном мосту и достижим с хаба джампом
  (в отличие от старого SLIRP), поэтому `applied_ip` известен сразу.

Длинные операции идут как `astra_update`: без per-команда timeout'а,
durable-retry на уровне `_runner`. Исход докладывается server_service через
internal-callback'и (`vms/{id}/prepared`, `vms/{id}/state`).
"""

from __future__ import annotations

import asyncio
import logging

from src.clients.ssh import SshError
from src.core.constants import (
    VMS_NAT_BRIDGE,
    VMS_NAT_HOST_IP,
)
from src.main import broker
from src.services import redis_pool, server_service_client
from src.tasks._runner import run_task
from src.tasks._vm_prepare_helpers import (
    _delete_base_user,
    _delete_stash,
    _ensure_mgmt_user,
    _harden_guest_sshd,
    _install_authorized_key,
    _install_guest_agent,
    _install_sudoers,
    _set_mgmt_password,
    _shred_temp_key,
    _verify_key_login,
    _write_temp_key,
    load_mgmt_material,
)
from src.tasks._vms_helpers import (
    LIBVIRT_SESSION_ENV,
    VMS_DEFAULT_GATEWAY,
    VMS_DEFAULT_NETMASK,
    bridge_label,
    guest_ssh,
    map_domstate,
    normalize_dns,
    open_hub_session,
    resolve_guest_ip,
    run_hub_cmd,
    static_interfaces_lines,
    validate_ip,
    validate_name,
    validate_path,
    write_static_interfaces_offline,
)
from src.tasks.vms import _nat_static_ip, _setup_nat_bridge

logger = logging.getLogger(__name__)


AUDIT_SAFE_FIELDS_PREPARE: set[str] = {
    "vm_id", "vm_name", "management_user", "is_managed", "hardened",
}
AUDIT_SAFE_FIELDS_NETWORK: set[str] = {
    "vm_id", "vm_name", "network_mode", "ip_address", "power_state",
}

# Тайминги ожидания гостя/адреса после рестарта домена. Вынесены в модульные
# константы, чтобы тесты обнуляли задержки (реальный ребут гостя — минуты).
_GUEST_REBOOT_SETTLE_S = 15.0
_GUEST_REBOOT_POLL_DELAY_S = 10.0
_GUEST_REBOOT_MAX_POLLS = 60

# Проба доступности гостя по SSH до записи статики: несколько коротких попыток.
# Не поднялся за это окно → уходим в offline-фолбэк (virt-customize).
_GUEST_SSH_PROBE_ATTEMPTS = 12
_GUEST_SSH_PROBE_DELAY_S = 5.0

# Маркер «пароль-сессия bootstrap отработала и вход по ключу подтверждён».
# После него на госте выключается парольный вход (хардинг) и удаляется базовая
# учётка `u` — повторный bootstrap по паролю уже не зайдёт, поэтому retry
# пропускает пароль-часть и добивает только идемпотентный key-финализ. TTL
# крупно больше суммарного back-off retry'я.
_VERIFIED_MARKER_PREFIX = "dbos:vm_prepared_marker:"
_VERIFIED_MARKER_TTL_SECONDS = 3600


def _host_label(payload: dict) -> str:
    return str(
        payload.get("host") or payload.get("hub_host")
        or payload.get("hub_server_id") or "hub",
    )


async def _read_verified_marker(task_id: str) -> bool:
    client = redis_pool.get_redis()
    raw = await client.get(_VERIFIED_MARKER_PREFIX + task_id)
    return raw is not None


async def _set_verified_marker(task_id: str) -> None:
    client = redis_pool.get_redis()
    try:
        await client.set(
            _VERIFIED_MARKER_PREFIX + task_id, "1", ex=_VERIFIED_MARKER_TTL_SECONDS,
        )
    except Exception:  # noqa: BLE001
        logger.debug("failed to set vm.prepare verified marker", exc_info=True)


async def _delete_verified_marker(task_id: str) -> None:
    client = redis_pool.get_redis()
    try:
        await client.delete(_VERIFIED_MARKER_PREFIX + task_id)
    except Exception:  # noqa: BLE001
        logger.debug("failed to delete vm.prepare verified marker", exc_info=True)


@broker.task("vm.prepare")
async def vm_prepare(task_id: str) -> None:
    """Бутстрап управления ВМ: mgmt-учётка + ключ, удаление базовой учётки `u`.

    Что делает: заходит на гостя дефолтными кредами образа (`u`/`1`) из
    hub-сессии, заводит управляющего пользователя, ставит ему публичный ключ
    (сгенерён server_service, приехал в dispatch-stash) + пароль + sudo NOPASSWD,
    проверяет вход по ключу (анти-локаут), хардит sshd и только после этого
    удаляет базовую учётку `u`. Порядок безопасный — доступ не теряем до успешной
    проверки нового входа. Исход докладывает server_service (`vms/{id}/prepared`,
    `is_managed=True`).

    Параметры: `task_id`. Payload — `vm_id`, `vm_name`/`name`, `creds_stash_key`
    (ссылка на dispatch-stash с `{management_user, public_key, private_key,
    password}`), `guest_ip`/`ip_address` (адрес гостя, если не по `domifaddr`),
    опц. `harden_sshd` (деф. True), hub-блок, `target_department_id`.

    Возвращает: `{vm_id, vm_name, management_user, is_managed, hardened}`.

    Возможные ошибки: `VM_INVALID_ARG`, `DISPATCH_STASH_MISSING`,
    `VM_PREPARE_FAILED`, `VM_PREPARE_KEY_VERIFY_FAILED`, `VM_GUEST_NO_IP`. На
    любой ошибке — best-effort `vms/{id}/state{error}`.
    """
    async def _impl(payload: dict) -> dict:
        vm_id = payload["vm_id"]
        target_dept = payload.get("target_department_id")
        host_label = _host_label(payload)
        vm_name = validate_name(
            payload.get("vm_name") or payload["name"], host_label, "vm_name",
        )
        harden = payload.get("harden_sshd", True) is not False
        stash_key = payload.get("creds_stash_key")
        if not stash_key:
            raise SshError(
                error_code="DISPATCH_STASH_MISSING", host=host_label,
                message="payload has no creds_stash_key reference",
            )

        mgmt = await load_mgmt_material(stash_key, host_label)
        management_user = mgmt["management_user"]
        public_key = mgmt["public_key"]
        private_key = mgmt["private_key"]
        password = mgmt["password"]

        verified_before = await _read_verified_marker(task_id)

        try:
            session, host = await open_hub_session(payload)
            async with session as ssh:
                guest_ip = await resolve_guest_ip(ssh, host, vm_name, payload)
                key_path = await _write_temp_key(ssh, host, private_key)
                try:
                    if not verified_before:
                        # Пароль-сессия (`u`/`1`): пока парольный вход жив.
                        await _ensure_mgmt_user(ssh, host, guest_ip, management_user)
                        await _install_sudoers(ssh, host, guest_ip, management_user)
                        await _set_mgmt_password(
                            ssh, host, guest_ip, management_user, password,
                        )
                        await _install_authorized_key(
                            ssh, host, guest_ip, management_user, public_key,
                        )
                        # qemu-guest-agent для graceful shutdown/domifaddr —
                        # best-effort, пока держим парольный доступ к гостю.
                        await _install_guest_agent(ssh, host, guest_ip)
                        # Анти-локаут: доступ по ключу подтверждён до деструктива.
                        await _verify_key_login(
                            ssh, host, guest_ip, management_user, key_path,
                        )
                        await _set_verified_marker(task_id)
                    else:
                        # Retry после деструктива: пароль-часть пропускаем
                        # (парольный вход мог быть уже выключен), но вход по
                        # ключу перепроверяем.
                        await _verify_key_login(
                            ssh, host, guest_ip, management_user, key_path,
                        )
                    # Финализ под ключевой сессией — идемпотентно, доступ уже по
                    # ключу. Пароль на госте выключаем и базовую учётку сносим
                    # только здесь, после подтверждённого входа.
                    if harden:
                        await _harden_guest_sshd(
                            ssh, host, guest_ip, management_user, key_path,
                        )
                    await _delete_base_user(
                        ssh, host, guest_ip, management_user, key_path,
                    )
                finally:
                    await _shred_temp_key(ssh, key_path)
        except Exception as exc:
            error_text = getattr(exc, "error_code", type(exc).__name__)
            try:
                await server_service_client.submit_vm_state(
                    vm_id, target_department_id=target_dept,
                    error=str(error_text),
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "vm.prepare failed-callback errored vm_id=%s", vm_id,
                    exc_info=True,
                )
            raise

        await server_service_client.submit_vm_prepared(
            vm_id, management_user, target_department_id=target_dept,
        )
        await _delete_stash(stash_key)
        await _delete_verified_marker(task_id)
        return {
            "vm_id": vm_id,
            "vm_name": vm_name,
            "management_user": management_user,
            "is_managed": True,
            "hardened": harden,
        }

    await run_task(
        task_id,
        audit_action="vm.prepare",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_PREPARE,
    )


# ── vm.set_network ───────────────────────────────────────────────────────────


# DNS-нормализатор общий с `vm.create` — живёт в `_vms_helpers`.
_normalize_dns = normalize_dns


async def _write_static_interfaces(
    ssh, host: str, guest_ip: str, addr: str, netmask: str,
    gateway: str, dns: list[str],
) -> None:
    """Прописать статику гостю (`/etc/network/interfaces`) — порт `provision.sh`.

    Заходим по текущему адресу гостя (`u`/`1`) и переписываем сетевой конфиг на
    `addr`/`netmask` с `gateway`/`dns`. После перевода NIC домена на `br0` гость
    окажется в боевом LAN с этим адресом.
    """
    cfg = "\\n".join(static_interfaces_lines(addr, netmask, gateway, dns)) + "\\n"
    cmd = f"bash -c 'printf \"{cfg}\" > /etc/network/interfaces'"
    await run_hub_cmd(
        ssh, guest_ssh(guest_ip, cmd, sudo=True), host,
        "VM_NET_APPLY_FAILED", "не удалось прописать статику гостю",
    )


async def _guest_ssh_reachable(ssh, guest_ip: str) -> bool:
    """Проба доступности гостя по SSH (несколько коротких попыток)."""
    for _ in range(_GUEST_SSH_PROBE_ATTEMPTS):
        rc, _out, _err = await ssh.run(guest_ssh(guest_ip, "true"), sudo=True)
        if rc == 0:
            return True
        await asyncio.sleep(_GUEST_SSH_PROBE_DELAY_S)
    return False


def _first_qcow2_path(domblklist_out: str) -> str | None:
    """Первый qcow2-диск из вывода `virsh domblklist`.

    Формат таблицы libvirt (`Target`/`Source`): берём первый абсолютный путь,
    заканчивающийся на `.qcow2`. None — если диска нет (CDROM/пусто).
    """
    for raw in (domblklist_out or "").splitlines():
        for tok in raw.split():
            if tok.startswith("/") and tok.endswith(".qcow2"):
                return tok
    return None


async def _resolve_vm_disk(ssh, host: str, vm_name: str) -> str:
    """Путь qcow2-диска домена (`virsh domblklist`) для offline-правки."""
    _rc, out, _err = await ssh.run(
        f"{LIBVIRT_SESSION_ENV} virsh domblklist {vm_name}", sudo=True,
    )
    disk = _first_qcow2_path(out)
    if disk is None:
        raise SshError(
            error_code="VM_NET_APPLY_FAILED", host=host,
            message=f"не удалось определить qcow2-диск ВМ {vm_name} для offline-правки",
        )
    return validate_path(disk, host)


async def _apply_static_offline(
    ssh, host: str, vm_name: str, addr: str, netmask: str,
    gateway: str, dns: list[str],
) -> None:
    """Фолбэк статики через offline-правку диска (`virt-customize`).

    Гость недостижим по SSH → глушим домен, находим его qcow2-диск и заливаем
    готовый `/etc/network/interfaces` прямо в образ (libguestfs), не заходя в
    гостя. Домен остаётся выключенным — caller переводит NIC и стартует.
    """
    # offline-правка требует выключенного домена
    await ssh.run(f"{LIBVIRT_SESSION_ENV} virsh destroy {vm_name}", sudo=True)
    disk = await _resolve_vm_disk(ssh, host, vm_name)
    await write_static_interfaces_offline(
        ssh, host, disk, addr, netmask, gateway, dns,
    )


async def _switch_domain_network(
    ssh, host: str, vm_name: str, net_arg: str,
) -> None:
    """Перевести NIC домена на заданную сеть (`virt-xml --edit --network`)."""
    await run_hub_cmd(
        ssh, f"virt-xml {vm_name} --edit --network {net_arg}", host,
        "VM_NET_APPLY_FAILED", "не удалось перевести NIC ВМ на новую сеть",
    )


async def _restart_domain(ssh, host: str, vm_name: str) -> None:
    """Рестартнуть домен (`virsh destroy` → `virsh start`) для применения сети."""
    # может быть уже выключен — non-zero глушим
    await ssh.run(f"{LIBVIRT_SESSION_ENV} virsh destroy {vm_name}", sudo=True)
    await run_hub_cmd(
        ssh, f"virsh start {vm_name}", host,
        "VM_NET_APPLY_FAILED", "ВМ не поднялась после смены сети",
    )


async def _wait_guest_ssh(ssh, host: str, guest_ip: str) -> None:
    """Дождаться, пока гость снова отвечает по SSH на заданном адресе."""
    await asyncio.sleep(_GUEST_REBOOT_SETTLE_S)
    for _ in range(_GUEST_REBOOT_MAX_POLLS):
        rc, _out, _err = await ssh.run(guest_ssh(guest_ip, "true"), sudo=True)
        if rc == 0:
            return
        await asyncio.sleep(_GUEST_REBOOT_POLL_DELAY_S)
    raise SshError(
        error_code="VM_NET_APPLY_FAILED", host=host,
        message=f"гость {guest_ip} не поднялся после смены сети",
    )


async def _apply_static_and_switch(
    ssh, host: str, vm_name: str, payload: dict, net_arg: str,
    addr: str, netmask: str, gateway: str, dns: list[str],
) -> None:
    """Залить статику гостю и перевести NIC домена на заданный мост.

    Единый путь для bridge (`br0`) и NAT (`natbr0`): статику пишем по текущему
    адресу гостя (пока он ещё на старой сети) — в живом госте по SSH, а если он
    недостижим, offline-правкой диска (`virt-customize`, домен на этот момент
    гасится). Затем NIC переводится на `net_arg` (`virt-xml`), домен рестартится
    и ждём гостя по новому статик-адресу `addr`.
    """
    # Текущий адрес гостя для входа (bridge-DHCP/NAT), пока он на старой сети;
    # нет адреса — сразу offline-фолбэк (по SSH зайти всё равно некуда).
    try:
        guest_ip = await resolve_guest_ip(ssh, host, vm_name, payload)
    except SshError as exc:
        if exc.error_code != "VM_GUEST_NO_IP":
            raise
        guest_ip = None
    reachable = bool(guest_ip) and await _guest_ssh_reachable(ssh, guest_ip)
    if reachable:
        await _write_static_interfaces(
            ssh, host, guest_ip, addr, netmask, gateway, dns,
        )
    else:
        await _apply_static_offline(
            ssh, host, vm_name, addr, netmask, gateway, dns,
        )
    await _switch_domain_network(ssh, host, vm_name, net_arg)
    await _restart_domain(ssh, host, vm_name)
    await _wait_guest_ssh(ssh, host, addr)


@broker.task("vm.set_network")
async def vm_set_network(task_id: str) -> None:
    """Перевести ВМ на статику в LAN (bridge `br0`) или в NAT-подсеть (`natbr0`).

    Что делает: заходит на hub по SSH. Оба режима переписывают статику в госте
    (`/etc/network/interfaces`: address/netmask/gateway/dns, порт
    `provision.sh`/`static_ip.sh`); если гость по SSH недостижим — фолбэк через
    `virt-customize` (offline-заливка interfaces в qcow2-диск). Затем переводят
    NIC домена на нужный мост (`virt-xml`) и рестартуют домен. Для `bridge` адрес
    берётся из payload (`ip_address`), гость встаёт в боевом LAN. Для `nat`
    сначала гарантируется host-only мост `natbr0` (`_setup_nat_bridge`), адрес —
    детерминированный `_nat_static_ip` в подсети `192.168.100.0/24` (шлюз/DNS
    `192.168.100.1`); гость сидит на реальном мосту и достижим с хаба джампом.
    Исход докладывает server_service (`vms/{id}/state` с
    `ip_address`/`power_state`).

    Параметры: `task_id`. Payload — `vm_id`, `vm_name`/`name`, `network_mode`
    (`bridge`|`nat`); для `bridge` — `ip_address`, опц. `netmask`/`gateway`/`dns`;
    опц. `guest_ip` (текущий адрес гостя для входа до перевода NIC); hub-блок,
    `target_department_id`.

    Возвращает: `{vm_id, vm_name, network_mode, ip_address, power_state}`.

    Возможные ошибки: `VM_INVALID_ARG`, `VM_NET_APPLY_FAILED`, `VM_GUEST_NO_IP`.
    На любой ошибке — best-effort `vms/{id}/state{error}`.
    """
    async def _impl(payload: dict) -> dict:
        vm_id = payload["vm_id"]
        target_dept = payload.get("target_department_id")
        host_label = _host_label(payload)
        vm_name = validate_name(
            payload.get("vm_name") or payload["name"], host_label, "vm_name",
        )
        network_mode = payload.get("network_mode")
        if network_mode not in ("bridge", "nat"):
            raise SshError(
                error_code="VM_INVALID_ARG", host=host_label,
                message=f"network_mode {network_mode!r} должен быть bridge или nat",
            )

        applied_ip: str | None = None
        try:
            session, host = await open_hub_session(payload)
            async with session as ssh:
                if network_mode == "bridge":
                    raw_ip = payload.get("ip_address")
                    if not raw_ip:
                        raise SshError(
                            error_code="VM_INVALID_ARG", host=host,
                            message="bridge-режим требует ip_address",
                        )
                    addr = validate_ip(str(raw_ip), host).split("/")[0]
                    netmask = validate_ip(
                        str(payload.get("netmask") or VMS_DEFAULT_NETMASK), host,
                    )
                    gateway = validate_ip(
                        str(payload.get("gateway") or VMS_DEFAULT_GATEWAY), host,
                    )
                    dns = _normalize_dns(payload, host)
                    await _apply_static_and_switch(
                        ssh, host, vm_name, payload,
                        f"bridge={bridge_label()},model=virtio",
                        addr, netmask, gateway, dns,
                    )
                    applied_ip = addr
                else:
                    # NAT: гость на реальном мосту natbr0 со статикой (как в
                    # vm.create) — детерминированный адрес подсети natbr0, шлюз/DNS
                    # 192.168.100.1, наружу через MASQUERADE. Мост host-only, живой
                    # подъём не рвёт SSH. Гость достижим с хаба джампом, поэтому
                    # applied_ip известен сразу (в отличие от старого SLIRP).
                    await _setup_nat_bridge(ssh, host)
                    addr = _nat_static_ip(vm_name)
                    await _apply_static_and_switch(
                        ssh, host, vm_name, payload,
                        f"bridge={VMS_NAT_BRIDGE},model=virtio",
                        addr, VMS_DEFAULT_NETMASK, VMS_NAT_HOST_IP,
                        [VMS_NAT_HOST_IP],
                    )
                    applied_ip = addr
                _rc, dom_out, _err = await ssh.run(
                    f"{LIBVIRT_SESSION_ENV} virsh domstate {vm_name}", sudo=True,
                )
                power_state = map_domstate(dom_out)
        except Exception as exc:
            error_text = getattr(exc, "error_code", type(exc).__name__)
            try:
                await server_service_client.submit_vm_state(
                    vm_id, target_department_id=target_dept,
                    error=str(error_text),
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "vm.set_network failed-callback errored vm_id=%s", vm_id,
                    exc_info=True,
                )
            raise

        await server_service_client.submit_vm_state(
            vm_id, target_department_id=target_dept,
            ip_address=applied_ip, power_state=power_state,
        )
        return {
            "vm_id": vm_id,
            "vm_name": vm_name,
            "network_mode": network_mode,
            "ip_address": applied_ip,
            "power_state": power_state,
        }

    await run_task(
        task_id,
        audit_action="vm.set_network",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_NETWORK,
    )
