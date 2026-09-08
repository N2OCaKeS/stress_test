"""Use cases для категорий серверов по мощности — платформенный каталог.

Устройство зеркалит `os_version_service`: read (list + get по id + get по
коду) открыт любому аутентифицированному актору и не аудитится, запись
(create/update/delete) идёт под матрицей прав `(server_category, *, ...)`.
Удаление категории, на которую ссылается хоть один сервер, отбивается 409
SERVER_CATEGORY_IN_USE — FK `servers.category_id` стоит ondelete=RESTRICT,
но явный pre-check нужен, потому что ORM без passive_deletes обнулил бы
дочерние ссылки сам.
"""

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType
from src.core.exceptions import AuthorizationError, ConflictError, NotFoundError
from src.models import ServerCategory
from src.repositories import server_category as repo
from src.schemas.identity import IdentityContext
from src.schemas.server_category import ServerCategoryCreate, ServerCategoryUpdate
from src.services import audit_service, permissions
from src.utils.ids import server_category_id as new_id

logger = logging.getLogger(__name__)


async def create_server_category(
    db: AsyncSession,
    identity: IdentityContext,
    payload: ServerCategoryCreate,
) -> ServerCategory:
    """INSERT новой категории. UNIQUE(code) → 409 SERVER_CATEGORY_DUPLICATE."""
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER_CATEGORY, Action.CREATE
        )
    except AuthorizationError:
        audit_service.emit(
            "server_category.create",
            target_type="server_category",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise

    data = payload.model_dump(mode="json")
    data["id"] = new_id()
    data["created_by"] = identity.user_id
    try:
        obj = await repo.create(db, data)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning(
            "IntegrityError на создании server_category: %s", type(exc.orig).__name__
        )
        audit_service.emit(
            "server_category.create",
            target_type="server_category",
            status="failure", allowed=True,
            details={"reason": "duplicate", "code": payload.code},
        )
        raise ConflictError(
            error_code="SERVER_CATEGORY_DUPLICATE",
            message="Server category with this code already exists",
            details={"hint": "уникальное поле — code"},
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "server_category.create",
        target_id=obj.id, target_type="server_category",
        status="success", allowed=True,
        details={"code": obj.code, "label": obj.label},
    )
    return obj


async def get_server_category(
    db: AsyncSession,
    category_id: str,
) -> ServerCategory:
    """SELECT категории по PK. Read без проверки прав и без аудита."""
    obj = await repo.get_by_id(db, category_id)
    if obj is None:
        raise NotFoundError(
            error_code="SERVER_CATEGORY_NOT_FOUND",
            message="Server category not found",
        )
    return obj


async def get_server_category_by_code(
    db: AsyncSession,
    code: str,
) -> ServerCategory:
    """SELECT категории по UNIQUE code. Read без проверки прав и без аудита."""
    obj = await repo.get_by_code(db, code)
    if obj is None:
        raise NotFoundError(
            error_code="SERVER_CATEGORY_NOT_FOUND",
            message="Server category not found",
        )
    return obj


async def list_server_categories(
    db: AsyncSession,
    limit: int,
    offset: int,
) -> tuple[list[ServerCategory], int]:
    """List + count полного каталога. Read без проверки прав и без аудита."""
    items = await repo.list_all(db, limit=limit, offset=offset)
    total = await repo.count_all(db)
    return items, total


async def update_server_category(
    db: AsyncSession,
    identity: IdentityContext,
    category_id: str,
    payload: ServerCategoryUpdate,
) -> ServerCategory:
    """PATCH-обновление. Пустой диф → возврат без UPDATE."""
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER_CATEGORY, Action.UPDATE
        )
    except AuthorizationError:
        audit_service.emit(
            "server_category.update",
            target_id=category_id, target_type="server_category",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise
    obj = await repo.get_by_id(db, category_id)
    if obj is None:
        audit_service.emit(
            "server_category.update",
            target_id=category_id, target_type="server_category",
            status="failure", allowed=True,
            details={"reason": "not_found"},
        )
        raise NotFoundError(
            error_code="SERVER_CATEGORY_NOT_FOUND",
            message="Server category not found",
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
            "IntegrityError на обновлении server_category %s: %s",
            category_id, type(exc.orig).__name__,
        )
        audit_service.emit(
            "server_category.update",
            target_id=category_id, target_type="server_category",
            status="failure", allowed=True,
            details={"reason": "duplicate", "fields": list(changes.keys())},
        )
        raise ConflictError(
            error_code="SERVER_CATEGORY_DUPLICATE",
            message="Update collides with an existing server category (code UNIQUE)",
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "server_category.update",
        target_id=obj.id, target_type="server_category",
        status="success", allowed=True,
        details={"fields": list(changes.keys()), "code": obj.code},
    )
    return obj


async def delete_server_category(
    db: AsyncSession,
    identity: IdentityContext,
    category_id: str,
) -> None:
    """Hard-delete. Категория, на которую ссылается сервер → 409 SERVER_CATEGORY_IN_USE."""
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER_CATEGORY, Action.DELETE
        )
    except AuthorizationError:
        audit_service.emit(
            "server_category.delete",
            target_id=category_id, target_type="server_category",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise
    obj = await repo.get_by_id(db, category_id)
    if obj is None:
        audit_service.emit(
            "server_category.delete",
            target_id=category_id, target_type="server_category",
            status="failure", allowed=True,
            details={"reason": "not_found"},
        )
        raise NotFoundError(
            error_code="SERVER_CATEGORY_NOT_FOUND",
            message="Server category not found",
        )
    code = obj.code
    in_use = await repo.count_referencing_servers(db, category_id)
    if in_use > 0:
        audit_service.emit(
            "server_category.delete",
            target_id=category_id, target_type="server_category",
            status="failure", allowed=True,
            details={"reason": "in_use", "referencing_servers": in_use},
        )
        raise ConflictError(
            error_code="SERVER_CATEGORY_IN_USE",
            message="Cannot delete server category: at least one server still references it",
            details={"hint": "сначала переключите servers.category_id на другую категорию"},
        )
    try:
        await repo.delete(db, obj)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning(
            "IntegrityError на удалении server_category %s: %s",
            category_id, type(exc.orig).__name__,
        )
        audit_service.emit(
            "server_category.delete",
            target_id=category_id, target_type="server_category",
            status="failure", allowed=True,
            details={"reason": "in_use"},
        )
        raise ConflictError(
            error_code="SERVER_CATEGORY_IN_USE",
            message="Cannot delete server category: at least one server still references it",
        ) from exc
    audit_service.emit(
        "server_category.delete",
        target_id=category_id, target_type="server_category",
        status="success", allowed=True,
        details={"code": code},
    )
