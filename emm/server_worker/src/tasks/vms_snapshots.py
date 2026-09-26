"""Задачи VM-менеджера: снимки, обновление ОС и перекатка кред — по SSH на hub'е.

Надстройка над базовыми VM-тасками (`tasks/vms.py`, `tasks/vms_disks.py`): та же
управляющая hub-сессия и sudo NOPASSWD (libvirt/kvm без пароля), гость по
`sshpass` (`u`/`1`). Хендлеры:

* `vm.snapshot_create` — `virsh snapshot-create-as` (`--disk-only`/`--live` по
  `snapshot_type`); callback снимок→`ready` (`kind='user'`).
* `vm.snapshot_delete` — `virsh snapshot-delete`.
* `vm.snapshot_revert` — `virsh snapshot-revert`; в режиме `per_snapshot`
  server_service сам переключит активные креды ВМ по callback'у.
* `vm.astra_update` — revert `<major>_build` → перезапись sources.list в госте →
  `astra-update -A -T -r` → reboot(wait) → смена пароля (managed → `dbos`, legacy
  → `u`) → снимок `<rc>`.
* `vm.allta_update` / `vm.passwd` — «reroll»: по каждому не-`_build` снимку
  revert → обновить guest-allta `.deb` с FTP → опц. смена пароля (managed →
  `dbos`, legacy → `u`) → пересоздать снимок. Один и тот же op; `passwd` требует
  пароль обязательно.

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
from src.tasks._vm_prepare_helpers import (
    _shred_temp_key,
    choose_guest_connector,
    load_guest_key,
)
from src.tasks._vms_helpers import (
    LIBVIRT_SESSION_ENV,
    MODE_OREL,
    MODE_SMOLENSK,
    SNAPSHOT_KIND_OS_BASELINE,
    guest_connector,
    map_domstate,
    open_hub_session,
    resolve_guest_ip,
    run_hub_cmd,
    switch_guest_to_smolensk,
    validate_name,
)

logger = logging.getLogger(__name__)


AUDIT_SAFE_FIELDS_SNAPSHOT: set[str] = {
    "vm_id", "snapshot_id", "name", "kind", "snapshot_type", "state",
    "is_current", "power_state",
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


def _snapshot_create_cmd(vm_name: str, snap: str, snapshot_type: str | None) -> str:
    """Собрать `virsh snapshot-create-as` с флагом по способу снятия.

    `disk_only` → внешний disk-only снимок (`--disk-only --atomic`), `full`
    (и дефолт) → полный снимок работающего домена (`--live`).
    """
    cmd = f"virsh snapshot-create-as --domain {vm_name} --name {snap}"
    if snapshot_type == "disk_only":
        cmd += " --disk-only --atomic"
    elif snapshot_type == "full":
        cmd += " --live"
    return cmd


def _validate_guest_password(value: str, host: str) -> str:
    """Отбить пароль гостя с символами, ломающими inline-`chpasswd` в госте.

    Пароль уходит в `bash -c 'echo <login>:<pwd> | chpasswd'` внутри вложенной
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


