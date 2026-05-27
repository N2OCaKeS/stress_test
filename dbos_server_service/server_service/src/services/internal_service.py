"""Internal-use cases для server_worker.

Исторически эти endpoint'ы обходили department-фильтр (worker — это
service-to-service caller, работает platform-wide) и опирались только на
матрицу `entity_permissions` — т.е. worker-PAT держал роль `admin` с
глобальными `view_credentials` / `view_password` / `rotate_password`,
что означало: любой holder PAT'а мог читать секреты ЛЮБОГО сервера в
ЛЮБОМ department'е.

Сейчас каждая функция ниже проводит **двухуровневый dept-check** через
`_check_target_department`:

  1. **Actor vs server**. `identity.department_id` (caller bot/user) **обязан**
     совпасть с `server.department_id`. Это блокирует **всегда**, независимо
     от `internal_require_dept_header`. `None` actor (platform-роли) тоже
     отбивается. 403 `TARGET_DEPARTMENT_MISMATCH`, `reason=actor_department_mismatch`.

  2. **`X-Target-Department-Id` header**. Worker форвардит target dept из
     task payload — defense-in-depth поверх actor-check'а (ловит stale
     payload / неправильный dispatch). Behaviour'ом управляет
     `internal_require_dept_header`:
     * `False` (dev/test) → отсутствие/mismatch → audit warning, не блок.
     * `True` (production default) → 403 `TARGET_DEPARTMENT_HEADER_REQUIRED`
       или `TARGET_DEPARTMENT_MISMATCH`.

Header НЕ заменяет существующую `require_action` permission-проверку —
это defense-in-depth cross-check, который делает компрометированный/неправильно
выданный worker-PAT видимым в audit-trail'е (и блокируемым в strict-режиме),
даже когда PAT всё ещё держит глобальные actions.
"""

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.constants import Action, EntityType
from src.core.exceptions import AuthorizationError, NotFoundError
from src.repositories import ipmi_controller as ipmi_repo
from src.repositories import os_version as osv_repo
from src.repositories import server as server_repo
from src.repositories import server_account as account_repo
from src.repositories import server_disk as disk_repo
from src.schemas.identity import IdentityContext
from src.schemas.internal import (
    InventoryCallbackRequest,
    IpmiCredentialsRotatedRequest,
    ProvisionStatusRequest,
    UsersInventoryCallbackRequest,
)
from src.services import audit_service, permissions, secrets_service
from src.utils.ids import os_version_id, server_account_id, server_disk_id


