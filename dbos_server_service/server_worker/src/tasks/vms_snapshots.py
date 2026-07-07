"""Задачи VM-менеджера: снимки, обновление ОС и перекатка кред — по SSH на hub'е.

Надстройка над базовыми VM-тасками (`tasks/vms.py`, `tasks/vms_disks.py`): та же
управляющая hub-сессия и sudo NOPASSWD (libvirt/kvm без пароля), гость по
`sshpass` (`u`/`1`). Хендлеры:

* `vm.snapshot_create` — `virsh snapshot-create-as` (`--disk-only`/`--live` по
  `kind`); callback снимок→`ready`.
* `vm.snapshot_delete` — `virsh snapshot-delete`.
* `vm.snapshot_revert` — `virsh snapshot-revert`; в режиме `per_snapshot`
  server_service сам переключит активные креды ВМ по callback'у.
* `vm.astra_update` — revert `<major>_build` → перезапись sources.list в госте →
  `astra-update -A -T -r` → reboot(wait) → `passwd u` → снимок `<rc>`.
* `vm.allta_update` / `vm.passwd` — «reroll»: по каждому не-`_build` снимку
  revert → обновить guest-allta `.deb` с FTP → опц. `chpasswd u` → пересоздать
  снимок. Один и тот же op; `passwd` требует пароль обязательно.

Длинные операции идут как `astra_update`: без per-команда timeout'а,
durable-retry на уровне `_runner`. Исход докладывается server_service через
internal-callback'и (`vms/{id}/snapshots`, `vms/{id}/state`).
"""

from __future__ import annotations

import asyncio
import logging

from src.clients.ssh import SshError
from src.core.constants import VMS_FTP_ALLTA_DEB_URL, VMS_GUEST_LOGIN
from src.main import broker
from src.services import server_service_client
from src.tasks._runner import run_task
from src.tasks._vms_helpers import (
    guest_ssh,
    map_domstate,
    open_hub_session,
    resolve_guest_ip,
    run_hub_cmd,
    validate_name,
)

logger = logging.getLogger(__name__)


AUDIT_SAFE_FIELDS_SNAPSHOT: set[str] = {
    "vm_id", "snapshot_id", "name", "kind", "state", "is_current", "power_state",
}
AUDIT_SAFE_FIELDS_ASTRA: set[str] = {
    "vm_id", "vm_name", "rc", "snapshot_id", "power_state", "password_changed",
}
AUDIT_SAFE_FIELDS_REROLL: set[str] = {
    "vm_id", "vm_name", "snapshots", "password_updated_vms", "password_changed",
}

# Семейство системных golden-снимков `<ver>_build`: их пароль не меняется и они
# не участвуют в reroll (защищённые). Совпадает с суффиксом из `vms.py`.
_BUILD_SUFFIX = "_build"

# Тайминги ожидания гостя после reboot в `astra_update`. Вынесены в модульные
# константы, чтобы тесты обнуляли задержки (реальная перезагрузка Astra — минуты).
_GUEST_REBOOT_SETTLE_S = 15.0
_GUEST_REBOOT_POLL_DELAY_S = 10.0
_GUEST_REBOOT_MAX_POLLS = 60


def _host_label(payload: dict) -> str:
    """Адрес hub'а для сообщений об ошибке (до открытия сессии)."""
    return str(
        payload.get("host") or payload.get("hub_host")
        or payload.get("hub_server_id") or "hub",
    )


# ── общие билдеры snapshot-команд ────────────────────────────────────────────


def _snapshot_create_cmd(vm_name: str, snap: str, kind: str | None) -> str:
    """Собрать `virsh snapshot-create-as` с флагом по типу снимка.

    `disk_only` → внешний disk-only снимок (`--disk-only --atomic`), `full`
    (и дефолт) → полный снимок работающего домена (`--live`).
    """
    cmd = f"virsh snapshot-create-as --domain {vm_name} --name {snap}"
    if kind == "disk_only":
        cmd += " --disk-only --atomic"
    elif kind == "full":
        cmd += " --live"
    return cmd


