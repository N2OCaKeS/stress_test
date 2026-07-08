"""Задачи VM-менеджера — исполнение по SSH на hub-сервере.

Три базовых handler'а, все под управляющей учёткой hub'а (ключевая сессия,
sudo NOPASSWD — libvirt/kvm без пароля):

* `vms_hub.prepare` — подготовить сервер как VMS-hub: precheck `/dev/kvm`,
  пакеты по семейству ОС, членство в libvirt-группах, cgroup-фикс qemu.conf,
  `libvirtd`, мост `br0` над физическим NIC, firewall (FORWARD/DOCKER-USER
  ACCEPT br0 + `bridge-nf-call-iptables=0`), storage-pool и скачивание образов
  с FTP. Идемпотентна.
* `vm.create` — создать ВМ: клон диска бокса + `virt-install`, провижн гостя,
  снимки. Universal-бокс (`vm_station`) несёт несколько версий ОС на одном
  диске — для каждой строим `<ver>_build` (golden) + `<ver>` (deliverable);
  single-бокс — один снимок `build`.
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

import logging
import re

from src.clients.ssh import SshError
from src.core.constants import (
    VMS_DEFAULT_POOL_PATH,
    VMS_FTP_BOXES_URL,
    VMS_GUEST_LOGIN,
    VMS_NAT_NETWORK,
    VMS_OS_VARIANT,
    VMS_POOL_NAME,
    VMS_UNIVERSAL_BOX,
)
from src.main import broker
from src.services import server_service_client
from src.tasks._runner import run_task
from src.tasks._vms_helpers import (
    POWER_VERBS,
    bridge_label,
    guest_ssh,
    map_domstate,
    open_hub_session,
    parse_domifaddr,
    positive_int,
    resolve_box_url,
    run_hub_cmd,
    validate_ip,
    validate_iface,
    validate_name,
    validate_path,
)

logger = logging.getLogger(__name__)


# Пакеты hub'а по семейству ОС. apt-набор — из референса
# (`Libvirt.prepare`): astra-kvm тянет qemu/libvirt зависимостями. dnf-аналог —
# ручной список для RHEL/RedOS.
_APT_PACKAGES = "astra-kvm virtinst qemu-utils wget tar sshpass bridge-utils"
_DNF_PACKAGES = "qemu-kvm libvirt virt-install qemu-img wget tar sshpass bridge-utils"

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


# hub-команда под sudo с проверкой кода возврата — общий раннер VM-тасок.
_run = run_hub_cmd


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


async def _setup_bridge(ssh, host: str, phy_if: str, os_family: str) -> None:
    """Настроить мост `br0` над физическим NIC (static, по факту сети хоста).

    Идемпотентно: если `br0` уже есть — выходим. Иначе снимаем текущую адресацию
    `phy_if` (адрес/шлюз/DNS) и переносим её на мост. Для apt-семейства пишем
    drop-in ifupdown (`/etc/network/interfaces.d`), для dnf — конфиг через
    NetworkManager (`nmcli`). Активацию делаем best-effort (`ifup`/`nmcli up`):
    полный переезд адреса с живого NIC на мост без разрыва текущей SSH-сессии не
    гарантируется — на части стендов он вступит в силу после reboot.
    """
    rc, _out, _err = await ssh.run("ip link show br0", sudo=True)
    if rc == 0:
        logger.info("vms_hub.prepare: bridge br0 already present on %s", host)
        return

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
    _rc, route_out, _ = await ssh.run("ip route show default", sudo=True)
    gw_m = re.search(r"default\s+via\s+(\d{1,3}(?:\.\d{1,3}){3})", route_out or "")
    gateway = gw_m.group(1) if gw_m else ""
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
        await ssh.run("ifup br0", sudo=True)
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
        await ssh.run("nmcli con up br0", sudo=True)


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
    """Идемпотентно поднять dir storage-pool `vms` на `pool_path`."""
    rc, _out, _err = await ssh.run(f"virsh pool-info {VMS_POOL_NAME}", sudo=True)
    if rc == 0:
        return
    await _run(ssh, f"mkdir -p {pool_path}", host,
               "VMS_HUB_POOL_FAILED", "не удалось создать каталог пула")
    await _run(
        ssh,
        f"virsh pool-define-as {VMS_POOL_NAME} dir --target {pool_path}",
        host, "VMS_HUB_POOL_FAILED", "virsh pool-define-as упал",
    )
    await ssh.run(f"virsh pool-build {VMS_POOL_NAME}", sudo=True)
    await _run(ssh, f"virsh pool-start {VMS_POOL_NAME}", host,
               "VMS_HUB_POOL_FAILED", "virsh pool-start упал")
    await ssh.run(f"virsh pool-autostart {VMS_POOL_NAME}", sudo=True)


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
        rc, _out, _err = await ssh.run(f"test -f {qcow}", sudo=True)
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
        await ssh.run(f"rm -f {tarball}", sudo=True)
        downloaded.append(base)
    return downloaded


@broker.task("vms_hub.prepare")
async def vms_hub_prepare(task_id: str) -> None:
    """Подготовить сервер как VMS-hub (идемпотентно).

    Что делает: заходит по SSH под управляющей учёткой hub'а, проверяет
    `/dev/kvm`, ставит пакеты по семейству ОС (`apt`/`dnf`), доклеивает
    управляющего пользователя в libvirt/kvm-группы, чинит qemu.conf, поднимает
    `libvirtd`, настраивает мост `br0` над физическим NIC, правит firewall
    (FORWARD/DOCKER-USER ACCEPT br0 + `bridge-nf-call-iptables=0`), поднимает
    storage-pool `vms` и тянет образы каталога с FTP. Исход докладывает
    server_service (`vms-hub-state`).

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
                await _run(
                    ssh, _install_packages_cmd(os_family), host,
                    "VMS_HUB_PACKAGES_FAILED", "установка пакетов hub'а упала",
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
                )
                await _setup_bridge(ssh, host, phy_if, os_family)
                await ssh.run(_FIREWALL_FIX, sudo=True)
                await ssh.run(_BRIDGE_NF_FIX, sudo=True)
                await _ensure_pool(ssh, host, pool_path)
                images = await _download_images(ssh, host, pool_path, image_refs)
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
) -> str:
    """Собрать `virt-install --import` под ВМ.

    universal собирается на NAT-сети `test` (транзит для провижна статики), затем
    NIC переводится на bridge. single с `network_mode=bridge` сразу на `br0`,
    `nat` — на сети `test`. `--cpu host-model,+vmx` — проброс nested-виртуализации.
    """
    if network_mode == "bridge":
        net = f"bridge={bridge_label()},model=virtio"
    else:
        net = f"network={VMS_NAT_NETWORK},model=virtio"
    return (
        f"virt-install -n {name} --memory {ram_mb} --vcpus {cpu} --import "
        f"--disk {pool_path}/{name}.qcow2,format=qcow2,bus=virtio "
        f"--os-variant {VMS_OS_VARIANT} --network {net} "
        "--cpu host-model,+vmx --autostart --graphics vnc --noautoconsole"
    )