def _check_target_department(
    *,
    audit_action: str,
    target_id: str,
    target_type: str,
    server_department_id: str | None,
    header_department_id: str | None,
    actor_department_id: str | None,
    extra_details: dict | None = None,
) -> None:
    """Cross-check caller department + `X-Target-Department-Id` header против
    server.department_id.

    Два уровня:

    1. **Actor department** (`identity.department_id`) **должен совпасть** с
       `server.department_id`. Это всегда блокирует, независимо от
       `internal_require_dept_header` — soft mode не должен открывать
       cross-department leak. `None` actor (platform-роли) тоже блокируется.
       403 ``TARGET_DEPARTMENT_MISMATCH``, `reason=actor_department_mismatch`.

    2. **Header** (`X-Target-Department-Id`). Бросает ``AuthorizationError``
       только в strict-режиме: header отсутствует → 403
       ``TARGET_DEPARTMENT_HEADER_REQUIRED``, header не совпадает → 403
       ``TARGET_DEPARTMENT_MISMATCH`` с `reason=target_department_mismatch`.
       В soft-режиме mismatch и missing — это warning-audit, не блок.

    `audit_action` — тот же action-key, что caller использует для
    success/denied/failure emit'ов, чтобы оператор мог корреллировать.
    """
    strict = get_settings().internal_require_dept_header
    extra = dict(extra_details or {})
    extra.update({
        "server_department_id": server_department_id,
        "header_department_id": header_department_id,
        "actor_department_id": actor_department_id,
    })

    # Actor-vs-server check: всегда блокирующий, не зависит от soft/strict.
    # Closes cross-department password/credentials leak в soft-mode, где
    # отсутствие/несовпадение `X-Target-Department-Id` header'а не отбивалось.
    if actor_department_id is None or actor_department_id != server_department_id:
        audit_service.emit(
            audit_action,
            target_id=target_id, target_type=target_type,
            status="denied", allowed=False,
            details={**extra, "reason": "actor_department_mismatch"},
        )
        raise AuthorizationError(
            error_code="TARGET_DEPARTMENT_MISMATCH",
            message=(
                "Caller department does not match the server's actual department"
            ),
            details={"server_id": target_id},
        )

    if header_department_id is None:
        if strict:
            audit_service.emit(
                audit_action,
                target_id=target_id, target_type=target_type,
                status="denied", allowed=False,
                details={**extra, "reason": "missing_target_department_header"},
            )
            raise AuthorizationError(
                error_code="TARGET_DEPARTMENT_HEADER_REQUIRED",
                message=(
                    "X-Target-Department-Id header is required for internal "
                    "credential endpoints in strict mode"
                ),
                details={"server_id": target_id},
            )
        # Soft mode: всё равно фиксируем отсутствие как warning-event.
        audit_service.emit(
            audit_action,
            target_id=target_id, target_type=target_type,
            status="warning", allowed=True,
            details={**extra, "reason": "missing_target_department_header_soft"},
        )
        return

    if header_department_id != server_department_id:
        # Mismatch эмитим всегда, независимо от режима — это сигнал бага в
        # worker'е (stale payload) или, хуже, PAT'а, который щупает чужие
        # отделы.
        audit_service.emit(
            audit_action,
            target_id=target_id, target_type=target_type,
            status="denied" if strict else "warning",
            allowed=not strict,
            details={**extra, "reason": "target_department_mismatch"},
        )
        if strict:
            raise AuthorizationError(
                error_code="TARGET_DEPARTMENT_MISMATCH",
                message=(
                    "X-Target-Department-Id does not match the server's "
                    "actual department"
                ),
                details={"server_id": target_id},
            )


