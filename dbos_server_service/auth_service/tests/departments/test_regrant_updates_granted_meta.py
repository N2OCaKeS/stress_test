"""Реактивация dept/group service access обновляет `granted_at`/`granted_by`.

До фикса повторный grant поверх revoked-row оставлял старые поля — list-эндпоинты
показывали автора первой выдачи, что вводит в заблуждение forensics/SOC.
"""

from datetime import datetime
from sqlalchemy import select

from src.models import DepartmentServiceAccess, GroupServiceAccess
from tests.conftest import _make_user, _login


DEPT_REVOKE_URL = "/api/auth/v1/departments/{dept_id}/services/{svc_name}"
DEPT_GRANT_URL = "/api/auth/v1/departments/{dept_id}/services"
GROUPS_URL = "/api/auth/v1/groups"


async def _make_second_admin_token(client, db, username="t_admin2"):
    await _make_user(db, username, "Admin1234!", platform_role="account_admin")
    return await _login(client, username, "Admin1234!")


async def test_dept_regrant_after_revoke_updates_granted_meta(
    client, admin_token, account_admin, dept_a_with_service, service_x, db,
):
    """revoke → grant другим админом → granted_at новее, granted_by — другой."""
    dept_id = dept_a_with_service.id
    svc_name = service_x.service_name
    row_before = await db.scalar(
        select(DepartmentServiceAccess).where(
            DepartmentServiceAccess.department_id == dept_id,
            DepartmentServiceAccess.service_name == svc_name,
        )
    )
    assert row_before is not None
    original_granted_at: datetime = row_before.granted_at
    original_granted_by: str | None = row_before.granted_by

    revoke = DEPT_REVOKE_URL.format(dept_id=dept_id, svc_name=svc_name)
    r = await client.delete(revoke, headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200, r.text

    admin2_token = await _make_second_admin_token(client, db)
    grant = DEPT_GRANT_URL.format(dept_id=dept_id)
    r = await client.post(
        grant,
        headers={"Authorization": f"Bearer {admin2_token}"},
        json={"service_name": svc_name},
    )
    assert r.status_code == 201, r.text

    db.expire_all()
    row_after = await db.scalar(
        select(DepartmentServiceAccess).where(
            DepartmentServiceAccess.department_id == dept_id,
            DepartmentServiceAccess.service_name == svc_name,
        )
    )
    assert row_after is not None
    assert row_after.granted_at >= original_granted_at
    assert row_after.granted_by != original_granted_by
    assert row_after.is_active is True


async def test_group_regrant_after_revoke_updates_granted_meta(
    client, admin_token, account_admin, dept_a_with_service, service_x, db,
):
    dept_id = dept_a_with_service.id
    svc_name = service_x.service_name
    grp_resp = await client.post(
        GROUPS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "department_id": dept_id,
            "name": "regrant_grp",
            "display_name": "regrant_grp",
        },
    )
    assert grp_resp.status_code == 201, grp_resp.text
    group_id = grp_resp.json()["id"]

    grant_url = f"{GROUPS_URL}/{group_id}/services"
    r = await client.post(
        grant_url,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": svc_name},
    )
    assert r.status_code == 201, r.text

    row_before = await db.scalar(
        select(GroupServiceAccess).where(
            GroupServiceAccess.group_id == group_id,
            GroupServiceAccess.service_name == svc_name,
        )
    )
    assert row_before is not None
    original_granted_at = row_before.granted_at
    original_granted_by = row_before.granted_by

    revoke_url = f"{GROUPS_URL}/{group_id}/services/{svc_name}"
    r = await client.delete(revoke_url, headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200, r.text

    admin2_token = await _make_second_admin_token(client, db)
    r = await client.post(
        grant_url,
        headers={"Authorization": f"Bearer {admin2_token}"},
        json={"service_name": svc_name},
    )
    assert r.status_code == 201, r.text

    db.expire_all()
    row_after = await db.scalar(
        select(GroupServiceAccess).where(
            GroupServiceAccess.group_id == group_id,
            GroupServiceAccess.service_name == svc_name,
        )
    )
    assert row_after is not None
    assert row_after.granted_at >= original_granted_at
    assert row_after.granted_by != original_granted_by
    assert row_after.is_active is True
