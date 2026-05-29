"""PAT introspect должен учитывать group-derived service access.

Контекст. `collect_user_permissions` собирает effective-права юзера из
двух источников: прямой `DepartmentServiceAccess` его отдела и
`GroupServiceAccess` групп, в которых он состоит. JWT-introspect и `/me`
давно идут через эту функцию. PAT-ветка introspect раньше брала только
`dept_repo.list_active_services(user.department_id)` — игнорировала
группы. Итог: PAT, выданный под сервис, доступный юзеру ТОЛЬКО через
группу, при introspect показывал `effective_services=[]`, тогда как JWT
того же юзера показывал сервис. Асимметрия нарушала контракт
«PAT = subset прав юзера в том же контексте, что JWT».

Тесты гоняются на реальной PG-сессии через сервис-функции (без моков).
PAT создаём напрямую через repo: штатный `create_pat` валидирует
`allowed_services ⊆ dept-services` и не дал бы заскоупить PAT на
group-only сервис — а проверять надо именно introspect-ветку.
"""

import pytest_asyncio

from src.core.security import generate_pat
from src.repositories.groups import GroupRepository
from src.repositories.tokens import TokenRepository
from src.services.auth_service import _build_access_token, collect_user_permissions
from src.services.authorization_service import introspect
from tests.conftest import (
    _grant_service,
    _make_dept,
    _make_service,
    _make_user,
)


async def _issue_pat(db, user_id: str, name: str, allowed_services: list[str]) -> str:
    """Завести PAT напрямую через repo и вернуть raw-токен.

    Минует `token_service.create_pat` (он режет scope по dept-сервисам) —
    нам нужно положить в `allowed_services` group-only сервис и проверить,
    что introspect его пропустит.
    """
    raw, prefix, token_hash = generate_pat()
    token_repo = TokenRepository(db)
    await token_repo.create(
        user_id=user_id,
        name=name,
        token_hash=token_hash,
        token_prefix=prefix,
        allowed_services=allowed_services,
    )
    await db.commit()
    return raw


@pytest_asyncio.fixture()
async def user_with_group_service(db):
    """user в dept без прямого access к group_only_service, но состоит в
    группе, которой выдан access к этому сервису + роль operator."""
    dept = await _make_dept(db, "pgs_dept")
    direct_svc = await _make_service(db, "pgs_direct_service")
    group_svc = await _make_service(db, "pgs_group_service")

    # Отдел имеет прямой access только к direct_service.
    await _grant_service(db, dept.id, direct_svc.service_name)

    user = await _make_user(db, "pgs_user", "User1234!", department_id=dept.id)

    group_repo = GroupRepository(db)
    grp = await group_repo.create(
        department_id=dept.id,
        name="pgs_group",
        display_name="PGS Group",
        description=None,
        created_by=None,
    )
    await group_repo.add_member(grp.id, user.id, added_by=None)
    await group_repo.grant_service(grp.id, group_svc.service_name, granted_by=None)
    await group_repo.set_roles(grp.id, group_svc.service_name, ["operator"], assigned_by=None)
    await db.commit()
    await db.refresh(user)
    return {
        "user": user,
        "dept": dept,
        "direct_service": direct_svc.service_name,
        "group_service": group_svc.service_name,
    }


# ── Основной баг: PAT видит group-derived сервис ────────────────────────────


async def test_pat_introspect_sees_group_only_service(db, user_with_group_service):
    """PAT, заскоупленный на сервис, доступный только через группу,
    при introspect возвращает его в effective_services (раньше — нет)."""
    ctx = user_with_group_service
    user = ctx["user"]
    group_svc = ctx["group_service"]

    raw = await _issue_pat(db, user.id, "pat_group_svc", [group_svc])
    resp = await introspect(db, raw)

    assert resp.active is True
    assert resp.subject_type == "user"
    assert group_svc in (resp.allowed_services or []), (
        "group-derived сервис должен попасть в effective — это и есть фикс"
    )
    assert resp.service_roles.get(group_svc) == ["operator"], (
        "group-роль operator должна прийти вместе с group-сервисом"
    )


async def test_pat_introspect_sees_direct_service(db, user_with_group_service):
    """Регрессия: прямой dept-сервис в scope PAT по-прежнему виден."""
    ctx = user_with_group_service
    user = ctx["user"]
    direct_svc = ctx["direct_service"]

    raw = await _issue_pat(db, user.id, "pat_direct_svc", [direct_svc])
    resp = await introspect(db, raw)

    assert resp.active is True
    assert direct_svc in (resp.allowed_services or [])


# ── PAT не расширяет права сверх пользовательских ───────────────────────────


async def test_pat_does_not_grant_unreachable_service(db, user_with_group_service):
    """Сервис, к которому нет access ни напрямую, ни через группу, не
    появляется в effective даже если PAT на него заскоуплен. PAT —
    subset прав юзера, не расширение."""
    ctx = user_with_group_service
    user = ctx["user"]

    other_svc = await _make_service(db, "pgs_unreachable_service")
    await db.commit()

    raw = await _issue_pat(db, user.id, "pat_unreachable", [other_svc.service_name])
    resp = await introspect(db, raw)

    assert resp.active is True
    assert other_svc.service_name not in (resp.allowed_services or []), (
        "сервис без dept- и group-access не должен пробрасываться через PAT"
    )
    assert other_svc.service_name not in (resp.service_roles or {})


async def test_pat_scope_intersects_user_permissions(db, user_with_group_service):
    """PAT с широким scope (direct + group + unreachable) обрезается до
    реально доступных юзеру сервисов."""
    ctx = user_with_group_service
    user = ctx["user"]
    direct_svc = ctx["direct_service"]
    group_svc = ctx["group_service"]

    other_svc = await _make_service(db, "pgs_unreachable_wide")
    await db.commit()

    raw = await _issue_pat(
        db, user.id, "pat_wide", [direct_svc, group_svc, other_svc.service_name]
    )
    resp = await introspect(db, raw)

    assert resp.active is True
    assert set(resp.allowed_services or []) == {direct_svc, group_svc}, (
        "effective = пересечение scope PAT с реальными правами юзера "
        "(direct + group), без unreachable"
    )


# ── Симметрия PAT ↔ JWT ──────────────────────────────────────────────────────


async def test_pat_and_jwt_yield_same_services(db, user_with_group_service):
    """PAT (заскоупленный на все доступные юзеру сервисы) и JWT того же
    юзера дают одинаковый набор allowed_services и service_roles."""
    ctx = user_with_group_service
    user = ctx["user"]

    # Полный scope = всё, что реально доступно юзеру.
    full_services, full_roles, _ = await collect_user_permissions(db, user)

    raw = await _issue_pat(db, user.id, "pat_symmetry", list(full_services))
    pat_resp = await introspect(db, raw)

    jwt = _build_access_token(user)
    jwt_resp = await introspect(db, jwt)

    assert pat_resp.active is True and jwt_resp.active is True
    assert set(pat_resp.allowed_services or []) == set(jwt_resp.allowed_services or [])
    assert pat_resp.service_roles == jwt_resp.service_roles
    # И оба совпадают с тем, что отдаёт collect_user_permissions напрямую.
    assert set(pat_resp.allowed_services or []) == set(full_services)
    assert pat_resp.service_roles == full_roles