def _validate_guest_password(value: str, host: str) -> str:
    """Отбить пароль гостя с символами, ломающими inline-`chpasswd` в госте.

    Пароль уходит в `bash -c 'echo u:<pwd> | chpasswd'` внутри вложенной
    guest-сессии; перевод строки/кавычки/подстановка расклеили бы команду или
    протащили постороннюю директиву. Пустой пароль тоже не пускаем.
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


async def _change_guest_password(ssh, host: str, guest_ip: str, password: str) -> None:
    """Сменить пароль аккаунта `u` в госте (`echo u:<pwd> | chpasswd`)."""
    await run_hub_cmd(
        ssh,
        guest_ssh(
            guest_ip,
            f"bash -c 'echo {VMS_GUEST_LOGIN}:{password} | chpasswd'",
            sudo=True,
        ),
        host, "VM_PASSWD_FAILED", "не удалось сменить пароль гостя",
    )


async def _report_snapshot_error(
    vm_id: str, snapshot_id: str | None, name: str | None,
    target_dept: str | None, action: str, exc: Exception,
) -> None:
    """Best-effort callback `state='error'` на провал snapshot-таски."""
    error_text = getattr(exc, "error_code", type(exc).__name__)
    entry: dict = {"state": "error", "error": str(error_text)}
    if snapshot_id is not None:
        entry["snapshot_id"] = snapshot_id
    if name is not None:
        entry["name"] = name
    try:
        await server_service_client.submit_vm_snapshots(
            vm_id, [entry], target_department_id=target_dept,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "%s failed-callback errored vm_id=%s snapshot_id=%s",
            action, vm_id, snapshot_id, exc_info=True,
        )


# ── vm.snapshot_create ───────────────────────────────────────────────────────


@broker.task("vm.snapshot_create")
async def vm_snapshot_create(task_id: str) -> None:
    """Создать снимок ВМ на hub'е (`virsh snapshot-create-as`).

    Что делает: заходит на hub по SSH, снимает снимок домена с флагом по типу
    (`disk_only` → `--disk-only`, `full` → `--live`). Исход докладывает
    server_service батчем `vms/{id}/snapshots` (`state='ready'`,
    `is_current=True`).

    Параметры: `task_id`. Payload — `vm_id`, `vm_name`/`name`, `snapshot_id`,
    `snapshot_name`/`name`, опц. `kind` (`disk_only`|`full`), hub-блок.

    Возвращает: `{vm_id, snapshot_id, name, kind, state, is_current}`.

    Возможные ошибки: `VM_INVALID_ARG`, `VM_SNAPSHOT_FAILED`. На ошибке —
    best-effort `state='error'`.
    """
    async def _impl(payload: dict) -> dict:
        vm_id = payload["vm_id"]
        snapshot_id = payload.get("snapshot_id")
        target_dept = payload.get("target_department_id")
        host_label = _host_label(payload)
        vm_name = validate_name(
            payload.get("vm_name") or payload["name"], host_label, "vm_name",
        )
        snap = validate_name(
            payload.get("snapshot_name") or payload["name"], host_label,
            "snapshot_name",
        )
        kind = payload.get("kind")

        try:
            session, host = await open_hub_session(payload)
            async with session as ssh:
                await run_hub_cmd(
                    ssh, _snapshot_create_cmd(vm_name, snap, kind), host,
                    "VM_SNAPSHOT_FAILED", f"не удалось создать снимок {snap}",
                )
        except Exception as exc:
            await _report_snapshot_error(
                vm_id, snapshot_id, snap, target_dept, "vm.snapshot_create", exc,
            )
            raise

        entry: dict = {"name": snap, "state": "ready", "is_current": True}
        if snapshot_id is not None:
            entry["snapshot_id"] = snapshot_id
        if kind is not None:
            entry["kind"] = kind
        await server_service_client.submit_vm_snapshots(
            vm_id, [entry], target_department_id=target_dept,
        )
        return {
            "vm_id": vm_id,
            "snapshot_id": snapshot_id,
            "name": snap,
            "kind": kind,
            "state": "ready",
            "is_current": True,
        }

    await run_task(
        task_id,
        audit_action="vm.snapshot_create",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_SNAPSHOT,
    )


# ── vm.snapshot_delete ───────────────────────────────────────────────────────


@broker.task("vm.snapshot_delete")
async def vm_snapshot_delete(task_id: str) -> None:
    """Удалить снимок ВМ на hub'е (`virsh snapshot-delete`).

    Что делает: заходит на hub по SSH и сносит снимок домена. Исход докладывает
    server_service батчем `vms/{id}/snapshots` (`state='deleted'`).

    Параметры: `task_id`. Payload — `vm_id`, `vm_name`/`name`, `snapshot_id`,
    `snapshot_name`/`name`, hub-блок.

    Возвращает: `{vm_id, snapshot_id, name, state}`.

    Возможные ошибки: `VM_INVALID_ARG`, `VM_SNAPSHOT_FAILED`. На ошибке —
    best-effort `state='error'`.
    """
    async def _impl(payload: dict) -> dict:
        vm_id = payload["vm_id"]
        snapshot_id = payload.get("snapshot_id")
        target_dept = payload.get("target_department_id")
        host_label = _host_label(payload)
        vm_name = validate_name(
            payload.get("vm_name") or payload["name"], host_label, "vm_name",
        )
        snap = validate_name(
            payload.get("snapshot_name") or payload["name"], host_label,
            "snapshot_name",
        )

        try:
            session, host = await open_hub_session(payload)
            async with session as ssh:
                await run_hub_cmd(
                    ssh,
                    f"virsh snapshot-delete --domain {vm_name} --snapshotname {snap}",
                    host, "VM_SNAPSHOT_FAILED", f"не удалось удалить снимок {snap}",
                )
        except Exception as exc:
            await _report_snapshot_error(
                vm_id, snapshot_id, snap, target_dept, "vm.snapshot_delete", exc,
            )
            raise

        entry: dict = {"name": snap, "state": "deleted"}
        if snapshot_id is not None:
            entry["snapshot_id"] = snapshot_id
        await server_service_client.submit_vm_snapshots(
            vm_id, [entry], target_department_id=target_dept,
        )
        return {
            "vm_id": vm_id,
            "snapshot_id": snapshot_id,
            "name": snap,
            "state": "deleted",
        }

    await run_task(
        task_id,
        audit_action="vm.snapshot_delete",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_SNAPSHOT,
    )


# ── vm.snapshot_revert ───────────────────────────────────────────────────────


@broker.task("vm.snapshot_revert")
async def vm_snapshot_revert(task_id: str) -> None:
    """Откатить ВМ на снимок (`virsh snapshot-revert`).

    Что делает: заходит на hub по SSH, ревертит домен на снимок и читает
    `virsh domstate`. Для режима `per_snapshot` креды ВМ переключает
    server_service по callback'у (`is_current`-снимок) — воркер лишь возвращает
    реверт-цель. Исход докладывает server_service батчем `vms/{id}/snapshots`
    (`is_current=True`) и `vms/{id}/state` (`power_state`).

    Параметры: `task_id`. Payload — `vm_id`, `vm_name`/`name`, `snapshot_id`,
    `snapshot_name`/`name`, hub-блок.

    Возвращает: `{vm_id, snapshot_id, name, power_state, is_current}`.

    Возможные ошибки: `VM_INVALID_ARG`, `VM_SNAPSHOT_FAILED`. На ошибке —
    best-effort `state='error'`.
    """
    async def _impl(payload: dict) -> dict:
        vm_id = payload["vm_id"]
        snapshot_id = payload.get("snapshot_id")
        target_dept = payload.get("target_department_id")
        host_label = _host_label(payload)
        vm_name = validate_name(
            payload.get("vm_name") or payload["name"], host_label, "vm_name",
        )
        snap = validate_name(
            payload.get("snapshot_name") or payload["name"], host_label,
            "snapshot_name",
        )

        try:
            session, host = await open_hub_session(payload)
            async with session as ssh:
                await run_hub_cmd(
                    ssh,
                    f"virsh snapshot-revert --domain {vm_name} --snapshotname {snap}",
                    host, "VM_SNAPSHOT_FAILED", f"не удалось откатить на снимок {snap}",
                )
                _rc, dom_out, _err = await ssh.run(
                    f"virsh domstate {vm_name}", sudo=True,
                )
                power_state = map_domstate(dom_out)
        except Exception as exc:
            await _report_snapshot_error(
                vm_id, snapshot_id, snap, target_dept, "vm.snapshot_revert", exc,
            )
            raise

        entry: dict = {"name": snap, "state": "ready", "is_current": True}
        if snapshot_id is not None:
            entry["snapshot_id"] = snapshot_id
        await server_service_client.submit_vm_snapshots(
            vm_id, [entry], target_department_id=target_dept,
        )
        await server_service_client.submit_vm_state(
            vm_id, target_department_id=target_dept, power_state=power_state,
        )
        return {
            "vm_id": vm_id,
            "snapshot_id": snapshot_id,
            "name": snap,
            "power_state": power_state,
            "is_current": True,
        }

    await run_task(
        task_id,
        audit_action="vm.snapshot_revert",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_SNAPSHOT,
    )


# ── astra_update / reroll: guest-хелперы ─────────────────────────────────────


def _sanitize_repositories(repositories, host: str) -> str:
    """Свести список репозиториев к тексту sources.list (по строке на репо).

    Отбиваем управляющие символы и кавычки внутри записи: содержимое уходит в
    госте через inline-`printf`, встроенный перевод строки/кавычка расклеили бы
    команду и протащили постороннюю директиву. Пустой список — ошибка.
    """
    if not isinstance(repositories, list) or not repositories:
        raise SshError(
            error_code="VM_ASTRA_UPDATE_NO_REPOSITORIES", host=host,
            message="astra_update payload has empty repository_urls",
        )
    lines: list[str] = []
    for repo in repositories:
        if not isinstance(repo, str) or not repo.strip():
            raise SshError(
                error_code="VM_ASTRA_UPDATE_INVALID_REPOSITORY", host=host,
                message="repository entry must be a non-empty string",
            )
        stripped = repo.strip()
        for bad in ("\n", "\r", "\0", '"', "'", "`", "$"):
            if bad in stripped:
                raise SshError(
                    error_code="VM_ASTRA_UPDATE_INVALID_REPOSITORY", host=host,
                    message="repository entry must not contain control/quote chars",
                )
        lines.append(stripped)
    return "\\n".join(lines) + "\\n"


async def _write_guest_sources(ssh, host: str, guest_ip: str, content: str) -> None:
    """Перезаписать `/etc/apt/sources.list` в госте (`printf ... > file`)."""
    await run_hub_cmd(
        ssh,
        guest_ssh(
            guest_ip,
            f"bash -c 'printf \"{content}\" > /etc/apt/sources.list'",
            sudo=True,
        ),
        host, "VM_ASTRA_UPDATE_FAILED",
        "не удалось перезаписать sources.list в госте",
    )


async def _reboot_guest_and_wait(ssh, host: str, guest_ip: str) -> None:
    """Ребутнуть гостя и дождаться, пока SSH снова отвечает.

    Отдаём `reboot` (сессия рвётся), выжидаем settle, затем поллим `true` по
    SSH до успешного коннекта. Таймауты — модульные константы (в тестах 0).
    """
    await ssh.run(guest_ssh(guest_ip, "reboot", sudo=True), sudo=True)
    await asyncio.sleep(_GUEST_REBOOT_SETTLE_S)
    for _ in range(_GUEST_REBOOT_MAX_POLLS):
        rc, _out, _err = await ssh.run(guest_ssh(guest_ip, "true"), sudo=True)
        if rc == 0:
            return
        await asyncio.sleep(_GUEST_REBOOT_POLL_DELAY_S)
    raise SshError(
        error_code="VM_ASTRA_UPDATE_FAILED", host=host,
        message=f"гость {guest_ip} не поднялся после reboot",
    )


# ── vm.astra_update ──────────────────────────────────────────────────────────


@broker.task("vm.astra_update")
async def vm_astra_update(task_id: str) -> None:
    """Обновить ОС Astra в снимке ВМ до целевой версии `rc` (порт `Vm.astra_update`).

    Что делает: реверт golden-снимка `<major>_build` (`base_snapshot` из
    payload), в госте перезаписывает `/etc/apt/sources.list` репозиториями
    целевой версии, гонит `astra-update -A -T -r`, перезагружает гостя и ждёт
    его, меняет пароль `u` на новый и снимает deliverable-снимок `<rc>`. Исход
    докладывает server_service (`vms/{id}/snapshots` новый снимок +
    `vms/{id}/state` power/ip).

    Параметры: `task_id`. Payload — `vm_id`, `vm_name`/`name`, `rc`,
    `repository_urls` (непустой список), `base_snapshot` (`<major>_build`),
    опц. `snapshot_id` (id создаваемого `<rc>`-снимка), `kind`, `password`
    (новый пароль `u`), `ip_address`/`guest_ip`, hub-блок.

    Возвращает: `{vm_id, vm_name, rc, snapshot_id, power_state, password_changed}`.

    Возможные ошибки: `VM_INVALID_ARG`, `VM_SNAPSHOT_FAILED`,
    `VM_ASTRA_UPDATE_*`, `VM_PASSWD_FAILED`, `VM_GUEST_NO_IP`. На ошибке —
    best-effort `vms/{id}/state{error}`.
    """
    async def _impl(payload: dict) -> dict:
        vm_id = payload["vm_id"]
        target_dept = payload.get("target_department_id")
        host_label = _host_label(payload)
        vm_name = validate_name(
            payload.get("vm_name") or payload["name"], host_label, "vm_name",
        )
        snapshot_id = payload.get("snapshot_id")
        kind = payload.get("kind")

        try:
            # Валидацию payload'а держим внутри try: server_service выставил
            # busy-lock на dispatch'е — даже отбой по кривому аргументу должен
            # снять его через failed-callback, иначе ВМ зависнет «в обновлении».
            rc_ver = validate_name(str(payload["rc"]), host_label, "rc")
            base_snapshot = validate_name(
                str(payload["base_snapshot"]), host_label, "base_snapshot",
            )
            password = payload.get("password")
            if password is not None:
                password = _validate_guest_password(str(password), host_label)
            sources_content = _sanitize_repositories(
                payload.get("repository_urls"), host_label,
            )
            session, host = await open_hub_session(payload)
            async with session as ssh:
                # 1. откат на golden `<major>_build`
                await run_hub_cmd(
                    ssh,
                    f"virsh snapshot-revert --domain {vm_name} "
                    f"--snapshotname {base_snapshot}",
                    host, "VM_SNAPSHOT_FAILED",
                    f"не удалось откатить на {base_snapshot}",
                )
                # старт на случай, если снимок снят с выключенной ВМ
                await ssh.run(f"virsh start {vm_name}", sudo=True)
                guest_ip = await resolve_guest_ip(ssh, host, vm_name, payload)
                # 2. репозитории целевой версии + astra-update
                await _write_guest_sources(ssh, host, guest_ip, sources_content)
                await run_hub_cmd(
                    ssh,
                    guest_ssh(
                        guest_ip,
                        "bash -c 'DEBIAN_FRONTEND=noninteractive apt-get update && "
                        "astra-update -A -T -r'",
                        sudo=True,
                    ),
                    host, "VM_ASTRA_UPDATE_FAILED",
                    "apt update / astra-update в госте упал",
                )
                # 3. reboot + ожидание
                await _reboot_guest_and_wait(ssh, host, guest_ip)
                # 4. смена пароля `u`
                if password:
                    await _change_guest_password(ssh, host, guest_ip, password)
                # 5. deliverable-снимок целевой версии
                await run_hub_cmd(
                    ssh, _snapshot_create_cmd(vm_name, rc_ver, kind), host,
                    "VM_SNAPSHOT_FAILED", f"не удалось снять снимок {rc_ver}",
                )
                _rc, dom_out, _err = await ssh.run(
                    f"virsh domstate {vm_name}", sudo=True,
                )
                power_state = map_domstate(dom_out)
        except Exception as exc:
            error_text = getattr(exc, "error_code", type(exc).__name__)
            try:
                await server_service_client.submit_vm_state(
                    vm_id, target_department_id=target_dept,
                    status=None, error=str(error_text), clear_busy_state=True,
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "vm.astra_update failed-callback errored vm_id=%s", vm_id,
                    exc_info=True,
                )
            raise

        entry: dict = {"name": rc_ver, "state": "ready", "is_current": True}
        if snapshot_id is not None:
            entry["snapshot_id"] = snapshot_id
        if kind is not None:
            entry["kind"] = kind
        await server_service_client.submit_vm_snapshots(
            vm_id, [entry], target_department_id=target_dept,
        )
        await server_service_client.submit_vm_state(
            vm_id, target_department_id=target_dept,
            power_state=power_state, ip_address=payload.get("ip_address"),
            clear_busy_state=True,
        )
        return {
            "vm_id": vm_id,
            "vm_name": vm_name,
            "rc": rc_ver,
            "snapshot_id": snapshot_id,
            "power_state": power_state,
            "password_changed": bool(password),
        }

    await run_task(
        task_id,
        audit_action="vm.astra_update",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_ASTRA,
    )


# ── vm.allta_update / vm.passwd (reroll) ─────────────────────────────────────


async def _reroll_impl(payload: dict, *, require_password: bool) -> dict:
    """Общий движок reroll'а снимков (`allta_update` == `passwd`).

    По каждому не-`_build` снимку payload'а: реверт → обновление guest-allta
    `.deb` с FTP → опц. `chpasswd u` → пересоздать снимок. `_build`-снимки
    (protected) пропускаются. `passwd` требует пароль; `allta_update` — нет.
    """
    vm_id = payload["vm_id"]
    target_dept = payload.get("target_department_id")
    host_label = _host_label(payload)
    vm_name = validate_name(
        payload.get("vm_name") or payload["name"], host_label, "vm_name",
    )

    rerolled: list[dict] = []
    password_applied = False
    try:
        # Валидацию держим внутри try: server_service выставил busy-lock на
        # dispatch'е — отбой по кривому аргументу тоже должен снять его.
        password = payload.get("password")
        if require_password and not password:
            raise SshError(
                error_code="VM_INVALID_ARG", host=host_label,
                message="vm.passwd требует непустой пароль",
            )
        if password is not None:
            password = _validate_guest_password(str(password), host_label)
        raw_snapshots = payload.get("snapshots") or []
        if not isinstance(raw_snapshots, list) or not raw_snapshots:
            raise SshError(
                error_code="VM_INVALID_ARG", host=host_label,
                message="reroll требует непустой список snapshots",
            )
        session, host = await open_hub_session(payload)
        async with session as ssh:
            for raw in raw_snapshots:
                if isinstance(raw, dict):
                    snap_name = raw.get("snapshot_name") or raw.get("name")
                    snap_id = raw.get("snapshot_id")
                    kind = raw.get("kind")
                else:
                    snap_name, snap_id, kind = raw, None, None
                snap = validate_name(str(snap_name), host, "snapshot_name")
                # `_build` — защищённые golden-снимки: пропускаем целиком.
                if snap.endswith(_BUILD_SUFFIX):
                    continue
                await run_hub_cmd(
                    ssh,
                    f"virsh snapshot-revert --domain {vm_name} --snapshotname {snap}",
                    host, "VM_SNAPSHOT_FAILED",
                    f"не удалось откатить на снимок {snap}",
                )
                await ssh.run(f"virsh start {vm_name}", sudo=True)
                guest_ip = await resolve_guest_ip(ssh, host, vm_name, payload)
                # обновить guest-allta CLI из свежего .deb на FTP
                await run_hub_cmd(
                    ssh,
                    guest_ssh(
                        guest_ip,
                        "bash -c 'cd /tmp && rm -f allta_*_amd64.deb && "
                        f"wget -q {VMS_FTP_ALLTA_DEB_URL} && "
                        "DEBIAN_FRONTEND=noninteractive apt-get install -y "
                        "./allta_*_amd64.deb'",
                        sudo=True,
                    ),
                    host, "VM_ALLTA_UPDATE_FAILED",
                    "обновление guest-allta в госте упало",
                )
                if password:
                    await _change_guest_password(ssh, host, guest_ip, password)
                    password_applied = True
                # пересоздать снимок (перекатка «варианта b»)
                await run_hub_cmd(
                    ssh,
                    f"virsh snapshot-delete --domain {vm_name} --snapshotname {snap}",
                    host, "VM_SNAPSHOT_FAILED",
                    f"не удалось удалить снимок {snap} перед пересъёмкой",
                )
                await run_hub_cmd(
                    ssh, _snapshot_create_cmd(vm_name, snap, kind), host,
                    "VM_SNAPSHOT_FAILED", f"не удалось пересоздать снимок {snap}",
                )
                entry: dict = {"name": snap, "state": "ready"}
                if snap_id is not None:
                    entry["snapshot_id"] = snap_id
                if kind is not None:
                    entry["kind"] = kind
                rerolled.append(entry)
    except Exception as exc:
        error_text = getattr(exc, "error_code", type(exc).__name__)
        try:
            await server_service_client.submit_vm_state(
                vm_id, target_department_id=target_dept,
                status=None, error=str(error_text), clear_busy_state=True,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "reroll failed-callback errored vm_id=%s", vm_id, exc_info=True,
            )
        raise

    if rerolled:
        await server_service_client.submit_vm_snapshots(
            vm_id, rerolled, target_department_id=target_dept,
        )
    await server_service_client.submit_vm_state(
        vm_id, target_department_id=target_dept, clear_busy_state=True,
    )
    password_updated_vms = [vm_id] if password_applied else []
    return {
        "vm_id": vm_id,
        "vm_name": vm_name,
        "snapshots": [e["name"] for e in rerolled],
        "password_updated_vms": password_updated_vms,
        "password_changed": password_applied,
    }


@broker.task("vm.allta_update")
async def vm_allta_update(task_id: str) -> None:
    """Обновить guest-allta во всех снимках ВМ (опц. со сменой пароля).

    Что делает: по каждому не-`_build` снимку — реверт, переустановка свежего
    guest-allta `.deb` с FTP, опциональная смена пароля `u`, пересоздание
    снимка. `_build`-снимки (golden) пропускает. Исход докладывает
    server_service (`vms/{id}/snapshots` пересозданные + снятие lock).

    Параметры: `task_id`. Payload — `vm_id`, `vm_name`/`name`, `snapshots`
    (список `{snapshot_id?, name/snapshot_name, kind?}`), опц. `password`,
    `ip_address`/`guest_ip`, hub-блок.

    Возвращает: `{vm_id, vm_name, snapshots, password_updated_vms,
    password_changed}`.

    Возможные ошибки: `VM_INVALID_ARG`, `VM_SNAPSHOT_FAILED`,
    `VM_ALLTA_UPDATE_FAILED`, `VM_PASSWD_FAILED`, `VM_GUEST_NO_IP`. На ошибке —
    best-effort `vms/{id}/state{error}` + снятие lock.
    """
    async def _impl(payload: dict) -> dict:
        return await _reroll_impl(payload, require_password=False)

    await run_task(
        task_id,
        audit_action="vm.allta_update",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_REROLL,
    )


@broker.task("vm.passwd")
async def vm_passwd(task_id: str) -> None:
    """Сменить пароль `u` во всех снимках ВМ (идентично `allta_update`).

    Тот же op, что `vm.allta_update`, но пароль обязателен: по каждому
    не-`_build` снимку — реверт, переустановка guest-allta, `chpasswd u`,
    пересоздание снимка. Исход докладывает server_service (`vms/{id}/snapshots`
    + снятие lock).

    Параметры: `task_id`. Payload — `vm_id`, `vm_name`/`name`, `snapshots`,
    `password` (обязателен), опц. `ip_address`/`guest_ip`, hub-блок.

    Возвращает: `{vm_id, vm_name, snapshots, password_updated_vms,
    password_changed}`.

    Возможные ошибки: `VM_INVALID_ARG` (пустой пароль/снимки),
    `VM_SNAPSHOT_FAILED`, `VM_ALLTA_UPDATE_FAILED`, `VM_PASSWD_FAILED`,
    `VM_GUEST_NO_IP`. На ошибке — best-effort `vms/{id}/state{error}` + снятие lock.
    """
    async def _impl(payload: dict) -> dict:
        return await _reroll_impl(payload, require_password=True)

    await run_task(
        task_id,
        audit_action="vm.passwd",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_REROLL,
    )
