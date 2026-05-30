"""actor_department_id thread-through: endpoint прокидывает identity.department_id в сервис.

`_resolve_actor_dept` избегает SELECT, если actor_department_id уже известен
из IdentityContext (endpoint передаёт `identity.department_id`). Здесь проверяем:

- department_admin видит/создаёт только в своём отделе (поведение корректно).
- При dept_admin-запросе через HTTP get_by_id для актора не вызывается лишний
  раз (интеграционный аспект: department_id уже есть в identity, нет N+1).
"""

import pytest

BOTS_URL = "/api/auth/v1/bots"


class TestDeptAdminBotCreateThreadThrough:
    async def test_dept_admin_creates_bot_in_own_dept_ok(
        self, client, dept_admin_a_token, dept_a_with_service, service_x,
    ):
        """department_admin создаёт бота в своём отделе — 201."""
        resp = await client.post(
            BOTS_URL,
            headers={"Authorization": f"Bearer {dept_admin_a_token}"},
            json={
                "name": "thread_own_dept_bot",
                "department_id": dept_a_with_service.id,
                "allowed_services": [],
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["department_id"] == dept_a_with_service.id

    async def test_dept_admin_cannot_create_bot_in_foreign_dept(
        self, client, dept_admin_a_token, dept_b,
    ):
        """department_admin не может создать бота в чужом отделе — 403."""
        resp = await client.post(
            BOTS_URL,
            headers={"Authorization": f"Bearer {dept_admin_a_token}"},
            json={
                "name": "cross_dept_bot",
                "department_id": dept_b.id,
                "allowed_services": [],
            },
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "BOT_CREATION_FORBIDDEN"

    async def test_dept_admin_no_extra_user_select_when_dept_known(
        self, client, dept_admin_a, dept_admin_a_token, dept_a_with_service, monkeypatch,
    ):
        """Когда identity.department_id известен, _resolve_actor_dept не дёргает UserRepository.

        Проверяем, что количество SELECT users не увеличивается для dept_admin'а,
        чей department_id уже есть в identity (передаётся через actor_department_id).
        """
        from src.repositories import users as users_module

        select_calls: list[str] = []
        original_get = users_module.UserRepository.get_by_id

        async def counting_get(self, uid):
            select_calls.append(uid)
            return await original_get(self, uid)

        monkeypatch.setattr(users_module.UserRepository, "get_by_id", counting_get)

        resp = await client.post(
            BOTS_URL,
            headers={"Authorization": f"Bearer {dept_admin_a_token}"},
            json={
                "name": "thread_perf_bot",
                "department_id": dept_a_with_service.id,
                "allowed_services": [],
            },
        )
        assert resp.status_code == 201, resp.text

        # actor_id не должен запрашиваться повторно из-за _resolve_actor_dept
        # (department_id уже известен из identity, передан через actor_department_id)
        actor_selects = [uid for uid in select_calls if uid == dept_admin_a.id]
        assert len(actor_selects) <= 1, (
            f"_resolve_actor_dept сделал лишний SELECT для актора с известным dept: "
            f"actor_id={dept_admin_a.id}, all_calls={select_calls}"
        )


class TestDeptAdminBotListThreadThrough:
    async def test_dept_admin_lists_only_own_dept_bots(
        self, client, admin_token, dept_admin_a_token,
        dept_a_with_service, dept_b,
    ):
        """department_admin видит только ботов своего отдела."""
        # Боты в dept_a
        for i in range(2):
            await client.post(
                BOTS_URL,
                headers={"Authorization": f"Bearer {admin_token}"},
                json={
                    "name": f"list_thread_bot_a_{i}",
                    "department_id": dept_a_with_service.id,
                    "allowed_services": [],
                },
            )
        # Бот в dept_b — не должен быть виден dept_admin_a
        await client.post(
            BOTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "list_thread_bot_b",
                "department_id": dept_b.id,
                "allowed_services": [],
            },
        )

        resp = await client.get(
            BOTS_URL,
            headers={"Authorization": f"Bearer {dept_admin_a_token}"},
        )
        assert resp.status_code == 200
        bots = resp.json()
        dept_ids = {b["department_id"] for b in bots}
        assert dept_ids.issubset({dept_a_with_service.id}), (
            f"dept_admin_a увидел бота из чужого отдела: dept_ids={dept_ids}"
        )

    async def test_account_admin_lists_all_depts_bots(
        self, client, admin_token, dept_a_with_service, dept_b,
    ):
        """account_admin видит ботов всех отделов."""
        for dept_id, name in [
            (dept_a_with_service.id, "aa_list_thread_bot_a"),
            (dept_b.id, "aa_list_thread_bot_b"),
        ]:
            await client.post(
                BOTS_URL,
                headers={"Authorization": f"Bearer {admin_token}"},
                json={"name": name, "department_id": dept_id, "allowed_services": []},
            )

        resp = await client.get(
            BOTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        bots_by_name = {b["name"]: b for b in resp.json()}
        assert "aa_list_thread_bot_a" in bots_by_name
        assert "aa_list_thread_bot_b" in bots_by_name


class TestDeptAdminBotUpdateThreadThrough:
    async def test_dept_admin_can_update_own_dept_bot(
        self, client, admin_token, dept_admin_a_token, dept_a_with_service,
    ):
        """department_admin обновляет бота своего отдела — 200."""
        create = await client.post(
            BOTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "thread_update_own_bot",
                "department_id": dept_a_with_service.id,
                "allowed_services": [],
            },
        )
        assert create.status_code == 201
        bot_id = create.json()["bot_id"]

        patch = await client.patch(
            f"{BOTS_URL}/{bot_id}",
            headers={"Authorization": f"Bearer {dept_admin_a_token}"},
            json={"name": "thread_update_own_bot_renamed"},
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["name"] == "thread_update_own_bot_renamed"

    async def test_dept_admin_cannot_update_foreign_dept_bot(
        self, client, admin_token, dept_admin_a_token, dept_b,
    ):
        """department_admin не может изменить бота чужого отдела — 403."""
        create = await client.post(
            BOTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "thread_update_foreign_bot",
                "department_id": dept_b.id,
                "allowed_services": [],
            },
        )
        assert create.status_code == 201
        bot_id = create.json()["bot_id"]

        patch = await client.patch(
            f"{BOTS_URL}/{bot_id}",
            headers={"Authorization": f"Bearer {dept_admin_a_token}"},
            json={"name": "thread_update_foreign_bot_attempt"},
        )
        assert patch.status_code == 403
        assert patch.json()["error_code"] == "BOT_UPDATE_FORBIDDEN"


class TestDeptAdminBotTokenThreadThrough:
    async def test_dept_admin_creates_token_for_own_bot(
        self, client, admin_token, dept_admin_a_token, dept_a_with_service,
    ):
        """department_admin выдаёт токен боту своего отдела — 201."""
        create = await client.post(
            BOTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "thread_token_own_bot",
                "department_id": dept_a_with_service.id,
                "allowed_services": [],
            },
        )
        assert create.status_code == 201
        bot_id = create.json()["bot_id"]

        tok = await client.post(
            f"{BOTS_URL}/{bot_id}/tokens",
            headers={"Authorization": f"Bearer {dept_admin_a_token}"},
            json={"name": "ci_token"},
        )
        assert tok.status_code == 201, tok.text
        assert tok.json()["name"] == "ci_token"

    async def test_dept_admin_cannot_create_token_for_foreign_bot(
        self, client, admin_token, dept_admin_a_token, dept_b,
    ):
        """department_admin не может выдать токен боту чужого отдела — 403."""
        create = await client.post(
            BOTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "thread_token_foreign_bot",
                "department_id": dept_b.id,
                "allowed_services": [],
            },
        )
        assert create.status_code == 201
        bot_id = create.json()["bot_id"]

        tok = await client.post(
            f"{BOTS_URL}/{bot_id}/tokens",
            headers={"Authorization": f"Bearer {dept_admin_a_token}"},
            json={"name": "cross_dept_token"},
        )
        assert tok.status_code == 403
