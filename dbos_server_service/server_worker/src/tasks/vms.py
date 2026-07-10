"""Задачи VM-менеджера — исполнение по SSH на hub-сервере.

Управляющая учётка hub'а заходит по ключу и всё гонит под `sudo` NOPASSWD.
libvirt/qemu крутятся в системном демоне (`qemu:///system`, от root): домены,
пулы и qcow2 принадлежат root. VM-команды (virsh/virt-install/libguestfs) несут
env-префикс (`LC_ALL=C LIBGUESTFS_BACKEND=direct`), системная настройка хоста
(пакеты, мост br0, firewall) — тот же root, без env-префикса.

Три базовых handler'а:

* `vms_hub.prepare` — подготовить сервер как VMS-hub: precheck `/dev/kvm`,
  пакеты по семейству ОС, членство в libvirt-группах, cgroup-фикс qemu.conf,
  system-`libvirtd`, мост `br0` над физическим NIC, firewall (FORWARD/DOCKER-USER
  ACCEPT br0 + `bridge-nf-call-iptables=0`), storage-pool и скачивание образов
  с FTP. Идемпотентна.
* `vm.create` — создать ВМ: клон диска бокса (virt-resize при disk_gb) +
  `virt-install`, провижн гостя (hostname, привязанные учётки), снимки.
  Universal-бокс (`vm_station`) несёт несколько версий ОС на одном диске — для
  каждой строим скрытый golden `<ver>_build` + deliverable `<ver>_orel`
  (Орёл) и `<ver>_smolensk` (Смоленск); single-бокс с известной версией — тот
  же набор, без версии — один снимок `build`.
* `vm.power` — `virsh start|shutdown|reboot|reset|destroy` + чтение
  `domstate`.
* `vm.delete` — снести домен ВМ: `virsh destroy` (глушим non-zero) +
  `virsh undefine --remove-all-storage --snapshots-metadata`. Карточку ВМ
  server_service удаляет из БД сразу при dispatch'е, воркер добивает хост.

Длинные операции идут как `astra_update`: без per-команда timeout'а,
durable-retry на уровне `_runner`. Guest доступен по `sshpass` (`u`/`1`).
Исход докладывается server_service через internal-callback'и.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import math
import re

from src.clients.ssh import SshError
from src.core.exceptions import CredentialFetchError
from src.core.constants import (
    VMS_DEFAULT_POOL_PATH,
    VMS_FTP_BOXES_URL,
    VMS_GUEST_LOGIN,
    VMS_NAT_BRIDGE,
    VMS_NAT_DHCP_END,
    VMS_NAT_DHCP_START,
    VMS_NAT_HOST_CIDR,
    VMS_NAT_HOST_IP,
    VMS_NAT_LEASE_FILE,
    VMS_NAT_SUBNET_CIDR,
    VMS_OS_VARIANT,
    VMS_POOL_NAME,
    VMS_UNIVERSAL_BOX,
)
from src.main import broker
from src.services import server_service_client
from src.tasks import installed_packages as ip_helpers
from src.tasks._runner import run_task
from src.tasks._vm_prepare_helpers import (
    _delete_stash,
    _shred_temp_key,
    _write_temp_key,
    apply_managed_baseline,
    choose_guest_connector,
    load_guest_key,
    load_mgmt_material,
)
from src.tasks._vms_helpers import (
    LIBVIRT_SESSION_ENV,
    MODE_OREL,
    MODE_SMOLENSK,
    POWER_VERBS,
    SNAPSHOT_KIND_OS_BASELINE,
    VMS_DEFAULT_GATEWAY,
    VMS_DEFAULT_NETMASK,
    bridge_label,
    guest_connector,
    guest_key_connector,
    guest_ssh,
    map_domstate,
    normalize_dns,
    open_hub_session,
    parse_domifaddr,
    positive_int,
    resolve_box_url,
    resolve_guest_ip,
    run_hub_cmd,
    switch_guest_to_smolensk,
    validate_ip,
    validate_iface,
    validate_name,
    validate_path,
    write_static_interfaces_offline,
)

logger = logging.getLogger(__name__)


# Пакеты hub'а по семейству ОС. apt-набор — из референса
# (`Libvirt.prepare`): astra-kvm тянет qemu/libvirt зависимостями. dnf-аналог —
# ручной список для RHEL/RedOS. libguestfs-tools нужен для virt-customize —
# offline-инъекции статики в диск ВМ (single-bridge в `vm.create` и фолбэк в
# `vm.set_network`).
_APT_PACKAGES = (
    "astra-kvm virtinst qemu-utils wget tar sshpass bridge-utils "
    "libguestfs-tools dnsmasq"
)
_DNF_PACKAGES = (
    "qemu-kvm libvirt virt-install qemu-img wget tar sshpass bridge-utils "
    "libguestfs-tools dnsmasq"
)

# Группы libvirt/kvm, в которые доклеиваем управляющего пользователя hub'а.
_LIBVIRT_GROUPS = ("kvm", "libvirt", "libvirt-qemu", "libvirt-admin")

# Пакеты, которые ставим в гостя при провижне (референс: rsync/htop/gcc/make/
# perl + qemu-guest-agent для domifaddr по agent-каналу).
_GUEST_DEPS = "rsync htop gcc make perl qemu-guest-agent"

AUDIT_SAFE_FIELDS_PREPARE: set[str] = {
    "server_id", "prepared", "phy_if", "os_family", "images",
}
AUDIT_SAFE_FIELDS_CREATE: set[str] = {
    "vm_id", "name", "box", "network_mode", "power_state", "status",
    "ip_address", "snapshots",
}
AUDIT_SAFE_FIELDS_POWER: set[str] = {"vm_id", "vm_name", "action", "power_state"}
AUDIT_SAFE_FIELDS_DELETE: set[str] = {"vm_id", "vm_name", "destroyed", "undefined"}
# Список пакетов гостя наружу в loging не уходит (как в installed_packages.list) —
# только операционные счётчики. Сам `packages` лежит в task.result.
AUDIT_SAFE_FIELDS_LIST_PACKAGES: set[str] = {
    "vm_id", "vm_name", "package_manager", "count",
}


# hub-команда под sudo с проверкой кода возврата — общий раннер VM-тасок.
_run = run_hub_cmd

# Корневой раздел бокса для virt-resize (подтверждено на боксе: vda1 — 2M
# BIOS-boot, vda2 — ext4 root). libguestfs адресует диск как /dev/sda.
_ROOT_PARTITION = "/dev/sda2"

# Сжатие диска: работаем через COW-overlay бокса, чтобы сам бокс остался
# нетронутым (`qemu-img create -b`). Суффикс временного overlay-файла рядом с
# целевым диском.
_SHRINK_WORK_SUFFIX = ".shrink-src"
# Зазор между целевым размером диска и размером, до которого ужимаем ФС перед
# virt-resize. Должен перекрывать sda1 (2M BIOS-boot) + выравнивание таблицы
# разделов; virt-resize потом сам растянет ФС обратно на весь новый раздел, так
# что этот зазор не теряется. 256 МиБ — с запасом.
_SHRINK_FS_MARGIN_BYTES = 256 * 1024 * 1024

# Суффикс скрытого golden-снимка версии (чистый managed-baseline, служебный).
# Кончается на `_build` — по нему server_service детектит скрытый снимок, а
# reroll/passwd его защищают (см. `vms_snapshots._BUILD_SUFFIX`).
_GOLDEN_SUFFIX = "_build"

# Ожидание graceful-выключения гостя перед снимком чистого managed-baseline.
# domstate поллим до `off`; не выключился штатно — глушим жёстко (`virsh destroy`).
# Тесты обнуляют задержку (реальное выключение Astra — десятки секунд).
_SNAP_OFF_POLL_DELAY_S = 5.0
_SNAP_OFF_MAX_POLLS = 60


def _validate_guest_password(value: str, host: str) -> str:
    """Отбить пароль гостя с символами, ломающими inline-`chpasswd`.

    Пароль уходит в `bash -c 'echo <login>:<pwd> | chpasswd'` внутри вложенной
    guest-сессии; перевод строки/кавычки/подстановка расклеили бы команду.
    """
    if not isinstance(value, str) or not value:
        raise SshError(
            error_code="VM_INVALID_ARG", host=host,
            message="пароль гостя должен быть непустой строкой",
        )
    for bad in ("\n", "\r", "\0", "'", '"', "`", "$", ";"):
        if bad in value:
            raise SshError(
                error_code="VM_INVALID_ARG", host=host,
                message="пароль гостя содержит недопустимый символ",
            )
    return value


# ── vms_hub.prepare ──────────────────────────────────────────────────────────


def _install_packages_cmd(os_family: str) -> str:
    """Команда установки пакетов hub'а по семейству ОС (apt|dnf)."""
    if os_family == "apt":
        return (
            "sh -c 'DEBIAN_FRONTEND=noninteractive apt-get update && "
            f"DEBIAN_FRONTEND=noninteractive apt-get install -y {_APT_PACKAGES}'"
        )
    return f"dnf install -y {_DNF_PACKAGES}"


def _usermod_cmd(management_user: str | None) -> str:
    """Идемпотентно доклеить управляющего пользователя в libvirt/kvm-группы.

    Управляющий пользователь берётся из creds/payload; если неизвестен — под
    sudo это `$SUDO_USER` (инвокер sudo = управляющая учётка). Каждую группу
    добавляем только если она есть в системе (`getent group`) — на RHEL часть
    групп libvirt-* может отсутствовать.
    """
    user = management_user if management_user else "${SUDO_USER:-root}"
    groups = " ".join(_LIBVIRT_GROUPS)
    return (
        "sh -c 'for g in " + groups + "; do "
        'getent group "$g" >/dev/null 2>&1 && usermod -aG "$g" "' + user + '"; '
        "done'"
    )


# Cgroup-фикс qemu.conf: на части стендов ВМ не стартуют из-за cgroup-делегации
# systemd — снимаем размещение qemu в контроллерах. Идемпотентно (marker-guard).
# TODO: сверить точный фикс с референсным `Libvirt.prepare` в ветке libs.
_QEMU_CONF_FIX = (
    "sh -c 'grep -q dbos-vms-hub /etc/libvirt/qemu.conf 2>/dev/null || "
    "printf \"\\n# dbos-vms-hub\\ncgroup_controllers = [ ]\\n\" "
    ">> /etc/libvirt/qemu.conf'"
)

# Astra SE держит libvirt под parsec: access-driver и security-driver. В режиме
# Смоленск parsec-ACL запрещает операции libvirt («доступ запрещён QEMU»), в Орле
# hub поднимается и без правки. Снимаем parsec-ACL и per-VM labeling, чтобы hub
# работал в обоих режимах. Идемпотентно: строку удаляем и дописываем заново.
_PARSEC_FIX = (
    "sh -c '"
    "sed -i \"/^access_drivers/d\" /etc/libvirt/libvirtd.conf; "
    "echo \"access_drivers = [ ]\" >> /etc/libvirt/libvirtd.conf; "
    "sed -i \"/^security_driver/d\" /etc/libvirt/qemu.conf; "
    "echo \"security_driver = \\\"none\\\"\" >> /etc/libvirt/qemu.conf'"
)

# Firewall: iptables→nft, FORWARD br0 ACCEPT (+ RELATED,ESTABLISHED обратно),
# DOCKER-USER ACCEPT br0 (если docker установлен — иначе цепочки нет). Все
# правила идемпотентны через `-C ... || -I ...`.
_FIREWALL_FIX = (
    "sh -c '"
    "command -v update-alternatives >/dev/null 2>&1 && "
    "update-alternatives --set iptables /usr/sbin/iptables-nft >/dev/null 2>&1 || true; "
    "iptables -C FORWARD -i br0 -j ACCEPT 2>/dev/null || "
    "iptables -I FORWARD 1 -i br0 -j ACCEPT; "
    "iptables -C FORWARD -o br0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || "
    "iptables -I FORWARD 2 -o br0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT; "
    "if iptables -L DOCKER-USER -n >/dev/null 2>&1; then "
    "iptables -C DOCKER-USER -i br0 -j ACCEPT 2>/dev/null || iptables -I DOCKER-USER 1 -i br0 -j ACCEPT; "
    "iptables -C DOCKER-USER -o br0 -j ACCEPT 2>/dev/null || iptables -I DOCKER-USER 1 -o br0 -j ACCEPT; "
    "fi'"
)