async def fetch_ipmi_credentials(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    target_department_id: str | None = None,
) -> dict:
    """Расшифровать и вернуть IPMI-credentials для worker'а."""
    try:
        await permissions.require_action(
            db, identity, EntityType.IPMI_CONTROLLER, Action.VIEW_CREDENTIALS
        )
    except AuthorizationError:
        audit_service.emit(
            "ipmi_controller.view_credentials",
            target_id=server_id, target_type="ipmi_controller",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise
    server = await server_repo.get_by_id(db, server_id)
    if server is None:
        audit_service.emit(
            "ipmi_controller.view_credentials",
            target_id=server_id, target_type="ipmi_controller",
            status="failure", allowed=True,
            details={"reason": "server_not_found"},
        )
        raise NotFoundError(error_code="SERVER_NOT_FOUND", message="Server not found")
    _check_target_department(
        audit_action="ipmi_controller.view_credentials",
        target_id=server_id,
        target_type="ipmi_controller",
        server_department_id=server.department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
    )
    ctrl = await ipmi_repo.get_by_server_id(db, server_id)
    if ctrl is None:
        audit_service.emit(
            "ipmi_controller.view_credentials",
            target_id=server_id, target_type="ipmi_controller",
            status="failure", allowed=True,
            details={"reason": "controller_not_registered"},
        )
        raise NotFoundError(
            error_code="IPMI_CONTROLLER_NOT_FOUND",
            message="No IPMI controller is registered for this server",
        )
    plain = secrets_service.decrypt(
        ctrl.password_encrypted,
        aad=secrets_service.aad_for_ipmi_credential(ctrl.id),
    )
    audit_service.emit(
        "ipmi_controller.view_credentials",
        target_id=ctrl.id, target_type="ipmi_controller",
        status="success", allowed=True,
        details={
            "server_id": server_id,
            "kind": ctrl.kind,
            "department_id": server.department_id,
        },
    )
    return {
        "controller_id": ctrl.id,
        "kind": ctrl.kind,
        "endpoint_url": ctrl.endpoint_url,
        "username": ctrl.username,
        "password": plain,
    }


async def fetch_account_password(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    account_id: str,
    target_department_id: str | None = None,
) -> dict:
    """Расшифровать и вернуть пароль OS-аккаунта для worker'а."""
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.VIEW_PASSWORD
        )
    except AuthorizationError:
        audit_service.emit(
            "server_account.view_password",
            target_id=account_id, target_type="server_account",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "server_id": server_id},
        )
        raise
    account = await account_repo.get_by_id(db, account_id)
    if account is None or not await account_repo.is_linked(db, account_id, server_id):
        audit_service.emit(
            "server_account.view_password",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "account_not_found", "server_id": server_id},
        )
        raise NotFoundError(
            error_code="ACCOUNT_NOT_FOUND",
            message="Server account not found on this server",
        )
    # Server lookup делаем безусловно — нужен `server.department_id` для
    # actor-vs-server dept check'а, который блокирует cross-department
    # утечку даже в soft-mode. Симметрично с `fetch_ipmi_credentials`.
    server = await server_repo.get_by_id(db, server_id)
    server_department_id = server.department_id if server is not None else None
    _check_target_department(
        audit_action="server_account.view_password",
        target_id=account_id,
        target_type="server_account",
        server_department_id=server_department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
        extra_details={"server_id": server_id},
    )
    # Soft-mode + missing header: actor-check уже прошёл выше, header-missing
    # warning эмитит `_check_target_department`. Дополнительный
    # `internal.dept_header_missing` сохранён для SIEM-совместимости —
    # отдельный action, по которому считают «worker без header'а».
    if target_department_id is None and not get_settings().internal_require_dept_header:
        audit_service.emit(
            "internal.dept_header_missing",
            actor_id=identity.user_id,
            target_id=account_id,
            target_type="server_account",
            status="warning",
            allowed=True,
            details={
                "path": "internal.fetch_account_password",
                "server_id": server_id,
                "soft_mode": True,
            },
        )
    if account.password_encrypted is None:
        audit_service.emit(
            "server_account.view_password",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "no_password_stored", "server_id": server_id},
        )
        raise NotFoundError(
            error_code="ACCOUNT_HAS_NO_PASSWORD",
            message="Account has no stored password",
        )
    plain = secrets_service.decrypt(
        account.password_encrypted,
        aad=secrets_service.aad_for_server_account_password(account.id),
    )
    audit_service.emit(
        "server_account.view_password",
        target_id=account_id, target_type="server_account",
        status="success", allowed=True,
        details={"server_id": server_id, "login": account.login},
    )
    return {"login": account.login, "password": plain}


async def rotate_account_password(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    account_id: str,
    new_password: str,
    target_department_id: str | None = None,
) -> dict:
    """Принять новый пароль OS-аккаунта (callback worker'а после SSH-apply)."""
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.ROTATE_PASSWORD
        )
    except AuthorizationError:
        audit_service.emit(
            "server_account.rotate_password",
            target_id=account_id, target_type="server_account",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "server_id": server_id},
        )
        raise
    account = await account_repo.get_by_id(db, account_id)
    if account is None or not await account_repo.is_linked(db, account_id, server_id):
        audit_service.emit(
            "server_account.rotate_password",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "account_not_found", "server_id": server_id},
        )
        raise NotFoundError(
            error_code="ACCOUNT_NOT_FOUND",
            message="Server account not found on this server",
        )
    # Server lookup безусловный — actor-vs-server dept check блокирует
    # cross-department rotate в soft-mode (worker без header'а из чужого
    # отдела ранее проходил без проверки). Симметрично с `fetch_ipmi_credentials`.
    server = await server_repo.get_by_id(db, server_id)
    server_department_id = server.department_id if server is not None else None
    _check_target_department(
        audit_action="server_account.rotate_password",
        target_id=account_id,
        target_type="server_account",
        server_department_id=server_department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
        extra_details={"server_id": server_id},
    )
    # Soft-mode + missing header: actor-check прошёл, header-missing warning
    # уже эмитнул `_check_target_department`. Дополнительный
    # `internal.dept_header_missing` сохраняем для SIEM-совместимости.
    if target_department_id is None and not get_settings().internal_require_dept_header:
        audit_service.emit(
            "internal.dept_header_missing",
            actor_id=identity.user_id,
            target_id=account_id,
            target_type="server_account",
            status="warning",
            allowed=True,
            details={
                "path": "internal.rotate_account_password",
                "server_id": server_id,
                "soft_mode": True,
            },
        )
    encrypted = secrets_service.encrypt(
        new_password,
        aad=secrets_service.aad_for_server_account_password(account.id),
    )
    updated = await account_repo.update_password(db, account, encrypted)
    await db.commit()
    audit_service.emit(
        "server_account.rotate_password",
        target_id=account_id, target_type="server_account",
        status="success", allowed=True,
        details={
            "server_id": server_id,
            "login": account.login,
            "rotated_at": updated.password_rotated_at.isoformat(),
        },
    )
    return {"ok": True, "rotated_at": updated.password_rotated_at.isoformat()}