async def _change_guest_password(
    ssh, host: str, guest_ip: str, password: str,
    *, login: str = VMS_GUEST_LOGIN, connect=None,
) -> None:
    """Сменить пароль аккаунта в госте (`echo <login>:<pwd> | chpasswd`).

    `login` — целевой аккаунт: на legacy-ВМ базовый `u`, на managed-ВМ (где `u`
    снесён при create) — управляющий пользователь `dbos`. `connect` — коннектор
    входа в гостя; по умолчанию `u`/`1`, на managed-ВМ сюда передаётся ключевой
    коннектор управляющего пользователя.
    """
    if connect is None:
        connect = guest_connector(guest_ip)
    await run_hub_cmd(
        ssh,
        connect(
            f"bash -c 'echo {login}:{password} | chpasswd'",
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

    Что делает: заходит на hub по SSH, снимает снимок домена с флагом по способу
    (`disk_only` → `--disk-only`, `full` → `--live`). Исход докладывает
    server_service батчем `vms/{id}/snapshots` (`state='ready'`,
    `is_current=True`, `kind='user'`).

    Параметры: `task_id`. Payload — `vm_id`, `vm_name`/`name`, `snapshot_id`,
    `snapshot_name`/`name`, опц. `snapshot_type` (`disk_only`|`full`), hub-блок.

    Возвращает: `{vm_id, snapshot_id, name, kind, snapshot_type, state,
    is_current}`.

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
        snapshot_type = payload.get("snapshot_type")

        try:
            session, host = await open_hub_session(payload)
            async with session as ssh:
                await run_hub_cmd(
                    ssh, _snapshot_create_cmd(vm_name, snap, snapshot_type), host,
                    "VM_SNAPSHOT_FAILED", f"не удалось создать снимок {snap}",
                )
        except Exception as exc:
            await _report_snapshot_error(
                vm_id, snapshot_id, snap, target_dept, "vm.snapshot_create", exc,
            )
            raise

        # Снятые пользователем снимки — всегда категория `user`, режим не несут.
        entry: dict = {
            "name": snap, "state": "ready", "is_current": True, "kind": "user",
        }
        if snapshot_id is not None:
            entry["snapshot_id"] = snapshot_id
        if snapshot_type is not None:
            entry["snapshot_type"] = snapshot_type
        await server_service_client.submit_vm_snapshots(
            vm_id, [entry], target_department_id=target_dept,
        )
        return {
            "vm_id": vm_id,
            "snapshot_id": snapshot_id,
            "name": snap,
            "kind": "user",
            "snapshot_type": snapshot_type,
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


async def revert_domain(ssh, vm_name: str, snap: str, host: str) -> str:
    """`virsh snapshot-revert` + `virsh domstate` → power_state.

    Общий шаг `vm.snapshot_revert` и отката ВМ-стенда перед тестом
    (`vm.prepare_for_test`). `vm_name`/`snap` уже провалидированы.
    """
    await run_hub_cmd(
        ssh,
        f"virsh snapshot-revert --domain {vm_name} --snapshotname {snap}",
        host, "VM_SNAPSHOT_FAILED", f"не удалось откатить на снимок {snap}",
    )
    _rc, dom_out, _err = await ssh.run(
        f"{LIBVIRT_SESSION_ENV} virsh domstate {vm_name}", sudo=True,
    )
    return map_domstate(dom_out)


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
                power_state = await revert_domain(ssh, vm_name, snap, host)
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


async def _write_guest_sources(
    ssh, host: str, guest_ip: str, content: str, *, connect=None,
) -> None:
    """Перезаписать `/etc/apt/sources.list` в госте (`printf ... > file`).

    `connect` — коннектор входа в гостя; по умолчанию `u`/`1`, на managed-ВМ —
    ключевой коннектор управляющего пользователя.
    """
    if connect is None:
        connect = guest_connector(guest_ip)
    await run_hub_cmd(
        ssh,
        connect(
            f"bash -c 'printf \"{content}\" > /etc/apt/sources.list'",
            sudo=True,
        ),
        host, "VM_ASTRA_UPDATE_FAILED",
        "не удалось перезаписать sources.list в госте",
    )


async def _reboot_guest_and_wait(ssh, host: str, guest_ip: str, *, connect=None) -> None:
    """Ребутнуть гостя и дождаться, пока SSH снова отвечает.

    Отдаём `reboot` (сессия рвётся), выжидаем settle, затем поллим `true` по
    SSH до успешного коннекта. Таймауты — модульные константы (в тестах 0).
    `connect` — коннектор входа в гостя; по умолчанию `u`/`1`, на managed-ВМ —
    ключевой коннектор управляющего пользователя.
    """
    if connect is None:
        connect = guest_connector(guest_ip)
    await ssh.run(connect("reboot", sudo=True), sudo=True)
    await asyncio.sleep(_GUEST_REBOOT_SETTLE_S)
    for _ in range(_GUEST_REBOOT_MAX_POLLS):
        rc, _out, _err = await ssh.run(connect("true"), sudo=True)
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
    его, меняет пароль целевого аккаунта на новый (managed → управляющий `dbos`,
    legacy → базовый `u`), снимает deliverable Орла `<rc>`, переводит
    гостя в Смоленск (astra-modeswitch + МРД/МКЦ + reboot) и снимает `<rc>_smolensk`.
    Оба снимка (mode orel/smolensk) докладывает `vms/{id}/snapshots`, состояние
    — `vms/{id}/state` power/ip.

    Параметры: `task_id`. Payload — `vm_id`, `vm_name`/`name`, `rc`,
    `repository_urls` (непустой список), `base_snapshot` (`<major>_build`),
    опц. `snapshot_id` (id создаваемого `<rc>`-снимка), `snapshot_type`,
    `password` (новый пароль `u`), `ip_address`/`guest_ip`, hub-блок.

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
        snapshot_type = payload.get("snapshot_type")

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
                await ssh.run(f"{LIBVIRT_SESSION_ENV} virsh start {vm_name}", sudo=True)
                guest_ip = await resolve_guest_ip(ssh, host, vm_name, payload)
                # managed-ВМ: guest-шаги идут по управляющему ключу (базовой
                # учётки `u` нет); legacy-ВМ — по `u`/`1`.
                mgmt_user, key_path = await load_guest_key(ssh, host, payload)
                connect = choose_guest_connector(guest_ip, mgmt_user, key_path)
                # на managed-ВМ пароль меняем управляющему `dbos` (базовый `u`
                # снесён при create); на legacy — прежней базовой учётке `u`.
                guest_login = mgmt_user or VMS_GUEST_LOGIN
                smolensk_snap = f"{rc_ver}_{MODE_SMOLENSK}"
                try:
                    # 2. репозитории целевой версии + astra-update
                    await _write_guest_sources(
                        ssh, host, guest_ip, sources_content, connect=connect,
                    )
                    await run_hub_cmd(
                        ssh,
                        connect(
                            "bash -c 'DEBIAN_FRONTEND=noninteractive apt-get update && "
                            "astra-update -A -T -r'",
                            sudo=True,
                        ),
                        host, "VM_ASTRA_UPDATE_FAILED",
                        "apt update / astra-update в госте упал",
                    )
                    # 3. reboot + ожидание
                    await _reboot_guest_and_wait(ssh, host, guest_ip, connect=connect)
                    # 4. смена пароля целевого аккаунта (managed → `dbos`)
                    if password:
                        await _change_guest_password(
                            ssh, host, guest_ip, password,
                            login=guest_login, connect=connect,
                        )
                    # 5. deliverable Орла `<rc>`
                    await run_hub_cmd(
                        ssh, _snapshot_create_cmd(vm_name, rc_ver, snapshot_type), host,
                        "VM_SNAPSHOT_FAILED", f"не удалось снять снимок {rc_ver}",
                    )
                    # 6. перевод гостя в Смоленск + deliverable `<rc>_smolensk`
                    await switch_guest_to_smolensk(
                        ssh, host, guest_ip, error_code="VM_ASTRA_UPDATE_FAILED",
                        connect=connect,
                    )
                    await run_hub_cmd(
                        ssh, _snapshot_create_cmd(vm_name, smolensk_snap, snapshot_type),
                        host, "VM_SNAPSHOT_FAILED",
                        f"не удалось снять снимок {smolensk_snap}",
                    )
                finally:
                    if key_path:
                        await _shred_temp_key(ssh, key_path)
                _rc, dom_out, _err = await ssh.run(
                    f"{LIBVIRT_SESSION_ENV} virsh domstate {vm_name}", sudo=True,
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

        # Оба deliverable'а версии: Орёл (`<rc>`, текущий) + Смоленск
        # (`<rc>_smolensk`). Несут kind=os_baseline + mode + os_version — по ним
        # server_service группирует чистые снимки версий (контракт снимков).
        orel_entry: dict = {
            "name": rc_ver, "state": "ready", "is_current": True,
            "kind": SNAPSHOT_KIND_OS_BASELINE, "mode": MODE_OREL,
            "os_version": rc_ver,
        }
        if snapshot_id is not None:
            orel_entry["snapshot_id"] = snapshot_id
        if snapshot_type is not None:
            orel_entry["snapshot_type"] = snapshot_type
        smolensk_entry: dict = {
            "name": smolensk_snap, "state": "ready",
            "kind": SNAPSHOT_KIND_OS_BASELINE, "mode": MODE_SMOLENSK,
            "os_version": rc_ver,
        }
        if snapshot_type is not None:
            smolensk_entry["snapshot_type"] = snapshot_type
        await server_service_client.submit_vm_snapshots(
            vm_id, [orel_entry, smolensk_entry], target_department_id=target_dept,
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
            # managed-ВМ: guest-шаги идут по управляющему ключу (базовой учётки
            # `u` нет); ключ пишем один раз на весь reroll, коннектор пересобираем
            # под адрес гостя каждой итерации. legacy-ВМ — по `u`/`1`.
            mgmt_user, key_path = await load_guest_key(ssh, host, payload)
            # managed-ВМ: пароль меняем управляющему `dbos` (`u` снесён при
            # create); legacy — прежней базовой учётке `u`.
            guest_login = mgmt_user or VMS_GUEST_LOGIN
            try:
                for raw in raw_snapshots:
                    if isinstance(raw, dict):
                        snap_name = raw.get("snapshot_name") or raw.get("name")
                        snap_id = raw.get("snapshot_id")
                        snapshot_type = raw.get("snapshot_type")
                    else:
                        snap_name, snap_id, snapshot_type = raw, None, None
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
                    await ssh.run(f"{LIBVIRT_SESSION_ENV} virsh start {vm_name}", sudo=True)
                    guest_ip = await resolve_guest_ip(ssh, host, vm_name, payload)
                    connect = choose_guest_connector(guest_ip, mgmt_user, key_path)
                    # обновить guest-allta CLI из свежего .deb на FTP
                    await run_hub_cmd(
                        ssh,
                        connect(
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
                        await _change_guest_password(
                            ssh, host, guest_ip, password,
                            login=guest_login, connect=connect,
                        )
                        password_applied = True
                    # пересоздать снимок (перекатка «варианта b»)
                    await run_hub_cmd(
                        ssh,
                        f"virsh snapshot-delete --domain {vm_name} --snapshotname {snap}",
                        host, "VM_SNAPSHOT_FAILED",
                        f"не удалось удалить снимок {snap} перед пересъёмкой",
                    )
                    await run_hub_cmd(
                        ssh, _snapshot_create_cmd(vm_name, snap, snapshot_type), host,
                        "VM_SNAPSHOT_FAILED", f"не удалось пересоздать снимок {snap}",
                    )
                    # Пересъёмка не меняет категорию/режим снимка — их server_service
                    # хранит по имени; шлём только способ снятия, если он известен.
                    entry: dict = {"name": snap, "state": "ready"}
                    if snap_id is not None:
                        entry["snapshot_id"] = snap_id
                    if snapshot_type is not None:
                        entry["snapshot_type"] = snapshot_type
                    rerolled.append(entry)
            finally:
                if key_path:
                    await _shred_temp_key(ssh, key_path)
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
    guest-allta `.deb` с FTP, опциональная смена пароля (managed → `dbos`,
    legacy → `u`), пересоздание снимка. `_build`-снимки (golden) пропускает.
    Исход докладывает
    server_service (`vms/{id}/snapshots` пересозданные + снятие lock).

    Параметры: `task_id`. Payload — `vm_id`, `vm_name`/`name`, `snapshots`
    (список `{snapshot_id?, name/snapshot_name, snapshot_type?}`), опц.
    `password`, `ip_address`/`guest_ip`, hub-блок.

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
    """Сменить пароль во всех снимках ВМ (идентично `allta_update`).

    Тот же op, что `vm.allta_update`, но пароль обязателен: по каждому
    не-`_build` снимку — реверт, переустановка guest-allta, смена пароля
    целевого аккаунта (managed → `dbos`, legacy → `u`), пересоздание снимка.
    Исход докладывает server_service (`vms/{id}/snapshots` + снятие lock).

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
