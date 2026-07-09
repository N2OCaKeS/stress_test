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
import json
import logging
import re

from src.clients.ssh import SshError
from src.core.constants import (
    VMS_GUEST_LOGIN,
    VMS_NAT_BRIDGE,
    VMS_NAT_HOST_IP,
)
from src.main import broker
from src.services import redis_pool, server_service_client
from src.services.redis_stash_crypto import (
    aad_for_redis_stash,
    decrypt_stash,
    stash_id_from_key,
)
from src.tasks._runner import run_task
from src.tasks._vms_helpers import (
    LIBVIRT_SESSION_ENV,
    VMS_DEFAULT_GATEWAY,
    VMS_DEFAULT_NETMASK,
    bridge_label,
    guest_ssh,
    guest_ssh_key,
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

# Формат ключа dispatch-stash'а — общий с provision/rotate (`worker_client
# .dispatch_creds_key`). Жёсткий guard: скомпрометированный payload не должен
# увести читателя Redis в чужой keyspace.
_DISPATCH_CREDS_KEY_RE = re.compile(r"^dbos:dispatch_creds:[A-Za-z0-9_\-]{1,128}$")

# Публичный SSH-ключ управляющего пользователя: тип + base64 + опц. комментарий.
# Ключ подставляется в inline-команду записи authorized_keys на госте, поэтому
# отбиваем всё, что могло бы расклеить shell (пробелы разрешены только между
# полями, метасимволы — нет).
_PUBKEY_RE = re.compile(
    r"^(?:ssh-(?:rsa|ed25519|dss)|ecdsa-sha2-[A-Za-z0-9-]+)"
    r"\s+[A-Za-z0-9+/]+=*"
    r"(?:\s+[A-Za-z0-9._@-]+)?$"
)

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


# ── vm.prepare: чтение управляющего материала из stash ───────────────────────


def _validate_dispatch_creds_key(stash_key: str) -> None:
    if not isinstance(stash_key, str) or not _DISPATCH_CREDS_KEY_RE.fullmatch(stash_key):
        raise SshError(
            error_code="DISPATCH_STASH_MISSING",
            host="",
            message="creds_stash_key has unexpected format",
        )


async def _read_mgmt_install(stash_key: str) -> dict:
    """Прочитать per-VM управляющий материал из dispatch-stash'а.

    Возвращает dict `{management_user, public_key, private_key, password}` —
    сгенерённую server_service'ом пару и пароль для этой ВМ. Нет ключа в Redis
    (TTL истёк) → `SshError(DISPATCH_STASH_MISSING)`. Битый/swap'нутый token →
    `AppException(STASH_DECRYPT_*)` до `_runner` (task FAILED с явным кодом).
    """
    _validate_dispatch_creds_key(stash_key)
    client = redis_pool.get_redis()
    raw = await client.get(stash_key)
    if raw is None:
        raise SshError(
            error_code="DISPATCH_STASH_MISSING",
            host="",
            message=(
                "vm management credentials are missing or expired in Redis; "
                "re-run vm.prepare"
            ),
        )
    text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
    plaintext = decrypt_stash(
        text, aad=aad_for_redis_stash(stash_id_from_key(stash_key)),
    )
    try:
        data = json.loads(plaintext)
    except (ValueError, TypeError) as exc:
        raise SshError(
            error_code="DISPATCH_STASH_MISSING",
            host="",
            message="vm management credentials payload is malformed; re-run vm.prepare",
        ) from exc
    if not isinstance(data, dict):
        raise SshError(
            error_code="DISPATCH_STASH_MISSING",
            host="",
            message="vm management credentials payload is malformed; re-run vm.prepare",
        )
    return data



async def _delete_stash(stash_key: str) -> None:
    """Снять dispatch-stash из Redis после успешного prepare (best-effort)."""
    try:
        _validate_dispatch_creds_key(stash_key)
    except SshError:
        return
    client = redis_pool.get_redis()
    try:
        await client.delete(stash_key)
    except Exception:  # noqa: BLE001
        logger.debug("failed to delete vm.prepare dispatch stash", exc_info=True)


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


def _validate_public_key(value: str, host: str) -> str:
    if not isinstance(value, str) or not _PUBKEY_RE.fullmatch(value.strip()):
        raise SshError(
            error_code="VM_INVALID_ARG", host=host,
            message="публичный ключ управляющего пользователя имеет неверный формат",
        )
    return value.strip()


def _validate_secret(value: str, host: str, field: str) -> str:
    """Отбить пароль/секрет с символами, ломающими inline-команду в госте."""
    if not isinstance(value, str) or not value:
        raise SshError(
            error_code="VM_INVALID_ARG", host=host,
            message=f"{field} должен быть непустой строкой",
        )
    for bad in ("\n", "\r", "\0", "'", '"', "`", "$", ";"):
        if bad in value:
            raise SshError(
                error_code="VM_INVALID_ARG", host=host,
                message=f"{field} содержит недопустимый символ",
            )
    return value


# ── vm.prepare: шаги bootstrap на госте ──────────────────────────────────────


async def _ensure_mgmt_user(ssh, host: str, guest_ip: str, mgmt_user: str) -> None:
    """Завести управляющего пользователя на госте (idempotent) + sudo-группа."""
    cmd = (
        f"bash -c 'id {mgmt_user} >/dev/null 2>&1 || "
        f"useradd -m -s /bin/bash {mgmt_user}; "
        f"usermod -aG sudo {mgmt_user}'"
    )
    await run_hub_cmd(
        ssh, guest_ssh(guest_ip, cmd, sudo=True), host,
        "VM_PREPARE_FAILED", "не удалось завести управляющего пользователя на госте",
    )


async def _install_sudoers(ssh, host: str, guest_ip: str, mgmt_user: str) -> None:
    """Положить NOPASSWD-правило управляющему пользователю (+ `visudo -cf`)."""
    path = f"/etc/sudoers.d/{mgmt_user}-management"
    line = f"{mgmt_user} ALL=(ALL) NOPASSWD: ALL"
    cmd = (
        f"bash -c 'printf \"%s\\n\" \"{line}\" > {path} && "
        f"chmod 440 {path} && visudo -cf {path}'"
    )
    await run_hub_cmd(
        ssh, guest_ssh(guest_ip, cmd, sudo=True), host,
        "VM_PREPARE_FAILED", "не удалось записать sudoers управляющего пользователя",
    )


async def _set_mgmt_password(
    ssh, host: str, guest_ip: str, mgmt_user: str, password: str,
) -> None:
    """Поставить пароль управляющему пользователю (`chpasswd`)."""
    cmd = f"bash -c 'echo {mgmt_user}:{password} | chpasswd'"
    await run_hub_cmd(
        ssh, guest_ssh(guest_ip, cmd, sudo=True), host,
        "VM_PREPARE_FAILED", "не удалось поставить пароль управляющему пользователю",
    )


async def _install_guest_agent(ssh, host: str, guest_ip: str) -> None:
    """Доустановить qemu-guest-agent в гостя (best-effort).

    Нужен для graceful `virsh shutdown` (ACPI/agent-канал) и для чтения адреса
    через `domifaddr --source agent`. Ставим по семейству пакетника гостя и
    поднимаем сервис. Провал не валит bootstrap — агент не критичен для входа
    по ключу.
    """
    cmd = (
        "bash -c 'if command -v apt-get >/dev/null 2>&1; then "
        "DEBIAN_FRONTEND=noninteractive apt-get install -y qemu-guest-agent; "
        "elif command -v dnf >/dev/null 2>&1; then "
        "dnf install -y qemu-guest-agent; fi; "
        "systemctl enable --now qemu-guest-agent 2>/dev/null || true'"
    )
    await ssh.run(guest_ssh(guest_ip, cmd, sudo=True), sudo=True)


async def _install_authorized_key(
    ssh, host: str, guest_ip: str, mgmt_user: str, public_key: str,
) -> None:
    """Положить публичный ключ в `authorized_keys` управляющего пользователя.

    Идемпотентно (`grep -qxF`): повторный prepare не плодит дубли. Права
    `700`/`600` и владелец выставляются явно.
    """
    home = f"/home/{mgmt_user}"
    cmd = (
        f"bash -c 'mkdir -p {home}/.ssh && chmod 700 {home}/.ssh && "
        f'grep -qxF "{public_key}" {home}/.ssh/authorized_keys 2>/dev/null || '
        f'echo "{public_key}" >> {home}/.ssh/authorized_keys; '
        f"chmod 600 {home}/.ssh/authorized_keys && "
        f"chown -R {mgmt_user}:{mgmt_user} {home}/.ssh'"
    )
    await run_hub_cmd(
        ssh, guest_ssh(guest_ip, cmd, sudo=True), host,
        "VM_PREPARE_FAILED", "не удалось положить публичный ключ управляющему пользователю",
    )


async def _write_temp_key(ssh, host: str, private_key: str) -> str:
    """Записать приватный ключ во временный файл на hub'е; вернуть путь.

    Ключ нужен, чтобы проверить вход по нему на госте и добить пост-хардинг шаги
    (удаление `u`), когда парольный вход уже выключен. Файл кладём с правами
    `600`; caller обязан подчистить его в `finally`.
    """
    rc, out, err = await ssh.run("mktemp", sudo=True)
    if rc != 0 or not (out or "").strip():
        raise SshError(
            error_code="VM_PREPARE_FAILED", host=host, returncode=rc,
            stderr=(err or "").strip(),
            message="не удалось создать временный файл для управляющего ключа",
        )
    path = out.strip()
    material = private_key if private_key.endswith("\n") else private_key + "\n"
    rc, _out, err = await ssh.run(
        f"tee {path} > /dev/null", sudo=True, stdin_payload=material,
    )
    if rc != 0:
        raise SshError(
            error_code="VM_PREPARE_FAILED", host=host, returncode=rc,
            stderr=(err or "").strip(),
            message="не удалось записать управляющий ключ во временный файл",
        )
    await ssh.run(f"chmod 600 {path}", sudo=True)
    return path


async def _shred_temp_key(ssh, path: str) -> None:
    """Затереть временный файл управляющего ключа на hub'е (best-effort)."""
    try:
        await ssh.run(f"sh -c 'shred -u {path} 2>/dev/null || rm -f {path}'", sudo=True)
    except Exception:  # noqa: BLE001
        logger.debug("failed to shred temp management key", exc_info=True)


async def _verify_key_login(
    ssh, host: str, guest_ip: str, mgmt_user: str, key_path: str,
) -> None:
    """Анти-локаут: убедиться, что вход по ключу и NOPASSWD-sudo работают.

    Заходим на гостя ключом под управляющим пользователем и выполняем
    `sudo -n true`. Провал (ключ не пускает, sudo просит пароль) → отказ
    хардить/удалять `u`, парольный доступ остаётся.
    """
    rc, _out, _err = await ssh.run(
        guest_ssh_key(guest_ip, mgmt_user, key_path, "sudo -n true"), sudo=True,
    )
    if rc != 0:
        raise SshError(
            error_code="VM_PREPARE_KEY_VERIFY_FAILED", host=host,
            returncode=rc,
            message=(
                "вход по управляющему ключу на госте не подтверждён — не удаляем "
                "базовую учётку и не выключаем пароль (анти-локаут)"
            ),
        )


async def _harden_guest_sshd(
    ssh, host: str, guest_ip: str, mgmt_user: str, key_path: str,
) -> None:
    """Захардить sshd гостя через drop-in (после подтверждённого входа по ключу).

    Выключаем парольную аутентификацию и root-login, оставляя pubkey. Правим
    только отдельный snippet, `sshd -t` до reload'а. Идём под ключевой сессией —
    к этому моменту ключ уже проверен.
    """
    path = f"/etc/ssh/sshd_config.d/{mgmt_user}-dbos.conf"
    snippet = (
        "PubkeyAuthentication yes\\n"
        "PasswordAuthentication no\\n"
        "PermitRootLogin no\\n"
    )
    remote = (
        f'bash -c \'printf "{snippet}" > {path} && sshd -t && '
        "(systemctl reload sshd 2>/dev/null || systemctl reload ssh 2>/dev/null || "
        "service ssh reload 2>/dev/null || true)'"
    )
    await run_hub_cmd(
        ssh, guest_ssh_key(guest_ip, mgmt_user, key_path, remote, sudo=True), host,
        "VM_PREPARE_FAILED", "не удалось захардить sshd гостя",
    )


async def _delete_base_user(
    ssh, host: str, guest_ip: str, mgmt_user: str, key_path: str,
) -> None:
    """Удалить базовую учётку `u` образа (после подтверждённого входа по ключу).

    Идём под ключевой сессией управляющего пользователя (парольный вход к этому
    моменту уже выключен). `userdel -rf` сносит и домашний каталог; код 6 (юзера
    нет) допускаем — retry идемпотентен.
    """
    await run_hub_cmd(
        ssh,
        guest_ssh_key(
            guest_ip, mgmt_user, key_path,
            f"userdel -rf {VMS_GUEST_LOGIN}", sudo=True,
        ),
        host, "VM_PREPARE_FAILED", "не удалось удалить базовую учётку образа",
        ok=(0, 6),
    )


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

        mgmt = await _read_mgmt_install(stash_key)
        management_user = validate_name(
            mgmt.get("management_user") or "", host_label, "management_user",
        )
        public_key = _validate_public_key(mgmt.get("public_key") or "", host_label)
        private_key = mgmt.get("private_key")
        password = _validate_secret(
            mgmt.get("password") or "", host_label, "mgmt password",
        )
        if not private_key:
            raise SshError(
                error_code="DISPATCH_STASH_MISSING", host=host_label,
                message="vm management stash has no private_key; re-run vm.prepare",
            )

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
        f"{LIBVIRT_SESSION_ENV} virsh domblklist {vm_name}",
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
    await ssh.run(f"{LIBVIRT_SESSION_ENV} virsh destroy {vm_name}")
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
    await ssh.run(f"{LIBVIRT_SESSION_ENV} virsh destroy {vm_name}")
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
                    f"{LIBVIRT_SESSION_ENV} virsh domstate {vm_name}",
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