async def _guest_ip(ssh, host: str, name: str) -> str:
    """Получить IP гостя через `virsh domifaddr` (NAT lease). Raise если нет."""
    _rc, out, _err = await ssh.run(
        f"virsh domifaddr {name} --source lease", sudo=True,
    )
    ip = parse_domifaddr(out)
    if ip is None:
        # fallback на agent-источник (qemu-guest-agent установлен в провижне)
        _rc, out2, _err2 = await ssh.run(
            f"virsh domifaddr {name} --source agent", sudo=True,
        )
        ip = parse_domifaddr(out2)
    if ip is None:
        raise SshError(
            error_code="VM_GUEST_NO_IP",
            host=host,
            message=f"ВМ {name} не получила IP (нет lease/agent-адреса)",
        )
    return ip


async def _provision_guest_base(ssh, host: str, name: str, guest_ip: str) -> None:
    """Базовый провижн гостя: hostname, ntp, установка зависимостей."""
    await _run(
        ssh, guest_ssh(guest_ip, f"hostnamectl set-hostname {name}", sudo=True),
        host, "VM_PROVISION_FAILED", "не удалось задать hostname гостю",
    )
    await ssh.run(
        guest_ssh(guest_ip, "timedatectl set-ntp true", sudo=True), sudo=True,
    )
    await _run(
        ssh,
        guest_ssh(
            guest_ip,
            f"DEBIAN_FRONTEND=noninteractive apt-get install -y {_GUEST_DEPS}",
            sudo=True,
        ),
        host, "VM_PROVISION_FAILED", "не удалось поставить зависимости в гостя",
    )


async def _apply_static_ip(ssh, host: str, guest_ip: str, ip_address: str) -> None:
    """Прописать статический IP гостю (rewrite /etc/network/interfaces).

    Заходим по текущему (NAT) адресу и переписываем сетевой конфиг гостя на
    `ip_address` из пула; NIC гостя после перевода домена на `br0` окажется в
    боевом LAN. gw/dns — дефолты стенда (как в референсном `provision.sh`).
    """
    addr = ip_address.split("/")[0]
    cfg = (
        "auto eth0\\niface eth0 inet static\\n"
        f"    address {addr}\\n    netmask 255.255.255.0\\n"
        "    gateway 10.177.103.254\\n    dns-nameservers 10.177.180.246\\n"
    )
    cmd = guest_ssh(
        guest_ip,
        f"bash -c 'printf \"{cfg}\" > /etc/network/interfaces'",
        sudo=True,
    )
    await _run(ssh, cmd, host, "VM_PROVISION_FAILED",
               "не удалось прописать статический IP гостю")


