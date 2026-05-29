"""Cross-department privilege retention guard через `user_service_roles`.

Контекст. `UserServiceRole` (table ``user_service_roles``) хранит связку
``(user_id, service_name, role)`` — без ``department_id``. Когда юзера
переводят между отделами (PATCH `/users/{id}` с ``department_id``),
``DepartmentServiceAccess`` нового отдела может НЕ включать сервис, в
котором у юзера была роль в старом отделе. Если ``_merge_permissions``
не пересекает direct_roles с allowed_services — юзер сохраняет admin
в сервисе, к которому его новый отдел потерял access.

Сценарии (все на реальной PG-сессии через сервис-функции — БЕЗ моков):
1. Transfer A→B без access к сервису в B → роль перестаёт быть effective.
2. introspect возвращает ту же отфильтрованную картинку.
3. Regression: если новый отдел имеет access — роль сохраняется.
4. Симметрия: revoke ``DepartmentServiceAccess`` (без transfer) тоже
   обрезает effective роли (та же дыра, тот же фикс).
"""

import pytest_asyncio

from src.repositories.departments import DepartmentRepository
from src.repositories.users import UserRepository
from src.services.auth_service import (
    collect_user_permissions,
    get_identity,
)
from src.services.authorization_service import introspect
from src.services.auth_service import _build_access_token
from tests.conftest import (
    _assign_role,
    _grant_service,
    _make_dept,
    _make_service,
    _make_user,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture()
async def two_depts_one_service(db):
    """Setup: dept_alpha с access к config_service, dept_beta без access.
    user X сидит в dept_alpha с ролью admin в config_service."""
    dept_a = await _make_dept(db, "ret_dept_a")
    dept_b = await _make_dept(db, "ret_dept_b")
    svc = await _make_service(db, "config_service")
    # dept_a имеет access к config_service, dept_b — нет
    await _grant_service(db, dept_a.id, svc.service_name)
    user = await _make_user(
        db, "ret_user", "User1234!", department_id=dept_a.id,
    )
    await _assign_role(db, user.id, svc.service_name, "admin")
    await db.commit()
    return {"user": user, "dept_a": dept_a, "dept_b": dept_b, "service": svc}


# ── Test 1: baseline — пока в dept_a, admin виден ────────────────────────────


async def test_baseline_role_visible_in_owning_dept(db, two_depts_one_service):
    """Юзер в dept_a (имеющем access к config_service) видит admin."""
    user = two_depts_one_service["user"]
    allowed, roles, _ = await collect_user_permissions(db, user)
    assert "config_service" in allowed
    assert roles.get("config_service") == ["admin"]


# ── Test 2: основной сценарий — transfer обрезает effective ──────────────


async def test_transfer_to_dept_without_access_drops_role(
    db, two_depts_one_service,
):
    """После transfer A → B (B без config_service access) роль admin
    больше не возвращается из `collect_user_permissions`. Раньше роль
    просачивалась через direct_roles."""
    user = two_depts_one_service["user"]
    dept_b = two_depts_one_service["dept_b"]

    # Transfer: меняем department_id напрямую через repo — повторяем то,
    # что делает `user_service.update_user(department_id=...)` без
    # дополнительных side-effects (role-deactivation на transfer пока
    # отсутствует — это и есть мотивация для defence in depth в
    # `_merge_permissions`).
    user_repo = UserRepository(db)
    await user_repo.update(user, department_id=dept_b.id)
    await db.commit()
    await db.refresh(user)

    allowed, roles, _ = await collect_user_permissions(db, user)
    assert "config_service" not in allowed, (
        "dept_b не имеет access к config_service — он не должен числиться "
        "в allowed_services"
    )
    assert "config_service" not in roles, (
        "user_service_roles row пережил transfer — без фильтра он бы "
        "exposed admin в сервисе, к которому новый отдел потерял access"
    )


# ── Test 3: introspect отражает ту же отфильтрованную картинку ───────────────


async def test_introspect_after_transfer_returns_filtered_view(
    db, two_depts_one_service,
):
    """JWT introspect (`authorization_service.introspect`) должен
    revalidate'ить права через `collect_user_permissions`. Если фикс
    в `_merge_permissions` работает — service_roles не содержит
    config_service / admin после transfer."""
    user = two_depts_one_service["user"]
    dept_b = two_depts_one_service["dept_b"]

    # Issue JWT до transfer'а — claims больше не несут permissions, но
    # `sub`/`actor_type` достаточно, чтобы introspect зашёл в БД и пересчитал
    # права уже после transfer'а.
    access = _build_access_token(user)

    # Transfer и commit.
    user_repo = UserRepository(db)
    await user_repo.update(user, department_id=dept_b.id)
    await db.commit()

    # Re-introspect: payload остался прежним (signed), но revalidate
    # должен ПЕРЕстроить allowed_services/service_roles из БД и обрезать.
    resp = await introspect(db, access)
    assert resp.active is True
    assert resp.sub == user.id
    assert "config_service" not in (resp.allowed_services or []), (
        "introspect не должен возвращать config_service после transfer "
        "в отдел без access — иначе JWT-revalidate бесполезен"
    )
    assert "config_service" not in (resp.service_roles or {}), (
        "service_roles тоже должны быть отфильтрованы"
    )


# ── Test 4: get_identity (/me path) ──────────────────────────────────────────


async def test_get_identity_after_transfer_drops_role(
    db, two_depts_one_service,
):
    """`get_identity` (что отдаёт `/me`) тоже должен быть отфильтрован —
    он использует ту же `_merge_permissions`."""
    user = two_depts_one_service["user"]
    dept_b = two_depts_one_service["dept_b"]

    user_repo = UserRepository(db)
    await user_repo.update(user, department_id=dept_b.id)
    await db.commit()

    ident = await get_identity(db, user.id)
    assert ident.user_id == user.id
    assert ident.department_id == dept_b.id
    assert "config_service" not in (ident.allowed_services or [])
    assert "config_service" not in (ident.service_roles or {})


# ── Test 5: regression — если новый отдел имеет access, роль остаётся ───────


async def test_transfer_to_dept_with_access_keeps_role(
    db, two_depts_one_service,
):
    """Regression: если dept_b ТОЖЕ имеет access к config_service —
    роль admin остаётся видна. Фикс не должен over-filter'ить."""
    user = two_depts_one_service["user"]
    dept_b = two_depts_one_service["dept_b"]
    svc = two_depts_one_service["service"]

    # Grant config_service access dept_b'у тоже.
    await _grant_service(db, dept_b.id, svc.service_name)
    user_repo = UserRepository(db)
    await user_repo.update(user, department_id=dept_b.id)
    await db.commit()

    allowed, roles, _ = await collect_user_permissions(db, user)
    assert "config_service" in allowed
    assert roles.get("config_service") == ["admin"], (
        "Юзер всё ещё admin в config_service: новый отдел имеет access, "
        "user_service_roles запись валидна — роль должна сохраниться"
    )


# ── Test 6: симметрия — revoke dept-service access без transfer ─────────────


async def test_revoke_dept_service_access_drops_role(
    db, two_depts_one_service,
):
    """Симметрия: если у юзера остался отдел, но у отдела отозвали
    DepartmentServiceAccess — direct_roles тоже не должны просочиться."""
    user = two_depts_one_service["user"]
    dept_a = two_depts_one_service["dept_a"]
    svc = two_depts_one_service["service"]

    # До revoke — роль видна (sanity check).
    allowed, roles, _ = await collect_user_permissions(db, user)
    assert "config_service" in allowed and roles.get("config_service") == ["admin"]

    # Revoke dept access (без касания user_service_roles).
    dept_repo = DepartmentRepository(db)
    access = await dept_repo.get_access(dept_a.id, svc.service_name)
    assert access is not None
    await dept_repo.revoke_access(access, revoked_by=None)
    await db.commit()

    allowed_after, roles_after, _ = await collect_user_permissions(db, user)
    assert "config_service" not in allowed_after, (
        "После revoke DepartmentServiceAccess отдел не должен видеть сервис"
    )
    assert "config_service" not in roles_after, (
        "user_service_roles row не отозван явно, но dept потерял access "
        "→ effective role должна быть отрезана INTERSECT'ом"
    )