# br_netfilter + постоянное отключение bridge-nf-call (фикс «ВМ теряют интернет
# под docker/grafana после reboot»): модуль в modules-load.d, sysctl в sysctl.d.
_BRIDGE_NF_FIX = (
    "sh -c '"
    "modprobe br_netfilter 2>/dev/null || true; "
    "echo br_netfilter > /etc/modules-load.d/dbos-br_netfilter.conf; "
    "printf \""
    "net.bridge.bridge-nf-call-iptables=0\\n"
    "net.bridge.bridge-nf-call-ip6tables=0\\n"
    "net.bridge.bridge-nf-call-arptables=0\\n\" "
    "> /etc/sysctl.d/99-dbos-vms-bridge.conf; "
    "sysctl -p /etc/sysctl.d/99-dbos-vms-bridge.conf >/dev/null 2>&1 || true'"
)


async def _default_gateway(ssh) -> str:
    """Достать адрес шлюза по умолчанию (`ip route show default`). Пусто если нет."""
    _rc, route_out, _ = await ssh.run("ip route show default", sudo=True)
    gw_m = re.search(r"default\s+via\s+(\d{1,3}(?:\.\d{1,3}){3})", route_out or "")
    return gw_m.group(1) if gw_m else ""


async def _setup_bridge(ssh, host: str, phy_if: str, os_family: str) -> bool:
    """Настроить мост `br0` над физическим NIC (static, по факту сети хоста).

    Идемпотентно: если `br0` уже есть — выходим (`False`, ребут не нужен). Иначе
    снимаем текущую адресацию `phy_if` (адрес/шлюз/DNS) и переносим её на мост.
    Для apt-семейства пишем drop-in ifupdown (`/etc/network/interfaces.d`), для
    dnf — конфиг через NetworkManager (`nmcli`). Живьём мост НЕ поднимаем: перенос
    адреса с управляющего NIC рвёт текущую SSH-сессию. Конфиг вступит в силу на
    следующей загрузке, поэтому при записи нового моста возвращаем `True` —
    caller ставит guard и перезагружает хост сам.
    """
    rc, _out, _err = await ssh.run("ip link show br0", sudo=True)
    if rc == 0:
        logger.info("vms_hub.prepare: bridge br0 already present on %s", host)
        return False

    _rc, addr_out, _ = await ssh.run(f"ip -o -4 addr show dev {phy_if}", sudo=True)
    m = re.search(r"inet\s+(\d{1,3}(?:\.\d{1,3}){3}/\d{1,2})", addr_out or "")
    if not m:
        raise SshError(
            error_code="VMS_HUB_BRIDGE_NO_ADDR",
            host=host,
            message=(
                f"не удалось определить адрес {phy_if} для переноса на br0 — "
                "настройте адресацию физического интерфейса вручную"
            ),
        )
    addr_cidr = m.group(1)
    gateway = await _default_gateway(ssh)
    _rc, dns_out, _ = await ssh.run(
        "grep -E '^nameserver' /etc/resolv.conf", sudo=True,
    )
    nameservers = re.findall(r"nameserver\s+(\S+)", dns_out or "")

    if os_family == "apt":
        lines = [
            "# dbos-vms-hub bridge",
            f"allow-hotplug {phy_if}",
            f"iface {phy_if} inet manual",
            "",
            "auto br0",
            "iface br0 inet static",
            f"    address {addr_cidr}",
        ]
        if gateway:
            lines.append(f"    gateway {gateway}")
        if nameservers:
            lines.append(f"    dns-nameservers {' '.join(nameservers)}")
        lines += [
            f"    bridge_ports {phy_if}",
            "    bridge_stp off",
            "    bridge_fd 0",
            "    bridge_maxwait 0",
            "",
        ]
        content = "\n".join(lines)
        # Основной /etc/network/interfaces обычно держит `iface <phy_if> inet
        # static` с адресом — она конфликтует с br0 (bridge_ports забирает тот же
        # NIC) и мост не встаёт после reboot. Сворачиваем основной файл до
        # `source .d/* + lo` (с бэкапом рядом, идемпотентно), чтобы адресацию нёс
        # только drop-in моста.
        await ssh.run(
            "sh -c 'test -f /etc/network/interfaces.dbos-bak || "
            "cp /etc/network/interfaces /etc/network/interfaces.dbos-bak; "
            "printf \"source /etc/network/interfaces.d/*\\n"
            "auto lo\\niface lo inet loopback\\n\" > /etc/network/interfaces'",
            sudo=True,
        )
        # Конфиг подаём на stdin (`tee`) — так multiline-содержимое не уходит в
        # shell-строку и не может её расклеить.
        rc, _out, stderr = await ssh.run(
            "tee /etc/network/interfaces.d/dbos-br0.cfg > /dev/null",
            sudo=True, stdin_payload=content,
        )
        if rc != 0:
            raise SshError(
                error_code="VMS_HUB_BRIDGE_WRITE_FAILED", host=host,
                returncode=rc, stderr=(stderr or "").strip(),
                message="не удалось записать конфиг моста",
            )
        # Живой `ifup br0` НЕ делаем: основной interfaces уже свёрнут (адрес
        # eno6 больше не закреплён в конфиге), поэтому live-подъём моста через
        # тот же NIC сбрасывает адрес и рвёт текущую SSH-сессию. Мост поднимется
        # штатно при следующем reboot хоста — тогда drop-in отработает без
        # конфликта с живым eno6.
        return True
    else:
        addr_only = addr_cidr
        await ssh.run("nmcli con add type bridge ifname br0 con-name br0", sudo=True)
        modify = (
            f"nmcli con modify br0 ipv4.addresses {addr_only} ipv4.method manual"
        )
        if gateway:
            modify += f" ipv4.gateway {gateway}"
        if nameservers:
            modify += f" ipv4.dns '{','.join(nameservers)}'"
        await ssh.run(modify, sudo=True)
        await ssh.run(
            f"nmcli con add type ethernet ifname {phy_if} master br0 "
            "con-name dbos-br0-slave",
            sudo=True,
        )
        # Живую активацию (`nmcli con up br0`) НЕ делаем — перенос адреса на мост
        # через тот же NIC рвёт SSH-сессию. Соединение определено и поднимется на
        # следующем reboot хоста.
        return True


# Guard активации моста. Пишется на hub при первичной настройке br0 и снимается
# сам собой на следующей загрузке, если сеть поднялась. Скрипт живёт как
# systemd-oneshot (`multi-user.target`), поэтому переживает reboot: prepare сам
# перезагружает хост, а если мост над управляющим NIC не встал — guard
# откатывает адресацию из бэкапа и при необходимости ребутит ещё раз, чтобы
# сервер не остался без сети.
_NET_GUARD_SCRIPT_PATH = "/usr/local/sbin/dbos-net-guard.sh"
_NET_GUARD_UNIT_PATH = "/etc/systemd/system/dbos-net-guard.service"
_NET_GUARD_UNIT = (
    "[Unit]\n"
    "Description=DBOS vms-hub bridge activation guard\n"
    "After=network.target networking.service\n"
    "\n"
    "[Service]\n"
    "Type=oneshot\n"
    f"ExecStart={_NET_GUARD_SCRIPT_PATH}\n"
    "\n"
    "[Install]\n"
    "WantedBy=multi-user.target\n"
)


def _net_guard_script(gateway: str) -> str:
    """Тело guard-скрипта: ждём загрузку сети, пингуем шлюз, откат при провале."""
    return (
        "#!/bin/bash\n"
        "sleep 90\n"
        f"if ping -c3 -W3 {gateway} >/dev/null 2>&1; then "
        "systemctl disable dbos-net-guard.service; exit 0; fi\n"
        "[ -f /etc/network/interfaces.dbos-bak ] && "
        "cp /etc/network/interfaces.dbos-bak /etc/network/interfaces\n"
        "rm -f /etc/network/interfaces.d/dbos-br0.cfg\n"
        "systemctl restart networking 2>/dev/null\n"
        "sleep 8\n"
        f"ping -c3 -W3 {gateway} >/dev/null 2>&1 || /sbin/reboot\n"
    )


async def _install_net_guard(ssh, host: str, gateway: str) -> None:
    """Поставить reboot-переживающий guard активации моста.

    Кладёт скрипт `/usr/local/sbin/dbos-net-guard.sh` и systemd-unit, делает
    daemon-reload и включает сервис. Содержимое подаём на stdin (`tee`), чтобы
    multiline не расклеивал shell-строку. Guard запустится на следующей загрузке
    (после `_trigger_reboot`) и сам решит судьбу моста по пингу шлюза.
    """
    rc, _out, stderr = await ssh.run(
        f"tee {_NET_GUARD_SCRIPT_PATH} > /dev/null",
        sudo=True, stdin_payload=_net_guard_script(gateway),
    )
    if rc != 0:
        raise SshError(
            error_code="VMS_HUB_GUARD_FAILED", host=host,
            returncode=rc, stderr=(stderr or "").strip(),
            message="не удалось записать net-guard скрипт",
        )
    await _run(ssh, f"chmod +x {_NET_GUARD_SCRIPT_PATH}", host,
               "VMS_HUB_GUARD_FAILED", "не удалось сделать net-guard исполняемым",
               sudo=True)
    rc, _out, stderr = await ssh.run(
        f"tee {_NET_GUARD_UNIT_PATH} > /dev/null",
        sudo=True, stdin_payload=_NET_GUARD_UNIT,
    )
    if rc != 0:
        raise SshError(
            error_code="VMS_HUB_GUARD_FAILED", host=host,
            returncode=rc, stderr=(stderr or "").strip(),
            message="не удалось записать net-guard unit",
        )
    await _run(
        ssh,
        "sh -c 'systemctl daemon-reload && "
        "systemctl enable dbos-net-guard.service'",
        host, "VMS_HUB_GUARD_FAILED", "не удалось включить net-guard",
        sudo=True,
    )


async def _trigger_reboot(ssh, host: str) -> None:
    """Запланировать отложенный ребут hub'а через systemd и вернуться сразу.

    Ребут нужен, чтобы поднялся мост br0. Планируем его как транзиентный
    systemd-таймер (`systemd-run --on-active`) — он полностью отвязан от
    SSH-сессии и переживёт её закрытие. Прежний вариант `setsid ... &` не
    работал: sshd бьёт SIGHUP по процессам канала при его закрытии, и фоновый
    `sleep 5; reboot` умирал до срабатывания. Фолбэк `shutdown -r` — на хостах
    без `systemd-run`. Небольшая задержка даёт таске завершиться и session
    закрыться до ребута. Обрыв SSH на самом триггере таску не роняет: хаб уже
    уходит в перезагрузку, дальше его подъём ждёт `_wait_hub_back`, поэтому
    глушим SshError.
    """
    try:
        await ssh.run(
            "sh -c 'systemd-run --on-active=8 systemctl reboot "
            "|| shutdown -r +1'",
            sudo=True,
        )
    except SshError:
        logger.info(
            "vms_hub.prepare: reboot-триггер оборвал SSH (ожидаемо) host=%s", host,
        )


# parsec-kiosk2 на профиле fly-desktop Astra включает kiosk-режим. На vm-hub'е
# он не нужен и активно мешает: режет пользовательские systemd-юниты non-root
# («PARSEC-UNIT [virtqemud.service] skipping for non-root»), из-за чего
# session-ВМ (qemu:///session) гибнут за минуту — idle-exit virtqemud, а
# systemd-scope сносит qemu следом.
_KIOSK_UNIT = "parsec-kiosk2.service"


async def _disable_kiosk(ssh, host: str) -> bool:
    """Выключить kiosk-режим (parsec-kiosk2) на vm-hub'е.

    Хаб — не киоск, режим тут только вредит: он не даёт держать ВМ в
    пользовательской сессии libvirt. Если сервис и не запущен, и не включён —
    менять нечего, возвращаем False. Иначе гасим, маскируем и снимаем
    enforce-флаг одной best-effort/идемпотентной командой и возвращаем True:
    эффект применяется только после reboot, поэтому caller обязан перезагрузить
    хаб.
    """
    rc_active, _out, _err = await ssh.run(
        f"systemctl is-active {_KIOSK_UNIT}", sudo=True,
    )
    rc_enabled, _out2, _err2 = await ssh.run(
        f"systemctl is-enabled {_KIOSK_UNIT}", sudo=True,
    )
    if rc_active != 0 and rc_enabled != 0:
        return False
    await ssh.run(
        f"sh -c 'systemctl disable --now {_KIOSK_UNIT}; "
        f"systemctl mask {_KIOSK_UNIT}; "
        "printf 0 > /etc/parsec/kiosk2_enforce 2>/dev/null || true'",
        sudo=True,
    )
    return True


# Ожидание подъёма хаба после самоперезагрузки в prepare. Хаб уходит в reboot с
# небольшой задержкой (systemd-run --on-active=8), br0 встаёт на следующей
# загрузке — поэтому первую пробу делаем не сразу, а после паузы, чтобы не
# поймать ещё живой доребутный SSH. Каждую итерацию переоткрываем управляющую
# сессию; пока хаб не поднялся, любой SSH-фейл (connect/auth/timeout) — это «ещё
# не готов», а не терминал. Общее окно — attempts * delay (~240с при 24 × 10с).
_HUB_REBOOT_WAIT_ATTEMPTS = 24
_HUB_REBOOT_WAIT_DELAY_S = 10.0