# ── Worker callbacks (write-direction internal API) ─────────────────────────


async def _resolve_or_create_os(db: AsyncSession, name: str) -> str:
    """Lookup OS-версии по name; INSERT при first-seen."""
    obj = await osv_repo.get_by_name(db, name)
    if obj is not None:
        return obj.id
    created = await osv_repo.create(db, {
        "id": os_version_id(),
        "name": name,
    })
    return created.id


async def _upsert_disks(
    db: AsyncSession, server_id: str, items: list,
) -> int:
    """Bulk-upsert по (server_id, device_name).

    Возвращает счётчик затронутых строк (INSERT + UPDATE). is_system-инвариант
    (ровно один system disk на server) гарантирует partial unique index в
    миграции, дубли отбиваются IntegrityError'ом на уровне БД.
    """
    touched = 0
    for item in items:
        existing = await disk_repo.get_by_server_and_device(
            db, server_id, item.name,
        )
        if existing is not None:
            await disk_repo.update(db, existing, {
                "size_gb": item.size_gb,
                "model": item.model,
                "is_system": item.is_system,
            })
        else:
            await disk_repo.create(db, {
                "id": server_disk_id(),
                "server_id": server_id,
                "device_name": item.name,
                "size_gb": item.size_gb,
                "model": item.model,
                "is_system": item.is_system,
            })
        touched += 1
    return touched


