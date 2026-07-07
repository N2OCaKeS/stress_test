"""Общие хелперы VM-менеджера: hub-сессия, валидаторы, сборка команд.

Все операции VM-менеджера исполняются по SSH на hub-сервере под его
управляющей учёткой (ключевая сессия, sudo NOPASSWD — libvirt/kvm без
пароля). Здесь живёт:

* `open_hub_session` — собрать и открыть SSH-сессию к hub'у (тот же паттерн
  выбора кред, что в `astra_update`/`installed_packages`: resolve → hints →
  attach per-server ключ → build_session). SSH-таргет — всегда `str(ip)` из
  payload (`host`/`hub_host`), не hostname.
* валидаторы имён/версий/интерфейсов/путей — defence-in-depth перед
  подстановкой в shell-команды virsh/virt-install/qemu-img;
* билдеры команд prepare/create/power и разбор `virsh domstate`/`domifaddr`.

Гость доступен по `sshpass` под дефолтным аккаунтом образа (`u`/`1`);
провижн-команды идут через вложенный `ssh` из hub-сессии.
"""

from __future__ import annotations

import logging
import re

from src.clients.ssh import SshError
from src.core.constants import (
    VMS_BRIDGE,
    VMS_GUEST_DEFAULT_PASSWORD,
    VMS_GUEST_LOGIN,
)
from src.services import ssh_client
from src.tasks._account_helpers import resolve_ssh_creds

logger = logging.getLogger(__name__)


# Connect-фейлы управляющей сессии к hub'у: до сервера дошли, но сессия не
# поднялась. На подготовленном (managed) hub'е это почти всегда reimage —
# ремапим в actionable ошибку. Тот же набор, что в `astra_update`.
_CONNECT_FAILURE_CODES = frozenset(
    {"SSH_AUTH_FAILED", "SSH_CONNECT_FAILED", "SSH_TIMEOUT"},
)


# Имя ВМ / снимка / бокса / версии ОС: POSIX-safe набор без shell-метасимволов.
# Подставляется в аргументы virsh/virt-install/qemu-img напрямую, поэтому
# `'`/`$`/`;`/пробел/перевод строки не проходят — break-out невозможен.
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
# Имя сетевого интерфейса (`ens192`, `eth0.100`, `enp3s0`): плюс `:`/`@` для
# alias/VLAN-нотаций.
_IFACE_RE = re.compile(r"^[A-Za-z0-9._:@-]+$")
# Путь storage-pool'а: только каталоги (`/vms`, `/srv/vms`).
_PATH_RE = re.compile(r"^/[A-Za-z0-9/._-]*$")
# IPv4 с опциональным CIDR (`10.177.103.42` / `10.177.103.42/24`).
_IP_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?$")


def _validate(value: str, pattern: re.Pattern, host: str, field: str) -> str:
    """Прогнать `value` через regex; вернуть stripped или поднять SshError.

    Единый валидатор для аргументов, уходящих в shell virsh/qemu-img: пустое
    или содержащее метасимволы значение отбивается кодом `VM_INVALID_ARG` до
    подстановки в команду.
    """
    if not isinstance(value, str) or not pattern.fullmatch(value.strip()):
        raise SshError(
            error_code="VM_INVALID_ARG",
            host=host,
            message=f"{field} {value!r} содержит недопустимые символы",
        )
    return value.strip()


def validate_name(value: str, host: str, field: str = "name") -> str:
    return _validate(value, _NAME_RE, host, field)


def validate_iface(value: str, host: str) -> str:
    return _validate(value, _IFACE_RE, host, "phy_if")


def validate_path(value: str, host: str) -> str:
    return _validate(value, _PATH_RE, host, "storage_pool_path")


def validate_ip(value: str, host: str) -> str:
    return _validate(value, _IP_RE, host, "ip_address")


def _positive_int(value, host: str, field: str) -> int:
    """Привести значение к положительному int (cpu/ram_mb/disk_gb)."""
    try:
        num = int(value)
    except (TypeError, ValueError) as exc:
        raise SshError(
            error_code="VM_INVALID_ARG",
            host=host,
            message=f"{field} {value!r} не является целым числом",
        ) from exc
    if num <= 0:
        raise SshError(
            error_code="VM_INVALID_ARG",
            host=host,
            message=f"{field} должен быть положительным, а не {num}",
        )
    return num


def positive_int(value, host: str, field: str) -> int:
    return _positive_int(value, host, field)


def remap_managed_connect_error(exc: SshError) -> SshError:
    """Connect-фейл управляющей сессии к hub'у → actionable ошибка.

    Тот же контракт, что в `astra_update`/`installed_packages`: на managed-hub'е
    auth/connect/timeout почти всегда значит переустановку ОС — оператору нужен
    повторный prepare, а не сырой SSH_AUTH_FAILED.
    """
    return SshError(
        error_code="VMS_HUB_MANAGEMENT_AUTH_FAILED",
        host=exc.host,
        message=(
            "управляющая SSH-сессия к hub'у не поднялась (возможно, ОС "
            "переустановлена и управляющий пользователь/ключ утрачены) — "
            "требуется повторный prepare сервера"
        ),
        details={"underlying_error_code": exc.error_code},
    )


