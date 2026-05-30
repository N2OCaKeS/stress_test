"""Use cases для ipmi_controllers — CRUD + rotate_credentials.

Связь servers↔ipmi_controllers 1:1 (UNIQUE на server_id). Поэтому все
эндпоинты идут через {server_id}, без отдельного controller_id в URL —
controller всегда однозначно резолвится через server.

Карточка контроллера доступна по `view`. Если вызывающий держит
`view_credentials`, тот же GET доносит расшифрованный BMC-пароль в base64 —
отдельной reveal-ручки нет.

Department-isolation скрывает cross-dept-сервер за 404 (`SERVER_NOT_FOUND`)
и при наличии контроллера — `IPMI_NOT_FOUND` для контроллера. Это
симметрично с `services/server_account.py` и `services/server.py`.
"""

import base64
import logging
import secrets

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType
from src.core.exceptions import (
    ConflictError,
    NotFoundError,
)
from src.models import IpmiController
from src.repositories import ipmi_controller as repo
from src.schemas.identity import IdentityContext
from src.schemas.ipmi_controller import IpmiControllerCreate, IpmiControllerUpdate
from src.services import audit_service, permissions, secrets_service
from src.services.audit_helpers import emit_denied_on_authz_error
from src.services.server import load_visible_server
from src.utils.ids import ipmi_controller_id as new_id

logger = logging.getLogger(__name__)


def _generate_password() -> str:
    """Дефолтный генератор IPMI-паролей."""
    return secrets.token_urlsafe(32)


def emit_create_success(controller: IpmiController, department_id: str) -> None:
    """Аудит-эмит успешной регистрации BMC.

    Вынесено отдельным хелпером, потому что create контроллера живёт в двух
    точках: standalone POST /servers/{id}/ipmi (этот сервис) и вложенный
    блок `ipmi` в POST /servers (`services/server.py::create_server`). Обе
    точки должны бить ровно одинаковыми деталями — каталог `AUDIT_EVENTS.md`
    описывает один формат.
    """
    audit_service.emit(
        "ipmi_controller.create",
        target_id=controller.id,
        target_type="ipmi_controller",
        status="success",
        allowed=True,
        details={
            "server_id": controller.server_id,
            "kind": controller.kind,
            "endpoint_url": controller.endpoint_url,
            "department_id": department_id,
        },
    )


async def create_controller(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    payload: IpmiControllerCreate,
) -> IpmiController:
    """INSERT IPMI-контроллера для существующего сервера.

    Порядок проверок:
      1. CREATE permission.
      2. Server существует + dept совпадает (иначе 404).
      3. Шифруем password.
      4. INSERT + commit. UNIQUE(server_id) → 409 IPMI_DUPLICATE.
    """
    with emit_denied_on_authz_error(
        "ipmi_controller.create",
        target_type="ipmi_controller",
        extra_details={"server_id": server_id},
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.IPMI_CONTROLLER, Action.CREATE
        )

    try:
        server = await load_visible_server(db, identity, server_id)
    except NotFoundError:
        audit_service.emit(
            "ipmi_controller.create",
            target_type="ipmi_controller",
            status="denied", allowed=False,
            details={"reason": "server_not_found_or_cross_dept", "server_id": server_id},
        )
        raise

    controller_id = new_id()
    encrypted = secrets_service.encrypt(
        payload.password,
        aad=secrets_service.aad_for_ipmi_credential(controller_id),
    )
    data = {
        "id": controller_id,
        "server_id": server_id,
        "kind": payload.kind.value,
        "endpoint_url": payload.endpoint_url,
        "username": payload.username,
        "password_encrypted": encrypted,
    }
    try:
        obj = await repo.create(db, data)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("IntegrityError на создании IPMI-контроллера: %s", type(exc.orig).__name__)
        audit_service.emit(
            "ipmi_controller.create",
            target_type="ipmi_controller",
            status="failure", allowed=True,
            details={"reason": "duplicate", "server_id": server_id},
        )
        raise ConflictError(
            error_code="IPMI_DUPLICATE",
            message="IPMI controller for this server already exists",
            details={"hint": "уникальный ключ server_id (1:1 с сервером)"},
        ) from exc
    await db.refresh(obj)
    emit_create_success(obj, server.department_id)
    return obj