async def _wait_hub_back(payload: dict, host: str) -> None:
    """Дождаться, пока hub снова примет управляющую SSH-сессию после reboot.

    В цикле переоткрываем сессию (`open_hub_session`) и делаем лёгкий
    health-check (`test -e /dev/kvm`). Пока хаб перезагружается, коннект не
    поднимется — любую `SshError` глотаем и ретраим (сюда же попадает ремап
    connect-фейла в `VMS_HUB_MANAGEMENT_AUTH_FAILED`, коды
    `SSH_AUTH_FAILED`/`SSH_CONNECT_FAILED`/`SSH_TIMEOUT`). Не поднялся за
    отведённое окно — `VMS_HUB_REBOOT_TIMEOUT`: net-guard вернёт сеть на
    следующей загрузке, оператор повторит prepare.
    """
    for _ in range(_HUB_REBOOT_WAIT_ATTEMPTS):
        await asyncio.sleep(_HUB_REBOOT_WAIT_DELAY_S)
        try:
            session, _host = await open_hub_session(payload)
            async with session as ssh:
                rc, _out, _err = await ssh.run("test -e /dev/kvm", sudo=True)
                if rc == 0:
                    return
        except SshError:
            pass
    raise SshError(
        error_code="VMS_HUB_REBOOT_TIMEOUT",
        host=host,
        message="hub не поднялся после reboot в отведённое окно",
    )


def _image_url(ref: str) -> tuple[str, str]:
    """По имени/URL образа вернуть `(url, base_name)`.

    `ref` может быть готовым URL (`ftp://.../vm_station.tar.gz`) либо голым
    именем бокса из каталога — тогда URL строим от FTP-базы стенда. `base_name`
    — имя без `.tar.gz`/`.qcow2`, под ним ляжет qcow2 в пуле.
    """
    if "://" in ref:
        url = ref
        fname = ref.rsplit("/", 1)[-1]
    else:
        fname = ref if ref.endswith(".tar.gz") else f"{ref}.tar.gz"
        url = f"{VMS_FTP_BOXES_URL}/{fname}"
    base = fname
    for suf in (".tar.gz", ".tgz", ".qcow2"):
        if base.endswith(suf):
            base = base[: -len(suf)]
            break
    return url, base


async def _ensure_pool(ssh, host: str, pool_path: str) -> None:
    """Идемпотентно поднять dir storage-pool `vms` на `pool_path` в системном libvirt.

    Пул определяется в `qemu:///system` под root — каталог к этому моменту уже
    создан (`_setup_storage` в prepare). define/build/start и последующие qemu-img
    идут под root (`sudo=True`), как и весь VM-флоу.
    """
    rc, _out, _err = await ssh.run(
        f"{LIBVIRT_SESSION_ENV} virsh pool-info {VMS_POOL_NAME}", sudo=True,
    )
    if rc == 0:
        return
    await _run(ssh, f"mkdir -p {pool_path}", host,
               "VMS_HUB_POOL_FAILED", "не удалось создать каталог пула")
    await _run(
        ssh,
        f"virsh pool-define-as {VMS_POOL_NAME} dir --target {pool_path}",
        host, "VMS_HUB_POOL_FAILED", "virsh pool-define-as упал",
    )
    await ssh.run(f"{LIBVIRT_SESSION_ENV} virsh pool-build {VMS_POOL_NAME}", sudo=True)
    await _run(ssh, f"virsh pool-start {VMS_POOL_NAME}", host,
               "VMS_HUB_POOL_FAILED", "virsh pool-start упал")
    await ssh.run(
        f"{LIBVIRT_SESSION_ENV} virsh pool-autostart {VMS_POOL_NAME}", sudo=True,
    )


async def _download_images(
    ssh, host: str, pool_path: str, image_refs: list[str],
) -> list[str]:
    """Скачать и распаковать образы каталога в пул (идемпотентно). Вернуть имена.

    Для каждого образа: если qcow2 уже в пуле — пропускаем; иначе тянем
    `.tar.gz` с FTP (`wget`) и распаковываем (`tar xzf`) в пул.
    """
    downloaded: list[str] = []
    for ref in image_refs or []:
        if not isinstance(ref, str) or not ref.strip():
            continue
        url, base = _image_url(ref.strip())
        validate_name(base, host, "image_ref")
        qcow = f"{pool_path}/{base}.qcow2"
        rc, _out, _err = await ssh.run(f"test -f {qcow}")
        if rc == 0:
            downloaded.append(base)
            continue
        tarball = f"{pool_path}/{base}.tar.gz"
        await _run(
            ssh, f"wget -q -O {tarball} {url}", host,
            "VMS_HUB_IMAGE_DOWNLOAD_FAILED", f"не удалось скачать образ {base}",
        )
        await _run(
            ssh, f"tar xzf {tarball} -C {pool_path}", host,
            "VMS_HUB_IMAGE_UNPACK_FAILED", f"не удалось распаковать образ {base}",
        )
        await ssh.run(f"rm -f {tarball}")
        downloaded.append(base)
    return downloaded


# ── NAT-мост natbr0 (host-only, dnsmasq + MASQUERADE) ────────────────────────
#
# В отличие от br0 (несёт физический NIC, требует reboot) natbr0 host-only:
# ставим и поднимаем на живую, SSH-сессия не рвётся. Мост + ip_forward + правила
# iptables кладём в oneshot-скрипт: он идемпотентен, исполняется сразу при
# prepare и переигрывается на каждой загрузке (systemd-unit WantedBy multi-user),
# так что MASQUERADE и адрес natbr0 переживают reboot без iptables-persistent.
_NAT_SETUP_SCRIPT_PATH = "/usr/local/sbin/dbos-vms-nat.sh"
_NAT_SETUP_UNIT_PATH = "/etc/systemd/system/dbos-vms-nat.service"
_NAT_DNSMASQ_CONF_PATH = "/etc/dnsmasq.d/dbos-natbr0.conf"

_NAT_SETUP_UNIT = (
    "[Unit]\n"
    "Description=DBOS vms-hub NAT bridge (natbr0 + MASQUERADE)\n"
    "After=network.target networking.service\n"
    "\n"
    "[Service]\n"
    "Type=oneshot\n"
    "RemainAfterExit=yes\n"
    f"ExecStart={_NAT_SETUP_SCRIPT_PATH}\n"
    "\n"
    "[Install]\n"
    "WantedBy=multi-user.target\n"
)


def _nat_setup_script() -> str:
    """Тело idempotent-скрипта natbr0: мост + адрес + ip_forward + MASQUERADE.

    Мост host-only (без bridge_ports), поэтому подъём не трогает адресацию
    физического NIC и не рвёт SSH. iptables — через iptables-nft, каждое правило
    под `-C ... || -I ...`, так что повторный прогон ничего не дублирует.
    """
    br = VMS_NAT_BRIDGE
    subnet = VMS_NAT_SUBNET_CIDR
    return (
        "#!/bin/bash\n"
        f"ip link show {br} >/dev/null 2>&1 || ip link add {br} type bridge\n"
        f"ip addr show dev {br} | grep -q '{VMS_NAT_HOST_IP}/' "
        f"|| ip addr add {VMS_NAT_HOST_CIDR} dev {br}\n"
        f"ip link set {br} up\n"
        "sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1 || true\n"
        f"iptables -t nat -C POSTROUTING -s {subnet} ! -o {br} -j MASQUERADE "
        f"2>/dev/null || iptables -t nat -I POSTROUTING 1 -s {subnet} ! -o {br} "
        "-j MASQUERADE\n"
        f"iptables -C FORWARD -i {br} -j ACCEPT 2>/dev/null "
        f"|| iptables -I FORWARD 1 -i {br} -j ACCEPT\n"
        f"iptables -C FORWARD -o {br} -m conntrack --ctstate RELATED,ESTABLISHED "
        f"-j ACCEPT 2>/dev/null || iptables -I FORWARD 2 -o {br} -m conntrack "
        "--ctstate RELATED,ESTABLISHED -j ACCEPT\n"
        "if iptables -L DOCKER-USER -n >/dev/null 2>&1; then "
        f"iptables -C DOCKER-USER -i {br} -j ACCEPT 2>/dev/null "
        f"|| iptables -I DOCKER-USER 1 -i {br} -j ACCEPT; "
        f"iptables -C DOCKER-USER -o {br} -j ACCEPT 2>/dev/null "
        f"|| iptables -I DOCKER-USER 1 -o {br} -j ACCEPT; fi\n"
    )


def _nat_dnsmasq_conf() -> str:
    """Конфиг выделенного dnsmasq-инстанса на natbr0.

    `bind-interfaces` + `interface=natbr0` держат dnsmasq только на мосту, чтобы
    он не спорил с системным резолвером на других интерфейсах. Свой lease-файл —
    по нему воркер резолвит адрес гостя по MAC. Шлюз и DNS для гостя — сам хаб
    (`192.168.100.1`): dnsmasq проксирует DNS в апстримы хоста.
    """
    return (
        "# dbos-vms-hub NAT DHCP on natbr0\n"
        f"interface={VMS_NAT_BRIDGE}\n"
        "bind-interfaces\n"
        "except-interface=lo\n"
        f"dhcp-range={VMS_NAT_DHCP_START},{VMS_NAT_DHCP_END},12h\n"
        f"dhcp-leasefile={VMS_NAT_LEASE_FILE}\n"
        f"dhcp-option=3,{VMS_NAT_HOST_IP}\n"
        f"dhcp-option=6,{VMS_NAT_HOST_IP}\n"
    )


async def _setup_nat_bridge(ssh, host: str) -> None:
    """Поднять host-only NAT-мост natbr0 с dnsmasq и MASQUERADE (без reboot).

    Идемпотентно: скрипт/unit/конфиг переписываются, живой подъём и iptables — под
    `-C || -I`. Последовательность: (1) кладём oneshot-скрипт+unit и включаем его,
    (2) исполняем скрипт сейчас (мост host-only — SSH не рвётся, reboot не нужен),
    (3) поднимаем выделенный dnsmasq на natbr0. Системный libvirt под root цепляет
    tap к natbr0 сам — qemu-bridge-helper/allow-list не нужны.
    """
    rc, _out, stderr = await ssh.run(
        f"tee {_NAT_SETUP_SCRIPT_PATH} > /dev/null",
        sudo=True, stdin_payload=_nat_setup_script(),
    )
    if rc != 0:
        raise SshError(
            error_code="VMS_HUB_NAT_FAILED", host=host,
            returncode=rc, stderr=(stderr or "").strip(),
            message="не удалось записать скрипт настройки natbr0",
        )
    await _run(ssh, f"chmod +x {_NAT_SETUP_SCRIPT_PATH}", host,
               "VMS_HUB_NAT_FAILED", "не удалось сделать natbr0-скрипт исполняемым",
               sudo=True)
    rc, _out, stderr = await ssh.run(
        f"tee {_NAT_SETUP_UNIT_PATH} > /dev/null",
        sudo=True, stdin_payload=_NAT_SETUP_UNIT,
    )
    if rc != 0:
        raise SshError(
            error_code="VMS_HUB_NAT_FAILED", host=host,
            returncode=rc, stderr=(stderr or "").strip(),
            message="не удалось записать natbr0 unit",
        )
    await _run(
        ssh,
        "sh -c 'systemctl daemon-reload && "
        "systemctl enable dbos-vms-nat.service'",
        host, "VMS_HUB_NAT_FAILED", "не удалось включить natbr0-unit",
        sudo=True,
    )
    # Живой подъём сейчас: host-only мост не рвёт управляющую SSH-сессию.
    await _run(ssh, _NAT_SETUP_SCRIPT_PATH, host,
               "VMS_HUB_NAT_FAILED", "не удалось поднять natbr0", sudo=True)
    rc, _out, stderr = await ssh.run(
        f"tee {_NAT_DNSMASQ_CONF_PATH} > /dev/null",
        sudo=True, stdin_payload=_nat_dnsmasq_conf(),
    )
    if rc != 0:
        raise SshError(
            error_code="VMS_HUB_NAT_FAILED", host=host,
            returncode=rc, stderr=(stderr or "").strip(),
            message="не удалось записать конфиг dnsmasq для natbr0",
        )
    await _run(
        ssh,
        "sh -c 'systemctl enable --now dnsmasq && systemctl restart dnsmasq'",
        host, "VMS_HUB_NAT_FAILED", "не удалось поднять dnsmasq на natbr0",
        sudo=True,
    )