async def receive_inventory(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    payload: InventoryCallbackRequest,
    target_department_id: str | None = None,
) -> dict:
    """Принять hardware-facts от worker'а после `inventory.sync`.

    Поток: permission check (`server:inventory_submit`) → server lookup → dept
    cross-check → upsert os_versions по имени → апдейт CPU-полей и
    hostname/cpu_*/os_version_id на server → bulk-upsert дисков → commit →
    audit `server.inventory_received`. CPU-данные пишутся плоско в строку
    `servers` (cpu_brand/cpu_model/cpu_cores/cpu_threads/cpu_frequency_ghz),
    отдельной таблицы-каталога нет.
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.INVENTORY_SUBMIT,
        )
    except AuthorizationError:
        audit_service.emit(
            "server.inventory_received",
            target_id=server_id, target_type="server",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise

    server = await server_repo.get_by_id(db, server_id)
    if server is None:
        audit_service.emit(
            "server.inventory_received",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "server_not_found"},
        )
        raise NotFoundError(
            error_code="SERVER_NOT_FOUND", message="Server not found",
        )

    _check_target_department(
        audit_action="server.inventory_received",
        target_id=server_id,
        target_type="server",
        server_department_id=server.department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
    )

    os_id_resolved = await _resolve_or_create_os(db, payload.os_version)

    await server_repo.update(db, server, {
        "hostname": payload.hostname,
        "cpu_brand": payload.cpu_brand,
        "cpu_model": payload.cpu_model,
        "cpu_cores": payload.cpu_cores,
        "cpu_threads": payload.cpu_threads,
        "cpu_frequency_ghz": payload.cpu_frequency_ghz,
        "os_version_id": os_id_resolved,
        "os_last_synced_at": datetime.now(timezone.utc),
    })
    disks_count = await _upsert_disks(db, server_id, payload.disks)
    await db.commit()

    audit_service.emit(
        "server.inventory_received",
        target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "hostname": payload.hostname,
            "cpu_brand": payload.cpu_brand,
            "cpu_model": payload.cpu_model,
            "os_version": payload.os_version,
            "disks": disks_count,
            "department_id": server.department_id,
        },
    )
    return {
        "ok": True,
        "os_version_id": os_id_resolved,
        "disks_upserted": disks_count,
    }


async def receive_users_inventory(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    payload: UsersInventoryCallbackRequest,
    target_department_id: str | None = None,
) -> dict:
    """Принять список реальных OS-пользователей от worker'а и reconcile'ить
    его против привязанных к серверу `server_accounts`.

    Право: `(server_account, *, inventory_submit)` — узкий грант worker_bot'а.

    Reconcile (для инвентаризуемого сервера X):

      * найден на X, нет привязанного аккаунта → создать discovered-аккаунт
        (без пароля, `source=discovered`, `department_id` = dept сервера X),
        привязать к X;
      * есть и там, и в API → обновить метаданные (sudo, группы, shell, home),
        пометить связку present + свежий `last_inventory_at`;
      * привязан в API, но не найден на сервере → пометить связку
        `present_on_server=False` (drift), запись НЕ удаляем.
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.INVENTORY_SUBMIT,
        )
    except AuthorizationError:
        audit_service.emit(
            "server_account.users_inventory_received",
            target_id=server_id, target_type="server",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise

    server = await server_repo.get_by_id(db, server_id)
    if server is None:
        audit_service.emit(
            "server_account.users_inventory_received",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "server_not_found"},
        )
        raise NotFoundError(
            error_code="SERVER_NOT_FOUND", message="Server not found",
        )

    _check_target_department(
        audit_action="server_account.users_inventory_received",
        target_id=server_id,
        target_type="server",
        server_department_id=server.department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
    )

    # Снимок текущих связок сервера + login'ов, найденных на боксе.
    links = await account_repo.list_links_for_server(db, server_id)
    seen_logins = {item.login for item in payload.users}

    created = 0
    updated = 0
    drifted = 0

    for item in payload.users:
        existing = await account_repo.get_account_on_server_by_login(
            db, server_id, item.login,
        )
        if existing is None:
            account_id = server_account_id()
            await account_repo.create_discovered(
                db,
                {
                    "id": account_id,
                    "department_id": server.department_id,
                    "login": item.login,
                    "password_encrypted": None,
                    "source": "discovered",
                    "has_sudo": item.has_sudo,
                    "unix_groups": list(item.unix_groups),
                    "shell": item.shell,
                    "home_dir": item.home_dir,
                    "is_active": True,
                    "created_by": identity.user_id,
                },
                server_id,
            )
            created += 1
        else:
            await account_repo.update(db, existing, {
                "has_sudo": item.has_sudo,
                "unix_groups": list(item.unix_groups),
                "shell": item.shell,
                "home_dir": item.home_dir,
            })
            link = await account_repo.get_link(db, existing.id, server_id)
            if link is not None:
                await account_repo.mark_link_inventoried(db, link, present=True)
            updated += 1

    # Привязанные в API, но не найденные на сервере — drift.
    for link in links:
        if link.login not in seen_logins:
            await account_repo.mark_link_inventoried(db, link, present=False)
            drifted += 1

    await db.commit()

    audit_service.emit(
        "server_account.users_inventory_received",
        target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "created": created,
            "updated": updated,
            "drifted": drifted,
            "found": len(payload.users),
            "department_id": server.department_id,
        },
    )
    return {"ok": True, "created": created, "updated": updated, "drifted": drifted}


