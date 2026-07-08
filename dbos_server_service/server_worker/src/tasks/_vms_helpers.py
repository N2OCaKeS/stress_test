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

import json
import logging
import re

from src.clients.ssh import SshError
from src.core.constants import (
    VMS_ADDITIONAL_POOL_DIR,
    VMS_ADDITIONAL_POOL_NAME,
    VMS_BOX_CATALOG_URL,
    VMS_BRIDGE,
    VMS_GUEST_DEFAULT_PASSWORD,
    VMS_GUEST_LOGIN,
)
from src.services import ssh_client
from src.tasks._account_helpers import resolve_ssh_creds

logger = logging.getLogger(__name__)


async def run_hub_cmd(
    ssh, cmd: str, host: str, error_code: str, message: str,
    *, ok: tuple[int, ...] = (0,),
) -> tuple[int, str, str]:
    """Выполнить команду на hub'е под sudo; поднять SshError на неожиданный код.

    Все hub-команды идут под sudo (NOPASSWD управляющей учётки): virsh работает
    с `qemu:///system`, файловые операции в пуле — с правами root. `ok` — набор
    допустимых кодов (например `virsh destroy` на уже выключенной ВМ отдаёт
    non-zero, но это не ошибка).
    """
    rc, stdout, stderr = await ssh.run(cmd, sudo=True)
    if rc not in ok:
        raise SshError(
            error_code=error_code,
            host=host,
            cmd_sanitized=cmd,
            returncode=rc,
            stderr=(stderr or stdout).strip(),
            message=message,
        )
    return rc, stdout, stderr


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


def parse_vncdisplay(stdout: str) -> int | None:
    """Достать TCP-порт VNC из вывода `virsh vncdisplay`.

    libvirt отдаёт номер дисплея вида `:0` или `127.0.0.1:0` — реальный порт
    равен `5900 + <дисплей>`. None — если домен без VNC (пустой вывод) или
    формат неожиданный.
    """
    m = re.search(r":(\d+)\s*$", (stdout or "").strip())
    if m is None:
        return None
    return 5900 + int(m.group(1))


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


def guest_ssh_key(
    ip: str, user: str, key_path: str, remote_cmd: str, *, sudo: bool = False,
) -> str:
    """Собрать вход на гостя по приватному ключу (не по паролю).

    Используется в `vm.prepare` уже после установки управляющей пары: проверить
    вход под управляющим пользователем и добить пост-хардинг шаги (удалить
    базовую учётку `u`), когда парольный вход на госте уже выключен. `key_path`
    — путь к временному файлу с приватным ключом на самом hub'е (его пишет и
    подчищает caller). `BatchMode=yes` + `PreferredAuthentications=publickey` —
    отбиваем любой fallback на пароль/интерактив (чётко фейлимся, если ключ не
    пускает). Известный хост не проверяем — гость только что развёрнут.
    """
    inner = f"sudo {remote_cmd}" if sudo else remote_cmd
    return (
        f"ssh -i {key_path} -o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null -o ConnectTimeout=15 "
        "-o BatchMode=yes -o PreferredAuthentications=publickey "
        f"{user}@{ip} {inner!r}"
    )


def bridge_label() -> str:
    return VMS_BRIDGE


# ── Диски ────────────────────────────────────────────────────────────────────


# target-dev диска (`vda`, `vdb`, ...): буква virtio-слота. Подставляется в
# `virsh attach-disk/detach-disk`, поэтому фильтруем строго до `vd[a-z]`.
_TARGET_DEV_RE = re.compile(r"^vd[a-z]$")


def validate_target_dev(value: str, host: str) -> str:
    """Прогнать target-dev через `vd[a-z]`; вернуть или поднять SshError."""
    if not isinstance(value, str) or not _TARGET_DEV_RE.fullmatch(value.strip()):
        raise SshError(
            error_code="VM_INVALID_ARG",
            host=host,
            message=f"target_dev {value!r} должен быть вида vd[a-z]",
        )
    return value.strip()


def next_target_dev(domblklist_out: str, host: str) -> str:
    """Выбрать свободный `vd<x>` из вывода `virsh domblklist --details`.

    Собираем уже занятые virtio-таргеты (колонка Target), берём первую свободную
    букву `a..z`. Если все заняты — поднимаем ошибку (24 диска на ВМ — предел,
    до которого в наших сценариях не доходит).
    """
    used: set[str] = set()
    for raw in (domblklist_out or "").splitlines():
        for tok in raw.split():
            m = re.fullmatch(r"vd([a-z])", tok)
            if m:
                used.add(m.group(1))
    for ch in "abcdefghijklmnopqrstuvwxyz":
        if ch not in used:
            return f"vd{ch}"
    raise SshError(
        error_code="VM_DISK_NO_FREE_SLOT",
        host=host,
        message="нет свободного virtio-слота для диска (заняты vda..vdz)",
    )


def additional_pool_path(pool_path: str) -> str:
    """Каталог dir-pool'а дополнительных дисков: `<pool>/additional_disk`."""
    return f"{pool_path}/{VMS_ADDITIONAL_POOL_DIR}"