async def get_controller(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
) -> tuple[IpmiController, str | None]:
    """SELECT IPMI-контроллера по server_id + visibility-check.

    Карточка доступна по `view` или `view_credentials`: держателю
    `view_credentials` голый `view` не нужен (так worker_bot читает креды).
    Второй элемент кортежа — base64(plaintext BMC-пароля) при наличии
    `view_credentials`, иначе `None`. Раскрытие пишет CRITICAL-аудит
    `ipmi_controller.credentials_revealed`.

    Возвращает 404 IPMI_NOT_FOUND если сервер видим, но контроллер не зарегистрирован.
    Cross-dept или non-existent server → 404 SERVER_NOT_FOUND.
    """
    has_credentials_action = await permissions.has_action(
        db, identity, EntityType.IPMI_CONTROLLER, Action.VIEW_CREDENTIALS
    )
    with emit_denied_on_authz_error(
        "ipmi_controller.view",
        target_id=server_id,
        target_type="ipmi_controller",
        extra_details={"server_id": server_id},
        identity=identity,
    ):
        if not has_credentials_action:
            await permissions.require_action(
                db, identity, EntityType.IPMI_CONTROLLER, Action.VIEW
            )
    try:
        server = await load_visible_server(db, identity, server_id)
    except NotFoundError:
        audit_service.emit(
            "ipmi_controller.view",
            target_id=server_id, target_type="ipmi_controller",
            status="denied", allowed=False,
            details={"reason": "server_not_found_or_cross_dept", "server_id": server_id},
        )
        raise
    obj = await repo.get_by_server_id(db, server_id)
    if obj is None:
        audit_service.emit(
            "ipmi_controller.view",
            target_id=server_id, target_type="ipmi_controller",
            status="denied", allowed=False,
            details={"reason": "not_registered", "server_id": server_id},
        )
        raise NotFoundError(
            error_code="IPMI_NOT_FOUND",
            message="No IPMI controller is registered for this server",
        )

    audit_service.emit(
        "ipmi_controller.view",
        target_id=obj.id, target_type="ipmi_controller",
        status="success", allowed=True,
        details={
            "server_id": server_id,
            "department_id": server.department_id,
            "with_credentials": has_credentials_action,
        },
    )

    if not has_credentials_action:
        return obj, None

    return obj, _reveal_controller_password(obj, server.department_id)


async def list_controllers_cursor(
    db: AsyncSession,
    identity: IdentityContext,
    *,
    limit: int,
    after: str | None,
) -> tuple[list[IpmiController], str | None, bool]:
    """Keyset-страница IPMI-контроллеров видимых caller'у. `(items, next_cursor, has_more)`."""
    from src.utils.cursor import (
        decode_cursor,
        encode_cursor,
        normalize_limit,
        parse_cursor_datetime,
    )

    with emit_denied_on_authz_error(
        "ipmi_controller.list",
        target_type="ipmi_controller",
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.IPMI_CONTROLLER, Action.VIEW
        )
    if identity.department_id is None:
        return [], None, False
    page_size = normalize_limit(limit)
    after_created_at = None
    after_id = None
    if after:
        cur = decode_cursor(after)
        after_created_at = parse_cursor_datetime(cur.sort_value)
        after_id = cur.row_id
    dept_filter = [identity.department_id]
    rows = await repo.list_in_departments_after(
        db,
        dept_filter,
        limit=page_size + 1,
        after_created_at=after_created_at,
        after_id=after_id,
    )
    has_more = len(rows) > page_size
    items = rows[:page_size]
    next_cursor = (
        encode_cursor(items[-1].created_at, items[-1].id) if has_more and items else None
    )
    return items, next_cursor, has_more


async def list_controllers(
    db: AsyncSession,
    identity: IdentityContext,
    limit: int,
    offset: int,
) -> tuple[list[IpmiController], int]:
    """List + count IPMI-контроллеров, видимых caller'у (по dept-фильтру).

    Department-фильтр строится тут, не в endpoint'е — caller без
    department_id получает пустой результат сразу. Platform-роли отрезаны
    guard'ом ещё в middleware.
    """
    with emit_denied_on_authz_error(
        "ipmi_controller.list",
        target_type="ipmi_controller",
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.IPMI_CONTROLLER, Action.VIEW
        )
    if identity.department_id is None:
        return [], 0
    dept_filter = [identity.department_id]
    items = await repo.list_in_departments(db, dept_filter, limit=limit, offset=offset)
    total = await repo.count_in_departments(db, dept_filter)
    return items, total


