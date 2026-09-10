"""Authorization-хелперы поверх матрицы entity_permissions (read-side).

Только проверки: `has_action` / `require_action` / `effective_actions`.
Импортируется любым бизнес-сервисом, который защищает свою операцию. CRUD
самой матрицы (grant/revoke через API) появится отдельным модулем, когда
понадобится управлять кастомными ролями отдела — сейчас строки пишет только
миграция-сид системной роли `admin`.

Пользователь может нести несколько ролей в сервисе; эффективный набор
действий — union по всем. Scope: system-wide строки (`department_id IS NULL`)
матчатся всем, per-department — только caller'у из того же отдела.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import SERVICE_NAME, PlatformRole
from src.core.exceptions import AuthorizationError
from src.dependencies.auth import Identity
from src.repositories import entity_permission as repo


async def has_action(
    db: AsyncSession,
    identity: Identity,
    entity_type: str,
    action: str,
) -> bool:
    """True iff caller имеет право выполнить `action` на `entity_type`."""
    roles = identity.roles_for(SERVICE_NAME)
    if not roles:
        return False
    return await repo.has_action(
        db, entity_type, roles, action, department_id=identity.department_id
    )


async def require_action(
    db: AsyncSession,
    identity: Identity,
    entity_type: str,
    action: str,
) -> None:
    """Бросает AuthorizationError, если у caller'а нет `action` на `entity_type`.

    Denied-аудит здесь не эмитим — это делают вызывающие сервисы, у них есть
    контекст для нормального target_id/details.
    """
    if await has_action(db, identity, entity_type, action):
        return
    raise AuthorizationError(
        error_code="PERMISSION_DENIED",
        message=f"Role does not grant '{action}' on '{entity_type}'",
        details={"entity_type": entity_type, "action": action},
    )


async def require_department_action(
    db: AsyncSession,
    identity: Identity,
    department_id: str,
    entity_type: str,
    action: str,
) -> None:
    """Бросает AuthorizationError, если caller не может `action` над бизнес-данными `department_id`.

    В отличие от `require_action`, здесь `department_id` — параметр запроса
    (обычно из URL), а не только `identity.department_id`: это гейт для
    операций над данными КОНКРЕТНОГО отдела (HR-отчёт по активности, §9.1),
    где cross-department вызов должен быть структурно невозможен, а не просто
    не даваться по роли.

    Два способа пройти:

    1. Caller — `department_admin` СВОЕГО отдела (bypass матрицы, тот же
       приём, что `server_service.permissions.require_host_service_action`);
    2. обычная проверка матрицы (`has_action`) — пропускает носителя
       `admin`/кастомной роли `testing_service` в своём отделе.

    Оба пути требуют `identity.department_id == department_id` — роль,
    выданная в одном отделе, не даёт доступа к данным другого, даже если имя
    роли совпадает.
    """
    if identity.department_id != department_id:
        raise AuthorizationError(
            error_code="PERMISSION_DENIED",
            message="Caller's department does not match the target department",
            details={"entity_type": entity_type, "action": action, "department_id": department_id},
        )
    if identity.platform_role == PlatformRole.DEPARTMENT_ADMIN:
        return
    if await has_action(db, identity, entity_type, action):
        return
    raise AuthorizationError(
        error_code="PERMISSION_DENIED",
        message=f"Role does not grant '{action}' on '{entity_type}' for this department",
        details={"entity_type": entity_type, "action": action, "department_id": department_id},
    )


async def effective_actions(
    db: AsyncSession,
    identity: Identity,
    entity_type: str,
) -> set[str]:
    """Union действий, которые caller может выполнить на `entity_type`."""
    roles = identity.roles_for(SERVICE_NAME)
    if not roles:
        return set()
    return await repo.effective_actions(
        db, entity_type, roles, department_id=identity.department_id
    )
