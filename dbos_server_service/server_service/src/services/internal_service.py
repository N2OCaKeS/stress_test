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

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.constants import AccountSource, Action, EntityType
from src.core.exceptions import AuthorizationError, BadRequestError, NotFoundError
from src.core.known_os import is_known_os
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
from src.schemas.server import ServerPrepareCallbackRequest
from src.services import audit_service, permissions, secrets_service
from src.utils.ids import os_version_id, server_account_id, server_disk_id

logger = logging.getLogger(__name__)

# Допустимый перекос между worker'ом и server_service'ом по NTP — 10 минут с
# каждой стороны. Используется для отбивания `rotated_at` из будущего в
# `record_ipmi_credentials_rotated`.
_ROTATED_AT_SKEW_SECONDS = 10 * 60


def _emit_dept_header_missing_soft(
    *,
    handler_path: str,
    identity: IdentityContext,
    target_id: str,
    target_type: str,
    extra: dict | None = None,
) -> None:
    """Soft-mode SIEM-trail для missing `X-Target-Department-Id` header'а.

    Эмитится только когда `_check_target_department` уже прошёл успешно
    (actor-vs-server совпало), но header не пришёл — `internal_require_dept_header`
    выключен. Отдельный action для SIEM-правила «worker без header'а».
    Эмиссия — однообразно во всех internal-handler'ах с двухуровневым
    dept-check'ом.
    """
    if get_settings().internal_require_dept_header:
        return
    details = {
        "path": handler_path,
        "soft_mode": True,
        "caller_type": identity.subject_type,
    }
    if extra:
        details.update(extra)
    audit_service.emit(
        "internal.dept_header_missing",
        actor_id=identity.user_id,
        target_id=target_id,
        target_type=target_type,
        status="warning",
        allowed=True,
        details=details,
    )


