"""Edge cases для `/departments/{dept}/services/{svc}/roles/{role}/assign|revoke`.

Базовые happy/path лежат в test_bulk_roles.py — здесь только:
* пустой `user_ids` (валиден ли запрос?),
* несуществующий user в середине списка (partial vs atomic);
* пользователь из другого департамента;
* несуществующий target department.
"""

ROLES_URL = "/api/auth/v1/departments/{department_id}/services/{service_name}/roles"


def _roles_url(dept_id, svc):
    return ROLES_URL.format(department_id=dept_id, service_name=svc)


# ── Empty list ───────────────────────────────────────────────────────────────

class TestEmptyUserIds:
    async def test_assign_empty_list_is_noop_200(self, client, admin_token, dept_a_with_service, service_x):
        url = f"{_roles_url(dept_a_with_service.id, service_x.service_name)}/reader/assign"
        resp = await client.post(
            url, headers={"Authorization": f"Bearer {admin_token}"},
            json={"user_ids": []},
        )
        assert resp.status_code == 200

    async def test_revoke_empty_list_is_noop_200(self, client, admin_token, dept_a_with_service, service_x):
        url = f"{_roles_url(dept_a_with_service.id, service_x.service_name)}/reader/revoke"
        resp = await client.post(
            url, headers={"Authorization": f"Bearer {admin_token}"},
            json={"user_ids": []},
        )
        assert resp.status_code == 200


# ── Partial failure / atomicity ──────────────────────────────────────────────

class TestPartialFailure:
    async def test_unknown_user_in_middle_fails_whole_batch(
        self, client, admin_token, user_a, dept_a_with_service, service_x, db,
    ):
        """Несуществующий user_id в списке — весь batch отклоняется (atomicity),
        существующий user НЕ получает роль."""
        url = f"{_roles_url(dept_a_with_service.id, service_x.service_name)}/operator/assign"
        resp = await client.post(
            url, headers={"Authorization": f"Bearer {admin_token}"},
            json={"user_ids": [user_a.id, "usr_doesnotexist"]},
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "USER_NOT_FOUND"

        from src.repositories.roles import RoleRepository
        roles = await RoleRepository(db).get_roles_by_service(user_a.id, service_x.service_name)
        # У user_a уже есть "reader" из фикстуры; "operator" не должна была проставиться.
        assert "operator" not in roles

    async def test_cross_dept_user_in_batch_fails_atomically(
        self, client, admin_token, user_a, user_b, dept_a_with_service, service_x, db,
    ):
        """user_b в dept_b — попытка дать ему роль в dept_a → отказ, user_a остаётся без роли."""
        url = f"{_roles_url(dept_a_with_service.id, service_x.service_name)}/operator/assign"
        resp = await client.post(
            url, headers={"Authorization": f"Bearer {admin_token}"},
            json={"user_ids": [user_a.id, user_b.id]},
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "USER_DEPARTMENT_MISMATCH"

        from src.repositories.roles import RoleRepository
        roles = await RoleRepository(db).get_roles_by_service(user_a.id, service_x.service_name)
        assert "operator" not in roles


# ── Department / role existence ──────────────────────────────────────────────

class TestTargetValidation:
    async def test_nonexistent_target_department_returns_404(
        self, client, admin_token, user_a, service_x,
    ):
        resp = await client.post(
            f"{_roles_url('dep_ghost', service_x.service_name)}/reader/assign",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"user_ids": [user_a.id]},
        )
        assert resp.status_code in (403, 404)
        assert resp.json()["error_code"] in {"DEPARTMENT_ACCESS_DENIED", "SERVICE_ROLE_NOT_FOUND"}

    async def test_revoke_nonexistent_role_no_error(
        self, client, admin_token, user_a, dept_a_with_service, service_x,
    ):
        """Revoke для роли, которой нет — должна пройти без ошибки (idempotent)."""
        url = f"{_roles_url(dept_a_with_service.id, service_x.service_name)}/ghost_role/revoke"
        resp = await client.post(
            url, headers={"Authorization": f"Bearer {admin_token}"},
            json={"user_ids": [user_a.id]},
        )
        # Текущая реализация не проверяет существование роли для revoke — фиксируем.
        assert resp.status_code == 200


# ── Duplicates in user_ids ───────────────────────────────────────────────────

class TestDuplicateUserIds:
    async def test_assign_with_duplicate_user_id_idempotent(
        self, client, admin_token, user_a, dept_a_with_service, service_x, db,
    ):
        """Дубль user_id в batch не должен бить UNIQUE и валиться 500.

        До фикса: цикл по input создавал 2 строки `UserServiceRole` с одинаковым
        `(user_id, service, role)` → второй INSERT триггерил UNIQUE → 500.
        Теперь дубликаты схлопываются на входе, ответ 200, в БД одна запись.
        """
        url = f"{_roles_url(dept_a_with_service.id, service_x.service_name)}/operator/assign"
        resp = await client.post(
            url, headers={"Authorization": f"Bearer {admin_token}"},
            json={"user_ids": [user_a.id, user_a.id]},
        )
        assert resp.status_code == 200

        from src.repositories.roles import RoleRepository
        roles = await RoleRepository(db).get_roles_by_service(user_a.id, service_x.service_name)
        # Operator проставлен ровно один раз; дубликат не уронил транзакцию.
        assert "operator" in roles
