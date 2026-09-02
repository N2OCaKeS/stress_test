"""Общие role-предикаты `_identity_roles` + их реэкспорт в сервисах.

Раньше эти функции жили копией в `access_service` и `credential_service` и
успели разъехаться. Тест фиксирует, что оба модуля ссылаются на одну
реализацию, и проверяет саму логику предикатов без БД.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.dependencies.auth import Identity
from src.services import _identity_roles, access_service, credential_service


@dataclass
class _FakeCred:
    scope: str
    owner_dept_id: str | None = None
    owner_user_dept_id: str | None = None


def _identity(
    *,
    department_id: str | None = "dep_a0000000000000000000000001",
    roles: list[str] | None = None,
    platform_role: str | None = None,
) -> Identity:
    return Identity(
        user_id="usr_a0000000000000000000000001",
        username="actor",
        actor_type="user",
        department_id=department_id,
        allowed_services=["secret_service"],
        service_roles={"secret_service": roles if roles is not None else []},
        is_banned=False,
        platform_role=platform_role,
    )


def test_modules_share_one_implementation():
    assert access_service._is_service_admin is _identity_roles.is_service_admin
    assert credential_service._is_service_admin is _identity_roles.is_service_admin
    assert access_service._is_service_admin_for is _identity_roles.is_service_admin_for
    assert credential_service._is_service_admin_for is _identity_roles.is_service_admin_for
    assert access_service._is_account_admin is _identity_roles.is_account_admin
    assert credential_service._is_account_admin is _identity_roles.is_account_admin
    assert access_service._is_guest_only is _identity_roles.is_guest_only
    assert credential_service._is_guest_only is _identity_roles.is_guest_only
    # Публичное имя для endpoint-слоя осталось стабильным.
    assert credential_service.is_guest_only is _identity_roles.is_guest_only


def test_is_service_admin():
    assert _identity_roles.is_service_admin(_identity(roles=["admin"]))
    assert not _identity_roles.is_service_admin(_identity(roles=["operator"]))
    assert not _identity_roles.is_service_admin(_identity(roles=[]))


def test_is_account_admin():
    assert _identity_roles.is_account_admin(_identity(platform_role="account_admin"))
    assert not _identity_roles.is_account_admin(_identity(platform_role="department_admin"))
    assert not _identity_roles.is_account_admin(_identity())


def test_is_guest_only():
    assert _identity_roles.is_guest_only(_identity(roles=["guest"]))
    # Любая дополнительная роль выводит из guest-only.
    assert not _identity_roles.is_guest_only(_identity(roles=["guest", "reader"]))
    # Без ролей — не guest.
    assert not _identity_roles.is_guest_only(_identity(roles=[]))


def test_is_service_admin_for_own_dept_only():
    admin = _identity(roles=["admin"], department_id="dep_a")
    own_dept = _FakeCred(scope="department", owner_dept_id="dep_a")
    other_dept = _FakeCred(scope="department", owner_dept_id="dep_b")
    assert _identity_roles.is_service_admin_for(admin, own_dept)
    assert not _identity_roles.is_service_admin_for(admin, other_dept)


def test_is_service_admin_for_requires_admin_role():
    operator = _identity(roles=["operator"], department_id="dep_a")
    own_dept = _FakeCred(scope="department", owner_dept_id="dep_a")
    assert not _identity_roles.is_service_admin_for(operator, own_dept)


def test_is_service_admin_for_personal_same_owner_dept():
    admin = _identity(roles=["admin"], department_id="dep_a")
    # personal владельца того же dep'а — допускается.
    pers_same = _FakeCred(scope="personal", owner_user_dept_id="dep_a")
    # personal с пустым owner_user_dept_id (старые записи) — best-effort допуск.
    pers_legacy = _FakeCred(scope="personal", owner_user_dept_id=None)
    # personal чужого dep'а — нет.
    pers_other = _FakeCred(scope="personal", owner_user_dept_id="dep_b")
    assert _identity_roles.is_service_admin_for(admin, pers_same)
    assert _identity_roles.is_service_admin_for(admin, pers_legacy)
    assert not _identity_roles.is_service_admin_for(admin, pers_other)


def test_is_service_admin_for_none_department():
    admin = _identity(roles=["admin"], department_id=None)
    cred = _FakeCred(scope="department", owner_dept_id="dep_a")
    assert not _identity_roles.is_service_admin_for(admin, cred)
