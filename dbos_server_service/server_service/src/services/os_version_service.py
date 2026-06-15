"""Use cases для OS-версий — глобальный каталог.

Read (list + get по id + get по имени) публичный: без identity, без
проверки прав и без аудита — каталог ОС открыт на чтение всем.
Запись (create/update/delete) остаётся под матрицей прав. Удаление
версии, на которую ссылается хоть один сервер (`servers.os_version_id`),
отбивается IntegrityError от FK ondelete=RESTRICT → 409 OS_VERSION_IN_USE.
"""

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
)
from src.models import OsVersion
from src.repositories import os_version as repo
from src.schemas.identity import IdentityContext
from src.schemas.os_version import OsVersionCreate, OsVersionUpdate
from src.services import audit_service, permissions
from src.utils.ids import os_version_id as new_id

logger = logging.getLogger(__name__)


async def create_os_version(
    db: AsyncSession,
    identity: IdentityContext,
    payload: OsVersionCreate,
) -> OsVersion:
    """INSERT новой OS-версии. UNIQUE(name) → 409 OS_VERSION_DUPLICATE."""
    try:
        await permissions.require_action(db, identity, EntityType.OS_VERSION, Action.CREATE)
    except AuthorizationError:
        audit_service.emit(
            "os_version.create",
            target_type="os_version",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise

    data = payload.model_dump(mode="json")
    data["id"] = new_id()
    try:
        obj = await repo.create(db, data)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("IntegrityError на создании os_version: %s", type(exc.orig).__name__)
        audit_service.emit(
            "os_version.create",
            target_type="os_version",
            status="failure", allowed=True,
            details={"reason": "duplicate", "name": payload.name},
        )
        raise ConflictError(
            error_code="OS_VERSION_DUPLICATE",
            message="OS version with this name already exists",
            details={"hint": "уникальное поле — name"},
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "os_version.create",
        target_id=obj.id, target_type="os_version",
        status="success", allowed=True,
        details={"name": obj.name},
    )
    return obj


async def get_os_version(
    db: AsyncSession,
    os_version_id: str,
) -> OsVersion:
    """SELECT OS-версии по PK. Публичный read — без auth и без аудита."""
    obj = await repo.get_by_id(db, os_version_id)
    if obj is None:
        raise NotFoundError(
            error_code="OS_VERSION_NOT_FOUND",
            message="OS version not found",
        )
    return obj


async def get_os_version_by_name(
    db: AsyncSession,
    name: str,
) -> OsVersion:
    """SELECT OS-версии по UNIQUE name. Публичный read — без auth и без аудита."""
    obj = await repo.get_by_name(db, name)
    if obj is None:
        raise NotFoundError(
            error_code="OS_VERSION_NOT_FOUND",
            message="OS version not found",
        )
    return obj


async def list_os_versions_cursor(
    db: AsyncSession,
    *,
    limit: int,
    after: str | None,
) -> tuple[list[OsVersion], str | None, bool]:
    """Keyset-страница каталога OS-версий. Возвращает `(items, next_cursor, has_more)`.

    Каталог глобальный, без dept-фильтра. `limit + 1` row-fetch для
    has_more-детекта без отдельного COUNT'а.
    """
    from src.utils.cursor import (
        decode_cursor,
        encode_cursor,
        normalize_limit,
        parse_cursor_datetime,
    )

    page_size = normalize_limit(limit)
    after_discovered_at = None
    after_id = None
    if after:
        cur = decode_cursor(after)
        after_discovered_at = parse_cursor_datetime(cur.sort_value)
        after_id = cur.row_id
    rows = await repo.list_all_after(
        db,
        limit=page_size + 1,
        after_discovered_at=after_discovered_at,
        after_id=after_id,
    )
    has_more = len(rows) > page_size
    items = rows[:page_size]
    next_cursor = (
        encode_cursor(items[-1].discovered_at, items[-1].id) if has_more and items else None
    )
    return items, next_cursor, has_more


async def list_os_versions(
    db: AsyncSession,
    limit: int,
    offset: int,
) -> tuple[list[OsVersion], int]:
    """List + count полного каталога. Публичный read — без auth и без аудита."""
    items = await repo.list_all(db, limit=limit, offset=offset)
    total = await repo.count_all(db)
    return items, total


async def update_os_version(
    db: AsyncSession,
    identity: IdentityContext,
    os_version_id: str,
    payload: OsVersionUpdate,
) -> OsVersion:
    """PATCH-обновление. Пустой диф → возврат без UPDATE."""
    try:
        await permissions.require_action(db, identity, EntityType.OS_VERSION, Action.UPDATE)
    except AuthorizationError:
        audit_service.emit(
            "os_version.update",
            target_id=os_version_id, target_type="os_version",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise
    obj = await repo.get_by_id(db, os_version_id)
    if obj is None:
        # Permission уже прошёл — отказ из-за отсутствия row, не из-за прав.
        audit_service.emit(
            "os_version.update",
            target_id=os_version_id, target_type="os_version",
            status="failure", allowed=True,
            details={"reason": "not_found"},
        )
        raise NotFoundError(
            error_code="OS_VERSION_NOT_FOUND",
            message="OS version not found",
        )

    changes = payload.model_dump(exclude_unset=True, mode="json")
    if not changes:
        return obj
    try:
        await repo.update(db, obj, changes)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning(
            "IntegrityError на обновлении os_version %s: %s",
            os_version_id, type(exc.orig).__name__,
        )
        audit_service.emit(
            "os_version.update",
            target_id=os_version_id, target_type="os_version",
            status="failure", allowed=True,
            details={"reason": "duplicate", "fields": list(changes.keys())},
        )
        raise ConflictError(
            error_code="OS_VERSION_DUPLICATE",
            message="Update collides with an existing OS version (name UNIQUE)",
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "os_version.update",
        target_id=obj.id, target_type="os_version",
        status="success", allowed=True,
        details={
            "fields": list(changes.keys()),
            "changed_fields": list(changes.keys()),
            "name": obj.name,
        },
    )
    return obj


async def delete_os_version(
    db: AsyncSession,
    identity: IdentityContext,
    os_version_id: str,
) -> None:
    """Hard-delete. FK ondelete=RESTRICT от servers.os_version_id → 409 OS_VERSION_IN_USE."""
    try:
        await permissions.require_action(db, identity, EntityType.OS_VERSION, Action.DELETE)
    except AuthorizationError:
        audit_service.emit(
            "os_version.delete",
            target_id=os_version_id, target_type="os_version",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise
    obj = await repo.get_by_id(db, os_version_id)
    if obj is None:
        # Permission уже прошёл — отказ из-за отсутствия row, не из-за прав.
        audit_service.emit(
            "os_version.delete",
            target_id=os_version_id, target_type="os_version",
            status="failure", allowed=True,
            details={"reason": "not_found"},
        )
        raise NotFoundError(
            error_code="OS_VERSION_NOT_FOUND",
            message="OS version not found",
        )
    name = obj.name
    # Pre-check: SQLAlchemy без passive_deletes обнуляет дочерние FK сам,
    # поэтому DB-уровневый RESTRICT не срабатывает. Явный count даёт
    # детерминированный 409 ещё до DELETE.
    in_use = await repo.count_referencing_servers(db, os_version_id)
    if in_use > 0:
        audit_service.emit(
            "os_version.delete",
            target_id=os_version_id, target_type="os_version",
            status="failure", allowed=True,
            details={"reason": "in_use", "referencing_servers": in_use},
        )
        raise ConflictError(
            error_code="OS_VERSION_IN_USE",
            message="Cannot delete OS version: at least one server still references it",
            details={"hint": "сначала переключите servers.os_version_id или удалите соответствующие сервера"},
        )
    try:
        await repo.delete(db, obj)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning(
            "IntegrityError на удалении os_version %s: %s",
            os_version_id, type(exc.orig).__name__,
        )
        audit_service.emit(
            "os_version.delete",
            target_id=os_version_id, target_type="os_version",
            status="failure", allowed=True,
            details={"reason": "in_use"},
        )
        raise ConflictError(
            error_code="OS_VERSION_IN_USE",
            message="Cannot delete OS version: at least one server still references it",
        ) from exc
    audit_service.emit(
        "os_version.delete",
        target_id=os_version_id, target_type="os_version",
        status="success", allowed=True,
        details={"name": name},
    )