async def update_controller(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    payload: IpmiControllerUpdate,
) -> IpmiController:
    """PATCH-апдейт IPMI-контроллера. Пустой диф → возврат без UPDATE.

    Смена пароля через PATCH НЕ предусмотрена — только через
    `/credentials/rotate` (отдельный CRITICAL audit-event).
    """
    with emit_denied_on_authz_error(
        "ipmi_controller.update",
        target_id=server_id,
        target_type="ipmi_controller",
        extra_details={"server_id": server_id},
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.IPMI_CONTROLLER, Action.UPDATE
        )
    try:
        server = await load_visible_server(db, identity, server_id)
    except NotFoundError:
        audit_service.emit(
            "ipmi_controller.update",
            target_id=server_id, target_type="ipmi_controller",
            status="denied", allowed=False,
            details={"reason": "server_not_found_or_cross_dept", "server_id": server_id},
        )
        raise
    obj = await repo.get_by_server_id(db, server_id)
    if obj is None:
        audit_service.emit(
            "ipmi_controller.update",
            target_id=server_id, target_type="ipmi_controller",
            status="denied", allowed=False,
            details={"reason": "not_registered", "server_id": server_id},
        )
        raise NotFoundError(
            error_code="IPMI_NOT_FOUND",
            message="No IPMI controller is registered for this server",
        )

    changes = payload.model_dump(exclude_unset=True, mode="json")
    if not changes:
        return obj
    # `kind` приходит как enum-value (string благодаря StrEnum + mode="json").
    try:
        await repo.update(db, obj, changes)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("IntegrityError на обновлении IPMI %s: %s", server_id, type(exc.orig).__name__)
        audit_service.emit(
            "ipmi_controller.update",
            target_id=obj.id, target_type="ipmi_controller",
            status="failure", allowed=True,
            details={"reason": "duplicate", "fields": list(changes.keys())},
        )
        raise ConflictError(
            error_code="IPMI_DUPLICATE",
            message="Update collides with an existing IPMI record",
        ) from exc
    await db.refresh(obj)
    audit_details = {
        "fields": list(changes.keys()),
        "server_id": obj.server_id,
        "department_id": server.department_id,
    }
    audit_service.emit(
        "ipmi_controller.update",
        target_id=obj.id, target_type="ipmi_controller",
        status="success", allowed=True,
        details=audit_details,
    )
    return obj


async def delete_controller(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
) -> None:
    """Hard-delete контроллера. После — power-операции на сервере будут
    отбиваться 404 NO_IPMI_CONTROLLER (см. `_dispatch_power` в endpoints/ipmi.py).

    Audit `ipmi_controller.delete` с CRITICAL severity — это deliberately
    destructive: теряются учётки BMC, новая запись потребует знание актуального
    пароля iDRAC/iLO.
    """
    with emit_denied_on_authz_error(
        "ipmi_controller.delete",
        target_id=server_id,
        target_type="ipmi_controller",
        extra_details={"server_id": server_id},
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.IPMI_CONTROLLER, Action.DELETE
        )
    try:
        server = await load_visible_server(db, identity, server_id)
    except NotFoundError:
        audit_service.emit(
            "ipmi_controller.delete",
            target_id=server_id, target_type="ipmi_controller",
            status="denied", allowed=False,
            details={"reason": "server_not_found_or_cross_dept", "server_id": server_id},
        )
        raise
    obj = await repo.get_by_server_id(db, server_id)
    if obj is None:
        audit_service.emit(
            "ipmi_controller.delete",
            target_id=server_id, target_type="ipmi_controller",
            status="denied", allowed=False,
            details={"reason": "not_registered", "server_id": server_id},
        )
        raise NotFoundError(
            error_code="IPMI_NOT_FOUND",
            message="No IPMI controller is registered for this server",
        )
    controller_id = obj.id
    kind = obj.kind
    await repo.delete(db, obj)
    await db.commit()
    audit_service.emit(
        "ipmi_controller.delete",
        target_id=controller_id, target_type="ipmi_controller",
        status="success", allowed=True,
        details={
            "server_id": server_id,
            "kind": kind,
            "department_id": server.department_id,
        },
    )