def _check_target_department(
    *,
    audit_action: str,
    target_id: str,
    target_type: str,
    server_department_id: str | None,
    header_department_id: str | None,
    actor_department_id: str | None,
    extra_details: dict | None = None,
    mask_as_not_found: bool = False,
    not_found_error_code: str | None = None,
    not_found_message: str | None = None,
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

    Идентификатор уезжает в `details` под единым ключом `target_id` —
    SIEM/правила различают тип через `target_type` рядом, а не по имени
    поля. Раньше у нас был `target_field` с дефолтом `server_id`, и
    account/controller caller'ы получали `acc_*`/`ipm_*` либо под чужой
    меткой, либо требовали явного override'а на каждом call-site.
    """
    strict = get_settings().internal_require_dept_header
    extra = dict(extra_details or {})
    extra.update({
        "server_department_id": server_department_id,
        "header_department_id": header_department_id,
        "actor_department_id": actor_department_id,
    })

    def _emit(reason: str, *, denied: bool) -> None:
        """Локальный shortcut для audit_service.emit с общим target/action.

        `denied=True` → status=denied/allowed=False (блок), иначе warning/allowed.
        """
        audit_service.emit(
            audit_action,
            target_id=target_id, target_type=target_type,
            status="denied" if denied else "warning",
            allowed=not denied,
            details={**extra, "reason": reason},
        )

    def _mask_or_403(message: str) -> None:
        """Поднимает 404 если caller просил `mask_as_not_found`, иначе 403.

        Маска применяется только к actor-mismatch'у: разница 403-vs-404 для
        cross-dept caller'а работает enumeration-oracle'ом. Header mismatch
        (worker bug-signal) оставляем 403 в strict-mode — это сигнал
        misconfig'а, не enumeration.
        """
        if mask_as_not_found:
            raise NotFoundError(
                error_code=not_found_error_code or "RESOURCE_NOT_FOUND",
                message=not_found_message or message,
                details={"target_id": target_id},
            )
        raise AuthorizationError(
            error_code="TARGET_DEPARTMENT_MISMATCH",
            message=message,
            details={"target_id": target_id},
        )

    # Actor-vs-server check: всегда блокирующий, не зависит от soft/strict.
    # Closes cross-department password/credentials leak в soft-mode, где
    # отсутствие/несовпадение `X-Target-Department-Id` header'а не отбивалось.
    # `actor_department_id is None` — platform-роли (account_admin/loging_admin),
    # которых platform_admin_guard должен был отбить раньше; defense-in-depth.
    if actor_department_id is None or actor_department_id != server_department_id:
        _emit("actor_department_mismatch", denied=True)
        _mask_or_403(
            "Caller department does not match the server's actual department",
        )

    if header_department_id is None:
        if strict:
            _emit("missing_target_department_header", denied=True)
            raise AuthorizationError(
                error_code="TARGET_DEPARTMENT_HEADER_REQUIRED",
                message=(
                    "X-Target-Department-Id header is required for internal "
                    "credential endpoints in strict mode"
                ),
                details={"target_id": target_id},
            )
        # Soft mode (`internal_require_dept_header=False`) — намеренное dev/test
        # ослабление защиты от stale-payload worker'а. В production
        # `internal_require_dept_header=True` (см. `core/config.py`), и сюда мы
        # сюда не доходим. denied=False / warning here — by design: actor-vs-server
        # check уже отбил cross-dept caller'а строкой выше, остаётся только
        # сигнализировать в audit о missing-header (это делает caller через
        # `_emit_dept_header_missing_soft` под отдельным action'ом
        # `internal.dept_header_missing`). Не использовать в prod.
        return

    if header_department_id != server_department_id:
        # Mismatch эмитим всегда, независимо от режима — это сигнал бага в
        # worker'е (stale payload) или, хуже, PAT'а, который щупает чужие
        # отделы. Header mismatch остаётся 403 даже при `mask_as_not_found` —
        # actor уже подтвердил, что он в правильном dept'е (проверка выше),
        # так что oracle'а тут нет, а 403 правильнее сигналит о misconfig'е.
        _emit("target_department_mismatch", denied=strict)
        if strict:
            raise AuthorizationError(
                error_code="TARGET_DEPARTMENT_MISMATCH",
                message=(
                    "X-Target-Department-Id does not match the server's "
                    "actual department"
                ),
                details={"target_id": target_id},
            )


# Тонкие обёртки над `_check_target_department` для случаев, где маска 404
# подаётся одним и тем же набором аргументов. Сжимают 5-строчный kwargs-блок
# на каждом call-site до одного вызова, error_code/message не разъезжаются.

def _check_target_department_for_server(
    *,
    audit_action: str,
    target_id: str,
    server_department_id: str | None,
    header_department_id: str | None,
    actor_department_id: str | None,
    extra_details: dict | None = None,
) -> None:
    _check_target_department(
        audit_action=audit_action,
        target_id=target_id,
        target_type="server",
        server_department_id=server_department_id,
        header_department_id=header_department_id,
        actor_department_id=actor_department_id,
        extra_details=extra_details,
        mask_as_not_found=True,
        not_found_error_code="SERVER_NOT_FOUND",
        not_found_message="Server not found",
    )


def _check_target_department_for_account(
    *,
    audit_action: str,
    target_id: str,
    server_department_id: str | None,
    header_department_id: str | None,
    actor_department_id: str | None,
    extra_details: dict | None = None,
) -> None:
    _check_target_department(
        audit_action=audit_action,
        target_id=target_id,
        target_type="server_account",
        server_department_id=server_department_id,
        header_department_id=header_department_id,
        actor_department_id=actor_department_id,
        extra_details=extra_details,
        mask_as_not_found=True,
        not_found_error_code="ACCOUNT_NOT_FOUND",
        not_found_message="Server account not found on this server",
    )


def _check_target_department_for_controller(
    *,
    audit_action: str,
    target_id: str,
    server_department_id: str | None,
    header_department_id: str | None,
    actor_department_id: str | None,
    extra_details: dict | None = None,
) -> None:
    _check_target_department(
        audit_action=audit_action,
        target_id=target_id,
        target_type="ipmi_controller",
        server_department_id=server_department_id,
        header_department_id=header_department_id,
        actor_department_id=actor_department_id,
        extra_details=extra_details,
        mask_as_not_found=True,
        not_found_error_code="NO_IPMI_CONTROLLER",
        not_found_message="IPMI controller not found",
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
            details={
                "reason": "permission_denied",
                "caller_type": identity.subject_type,
            },
        )
        raise
    server = await server_repo.get_by_id(db, server_id)
    # Dept-check фактически объединяем с existence-check: если сервера нет или
    # dept у caller'а другой — выдаём один и тот же SERVER_NOT_FOUND, не
    # выделяя 403 в отдельный сигнал. Иначе по разнице 403 vs 404 caller
    # угадывал бы, что сервер есть в чужом dept'е. Несуществующий server_id
    # обрабатываем ДО dept-check'а: SIEM иначе ловит фейковый
    # actor_department_mismatch, которого фактически нет.
    if server is None:
        audit_service.emit(
            "ipmi_controller.view_credentials",
            target_id=server_id, target_type="ipmi_controller",
            status="failure", allowed=True,
            details={"reason": "server_not_found"},
        )
        raise NotFoundError(error_code="SERVER_NOT_FOUND", message="Server not found")
    server_department_id = server.department_id
    _check_target_department(
        audit_action="ipmi_controller.view_credentials",
        target_id=server_id,
        target_type="ipmi_controller",
        server_department_id=server_department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
        mask_as_not_found=True,
        not_found_error_code="SERVER_NOT_FOUND",
        not_found_message="Server not found",
    )
    if target_department_id is None:
        _emit_dept_header_missing_soft(
            handler_path="internal.fetch_ipmi_credentials",
            identity=identity,
            target_id=server_id,
            target_type="ipmi_controller",
            extra={"server_id": server_id},
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
            error_code="NO_IPMI_CONTROLLER",
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
            "caller_type": identity.subject_type,
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
            details={
                "reason": "permission_denied",
                "server_id": server_id,
                "caller_type": identity.subject_type,
            },
        )
        raise
    # Dept-check РАНЬШЕ existence-проверок: иначе разная реакция
    # (404 ACCOUNT_NOT_FOUND vs 403 TARGET_DEPARTMENT_MISMATCH) сама по себе
    # сливает caller'у, привязан ли account_id к серверу чужого dept.
    # mask_as_not_found унифицирует все «не твой dept» сценарии в 404,
    # 403 остаётся только для permission_denied.
    # Исключение: если server_id фактически не существует — эмитим честный
    # `server_not_found` ДО dept-check'а. Иначе SIEM ловит фейковый
    # `actor_department_mismatch` на каждом тычке несуществующим server_id.
    # Клиенту всё равно уходит 404 ACCOUNT_NOT_FOUND/SERVER_NOT_FOUND, oracle
    # не открывается.
    server = await server_repo.get_by_id(db, server_id)
    if server is None:
        # Сервер отсутствует — реальная missing-entity это server, не account.
        # Эмитим под `target_type="server"`, account_id уезжает в details как
        # secondary. Иначе SIEM ловит server-level miss под account-меткой.
        audit_service.emit(
            "server_account.view_password",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "server_not_found",
                "server_id": server_id,
                "account_id": account_id,
            },
        )
        raise NotFoundError(
            error_code="ACCOUNT_NOT_FOUND",
            message="Server account not found on this server",
        )
    server_department_id = server.department_id
    _check_target_department_for_account(
        audit_action="server_account.view_password",
        target_id=account_id,
        server_department_id=server_department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
        extra_details={"server_id": server_id},
    )
    if target_department_id is None:
        _emit_dept_header_missing_soft(
            handler_path="internal.fetch_account_password",
            identity=identity,
            target_id=account_id,
            target_type="server_account",
            extra={"server_id": server_id},
        )
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
    if account.password_encrypted is None:
        # Discovered-аккаунт (`source=discovered`) приходит в БД без пароля —
        # инвентаризация ловит существующего OS-юзера и заводит карточку.
        # `account.provision` на такой записи отдаёт worker'у пустой пароль:
        # tasks/users.py трактует это как «useradd без chpasswd», что
        # соответствует ТЗ (SUCCEEDED + INFO, не failure). Для managed-аккаунтов
        # без пароля поведение прежнее — 404 ACCOUNT_HAS_NO_PASSWORD.
        if account.source == AccountSource.DISCOVERED.value:
            audit_service.emit(
                "server_account.view_password",
                target_id=account_id, target_type="server_account",
                status="success", allowed=True,
                details={
                    "server_id": server_id,
                    "login": account.login,
                    "discovered_no_password": True,
                    "caller_type": identity.subject_type,
                },
            )
            return {"login": account.login, "password": ""}
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
        details={
            "server_id": server_id,
            "login": account.login,
            "caller_type": identity.subject_type,
        },
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
            details={
                "reason": "permission_denied",
                "server_id": server_id,
                "caller_type": identity.subject_type,
            },
        )
        raise
    # Dept-check РАНЬШЕ existence-проверок: иначе 404 ACCOUNT_NOT_FOUND vs
    # 403 TARGET_DEPARTMENT_MISMATCH сами по себе сливают caller'у, чей
    # отдел держит account_id. Симметрично с `fetch_account_password`.
    # Исключение: если server отсутствует — эмитим честный `server_not_found`
    # ДО dept-check'а, чтобы SIEM не ловил фейковый actor_department_mismatch.
    server = await server_repo.get_by_id(db, server_id)
    if server is None:
        # Сервер отсутствует — missing-entity это server, не account.
        # Эмитим под `target_type="server"`, account_id уезжает в details.
        # Симметрично с `fetch_account_password`.
        audit_service.emit(
            "server_account.rotate_password",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "server_not_found",
                "server_id": server_id,
                "account_id": account_id,
            },
        )
        raise NotFoundError(
            error_code="ACCOUNT_NOT_FOUND",
            message="Server account not found on this server",
        )
    server_department_id = server.department_id
    _check_target_department_for_account(
        audit_action="server_account.rotate_password",
        target_id=account_id,
        server_department_id=server_department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
        extra_details={"server_id": server_id},
    )
    if target_department_id is None:
        _emit_dept_header_missing_soft(
            handler_path="internal.rotate_account_password",
            identity=identity,
            target_id=account_id,
            target_type="server_account",
            extra={"server_id": server_id},
        )
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
            "caller_type": identity.subject_type,
        },
    )
    return {"ok": True, "rotated_at": updated.password_rotated_at.isoformat()}


# ── Worker callbacks (write-direction internal API) ─────────────────────────


async def _resolve_or_create_os(
    db: AsyncSession,
    name: str,
    *,
    server_id: str | None = None,
    server_department_id: str | None = None,
    actor_subject_type: str | None = None,
) -> str | None:
    """Lookup OS-версии по name; INSERT при first-seen.

    Worker отдаёт `os_version` строкой из `/etc/os-release`. Имя обязано
    начинаться с одного из whitelist-префиксов (`KNOWN_OS_PREFIXES`) —
    иначе запись в каталоге НЕ создаётся, эмитим WARNING
    `os.unknown_observed` и возвращаем None. Inventory.sync продолжается
    без апдейта `server.os_version_id` (поле остаётся прежним).

    Известное имя → существующий flow: если в каталоге есть строка с тем
    же name — возвращаем её id; иначе INSERT с WARNING-аудитом
    `os_version.create` (есть авто-создание, оператор видит источник).
    """
    if not is_known_os(name):
        logger.warning(
            "unknown OS observed: %r from server %s — skip create",
            name, server_id,
        )
        audit_service.emit(
            "os.unknown_observed",
            target_id=server_id,
            target_type="server",
            status="warning",
            allowed=True,
            details={
                "reason": "os_not_in_whitelist",
                "os_name": name,
                "server_id": server_id,
                "server_department_id": server_department_id,
                "actor_subject_type": actor_subject_type,
            },
        )
        return None
    obj = await osv_repo.get_by_name(db, name)
    if obj is not None:
        return obj.id
    created = await osv_repo.create(db, {
        "id": os_version_id(),
        "name": name,
    })
    audit_service.emit(
        "os_version.create",
        target_id=created.id,
        target_type="os_version",
        status="warning",
        allowed=True,
        details={
            "reason": "auto_from_inventory",
            "name": name,
            "server_id": server_id,
            "server_department_id": server_department_id,
        },
    )
    return created.id


async def _upsert_disks(
    db: AsyncSession, server_id: str, items: list,
) -> int:
    """Bulk-upsert по (server_id, device_name) через PostgreSQL ON CONFLICT.

    Возвращает счётчик затронутых строк (INSERT + UPDATE). is_system-инвариант
    (ровно один system disk на server) гарантирует partial unique index в
    миграции, дубли отбиваются IntegrityError'ом на уровне БД.

    Каждый item летит как `INSERT ... ON CONFLICT DO UPDATE` — один SQL,
    атомарно, без savepoint'ов. Повторный callback от worker'а (HTTP-таймаут
    → retry) и параллельные callback'и обрабатываются БД, не приложением.
    """
    touched = 0
    for item in items:
        await disk_repo.upsert_by_device(db, {
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

    # Dept-check ВЫШЕ existence-проверки: разница 404 SERVER_NOT_FOUND vs
    # 403 TARGET_DEPARTMENT_MISMATCH сама сливает caller'у факт существования
    # сервера в чужом dept. `mask_as_not_found=True` унифицирует оба исхода
    # «не твой dept» / «не существует» в 404 SERVER_NOT_FOUND. Несуществующий
    # server_id обрабатываем ДО dept-check'а — иначе SIEM ловит фейковый
    # actor_department_mismatch (server_dept=None vs actor.dept != None).
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
    server_department_id = server.department_id
    _check_target_department_for_server(
        audit_action="server.inventory_received",
        target_id=server_id,
        server_department_id=server_department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
    )
    if target_department_id is None:
        _emit_dept_header_missing_soft(
            handler_path="internal.receive_inventory",
            identity=identity,
            target_id=server_id,
            target_type="server",
        )

    os_id_resolved = await _resolve_or_create_os(
        db,
        payload.os_version,
        server_id=server_id,
        server_department_id=server.department_id,
        actor_subject_type=identity.subject_type,
    )

    # Если OS не прошла whitelist (`_resolve_or_create_os` вернул None) —
    # `os_version_id` оставляем прежний, чтобы случайный мусор из inventory
    # не сносил легитимную привязку. Остальные hardware-поля апдейтим как
    # обычно: они не зависят от каталога os_versions.
    server_update: dict = {
        "hostname": payload.hostname,
        "cpu_brand": payload.cpu_brand,
        "cpu_model": payload.cpu_model,
        "cpu_cores": payload.cpu_cores,
        "cpu_threads": payload.cpu_threads,
        "cpu_frequency_ghz": payload.cpu_frequency_ghz,
        "os_last_synced_at": datetime.now(timezone.utc),
    }
    if os_id_resolved is not None:
        server_update["os_version_id"] = os_id_resolved
    await server_repo.update(db, server, server_update)
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
            "caller_type": identity.subject_type,
        },
    )
    return {
        "ok": True,
        "os_version_id": os_id_resolved,
        "disks_upserted": disks_count,
    }


# Атрибуты OS-пользователя, чьё расхождение бокса с БД считается дрейфом.
#
# `home_dir` намеренно НЕ в списке: PATCH home_dir не запускает fan-out
# (worker не двигает $HOME), а inventory эмитил бы drift на каждом скане —
# оператор получал бы шум, который ничем не закрыть. Симметрично `home_dir`
# исключён из `_OS_MANAGED_FIELDS` для fanout-payload в server_accounts.py.
_DRIFT_ATTRS = ("has_sudo", "unix_groups", "shell")


def _account_attr_drift(account, item) -> dict:
    """Сравнить атрибуты бокса (`item`) с записью в БД (`account`).

    БД — источник истины: возвращаем поля, по которым бокс разошёлся, в виде
    `{field: {"expected": <БД>, "found": <бокс>}}`. Группы сравниваем как
    множества — порядок и дубли в выводе getent не значимы. Пустой dict —
    расхождений нет.

    `home_dir` сюда не входит — см. комментарий к `_DRIFT_ATTRS`.
    """
    diff: dict = {}
    if bool(account.has_sudo) != bool(item.has_sudo):
        diff["has_sudo"] = {"expected": account.has_sudo, "found": item.has_sudo}
    if set(account.unix_groups or []) != set(item.unix_groups or []):
        diff["unix_groups"] = {
            "expected": list(account.unix_groups or []),
            "found": list(item.unix_groups or []),
        }
    if account.shell != item.shell:
        diff["shell"] = {"expected": account.shell, "found": item.shell}
    return diff


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

    Модель shared/M2M: один аккаунт держит одинаковые атрибуты на всех
    привязанных серверах, истина — БД. Инвентаризация фиксирует факт-состояние
    бокса, но НЕ перетирает поля аккаунта — расхождение поднимает WARNING-аудит
    `server_account.drift_detected`, чтобы оператор разобрался вручную.

    Reconcile (для инвентаризуемого сервера X):

      * найден на X, нет привязанного аккаунта → создать discovered-аккаунт
        (без пароля, `source=discovered`, `department_id` = dept сервера X),
        привязать к X; это drift-сигнал (на боксе живёт неуправляемый юзер);
      * есть и там, и в API → пометить связку present + свежий
        `last_inventory_at`; если атрибуты бокса разошлись с БД — drift, поля
        аккаунта НЕ трогаем;
      * привязан в API, но не найден на сервере → drift + пометить связку
        `present_on_server=False`, запись НЕ удаляем.
    """
    # Permission ВЫШЕ existence/dept: caller без grant'а получает 403 ровно
    # такой же, как same-dept caller без grant'а, и факт существования
    # сервера в чужом dept не утекает. Same-dept caller с grant'ом проходит
    # дальше к existence+dept-check'у — там 404 SERVER_NOT_FOUND под маской
    # `mask_as_not_found=True` одинаково покрывает «не твой dept» и «не
    # существует».
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
    server_department_id = server.department_id
    _check_target_department_for_server(
        audit_action="server_account.users_inventory_received",
        target_id=server_id,
        server_department_id=server_department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
    )
    if target_department_id is None:
        _emit_dept_header_missing_soft(
            handler_path="internal.receive_users_inventory",
            identity=identity,
            target_id=server_id,
            target_type="server",
        )

    # Снимок текущих связок сервера + login'ов, найденных на боксе.
    links = await account_repo.list_links_for_server(db, server_id)
    seen_logins = {item.login for item in payload.users}

    # Батчим выборки на N юзеров: один SELECT по login'ам, один по account_id'ам
    # их связок. Без батча reconcile делает 2·N запросов и проседает на больших
    # инвентаризациях.
    existing_by_login = await account_repo.list_accounts_on_server_by_logins(
        db, server_id, [item.login for item in payload.users],
    )
    links_by_account_id = await account_repo.list_links_for_server_by_account_ids(
        db, server_id, [acc.id for acc in existing_by_login.values()],
    )

    created = 0
    present = 0
    drifted = 0
    # Дрейф эмитим после commit'а — события best-effort, в транзакцию не входят.
    drift_emits: list[dict] = []
    # Аккумулируем account_id'ы под bulk-апдейты в конце цикла — один UPDATE
    # на N связок вместо N flush'ей в `mark_link_inventoried`. Гонка-recover
    # (когда `try_create_discovered` вернул None) идёт точечно — N там маленькое.
    present_account_ids: list[str] = []
    missing_account_ids: list[str] = []

    for item in payload.users:
        existing = existing_by_login.get(item.login)
        if existing is None:
            account_id = server_account_id()
            created_account = await account_repo.try_create_discovered(
                db,
                {
                    "id": account_id,
                    "department_id": server.department_id,
                    "login": item.login,
                    "password_encrypted": None,
                    "source": AccountSource.DISCOVERED.value,
                    "has_sudo": item.has_sudo,
                    "unix_groups": list(item.unix_groups),
                    "shell": item.shell,
                    "home_dir": item.home_dir,
                    # `is_active` берётся из дефолта колонки — поле пока
                    # зарезервировано, в выборках не фильтруется.
                    "created_by": identity.user_id,
                },
                server_id,
            )
            if created_account is None:
                # Гонка callback'ов: параллельный воркер уже завёл discovered со
                # связкой по `uq_server_login`. Подтягиваем существующую запись
                # и трактуем как present — без задвоения drift'а.
                existing = await account_repo.get_account_on_server_by_login(
                    db, server_id, item.login,
                )
                if existing is None:
                    # Конфликт пришёл не по `uq_server_login` — таких сценариев
                    # быть не должно, но защищаемся явно.
                    raise RuntimeError(
                        "try_create_discovered returned None but no existing "
                        f"link found for server={server_id} login={item.login}"
                    )
                # Race-ветка идёт точечно (link уже из чужой транзакции, в
                # `links_by_account_id` его нет). Случается редко — оставляем
                # per-call.
                link = await account_repo.get_link(db, existing.id, server_id)
                if link is not None:
                    await account_repo.mark_link_inventoried(
                        db, link, present=True,
                    )
                present += 1
                continue
            created += 1
            drifted += 1
            drift_emits.append({
                "login": item.login,
                "drift": "unknown_login",
            })
        else:
            # БД — истина: атрибуты аккаунта НЕ перетираем, только presence.
            diff = _account_attr_drift(existing, item)
            if existing.id in links_by_account_id:
                present_account_ids.append(existing.id)
            present += 1
            if diff:
                drifted += 1
                drift_emits.append({
                    "login": item.login,
                    "drift": "attributes",
                    "fields": sorted(diff.keys()),
                    "diff": diff,
                })

    # Привязанные в API, но не найденные на сервере — drift.
    # Emit идёт только при переходе present_on_server: True → False — это
    # «свежий» drift на конкретном скане. Если link уже был помечен False в
    # прошлый раз и юзера на боксе всё ещё нет, повторно эмитить нечего:
    # иначе каждый периодический скан плодил бы дубликат warning'ов в audit.
    # `is_new_drift` уезжает в details, чтобы потребители (loging_service /
    # отчёты) могли отличить первое срабатывание от прежнего состояния.
    for link in links:
        if link.login not in seen_logins:
            missing_account_ids.append(link.account_id)
            is_new_drift = link.present_on_server is True
            if is_new_drift:
                drifted += 1
                drift_emits.append({
                    "login": link.login,
                    "drift": "missing_on_box",
                    "is_new_drift": True,
                })

    # Bulk-flush presence: два statement'а вместо N flush'ей в цикле.
    if present_account_ids:
        await account_repo.mark_links_inventoried_bulk(
            db, server_id, present_account_ids, present=True,
        )
    if missing_account_ids:
        await account_repo.mark_links_inventoried_bulk(
            db, server_id, missing_account_ids, present=False,
        )

    await db.commit()

    for emit in drift_emits:
        details = {
            "server_id": server_id,
            "login": emit["login"],
            "drift": emit["drift"],
            "department_id": server.department_id,
        }
        if "fields" in emit:
            details["fields"] = emit["fields"]
        if "diff" in emit:
            details["expected"] = {f: v["expected"] for f, v in emit["diff"].items()}
            details["found"] = {f: v["found"] for f, v in emit["diff"].items()}
        if "is_new_drift" in emit:
            details["is_new_drift"] = emit["is_new_drift"]
        audit_service.emit(
            "server_account.drift_detected",
            target_id=server_id, target_type="server",
            status="warning", allowed=True,
            details=details,
        )

    audit_service.emit(
        "server_account.users_inventory_received",
        target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "created": created,
            "present": present,
            "drifted": drifted,
            "found": len(payload.users),
            "department_id": server.department_id,
            "caller_type": identity.subject_type,
        },
    )

    # Сводный result_summary — worker сохранит его в `tasks.result_payload`,
    # либо клиент построит drift-отчёт через `GET /servers/{id}/drift`.
    drift_items = [
        {
            "login": emit["login"],
            "drift_type": emit["drift"],
            "fields": emit.get("fields"),
        }
        for emit in drift_emits
    ]
    return {
        "ok": True,
        "created": created,
        "present": present,
        "drifted": drifted,
        "result_summary": {
            "total_users": len(payload.users),
            "created_discovered": created,
            "drifts": drift_items,
        },
    }


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
    # Permission ВЫШЕ existence/dept: caller без grant'а получает 403
    # permission_denied вне зависимости от того, в каком dept'е сервер —
    # факт привязки account_id к серверу чужого dept не утекает. Само
    # cross-dept смешение остаётся скрытым за 404 ACCOUNT_NOT_FOUND ниже
    # (`mask_as_not_found=True` в `_check_target_department_for_account`).
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
    server = await server_repo.get_by_id(db, server_id)
    if server is None:
        audit_service.emit(
            "server_account.provision_status",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "server_not_found", "server_id": server_id},
        )
        raise NotFoundError(
            error_code="ACCOUNT_NOT_FOUND",
            message="Server account not found on this server",
        )
    server_department_id = server.department_id
    _check_target_department_for_account(
        audit_action="server_account.provision_status",
        target_id=account_id,
        server_department_id=server_department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
        extra_details={"server_id": server_id},
    )
    if target_department_id is None:
        _emit_dept_header_missing_soft(
            handler_path="internal.record_provision_status",
            identity=identity,
            target_id=account_id,
            target_type="server_account",
            extra={"server_id": server_id},
        )

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

    await account_repo.set_link_presence(db, link, present=payload.present)
    # Callback от worker'а — единственная точка подтверждения, что credentials,
    # сгенерированные на dispatch'е и сохранённые в server_accounts.password_encrypted /
    # ssh_private_key_encrypted, реально доехали до боксу. Снимаем pending_apply
    # на любой успешный provision/update (`present=True`); deprovision (`present=False`)
    # тоже завершает цикл — на боксе пользователя больше нет, БД-creds логически
    # выровнены с реальностью. Не трогаем, если у row'а флаг и так False —
    # бесплатно по UPDATE, но логически чище.
    if account.credentials_pending_apply:
        account.credentials_pending_apply = False
        await db.flush()
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
            "caller_type": identity.subject_type,
        },
    )
    return {"ok": True, "present_on_server": payload.present}


