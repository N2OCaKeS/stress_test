"""Edge cases для `bulk_revoke` с дубликатами user_ids.

W8: bulk_assign дедуплицирует через dict.fromkeys(), но bulk_revoke — нет.
Тест фиксирует текущее поведение: idempotent UPDATE/deactivate не падает
на дубликатах (в отличие от INSERT с UNIQUE).
"""

ROLES_URL = "/api/auth/v1/departments/{department_id}/services/{service_name}/roles"


def _roles_url(dept_id, svc):
    return ROLES_URL.format(department_id=dept_id, service_name=svc)


class TestBulkRevokeDuplicates:
    async def test_revoke_with_duplicate_user_id_does_not_fail(
        self, client, admin_token, user_a, dept_a_with_service, service_x,
    ):
        """Дубликат user_id в bulk_revoke не падает ни на 500, ни на validation error.

        bulk_revoke использует idempotent deactivate-путь (UPDATE is_active=False),
        так что повтор одного user_id безопасен.
        """
        url = f"{_roles_url(dept_a_with_service.id, service_x.service_name)}/reader/revoke"
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"user_ids": [user_a.id, user_a.id]},
        )
        assert resp.status_code == 200, resp.text

    async def test_revoke_with_duplicate_actually_revokes_once(
        self, client, admin_token, user_a, dept_a_with_service, service_x, db,
    ):
        """После bulk_revoke с дублем роль деактивирована, повтор не воскрешает."""
        url = f"{_roles_url(dept_a_with_service.id, service_x.service_name)}/reader/revoke"
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"user_ids": [user_a.id, user_a.id]},
        )
        assert resp.status_code == 200

        from src.repositories.roles import RoleRepository
        roles = await RoleRepository(db).get_roles_by_service(user_a.id, service_x.service_name)
        assert "reader" not in roles, (
            "bulk_revoke должен убрать роль, даже если user_id продублирован"
        )

    async def test_bulk_assign_dedup_does_not_persist_duplicates(
        self, client, admin_token, user_a, dept_a_with_service, service_x, db,
    ):
        """Регрессия: bulk_assign с [user_id, user_id] — одна строка в БД, не две.

        До фикса дублированный user_id порождал два INSERT с одним (user_id, service,
        role) → UNIQUE violation → 500. Фикс: dict.fromkeys() схлопывает дубли.
        """
        url = f"{_roles_url(dept_a_with_service.id, service_x.service_name)}/operator/assign"
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"user_ids": [user_a.id, user_a.id, user_a.id]},
        )
        assert resp.status_code == 200, resp.text

        from src.repositories.roles import RoleRepository
        roles = await RoleRepository(db).get_roles_by_service(user_a.id, service_x.service_name)
        assert "operator" in roles
        # Убедимся, что строк нет дублей через count active rows:
        from sqlalchemy import select, func
        from src.models import UserServiceRole
        count = await db.scalar(
            select(func.count()).select_from(UserServiceRole).where(
                UserServiceRole.user_id == user_a.id,
                UserServiceRole.service_name == service_x.service_name,
                UserServiceRole.role == "operator",
                UserServiceRole.is_active.is_(True),
            )
        )
        assert count == 1, f"expected exactly 1 active operator row, got {count}"