async def rotate_credentials(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    new_password: str | None = None,
) -> IpmiController:
    """Ротация IPMI-пароля.

    Если `new_password=None` — генерируем серверной стороной
    (`secrets.token_urlsafe(32)`); полезно для случая «компрометация без
    apply'я на BMC». Worker-side callback path передаёт явный password
    после успешного применения через Redfish/IPMI-tool.

    Plaintext клиенту НЕ возвращается ни в одном случае.
    """
    with emit_denied_on_authz_error(
        "ipmi_controller.rotate_credentials",
        target_id=server_id,
        target_type="ipmi_controller",
        extra_details={"server_id": server_id},
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.IPMI_CONTROLLER, Action.ROTATE_CREDENTIALS
        )
    try:
        server = await load_visible_server(db, identity, server_id)
    except NotFoundError:
        audit_service.emit(
            "ipmi_controller.rotate_credentials",
            target_id=server_id, target_type="ipmi_controller",
            status="denied", allowed=False,
            details={"reason": "server_not_found_or_cross_dept", "server_id": server_id},
        )
        raise
    obj = await repo.get_by_server_id(db, server_id)
    if obj is None:
        audit_service.emit(
            "ipmi_controller.rotate_credentials",
            target_id=server_id, target_type="ipmi_controller",
            status="denied", allowed=False,
            details={"reason": "not_registered", "server_id": server_id},
        )
        raise NotFoundError(
            error_code="IPMI_NOT_FOUND",
            message="No IPMI controller is registered for this server",
        )
    plaintext = new_password if new_password is not None else _generate_password()
    encrypted = secrets_service.encrypt(
        plaintext,
        aad=secrets_service.aad_for_ipmi_credential(obj.id),
    )
    updated = await repo.update_password(db, obj, encrypted)
    await db.commit()
    await db.refresh(updated)
    audit_service.emit(
        "ipmi_controller.rotate_credentials",
        target_id=updated.id, target_type="ipmi_controller",
        status="success", allowed=True,
        details={
            "server_id": updated.server_id,
            "department_id": server.department_id,
            "reason": "worker_callback" if new_password is not None else "user_initiated",
            "rotated_at": (
                updated.password_rotated_at.isoformat()
                if updated.password_rotated_at else None
            ),
        },
    )
    return updated


def _reveal_controller_password(
    obj: IpmiController,
    department_id: str | None,
) -> str | None:
    """Расшифровать BMC-пароль в base64 и записать аудит раскрытия.

    Вызывается из `get_controller` только после успешной проверки
    `view_credentials`, поэтому permission тут уже не проверяется. Возвращает
    `None`, если у контроллера нет сохранённого пароля. Раскрытие пишет
    CRITICAL-аудит `ipmi_controller.credentials_revealed`; сломанный
    ciphertext поднимает `DECRYPT_FAILED` (500) + failure-аудит.
    """
    audit_action = "ipmi_controller.credentials_revealed"

    if obj.password_encrypted is None:
        audit_service.emit(
            audit_action,
            target_id=obj.id, target_type="ipmi_controller",
            status="failure", allowed=True,
            details={
                "reason": "no_password_stored",
                "server_id": obj.server_id,
                "department_id": department_id,
            },
        )
        return None

    try:
        plain = secrets_service.decrypt(
            obj.password_encrypted,
            aad=secrets_service.aad_for_ipmi_credential(obj.id),
        )
    except Exception:
        audit_service.emit(
            audit_action,
            target_id=obj.id, target_type="ipmi_controller",
            status="failure", allowed=True,
            details={
                "reason": "decrypt_failed",
                "server_id": obj.server_id,
                "department_id": department_id,
            },
        )
        raise

    audit_service.emit(
        audit_action,
        target_id=obj.id, target_type="ipmi_controller",
        status="success", allowed=True,
        details={
            "server_id": obj.server_id,
            "username": obj.username,
            "department_id": department_id,
        },
    )
    return base64.b64encode(plain.encode("utf-8")).decode("ascii")