async def record_server_prepared(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    payload: ServerPrepareCallbackRequest,
    target_department_id: str | None = None,
) -> dict:
    """Зафиксировать завершение бутстрапа управления сервером (callback worker'а).

    Право: `(server, *, prepare_callback)` — узкий грант worker_bot'а.

    Помечает сервер подготовленным: `is_managed=True`, `prepared_at=now`,
    `management_user=<имя>`. Идемпотентно: повторный callback просто
    переписывает те же поля. Аудит — CRITICAL.
    """
    # Dept-check ВЫШЕ permission: cross-dept caller без grant'а иначе ловит 403
    # permission_denied, что enum-oracle'ит факт существования сервера в чужом dept.
    # Несуществующий server_id обрабатываем ДО dept-check'а — иначе SIEM ловит
    # фейковый actor_department_mismatch на тычках в air.
    server = await server_repo.get_by_id(db, server_id)
    if server is None:
        audit_service.emit(
            "server.prepared",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "server_not_found"},
        )
        raise NotFoundError(
            error_code="SERVER_NOT_FOUND", message="Server not found",
        )
    server_department_id = server.department_id
    _check_target_department_for_server(
        audit_action="server.prepared",
        target_id=server_id,
        server_department_id=server_department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
    )
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.PREPARE_CALLBACK,
        )
    except AuthorizationError:
        audit_service.emit(
            "server.prepared",
            target_id=server_id, target_type="server",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise
    if target_department_id is None:
        _emit_dept_header_missing_soft(
            handler_path="internal.record_server_prepared",
            identity=identity,
            target_id=server_id,
            target_type="server",
        )

    prepared_at = datetime.now(timezone.utc)
    await server_repo.update(db, server, {
        "is_managed": True,
        "management_user": payload.management_user,
        "prepared_at": prepared_at,
    })
    await db.commit()

    audit_service.emit(
        "server.prepared",
        target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "management_user": payload.management_user,
            "prepared_at": prepared_at.isoformat(),
            "department_id": server.department_id,
            "caller_type": identity.subject_type,
        },
    )
    return {
        "ok": True,
        "is_managed": True,
        "prepared_at": prepared_at.isoformat(),
    }


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
    # Dept-check ВЫШЕ permission: cross-dept caller без grant'а иначе ловит 403
    # permission_denied, что enum-oracle'ит факт привязки controller_id к
    # серверу чужого dept. Но если controller_id фактически не существует —
    # эмитим честный `controller_not_found` ДО dept-check'а, иначе SIEM ловит
    # фейковый `actor_department_mismatch` на каждом тычке несуществующим id.
    # Клиенту всё равно уходит 404 NO_IPMI_CONTROLLER, oracle не открывается.
    ctrl = await ipmi_repo.get_by_id(db, controller_id)
    if ctrl is None:
        audit_service.emit(
            "ipmi_controller.credentials_rotated_callback",
            target_id=controller_id, target_type="ipmi_controller",
            status="failure", allowed=True,
            details={"reason": "controller_not_found"},
        )
        raise NotFoundError(
            error_code="NO_IPMI_CONTROLLER",
            message="IPMI controller not found",
        )
    server = await server_repo.get_by_id(db, ctrl.server_id)
    if server is None:
        # Orphaned controller: controller-row жив, но server_id ссылается на
        # удалённый сервер. Без явной ветки _check_target_department сравнил бы
        # actor.department_id с None и эмитнул ложный `actor_department_mismatch`,
        # будто caller лез в чужой dept — на деле dept-конфликта нет, просто
        # сервер пропал. Пишем честный `orphaned_ipmi_controller` и отдаём 404,
        # пусть оператор подчистит запись.
        audit_service.emit(
            "ipmi_controller.credentials_rotated_callback",
            target_id=controller_id, target_type="ipmi_controller",
            status="failure", allowed=True,
            details={
                "reason": "orphaned_ipmi_controller",
                "server_id": ctrl.server_id,
            },
        )
        raise NotFoundError(
            error_code="NO_IPMI_CONTROLLER",
            message="IPMI controller not found",
        )
    server_dept = server.department_id
    _check_target_department_for_controller(
        audit_action="ipmi_controller.credentials_rotated_callback",
        target_id=controller_id,
        server_department_id=server_dept,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
        extra_details={"server_id": ctrl.server_id},
    )
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
    if target_department_id is None:
        _emit_dept_header_missing_soft(
            handler_path="internal.record_ipmi_credentials_rotated",
            identity=identity,
            target_id=controller_id,
            target_type="ipmi_controller",
            extra={"server_id": ctrl.server_id},
        )

    rotated_at = payload.rotated_at
    if rotated_at.tzinfo is None:
        rotated_at = rotated_at.replace(tzinfo=timezone.utc)
    verified_at = payload.verified_at
    if verified_at.tzinfo is None:
        verified_at = verified_at.replace(tzinfo=timezone.utc)

    # `rotated_at` ложится в `password_rotated_at`, по которому UI сортирует
    # карточки. Worker с убежавшими часами (NTP-drift) или умышленно подменённый
    # PAT мог бы прислать «ротация на год вперёд» и навсегда вытолкнуть запись
    # в топ списка. Допускаем небольшой перекос (±10 минут) — пишет ошибки
    # без оператора при стандартном NTP-skew между worker'ом и server_service'ом.
    now = datetime.now(timezone.utc)
    rotated_drift = (rotated_at - now).total_seconds()
    if rotated_drift > _ROTATED_AT_SKEW_SECONDS:
        audit_service.emit(
            "ipmi_controller.credentials_rotated_callback",
            target_id=controller_id, target_type="ipmi_controller",
            status="failure", allowed=True,
            details={
                "reason": "rotated_at_in_future",
                "server_id": ctrl.server_id,
                "rotated_at": rotated_at.isoformat(),
                "rotated_drift_seconds": round(rotated_drift, 3),
                "max_skew_seconds": _ROTATED_AT_SKEW_SECONDS,
            },
        )
        raise BadRequestError(
            error_code="ROTATED_AT_IN_FUTURE",
            message=(
                f"rotated_at must be within {_ROTATED_AT_SKEW_SECONDS}s of now "
                "(NTP-skew tolerated)"
            ),
            details={
                "rotated_at": rotated_at.isoformat(),
                "max_skew_seconds": _ROTATED_AT_SKEW_SECONDS,
            },
        )
    # Симметрично — нижняя граница. Worker с очень отставшими часами либо
    # подменённый PAT мог бы прислать `rotated_at` из глубокого прошлого и
    # утопить новую запись в конце сортировки UI. Допускаем тот же ±10-мин
    # NTP-skew, что и для верхней границы.
    if rotated_drift < -_ROTATED_AT_SKEW_SECONDS:
        audit_service.emit(
            "ipmi_controller.credentials_rotated_callback",
            target_id=controller_id, target_type="ipmi_controller",
            status="failure", allowed=True,
            details={
                "reason": "rotated_at_too_old",
                "server_id": ctrl.server_id,
                "rotated_at": rotated_at.isoformat(),
                "rotated_drift_seconds": round(rotated_drift, 3),
                "max_skew_seconds": _ROTATED_AT_SKEW_SECONDS,
            },
        )
        raise BadRequestError(
            error_code="ROTATED_AT_TOO_OLD",
            message=(
                f"rotated_at must be within {_ROTATED_AT_SKEW_SECONDS}s of now "
                "(NTP-skew tolerated)"
            ),
            details={
                "rotated_at": rotated_at.isoformat(),
                "max_skew_seconds": _ROTATED_AT_SKEW_SECONDS,
            },
        )

    # verify-then-storage: пишем ciphertext только если worker подтвердил
    # удачный BMC test-call в окне `IPMI_VERIFY_MAX_AGE_SECONDS`. Старый /
    # отсутствующий verify => 400. Иначе сохранили бы пароль, которым
    # нельзя залогиниться, и out-of-band доступ потерян до ручной починки.
    max_age = get_settings().ipmi_verify_max_age_seconds
    verify_age = (now - verified_at).total_seconds()
    if abs(verify_age) > max_age:
        # Future-dated verified_at — отдельная ветка: worker'у не доверяем,
        # если он шлёт «доказательство из будущего» (часы поплыли / умышленный
        # spoof). Reason и message отличаются от обычного stale-случая, чтобы
        # оператор в loging_service сразу видел причину.
        is_future = verify_age < 0
        reason = "verify_in_future" if is_future else "verify_stale"
        if is_future:
            message = (
                f"BMC verified_at is in the future: must be within {max_age}s of now"
            )
        else:
            message = (
                "Stale or missing BMC verify proof: verified_at must be "
                f"within {max_age}s of now"
            )
        audit_service.emit(
            "ipmi_controller.credentials_rotated_callback",
            target_id=controller_id, target_type="ipmi_controller",
            status="failure", allowed=True,
            details={
                "reason": reason,
                "server_id": ctrl.server_id,
                "verified_at": verified_at.isoformat(),
                "verify_age_seconds": round(verify_age, 3),
                "max_age_seconds": max_age,
            },
        )
        raise BadRequestError(
            error_code="BMC_VERIFY_REQUIRED",
            message=message,
            details={
                "verified_at": verified_at.isoformat(),
                "max_age_seconds": max_age,
            },
        )

    encrypted = secrets_service.encrypt(
        payload.new_password,
        aad=secrets_service.aad_for_ipmi_credential(ctrl.id),
    )
    await ipmi_repo.update(db, ctrl, {
        "password_encrypted": encrypted,
        "password_rotated_at": rotated_at,
        # Callback worker'а — единственная точка, где БД-ciphertext
        # подтверждён применением на BMC. Снимаем pending_apply, чтобы
        # следующий dispatch не уходил в force_replace-режим.
        "credentials_pending_apply": False,
    })
    await db.commit()

    audit_service.emit(
        "ipmi_controller.credentials_rotated_callback",
        target_id=controller_id, target_type="ipmi_controller",
        status="success", allowed=True,
        details={
            "server_id": ctrl.server_id,
            "rotated_at": rotated_at.isoformat(),
            "verified_at": verified_at.isoformat(),
            "department_id": server_dept,
            "caller_type": identity.subject_type,
        },
    )
    return {"ok": True, "rotated_at": rotated_at.isoformat()}


