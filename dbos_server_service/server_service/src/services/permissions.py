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

from src.core.constants import PlatformRole
from src.core.exceptions import AuthorizationError
from src.dependencies.auth import SERVICE_NAME
from src.repositories import entity_permission as repo
from src.repositories import resource_role_permission as resource_repo
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


async def has_resource_action(
    db: AsyncSession,
    identity: IdentityContext,
    resource_type: str,
    resource_id: str,
    action: str,
) -> bool:
    """True iff caller имеет `action` на КОНКРЕТНОМ ресурсе.

    Аддитивно: тип-wide грант (`has_action`) **либо** инстанс-грант на этот
    `(resource_type, resource_id, action)` через одну из ролей caller'а
    (dept-scope-матч как в матрице). Deny-строк нет.
    """
    if await has_action(db, identity, resource_type, action):
        return True
    roles = identity.roles_for_service(SERVICE_NAME)
    if not roles:
        return False
    return await resource_repo.has_resource_action(
        db, resource_type, resource_id, roles, action,
        department_id=identity.department_id,
    )


async def require_resource_action(
    db: AsyncSession,
    identity: IdentityContext,
    resource_type: str,
    resource_id: str,
    action: str,
) -> None:
    """Бросает AuthorizationError, если у caller'а нет `action` на ресурсе.

    Denied-аудит, как и в `require_action`, эмитят вызывающие endpoint'ы.
    """
    if await has_resource_action(db, identity, resource_type, resource_id, action):
        return
    raise AuthorizationError(
        error_code="PERMISSION_DENIED",
        message=f"No access to action '{action}' on this {resource_type}",
        details={"entity_type": resource_type, "action": action},
    )


async def effective_resource_actions(
    db: AsyncSession,
    identity: IdentityContext,
    resource_type: str,
    resource_id: str,
) -> set[str]:
    """Union тип-wide и инстанс-грантованных действий caller'а на ресурсе.

    UI рисует кнопки на основе того, что caller может с конкретным объектом.
    """
    actions = await effective_actions(db, identity, resource_type)
    roles = identity.roles_for_service(SERVICE_NAME)
    if roles:
        actions = actions | await resource_repo.effective_resource_actions(
            db, resource_type, resource_id, roles,
            department_id=identity.department_id,
        )
    return actions


async def has_resource_grant(
    db: AsyncSession,
    identity: IdentityContext,
    resource_type: str,
    resource_id: str,
) -> bool:
    """True iff у caller'а есть ХОТЬ ОДИН инстанс-грант на ресурс (любой action).

    Используется для расширения видимости: ресурс виден, если у caller'а есть
    точечный доступ к нему, даже когда тип-wide прав нет.
    """
    roles = identity.roles_for_service(SERVICE_NAME)
    if not roles:
        return False
    granted = await resource_repo.resource_ids_with_any_grant(
        db, resource_type, roles, department_id=identity.department_id,
        candidate_ids=[resource_id],
    )
    return resource_id in granted


async def visible_resource_ids(
    db: AsyncSession,
    identity: IdentityContext,
    resource_type: str,
    candidate_ids: list[str],
) -> set[str]:
    """Подмножество `candidate_ids`, на которые у caller'а есть инстанс-грант."""
    roles = identity.roles_for_service(SERVICE_NAME)
    if not roles or not candidate_ids:
        return set()
    return await resource_repo.resource_ids_with_any_grant(
        db, resource_type, roles, department_id=identity.department_id,
        candidate_ids=candidate_ids,
    )


async def granted_resource_ids(
    db: AsyncSession,
    identity: IdentityContext,
    resource_type: str,
) -> set[str]:
    """Все resource_id, на которые у ролей caller'а есть хоть один инстанс-грант.

    Для grant-only листинга: роль без тип-wide прав видит ровно те ресурсы,
    на которые ей точечно выдан доступ.
    """
    roles = identity.roles_for_service(SERVICE_NAME)
    if not roles:
        return set()
    return await resource_repo.resource_ids_with_any_grant(
        db, resource_type, roles, department_id=identity.department_id,
    )


async def has_account_action(
    db: AsyncSession,
    identity: IdentityContext,
    account,
    action: str,
) -> bool:
    """True iff caller имеет право на `action` для КОНКРЕТНОЙ учётки `account`.

    Доступ к учётке резолвится ролями:

      * бланкетная роль отдела даёт `action` на server_account (как `has_action`);
      ИЛИ
      * инстанс-грант на ЭТУ учётку (`resource_role_permissions`) через одну из
        ролей caller'а;
      ИЛИ
      * caller — `department_admin` своего отдела (bypass, видит/может всё со
        всеми учётками отдела; service-роль `admin` покрыта ролевым путём выше).

    Dept-изоляция обеспечена тем, что caller дошёл до видимой ему учётки
    (visibility-404/инстанс-грант в сервисе).
    """
    if await has_action(db, identity, "server_account", action):
        return True
    roles = identity.roles_for_service(SERVICE_NAME)
    if roles and await resource_repo.has_resource_action(
        db, "server_account", account.id, roles, action,
        department_id=identity.department_id,
    ):
        return True
    # department_admin своего отдела — bypass. Учётку он уже видит (dept-isolation
    # пройдена выше по стеку), отдельный matrix-grant ему не нужен.
    return (
        identity.platform_role == PlatformRole.DEPARTMENT_ADMIN
        and identity.department_id is not None
        and identity.department_id == account.department_id
    )


async def require_account_action(
    db: AsyncSession,
    identity: IdentityContext,
    account,
    action: str,
) -> None:
    """Бросает AuthorizationError, если нет `action` на конкретной учётке.

    Account-aware аналог `require_action`: учитывает department_admin-bypass
    поверх ролевой матрицы. denied-аудит, как и в `require_action`, эмитят
    вызывающие endpoint'ы.
    """
    if await has_account_action(db, identity, account, action):
        return
    raise AuthorizationError(
        error_code="PERMISSION_DENIED",
        message=f"No access to action '{action}' on this server account",
        details={"entity_type": "server_account", "action": action},
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
    return await repo.effective_actions(
        db, entity_type, roles, department_id=identity.department_id
    )