async def ensure_additional_pool(ssh, host: str, pool_path: str) -> str:
    """Идемпотентно поднять dir-pool `additional` под data-диски. Вернуть каталог.

    Симметрия `_ensure_pool` в основном модуле: `pool-define-as ... dir --target
    <pool>/additional_disk` + build/start/autostart + refresh. Если пул уже
    определён — только refresh (подхватить внешне созданные qcow2).
    """
    target = additional_pool_path(pool_path)
    rc, _out, _err = await ssh.run(
        f"virsh pool-info {VMS_ADDITIONAL_POOL_NAME}", sudo=True,
    )
    if rc != 0:
        await run_hub_cmd(ssh, f"mkdir -p {target}", host,
                          "VM_DISK_POOL_FAILED", "не удалось создать каталог пула дисков")
        await run_hub_cmd(
            ssh,
            f"virsh pool-define-as {VMS_ADDITIONAL_POOL_NAME} dir --target {target}",
            host, "VM_DISK_POOL_FAILED", "virsh pool-define-as additional упал",
        )
        await ssh.run(f"virsh pool-build {VMS_ADDITIONAL_POOL_NAME}", sudo=True)
        await run_hub_cmd(ssh, f"virsh pool-start {VMS_ADDITIONAL_POOL_NAME}", host,
                          "VM_DISK_POOL_FAILED", "virsh pool-start additional упал")
        await ssh.run(f"virsh pool-autostart {VMS_ADDITIONAL_POOL_NAME}", sudo=True)
    await ssh.run(f"virsh pool-refresh {VMS_ADDITIONAL_POOL_NAME}", sudo=True)
    return target


# ── Гостевой IP ──────────────────────────────────────────────────────────────


async def resolve_guest_ip(ssh, host: str, name: str, payload: dict) -> str:
    """IP гостя для guest-операций (mkfs/growpart): payload > domifaddr.

    Приоритет — явный `guest_ip`/`ip_address` из payload (bridge-ВМ со статикой
    его знают заранее); иначе спрашиваем libvirt (`domifaddr` lease→agent). None
    везде → `VM_GUEST_NO_IP`.
    """
    ip = payload.get("guest_ip") or payload.get("ip_address")
    if ip:
        return validate_ip(str(ip), host).split("/")[0]
    _rc, out, _err = await ssh.run(
        f"virsh domifaddr {name} --source lease", sudo=True,
    )
    parsed = parse_domifaddr(out)
    if parsed is None:
        _rc, out2, _err2 = await ssh.run(
            f"virsh domifaddr {name} --source agent", sudo=True,
        )
        parsed = parse_domifaddr(out2)
    if parsed is None:
        raise SshError(
            error_code="VM_GUEST_NO_IP",
            host=host,
            message=f"ВМ {name} не получила IP (нет lease/agent-адреса)",
        )
    return parsed


# ── Статика гостя (offline-инъекция в диск) ──────────────────────────────────


# Дефолты сети стенда, если payload не задал их явно (порт `provision.sh`:
# gw .254, /24, dns .246).
VMS_DEFAULT_NETMASK = "255.255.255.0"
VMS_DEFAULT_GATEWAY = "10.177.103.254"
VMS_DEFAULT_DNS = ("10.177.180.246",)


def static_interfaces_lines(
    addr: str, netmask: str, gateway: str, dns: list[str],
) -> list[str]:
    """Строки `/etc/network/interfaces` со статикой — порт `provision.sh`."""
    return [
        "auto eth0",
        "iface eth0 inet static",
        f"    address {addr}",
        f"    netmask {netmask}",
        f"    gateway {gateway}",
        f"    dns-nameservers {' '.join(dns)}",
    ]


def normalize_dns(payload: dict, host: str) -> list[str]:
    """Список DNS-серверов из payload (str|list) → валидированные адреса."""
    raw = payload.get("dns")
    if raw is None:
        return [str(d) for d in VMS_DEFAULT_DNS]
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    out: list[str] = []
    for item in items:
        out.append(validate_ip(str(item), host).split("/")[0])
    return out or [str(d) for d in VMS_DEFAULT_DNS]


async def ensure_virt_customize(
    ssh, host: str, *, error_code: str = "VM_NET_APPLY_FAILED",
) -> None:
    """Убедиться, что на hub'е есть virt-customize (libguestfs-tools).

    Обычно ставится в `vms_hub.prepare`. Если бинаря нет — best-effort
    доустановка (apt|dnf); всё равно нет → внятная ошибка с переданным кодом.
    """
    rc, _out, _err = await ssh.run("command -v virt-customize", sudo=True)
    if rc == 0:
        return
    await ssh.run(
        "sh -c 'if command -v apt-get >/dev/null 2>&1; then "
        "DEBIAN_FRONTEND=noninteractive apt-get install -y libguestfs-tools; "
        "elif command -v dnf >/dev/null 2>&1; then "
        "dnf install -y libguestfs-tools || dnf install -y libguestfs-tools-c; fi'",
        sudo=True,
    )
    rc, _out, _err = await ssh.run("command -v virt-customize", sudo=True)
    if rc != 0:
        raise SshError(
            error_code=error_code, host=host,
            message=(
                "на hub'е нет virt-customize (libguestfs-tools) для offline-правки "
                "статики — доустановите пакет или повторите prepare сервера"
            ),
        )