async def _setup_storage(ssh, host: str, pool_path: str) -> None:
    """Обеспечить каталог хранилища под VM-диски (root-owned).

    Системный qemu бежит от root, поэтому chown в управляющую учётку не нужен —
    достаточно, чтобы каталог существовал (`mkdir -p`, идемпотентно). qemu-img и
    libguestfs-тулы пишут сюда под root.
    """
    await _run(
        ssh, f"mkdir -p {pool_path}", host, "VMS_HUB_STORAGE_FAILED",
        "не удалось создать каталог хранилища", sudo=True,
    )


@broker.task("vms_hub.prepare")
async def vms_hub_prepare(task_id: str) -> None:
    """Подготовить сервер как VMS-hub (идемпотентно).

    Что делает: заходит по SSH под управляющей учёткой hub'а, проверяет
    `/dev/kvm`, ставит пакеты по семейству ОС (`apt`/`dnf`), доклеивает
    управляющего пользователя в libvirt/kvm-группы, чинит qemu.conf (cgroup +
    parsec `security_driver=none`), поднимает системный `libvirtd`, настраивает
    мост `br0` над физическим NIC, правит firewall (FORWARD/DOCKER-USER ACCEPT
    br0 + `bridge-nf-call-iptables=0`), поднимает host-only NAT-мост natbr0,
    storage-pool `vms` и тянет образы каталога с FTP. Исход докладывает
    server_service (`vms-hub-state`).

    Если мост `br0` создаётся впервые (живьём поднять его над управляющим NIC
    нельзя — рвётся SSH) или сняли kiosk-режим (parsec-kiosk2), хабу нужен
    reboot: для br0 ставим reboot-переживающий guard, перезагружаем
    хаб и ждём его подъёма (`_wait_hub_back`), и только после этого докладываем
    успех. Мост встаёт на следующей загрузке; не поднялась сеть — guard
    откатывает адресацию из бэкапа; не поднялся хаб за окно —
    `VMS_HUB_REBOOT_TIMEOUT`, оператор повторит prepare. Если ни br0, ни kiosk
    менять не пришлось — обычный callback без перезагрузки.

    Параметры: `task_id`. Payload — `server_id`, `host`/`hub_host` (str ip),
    `phy_if`, `os_family` (`apt`|`dnf`), `storage_pool_path` (опц., деф.
    `/vms`), `image_refs` (список имён/URL боксов), management-хинты.

    Возвращает: `{server_id, prepared, phy_if, os_family, images}`.

    Возможные ошибки: `VMS_HUB_NO_KVM` (нет `/dev/kvm` — fail без остального),
    `VMS_HUB_*` на конкретном шаге. На любой ошибке — best-effort
    `vms-hub-state{prepared:false, error}`.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload.get("hub_server_id") or payload.get("server_id")
        target_dept = payload.get("target_department_id")
        host_label = str(
            payload.get("host") or payload.get("hub_host") or server_id or "hub",
        )
        os_family = payload.get("os_family", "apt")
        if os_family not in ("apt", "dnf"):
            raise SshError(
                error_code="VM_INVALID_ARG",
                host=host_label,
                message=f"os_family {os_family!r} должен быть apt или dnf",
            )
        phy_if = validate_iface(payload["phy_if"], host_label)
        pool_path = validate_path(
            payload.get("storage_pool_path", VMS_DEFAULT_POOL_PATH), host_label,
        )
        image_refs = payload.get("image_refs") or []

        try:
            session, host = await open_hub_session(payload)
            async with session as ssh:
                # Precheck: без /dev/kvm дальше идти бессмысленно — падаем сразу,
                # не трогая пакеты/сеть/пул.
                rc, _out, _err = await ssh.run("test -e /dev/kvm", sudo=True)
                if rc != 0:
                    raise SshError(
                        error_code="VMS_HUB_NO_KVM",
                        host=host,
                        message=(
                            "на сервере нет /dev/kvm — аппаратная виртуализация "
                            "недоступна (не включён VT-x/AMD-V или это ВМ без "
                            "nested)"
                        ),
                    )
                # Kiosk-режим (parsec-kiosk2) на хабе не нужен и рвёт session-ВМ;
                # если он был активен/включён — гасим, потребуется reboot.
                kiosk_changed = await _disable_kiosk(ssh, host)
                await _run(
                    ssh, _install_packages_cmd(os_family), host,
                    "VMS_HUB_PACKAGES_FAILED", "установка пакетов hub'а упала",
                    sudo=True,
                )
                mgmt_user = payload.get("management_user")
                await ssh.run(_usermod_cmd(mgmt_user), sudo=True)
                await ssh.run(_QEMU_CONF_FIX, sudo=True)
                await ssh.run(_PARSEC_FIX, sudo=True)
                await _run(
                    ssh,
                    "sh -c 'systemctl enable --now libvirtd && "
                    "systemctl restart libvirtd'",
                    host,
                    "VMS_HUB_LIBVIRTD_FAILED", "не удалось поднять libvirtd",
                    sudo=True,
                )
                needs_reboot = await _setup_bridge(ssh, host, phy_if, os_family)
                await ssh.run(_FIREWALL_FIX, sudo=True)
                await ssh.run(_BRIDGE_NF_FIX, sudo=True)
                # Инфра под root: host-only NAT-мост natbr0 (dnsmasq + MASQUERADE)
                # и каталог хранилища. ВМ крутятся в системном libvirt от root,
                # поэтому setuid-хелпер моста и пользовательский демон не нужны.
                await _setup_nat_bridge(ssh, host)
                await _setup_storage(ssh, host, pool_path)
                await _ensure_pool(ssh, host, pool_path)
                images = await _download_images(ssh, host, pool_path, image_refs)
                # Ребут нужен, если впервые записан br0 (мост встаёт только на
                # следующей загрузке) или если сняли kiosk (эффект тоже после
                # reboot). Успех докладываем не сейчас, а после подъёма хаба.
                reboot_needed = needs_reboot or kiosk_changed
                if reboot_needed:
                    # br0 записан, но живьём не поднят: ставим reboot-переживающий
                    # guard ПЕРЕД перезагрузкой (страховка отката сети, если мост
                    # над управляющим NIC не встанет). Для чистого kiosk-ребута
                    # (br0 уже был) guard не нужен — сеть не трогали.
                    if needs_reboot:
                        gateway = await _default_gateway(ssh)
                        await _install_net_guard(ssh, host, gateway)
                    await _trigger_reboot(ssh, host)
                # Выходим из async with — сессия закрыта, хаб перезагружается.
            if reboot_needed:
                # Ждём, пока хаб поднимется и снова примет управляющую сессию;
                # только после этого докладываем prepared=True (см. финальный
                # callback ниже). Не поднялся за окно — VMS_HUB_REBOOT_TIMEOUT.
                await _wait_hub_back(payload, host)
        except Exception as exc:
            # Best-effort доложить провал, чтобы server_service снял «в
            # процессе» и показал причину; ошибку callback'а глушим.
            error_text = getattr(exc, "error_code", type(exc).__name__)
            try:
                await server_service_client.submit_vms_hub_state(
                    server_id, prepared=False, target_department_id=target_dept,
                    phy_if=phy_if, error=str(error_text),
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "vms_hub.prepare failed-callback errored server_id=%s",
                    server_id, exc_info=True,
                )
            raise

        await server_service_client.submit_vms_hub_state(
            server_id, prepared=True, target_department_id=target_dept,
            phy_if=phy_if,
        )
        return {
            "server_id": server_id,
            "prepared": True,
            "phy_if": phy_if,
            "os_family": os_family,
            "images": images,
        }

    await run_task(
        task_id,
        audit_action="vms_hub.prepare",
        audit_target_type="server",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_PREPARE,
    )


# ── vm.create ────────────────────────────────────────────────────────────────


def _virt_install_cmd(
    name: str, cpu: int, ram_mb: int, pool_path: str, network_mode: str,
    graphics: str = "vnc",
) -> str:
    """Собрать `virt-install --import` под ВМ (qemu:///system, root).

    universal всегда собирается на мосту `br0` (статику каждой версии льём в диск
    offline, гость поднимается с боевым адресом сразу). single с
    `network_mode=bridge` идёт на `br0` (боевой LAN, статик-адрес), `nat` — на
    host-only мосту `natbr0`: приватная подсеть `192.168.100.0/24`, адрес выдаёт
    выделенный dnsmasq хаба, наружу — через MASQUERADE. Оба режима цепляют NIC
    штатным `--network bridge=<мост>,model=virtio`: системный libvirt под root
    сам поднимает tap на мосту, отдельный qemu-bridge-helper и qemu-commandline
    не нужны. Отличается только имя моста. NAT-гость виден с хаба (адрес на
    natbr0), поэтому провижн/консоль ходят к нему джампом с хаба.

    `--cpu host-model` пробрасывает фичи хоста как есть, включая vmx/svm для
    nested там, где хост их отдаёт; форсить `+vmx` нельзя — на хостах без vmx
    (AMD, не-nested) virt-install падает целиком. MAC libvirt генерит сам —
    домену не нужен детерминированный адрес.

    `graphics` — тип графической консоли (`vnc`|`spice`, деф. vnc). Слушаем на
    `0.0.0.0`, чтобы websockify/spice-прокси с хаба мог дотянуться до порта
    дисплея; `console_prep` потом читает конкретный порт.
    """
    gfx = "spice" if graphics == "spice" else "vnc"
    # bridge/nat отличаются только мостом.
    br = bridge_label() if network_mode == "bridge" else VMS_NAT_BRIDGE
    return (
        f"virt-install -n {name} --memory {ram_mb} --vcpus {cpu} --import "
        f"--disk {pool_path}/{name}.qcow2,format=qcow2,bus=virtio "
        f"--network bridge={br},model=virtio "
        f"--os-variant {VMS_OS_VARIANT} --cpu host-model --autostart "
        f"--graphics {gfx},listen=0.0.0.0 --noautoconsole"
    )


async def _guest_ip(ssh, host: str, name: str) -> str:
    """Получить IP гостя через `virsh domifaddr` (session). Raise если нет.

    Пробуем lease-таблицу, затем qemu-guest-agent. У SLIRP-сети (`--network
    user`) lease нет вовсе — адрес отдаёт только agent-источник.
    """
    _rc, out, _err = await ssh.run(
        f"{LIBVIRT_SESSION_ENV} virsh domifaddr {name} --source lease", sudo=True,
    )
    ip = parse_domifaddr(out)
    if ip is None:
        # fallback на agent-источник (qemu-guest-agent установлен в провижне);
        # для SLIRP это единственный рабочий путь.
        _rc, out2, _err2 = await ssh.run(
            f"{LIBVIRT_SESSION_ENV} virsh domifaddr {name} --source agent",
            sudo=True,
        )
        ip = parse_domifaddr(out2)
    if ip is None:
        raise SshError(
            error_code="VM_GUEST_NO_IP",
            host=host,
            message=f"ВМ {name} не получила IP (нет lease/agent-адреса)",
        )
    return ip


# Ожидание DHCP-lease гостя на natbr0. dnsmasq выдаёт адрес за секунды после
# старта NIC; поллим lease-файл, пока по MAC гостя не появится строка.
_NAT_LEASE_ATTEMPTS = 30
_NAT_LEASE_DELAY_S = 4.0

_NAT_LEASE_IP_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def _parse_nat_lease_ip(text: str) -> str | None:
    """IP из строки dnsmasq-lease (`<expiry> <mac> <ip> <host> <clientid>`).

    На вход подаётся уже отфильтрованный по MAC вывод (`grep <mac> <leasefile>`
    на хабе), поэтому берём первую строку с валидным IPv4 в третьем поле.
    """
    for raw in (text or "").splitlines():
        parts = raw.split()
        if len(parts) >= 3 and _NAT_LEASE_IP_RE.match(parts[2]):
            return parts[2]
    return None


def _nat_static_ip(name: str) -> str:
    """Детерминированный статический адрес NAT-гостя в подсети natbr0.

    NAT-гость сидит на статике (как bridge), а не на DHCP: на боксе включены и
    NetworkManager, и ifupdown, из-за чего dhclient на eth0 не поднимается
    надёжно, а ifupdown-статика работает. Октет — из хэша имени (10..249,
    стабилен между ретраями), `.1` (шлюз natbr0) исключён.
    """
    octet = int(hashlib.sha256(name.encode()).hexdigest()[:6], 16) % 240 + 10
    return f"192.168.100.{octet}"


async def _wait_guest_ssh(
    ssh, host: str, guest_ip: str, *, attempts: int = 30, delay: float = 6.0,
    connect=None,
) -> None:
    """Дождаться, пока гость примет SSH.

    Гость только что стартовал из virt-install — sshd поднимается не мгновенно.
    Провижн (bridge берёт IP напрямую, без ожидания dhcp-lease) без этой паузы
    бьёт по гостю раньше времени и падает. Пробуем `true` по SSH до успеха.
    `connect` — коннектор входа на гостя; по умолчанию `u`/`1`, в managed-сборке
    после сноса `u` сюда передаётся ключевой коннектор.
    """
    if connect is None:
        connect = guest_connector(guest_ip)
    for _ in range(attempts):
        rc, _out, _err = await ssh.run(connect("true"))
        if rc == 0:
            return
        await asyncio.sleep(delay)
    raise SshError(
        error_code="VM_PROVISION_FAILED", host=host,
        message=f"гость {guest_ip} не принял SSH за отведённое время",
    )


async def _provision_guest_base(
    ssh, host: str, hostname: str, guest_ip: str,
    *, base_login: str | None = None, base_password: str | None = None,
) -> None:
    """Базовый провижн гостя: hostname (+ /etc/hosts), ntp, зависимости.

    Идёт на «сырой» бокс под его базовой учёткой (`base_login`/`base_password`;
    без них — дефолт `u`/`1`).
    """
    conn = guest_connector(guest_ip, login=base_login, password=base_password)
    await _wait_guest_ssh(ssh, host, guest_ip, connect=conn)
    await _run(
        ssh,
        conn(f"hostnamectl set-hostname {hostname}", sudo=True),
        host, "VM_PROVISION_FAILED", "не удалось задать hostname гостю",
    )
    # Без записи в /etc/hosts sudo ругается «unable to resolve host <hostname>»
    # и каждая привилегированная команда тянет за собой таймаут резолва.
    await _run(
        ssh,
        conn(
            f"bash -c 'grep -q \" {hostname}$\" /etc/hosts "
            f"|| echo \"127.0.1.1 {hostname}\" >> /etc/hosts'",
            sudo=True,
        ),
        host, "VM_PROVISION_FAILED", "не удалось прописать hostname в /etc/hosts",
    )
    await ssh.run(conn("timedatectl set-ntp true", sudo=True))
    # deps — convenience-тулзы (rsync/gcc/qemu-guest-agent), не критичны для
    # работы/управления ВМ. Ставим best-effort: у NAT-гостя apt-репо может быть
    # недостижимо через MASQUERADE, а валить весь create из-за опциональных
    # пакетов нельзя. bridge (LAN) их получит штатно.
    rc, _out, err = await ssh.run(
        conn(
            f"DEBIAN_FRONTEND=noninteractive apt-get install -y {_GUEST_DEPS}",
            sudo=True,
        ),
    )
    if rc != 0:
        logger.warning(
            "vm.create: не поставились guest-deps в %s (apt rc=%s) — "
            "ВМ создана без опциональных пакетов: %s",
            guest_ip, rc, (err or "").strip()[:200],
        )


# Публичный SSH-ключ аккаунта: base64/PEM-безопасный набор для отбоя shell-мета
# до заливки в госте (сам ключ уходит base64-обёрнутым, здесь — вход-валидатор).
_PUBKEY_RE = re.compile(r"^[A-Za-z0-9+/=@:.,_ \-]+$")


async def _provision_guest_accounts(
    ssh, host: str, guest_ip: str, accounts, host_label: str,
    target_dept: str | None, *, connect=None,
) -> list[str]:
    """Завести привязанные к ВМ учётки в госте (`useradd` + пароль/ключ/группы).

    Каждый элемент `accounts` — dict привязанного `server_account`:
    `{login, password?, ssh_public_key?, has_sudo?, unix_groups?, account_id?,
    server_id?}`. Пароль берём из `password` (если server_service положил его в
    dispatch); иначе тянем через internal: по `server_id`+`account_id`
    (`fetch_account_password`) или, когда исходный сервер неизвестен — по одному
    `account_id` (`fetch_account_password_by_id`). Заводить учётку важнее пароля:
    если пароль вытянуть не удалось (нет доступа/эндпоинта), пользователя всё
    равно создаём, только без `chpasswd`. useradd/группы идемпотентны; их фейл
    критичен (падаем `VM_CREATE_FAILED`). `connect` — коннектор входа на гостя;
    по умолчанию `u`/`1`, в managed-сборке после сноса `u` — ключевой коннектор.
    Возвращаем список заведённых логинов.
    """
    if connect is None:
        connect = guest_connector(guest_ip)
    provisioned: list[str] = []
    for acc in accounts or []:
        if not isinstance(acc, dict):
            continue
        login = validate_name(str(acc.get("login") or ""), host_label, "account login")
        password = acc.get("password")
        account_id = acc.get("account_id") or acc.get("id")
        source_server_id = acc.get("server_id")
        if password is None and account_id:
            try:
                if source_server_id:
                    creds = await server_service_client.fetch_account_password(
                        str(source_server_id), str(account_id), target_dept,
                    )
                else:
                    creds = await server_service_client.fetch_account_password_by_id(
                        str(account_id), target_dept,
                    )
                password = creds.get("password")
            except CredentialFetchError:
                logger.warning(
                    "vm provision: пароль учётки %s недоступен — заводим без пароля",
                    login,
                )

        groups = [
            validate_name(str(g), host_label, "unix group")
            for g in (acc.get("unix_groups") or [])
        ]
        if acc.get("has_sudo") and "sudo" not in groups:
            groups.append("sudo")
        gopt = f" -G {','.join(groups)}" if groups else ""
        # useradd идемпотентно (уже есть — не падаем); группы добиваем usermod'ом.
        await _run(
            ssh,
            connect(
                f"bash -c 'id {login} >/dev/null 2>&1 "
                f"|| useradd -m{gopt} {login}'",
                sudo=True,
            ),
            host, "VM_CREATE_FAILED",
            f"не удалось завести пользователя {login} в госте",
        )
        if groups:
            await _run(
                ssh,
                connect(
                    f"usermod -aG {','.join(groups)} {login}", sudo=True,
                ),
                host, "VM_CREATE_FAILED",
                f"не удалось добавить группы пользователю {login}",
            )
        if password:
            password = _validate_guest_password(str(password), host_label)
            await _run(
                ssh,
                connect(
                    f"bash -c 'echo {login}:{password} | chpasswd'",
                    sudo=True,
                ),
                host, "VM_CREATE_FAILED",
                f"не удалось задать пароль пользователю {login}",
            )
        public_key = acc.get("ssh_public_key")
        if public_key:
            if not _PUBKEY_RE.fullmatch(str(public_key)):
                raise SshError(
                    error_code="VM_INVALID_ARG", host=host_label,
                    message=f"публичный ключ {login} содержит недопустимые символы",
                )
            # Ключ несёт пробелы — заливаем base64-обёрткой, чтобы не расклеить
            # вложенную guest-команду.
            b64 = base64.b64encode(str(public_key).encode()).decode()
            await _run(
                ssh,
                connect(
                    f"bash -c 'umask 077 && mkdir -p ~{login}/.ssh && "
                    f"echo {b64} | base64 -d >> ~{login}/.ssh/authorized_keys && "
                    f"chown -R {login}: ~{login}/.ssh'",
                    sudo=True,
                ),
                host, "VM_CREATE_FAILED",
                f"не удалось положить ключ пользователю {login}",
            )
        provisioned.append(login)
    return provisioned


async def _box_virtual_gb(ssh, host: str, box_path: str) -> int | None:
    """Виртуальный размер диска бокса в ГБ (`qemu-img info`). None — не распарсили."""
    _rc, out, _err = await ssh.run(
        f"{LIBVIRT_SESSION_ENV} qemu-img info --output=json {box_path}", sudo=True,
    )
    try:
        data = json.loads(out or "")
        vsize = int(data["virtual-size"])
    except (ValueError, TypeError, KeyError):
        return None
    return vsize // (1024 ** 3)


async def _fs_minimum_bytes(ssh, host: str, box_path: str) -> int | None:
    """Минимальный размер ФС корневого раздела бокса в байтах (libguestfs).

    `guestfish vfs-minimum-size` считает, до скольки можно ужать ext4 с учётом
    занятых блоков и метаданных. Читаем в read-only (guestfish сам делает
    write-overlay для e2fsck). None — не распарсили (нет числа в выводе).
    """
    _rc, out, _err = await ssh.run(
        f"{LIBVIRT_SESSION_ENV} guestfish --ro -a {box_path} run : "
        f"e2fsck-f {_ROOT_PARTITION} : vfs-minimum-size {_ROOT_PARTITION}",
        sudo=True,
    )
    nums = re.findall(r"\d+", out or "")
    if not nums:
        return None
    return int(nums[-1])


async def _clone_disk_shrink(
    ssh, host: str, box_path: str, target_path: str, disk_gb: int,
) -> None:
    """Сжать диск бокса до `disk_gb` ГБ, не порушив ext4.

    virt-resize сам ФС не ужимает, а `--shrink`/`--resize-force` на разделе, где
    ext4 занимает весь объём, рушат данные. Поэтому: (1) считаем минимальный
    размер ФС и отбиваем понятной ошибкой запрос меньше занятого места ДО работы;
    (2) поднимаем COW-overlay над боксом (сам бокс не трогаем); (3) offline
    ужимаем ext4 в overlay (`e2fsck` + `resize2fs`) до целевого минус зазор;
    (4) `virt-resize --shrink` переносит разметку в целевой диск и растягивает ФС
    обратно на весь новый раздел; (5) убираем overlay.
    """
    target_bytes = disk_gb * (1024 ** 3)
    fs_target = target_bytes - _SHRINK_FS_MARGIN_BYTES
    min_bytes = await _fs_minimum_bytes(ssh, host, box_path)
    if min_bytes is not None and fs_target <= min_bytes:
        need_gb = math.ceil((min_bytes + _SHRINK_FS_MARGIN_BYTES) / (1024 ** 3))
        raise SshError(
            error_code="VM_CREATE_FAILED", host=host,
            message=(
                f"запрошенный размер диска {disk_gb}G меньше занятого места "
                f"бокса — нужно минимум {need_gb}G"
            ),
        )

    work = f"{target_path}{_SHRINK_WORK_SUFFIX}"
    await ssh.run(f"rm -f {work}")
    await _run(
        ssh, f"qemu-img create -f qcow2 -b {box_path} -F qcow2 {work}", host,
        "VM_CREATE_FAILED", "не удалось создать overlay бокса для сжатия диска",
    )
    try:
        await _run(
            ssh,
            f"guestfish -a {work} run : e2fsck-f {_ROOT_PARTITION} : "
            f"resize2fs-size {_ROOT_PARTITION} {fs_target}",
            host, "VM_CREATE_FAILED",
            "не удалось ужать файловую систему бокса перед сжатием диска",
        )
        await _run(
            ssh, f"qemu-img create -f qcow2 {target_path} {disk_gb}G", host,
            "VM_CREATE_FAILED", "не удалось создать целевой диск ВМ",
        )
        await _run(
            ssh,
            f"virt-resize --shrink {_ROOT_PARTITION} {work} {target_path}",
            host, "VM_CREATE_FAILED", "virt-resize не смог сжать диск ВМ",
        )
    finally:
        await ssh.run(f"rm -f {work}")


async def _clone_disk_resized(
    ssh, host: str, box_path: str, target_path: str, disk_gb: int,
) -> None:
    """Клонировать диск бокса в новый qcow2 целевого размера через virt-resize.

    Создаём пустой целевой диск на `disk_gb` ГБ и переносим в него разметку+ФС
    бокса. Рост (`--expand`) virt-resize делает в один проход. Сжатие идёт
    отдельным путём (`_clone_disk_shrink`): virt-resize сам ФС не ужимает, надо
    сначала offline ужать ext4 в overlay бокса.
    """
    box_gb = await _box_virtual_gb(ssh, host, box_path)
    if box_gb is not None and disk_gb < box_gb:
        await _clone_disk_shrink(ssh, host, box_path, target_path, disk_gb)
        return
    await _run(
        ssh, f"qemu-img create -f qcow2 {target_path} {disk_gb}G", host,
        "VM_CREATE_FAILED", "не удалось создать целевой диск ВМ",
    )
    await _run(
        ssh,
        f"virt-resize --expand {_ROOT_PARTITION} {box_path} {target_path}",
        host, "VM_CREATE_FAILED",
        "virt-resize не смог расширить диск ВМ",
    )


async def _snapshot(ssh, host: str, name: str, snap: str) -> None:
    await _run(
        ssh, f"virsh snapshot-create-as {name} --name {snap} --atomic", host,
        "VM_SNAPSHOT_FAILED", f"не удалось создать снимок {snap}",
    )


async def _power_off_for_snapshot(ssh, name: str) -> None:
    """Выключить гостя перед снимком чистого managed-baseline (disk-only).

    Заказываем graceful `virsh shutdown` (qemu-guest-agent уже стоит) и поллим
    `domstate` до `off`. Не выключился штатно за окно — глушим жёстко
    (`virsh destroy`, non-zero на уже выключенном терпим). Снимок baseline
    снимаем на выключенной ВМ, чтобы он нёс чистое disk-состояние без памяти.
    """
    await ssh.run(f"{LIBVIRT_SESSION_ENV} virsh shutdown {name}", sudo=True)
    for _ in range(_SNAP_OFF_MAX_POLLS):
        _rc, out, _err = await ssh.run(
            f"{LIBVIRT_SESSION_ENV} virsh domstate {name}", sudo=True,
        )
        if map_domstate(out) == "off":
            return
        await asyncio.sleep(_SNAP_OFF_POLL_DELAY_S)
    await ssh.run(f"{LIBVIRT_SESSION_ENV} virsh destroy {name}", sudo=True)


def _os_baseline_entry(
    name: str, os_version: str, mode: str, *, is_system: bool = False,
) -> dict:
    """Rich-элемент снимка версии ОС для callback'а `submit_vm_snapshots`.

    Несёт `mode` (orel/smolensk), `os_version` и `kind=os_baseline` — по этим
    полям server_service группирует «чистые» снимки версий (контракт снимков).
    golden помечаем `is_system` (скрыт, защищён от ручного delete/revert).
    """
    entry: dict = {
        "name": name,
        "state": "ready",
        "kind": SNAPSHOT_KIND_OS_BASELINE,
        "mode": mode,
        "os_version": os_version,
    }
    if is_system:
        entry["is_system"] = True
    return entry


async def _build_universal(
    ssh, host: str, name: str, hostname: str, pool_path: str, ip_address: str,
    netmask: str, gateway: str, dns: list[str],
    os_versions: list[str], password: str | None, accounts,
    host_label: str, target_dept: str | None, *, mgmt=None, key_path: str | None = None,
    base_login: str | None = None, base_password: str | None = None,
) -> list[dict]:
    """Построить universal-ВМ: на каждую версию — golden + Орёл + Смоленск.

    Домен уже определён на мосту `br0` (LAN без DHCP). Для каждой версии:
    гасим ВМ, переключаем внутренний qemu-снимок ОС (`qemu-img snapshot -a`),
    заливаем статику версии в диск offline (`virt-customize` — на br0 без DHCP
    гость иначе не получит адрес и будет недостижим; каждая версия несёт свой
    rootfs, поэтому инъекция идёт на каждую заново), поднимаем ВМ и заходим по
    боевому адресу.

    Если передан управляющий материал (`mgmt`/`key_path` — managed-сборка), для
    каждой версии заходим по `u`/`1`, ставим управляющего пользователя (ключ +
    пароль + sudo, qemu-guest-agent), проверяем вход по ключу, хардим sshd и
    сносим базовую учётку (`apply_managed_baseline`). Скрытый golden
    `<ver>_build` снимаем на ВЫКЛЮЧЕННОЙ ВМ — это чистый managed-baseline без
    привязанных пользователей. Затем поднимаем ВМ, добавляем привязанные учётки
    уже по управляющему ключу, снимаем deliverable Орла `<ver>_orel`, переводим
    гостя в Смоленск (по ключу) и снимаем `<ver>_smolensk`.

    Без управляющего материала (legacy-путь: `creds_stash_key` в payload нет)
    сохраняется прежнее поведение — провижн и снимки идут по `u`/`1`, базовая
    учётка не сносится, golden несёт привязанные учётки.

    Клон бокса под universal идёт plain `cp` (сохраняет внутренние qemu-снимки
    версий), а `disk_gb` для него игнорируется — версии зашиты в бокс
    фиксированного размера.
    """
    disk_path = f"{pool_path}/{name}.qcow2"
    static_ip = ip_address.split("/")[0]
    managed = mgmt is not None
    snapshots: list[dict] = []
    for raw_ver in os_versions:
        ver = validate_name(str(raw_ver), host_label, "os_version")
        # stop (может быть уже off) под root
        await ssh.run(f"{LIBVIRT_SESSION_ENV} virsh destroy {name}", sudo=True)
        await _run(
            ssh, f"qemu-img snapshot -a {ver} {disk_path}", host,
            "VM_CREATE_FAILED", f"не удалось переключить ОС на {ver}",
        )
        # статику версии заливаем в диск offline (домен выключен) — гость
        # поднимется на br0 сразу с боевым адресом и будет доступен по SSH.
        await write_static_interfaces_offline(
            ssh, host, disk_path, static_ip, netmask, gateway, dns,
            error_code="VM_CREATE_FAILED",
        )
        await _run(ssh, f"virsh start {name}", host,
                   "VM_CREATE_FAILED", f"не удалось запустить ВМ на версии {ver}")
        await _provision_guest_base(
            ssh, host, hostname, static_ip,
            base_login=base_login, base_password=base_password,
        )

        if managed:
            # bootstrap управления по базовой учётке: заводим dbos-юзера, проверяем
            # вход по ключу, сносим базовую учётку. Дальше — только по ключу.
            await apply_managed_baseline(
                ssh, host, static_ip, mgmt, key_path,
                base_login=base_login, base_password=base_password,
            )
            guest_conn = guest_key_connector(
                static_ip, mgmt["management_user"], key_path,
            )
            # чистый managed-baseline (без привязанных юзеров) — на выключенной ВМ
            await _power_off_for_snapshot(ssh, name)
            await _snapshot(ssh, host, name, f"{ver}{_GOLDEN_SUFFIX}")
            snapshots.append(
                _os_baseline_entry(
                    f"{ver}{_GOLDEN_SUFFIX}", ver, MODE_OREL, is_system=True,
                ),
            )
            # поднимаем и добавляем привязанные учётки уже по управляющему ключу
            await _run(ssh, f"virsh start {name}", host,
                       "VM_CREATE_FAILED", f"не удалось поднять ВМ версии {ver}")
            await _wait_guest_ssh(ssh, host, static_ip, connect=guest_conn)
            await _provision_guest_accounts(
                ssh, host, static_ip, accounts, host_label, target_dept,
                connect=guest_conn,
            )
            await _snapshot(ssh, host, name, f"{ver}_{MODE_OREL}")
            snapshots.append(_os_baseline_entry(f"{ver}_{MODE_OREL}", ver, MODE_OREL))
            await switch_guest_to_smolensk(
                ssh, host, static_ip, error_code="VM_CREATE_FAILED",
                connect=guest_conn,
            )
            await _snapshot(ssh, host, name, f"{ver}_{MODE_SMOLENSK}")
            snapshots.append(
                _os_baseline_entry(f"{ver}_{MODE_SMOLENSK}", ver, MODE_SMOLENSK),
            )
            continue

        # legacy-путь (без управляющего материала): всё по `u`/`1`
        await _provision_guest_accounts(
            ssh, host, static_ip, accounts, host_label, target_dept,
        )
        # скрытый golden текущей версии (сырой бокс-стейт, креды u:1)
        await _snapshot(ssh, host, name, f"{ver}{_GOLDEN_SUFFIX}")
        snapshots.append(
            _os_baseline_entry(
                f"{ver}{_GOLDEN_SUFFIX}", ver, MODE_OREL, is_system=True,
            ),
        )
        if password:
            await ssh.run(
                guest_ssh(
                    static_ip,
                    f"bash -c 'echo {base_login or VMS_GUEST_LOGIN}:{password} "
                    "| chpasswd'",
                    sudo=True,
                    login=base_login, password=base_password,
                ),
            )
        # deliverable Орла
        await _snapshot(ssh, host, name, f"{ver}_{MODE_OREL}")
        snapshots.append(_os_baseline_entry(f"{ver}_{MODE_OREL}", ver, MODE_OREL))
        # перевод гостя в Смоленск (уровень 2 + МРД/МКЦ + reboot) → deliverable
        await switch_guest_to_smolensk(
            ssh, host, static_ip, error_code="VM_CREATE_FAILED",
        )
        await _snapshot(ssh, host, name, f"{ver}_{MODE_SMOLENSK}")
        snapshots.append(
            _os_baseline_entry(f"{ver}_{MODE_SMOLENSK}", ver, MODE_SMOLENSK),
        )
    return snapshots


async def _build_single(
    ssh, host: str, name: str, hostname: str, guest_ip: str,
    os_version: str | None, accounts, host_label: str,
    target_dept: str | None, *, mgmt=None, key_path: str | None = None,
    base_login: str | None = None, base_password: str | None = None,
) -> list[dict]:
    """Построить single-ВМ из конкретного бокса: golden + Орёл + Смоленск.

    Диск уже приведён к нужному размеру до virt-install (offline, virt-resize) в
    `vm.create`, адрес гостя резолвит caller (bridge — статик из пула, nat —
    lease natbr0). Провижним гостя (hostname/ntp/deps) по базовой учётке.

    Если передан управляющий материал (`mgmt`/`key_path` — managed-сборка),
    ставим управляющего пользователя и сносим базовую учётку
    (`apply_managed_baseline`); привязанные учётки заводим уже по управляющему
    ключу. Без управляющего материала (legacy) — по базовой учётке бокса.

    При известной `os_version` строим тот же набор снимков, что и universal для
    одной версии: скрытый golden `<ver>_build` (чистый baseline, снят на
    ВЫКЛЮЧЕННОЙ ВМ) + deliverable Орла `<ver>_orel` + `<ver>_smolensk` (после
    перевода в Смоленск вживую — `astra-modeswitch` + МРД/МКЦ + reboot). У
    single-бокса нет пред-запечённых qemu-снимков версий, поэтому режимы
    переключаются на живой ВМ, без `qemu-img snapshot -a`. ВМ по итогу остаётся
    поднятой; финальный `virsh start` в `vm.create` — no-op (natbr0 гарантирован).

    Если `os_version` не задана — снимаем прежний одиночный `build` на
    выключенной ВМ (без режимов) и логируем.
    """
    await _provision_guest_base(
        ssh, host, hostname, guest_ip,
        base_login=base_login, base_password=base_password,
    )
    managed = mgmt is not None
    if managed:
        await apply_managed_baseline(
            ssh, host, guest_ip, mgmt, key_path,
            base_login=base_login, base_password=base_password,
        )
        guest_conn = guest_key_connector(guest_ip, mgmt["management_user"], key_path)
    else:
        guest_conn = guest_connector(
            guest_ip, login=base_login, password=base_password,
        )

    if not os_version:
        # Версия неизвестна — старое одиночное поведение: один снимок build на
        # выключенной ВМ (disk-only), без режимов.
        await _provision_guest_accounts(
            ssh, host, guest_ip, accounts, host_label, target_dept,
            connect=guest_conn,
        )
        await _power_off_for_snapshot(ssh, name)
        await _snapshot(ssh, host, name, "build")
        logger.warning(
            "vm.create single-бокс без os_version — снят одиночный build без "
            "режимов name=%s", name,
        )
        return [{"name": "build", "state": "ready"}]

    ver = validate_name(str(os_version), host_label, "os_version")
    snapshots: list[dict] = []
    # чистый baseline (без привязанных юзеров) — на выключенной ВМ, без памяти
    await _power_off_for_snapshot(ssh, name)
    await _snapshot(ssh, host, name, f"{ver}{_GOLDEN_SUFFIX}")
    snapshots.append(
        _os_baseline_entry(f"{ver}{_GOLDEN_SUFFIX}", ver, MODE_OREL, is_system=True),
    )
    # поднимаем и добавляем привязанные учётки
    await _run(ssh, f"virsh start {name}", host,
               "VM_CREATE_FAILED", f"не удалось поднять ВМ версии {ver}")
    await _wait_guest_ssh(ssh, host, guest_ip, connect=guest_conn)
    await _provision_guest_accounts(
        ssh, host, guest_ip, accounts, host_label, target_dept, connect=guest_conn,
    )
    await _snapshot(ssh, host, name, f"{ver}_{MODE_OREL}")
    snapshots.append(_os_baseline_entry(f"{ver}_{MODE_OREL}", ver, MODE_OREL))
    # перевод гостя в Смоленск (уровень 2 + МРД/МКЦ + reboot) → deliverable
    await switch_guest_to_smolensk(
        ssh, host, guest_ip, error_code="VM_CREATE_FAILED", connect=guest_conn,
    )
    await _snapshot(ssh, host, name, f"{ver}_{MODE_SMOLENSK}")
    snapshots.append(_os_baseline_entry(f"{ver}_{MODE_SMOLENSK}", ver, MODE_SMOLENSK))
    return snapshots


@broker.task("vm.create")
async def vm_create(task_id: str) -> None:
    """Создать ВМ на hub'е (порт флоу референса).

    Что делает: клонирует диск бокса в пул (с disk_gb — через virt-resize,
    рост/сжатие; иначе cp), собирает домен `virt-install --import`, провижнит
    гостя по SSH (`u`/`1` + hostname из `hostname or name`, привязанные учётки) и
    снимает снимки. Universal-бокс (`vm_station`) несёт несколько версий ОС на
    одном диске — для каждой строит скрытый golden `<ver>_build` +
    deliverable `<ver>_orel` (Орёл) + `<ver>_smolensk` (Смоленск, после
    astra-modeswitch + МРД/МКЦ + reboot); single-бокс с известной версией — тот
    же набор, без версии — один снимок `build`.
    Снимки докладывает `vms/{id}/snapshots` (с kind/mode/os_version), состояние —
    `vms/{id}/state`.

    Параметры: `task_id`. Payload — `vm_id`, `hub_host` (str ip), `name`,
    `hostname` (опц.), `cpu`, `ram_mb`, `disk_gb`, `box`, `network_mode`
    (`bridge`|`nat`), `ip_address`, `box_url` (для скачивания single-бокса),
    `os_versions` (для universal), `os_version` (single), `accounts` (список
    привязанных учёток), опц. `password`, `graphics` (`vnc`|`spice`, деф. vnc),
    management-хинты.

    Возвращает: `{vm_id, name, box, network_mode, power_state, status,
    ip_address, snapshots}` — snapshots только plain (без скрытого golden).

    Возможные ошибки: `VM_INVALID_ARG`, `VM_CREATE_FAILED`, `VM_SNAPSHOT_FAILED`,
    `VM_GUEST_NO_IP`, `VM_PROVISION_FAILED`. На любой ошибке — best-effort
    `vms/{id}/state{status:'error', error}`.
    """
    async def _impl(payload: dict) -> dict:
        vm_id = payload["vm_id"]
        target_dept = payload.get("target_department_id")
        host_label = str(
            payload.get("host") or payload.get("hub_host")
            or payload.get("hub_server_id") or "hub",
        )
        name = validate_name(payload["name"], host_label, "name")
        # hostname — отдельное поле; пусто → имя ВМ (валидатор тот же).
        hostname = validate_name(
            str(payload.get("hostname") or payload["name"]), host_label, "hostname",
        )
        box = validate_name(payload["box"], host_label, "box")
        cpu = positive_int(payload["cpu"], host_label, "cpu")
        ram_mb = positive_int(payload["ram_mb"], host_label, "ram_mb")
        disk_gb = int(payload.get("disk_gb") or 0)
        accounts = payload.get("accounts") or []
        os_version = payload.get("os_version")
        network_mode = payload.get("network_mode", "bridge")
        if network_mode not in ("bridge", "nat"):
            raise SshError(
                error_code="VM_INVALID_ARG", host=host_label,
                message=f"network_mode {network_mode!r} должен быть bridge или nat",
            )
        graphics = payload.get("graphics", "vnc")
        if graphics not in ("vnc", "spice"):
            raise SshError(
                error_code="VM_INVALID_ARG", host=host_label,
                message=f"graphics {graphics!r} должен быть vnc или spice",
            )
        ip_address = payload.get("ip_address")
        if ip_address:
            ip_address = validate_ip(str(ip_address), host_label)
        pool_path = validate_path(
            payload.get("storage_pool_path", VMS_DEFAULT_POOL_PATH), host_label,
        )
        box_url = payload.get("box_url")
        os_versions = payload.get("os_versions") or []
        password = payload.get("password")
        is_universal = box == VMS_UNIVERSAL_BOX
        # Для NAT адрес гостя заранее неизвестен — резолвим по lease natbr0 и
        # докладываем его в state (bridge/universal несут статик из payload).
        nat_ip: str | None = None
        # Управляющий материал для managed-сборки: server_service генерит per-VM
        # пару+пароль и кладёт в dispatch-stash, в payload едет `creds_stash_key`.
        # Есть ключ → сборка сразу заводит dbos-учётку и сносит `u` (каждый снимок
        # уже managed). Нет ключа (старый dispatch/ретрай до апгрейда) → legacy-
        # путь по `u`/`1` без prepare.
        stash_key = payload.get("creds_stash_key")
        mgmt = await load_mgmt_material(stash_key, host_label) if stash_key else None
        managed = mgmt is not None
        # Базовая учётка «сырого» бокса: у каждого бокса своя (реестр наполняет
        # payload). Без хинтов — дефолт `u`/`1`.
        base_login = payload.get("base_user_login")
        base_password = payload.get("base_user_password")

        try:
            session, host = await open_hub_session(payload)
            async with session as ssh:
                # single-бокс может ещё не лежать в пуле — тянем по box_url.
                rc, _out, _err = await ssh.run(
                    f"test -f {pool_path}/{box}.qcow2",
                )
                if rc != 0:
                    # server_service обычно шлёт box_url; если нет — тянем
                    # каталог с FTP и резолвим сами (страховка).
                    if not box_url:
                        box_url = await resolve_box_url(ssh, box)
                    if not box_url:
                        raise SshError(
                            error_code="VM_CREATE_FAILED", host=host,
                            message=f"образ бокса {box} отсутствует в пуле и нет box_url",
                        )
                    url, _base = _image_url(str(box_url))
                    await _run(
                        ssh, f"wget -q -O {pool_path}/{box}.tar.gz {url}", host,
                        "VM_CREATE_FAILED", "не удалось скачать бокс",
                    )
                    await _run(
                        ssh, f"tar xzf {pool_path}/{box}.tar.gz -C {pool_path}", host,
                        "VM_CREATE_FAILED", "не удалось распаковать бокс",
                    )
                    await ssh.run(f"rm -f {pool_path}/{box}.tar.gz")
                # Идемпотентность ретраёв: если от прошлой попытки остался домен
                # того же имени, virt-install падает «диск занят». Снимаем его
                # (best-effort, диск перезальём клоном ниже).
                await ssh.run(
                    f"{LIBVIRT_SESSION_ENV} virsh destroy {name}", sudo=True,
                )
                await ssh.run(
                    f"{LIBVIRT_SESSION_ENV} virsh undefine {name} "
                    "--snapshots-metadata",
                    sudo=True,
                )
                box_path = f"{pool_path}/{box}.qcow2"
                target_path = f"{pool_path}/{name}.qcow2"
                # Клон диска под ВМ идёт ПОКА ОН OFFLINE (до virt-install): у
                # запущенного домена qcow2 залочен. Если задан disk_gb — клонируем
                # через virt-resize (рост И сжатие без порчи разметки); иначе
                # обычный cp сохраняет исходный размер бокса. Universal несёт
                # внутренние qemu-img снимки версий, virt-resize их сломал бы —
                # для него только cp.
                if disk_gb and not is_universal:
                    await _clone_disk_resized(
                        ssh, host, box_path, target_path, disk_gb,
                    )
                else:
                    await _run(
                        ssh, f"cp {box_path} {target_path}", host,
                        "VM_CREATE_FAILED", "не удалось клонировать диск бокса",
                    )
                if network_mode == "bridge" and ip_address and not is_universal:
                    # single-bridge: LAN статический, DHCP-сервера нет — на br0
                    # гость не получит адрес и будет недостижим по SSH. Заливаем
                    # статику в клон offline (virt-customize до virt-install),
                    # чтобы гость поднялся сразу на боевом ip_address.
                    netmask = validate_ip(
                        str(payload.get("netmask") or VMS_DEFAULT_NETMASK), host,
                    )
                    gateway = validate_ip(
                        str(payload.get("gateway") or VMS_DEFAULT_GATEWAY), host,
                    )
                    dns = normalize_dns(payload, host)
                    await write_static_interfaces_offline(
                        ssh, host, f"{pool_path}/{name}.qcow2",
                        ip_address, netmask, gateway, dns,
                        error_code="VM_CREATE_FAILED",
                    )
                elif network_mode == "nat" and not is_universal:
                    # NAT: статика в подсети natbr0 (шлюз/DNS 192.168.100.1,
                    # интернет через MASQUERADE). DHCP не используем — на боксе
                    # NM+ifupdown конфликтуют и dhclient на eth0 не поднимается;
                    # статику ifupdown применяет надёжно (как у bridge).
                    await write_static_interfaces_offline(
                        ssh, host, f"{pool_path}/{name}.qcow2",
                        _nat_static_ip(name), "255.255.255.0",
                        VMS_NAT_HOST_IP, [VMS_NAT_HOST_IP],
                        error_code="VM_CREATE_FAILED",
                    )
                # universal всегда на br0 (провижн версий идёт по боевому статик-
                # адресу); network_mode из payload его не переопределяет.
                install_network_mode = "bridge" if is_universal else network_mode
                await _run(
                    ssh,
                    _virt_install_cmd(
                        name, cpu, ram_mb, pool_path, install_network_mode,
                        graphics,
                    ),
                    host, "VM_CREATE_FAILED", "virt-install упал",
                )
                # Временный приватный ключ на hub'е для managed-сборки (проверка
                # входа по ключу + пост-хардинг шаги); подчищаем в finally.
                key_path = (
                    await _write_temp_key(ssh, host, mgmt["private_key"])
                    if managed else None
                )
                try:
                    if is_universal:
                        if not os_versions:
                            raise SshError(
                                error_code="VM_INVALID_ARG", host=host,
                                message="universal-ВМ требует непустой os_versions",
                            )
                        if not ip_address:
                            raise SshError(
                                error_code="VM_INVALID_ARG", host=host,
                                message=(
                                    "universal-ВМ требует ip_address — гость "
                                    "провижнится по статике на br0 (DHCP на LAN нет)"
                                ),
                            )
                        netmask = validate_ip(
                            str(payload.get("netmask") or VMS_DEFAULT_NETMASK), host,
                        )
                        gateway = validate_ip(
                            str(payload.get("gateway") or VMS_DEFAULT_GATEWAY), host,
                        )
                        dns = normalize_dns(payload, host)
                        snapshots = await _build_universal(
                            ssh, host, name, hostname, pool_path, ip_address,
                            netmask, gateway, dns, os_versions, password, accounts,
                            host_label, target_dept, mgmt=mgmt, key_path=key_path,
                            base_login=base_login, base_password=base_password,
                        )
                    else:
                        if network_mode == "bridge" and ip_address:
                            # bridge: статику залили в диск offline (virt-customize
                            # до virt-install), гость уже на br0 с этим адресом.
                            guest_ip = ip_address.split("/")[0]
                        elif network_mode == "nat":
                            # nat: статика в подсети natbr0 (залита offline выше),
                            # детерминирована от имени — гость на этом адресе.
                            guest_ip = _nat_static_ip(name)
                            nat_ip = guest_ip
                        else:
                            guest_ip = await _guest_ip(ssh, host, name)
                        # NAT цепляет tap к natbr0 в момент старта — гарантируем
                        # мост до любого подъёма ВМ (внутренние старты снимков
                        # режимов в _build_single + финальный serve). Идемпотентно;
                        # не зависим от того, что unit prepare пережил reboot хаба.
                        if network_mode == "nat":
                            await _setup_nat_bridge(ssh, host)
                        snapshots = await _build_single(
                            ssh, host, name, hostname, guest_ip, os_version,
                            accounts, host_label, target_dept,
                            mgmt=mgmt, key_path=key_path,
                            base_login=base_login, base_password=base_password,
                        )
                        # golden снят на выключенной ВМ; при известной версии
                        # _build_single оставляет ВМ поднятой (Орёл/Смоленск), при
                        # неизвестной — выключенной. В обоих случаях доводим до
                        # рабочего состояния (уже запущенный virsh start — no-op).
                        await ssh.run(
                            f"{LIBVIRT_SESSION_ENV} virsh start {name}", sudo=True,
                        )
                finally:
                    if key_path is not None:
                        await _shred_temp_key(ssh, key_path)
                _rc, dom_out, _err = await ssh.run(
                    f"{LIBVIRT_SESSION_ENV} virsh domstate {name}", sudo=True,
                )
                power_state = map_domstate(dom_out)
        except Exception as exc:
            error_text = getattr(exc, "error_code", type(exc).__name__)
            try:
                await server_service_client.submit_vm_state(
                    vm_id, target_department_id=target_dept,
                    status="error", error=str(error_text),
                    clear_busy_state=True,
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "vm.create failed-callback errored vm_id=%s", vm_id,
                    exc_info=True,
                )
            raise

        # Снимки уходят отдельным rich-callback'ом (`vms/{id}/snapshots`): у него
        # есть kind/mode/os_version/is_system, а `vms/{id}/state` их не принимает
        # (раньше снимки слались в state и молча терялись — базовый снимок не был
        # виден в UI). Plain-имена (без скрытого golden) — в state и в result.
        plain = [s["name"] for s in snapshots if not s.get("is_system")]
        # NAT-адрес известен только после старта (lease natbr0); bridge/universal
        # несут статик из payload.
        report_ip = nat_ip or ip_address
        await server_service_client.submit_vm_snapshots(
            vm_id, snapshots, target_department_id=target_dept,
        )
        await server_service_client.submit_vm_state(
            vm_id, target_department_id=target_dept,
            power_state=power_state, ip_address=report_ip, status="free",
            snapshots=plain, clear_busy_state=True,
        )
        if managed:
            # Сборка сделала ВМ управляемой (dbos-учётка, `u` снесён): помечаем
            # ВМ managed зеркально `vm.prepare` (`is_managed=True`,
            # `management_user`, снят `mgmt_creds_pending_apply`). Управляющий
            # материал server_service уже держит у себя — обратно ничего секретного
            # не шлём. Stash больше не нужен.
            await server_service_client.submit_vm_prepared(
                vm_id, mgmt["management_user"], target_department_id=target_dept,
            )
            await _delete_stash(stash_key)
        return {
            "vm_id": vm_id,
            "name": name,
            "box": box,
            "network_mode": network_mode,
            "power_state": power_state,
            "status": "free",
            "ip_address": report_ip,
            "snapshots": plain,
        }

    await run_task(
        task_id,
        audit_action="vm.create",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_CREATE,
    )


# ── vm.power ─────────────────────────────────────────────────────────────────


@broker.task("vm.power")
async def vm_power(task_id: str) -> None:
    """Управление питанием ВМ (`virsh start|shutdown|reboot|reset|destroy`).

    Что делает: заходит на hub по SSH, выполняет `virsh <action> <vm>`, читает
    новое состояние `virsh domstate`. `shutdown` — graceful (добавлен к старому
    набору), `destroy` — жёсткое выключение. Исход докладывает server_service
    (`vms/{id}/state{power_state}`).

    Параметры: `task_id`. Payload — `vm_id`, `hub_host` (str ip), `vm_name`,
    `action` (`start`|`shutdown`|`reboot`|`reset`|`destroy`), management-хинты.

    Возвращает: `{vm_id, vm_name, action, power_state}`.

    Возможные ошибки: `VM_INVALID_ARG` (неизвестный action / имя),
    `VM_POWER_FAILED` (virsh упал). Read-only `domstate` после action — если
    ВМ выключилась (`shutdown`/`destroy`), power_state будет `off`/`shutting_down`.
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
        action = payload.get("action")
        if action not in POWER_VERBS:
            raise SshError(
                error_code="VM_INVALID_ARG", host=host_label,
                message=(
                    f"action {action!r} должен быть одним из "
                    f"{sorted(POWER_VERBS)}"
                ),
            )
        network_mode = payload.get("network_mode")

        session, host = await open_hub_session(payload)
        async with session as ssh:
            # NAT-ВМ цепляет NIC к natbr0 через qemu-bridge-helper в момент
            # старта домена. Если мост не поднят (после reboot хаба) — tap не
            # прицепится и гость останется без сети. Перед подъёмом домена
            # гарантируем natbr0 (идемпотентно). bridge-ВМ на br0 не трогаем.
            if network_mode == "nat" and action in ("start", "reboot", "reset"):
                await _setup_nat_bridge(ssh, host)
            verb = POWER_VERBS[action]
            # start идемпотентен на запущенной ВМ (virsh отдаст non-zero
            # "already active") — допускаем; destroy на выключенной тоже.
            ok = (0, 1) if action in ("start", "destroy", "shutdown") else (0,)
            await _run(
                ssh, f"virsh {verb} {vm_name}", host,
                "VM_POWER_FAILED", f"virsh {verb} упал", ok=ok,
            )
            _rc, dom_out, _err = await ssh.run(
                f"{LIBVIRT_SESSION_ENV} virsh domstate {vm_name}", sudo=True,
            )
            power_state = map_domstate(dom_out)

        await server_service_client.submit_vm_state(
            vm_id, target_department_id=target_dept, power_state=power_state,
        )
        return {
            "vm_id": vm_id,
            "vm_name": vm_name,
            "action": action,
            "power_state": power_state,
        }

    await run_task(
        task_id,
        audit_action="vm.power",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_POWER,
    )


# ── vm.delete ────────────────────────────────────────────────────────────────


@broker.task("vm.delete")
async def vm_delete(task_id: str) -> None:
    """Снести домен ВМ на hub'е (`virsh destroy` + `virsh undefine`).

    Что делает: заходит на hub по SSH, жёстко гасит домен (`virsh destroy` —
    non-zero на уже выключенной ВМ глушим) и удаляет его вместе с дисками и
    метаданными снимков (`virsh undefine <vm> --remove-all-storage
    --snapshots-metadata`, допустимые коды 0/1). Карточку ВМ server_service
    удаляет из БД сразу при dispatch'е, поэтому финального state-callback'а нет —
    обновлять уже нечего.

    Параметры: `task_id`. Payload — `vm_id`, `vm_name`/`name`, hub-блок,
    `target_department_id`.

    Возвращает: `{vm_id, vm_name, destroyed, undefined}`.

    Возможные ошибки: `VM_INVALID_ARG` (нет/битое имя),
    `VM_DELETE_FAILED` (undefine упал на неожиданном коде).
    """
    async def _impl(payload: dict) -> dict:
        vm_id = payload["vm_id"]
        host_label = str(
            payload.get("host") or payload.get("hub_host")
            or payload.get("hub_server_id") or "hub",
        )
        vm_name = validate_name(
            payload.get("vm_name") or payload["name"], host_label, "vm_name",
        )

        session, host = await open_hub_session(payload)
        async with session as ssh:
            # destroy на уже выключенном домене отдаёт non-zero — это не ошибка.
            await ssh.run(
                f"{LIBVIRT_SESSION_ENV} virsh destroy {vm_name}", sudo=True,
            )
            await _run(
                ssh,
                f"virsh undefine {vm_name} --remove-all-storage "
                "--snapshots-metadata",
                host, "VM_DELETE_FAILED",
                f"не удалось удалить домен {vm_name}",
                ok=(0, 1),
            )
        return {
            "vm_id": vm_id,
            "vm_name": vm_name,
            "destroyed": True,
            "undefined": True,
        }

    await run_task(
        task_id,
        audit_action="vm.delete",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_DELETE,
    )


# ── vm.list_packages ─────────────────────────────────────────────────────────


# Явный хинт os_family из payload → package manager, чтобы не гонять 6 проб по
# SSH, когда семейство ОС гостя уже известно server_service'у. Всё, что не
# распознали, уходит в живой детект внутри гостя.
_OS_FAMILY_TO_PM: dict[str, str] = {
    "apt": "dpkg", "dpkg": "dpkg", "debian": "dpkg", "astra": "dpkg",
    "dnf": "rpm", "rpm": "rpm", "yum": "rpm", "rhel": "rpm", "redos": "rpm",
}

# Порядок живого детекта пакетного менеджера в госте (тот же приоритет, что в
# `installed_packages._detect_package_manager`, но команды идут вложенным
# guest_ssh, а не напрямую по hub-сессии).
_GUEST_PM_PROBE: tuple[tuple[str, str], ...] = (
    ("dpkg", "command -v dpkg-query"),
    ("rpm", "command -v rpm"),
    ("apk", "command -v apk"),
    ("pacman", "command -v pacman"),
    ("portage", "command -v qlist"),
    ("xbps", "command -v xbps-query"),
)


async def _detect_guest_package_manager(
    ssh, host: str, guest_ip: str, *, connect=None,
) -> str:
    """Определить package manager внутри гостя ВМ (через вложенный коннектор).

    Зеркало `installed_packages._detect_package_manager`, но команды `command -v`
    идут в гостя вложенной сессией, а не на hub напрямую. `connect` — коннектор
    входа на гостя (по управляющему ключу на managed-ВМ, иначе по базовой учётке).
    Возвращает первый найденный менеджер по приоритету; ни одного —
    `SshError(NO_PACKAGE_MANAGER)`.
    """
    if connect is None:
        connect = guest_connector(guest_ip)
    for pm, probe in _GUEST_PM_PROBE:
        rc, _out, _err = await ssh.run(connect(probe))
        if rc == 0:
            return pm
    raise SshError(
        error_code="NO_PACKAGE_MANAGER",
        host=host,
        message=(
            "в госте ВМ нет поддерживаемого package manager'а "
            "(dpkg-query/rpm/apk/pacman/qlist/xbps-query)"
        ),
    )


@broker.task("vm.list_packages")
async def vm_list_packages(task_id: str) -> None:
    """Снять список установленных пакетов гостя ВМ по SSH.

    Что делает: заходит на hub по управляющей SSH-сессии, определяет IP гостя
    (`guest_ip`/`ip_address` из payload либо `virsh domifaddr`), заходит в гостя
    по управляющему ключу (managed-ВМ) либо по базовой учётке `u`/`1` (legacy),
    определяет package manager (по `os_family` из payload
    либо живым `command -v` в госте), листит пакеты по glob-паттернам
    (`dpkg-query -W` / `rpm -qa` / apk/pacman/portage/xbps) и возвращает плоский
    список `{name, version}`. Дубли по имени схлопываются, итог режется по
    `max_rows`. Результат докладывает server_service (`vms/{id}/packages`).

    Зеркало серверного `installed_packages.list`, но цель — гость ВМ, а не сам
    сервер; парсеры/фильтры/валидатор glob'а переиспользуются из того модуля.

    Параметры: `task_id`. Payload — `vm_id` (обязательно), `vm_name`/`name`,
    `guest_ip`/`ip_address` (адрес гостя, если не по `domifaddr`), опц.
    `os_family`, `patterns` (список glob'ов) либо `pattern` (back-compat, деф.
    `*`), `max_rows`, hub-блок, `target_department_id`.

    Возвращает: `{vm_id, vm_name, package_manager, count, packages: [...]}`.
    Список пакетов в audit не утекает (whitelist `AUDIT_SAFE_FIELDS_LIST_PACKAGES`).

    Возможные ошибки: `VM_INVALID_ARG`, `VM_GUEST_NO_IP`,
    `NO_PACKAGE_MANAGER`, `PACKAGE_QUERY_FAILED`, `INVALID_PATTERN`.
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
        patterns = ip_helpers._resolve_patterns(payload)
        max_rows = payload.get("max_rows")
        os_family = str(payload.get("os_family") or "").strip().lower()

        # Паттерны уходят в shell-команду гостя — валидируем каждый локально
        # (defence-in-depth, тот же allow-list, что в installed_packages.list).
        for pattern in patterns:
            if not isinstance(pattern, str) or not ip_helpers._PATTERN_RE.match(
                pattern,
            ):
                raise SshError(
                    error_code="INVALID_PATTERN", host=host_label,
                    message=(
                        f"pattern {pattern!r} содержит символы, недопустимые "
                        "для glob'а пакета"
                    ),
                )

        session, host = await open_hub_session(payload)
        async with session as ssh:
            guest_ip = await resolve_guest_ip(ssh, host, vm_name, payload)
            # На managed-ВМ базовая учётка снесена — ходим в гостя по управляющему
            # ключу (`load_guest_key`); legacy/unmanaged (ключа в payload нет) —
            # фолбэк на базовую учётку `u`/`1`. Ключ шредим в finally.
            mgmt_user, key_path = await load_guest_key(ssh, host, payload)
            connect = choose_guest_connector(guest_ip, mgmt_user, key_path)
            try:
                package_manager = _OS_FAMILY_TO_PM.get(os_family) or (
                    await _detect_guest_package_manager(
                        ssh, host, guest_ip, connect=connect,
                    )
                )
                cmd = ip_helpers._build_command(package_manager, patterns)
                rc, stdout, stderr = await ssh.run(connect(cmd))
                if rc != 0:
                    # dpkg-query/rpm отдают non-zero, если ничего не подошло под
                    # pattern (stderr пуст) — это валидный пустой результат; rc!=0
                    # со stderr — реальная поломка (битая БД пакетов).
                    if stderr.strip():
                        raise SshError(
                            error_code="PACKAGE_QUERY_FAILED", host=host,
                            cmd_sanitized=cmd, returncode=rc,
                            stderr=stderr.strip(),
                            message=f"{package_manager} query в госте упал",
                        )
                    stdout = ""
            finally:
                if key_path:
                    await _shred_temp_key(ssh, key_path)

        packages = ip_helpers._parse_packages(stdout, package_manager)
        if package_manager in ("apk", "pacman", "portage", "xbps"):
            packages = ip_helpers._filter_by_patterns(packages, patterns)
        packages = ip_helpers._dedup_by_name(packages)
        if isinstance(max_rows, int) and max_rows >= 0:
            packages = packages[:max_rows]

        await server_service_client.record_vm_packages(
            vm_id, packages, target_department_id=target_dept,
            source=package_manager, task_id=task_id,
        )
        return {
            "vm_id": vm_id,
            "vm_name": vm_name,
            "package_manager": package_manager,
            "count": len(packages),
            "packages": packages,
        }

    await run_task(
        task_id,
        audit_action="vm.list_packages",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_LIST_PACKAGES,
    )