async def _snapshot(ssh, host: str, name: str, snap: str) -> None:
    await _run(
        ssh, f"virsh snapshot-create-as {name} --name {snap} --atomic", host,
        "VM_SNAPSHOT_FAILED", f"не удалось создать снимок {snap}",
    )


async def _build_universal(
    ssh, host: str, name: str, pool_path: str, ip_address: str,
    os_versions: list[str], password: str | None,
) -> list[str]:
    """Построить universal-ВМ: per version снимки `<ver>_build` + `<ver>`.

    Для каждой версии: переключаем внутренний qemu-img снимок ОС, поднимаем ВМ
    на NAT, провижним (hostname/ntp/deps), снимаем golden `<ver>_build`,
    прописываем статику + переводим NIC на `br0`, переснимаем `<ver>_build`
    (prepared+bridged), опционально меняем пароль `u` и снимаем deliverable
    `<ver>`. В БД (callback) уходят только plain-имена версий.
    """
    plain: list[str] = []
    for raw_ver in os_versions:
        ver = validate_name(str(raw_ver), host, "os_version")
        await ssh.run(f"virsh destroy {name}", sudo=True)  # stop (может быть off)
        await _run(
            ssh, f"qemu-img snapshot -a {ver} {pool_path}/{name}.qcow2", host,
            "VM_CREATE_FAILED", f"не удалось переключить ОС на {ver}",
        )
        await _run(ssh, f"virsh start {name}", host,
                   "VM_CREATE_FAILED", f"не удалось запустить ВМ на версии {ver}")
        guest_ip = await _guest_ip(ssh, host, name)
        await _provision_guest_base(ssh, host, name, guest_ip)
        # golden-снимок текущей версии (креды u:1, чистая система)
        await _snapshot(ssh, host, name, f"{ver}_build")
        # статика + перевод NIC на bridge br0
        await _apply_static_ip(ssh, host, guest_ip, ip_address)
        await _run(
            ssh,
            f"virt-xml {name} --edit --network bridge={bridge_label()},model=virtio",
            host, "VM_CREATE_FAILED", "не удалось перевести NIC ВМ на br0",
        )
        await ssh.run(f"virsh destroy {name}", sudo=True)
        await _run(ssh, f"virsh start {name}", host,
                   "VM_CREATE_FAILED", "ВМ не поднялась на bridge")
        # переснять golden в bridged-состоянии
        await ssh.run(
            f"virsh snapshot-delete {name} --snapshotname {ver}_build", sudo=True,
        )
        await _snapshot(ssh, host, name, f"{ver}_build")
        if password:
            await ssh.run(
                guest_ssh(
                    ip_address.split("/")[0],
                    f"bash -c 'echo {VMS_GUEST_LOGIN}:{password} | chpasswd'",
                    sudo=True,
                ),
                sudo=True,
            )
        # deliverable-снимок версии
        await _snapshot(ssh, host, name, ver)
        plain.append(ver)
    return plain


async def _build_single(
    ssh, host: str, name: str, box: str, pool_path: str, disk_gb: int,
    network_mode: str, ip_address: str | None,
) -> list[str]:
    """Построить single-ВМ из конкретного бокса: провижн + снимок `build`."""
    if disk_gb:
        await _run(
            ssh, f"qemu-img resize {pool_path}/{name}.qcow2 {disk_gb}G", host,
            "VM_CREATE_FAILED", "не удалось увеличить диск ВМ",
        )
    if network_mode == "bridge" and ip_address:
        # bridge: NIC уже на br0 (virt-install), но гость поднялся без адреса —
        # временно смотрим по NAT нельзя, поэтому статику прописываем по факту
        # старта. Здесь ограничиваемся снимком build; детальная статика single
        # bridge-боксов — как в universal-ветке (provision.sh).
        guest_ip = ip_address.split("/")[0]
    else:
        guest_ip = await _guest_ip(ssh, host, name)
    await _provision_guest_base(ssh, host, name, guest_ip)
    # single-боксы под конкретную ОС уже несут репозитории; растим ФС под гостём.
    await ssh.run(
        guest_ssh(guest_ip, "bash -c 'growpart /dev/vda 1 || true; "
                            "resize2fs /dev/vda1 || true'", sudo=True),
        sudo=True,
    )
    await _snapshot(ssh, host, name, "build")
    return ["build"]


