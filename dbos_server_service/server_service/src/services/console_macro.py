"""Use cases для макросов консоли — личные + системные.

Скоуп решает доступ, а не матрица `entity_permissions`:

* личный (`is_system=false`) — создаёт любой аутентифицированный пользователь,
  привязывается к нему (`user_id`); видит/правит/удаляет только владелец.
* системный (`is_system=true`) — общий в отделе; создаёт/правит/удаляет только
  department_admin своего отдела (`platform_role == department_admin` и
  `department_id` совпадает). Виден всем в отделе.

GET отдаёт мои личные + системные моего отдела, отсортированные по
`(is_system, display_order)`. Чужой личный и чужой отдел в выдачу не попадают;
точечный GET/PATCH/DELETE по чужому id → 404 (не светим существование).
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import PlatformRole
from src.core.exceptions import AuthorizationError, NotFoundError
from src.models import ConsoleMacro
from src.repositories import console_macro as repo
from src.schemas.console_macro import ConsoleMacroCreate, ConsoleMacroUpdate
from src.schemas.identity import IdentityContext
from src.services import audit_service
from src.utils.ids import console_macro_id as new_id

logger = logging.getLogger(__name__)


def _is_department_admin(identity: IdentityContext, department_id: str | None) -> bool:
    """department_admin своего отдела. department_id — отдел макроса."""
    return (
        identity.platform_role == PlatformRole.DEPARTMENT_ADMIN
        and identity.department_id is not None
        and department_id is not None
        and identity.department_id == department_id
    )


def _can_manage(identity: IdentityContext, macro: ConsoleMacro) -> bool:
    """Право на PATCH/DELETE макроса.

    Личный — только владелец. Системный — department_admin отдела макроса.
    """
    if macro.is_system:
        return _is_department_admin(identity, macro.department_id)
    return macro.user_id == identity.user_id


async def list_macros(
    db: AsyncSession,
    identity: IdentityContext,
) -> list[ConsoleMacro]:
    """Мои личные + системные моего отдела, сортировка `(is_system, display_order)`.

    Read без аудита. Если у caller'а нет отдела, системные просто не подмешиваются.
    """
    return await repo.list_for_user(
        db,
        user_id=identity.user_id,
        department_id=identity.department_id,
    )


async def create_macro(
    db: AsyncSession,
    identity: IdentityContext,
    payload: ConsoleMacroCreate,
) -> ConsoleMacro:
    """INSERT макроса. Личный — любой; системный — только department_admin отдела."""
    if payload.is_system:
        if identity.department_id is None or not _is_department_admin(
            identity, identity.department_id
        ):
            audit_service.emit(
                "console_macro.create",
                target_type="console_macro",
                status="denied", allowed=False,
                details={"reason": "permission_denied", "is_system": True},
            )
            raise AuthorizationError(
                error_code="CONSOLE_MACRO_SYSTEM_REQUIRES_DEPARTMENT_ADMIN",
                message="System console macros can only be managed by a department admin",
            )

    data = {
        "id": new_id(),
        "name": payload.name,
        "command_text": payload.command_text,
        "display_order": payload.display_order,
        "is_system": payload.is_system,
        "created_by": identity.user_id,
        "department_id": identity.department_id,
    }
    if payload.is_system:
        # Системный: владельца нет, привязка к отделу.
        data["user_id"] = None
    else:
        # Личный: владелец — caller; department_id для удобной фильтрации.
        data["user_id"] = identity.user_id

    obj = await repo.create(db, data)
    await db.commit()
    await db.refresh(obj)
    audit_service.emit(
        "console_macro.create",
        target_id=obj.id, target_type="console_macro",
        status="success", allowed=True,
        details={"is_system": obj.is_system, "name": obj.name},
    )
    return obj


async def _load_visible(
    db: AsyncSession,
    identity: IdentityContext,
    macro_id: str,
) -> ConsoleMacro:
    """Достать макрос, видимый caller'у, иначе 404.

    Видим: личный самого caller'а ЛИБО системный его отдела. Чужой личный /
    чужой отдел → 404 (не светим существование).
    """
    obj = await repo.get_by_id(db, macro_id)
    if obj is None:
        raise NotFoundError(
            error_code="CONSOLE_MACRO_NOT_FOUND",
            message="Console macro not found",
        )
    if obj.is_system:
        visible = (
            identity.department_id is not None
            and obj.department_id == identity.department_id
        )
    else:
        visible = obj.user_id == identity.user_id
    if not visible:
        raise NotFoundError(
            error_code="CONSOLE_MACRO_NOT_FOUND",
            message="Console macro not found",
        )
    return obj


async def update_macro(
    db: AsyncSession,
    identity: IdentityContext,
    macro_id: str,
    payload: ConsoleMacroUpdate,
) -> ConsoleMacro:
    """PATCH макроса. Личный — владелец; системный — department_admin отдела."""
    obj = await _load_visible(db, identity, macro_id)
    if not _can_manage(identity, obj):
        audit_service.emit(
            "console_macro.update",
            target_id=macro_id, target_type="console_macro",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "is_system": obj.is_system},
        )
        raise AuthorizationError(
            error_code="CONSOLE_MACRO_FORBIDDEN",
            message="Not allowed to modify this console macro",
        )

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return obj
    await repo.update(db, obj, changes)
    await db.commit()
    await db.refresh(obj)
    audit_service.emit(
        "console_macro.update",
        target_id=obj.id, target_type="console_macro",
        status="success", allowed=True,
        details={"is_system": obj.is_system, "fields": list(changes.keys())},
    )
    return obj


async def delete_macro(
    db: AsyncSession,
    identity: IdentityContext,
    macro_id: str,
) -> None:
    """DELETE макроса. Те же правила доступа, что у PATCH."""
    obj = await _load_visible(db, identity, macro_id)
    if not _can_manage(identity, obj):
        audit_service.emit(
            "console_macro.delete",
            target_id=macro_id, target_type="console_macro",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "is_system": obj.is_system},
        )
        raise AuthorizationError(
            error_code="CONSOLE_MACRO_FORBIDDEN",
            message="Not allowed to delete this console macro",
        )
    is_system = obj.is_system
    await repo.delete(db, obj)
    await db.commit()
    audit_service.emit(
        "console_macro.delete",
        target_id=macro_id, target_type="console_macro",
        status="success", allowed=True,
        details={"is_system": is_system},
    )