async def write_static_interfaces_offline(
    ssh, host: str, disk_path: str, ip: str, netmask: str,
    gateway: str, dns: list[str], *, error_code: str = "VM_NET_APPLY_FAILED",
) -> None:
    """Залить статику в qcow2-диск ВМ offline (`virt-customize`), не заходя в гостя.

    Генерит `/etc/network/interfaces` (address/netmask/gateway/dns) во временный
    файл на hub'е и заливает его прямо в образ — гость поднимается сразу с боевым
    адресом. Домен на момент вызова должен быть выключен: `vm.create` зовёт до
    `virt-install`, фолбэк `vm.set_network` — после `virsh destroy`. Требует
    libguestfs-tools на hub'е (ставится в `vms_hub.prepare`). Содержимое подаём
    на stdin (`tee`), а не в shell-строку — не расклеивает команду.
    """
    addr = str(ip).split("/")[0]
    safe_disk = validate_path(str(disk_path), host)
    await ensure_virt_customize(ssh, host, error_code=error_code)
    content = "\n".join(static_interfaces_lines(addr, netmask, gateway, dns)) + "\n"
    rc, out, err = await ssh.run("mktemp", sudo=True)
    if rc != 0 or not (out or "").strip():
        raise SshError(
            error_code=error_code, host=host, returncode=rc,
            stderr=(err or "").strip(),
            message="не удалось создать временный файл для статик-конфига",
        )
    tmp = out.strip()
    rc, _out, err = await ssh.run(
        f"tee {tmp} > /dev/null", sudo=True, stdin_payload=content,
    )
    if rc != 0:
        raise SshError(
            error_code=error_code, host=host, returncode=rc,
            stderr=(err or "").strip(),
            message="не удалось записать статик-конфиг во временный файл",
        )
    try:
        await run_hub_cmd(
            ssh,
            f"virt-customize -a {safe_disk} --upload {tmp}:/etc/network/interfaces",
            host, error_code,
            "virt-customize не смог записать статику в диск ВМ",
        )
    finally:
        await ssh.run(f"rm -f {tmp}", sudo=True)


# ── Каталог образов (страховка box_url) ──────────────────────────────────────


def box_url_from_catalog(raw: str, box: str) -> str | None:
    """Резолв имени бокса в url по JSON-каталогу `test-box-config.json`.

    Каталог — карта `имя → url .tar.gz`; ключи лежат либо в секции
    `libvirt_box`, либо на верхнем уровне; значение — строка-url или объект с
    полем `url`/`box_url`. Кривой JSON / отсутствие бокса → None.
    """
    try:
        data = json.loads(raw or "")
    except (ValueError, TypeError):
        return None
    sources: list = []
    if isinstance(data, dict):
        section = data.get("libvirt_box")
        if isinstance(section, dict):
            sources.append(section)
        sources.append(data)
    for src in sources:
        if isinstance(src, dict) and box in src:
            entry = src[box]
            if isinstance(entry, str) and entry.strip():
                return entry.strip()
            if isinstance(entry, dict):
                url = entry.get("url") or entry.get("box_url")
                if isinstance(url, str) and url.strip():
                    return url.strip()
    return None


async def resolve_box_url(ssh, box: str) -> str | None:
    """Скачать каталог `test-box-config.json` с FTP и резолвить `box`→url.

    Страховка на случай, когда server_service не положил `box_url` в payload
    `vm.create`: `wget -qO-` каталога на hub'е, парсинг на стороне воркера.
    Транспорт/парсинг молчаливо возвращает None — caller решает, падать ли.
    """
    _rc, out, _err = await ssh.run(f"wget -qO- {VMS_BOX_CATALOG_URL}", sudo=True)
    return box_url_from_catalog(out, box)


__all__ = [
    "open_hub_session",
    "remap_managed_connect_error",
    "run_hub_cmd",
    "validate_name",
    "validate_iface",
    "validate_path",
    "validate_ip",
    "validate_target_dev",
    "positive_int",
    "map_domstate",
    "parse_domifaddr",
    "parse_vncdisplay",
    "next_target_dev",
    "additional_pool_path",
    "ensure_additional_pool",
    "resolve_guest_ip",
    "static_interfaces_lines",
    "normalize_dns",
    "ensure_virt_customize",
    "write_static_interfaces_offline",
    "VMS_DEFAULT_NETMASK",
    "VMS_DEFAULT_GATEWAY",
    "VMS_DEFAULT_DNS",
    "box_url_from_catalog",
    "resolve_box_url",
    "guest_ssh",
    "guest_ssh_key",
    "bridge_label",
    "POWER_VERBS",
]
