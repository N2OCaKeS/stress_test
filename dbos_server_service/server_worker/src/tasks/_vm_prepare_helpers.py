"""Общие шаги bootstrap управления гостем ВМ — нейтральный модуль без циклов.

Здесь живут прикладные шаги, которые заводят на госте управляющего
пользователя (dbos-mgmt): ключ + пароль + sudo NOPASSWD, qemu-guest-agent,
проверка входа по ключу (анти-локаут), хардинг sshd и снос базовой учётки `u`
образа. Плюс чтение управляющего материала из dispatch-stash'а и запись/затирка
временного приватного ключа на hub'е.

Модуль намеренно не импортит ни `vms.py`, ни `vms_network.py` — только базовые
хелперы (`_vms_helpers`), чтобы обе стороны (сборка ВМ в `vms.py` и отдельная
задача `vm.prepare` в `vms_network.py`) тянули один и тот же код без циклического
импорта. Порядок шагов безопасный: парольный доступ и базовую учётку `u` трогаем
только после подтверждённого входа по ключу.
"""

from __future__ import annotations

import json
import logging
import re

from src.clients.ssh import SshError
from src.core.constants import VMS_GUEST_LOGIN
from src.services import redis_pool
from src.services.redis_stash_crypto import (
    aad_for_redis_stash,
    decrypt_stash,
    stash_id_from_key,
)
from src.tasks._vms_helpers import (
    guest_connector,
    guest_key_connector,
    guest_ssh,
    guest_ssh_key,
    run_hub_cmd,
    validate_name,
)

logger = logging.getLogger(__name__)


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


# ── чтение управляющего материала из stash ───────────────────────────────────


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
    """Снять dispatch-stash из Redis после успешного применения (best-effort)."""
    try:
        _validate_dispatch_creds_key(stash_key)
    except SshError:
        return
    client = redis_pool.get_redis()
    try:
        await client.delete(stash_key)
    except Exception:  # noqa: BLE001
        logger.debug("failed to delete vm dispatch stash", exc_info=True)


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


async def load_mgmt_material(stash_key: str, host: str) -> dict:
    """Прочитать stash и вернуть валидированный управляющий материал ВМ.

    Возвращает `{management_user, public_key, private_key, password}` — все поля
    проверены (имя пользователя, формат ключа, безопасность пароля, наличие
    приватного ключа). Нет ключа/битый payload → `SshError(DISPATCH_STASH_MISSING)`.
    """
    mgmt = await _read_mgmt_install(stash_key)
    management_user = validate_name(
        mgmt.get("management_user") or "", host, "management_user",
    )
    public_key = _validate_public_key(mgmt.get("public_key") or "", host)
    password = _validate_secret(mgmt.get("password") or "", host, "mgmt password")
    private_key = mgmt.get("private_key")
    if not private_key:
        raise SshError(
            error_code="DISPATCH_STASH_MISSING", host=host,
            message="vm management stash has no private_key; re-run vm.prepare",
        )
    return {
        "management_user": management_user,
        "public_key": public_key,
        "private_key": private_key,
        "password": password,
    }


# ── выбор коннектора для пост-создательных guest-задач ───────────────────────


async def load_guest_key(ssh, host: str, payload: dict) -> tuple[str | None, str | None]:
    """Достать управляющий ключ ВМ из stash'а и написать его во временный файл.

    Managed-ВМ (в payload есть `creds_stash_key`): читаем управляющий материал,
    пишем приватный ключ на hub во временный файл и возвращаем
    `(management_user, key_path)`. Пост-создательные guest-задачи (учётки,
    снимки, диски) ходят в гостя ключом этого пользователя — базовая учётка `u`
    на managed-ВМ снесена. Caller обязан затереть ключ через `_shred_temp_key`
    в `finally`.

    Не-managed / legacy-ВМ (нет `creds_stash_key`) → `(None, None)`: guest-задачи
    остаются на парольном входе `u`/`1`.
    """
    stash_key = payload.get("creds_stash_key")
    if not stash_key:
        return None, None
    mgmt = await load_mgmt_material(stash_key, host)
    key_path = await _write_temp_key(ssh, host, mgmt["private_key"])
    return mgmt["management_user"], key_path


def choose_guest_connector(guest_ip: str, mgmt_user: str | None, key_path: str | None):
    """Коннектор входа в гостя: ключевой (managed) либо парольный `u`/`1`.

    Есть временный ключ (`key_path`) → вход управляющим пользователем по ключу
    (`guest_key_connector`); иначе — дефолтные креды образа `u`/`1`
    (`guest_connector`).
    """
    if key_path:
        return guest_key_connector(guest_ip, mgmt_user, key_path)
    return guest_connector(guest_ip)


# ── шаги bootstrap на госте ──────────────────────────────────────────────────


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


async def apply_managed_baseline(
    ssh, host: str, guest_ip: str, mgmt: dict, key_path: str, *, harden: bool = True,
) -> None:
    """Прогнать полный bootstrap управления на госте (заходя по `u`/`1`).

    `mgmt` — уже валидированный `load_mgmt_material` набор. Порядок безопасный:
    заводим управляющего пользователя, кладём ключ + пароль + sudo NOPASSWD,
    ставим qemu-guest-agent, проверяем вход по ключу (анти-локаут) и только после
    этого хардим sshd (при `harden`) и сносим базовую учётку `u`. `key_path` —
    временный приватный ключ на hub'е (пишет и подчищает caller).
    """
    management_user = mgmt["management_user"]
    await _ensure_mgmt_user(ssh, host, guest_ip, management_user)
    await _install_sudoers(ssh, host, guest_ip, management_user)
    await _set_mgmt_password(ssh, host, guest_ip, management_user, mgmt["password"])
    await _install_authorized_key(
        ssh, host, guest_ip, management_user, mgmt["public_key"],
    )
    await _install_guest_agent(ssh, host, guest_ip)
    await _verify_key_login(ssh, host, guest_ip, management_user, key_path)
    if harden:
        await _harden_guest_sshd(ssh, host, guest_ip, management_user, key_path)
    await _delete_base_user(ssh, host, guest_ip, management_user, key_path)
