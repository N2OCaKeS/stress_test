"""Authorization-хелперы поверх матрицы entity_permissions (read-side).

Это **read-side** модуль: только проверки (`has_action` / `require_action` /
`effective_actions`). Импортируется любым бизнес-сервисом, который защищает
свои операции. CRUD матрицы (grant/revoke/list через endpoint'ы) живёт
отдельно в :mod:`src.services.permission_service` — он сам вызывает
`require_action` отсюда для авторизации своих write-операций.

Матрица per-(entity_type, role, action); пользователь может нести несколько
ролей, эффективный набор actions — union по всем ролям, что у него есть в
этом сервисе.

Platform-роли (account_admin / loging_admin / loging_reader) не имеют
сервисных ролей по модели §7-8 и блокируются `platform_admin_guard`
middleware на 403 ДО endpoint-логики (всё, кроме health/ready/openapi/
docs/redoc). Матрица здесь — единственный источник истины: если
middleware снимут, без явной service-роли всё равно ничего не пройдёт.

**Department scope.** Строка в `entity_permissions` бывает system-wide
(`department_id IS NULL`, для встроенных ролей `guest`/`reader`/`operator`/
`admin`/`worker_bot`) или per-department (`department_id IS NOT NULL`, для
кастомных ролей service/department admin'а). Lookup'ы видят оба: system-wide
матчатся всем, per-department — только caller'у из того же отдела. Закрывает
cross-dept privilege leak через коллизии имён кастомных ролей.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AuthorizationError
from src.dependencies.auth import SERVICE_NAME
from src.repositories import entity_permission as repo
from src.schemas.identity import IdentityContext


async def has_action(
    db: AsyncSession,
    identity: IdentityContext,
    entity_type: str,
    action: str,
) -> bool:
    """True iff caller имеет право выполнить `action` на `entity_type`.

    Раньше тут был ``account_admin`` bypass — теперь блокируется в
    ``platform_admin_guard`` middleware (см. module docstring). Матрица
    оставлена как defense-in-depth.

    Алгоритм: ищем строки по любой из ролей caller'а, ограниченные его
    department'ом: system-wide строки (``department_id IS NULL``) всегда
    подходят, per-department — только если ``department_id`` совпадает
    с department'ом caller'а. Caller без ролей в этом сервисе сразу
    получает False.
    """
    roles = identity.roles_for_service(SERVICE_NAME)
    if not roles:
        return False
    return await repo.has_action(
        db, entity_type, roles, action, department_id=identity.department_id
    )


async def require_action(
    db: AsyncSession,
    identity: IdentityContext,
    entity_type: str,
    action: str,
) -> None:
    """Бросает AuthorizationError, если у caller'а нет `action` на `entity_type`.

    Здесь НЕ эмитим denied-аудит — это делают вызывающие endpoint'ы,
    у них есть контекст для нормального target_id/target_type/details.
    """
    if await has_action(db, identity, entity_type, action):
        return
    raise AuthorizationError(
        error_code="PERMISSION_DENIED",
        message=f"Role does not grant '{action}' on '{entity_type}'",
        details={"entity_type": entity_type, "action": action},
    )


async def effective_actions(
    db: AsyncSession,
    identity: IdentityContext,
    entity_type: str,
) -> set[str]:
    """Union actions, которые caller может выполнить на `entity_type`.

    Полезно для client-facing endpoint'ов: UI рендерит кнопки на основе
    того, что caller может. Учитывает тот же system-wide vs per-department
    scoping, что и ``has_action``.

    Раньше тут был ``account_admin`` bypass — теперь блокируется в
    ``platform_admin_guard`` middleware (см. module docstring).
    """
    roles = identity.roles_for_service(SERVICE_NAME)
    if not roles:
        return set()
    rows = await repo.list_for_entity(db, entity_type)
    role_set = set(roles)
    dept_id = identity.department_id
    actions: set[str] = set()
    for row in rows:
        if row.role not in role_set:
            continue
        # system-wide row → всегда видима; per-dept row → только если dept совпал
        if row.department_id is None or row.department_id == dept_id:
            actions.add(row.action)
    return actions