async def record_provision_status(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    account_id: str,
    payload: ProvisionStatusRequest,
    target_department_id: str | None = None,
) -> dict:
    """Зафиксировать результат useradd/usermod/userdel на боксе (callback worker'а).

    Право: `(server_account, *, provision_on_host)` — узкий грант worker_bot'а.

    Обновляет `present_on_server` на связке аккаунт ↔ сервер: provision/update
    → True, deprovision → False. `last_inventory_at` не трогается — это не
    инвентаризация. Аккаунт обязан быть привязан к серверу, иначе 404.
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.PROVISION_ON_HOST,
        )
    except AuthorizationError:
        audit_service.emit(
            "server_account.provision_status",
            target_id=account_id, target_type="server_account",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "server_id": server_id},
        )
        raise

    account = await account_repo.get_by_id(db, account_id)
    link = await account_repo.get_link(db, account_id, server_id)
    if account is None or link is None:
        audit_service.emit(
            "server_account.provision_status",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "account_not_found", "server_id": server_id},
        )
        raise NotFoundError(
            error_code="ACCOUNT_NOT_FOUND",
            message="Server account not found on this server",
        )

    server = await server_repo.get_by_id(db, server_id)
    server_department_id = server.department_id if server is not None else None
    _check_target_department(
        audit_action="server_account.provision_status",
        target_id=account_id,
        target_type="server_account",
        server_department_id=server_department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
        extra_details={"server_id": server_id},
    )

    await account_repo.set_link_presence(db, link, present=payload.present)
    await db.commit()

    audit_service.emit(
        "server_account.provision_status",
        target_id=account_id, target_type="server_account",
        status="success", allowed=True,
        details={
            "server_id": server_id,
            "login": account.login,
            "operation": payload.operation,
            "present_on_server": payload.present,
            "department_id": server_department_id,
        },
    )
    return {"ok": True, "present_on_server": payload.present}


async def record_ipmi_credentials_rotated(
    db: AsyncSession,
    identity: IdentityContext,
    controller_id: str,
    payload: IpmiCredentialsRotatedRequest,
    target_department_id: str | None = None,
) -> dict:
    """Сохранить результат rotate'а IPMI-credentials, инициированного worker'ом.

    Симметрия с `rotate_account_password`: worker присылает plaintext по TLS
    внутри cluster'а, server_service шифрует через `secrets_service.encrypt()`
    и сохраняет ciphertext в `ipmi_controllers.password_encrypted`. У worker'а
    нет `SERVER_ENCRYPTION_KEY`, поэтому encrypt происходит на приёмной стороне.

    Аудит — WARNING, `ipmi_controller.credentials_rotated_callback`.
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.IPMI_CONTROLLER, Action.ROTATE_CREDENTIALS,
        )
    except AuthorizationError:
        audit_service.emit(
            "ipmi_controller.credentials_rotated_callback",
            target_id=controller_id, target_type="ipmi_controller",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise

    ctrl = await ipmi_repo.get_by_id(db, controller_id)
    if ctrl is None:
        audit_service.emit(
            "ipmi_controller.credentials_rotated_callback",
            target_id=controller_id, target_type="ipmi_controller",
            status="failure", allowed=True,
            details={"reason": "controller_not_found"},
        )
        raise NotFoundError(
            error_code="IPMI_CONTROLLER_NOT_FOUND",
            message="IPMI controller not found",
        )

    server = await server_repo.get_by_id(db, ctrl.server_id)
    server_dept = server.department_id if server is not None else None
    _check_target_department(
        audit_action="ipmi_controller.credentials_rotated_callback",
        target_id=controller_id,
        target_type="ipmi_controller",
        server_department_id=server_dept,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
        extra_details={"server_id": ctrl.server_id},
    )

    rotated_at = payload.rotated_at
    if rotated_at.tzinfo is None:
        rotated_at = rotated_at.replace(tzinfo=timezone.utc)

    encrypted = secrets_service.encrypt(
        payload.new_password,
        aad=secrets_service.aad_for_ipmi_credential(ctrl.id),
    )
    await ipmi_repo.update(db, ctrl, {
        "password_encrypted": encrypted,
        "password_rotated_at": rotated_at,
    })
    await db.commit()

    audit_service.emit(
        "ipmi_controller.credentials_rotated_callback",
        target_id=controller_id, target_type="ipmi_controller",
        status="success", allowed=True,
        details={
            "server_id": ctrl.server_id,
            "rotated_at": rotated_at.isoformat(),
            "department_id": server_dept,
        },
    )
    return {"ok": True, "rotated_at": rotated_at.isoformat()}