@broker.task("vm.create")
async def vm_create(task_id: str) -> None:
    """Создать ВМ на hub'е (порт флоу референса).

    Что делает: клонирует диск бокса в пул, собирает домен `virt-install
    --import`, провижнит гостя по SSH (`u`/`1`) и снимает снимки. Universal-бокс
    (`vm_station`) несёт несколько версий ОС на одном диске — для каждой строит
    `<ver>_build` (golden) + `<ver>` (deliverable); single-бокс — один снимок
    `build`. Исход докладывает server_service (`vms/{id}/state`).

    Параметры: `task_id`. Payload — `vm_id`, `hub_host` (str ip), `name`, `cpu`,
    `ram_mb`, `disk_gb`, `box`, `network_mode` (`bridge`|`nat`), `ip_address`,
    `box_url` (для скачивания single-бокса), `os_versions` (для universal),
    опц. `password`, management-хинты.

    Возвращает: `{vm_id, name, box, network_mode, power_state, status,
    ip_address, snapshots}` — snapshots только plain (без `_build`).

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
        box = validate_name(payload["box"], host_label, "box")
        cpu = positive_int(payload["cpu"], host_label, "cpu")
        ram_mb = positive_int(payload["ram_mb"], host_label, "ram_mb")
        disk_gb = int(payload.get("disk_gb") or 0)
        network_mode = payload.get("network_mode", "bridge")
        if network_mode not in ("bridge", "nat"):
            raise SshError(
                error_code="VM_INVALID_ARG", host=host_label,
                message=f"network_mode {network_mode!r} должен быть bridge или nat",
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

        try:
            session, host = await open_hub_session(payload)
            async with session as ssh:
                # single-бокс может ещё не лежать в пуле — тянем по box_url.
                rc, _out, _err = await ssh.run(
                    f"test -f {pool_path}/{box}.qcow2", sudo=True,
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
                    await ssh.run(f"rm -f {pool_path}/{box}.tar.gz", sudo=True)
                # клон диска бокса под ВМ (COW-исходник — готовый qcow2 бокса).
                await _run(
                    ssh, f"cp {pool_path}/{box}.qcow2 {pool_path}/{name}.qcow2", host,
                    "VM_CREATE_FAILED", "не удалось клонировать диск бокса",
                )
                await _run(
                    ssh,
                    _virt_install_cmd(name, cpu, ram_mb, pool_path, network_mode),
                    host, "VM_CREATE_FAILED", "virt-install упал",
                )
                if is_universal:
                    if not os_versions:
                        raise SshError(
                            error_code="VM_INVALID_ARG", host=host,
                            message="universal-ВМ требует непустой os_versions",
                        )
                    snapshots = await _build_universal(
                        ssh, host, name, pool_path, ip_address or "",
                        os_versions, password,
                    )
                else:
                    snapshots = await _build_single(
                        ssh, host, name, box, pool_path, disk_gb,
                        network_mode, ip_address,
                    )
                _rc, dom_out, _err = await ssh.run(
                    f"virsh domstate {name}", sudo=True,
                )
                power_state = map_domstate(dom_out)
        except Exception as exc:
            error_text = getattr(exc, "error_code", type(exc).__name__)
            try:
                await server_service_client.submit_vm_state(
                    vm_id, target_department_id=target_dept,
                    status="error", error=str(error_text),
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "vm.create failed-callback errored vm_id=%s", vm_id,
                    exc_info=True,
                )
            raise

        await server_service_client.submit_vm_state(
            vm_id, target_department_id=target_dept,
            power_state=power_state, ip_address=ip_address, status="free",
            snapshots=snapshots,
        )
        return {
            "vm_id": vm_id,
            "name": name,
            "box": box,
            "network_mode": network_mode,
            "power_state": power_state,
            "status": "free",
            "ip_address": ip_address,
            "snapshots": snapshots,
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

        session, host = await open_hub_session(payload)
        async with session as ssh:
            verb = POWER_VERBS[action]
            # start идемпотентен на запущенной ВМ (virsh отдаст non-zero
            # "already active") — допускаем; destroy на выключенной тоже.
            ok = (0, 1) if action in ("start", "destroy", "shutdown") else (0,)
            await _run(
                ssh, f"virsh {verb} {vm_name}", host,
                "VM_POWER_FAILED", f"virsh {verb} упал", ok=ok,
            )
            _rc, dom_out, _err = await ssh.run(
                f"virsh domstate {vm_name}", sudo=True,
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
            await ssh.run(f"virsh destroy {vm_name}", sudo=True)
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
