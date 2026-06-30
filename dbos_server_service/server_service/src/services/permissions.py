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
(`department_id IS NULL`, для системных ролей `guest`/`admin`/`worker_bot`)
или per-department (`department_id IS NOT NULL`, для
кастомных ролей service/department admin'а). Lookup'ы видят оба: system-wide
матчатся всем, per-department — только caller'у из того же отдела. Закрывает
cross-dept privilege leak через коллизии имён кастомных ролей.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType, PlatformRole
from src.core.exceptions import AuthorizationError
from src.dependencies.auth import SERVICE_NAME
from src.repositories import entity_permission as repo
from src.repositories import resource_role_permission as resource_repo
from src.schemas.identity import IdentityContext

# Типы ресурсов, где view включается автоматически при наличии любого другого
# права: достаточно уметь что-то делать с сервером/учёткой, чтобы видеть его
# карточку и листинг. Для остального (permission, task) view остаётся явным.
_IMPLIED_VIEW_TYPES = frozenset({EntityType.SERVER, EntityType.SERVER_ACCOUNT})


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
    if await repo.has_action(
        db, entity_type, roles, action, department_id=identity.department_id
    ):
        return True
    # Тип-wide view включается неявно: роль с любым другим тип-wide правом на
    # этот тип (например `power_on`) видит карточки и листинг ресурсов отдела.
    if action == Action.VIEW and entity_type in _IMPLIED_VIEW_TYPES:
        other = await repo.roles_with_other_action(
            db, entity_type, roles, department_id=identity.department_id,
            exclude_action=Action.VIEW,
        )
        return bool(other)
    return False


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


async def _effective_resource_allow(
    db: AsyncSession,
    identity: IdentityContext,
    resource_type: str,
    resource_id: str,
    action: str,
) -> bool:
    """Per-role precedence: инстанс-deny > инстанс-allow > тип-wide база.

    Для каждой роли caller'а эффективное право на ЭТОТ ресурс:

      * есть инстанс `deny`-строка → роль ЗАПРЕЩЕНА (её вклад снимается, базу
        тип-wide эта роль тоже не даёт);
      * иначе есть инстанс `allow`-строка → роль РАЗРЕШЕНА;
      * иначе → базовое тип-wide право (`entity_permissions`).

    Итог — OR по ролям: одна разрешающая роль даёт доступ, deny у другой роли
    его не отбирает.

    Для action `view` на server/server_account действует ещё одно правило:
    если у неотклонённой роли нет ни явного view, ни тип-wide базы view, но
    есть любое другое право на ресурсе (инстанс-allow на любой action либо
    тип-wide база на не-view action) — view включается неявно. Инстанс-deny на
    view, как обычно, снимает роль раньше и неявный view ей уже не достаётся.
    """
    roles = identity.roles_for_service(SERVICE_NAME)
    if not roles:
        return False
    effects = await resource_repo.role_effects_for_action(
        db, resource_type, resource_id, roles, action,
        department_id=identity.department_id,
    )
    undenied = [r for r in roles if effects.get(r) != "deny"]
    if not undenied:
        return False
    # Любая неотклонённая роль с инстанс-allow — сразу доступ.
    if any(effects.get(r) == "allow" for r in undenied):
        return True
    # Иначе — тип-wide база, но только для неотклонённых ролей.
    base_roles = await repo.roles_with_action(
        db, resource_type, undenied, action, department_id=identity.department_id
    )
    if base_roles:
        return True
    # view включается неявно: если у неотклонённой роли есть любое другое право
    # на этом ресурсе (инстанс-allow на любой action или тип-wide база на не-view
    # action), она может ресурс и видеть. Инстанс-deny на view выше уже снял роль.
    if action == Action.VIEW and resource_type in _IMPLIED_VIEW_TYPES:
        return await _has_any_other_grant(
            db, identity, resource_type, resource_id, undenied
        )
    return False


async def _has_any_other_grant(
    db: AsyncSession,
    identity: IdentityContext,
    resource_type: str,
    resource_id: str,
    roles: list[str],
) -> bool:
    """True iff у одной из `roles` есть хоть какое-то право на этом ресурсе.

    Право засчитывается как инстанс-allow на любой action ИЛИ тип-wide база на
    любой не-view action. Это и есть носитель неявного view.
    """
    if not roles:
        return False
    inst = await resource_repo.roles_with_any_allow(
        db, resource_type, resource_id, roles,
        department_id=identity.department_id,
    )
    if inst:
        return True
    base_other = await repo.roles_with_other_action(
        db, resource_type, roles, department_id=identity.department_id,
        exclude_action=Action.VIEW,
    )
    return bool(base_other)


async def has_resource_action(
    db: AsyncSession,
    identity: IdentityContext,
    resource_type: str,
    resource_id: str,
    action: str,
) -> bool:
    """True iff caller имеет `action` на КОНКРЕТНОМ ресурсе.

    Override-модель с precedence (см. `_effective_resource_allow`): инстанс
    `deny` перекрывает тип-wide базу для своей роли, инстанс `allow` добавляет
    право, в остальном действует тип-wide матрица. Итог — OR по ролям caller'а.
    """
    return await _effective_resource_allow(
        db, identity, resource_type, resource_id, action
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
    """Эффективные действия caller'а на ресурсе с учётом deny-override.

    UI рисует кнопки на основе того, что caller может с конкретным объектом.
    Кандидаты — union тип-wide действий и инстанс-allow; каждое прогоняется
    через precedence, чтобы deny-строка убрала действие из набора.
    """
    roles = identity.roles_for_service(SERVICE_NAME)
    if not roles:
        return set()
    candidates = await effective_actions(db, identity, resource_type)
    candidates = candidates | await resource_repo.effective_resource_actions(
        db, resource_type, resource_id, roles,
        department_id=identity.department_id,
    )
    allowed: set[str] = set()
    for action in candidates:
        if await _effective_resource_allow(
            db, identity, resource_type, resource_id, action
        ):
            allowed.add(action)
    return allowed


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

    Доступ к учётке резолвится ролями с тем же precedence, что и
    `has_resource_action` (инстанс-deny > инстанс-allow > тип-wide база):

      * эффективное право хотя бы одной роли caller'а на ЭТУ учётку (инстанс
        deny конкретной роли убирает её вклад, но другая роль может разрешить);
      ИЛИ
      * caller — `department_admin` своего отдела (платформенный bypass: видит/
        может всё со всеми учётками отдела, инстанс-deny на роли его не касается).

    Dept-изоляция обеспечена тем, что caller дошёл до видимой ему учётки
    (visibility-404/инстанс-грант в сервисе).
    """
    if await _effective_resource_allow(
        db, identity, "server_account", account.id, action
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
