"""Edge cases для `bulk_revoke` с дубликатами user_ids.

`bulk_revoke` дедуплицирует user_ids через `dict.fromkeys()` симметрично
`bulk_assign` — дубль не размножает `_invalidate_identity_cache` и не пишет
повторов в audit details.
"""

import pytest

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

    async def test_revoke_dedup_collapses_audit_user_ids(
        self, client, admin_token, user_a, dept_a_with_service, service_x, monkeypatch,
    ):
        """Дубль в запросе не размножает user_ids в audit-детали bulk_revoke."""
        from src.services import audit_service as audit_mod

        captured: list[dict] = []
        original = audit_mod.emit

        def _spy(action, actor_id=None, **kw):
            captured.append({"action": action, **kw})
            return original(action, actor_id, **kw)

        monkeypatch.setattr(audit_mod, "emit", _spy)

        url = f"{_roles_url(dept_a_with_service.id, service_x.service_name)}/reader/revoke"
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"user_ids": [user_a.id, user_a.id, user_a.id]},
        )
        assert resp.status_code == 200, resp.text

        events = [e for e in captured if e["action"] == "service_role.bulk_revoke"]
        assert events, captured
        details = events[-1]["details"]
        assert details["user_ids"] == [user_a.id]
        assert details["user_count"] == 1


class TestBulkRevokeInactive:
    async def test_revoke_rejects_inactive_user(
        self, db, account_admin, user_a, dept_a_with_service, service_x,
    ):
        """Inactive-юзер в bulk_revoke отбивается USER_INACTIVE (симметрия с assign)."""
        from src.core.constants import PlatformRole
        from src.core.exceptions import ConflictError
        from src.schemas.auth import IdentityContext
        from src.services import service_role_service

        user_a.is_active = False
        await db.flush()

        identity = IdentityContext(
            user_id=account_admin.id,
            username=account_admin.username,
            platform_role=PlatformRole.ACCOUNT_ADMIN,
            department_id=None,
        )
        with pytest.raises(ConflictError) as ei:
            await service_role_service.bulk_revoke(
                db,
                identity=identity,
                department_id=dept_a_with_service.id,
                service_name=service_x.service_name,
                role_name="reader",
                user_ids=[user_a.id],
            )
        assert ei.value.error_code == "USER_INACTIVE"
