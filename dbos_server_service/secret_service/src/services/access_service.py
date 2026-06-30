"""Единая точка авторизации доступа к Credential.

`check_access(db, identity, cred, action)` возвращает пару `(allowed, reason)`.
`reason` — стабильный строковый код, который пишется в audit-details — нужен
для разбора жалоб «почему мне 403».

Порядок проверок строго фиксирован (README §«Модель доступа»):

  1. Cred status — blocked-креды видят только admin'ы своего dept'а для
     аудита/recover.
  2. Department service-access уже проверено `require_user_context`,
     отдельной проверки тут нет.
  3. Scope-зависимая проверка: personal — owner_match или RoleACL у actor.dept;
     department — actor.dept == owner_dept + RoleACL; cross_department — либо
     actor.dept == owner_dept (тогда DeptGrant не нужен), либо DeptGrant
     для actor.dept + RoleACL.
  4. Action-specific: delete/manage_status/grant_dept — только owner-side
     dep_admin или service-роль admin того же dept'а; grant_acl — owner для
     personal, dep_admin соответствующей стороны для department/cross.
  5. Системные сервис-роли:
     - `admin` своего dept'а — полный доступ (read/reveal/write/delete/
       grant_acl/grant_dept/manage_status) к department/cross_department-кред'ам
       этого отдела, наравне с dep_admin. Personal чужих admin НЕ видит —
       cross-dept привилегий у роли тоже нет. Исключение — blocked-кред'ы: их
       admin своего dept'а читает и recover'ит для аудита (включая personal
       своего отдела), это узкий lifecycle-канал.
     - `guest` своего dept'а — уровень `view`: метаданные/листинг всех
       department/cross_department-кред отдела (read/list_guest), без значения
       и без записи.

account_admin (платформенная роль) в этой матрице НЕ получает доступа: ни
read, ни reveal/write/delete — это зона ответственности dept-уровня (см.
Memory: project-dbos-secrets-scope). Исключение лежит ВНЕ check_access:
emergency transfer/recover для кред'ы с удалённым владеющим отделом
проверяется напрямую в `credential_service` (ветка `_is_account_admin`), а не
здесь — это аварийный override восстановления, не обычный доступ к содержимому.

Доступ к секрету выстроен лесенкой из трёх уровней:

  * `view`   — видеть, что секрет есть (метаданные / в листинге), без значения;
  * `read`   — видеть значение (reveal);
  * `write`  — менять / удалять / раздавать доступ.

Лесенка вложена: `can_write ⊇ can_read ⊇ can_view`. Поэтому action `read`
(метаданные) проходит при любом из трёх флагов, `reveal` — при can_read или
can_write, write-actions — только при can_write.

Все ветки возвращают конкретный reason: `owner_match`, `acl_view`,
`acl_read`, `acl_write`, `user_acl_view`, `user_acl_read`, `user_acl_write`,
`dept_admin`, `service_admin`, `blocked`, `dept_grant_missing`,
`role_not_in_acl`, `acl_missing_can_view`, `acl_missing_can_read`,
`acl_missing_can_write`, `scope_mismatch`, `not_owner_dept`,
`guest_visible_to_dept`, `guest_not_visible`, `guest_no_value_access`,
`guest_role_no_access`. Любой другой текст в reason — это баг, имейте в виду.

Прямой per-user grant (`CredentialUserACL`) действует ТОЛЬКО на личные кред'ы
(scope=personal): проверяется до scope-веток и короткозамыкает на положительном
исходе; его miss'овый reason `no_user_acl` наружу не выходит — caller продолжает
scope-проверку и вернёт её reason. Для department/cross_department доступ
раздаётся только ролями, user-ACL на них не учитывается.
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import SERVICE_NAME
from src.dependencies.auth import Identity
from src.models import Credential
from src.repositories import dept_grants as dept_grants_repo
from src.repositories import role_acls as role_acls_repo
from src.repositories import user_acls as user_acls_repo
from src.services import _identity_roles


# Все поддерживаемые actions. Список замкнут — caller'ы передают строкой,
# Literal даёт mypy подсветить опечатку.
#
# `list_guest` — отдельная проверка для guest-роли в листинге. Возвращает True
# на любой department/cross_department-кред'е своего dep'а (owner_dept ==
# identity.dept). guest тем же путём проходит `read` (метаданные карточки);
# value-actions (reveal/write/...) для guest всегда False.
Action = Literal[
    "read",
    "reveal",
    "write",
    "delete",
    "grant_acl",
    "grant_dept",
    "manage_status",
    "list_guest",
]

# Actions, которые требуют can_write на ACL.
_WRITE_ACTIONS: frozenset[str] = frozenset({"write", "delete", "grant_acl", "grant_dept", "manage_status"})

# Actions, раскрывающие значение секрета — требуют can_read (или выше).
_REVEAL_ACTIONS: frozenset[str] = frozenset({"reveal"})

# Прочие нечитающие-значение actions (read, list_guest) — метаданные/листинг,
# им достаточно can_view (или выше).

# Actions, которые при scope=personal на cred НЕ принадлежащей actor'у
# никогда не выдаются по ACL (только владелец может). delete и
# manage_status требуют admin override.
_PERSONAL_OWNER_ONLY: frozenset[str] = frozenset({"write", "delete", "manage_status"})


# Role-предикаты — общие с credential_service, живут в `_identity_roles`.
_is_service_admin = _identity_roles.is_service_admin
_is_service_admin_for = _identity_roles.is_service_admin_for
_is_account_admin = _identity_roles.is_account_admin
_is_guest_only = _identity_roles.is_guest_only


def _has_acl_permission(acls: list, action: Action, identity_roles: list[str]) -> tuple[bool, str]:
    """Есть ли среди ACL'ей роль actor'а с нужным правом.

    Возвращает (allowed, reason). Сначала пытаемся найти ACL с подходящей
    role_name, потом проверяем can_read/can_write на нём.
    """
    if not acls:
        return False, "role_not_in_acl"
    matching = [a for a in acls if a.role_name in identity_roles]
    if not matching:
        return False, "role_not_in_acl"
    if action in _WRITE_ACTIONS:
        for acl in matching:
            if acl.can_write:
                return True, "acl_write"
        return False, "acl_missing_can_write"
    if action in _REVEAL_ACTIONS:
        # Значение секрета — can_read или (по лесенке) can_write.
        for acl in matching:
            if acl.can_read or acl.can_write:
                return True, "acl_read"
        return False, "acl_missing_can_read"
    # Метаданные / листинг — достаточно любого уровня лесенки.
    for acl in matching:
        if acl.can_view or acl.can_read or acl.can_write:
            return True, "acl_view"
    return False, "acl_missing_can_view"


async def _check_user_acl(
    db: AsyncSession,
    identity: Identity,
    cred: Credential,
    action: Action,
) -> tuple[bool, str]:
    """Прямой per-user grant на личную креду.

    Действует только для scope=personal: владелец personal-кред'ы выдаёт доступ
    поимённо одному user_id. Лесенка та же, что у RoleACL: метаданные (`read`)
    — `can_view` и выше, значение (`reveal`) — `can_read` и выше, запись —
    `can_write`. Бот сюда не попадает — у него нет user-identity, а user-ACL
    адресован
    конкретному пользователю.

    Для department/cross_department доступ раздаётся только ролями (RoleACL),
    поэтому user-ACL на них не учитывается — даже если в БД остались
    исторические строки (write-side их больше не создаёт).

    Возвращает `(False, "no_user_acl")`, если записи нет / скоуп не personal —
    это не финальный отказ, caller продолжает scope-проверку.
    """
    if cred.scope != "personal":
        return False, "no_user_acl"
    if identity.actor_type != "user" or not identity.user_id:
        return False, "no_user_acl"
    acl = await user_acls_repo.find(db, cred.id, identity.user_id)
    if acl is None:
        return False, "no_user_acl"
    # user-ACL покрывает только контентные действия. Управление кред'ой и
    # её grants (delete/manage_status/grant_acl/grant_dept) остаётся за
    # владельцем / dep_admin — выдать «право раздавать доступ» поимённо нельзя.
    if action in {"delete", "manage_status", "grant_acl", "grant_dept"}:
        return False, "no_user_acl"
    if action == "write":
        if acl.can_write:
            return True, "user_acl_write"
        return False, "no_user_acl"
    if action in _REVEAL_ACTIONS:
        if acl.can_read or acl.can_write:
            return True, "user_acl_read"
        return False, "no_user_acl"
    # Метаданные / листинг — достаточно любого уровня лесенки.
    if acl.can_view or acl.can_read or acl.can_write:
        return True, "user_acl_view"
    return False, "no_user_acl"


async def _check_personal(
    db: AsyncSession,
    identity: Identity,
    cred: Credential,
    action: Action,
) -> tuple[bool, str]:
    """personal: owner — всегда allowed; иначе RoleACL в dep'е ВЛАДЕЛЬЦА.

    Cross-dep leak: раньше ACL искался в dep'е actor'а, что позволяло Bob'у из
    dep_B читать personal Alice'ы из dep_A, если кто-то завёл `RoleACL(cred,
    dept_id=dep_B)` — теоретически такой ACL не должен существовать, но
    role_acl_service до фикса не проверял. Теперь требуем actor.dept ==
    cred.owner_user_dept_id ДО фильтра по dept_id.
    """
    if cred.owner_user_id == identity.user_id and identity.actor_type == "user":
        return True, "owner_match"

    # Bot не может владеть personal cred — у него нет user-identity.
    # И на чужие personal через RoleACL он тоже не лезет (см. README §«Bot и PAT»).
    if identity.actor_type == "bot":
        return False, "scope_mismatch"

    # Чужой personal — admin actions запрещены без service-admin override
    # своего dept'а (override обрабатывается выше в check_access).
    if action in _PERSONAL_OWNER_ONLY:
        return False, "scope_mismatch"
    # grant_acl на personal — только владельцу. Проверили выше owner_match,
    # сюда падают не-владельцы.
    if action == "grant_acl":
        return False, "scope_mismatch"
    # grant_dept к personal не применим — у personal cred'ы нет DeptGrant'ов.
    if action == "grant_dept":
        return False, "scope_mismatch"

    if identity.department_id is None:
        return False, "scope_mismatch"

    # ACL живёт в dep'е владельца; actor должен быть в том же dep'е. Если
    # `owner_user_dept_id` пуст (старые записи до миграции c3b5e7d2a1f8),
    # дозволяем старый путь как best-effort — миграция не backfill'ила
    # значения. Backfill — отдельной задачей.
    owner_dept = cred.owner_user_dept_id
    if owner_dept is not None and owner_dept != identity.department_id:
        return False, "scope_mismatch"

    acls = await role_acls_repo.get_for_cred_dept(
        db, cred.id, identity.department_id
    )
    return _has_acl_permission(acls, action, identity.roles_for(SERVICE_NAME))


async def _check_department(
    db: AsyncSession,
    identity: Identity,
    cred: Credential,
    action: Action,
) -> tuple[bool, str]:
    """department: actor должен быть в owner_dept + иметь RoleACL."""
    if identity.department_id != cred.owner_dept_id:
        return False, "scope_mismatch"
    # Внутри owner-dep'а dep_admin может всё (включая grant_acl/delete/grant_dept).
    if (
        identity.platform_role == "department_admin"
        and identity.department_id == cred.owner_dept_id
    ):
        return True, "dept_admin"
    # Сервисная роль admin своего dept'а — тот же объём, что у dep_admin, но
    # только в рамках department/cross_department-кред этого отдела.
    if _is_service_admin(identity):
        return True, "service_admin"
    # Обычный actor — только через RoleACL.
    acls = await role_acls_repo.get_for_cred_dept(
        db, cred.id, identity.department_id or ""
    )
    return _has_acl_permission(acls, action, identity.roles_for(SERVICE_NAME))


async def _check_cross_department(
    db: AsyncSession,
    identity: Identity,
    cred: Credential,
    action: Action,
) -> tuple[bool, str]:
    """cross_department: owner-dep — как department; recipient-dep — DeptGrant + RoleACL."""
    if identity.department_id is None:
        return False, "scope_mismatch"

    is_owner_dep = identity.department_id == cred.owner_dept_id

    # Owner-dep: тот же путь, что и department, без DeptGrant.
    if is_owner_dep:
        if identity.platform_role == "department_admin":
            return True, "dept_admin"
        if _is_service_admin(identity):
            return True, "service_admin"
        acls = await role_acls_repo.get_for_cred_dept(
            db, cred.id, identity.department_id
        )
        return _has_acl_permission(acls, action, identity.roles_for(SERVICE_NAME))

    # Recipient-dep: первично — управление (delete/manage_status/grant_dept)
    # запрещено. Это сторона владельца.
    if action in {"delete", "manage_status"}:
        return False, "not_owner_dept"

    # grant_dept — это owner-side операция (выдача нового DeptGrant'а). Для
    # recipient-dep'а смысла нет.
    if action == "grant_dept":
        return False, "not_owner_dept"

    # Recipient-dep: должен существовать DeptGrant.
    has_grant = await dept_grants_repo.exists_for(
        db, cred.id, identity.department_id
    )
    if not has_grant:
        return False, "dept_grant_missing"

    # grant_acl recipient'у разрешён только локальному dep_admin'у.
    if action == "grant_acl":
        if identity.platform_role == "department_admin":
            return True, "dept_admin"
        return False, "scope_mismatch"

    # read/reveal/write — через RoleACL в recipient'е.
    acls = await role_acls_repo.get_for_cred_dept(
        db, cred.id, identity.department_id
    )
    return _has_acl_permission(acls, action, identity.roles_for(SERVICE_NAME))


async def check_access(
    db: AsyncSession,
    identity: Identity,
    cred: Credential,
    action: Action,
    *,
    status_override: str | None = None,
) -> tuple[bool, str]:
    """Главный entry-point. См. модуль-docstring.

    `status_override` — для гипотетического check'а вида «а если бы cred была
    active»: caller передаёт `"active"` без мутации ORM-объекта. Не используется
    в обычном flow; задействован только в credential_service для решения
    «410 vs 404» на blocked-кред'ах.
    """
    effective_status = status_override if status_override is not None else cred.status
    # 0. Guest-роль — это уровень `view` на весь отдел: видит метаданные всех
    # department/cross_department-кред своего dep'а (листинг и карточку), но не
    # значение и ничего не меняет. Personal чужих и cred'ы других отделов — мимо.
    if _is_guest_only(identity):
        own_dept_shared = (
            cred.scope in ("department", "cross_department")
            and cred.owner_dept_id is not None
            and cred.owner_dept_id == identity.department_id
        )
        if action == "list_guest":
            if own_dept_shared:
                return True, "guest_visible_to_dept"
            return False, "guest_not_visible"
        if action == "read":
            if own_dept_shared:
                return True, "guest_visible_to_dept"
            # Не своя dep-cred'а — маскируем существование под 404.
            return False, "guest_role_no_access"
        # reveal/write/delete/grant/manage_status. На своей dep-cred'е guest её
        # видит, поэтому отказ — честный 403; на чужой/personal — 404-маска.
        if own_dept_shared:
            return False, "guest_no_value_access"
        return False, "guest_role_no_access"

    # 1. Blocked status — почти всё запрещено.
    if effective_status == "blocked":
        # admin secret_service'а своего dept'а может читать blocked кред для аудита.
        if action == "read" and _is_service_admin_for(identity, cred):
            return True, "admin_override"
        # manage_status на blocked-кред'е через check_access — только admin
        # secret_service'а своего dept'а. account_admin тут не пускаем. Его
        # emergency transfer/recover идёт мимо check_access, прямой веткой в
        # credential_service, и этой матрицы не касается.
        if action == "manage_status":
            if _is_service_admin_for(identity, cred):
                return True, "service_admin"
        # Все остальные операции на blocked → запрещены.
        return False, "blocked"

    # Полный доступ admin'а к НЕличным cred'ам своего dept'а живёт внутри
    # scope-веток (_check_department / _check_cross_department): там admin
    # трактуется наравне с dep_admin. Personal чужих admin не видит — отдельного
    # override на personal больше нет.

    # 5b. Прямой per-user grant. Орто­гонален scope: пускает поимённо
    # выданного пользователя на read/reveal/write независимо от его роли и
    # департамента. Только положительный исход короткозамыкает — на miss'е
    # (нет записи / нет нужного флага) продолжаем обычную scope-проверку.
    user_allowed, user_reason = await _check_user_acl(db, identity, cred, action)
    if user_allowed:
        return True, user_reason

    # 2. Department service-access проверен выше (require_user_context).
    # 3+4. Scope-зависимая проверка.
    if cred.scope == "personal":
        return await _check_personal(db, identity, cred, action)
    if cred.scope == "department":
        return await _check_department(db, identity, cred, action)
    if cred.scope == "cross_department":
        return await _check_cross_department(db, identity, cred, action)

    # Невозможный scope — модель не допускает, но возвращаем False, не
    # AssertionError, чтоб не уронить процесс на повреждённой строке.
    return False, "scope_mismatch"
