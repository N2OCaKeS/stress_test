"""Единая точка авторизации доступа к Credential.

`check_access(db, identity, cred, action)` возвращает пару `(allowed, reason)`.
`reason` — стабильный строковый код, который пишется в audit-details — нужен
для разбора жалоб «почему мне 403».

Порядок проверок строго фиксирован (README §«Модель доступа»):

  1. Cred status — blocked-креды видят только admin'ы для аудита/recover.
  2. Department service-access уже проверено `require_user_context`,
     отдельной проверки тут нет.
  3. Scope-зависимая проверка: personal — owner_match или RoleACL у actor.dept;
     department — actor.dept == owner_dept + RoleACL; cross_department — либо
     actor.dept == owner_dept (тогда DeptGrant не нужен), либо DeptGrant
     для actor.dept + RoleACL.
  4. Action-specific: delete/manage_status/grant_dept — только owner-side
     dep_admin или service_admin; grant_acl — owner для personal, dep_admin
     соответствующей стороны для department/cross.
  5. Admin overrides: service_admin — read-only любую креду;
     account_admin — только cross_dep transfer при удалённом owner_dep.

Все ветки возвращают конкретный reason: `owner_match`, `acl_read`,
`acl_write`, `dept_admin`, `service_admin`, `account_admin`, `blocked`,
`dept_grant_missing`, `role_not_in_acl`, `acl_missing_can_read`,
`acl_missing_can_write`, `scope_mismatch`, `not_owner_dept`. Любой
другой текст в reason — это баг, имейте в виду.
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import SERVICE_NAME
from src.dependencies.auth import Identity
from src.models import Credential
from src.repositories import dept_grants as dept_grants_repo
from src.repositories import role_acls as role_acls_repo


# Все поддерживаемые actions. Список замкнут — caller'ы передают строкой,
# Literal даёт mypy подсветить опечатку.
#
# `list_guest` — отдельная проверка для guest-роли. Возвращает True только
# на `visible_to_dept=True` AND owner_dept == identity.dept. Прочие actions
# для guest всегда False (даже на свои dep-cred'ы без visible_to_dept).
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

# Actions, которые требуют can_write на RoleACL (не просто can_read).
_WRITE_ACTIONS: frozenset[str] = frozenset({"write", "delete", "grant_acl", "grant_dept", "manage_status"})

# Actions, которые при scope=personal на cred НЕ принадлежащей actor'у
# никогда не выдаются по ACL (только владелец может). delete и
# manage_status требуют admin override.
_PERSONAL_OWNER_ONLY: frozenset[str] = frozenset({"write", "delete", "manage_status"})


def _is_service_admin(identity: Identity) -> bool:
    """`service_admin` = носитель `admin` роли в secret_service.

    Symmetric с require_service_admin guard'ом: одна точка истины «кто
    считается админом сервиса».
    """
    return "admin" in identity.roles_for(SERVICE_NAME)


def _is_account_admin(identity: Identity) -> bool:
    return identity.platform_role == "account_admin"


def _is_guest_only(identity: Identity) -> bool:
    """Guest = носитель ТОЛЬКО роли `guest` в secret_service."""
    roles = identity.roles_for(SERVICE_NAME)
    return bool(roles) and all(r == "guest" for r in roles)


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
    needs_write = action in _WRITE_ACTIONS
    for acl in matching:
        if needs_write and acl.can_write:
            return True, "acl_write"
        if not needs_write and acl.can_read:
            return True, "acl_read"
    return False, "acl_missing_can_write" if needs_write else "acl_missing_can_read"


async def _check_personal(
    db: AsyncSession,
    identity: Identity,
    cred: Credential,
    action: Action,
) -> tuple[bool, str]:
    """personal: owner — всегда allowed; иначе RoleACL в его dep'е."""
    if cred.owner_user_id == identity.user_id and identity.actor_type == "user":
        return True, "owner_match"

    # Bot не может владеть personal cred — у него нет user-identity.
    # И на чужие personal через RoleACL он тоже не лезет (см. README §«Bot и PAT»).
    if identity.actor_type == "bot":
        return False, "scope_mismatch"

    # Чужой personal — admin actions запрещены без service_admin override.
    if action in _PERSONAL_OWNER_ONLY:
        return False, "scope_mismatch"
    # grant_acl на personal — только владельцу. Проверили выше owner_match,
    # сюда падают не-владельцы.
    if action == "grant_acl":
        return False, "scope_mismatch"
    # grant_dept к personal не применим — у personal cred'ы нет DeptGrant'ов.
    if action == "grant_dept":
        return False, "scope_mismatch"

    # Read/reveal — через RoleACL в dep'е actor'а. У personal cred'ы ACL
    # всегда выдают «в своём dep'е владельца» — actor должен быть в том же dep'е.
    if identity.department_id is None:
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
) -> tuple[bool, str]:
    """Главный entry-point. См. модуль-docstring."""
    # 0. Guest-only: видит только visible_to_dept-cred'ы своего dep'а в listing.
    # Любой другой action (read/reveal/write/delete/...) — мгновенный False.
    if _is_guest_only(identity):
        if action == "list_guest":
            if (
                cred.visible_to_dept
                and cred.owner_dept_id is not None
                and cred.owner_dept_id == identity.department_id
            ):
                return True, "guest_visible_to_dept"
            return False, "guest_not_visible"
        return False, "guest_role_no_access"

    # 1. Blocked status — почти всё запрещено.
    if cred.status == "blocked":
        # service_admin может читать заблокированные креды для аудита.
        if action == "read" and _is_service_admin(identity):
            return True, "admin_override"
        # Recover (manage_status) — service_admin или account_admin.
        if action == "manage_status":
            if _is_service_admin(identity):
                return True, "service_admin"
            if _is_account_admin(identity):
                return True, "account_admin"
        # Все остальные операции на blocked → запрещены.
        return False, "blocked"

    # 5a. service_admin read-only override на active cred'ах любого scope.
    # Кладём ДО scope-проверки — это override, а не fallback.
    if _is_service_admin(identity) and action == "read":
        return True, "admin_override"

    # 5b. account_admin cross_dep transfer — только для cross_department.
    # Transfer моделируем как write на cred (меняем owner_dept_id). Если
    # owner_dept удалён, scope-check ниже не пройдёт (actor не в owner_dep'е,
    # и DeptGrant'а у него нет). Поэтому ловим явно тут.
    if (
        _is_account_admin(identity)
        and action == "write"
        and cred.scope == "cross_department"
    ):
        return True, "admin_override"

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