async def open_hub_session(payload: dict):
    """Собрать и открыть управляющую SSH-сессию к hub-серверу.

    Возвращает `(session, host)` — открытый `SshClient` (caller закрывает через
    `async with`/`close`) и строковый адрес hub'а. Адрес берётся из payload
    (`host`/`hub_host`), всегда как `str(ip)`, никогда не hostname.

    Hub — подготовленный (managed) DBOS-сервер: заходим под управляющим
    пользователем по per-server ключу (его тянет `attach_management_creds`),
    sudo без пароля. `is_managed` форсим True (кроме явного `is_managed=False`
    в payload), потому что libvirt/kvm на hub'е крутится именно под управляющей
    учёткой.
    """
    hub_server_id = payload.get("hub_server_id") or payload.get("server_id")
    target_dept = payload.get("target_department_id")
    managed = payload.get("is_managed") is not False

    creds = await resolve_ssh_creds(
        payload, hub_server_id, account_id=None,
        target_dept=target_dept, is_managed=managed,
    )
    ssh_client.apply_session_hints(creds, payload)
    # SSH-таргет — всегда str(ip) из payload; hub_host имеет приоритет над тем,
    # что подставил apply_session_hints (host/ssh_host).
    hub_host = payload.get("host") or payload.get("hub_host") or payload.get("ssh_host")
    if hub_host:
        creds["host"] = str(hub_host)
    if managed:
        creds["is_managed"] = True
        mgmt_user = payload.get("management_user")
        if mgmt_user:
            creds["management_user"] = mgmt_user
    await ssh_client.attach_management_creds(creds, hub_server_id)

    host = creds.get("host") or hub_server_id
    session = ssh_client.build_session(creds, hub_server_id)
    try:
        await session.connect()
    except SshError as exc:
        if managed and exc.error_code in _CONNECT_FAILURE_CODES:
            await session.close()
            raise remap_managed_connect_error(exc) from exc
        await session.close()
        raise
    return session, str(host)


# ── virsh / power ────────────────────────────────────────────────────────────


def map_domstate(text: str) -> str:
    """`virsh domstate` → каноничный power_state.

    Нормализуем сырой вывод libvirt (`running`, `shut off`, `paused`,
    `in shutdown`, `pmsuspended`, `crashed`) к строкам, которые ждёт
    server_service: `on`/`off`/`paused`/`shutting_down`/`suspended`/`crashed`,
    иначе `unknown`.
    """
    t = (text or "").strip().lower()
    if "running" in t:
        return "on"
    if "shut off" in t or "shutoff" in t:
        return "off"
    if "in shutdown" in t:
        return "shutting_down"
    if "paused" in t:
        return "paused"
    if "pmsuspended" in t or "suspend" in t:
        return "suspended"
    if "crashed" in t:
        return "crashed"
    return "unknown"


# Действия питания → глагол virsh. Все — `virsh <verb> <domain>`; graceful
# `shutdown` добавлен к старому набору (в старом API его не было).
POWER_VERBS: dict[str, str] = {
    "start": "start",
    "shutdown": "shutdown",
    "reboot": "reboot",
    "reset": "reset",
    "destroy": "destroy",
}


def parse_domifaddr(stdout: str) -> str | None:
    """Достать IPv4 гостя из `virsh domifaddr`.

    Формат таблицы libvirt:
      ` Name  MAC  Protocol  Address`
      ` vnet0 52:.. ipv4      192.168.100.24/24`
    Берём первый ipv4-адрес; отбрасываем префикс CIDR. None — если адреса ещё
    нет (гость не получил lease).
    """
    for raw in (stdout or "").splitlines():
        line = raw.strip()
        if "ipv4" not in line:
            continue
        for tok in line.split():
            m = re.match(r"^(\d{1,3}(?:\.\d{1,3}){3})(?:/\d{1,2})?$", tok)
            if m:
                return m.group(1)
    return None


# ── Гостевой доступ по sshpass ───────────────────────────────────────────────


def guest_ssh(ip: str, remote_cmd: str, *, sudo: bool = False) -> str:
    """Собрать команду входа на гостя по `sshpass` из hub-сессии.

    Дефолтный аккаунт образа — `u`/`1` (публичный дефолт артефакта, sudo
    NOPASSWD). Пароль подаём через переменную окружения `SSHPASS` (`sshpass -e`),
    а не аргументом `-p`, чтобы он не оседал в списке процессов и в тексте
    команды/ошибки. `StrictHostKeyChecking=no` + `/dev/null` known_hosts — гость
    только что развёрнут, host-key меняется на каждой пересборке.
    """
    inner = f"sudo {remote_cmd}" if sudo else remote_cmd
    return (
        f"SSHPASS={VMS_GUEST_DEFAULT_PASSWORD} sshpass -e ssh "
        "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-o ConnectTimeout=15 {VMS_GUEST_LOGIN}@{ip} {inner!r}"
    )


def bridge_label() -> str:
    return VMS_BRIDGE


__all__ = [
    "open_hub_session",
    "remap_managed_connect_error",
    "validate_name",
    "validate_iface",
    "validate_path",
    "validate_ip",
    "positive_int",
    "map_domstate",
    "parse_domifaddr",
    "guest_ssh",
    "POWER_VERBS",
]
