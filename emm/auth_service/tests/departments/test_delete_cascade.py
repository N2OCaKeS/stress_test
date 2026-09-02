"""Тесты: DELETE /api/auth/v1/departments/{id} каскадит зависимые row'ы.

Регрессия: relationship `Department.service_access` без cascade приводил
к `UPDATE ... SET department_id=NULL` (NOT NULL колонка) → 500 при сносе
любого отдела с выданным сервисом. Здесь проверяем, что снос проходит и
`department_service_access` / `service_role_definitions` / `user_groups`
физически удаляются вместе с отделом.
"""

import pytest
from sqlalchemy import select

from src.models import (
    Department,
    DepartmentServiceAccess,
    ServiceRoleDefinition,
    UserGroup,
)
from src.services import secret_service_client
from src.utils.ids import group_id

DELETE_URL = "/api/auth/v1/departments/{dept_id}"


@pytest.fixture
def notify_calls(monkeypatch):
    calls = []

    async def _capture(dept_id, actor_id, actor_username):
        calls.append(dept_id)

    monkeypatch.setattr(secret_service_client, "notify_dept_deleted", _capture)
    return calls


async def _hard_delete(client, token, dept_id, reason="closed"):
    return await client.request(
        "DELETE",
        DELETE_URL.format(dept_id=dept_id),
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": reason},
    )


async def _grant(db, dept_id, service_name):
    """Выдать отделу сервис + засеять system-роль admin (как в проде)."""
    from tests.conftest import _grant_service
    return await _grant_service(db, dept_id, service_name)


async def test_delete_dept_with_service_grant_cascades(
    client, admin_token, dept_b, service_x, db, notify_calls,
):
    """Отдел с service-грантом удаляется без 500; access-row'ы снесены."""
    access = await _grant(db, dept_b.id, service_x.service_name)
    await db.commit()
    access_id = access.id

    resp = await _hard_delete(client, admin_token, dept_b.id)
    assert resp.status_code == 200, resp.text

    assert await db.scalar(
        select(Department).where(Department.id == dept_b.id)
    ) is None
    assert await db.scalar(
        select(DepartmentServiceAccess).where(DepartmentServiceAccess.id == access_id)
    ) is None
    # Системные role-definitions пары (dept, service) тоже снесены.
    remaining_roles = (await db.execute(
        select(ServiceRoleDefinition).where(
            ServiceRoleDefinition.department_id == dept_b.id
        )
    )).scalars().all()
    assert remaining_roles == []
    assert notify_calls == [dept_b.id]


async def test_delete_dept_cascades_groups(
    client, admin_token, dept_b, db, notify_calls,
):
    """Группа отдела каскадно снимается при сносе отдела."""
    grp = UserGroup(id=group_id(), department_id=dept_b.id, name="t_grp_b", is_active=True)
    db.add(grp)
    await db.commit()
    grp_id = grp.id

    resp = await _hard_delete(client, admin_token, dept_b.id)
    assert resp.status_code == 200, resp.text

    assert await db.scalar(
        select(UserGroup).where(UserGroup.id == grp_id)
    ) is None


async def test_delete_dept_with_user_blocked_regression(
    client, admin_token, dept_a, user_a, db, notify_calls,
):
    """Отдел с активным юзером → 422 USERS_REMAIN_IN_DEPT, отдел цел."""
    resp = await _hard_delete(client, admin_token, dept_a.id)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error_code"] == "USERS_REMAIN_IN_DEPT"
    assert await db.scalar(
        select(Department).where(Department.id == dept_a.id)
    ) is not None
    assert notify_calls == []
